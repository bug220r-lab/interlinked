"""Content-free cooler observations using isolated state and deterministic models.

No installed runtime, USB, network listener or personal project is accessed.
"""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import interlinked_ai as ai
from interlinked_ai_bridge import BridgeService
from interlinked_buddy import BuddyAI
from test_ai import FakeNode
from test_ai_integration import FixtureRuntime
import test_ai_integration as integration_fixtures


class BlockingNode(FakeNode):
    def __init__(self, root):
        super().__init__(root)
        self.hold, self.started = None, threading.Event()

    def chat(self, *args, **kwargs):
        self.started.set()
        if self.hold is not None:
            if not self.hold.wait(5): raise TimeoutError('Fixture was not released.')
        return super().chat(*args, **kwargs)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-ai-observations-')
        self.root = Path(self.temp.name)
        self.service = ai.AIService(SimpleNamespace(state=self.root, kit=self.root, demo=True), node_factory=BlockingNode)
        self.service.refresh()
        for capability in ('text', 'coding', 'tools'):
            self.service.calibrate(dict(host='local', model='fixture', capability=capability))
        self.service.observations = ai.AIObservations()
        self.service.workspaces.observer = self.service.observations.worker
        self.node = self.service.nodes['local']
        self.node.started.clear()

    def tearDown(self):
        if self.node.hold is not None: self.node.hold.set()
        self.service.close()
        self.temp.cleanup()

    def test_passive_allowlist_and_host_freshness(self):
        before = len(self.node.calls)
        self.service.master = SimpleNamespace(state=lambda: self.fail('Observation must not recurse through MASTER.'))
        self.service.hosts['local']['inventory']['loaded'] = [dict(name='fixture', digest='a' * 64, secret='PRIVATE')]
        snapshot = self.service.observation_summary()
        self.assertEqual(before, len(self.node.calls))
        self.assertEqual(snapshot['hosts'][0]['loadedModels'], [dict(id='fixture', digest='a' * 64)])
        self.assertTrue(snapshot['hosts'][0]['available'])
        self.assertFalse(snapshot['hosts'][1]['available'])
        self.assertNotIn('PRIVATE', json.dumps(snapshot))
        self.service.hosts['local']['refreshed'] = time.time() - 61
        snapshot = self.service.observation_summary()
        self.assertTrue(snapshot['hosts'][0]['stale'])
        self.assertFalse(snapshot['hosts'][0]['available'])

    def test_queue_request_response_and_retained_completion_are_distinct(self):
        gate = self.service.slots.setdefault('local', threading.Lock())
        gate.acquire()
        self.node.hold = threading.Event()
        activity = self.service._activity_start('buddy', 'buddy:' + uuid.uuid4().hex, projectId='fixture')
        activity['role'] = 'analysis'
        errors = []
        def infer():
            try: self.service.infer(self.service.route({'text'}), [dict(role='user', content='PRIVATE PROMPT')], activity=activity)
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=infer)
        thread.start()
        try:
            self.wait(lambda: bool(self.service.observation_summary()['activeCalls']))
            snapshot = self.service.observation_summary()
            self.assertEqual((snapshot['hosts'][0]['queuedCount'], snapshot['hosts'][0]['runningCount']), (1, 0))
            self.assertFalse(snapshot['pulses'])
            gate.release()
            self.assertTrue(self.node.started.wait(3))
            snapshot = self.service.observation_summary()
            self.assertEqual((snapshot['hosts'][0]['queuedCount'], snapshot['hosts'][0]['runningCount']), (0, 1))
            self.assertEqual(snapshot['activeCalls'][0]['origin'], 'buddy')
            self.assertEqual(snapshot['activeCalls'][0]['phase'], 'request_pending')
            self.assertEqual([p['kind'] for p in snapshot['pulses']], ['request_sent'])
            self.assertNotIn('PRIVATE PROMPT', json.dumps(snapshot))
        finally:
            if gate.locked() and not self.node.started.is_set(): gate.release()
            self.node.hold.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        snapshot = self.service.observation_summary()
        self.assertFalse(snapshot['activeCalls'])
        self.assertEqual([p['kind'] for p in snapshot['pulses']], ['request_sent', 'response_received'])
        self.service._activity_finish(activity['flowId'])
        self.service._activity_read(activity['flowId'])
        self.service._activity_read(activity['flowId'])
        kinds = [p['kind'] for p in self.service.observation_summary()['pulses']]
        self.assertEqual(kinds, ['request_sent', 'response_received', 'result_ready', 'result_read'])

    def test_bad_response_never_emits_response_or_ready(self):
        self.node.chat = lambda *a, **k: dict(message=dict(content=[]))
        with self.assertRaisesRegex(ValueError, 'Malformed'):
            self.service.infer(self.service.route({'text'}), [dict(role='user', content='fixture')])
        snapshot = self.service.observation_summary()
        self.assertEqual([p['kind'] for p in snapshot['pulses']], ['request_sent'])
        self.assertEqual(snapshot['selectedFlow']['phase'], 'failed')
        self.assertFalse(snapshot['activeCalls'])

    def test_bridge_ready_precedes_first_actual_result_read_only(self):
        bridge = BridgeService(self.service)
        try:
            job = bridge.dispatch('submit_analysis', dict(requestId=uuid.uuid4().hex, text='PRIVATE', contextScope='supplied_text_only'))
            bridge.threads[-1].join(5)
            self.assertFalse(bridge.threads[-1].is_alive())
            before = self.service.observation_summary()
            self.assertEqual(before['selectedFlow']['origin'], 'violet_bridge')
            self.assertIn('result_ready', [p['kind'] for p in before['pulses']])
            self.assertNotIn('result_read', [p['kind'] for p in before['pulses']])
            bridge.dispatch('get_job', {'id': job['id']})
            self.assertNotIn('result_read', [p['kind'] for p in self.service.observation_summary()['pulses']])
            result = bridge.dispatch('get_result', {'id': job['id']})
            self.assertEqual(result['status'], 'completed')
            bridge.dispatch('get_result', {'id': job['id']})
            snapshot = self.service.observation_summary()
            self.assertEqual(sum(p['kind'] == 'result_read' for p in snapshot['pulses']), 1)
            self.assertNotIn('PRIVATE', json.dumps(snapshot))
            response = next(p for p in snapshot['pulses'] if p['kind'] == 'response_received')
            self.assertEqual((response['origin'], response['modelId'], response['digest']), ('violet_bridge', 'fixture', 'a' * 64))
            self.assertEqual(snapshot['selectedFlow']['model'], dict(host='local', modelId='fixture', digest='a' * 64))
            # Retained jobs from another coordinator lifetime cannot replay pulses.
            self.service.observations = ai.AIObservations()
            bridge.dispatch('get_result', {'id': job['id']})
            self.assertFalse(self.service.observation_summary()['pulses'])
        finally:
            bridge.close()

    def test_assistant_and_buddy_keep_their_own_origins(self):
        chat = self.service.chat(dict(text='PRIVATE assistant question', origin='violet_bridge'))
        self.wait(lambda: self.service.conversations[chat['id']]['status'] not in ('queued', 'running'))
        self.wait(lambda: any(r['origin'] == 'assistant' and r['phase'] == 'result_ready' for r in self.service.observation_summary()['flows']))
        buddy = BuddyAI(self.service, lambda project: dict(id=project))
        try:
            # Buddy's fixed tool is not the calibration-only FakeNode lookup.
            self.node.chat = lambda *args, **kwargs: dict(message=dict(role='assistant', content='Fixture reply.'))
            request_id = uuid.uuid4().hex
            buddy.submit(dict(request_id=request_id, project_id='fixture', messages=[dict(role='user', content='PRIVATE buddy question')]))
            buddy.threads[-1].join(5)
            self.assertEqual(buddy.records[request_id]['status'], 'completed')
            self.assertNotIn(request_id, buddy.cancels)
            snapshot = self.service.observation_summary()
            self.assertEqual({r['origin'] for r in snapshot['flows']}, {'assistant', 'buddy'})
            self.assertNotIn('PRIVATE', json.dumps(snapshot))
        finally:
            buddy.close()

    def test_buddy_retention_failure_never_claims_result_ready(self):
        buddy = BuddyAI(self.service, lambda project: dict(id=project))
        self.node.chat = lambda *args, **kwargs: dict(message=dict(role='assistant', content='Fixture reply.'))
        original_write = self.service.write
        failed = threading.Event()
        def write(path, row):
            if path.parent == buddy.root and row.get('status') == 'completed':
                failed.set()
                raise OSError('Synthetic receipt failure')
            return original_write(path, row)
        self.service.write = write
        try:
            request_id = uuid.uuid4().hex
            # Suppress only the expected fixture thread's unhandled write failure.
            with patch.object(threading, 'excepthook', lambda arguments: None):
                buddy.submit(dict(request_id=request_id, project_id='fixture', messages=[dict(role='user', content='Fixture')]))
                buddy.threads[-1].join(5)
            self.assertTrue(failed.is_set())
            snapshot = self.service.observation_summary()
            self.assertEqual(snapshot['selectedFlow']['phase'], 'failed')
            self.assertNotIn('result_ready', [p['kind'] for p in snapshot['pulses']])
        finally:
            self.service.write = original_write
            buddy.close()

    def test_calibration_origin_is_explicit(self):
        self.service.calibrate(dict(host='local', model='fixture', capability='text', origin='violet_bridge'))
        snapshot = self.service.observation_summary()
        self.assertEqual({r['origin'] for r in snapshot['flows']}, {'calibration'})
        self.assertEqual({r['role'] for r in snapshot['pulses'] if 'role' in r}, {'calibration'})

    @staticmethod
    def wait(condition):
        until = time.monotonic() + 5
        while not condition():
            if time.monotonic() >= until: raise AssertionError('Fixture observation timed out.')
            time.sleep(.01)


class BoundedTrackerTests(unittest.TestCase):
    def test_worker_human_review_stays_attention_after_read_while_direct_ready_expires(self):
        clock = [100.0]
        with patch.object(ai.time, 'monotonic', side_effect=lambda: clock[0]):
            observations = ai.AIObservations()
            worker = observations.start('assistant', 'worker:review', workerRequestId='request')
            task = dict(id='worker', requestId='request', status='READY_FOR_REVIEW', resultReady=True)
            observations.worker(task)
            direct = observations.start('assistant', 'assistant:ready')
            observations.phase(direct['flowId'], 'result_ready', 'ready')
            first = next(row for row in observations.summary([])['flows'] if row['id'] == worker['flowId'])
            clock[0] += 10
            observations.read(worker['flowId'])
            observations.read(direct['flowId'])
            current = observations.summary([])
            self.assertEqual([row['id'] for row in current['flows']], [worker['flowId']])
            self.assertEqual(current['selectedFlow']['resultState'], 'review_required')
            self.assertEqual((current['selectedFlow']['phaseAt'], current['selectedFlow']['terminalAt']),
                             (first['phaseAt'], first['terminalAt']))
            observations.worker(task)
            clock[0] += 100
            self.assertEqual(observations.summary([])['selectedFlow']['id'], worker['flowId'])
            clock[0] += 81
            self.assertFalse(observations.summary([])['flows'])

    def test_failure_retention_source_freshness_clear_and_retry(self):
        clock = [100.0]
        with patch.object(ai.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(ai, 'utc',
                side_effect=lambda: ai.dt.datetime.fromtimestamp(clock[0],ai.dt.timezone.utc).isoformat()):
            observations = ai.AIObservations()
            flow = observations.start('assistant', 'retained:failure')
            observations.phase(flow['flowId'], 'failed', 'unavailable')
            first = observations.summary([])['selectedFlow']
            clock[0] += 40
            self.assertEqual(observations.summary([])['selectedFlow']['phase'], 'failed')
            observations.phase(flow['flowId'], 'failed', 'unavailable')
            current = observations.summary([])['selectedFlow']
            self.assertEqual((current['phaseAt'], current['terminalAt']), (first['phaseAt'], first['terminalAt']))
            self.assertNotEqual(current['observedAt'], first['observedAt'])
            clock[0] += 181
            self.assertFalse(observations.summary([])['flows'], 'Polling cannot freshen a terminal source observation.')
            observations.phase(flow['flowId'], 'failed')
            self.assertEqual(observations.summary([])['selectedFlow']['phase'], 'failed')
            observations.clear(flow['flowId'])
            self.assertFalse(observations.summary([])['flows'])
            call = observations.call_start(flow, dict(host='local', id='fixture', digest='a' * 64))
            snapshot = observations.summary([dict(id='local')])
            self.assertEqual(snapshot['selectedFlow']['phase'], 'queued')
            self.assertEqual((snapshot['hosts'][0]['queuedCount'], snapshot['hosts'][0]['runningCount']), (1, 0))
            self.assertNotIn('terminalAt', snapshot['selectedFlow'])
            observations.call_end(call)

    def test_result_read_and_repeated_source_status_do_not_restart_green_hold(self):
        clock = [100.0]
        with patch.object(ai.time, 'monotonic', side_effect=lambda: clock[0]):
            observations = ai.AIObservations()
            flow = observations.start('violet_bridge', 'ready:one')
            observations.phase(flow['flowId'], 'result_ready', 'ready')
            first = observations.summary([])['selectedFlow']
            clock[0] += 2.5
            observations.read(flow['flowId'])
            observations.phase(flow['flowId'], 'result_ready', 'ready')
            current = observations.summary([])['selectedFlow']
            self.assertEqual((current['phaseAt'], current['terminalAt']), (first['phaseAt'], first['terminalAt']))
            clock[0] += .6
            self.assertFalse(observations.summary([])['flows'])
            self.assertEqual(sum(p['kind'] == 'result_read' for p in observations.summary([])['pulses']), 1)

    def test_hardware_cooler_summary_is_passive_and_absent_from_generic_observations(self):
        with tempfile.TemporaryDirectory(prefix='interlinked-cooler-resources-') as tmp:
            service = ai.AIService(SimpleNamespace(state=Path(tmp), kit=Path(tmp), demo=True), node_factory=FakeNode)
            try:
                observed = ai.utc()
                service._cooler_resources['local'] = dict(gpuId='GPU-12345678-1234-1234-1234-123456789abc',
                    gpuLoad=dict(value=80, status='available', source='nvidia-smi', observedAt=observed),
                    token='PRIVATE', prompt='PRIVATE')
                service.nodes['local'].inventory = lambda: self.fail('Cooler reads must not refresh models.')
                resources = service.cooler_resources()
                self.assertEqual(resources[0]['gpuLoad']['value'], 80)
                self.assertIsNone(resources[1]['gpuLoad']['value'])
                self.assertNotIn('PRIVATE', json.dumps(resources))
                self.assertNotIn('gpuLoad', json.dumps(service.observation_summary()))
            finally:
                service.close()

    def test_selection_stability_expiration_and_restart_identity(self):
        clock = [100.0]
        with patch.object(ai.time, 'monotonic', side_effect=lambda: clock[0]):
            observations = ai.AIObservations()
            first = observations.start('assistant', 'worker:first')
            observations.phase(first['flowId'], 'working')
            second = observations.start('assistant', 'worker:second')
            observations.phase(second['flowId'], 'reviewing')
            self.assertEqual(observations.summary([])['selectedFlow']['id'], second['flowId'])
            observations.phase(first['flowId'], 'reviewing')
            self.assertEqual(observations.summary([])['selectedFlow']['id'], second['flowId'])
            # Ordinary finished work holds for three seconds; human review is
            # covered separately and remains an attention state.
            observations.phase(second['flowId'], 'result_ready', 'ready')
            clock[0] += 2
            self.assertEqual(observations.summary([])['selectedFlow']['id'], second['flowId'])
            clock[0] += 2
            self.assertEqual(observations.summary([])['selectedFlow']['id'], first['flowId'])
            clock[0] += 10
            self.assertFalse(observations.summary([])['pulses'])
            self.assertNotEqual(observations.boot_id, ai.AIObservations().boot_id)

    def test_overflow_never_changes_execution_or_claims_complete_counts(self):
        observations = ai.AIObservations()
        model = dict(host='local', id='fixture', digest='a' * 64)
        for index in range(65):
            flow = observations.start('assistant', 'worker:' + str(index))
            observations.call_start(flow, model)
        summary = observations.summary([dict(id='local')])
        self.assertEqual(len(summary['activeCalls']), 64)
        self.assertEqual(len(summary['flows']), 32)
        self.assertTrue(summary['partial'])
        self.assertIsNone(summary['hosts'][0]['queuedCount'])
        for index in range(200):
            flow = observations.start('calibration', 'done:' + str(index))
            observations.phase(flow['flowId'], 'result_ready', 'ready')
        self.assertEqual(len(observations.flows), 128)
        self.assertIn('worker:0', observations.flows)
        self.assertLessEqual(len(observations.pulses), 256)
        self.assertLessEqual(len(observations.summary([])['pulses']), 16)

    def test_remote_terminal_status_waits_for_verified_result(self):
        observations = ai.AIObservations()
        observations.start('assistant', 'worker:test', workerRequestId='request')
        row = dict(id='worker', requestId='request', status='READY_FOR_REVIEW', resultReady=False)
        observations.worker(row)
        self.assertFalse(observations.summary([])['pulses'])
        self.assertEqual(observations.summary([])['selectedFlow']['resultState'], 'awaiting_transfer')
        observations.worker(dict(row, resultReady=True))
        observations.worker(dict(row, resultReady=True))
        self.assertEqual([p['kind'] for p in observations.summary([])['pulses']], ['result_ready'])


class SameHostWorkerTests(unittest.TestCase):
    def test_remote_execution_keeps_worker_and_reviewer_inference_hosts(self):
        fixture = integration_fixtures.Integration('test_coordinator_remote_mailbox_independent_inference')
        fixture.setUp()
        try:
            fixture.service.observations = ai.AIObservations()
            fixture.service.workspaces.observer = fixture.service.observations.worker
            task = fixture.start('msi')
            self.assertEqual(task['status'], 'READY_FOR_REVIEW')
            snapshot = fixture.service.observation_summary()
            flow = snapshot['selectedFlow']
            self.assertEqual((flow['executionHost'], flow['worker']['host'], flow['reviewer']['host']), ('msi', 'local', 'msi'))
            self.assertEqual(flow['projectId'], 'fixture')
            self.assertEqual({(p['host'], p['role']) for p in snapshot['pulses'] if 'role' in p}, {('local', 'worker'), ('msi', 'reviewer')})
            self.assertEqual(flow['resultState'], 'review_required')
            self.assertEqual(sum(p['kind'] == 'result_ready' for p in snapshot['pulses']), 1)
        finally:
            fixture.tearDown()

    def test_distinct_same_host_reviewer_and_explicit_application(self):
        class TwinRuntime(FixtureRuntime):
            def inventory(self):
                value = super().inventory()
                value['models'].append(dict(name='reviewer', digest='b' * 64, size=10, capabilities=['completion', 'tools']))
                return value
        with tempfile.TemporaryDirectory(prefix='interlinked-ai-web-worker-') as tmp:
            root = Path(tmp)
            source = root / 'project'; source.mkdir()
            (source / 'example.py').write_text('answer = 1\n', encoding='utf-8')
            master = SimpleNamespace(state=lambda: dict(settings=dict(projects=[dict(id='fixture', label='Fixture', root=str(source))])))
            service = ai.AIService(SimpleNamespace(state=root, kit=root, demo=True), master=master, node_factory=TwinRuntime)
            try:
                service.workspaces.acquire = lambda project: nullcontext()
                service.refresh()
                for model in ('writer', 'reviewer'):
                    for capability in ('text', 'coding', 'tools'):
                        service.calibrate(dict(host='local', model=model, capability=capability))
                service.workspaces.approve('fixture')
                service.observations = ai.AIObservations()
                service.workspaces.observer = service.observations.worker
                task = service.start_task(dict(requestId=uuid.uuid4().hex, projectId='fixture', prompt='PRIVATE change',
                    host='local', model='writer', executionHost='local', permissions=['read', 'write'], origin='violet_bridge'))
                service.workspaces.threads[task['id']].join(10)
                task = service.workspaces.task(task['id'])
                self.assertEqual(task['status'], 'READY_FOR_REVIEW', task)
                snapshot = service.observation_summary()
                flow = snapshot['selectedFlow']
                self.assertEqual((flow['origin'], flow['executionHost'], flow['workerId']), ('assistant', 'local', task['id']))
                self.assertEqual((flow['worker']['host'], flow['reviewer']['host']), ('local', 'local'))
                self.assertNotEqual(flow['worker']['digest'], flow['reviewer']['digest'])
                self.assertEqual({p['role'] for p in snapshot['pulses'] if 'role' in p}, {'worker', 'reviewer'})
                self.assertEqual(flow['resultState'], 'review_required')
                self.assertEqual((source / 'example.py').read_text(), 'answer = 1\n')
                self.assertNotIn('PRIVATE', json.dumps(snapshot))
                service.workspaces.apply(task['id'], task['result']['sha256'])
                self.assertEqual(service.observation_summary()['selectedFlow']['resultState'], 'applied')
                self.assertIn('result_applied', [p['kind'] for p in service.observation_summary()['pulses']])
                self.assertEqual((source / 'example.py').read_text(), 'answer = 2\n')
            finally:
                service.close()


if __name__ == '__main__': unittest.main()
