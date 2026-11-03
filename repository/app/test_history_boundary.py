"""Synthetic history/privacy boundary acceptance; no production inputs or models.

These tests exercise actual SQLite history, reviewed memories, coordinator chat,
bridge operations and workspace snapshots. Model replies are deterministic local
fixtures. HTTP transport and gateway credentials are covered separately.
"""
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from interlinked_history_runtime import load_dependencies
load_dependencies()
import interlinked_ai as ai
from interlinked_ai_bridge import BridgeService
import interlinked_ai_node as node
import interlinked_ai_workspaces as workspaces
from interlinked_history_store import HistoryStore
from test_ai_bridge import ManagedFixture


ARCHIVE_CANARY = 'BOUNDARY_ARCHIVE_7d623bf0'
MEMORY_CANARY = 'BOUNDARY_MEMORY_190a2f94'
ANSWER_CANARY = 'BOUNDARY_LOCAL_ANSWER_5ebd0a63'
CANARIES = (ARCHIVE_CANARY, MEMORY_CANARY, ANSWER_CANARY)
IDENTITY = {'computer': 'BOUNDARY-FIXTURE', 'sid': 'S-1-5-21-1-2-3-1001'}


def history_record():
    text = 'Private boundary archive fact: ' + ARCHIVE_CANARY
    return dict(id=hashlib.sha256(b'boundary-source').hexdigest(),
                revision=hashlib.sha256(text.encode()).hexdigest(), kind='conversation',
                title='Boundary archive', text=text, timestamp=None, project='fixture',
                memory_allowed=True, metadata={'conversationId': 'boundary-chat', 'role': 'user', 'onCurrentBranch': True})


class BoundaryRuntime(ManagedFixture):
    """Capture real coordinator inputs while returning deterministic local text."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = []

    def chat(self, model, messages, **kwargs):
        self.options.append(copy.deepcopy({k: v for k, v in kwargs.items() if k in ('options', 'response_profile')}))
        system, prompt = messages[0].get('content', ''), messages[-1].get('content', '')
        answer = None
        if kwargs.get('options') == {'num_ctx': 16384, 'num_predict': 4096}:
            if '"pending":7' in prompt:
                answer = '{"pending":7}'
            elif '"latest_failed":2' in prompt:
                answer = '{"latest_failed":2}'
            elif '"owner":null' in prompt:
                answer = '{"owner":null}'
            elif system.startswith('You are INTERLINKED Assistant.'):
                answer = ANSWER_CANARY if ARCHIVE_CANARY in system or MEMORY_CANARY in system else 'No matching historical evidence.'
        if answer is not None:
            self.calls.append(copy.deepcopy(messages))
            return {'message': {'content': answer}}
        return super().chat(model, messages, **kwargs)


class HistoryBridgeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'project'; self.source.mkdir()
        (self.source / 'example.py').write_text('answer = 1\n# Public boundary fixture.\n', encoding='utf-8')
        state = self.root / 'app'; state.mkdir()
        self.projects = [dict(id='fixture', label='Fixture project', root=str(self.source))]
        master = SimpleNamespace(state=lambda: dict(settings=dict(projects=self.projects)))
        self.ai = ai.AIService(SimpleNamespace(state=state, kit=self.root, demo=True), master=master, node_factory=BoundaryRuntime)
        self.addCleanup(self.ai.close)
        self.ai.workspaces.acquire = lambda project_id: nullcontext()
        self.ai.nodes['msi'] = BoundaryRuntime(self.root, 'reviewer', 'b' * 64)
        self.ai.refresh()
        for host, model in [('local', 'writer'), ('msi', 'reviewer')]:
            for capability in ('text', 'coding', 'tools'):
                self.ai.calibrate(dict(host=host, model=model, capability=capability))
        self.ai.workspaces.approve('fixture')
        self.ai.workspaces.index('fixture')

        self.primary = HistoryStore(self.root / 'private-archive')
        self.addCleanup(self.primary.close)
        self.record = history_record()
        generation = self.primary.build([self.record])
        self.primary.activate(generation['generationId'])
        memory = self.primary.propose_memory(MEMORY_CANARY, [self.record['id']], project='fixture')
        self.primary.review_memory(memory['id'], 'approve')
        self.history = self.ai.local_history()
        snapshot = self.primary.snapshot_manifest()
        self.history.store.install_snapshot(snapshot, lambda path, offset, length: self.primary.snapshot_read_chunk(snapshot['snapshotId'], path, offset, length))
        self.history._embedder = lambda: None
        self.assertTrue(self.history.calibrate({'host': 'local', 'model': 'writer'}, threading.Event())['passed'])
        self.chat = self.wait_chat(self.ai.chat(dict(text='Find the boundary archive fact', historyEnabled=True,
                                                    historyProject='fixture', host='local', model='writer')))
        # Prove the controls are populated, and the local reply was generated
        # from archive/memory context before checking the separate bridge.
        sent = json.dumps(self.ai.nodes['local'].calls[-1])
        self.assertIn(ARCHIVE_CANARY, sent)
        self.assertIn(MEMORY_CANARY, sent)
        self.assertIn(ANSWER_CANARY, json.dumps(self.chat['messages']))
        self.bridge = BridgeService(self.ai)
        self.addCleanup(self.bridge.close)

    def wait_chat(self, row):
        deadline = time.monotonic() + 10
        while self.ai.conversations[row['id']]['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
        result = self.ai.conversations[row['id']]
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return result

    def wait_job(self, row):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.bridge.dispatch('get_result', {'id': row['id']})
            if result['status'] not in ('queued', 'running', 'cancelling'):
                self.assertEqual(result['status'], 'completed', result)
                return result
            time.sleep(.01)
        self.fail('Boundary fixture operation did not complete.')

    def assert_private_absent(self, value):
        text = json.dumps(value, ensure_ascii=False)
        for canary in CANARIES:
            self.assertNotIn(canary, text)

    def test_bridge_state_search_analysis_worker_and_results_exclude_private_history(self):
        before = {host: len(runtime.calls) for host, runtime in self.ai.nodes.items()}
        self.assert_private_absent(self.bridge.dispatch('get_ai_state', {}))
        sources = self.bridge.dispatch('search_context', {'query': 'boundary'})
        self.assertTrue(sources['sources'], 'The ordinary project must remain searchable.')
        self.assert_private_absent(sources)
        for canary in CANARIES:
            self.assertEqual(self.bridge.dispatch('search_context', {'query': canary})['sources'], [])
        analysis = self.bridge.dispatch('submit_analysis', dict(requestId=uuid.uuid4().hex,
            text='Describe the boundary fixture.', host='local', model='writer'))
        self.assert_private_absent(self.wait_job(analysis))
        worker = self.bridge.dispatch('start_worker', dict(requestId=uuid.uuid4().hex,
            projectId='fixture', prompt='Change answer to 2.', permissions=['read', 'write'],
            executionHost='local', host='local', model='writer'))
        result = self.wait_job(worker)
        self.assertEqual(result['result']['status'], 'READY_FOR_REVIEW')
        self.assert_private_absent(result)
        self.assertIn('answer = 1', (self.source / 'example.py').read_text())
        self.assert_private_absent(self.bridge.dispatch('get_ai_state', {}))
        for host, runtime in self.ai.nodes.items():
            self.assert_private_absent(runtime.calls[before[host]:])

    def test_bridge_cannot_request_history_or_read_local_conversation(self):
        for action in ('history', 'history/search', 'memory-propose', 'handoff'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.bridge.dispatch(action, {})
        for action in ('get_job', 'get_result'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Unknown bridge'):
                self.bridge.dispatch(action, {'id': self.chat['id']})
        for field, value in [('historyEnabled', True), ('historyProject', 'fixture'), ('conversationId', self.chat['id'])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.bridge.dispatch('submit_analysis', dict(requestId=uuid.uuid4().hex, text='Describe the fixture.', **{field: value}))

    def test_history_off_preserves_visible_transcript_but_resets_model_context(self):
        row = self.wait_chat(self.ai.chat(dict(text='A new ordinary question.', historyEnabled=False,
            conversationId=self.chat['id'], host='local', model='writer')))
        self.assert_private_absent(self.ai.nodes['local'].calls[-1])
        self.assertIn(ANSWER_CANARY, json.dumps(row['messages']))
        self.assertEqual(self.ai.nodes['local'].calls[-1][-1]['content'], 'A new ordinary question.')

    def test_history_reenable_resets_previous_off_context_without_ui_flag(self):
        off_text = 'OFF_CONTEXT_8d9f463a'
        self.wait_chat(self.ai.chat(dict(text=off_text, historyEnabled=False, conversationId=self.chat['id'], host='local', model='writer')))
        self.wait_chat(self.ai.chat(dict(text='Find the boundary archive fact', historyEnabled=True,
            historyProject='fixture', conversationId=self.chat['id'], host='local', model='writer')))
        self.assertNotIn(off_text, json.dumps(self.ai.nodes['local'].calls[-1]))

    def test_project_change_and_exclusion_drop_derived_context(self):
        self.wait_chat(self.ai.chat(dict(text='What is recorded?', historyEnabled=True, historyProject='unrelated',
            conversationId=self.chat['id'], host='local', model='writer')))
        self.assert_private_absent(self.ai.nodes['local'].calls[-1])
        self.wait_chat(self.ai.chat(dict(text='Find the boundary archive fact', historyEnabled=True, historyProject='fixture',
            conversationId=self.chat['id'], host='local', model='writer')))
        self.history.post('exclude', {'id': self.record['id'], 'enabled': True})
        self.wait_chat(self.ai.chat(dict(text='What remains?', historyEnabled=True, historyProject='fixture',
            conversationId=self.chat['id'], host='local', model='writer')))
        self.assert_private_absent(self.ai.nodes['local'].calls[-1])

    def test_coordinator_forwards_fixed_history_profile_to_node(self):
        self.assertEqual(self.ai.nodes['local'].options[-1].get('response_profile'), 'history_v1')
        self.assertEqual(self.ai.nodes['local'].options[-1]['options'], {'num_ctx': 16384, 'num_predict': 4096})

    def test_owned_archive_below_project_is_never_cloud_context(self):
        nested = HistoryStore(self.source / 'nested-owned-archive')
        self.addCleanup(nested.close)
        # Export intake legitimately contains JSON readable by project scanning.
        (nested.root / 'exports' / 'conversations.json').write_text(json.dumps({'text': ARCHIVE_CANARY}), encoding='utf-8')
        files, excluded = workspaces.scan(self.source)
        self.assertNotIn(ARCHIVE_CANARY, json.dumps(files))
        self.assertGreater(excluded, 0)
        self.ai.workspaces.index('fixture')
        self.assertEqual(self.bridge.dispatch('search_context', {'query': ARCHIVE_CANARY})['sources'], [])

    def test_local_ai_transcripts_cannot_be_approved_for_bridge_context(self):
        transcripts = self.ai.root / 'conversations'
        self.assertIn(ANSWER_CANARY, (transcripts / (self.chat['id'] + '.json')).read_text(encoding='utf-8'))
        self.projects.append(dict(id='transcripts', label='Local conversations', root=str(transcripts)))
        with self.assertRaises(ValueError):
            self.ai.workspaces.approve('transcripts')


class HistoryNodeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-node-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = node.LocalNode(self.root / 'node', IDENTITY)
        self.addCleanup(self.local.close)

    def test_upstream_urls_and_cloud_models_rejected_without_inference(self):
        for url in ('https://api.example.test', 'http://example.test:11434', 'http://127.0.0.1:11434/path',
                    'http://user:pass@127.0.0.1:11434', 'http://127.0.0.1:11434?url=cloud'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                node.LocalNode(self.root / 'bad-node', IDENTITY, ollama_url=url)
        for model in ('fixture:cloud', 'fixture-cloud', 'https://api.example.test/model'):
            with self.subTest(model=model), mock.patch.object(self.local, '_request', return_value={'models': [{'name': model, 'digest': 'a' * 64}]}) as request:
                with self.assertRaises(ValueError):
                    self.local.chat(model, [{'role': 'user', 'content': ARCHIVE_CANARY}])
                self.assertFalse(any(call.args[0] == 'POST' for call in request.call_args_list))
        for remote in ({'remote_host': 'cloud.example.test'}, {'remote_model': 'remote'}):
            with self.subTest(remote=remote), mock.patch.object(self.local, '_request', return_value={'models': [dict(name='fixture', digest='a' * 64, **remote)]}) as request:
                with self.assertRaises(ValueError):
                    self.local.chat('fixture', [{'role': 'user', 'content': ARCHIVE_CANARY}])
                self.assertEqual(request.call_count, 1)

    def test_embeddings_require_exact_local_digest_and_no_url_overrides(self):
        for model in ('embeddinggemma:cloud', 'embeddinggemma:300m-cloud', 'https://api.example.test/embed'):
            with self.subTest(model=model), mock.patch.object(self.local, '_request') as request:
                with self.assertRaises(ValueError): self.local.embed(model, [ARCHIVE_CANARY], 'a' * 64)
                request.assert_not_called()
        for extra in ({'remote_host': 'cloud.example.test'}, {'remote_model': 'remote'}, {'digest': 'b' * 64}):
            row = dict(name='embeddinggemma:300m', digest='a' * 64); row.update(extra)
            with self.subTest(extra=extra), mock.patch.object(self.local, '_request', return_value={'models': [row]}) as request:
                with self.assertRaises(ValueError): self.local.embed('embeddinggemma:300m', [ARCHIVE_CANARY], 'a' * 64)
                self.assertEqual(request.call_count, 1)
        with mock.patch.object(self.local, '_request') as request:
            with self.assertRaises((TypeError, ValueError)):
                self.local.execute('embed', dict(model='embeddinggemma:300m', texts=[ARCHIVE_CANARY], digest='a' * 64,
                                                upstream='https://api.example.test/embed'))
            request.assert_not_called()

    def test_node_history_profile_enforces_16384_and_4096(self):
        replies = [{'models': [{'name': 'fixture', 'digest': 'a' * 64}]}, {'message': {'content': 'local'}, 'done': True}]
        with mock.patch.object(self.local, '_request', side_effect=replies) as request:
            self.local.chat('fixture', [{'role': 'user', 'content': 'fixture'}],
                            response_profile='history_v1', options={'num_ctx': 16384, 'num_predict': 4096})
            self.assertEqual(request.call_args.args[2]['options'], {'num_ctx': 16384, 'num_predict': 4096})
        for options in ({'num_ctx': 8192, 'num_predict': 4096}, {'num_ctx': 16384, 'num_predict': 512}):
            with self.subTest(options=options), mock.patch.object(self.local, '_request') as request:
                with self.assertRaises(ValueError):
                    self.local.chat('fixture', [{'role': 'user', 'content': 'fixture'}], response_profile='history_v1', options=options)
                request.assert_not_called()

    def test_history_owned_roots_and_default_root_ancestors_cannot_be_registered(self):
        custom = HistoryStore(self.root / 'custom-owned-archive')
        self.addCleanup(custom.close)
        (custom.root / 'exports' / 'fixture.json').write_text('{"text":"boundary"}')
        localappdata = self.root / 'user-local'
        default = localappdata / 'INTERLINKED' / 'History'; default.mkdir(parents=True)
        child = default / 'exports'; child.mkdir()
        rows = [dict(id='custom', root=str(custom.root)), dict(id='custom-child', root=str(custom.root / 'exports')),
                dict(id='default', root=str(default)), dict(id='child', root=str(child)),
                dict(id='ancestor', root=str(default.parent))]
        ws = workspaces.Workspaces(self.root / 'workspaces', registered_projects=lambda: rows)
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(localappdata)}):
            for row in rows:
                with self.subTest(project=row['id']), self.assertRaises(ValueError): ws.approve(row['id'])

    def test_new_intake_marker_invalidates_previously_indexed_project_passages(self):
        project = self.root / 'project'; project.mkdir()
        nested = project / 'intake'; nested.mkdir()
        (nested / 'conversations.json').write_text(json.dumps({'text': ARCHIVE_CANARY}), encoding='utf-8')
        ws = workspaces.Workspaces(self.root / 'workspaces', registered_projects=lambda: [dict(id='fixture', root=str(project))])
        ws.approve('fixture'); ws.index('fixture')
        self.assertTrue(ws.search(ARCHIVE_CANARY), 'Precondition: the fixture passage was previously indexed.')
        (nested / 'export_manifest.json').write_text('{}', encoding='utf-8')
        self.assertEqual(ws.search(ARCHIVE_CANARY), [])
        files, _ = workspaces.scan(project)
        self.assertNotIn(ARCHIVE_CANARY, json.dumps(files))


if __name__ == '__main__':
    unittest.main()
