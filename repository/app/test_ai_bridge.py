"""Durable bridge acceptance using real coordinator, indexes and isolated workers."""
import copy
from contextlib import nullcontext
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
from interlinked_ai_bridge import BridgeService
from test_ai_integration import FixtureRuntime


class ManagedFixture(FixtureRuntime):
    def __init__(self, root, name='writer', digest='a' * 64):
        super().__init__(root, name, digest)
        self.owned = False
        self.load_session = 'fixture-session-1'
        self.unloads = 0
        self.hold = None
    def chat(self, *args, **kwargs):
        if self.hold is not None:
            while not self.hold.wait(.01):
                if kwargs.get('cancel') is not None and kwargs['cancel'].is_set():
                    raise ValueError('Request cancelled.')
        return super().chat(*args, **kwargs)
    def load(self, model, **kwargs):
        self.owned = True
        return dict(status='loaded', owned=True, loadIdentity=self.load_identity(model))
    def load_identity(self, model):
        if not self.owned:
            raise ValueError('Not an owned model.')
        return dict(model=model, digest=self.digest, runtimeVersion=self.version, nodeSession='node-fixture', loadSession=self.load_session)
    def unload(self, model, confirmed_external_idle=False, expected_identity=None, **kwargs):
        if not confirmed_external_idle or expected_identity != self.load_identity(model):
            raise ValueError('Confirmation identity changed.')
        self.unloads += 1
        self.owned = False
        return dict(status='unloaded')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2] / '.handoff/violet-ai-validation/bridge-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='test-', dir=base)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'; self.state.mkdir()
        self.source = self.root / 'project'; self.source.mkdir()
        (self.source / 'example.py').write_text('answer = 1\n# Lantern checkpoint has seven pending checks.\n', encoding='utf-8')
        self.other = self.root / 'other'; self.other.mkdir()
        (self.other / 'notes.txt').write_text('Lantern has unrelated private reference.', encoding='utf-8')
        projects = [dict(id='fixture', label='Fixture', root=str(self.source)), dict(id='other', label='Other', root=str(self.other))]
        master = SimpleNamespace(state=lambda: dict(settings=dict(projects=projects)))
        runtime = SimpleNamespace(state=self.state, kit=self.root, demo=True)
        self.ai = ai.AIService(runtime, master=master, node_factory=ManagedFixture)
        # Fixture copies have no production workflow locks or managed cache.
        self.ai.workspaces.acquire = lambda project_id: nullcontext()
        self.ai.nodes['msi'] = ManagedFixture(self.root, 'reviewer', 'b' * 64)
        self.ai.refresh()
        for host, name in [('local', 'writer'), ('msi', 'reviewer')]:
            for cap in ('text', 'coding', 'tools'):
                self.ai.calibrate(dict(host=host, model=name, capability=cap))
        for project in ('fixture', 'other'):
            self.ai.workspaces.approve(project)
            self.ai.workspaces.index(project)
        self.bridge = BridgeService(self.ai)
    def tearDown(self):
        self.bridge.close()
        self.ai.close()
        self.temp.cleanup()
    def submit(self, action, **body):
        body.setdefault('requestId', uuid.uuid4().hex)
        return self.bridge.dispatch(action, body)
    def wait(self, job, pending=False):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = self.bridge.dispatch('get_result', {'id': job['id']})
            if row['status'] not in ('queued', 'running', 'cancelling'):
                if pending or row['status'] != 'confirmation_required':
                    return row
            time.sleep(.01)
        self.fail('Operation did not finish: ' + repr(row))
    def test_analysis_supplied_text_only_and_exactly_once(self):
        request = dict(requestId=uuid.uuid4().hex, text='Summarize Lantern.', contextScope='supplied_text_only', host='local', model='writer')
        before = len(self.ai.nodes['local'].calls)
        first = self.bridge.dispatch('submit_analysis', request)
        again = self.bridge.dispatch('submit_analysis', request)
        self.assertEqual(first['id'], again['id'])
        result = self.wait(first)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result']['sources'], [])
        self.assertEqual(result['provenance']['model']['digest'], 'a' * 64)
        self.assertEqual(len(self.ai.nodes['local'].calls), before + 1)
        with self.assertRaisesRegex(ValueError, 'different'):
            self.bridge.dispatch('submit_analysis', dict(request, text='Changed'))
    def test_default_and_selected_context(self):
        all_sources = self.bridge.dispatch('search_context', dict(query='Lantern'))['sources']
        self.assertEqual({s['projectId'] for s in all_sources}, {'fixture', 'other'})
        selected = self.bridge.dispatch('search_context', dict(query='Lantern', contextScope='selected_projects', projectIds=['fixture']))['sources']
        self.assertEqual({s['projectId'] for s in selected}, {'fixture'})
        with self.assertRaises(ValueError):
            self.bridge.dispatch('search_context', dict(query='Lantern', contextScope='selected_projects', projectIds=['unapproved']))

    def test_auto_mode_provenance_and_duplicate_do_not_repeat(self):
        request=dict(requestId=uuid.uuid4().hex, text='Explain the supplied source.', routingMode='fast', contextScope='supplied_text_only')
        first=self.bridge.dispatch('submit_analysis',request)
        result=self.wait(first)
        self.assertEqual(result['status'],'completed',result)
        routing=result['result']['routingDecision']
        self.assertEqual(routing['mode'],'fast')
        self.assertTrue(routing['fallback'])
        self.assertEqual(result['provenance']['routingDecision'],routing)
        self.assertEqual(self.bridge.dispatch('submit_analysis',request)['id'],first['id'])
        with self.assertRaises(ValueError): self.bridge.dispatch('submit_analysis',dict(request,routingMode='best_quality'))

    def test_state_observes_non_bridge_inference_without_dispatch(self):
        model=self.ai.model('local','writer')
        call=self.ai.observations.call_start({'origin':'buddy','flowId':'buddy:fixture'},model)
        try:
            state=self.bridge.state()
            self.assertTrue(state['routingCapabilities']['activityObservation'])
            self.assertTrue(any(r['origin']=='buddy' for r in state['activity']['activeCalls']))
            self.assertNotIn('prompt',json.dumps(state['activity']))
        finally:
            self.ai.observations.call_end(call)

    def test_classifier_evaluation_is_supplied_only_and_fixed_profile(self):
        self.ai.hosts['local']['inventory']['responseProfiles']=['routing_v1']
        captured=[]
        def classify(model,messages,**kwargs):
            captured.append((messages,kwargs))
            return {'message':{'content':'{"task":"coding","complexity":"simple","responseFormat":"code"}'}}
        self.ai.nodes['local'].chat=classify
        result=self.wait(self.submit('classify_prompt',host='local',model='writer',text='Review this function.'))
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['result']['classification']['task'],'coding')
        self.assertNotIn('Lantern',json.dumps(captured[0][0]))
        self.assertEqual(captured[0][1]['response_profile'],'routing_v1')
        self.assertEqual(captured[0][1]['options']['num_predict'],512)
        self.assertIn('inferencePolicySha256',result['provenance'])
        with self.assertRaises(ValueError): self.submit('classify_prompt',host='local',model='writer',text='x',contextScope='approved_projects')
    def test_strict_authority_and_bridge_job_ownership(self):
        for action in ('tasks/apply', 'pair', 'context/approve', 'confirm_unload', 'register_fixture'):
            with self.assertRaises(ValueError): self.bridge.dispatch(action, {})
        with self.assertRaises(ValueError):
            self.submit('request_unload', host='local', model='writer', confirmedExternalIdle=True)
        with self.assertRaises(ValueError): self.bridge.dispatch('get_job', {'id': uuid.uuid4().hex})
        with self.assertRaises(ValueError): self.submit('start_worker', projectId='fixture', prompt='x', shell='bad')
    def test_reduced_state_omits_paths_transcripts_credentials(self):
        self.ai.conversations['private'] = dict(id='private', title='secret transcript', status='idle')
        state = json.dumps(self.bridge.dispatch('get_ai_state', {}))
        self.assertNotIn(str(self.source), state)
        self.assertNotIn('secret transcript', state)
        self.assertNotIn('"token"', state.lower())
        self.assertIn('fixture', state)
    def test_settings_duplicate_returns_original_before_stale_revision(self):
        snapshot = self.ai.settings_value()
        value = copy.deepcopy(snapshot['value']); value['responseTokens'] = 1024
        args = dict(requestId=uuid.uuid4().hex, settingsRevision=snapshot['revision'], value=value)
        job = self.bridge.dispatch('update_ai_settings', args)
        result = self.wait(job)
        self.assertEqual(result['status'], 'completed', result)
        revision = self.ai.settings_value()['revision']
        self.assertNotEqual(revision, snapshot['revision'])
        self.assertEqual(self.bridge.dispatch('update_ai_settings', args)['status'], 'completed')
        self.assertEqual(self.ai.settings_value()['revision'], revision)
    def test_unload_requires_gui_single_use_identity_confirmation(self):
        self.wait(self.submit('load_model', host='local', model='writer'))
        pending = self.wait(self.submit('request_unload', host='local', model='writer'), pending=True)
        self.assertEqual(pending['status'], 'confirmation_required')
        self.assertEqual(self.ai.nodes['local'].unloads, 0)
        self.bridge.confirm_unload(pending['id'])
        self.assertEqual(self.wait(pending)['status'], 'completed')
        self.assertEqual(self.ai.nodes['local'].unloads, 1)
        with self.assertRaises(ValueError): self.bridge.confirm_unload(pending['id'])
    def test_changed_load_session_refuses_unload(self):
        self.wait(self.submit('load_model', host='local', model='writer'))
        pending = self.wait(self.submit('request_unload', host='local', model='writer'), pending=True)
        self.ai.nodes['local'].load_session = 'different'
        self.bridge.confirm_unload(pending['id'])
        self.assertEqual(self.wait(pending)['status'], 'failed')
        self.assertEqual(self.ai.nodes['local'].unloads, 0)
    def test_cancelled_confirmation_cannot_be_consumed(self):
        self.wait(self.submit('load_model', host='local', model='writer'))
        pending = self.wait(self.submit('request_unload', host='local', model='writer'), pending=True)
        self.bridge.dispatch('cancel_job', {'id': pending['id']})
        with self.assertRaises(ValueError): self.bridge.confirm_unload(pending['id'])
        self.assertEqual(self.ai.nodes['local'].unloads, 0)
    def test_restart_marks_incomplete_job_without_replay(self):
        request = dict(requestId=uuid.uuid4().hex, host='local', model='writer')
        self.wait(self.bridge.dispatch('load_model', request))
        row = self.bridge.jobs[request['requestId']]
        row.update(status='running'); self.bridge._save(row)
        self.bridge.close()
        self.bridge = BridgeService(self.ai)
        self.assertEqual(self.bridge.dispatch('load_model', request)['status'], 'interrupted')
    def test_calibration_cancel_does_not_publish_new_verdict(self):
        old = copy.deepcopy(self.ai.calibration)
        self.ai.nodes['local'].hold = threading.Event()
        job = self.submit('calibrate_model', host='local', model='writer', capability='text')
        time.sleep(.1)
        self.bridge.dispatch('cancel_job', {'id': job['id']})
        self.assertEqual(self.wait(job)['status'], 'cancelled')
        self.ai.nodes['local'].hold.set()
        self.assertEqual(self.ai.calibration, old)
    def test_queued_settings_change_refuses_inference(self):
        slot = self.ai.slots.setdefault('local', threading.Lock()); slot.acquire()
        job = self.submit('submit_analysis', text='Lantern', contextScope='supplied_text_only', host='local', model='writer')
        time.sleep(.1)
        snapshot = self.ai.settings_value(); value = copy.deepcopy(snapshot['value']); value['responseTokens'] = 1024
        self.ai.configuration.save(value, snapshot['revision'])
        slot.release()
        self.assertEqual(self.wait(job)['errorCode'], 'configuration_changed')
    def test_worker_returns_reviewable_diff_without_application(self):
        job = self.submit('start_worker', projectId='fixture', prompt='Change answer to 2.', permissions=['read', 'write'], executionHost='local', host='local', model='writer', contextScope='supplied_text_only')
        result = self.wait(job)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result']['status'], 'READY_FOR_REVIEW')
        self.assertIn('answer = 2', result['result']['result']['changes'][0]['diff'])
        self.assertIn('answer = 1', (self.source / 'example.py').read_text())
    def test_media_requires_locally_registered_fixture(self):
        job = self.submit('calibrate_model', host='local', model='writer', capability='vision', fixtureId=uuid.uuid4().hex)
        self.assertEqual(self.wait(job)['status'], 'failed')
        with self.assertRaises(ValueError): self.submit('calibrate_model', host='local', model='writer', capability='vision', attachmentId=uuid.uuid4().hex, expected='red')
    def test_load_waits_for_capacity_and_cancellation_keeps_server(self):
        self.ai.capacity = lambda inventory, model: False
        job = self.submit('load_model', host='local', model='writer')
        time.sleep(.1)
        self.assertFalse(self.ai.nodes['local'].owned)
        self.bridge.dispatch('cancel_job', {'id': job['id']})
        self.assertEqual(self.wait(job)['status'], 'cancelled')
        self.assertFalse(self.ai.nodes['local'].owned)
        self.assertEqual(self.ai.nodes['local'].unloads, 0)
    def test_changed_digest_before_load_refused(self):
        original = self.ai.nodes['local'].inventory
        def changed():
            result = original()
            result['models'][0]['digest'] = 'c' * 64
            return result
        self.ai.nodes['local'].inventory = changed
        job = self.submit('load_model', host='local', model='writer')
        self.assertEqual(self.wait(job)['status'], 'failed')
        self.assertFalse(self.ai.nodes['local'].owned)
    def test_result_truncation_is_explicit(self):
        node = self.ai.nodes['local']
        node.chat = lambda *args, **kwargs: dict(message=dict(content='x' * 180000))
        job = self.submit('submit_analysis', text='Summarize', contextScope='supplied_text_only', host='local', model='writer')
        result = self.wait(job)['result']
        self.assertTrue(result['answerTruncated'])
        self.assertEqual(len(result['answer']), 128 * 1024)
        self.assertEqual(len(self.bridge.jobs[job['id']]['fullAnswer']), 180000)
    def test_empty_analysis_is_failure_without_reasoning_disclosure_or_retry(self):
        for content in ('', ' \n\t'):
            with self.subTest(content=content):
                calls = []
                def empty(*args, **kwargs):
                    calls.append(1)
                    return dict(message=dict(content=content, thinking='private reasoning'))
                self.ai.nodes['local'].chat = empty
                job = self.submit('submit_analysis', text='Summarize', contextScope='supplied_text_only', host='local', model='writer')
                result = self.wait(job)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['errorCode'], 'empty_analysis_response')
                self.assertIsNone(result['result'])
                self.assertNotIn('private reasoning', json.dumps(result))
                self.assertEqual(len(calls), 1)
    def test_remote_recovery_uncertainty_is_not_completion(self):
        worker = dict(id=uuid.uuid4().hex, projectId='fixture', status='RECOVERY_REQUIRED', result=None, checks=[])
        self.ai.start_task = lambda *args, **kwargs: copy.deepcopy(worker)
        self.ai.workspaces.task = lambda task_id: copy.deepcopy(worker)
        self.ai.workspaces.cancel = lambda task_id: copy.deepcopy(worker)
        job = self.submit('start_worker', projectId='fixture', prompt='Inspect source.', contextScope='supplied_text_only')
        result = self.wait(job)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['errorCode'], 'worker_recovery_required')
        self.assertEqual(result['workerStatus'], 'RECOVERY_REQUIRED')


if __name__ == '__main__':
    unittest.main()
