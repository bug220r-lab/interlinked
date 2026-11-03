"""Coordinator + real workspace + authenticated node dispatch synthetic integration.

Inference is deterministic fixture code; no installed model or real project is
used. TLS/pinning have their own node suite. These checks use actual worker
threads, on-disk snapshots, SQLite search, RPC journals and reviewer mailboxes.
"""
from contextlib import contextmanager
import copy
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_ai as ai
import interlinked_ai_node as node
import interlinked_ai_workspaces as workspaces


class FixtureRuntime:
    def __init__(self, root, name='writer', digest='a' * 64, computer='THIS-MACHINE'):
        self.name, self.digest = name, digest
        self.identity = dict(computer=computer, sid='S-1-5-21-1-2-3-1001')
        self.calls = []
        self.version = 'fixture-1'
        self.miss_unknown = False
    def inventory(self):
        return dict(protocol=node.PROTOCOL, workerOperationRecoveryV1=True, inferencePolicySha256=None, identity=self.identity, observedAt=ai.utc(), runtime={'version': self.version}, models=[dict(name=self.name, digest=self.digest, size=10, capabilities=['completion', 'tools'])], loaded=[], resources=dict(memory={'available': 64 * 1024**3, 'total': 96 * 1024**3}, gpus=[]))
    def chat(self, model, messages, tools=None, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        system = messages[0].get('content', '')
        prompt = messages[-1].get('content', '')
        if 'Independently review' in system:
            return dict(message=dict(content='{"approved":true,"findings":[]}'))
        if tools and tools[0]['function']['name'] == 'lookup_project':
            project = prompt.split('set to ')[1].split('.')[0]
            return dict(message=dict(content='', tool_calls=[dict(function=dict(name='lookup_project', arguments=dict(project_id=project)))]))
        if 'No owner is recorded' in prompt:
            return dict(message=dict(content='{"owner":"invented"}' if self.miss_unknown else '{"owner":null}'))
        if 'Source A at' in prompt:
            return dict(message=dict(content='{"latest_failed":2}'))
        if 'f([1,2,3,4])' in prompt:
            return dict(message=dict(content='[4,16]'))
        if 'raises IndexError' in prompt:
            return dict(message=dict(content='{"fixed":"range(len(xs))"}'))
        if 'range(' in prompt:
            return dict(message=dict(content='[1,3,5]'))
        if 'exactly 7' in prompt:
            return dict(message=dict(content='{"project":"Lantern","pending":7}'))
        if 'project worker' in system:
            if messages[-1]['role'] == 'tool':
                return dict(message=dict(content='Updated answer to 2. No check profile was assigned.'))
            return dict(message=dict(content='', tool_calls=[dict(function=dict(name='write_file', arguments=dict(path='example.py', content='answer = 2\n')))]))
        return dict(message=dict(content='The fixture answer is documented in fixture/example.py.'))
    def execute(self, action, payload, cancel=None):
        if action == 'inventory':
            return self.inventory()
        if action == 'chat':
            return self.chat(**payload)
        raise ValueError('Unexpected fixture runtime action')


class InProcessPeer:
    """Actual node authentication and RPC dispatch; transport remains in-process."""
    def __init__(self, service, token):
        self.service, self.token = service, token
        self.worker_operation_recovery = True
        self.identity, self.fingerprint = service.identity, 'fixture-certificate'
    def execute(self, action, payload=None, cancel=None, request_id=None, **kwargs):
        self.service.authenticate('Bearer ' + self.token)
        result = self.service.rpc(dict(protocol=node.PROTOCOL, identity=self.service.identity, requestId=request_id or uuid.uuid4().hex, action=action, payload=payload or {}))
        return result['result']
    def inventory(self):
        return self.execute('inventory')
    def chat(self, model, messages, tools=None, options=None, cancel=None, timeout=180, audio=False):
        return self.execute('chat', dict(model=model, messages=messages, tools=tools, options=options, timeout=timeout, audio=audio), cancel)


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-ai-integration-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'example.py').write_text('answer = 1\n', encoding='utf-8')
        kit, state, cache = [self.root / name for name in ('kit', 'state', 'cache')]
        for path in (kit, state, cache):
            path.mkdir()
        (kit / 'MAGIX-Workspace-State').mkdir()
        (kit / 'MAGIX-Workspace-State/.workflow.lock').write_bytes(b'fixture')
        for name in ('binding.json', 'multi-binding.json'):
            (kit / 'MAGIX-Workspace-State' / name).write_text('{"status":"UNBOUND"}')
        self.active = False
        self.leases = []
        test = self
        class Engine:
            @contextmanager
            def exclusive(self, path, create=False):
                test.leases.append(str(path))
                try:
                    yield
                finally:
                    test.leases.remove(str(path))
        self.controller = SimpleNamespace(lock=threading.RLock(), closing=False, engine=Engine(), busy=lambda: self.active)
        self.master = SimpleNamespace(state=lambda: dict(settings=dict(projects=[dict(id='fixture', label='Fixture', root=str(self.source))])))
        runtime = SimpleNamespace(state=state, kit=kit, cache=cache, demo=False)
        self.service = ai.AIService(runtime, self.controller, self.master, node_factory=FixtureRuntime)
        self.remote_runtime = FixtureRuntime(self.root, 'reviewer', 'b' * 64, 'MSI')
        self.remote = node.NodeService(self.remote_runtime, self.root / 'remote', protector=lambda raw, decrypt=False: raw)
        self.remote_workers = workspaces.Workspaces(self.root / 'remote' / 'workspaces')
        self.dispatcher = node.WorkerDispatcher(self.remote_workers)
        self.remote.worker_dispatch = self.dispatcher
        code = self.remote.begin_pairing()
        credentials = self.remote.pair(dict(code=code, expectedIdentity=self.remote.identity))
        self.peer = InProcessPeer(self.remote, credentials['token'])
        self.service.nodes['msi'] = self.peer
        self.service.refresh()
        for host, name in [('local', 'writer'), ('msi', 'reviewer')]:
            for capability in ('text', 'coding', 'tools'):
                self.assertTrue(self.service.calibrate(dict(host=host, model=name, capability=capability))['passed'])
        self.service.post('context/approve', dict(projectId='fixture'))

    def tearDown(self):
        self.service.close()
        self.dispatcher.close()
        self.temp.cleanup()

    def start(self, execution):
        body = dict(projectId='fixture', prompt='Change answer to 2.', host='local', model='writer', executionHost=execution, permissions=['read', 'write'], checkProfiles=[], requestId=uuid.uuid4().hex)
        row = self.service.post('tasks/start', body)
        self.service.workspaces.threads[row['id']].join(10)
        return self.service.get('task', {'id': [row['id']]})

    def test_coordinator_local_worker_review_collect(self):
        row = self.start('local')
        self.assertEqual(row['status'], 'READY_FOR_REVIEW', row)
        self.assertEqual(row['review']['model']['digest'], 'b' * 64)
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')
        applied = self.service.post('tasks/apply', dict(id=row['id'], expectedResultHash=row['result']['sha256']))
        self.assertEqual(applied['status'], 'APPLIED')
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')
        self.assertEqual(self.leases, [])

    def test_coordinator_remote_mailbox_independent_inference(self):
        row = self.start('msi')
        self.assertEqual(row['status'], 'READY_FOR_REVIEW', row)
        self.assertTrue(row['remote'])
        self.assertEqual(row['model']['host'], 'local')
        self.assertEqual(row['review']['model']['host'], 'msi')
        self.assertTrue(self.remote_workers.tasks())
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')
        self.service.post('tasks/apply', dict(id=row['id'], expectedResultHash=row['result']['sha256']))
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')

    def test_context_settings_and_chat_use_real_stored_index(self):
        (self.source / '.env').write_text('TOKEN=private answer')
        result = self.service.post('context/index', dict(projectId='fixture'))
        self.assertEqual(result['files'], 1)
        result = self.service.post('search', dict(query='answer'))
        self.assertEqual([x['path'] for x in result], ['example.py'])
        settings = self.service.settings_value()
        value = copy.deepcopy(settings['value'])
        value['speakReplies'] = True
        self.service.post('settings', dict(value=value, settingsRevision=settings['revision']))
        self.assertTrue(self.service.settings_value()['value']['speakReplies'])
        row = self.service.post('chat', dict(text='What is the answer?', host='local', model='writer'))
        for thread in self.service.threads:
            thread.join(5)
        conversation = self.service.get('conversation', {'id': [row['id']]})
        self.assertEqual(conversation['status'], 'completed')
        self.assertEqual(conversation['messages'][-1]['sources'][0]['path'], 'example.py')
        self.assertNotIn('private answer', json.dumps(self.service.nodes['local'].calls))

    def test_node_worker_start_duplicate_preserves_mailbox(self):
        bundle = self.service.workspaces.export_source('fixture')
        project = self.peer.execute('worker_prepare', bundle)
        payload = dict(projectId=project['projectId'], prompt='Change answer to 2.', model=dict(host='local', id='writer', digest='a' * 64), permissions=['read', 'write'], requestId=uuid.uuid4().hex)
        row = self.peer.execute('worker_start', payload)
        end = time.monotonic() + 3
        while not self.dispatcher.pending and time.monotonic() < end:
            time.sleep(.01)
        again = self.peer.execute('worker_start', payload)
        self.assertEqual(row['id'], again['id'])
        status = self.peer.execute('worker_status', dict(taskId=row['id']))
        self.assertEqual(len(status['pendingInference']), 1, 'An idempotent worker start hid the original inference mailbox.')
        self.peer.execute('worker_cancel', dict(taskId=row['id']))

    def test_request_retry_keeps_original_execution_and_route(self):
        body = dict(projectId='fixture', prompt='Change answer to 2.', host='local', model='writer', executionHost='local', permissions=['read', 'write'], requestId=uuid.uuid4().hex)
        row = self.service.post('tasks/start', body)
        self.service.workspaces.threads[row['id']].join(5)
        self.service.calibrate(dict(host='local', model='writer', capability='coding'))
        self.service.refresh()
        repeated = self.service.post('tasks/start', body)
        self.assertEqual(row['id'], repeated['id'])
        self.assertEqual(len(self.service.workspaces.tasks()), 1)
        with self.assertRaisesRegex(ValueError, 'different task'):
            self.service.post('tasks/start', dict(body, executionHost='msi'))

    def test_calibration_invalidates_on_runtime_and_context_change(self):
        trials = self.service.model('local', 'writer')['trials']
        self.assertEqual(trials['text']['runtimeVersion'], 'fixture-1')
        self.assertIn('memory', trials['text']['resources'])
        self.assertEqual(len(trials['coding']['trials']), 3)
        self.service.nodes['local'].version = 'fixture-2'
        self.service.refresh()
        self.assertFalse(self.service.model('local', 'writer')['verified'])
        settings = self.service.settings_value()
        value = dict(settings['value'], contextTokens=16384)
        self.service.post('settings', dict(value=value, settingsRevision=settings['revision']))
        with self.assertRaisesRegex(ValueError, 'capability tests'):
            self.service.route({'text'})

    def test_quality_score_precedes_latency(self):
        self.remote_runtime.miss_unknown = True
        result = self.service.calibrate(dict(host='msi', model='reviewer', capability='text'))
        self.assertTrue(result['passed'])
        self.assertAlmostEqual(result['score'], 2 / 3)
        self.service.calibration[ai.qualification_key(self.service.qualification(self.service.model('local','writer')))]['text']['seconds'] = 1000
        self.service.calibration[ai.qualification_key(self.service.qualification(self.service.model('msi','reviewer')))]['text']['seconds'] = .001
        self.assertEqual(self.service.route({'text'})['host'], 'local')

    def test_speech_request_uses_speech_verified_route(self):
        item = self.service.attach(dict(name='fixture.wav', mime='audio/wav', data=base64.b64encode(b'RIFF\x04\x00\x00\x00WAVE').decode()))
        result = self.service.calibrate(dict(host='local', model='writer', capability='audio_speech', attachmentId=item['id'], expected='fixture'))
        self.assertTrue(result['passed'])
        self.assertNotIn('audio_general', self.service.model('local', 'writer')['verified'])
        row = self.service.chat(dict(text='Listen to the recording.', host='local', model='writer', attachments=[item['id']], audioKind='speech'))
        for thread in self.service.threads:
            thread.join(5)
        self.assertEqual(self.service.get('conversation', {'id': [row['id']]})['status'], 'completed')
        rejected = self.service.chat(dict(text='Describe the sounds.', host='local', model='writer', attachments=[item['id']], audioKind='general'))
        for thread in self.service.threads: thread.join(5)
        rejected = self.service.get('conversation', {'id': [rejected['id']]})
        self.assertEqual(rejected['status'], 'failed')
        self.assertIn('capability tests', rejected['error'])

    def test_coordinator_worker_cancellation_retains_result(self):
        entered = threading.Event()
        def blocked(model, messages, tools=None, **kwargs):
            entered.set()
            kwargs['cancel'].wait(5)
            raise node.Cancelled('Fixture inference canceled')
        self.service.nodes['local'].chat = blocked
        row = self.service.start_task(dict(projectId='fixture', prompt='Change answer to 2.', host='local', model='writer', executionHost='local', permissions=['read', 'write'], requestId=uuid.uuid4().hex))
        self.assertTrue(entered.wait(3))
        self.service.post('tasks/cancel', dict(id=row['id']))
        self.service.workspaces.threads[row['id']].join(5)
        result = self.service.workspaces.task(row['id'])
        self.assertEqual(result['status'], 'CANCELED')
        self.assertIsNotNone(result['result'])
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')


if __name__ == '__main__':
    unittest.main()
