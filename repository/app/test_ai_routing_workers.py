"""Async Auto worker identity, cancellation and deadline integration fixtures."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock
import uuid
import copy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ai_integration as integration_fixture


class RoutingWorkers(unittest.TestCase):
    def setUp(self):
        integration_fixture.Integration.setUp(self)
        self.entered, self.release = threading.Event(), threading.Event()
        self.selections = 0
        self.selection_activities = []

        def select(capabilities, body, prompt, **kwargs):
            self.selections += 1
            self.selection_activities.append(copy.deepcopy(kwargs.get('activity')))
            self.entered.set()
            if not body.get('model'):
                self.release.wait(3)
            self.service._routing_guard(kwargs['settings_snapshot'], kwargs['cancel_event'], kwargs['deadline'])
            model = self.service.route(capabilities, body)
            decision = dict(version=1, mode='balanced', task='coding',
                            classification=dict(task='coding', complexity='simple', responseFormat='code'))
            return model, decision
        self.service.select_for_request = select

    def tearDown(self):
        self.release.set()
        integration_fixture.Integration.tearDown(self)

    def body(self, **changes):
        return dict(projectId='fixture', prompt='Change answer to 2.', executionHost='local',
                    permissions=['read', 'write'], checkProfiles=[], requestId=uuid.uuid4().hex,
                    timeoutSeconds=30, **changes)

    def wait(self, request_id):
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            row = self.service.task(request_id)
            if row['status'] not in ('QUEUED', 'STARTING', 'PREPARING', 'RUNNING', 'REVIEWING'):
                return row
            time.sleep(.01)
        self.fail('Worker did not finish: ' + repr(row))

    def test_auto_identity_is_stable_through_completion_retry_list_and_apply(self):
        body = self.body()
        first = self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        self.assertEqual(first['id'], body['requestId'])
        self.assertEqual(self.service.start_task(body)['id'], first['id'])
        self.release.set()
        row = self.wait(first['id'])
        self.assertEqual(row['status'], 'READY_FOR_REVIEW', row)
        self.assertEqual(row['id'], first['id'])
        self.assertNotEqual(row['workerId'], first['id'])
        self.assertEqual(self.service.get('task', {'id': [first['id']]})['id'], first['id'])
        self.assertEqual(self.service.start_task(body)['id'], first['id'])
        self.assertEqual(self.selections, 1)
        listing = self.service.tasks()
        self.assertEqual([item['id'] for item in listing], [first['id']])
        applied = self.service.post('tasks/apply', dict(id=first['id'], expectedResultHash=row['result']['sha256']))
        self.assertEqual(applied['id'], first['id'])
        self.assertEqual(applied['status'], 'APPLIED')
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')

    def test_cancel_during_classification_never_starts_workspace(self):
        body = self.body()
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        row = self.service.cancel_task(body['requestId'])
        self.assertEqual(row['status'], 'CANCELED')
        self.release.set()
        self.assertEqual(self.wait(body['requestId'])['status'], 'CANCELED')
        self.assertEqual(self.service.workspaces.tasks(), [])
        self.assertEqual(self.service.start_task(body)['status'], 'CANCELED')
        self.assertEqual(self.selections, 1)

    def test_reentrant_startup_cancel_reaches_new_child(self):
        body = self.body()
        original = self.service.workspaces.start
        cancelled_children = []
        original_cancel = self.service.workspaces.cancel
        def start(payload, infer):
            self.service.cancel_task(body['requestId'])
            return original(payload, infer)
        def cancel(child):
            cancelled_children.append(child)
            return original_cancel(child)
        with mock.patch.object(self.service.workspaces, 'start', side_effect=start), \
             mock.patch.object(self.service.workspaces, 'cancel', side_effect=cancel):
            self.service.start_task(body)
            self.assertTrue(self.entered.wait(1))
            self.release.set()
            row = self.wait(body['requestId'])
        self.assertEqual(row['id'], body['requestId'])
        self.assertIn(row['workerId'], cancelled_children)
        self.assertNotEqual(row['status'], 'READY_FOR_REVIEW')

    def test_selection_time_is_subtracted_from_worker_budget(self):
        body = self.body()
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        with self.service.lock:
            self.service._task_deadlines[body['requestId']] = time.monotonic() + 20
        self.release.set()
        row = self.wait(body['requestId'])
        self.assertEqual(row['status'], 'READY_FOR_REVIEW', row)
        child = self.service.workspaces.task(row['workerId'])
        self.assertGreaterEqual(child['timeoutSeconds'], 10)
        self.assertLessEqual(child['timeoutSeconds'], 20)
        retained = self.service.read(self.service.root / 'task-requests.json')
        self.assertEqual(retained[body['requestId']]['payload']['timeoutSeconds'], child['timeoutSeconds'])

    def test_insufficient_remaining_budget_fails_without_start(self):
        body = self.body()
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        with self.service.lock:
            self.service._task_deadlines[body['requestId']] = time.monotonic() + 9
        self.release.set()
        row = self.wait(body['requestId'])
        self.assertEqual(row['status'], 'FAILED')
        self.assertIn('insufficient time', row['error'])
        self.assertEqual(self.service.workspaces.tasks(), [])

    def test_changed_duplicate_request_is_rejected_while_classifying(self):
        body = self.body()
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        with self.assertRaisesRegex(ValueError, 'different task'):
            self.service.start_task(dict(body, routingMode='fast'))
        self.service.cancel_task(body['requestId'])
        self.release.set()

    def test_worker_selection_uses_parent_flow_and_cancellation_closes_it(self):
        body = self.body()
        flow_id = 'worker:' + body['requestId']
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        self.assertEqual(self.selection_activities, [dict(origin='assistant', flowId=flow_id, role='analysis')])
        self.service.cancel_task(body['requestId'])
        self.release.set()
        limit = time.monotonic() + 3
        while time.monotonic() < limit and self.service.observations.flows[flow_id]['phase'] != 'cancelled':
            time.sleep(.01)
        self.assertEqual(self.service.observations.flows[flow_id]['phase'], 'cancelled')
        self.assertEqual(self.service.workspaces.tasks(), [])

    def test_manual_start_preserves_synchronous_child_identity(self):
        body = self.body(host='local', model='writer')
        row = self.service.start_task(body)
        self.assertNotEqual(row['id'], body['requestId'])
        self.assertIn(row['id'], self.service.workspaces.threads)
        self.assertEqual(self.service.start_task(body)['id'], row['id'])
        self.service.workspaces.threads[row['id']].join(5)
        self.assertEqual(self.service.task(row['id'])['status'], 'READY_FOR_REVIEW')

    def test_recovered_pending_selection_is_not_replayed(self):
        body = self.body()
        self.service.start_task(body)
        self.assertTrue(self.entered.wait(1))
        with self.service.lock:
            interrupted = copy.deepcopy(self.service.task_requests)
        self.service.cancel_task(body['requestId'])
        self.release.set()
        self.service.close()
        # The retained snapshot represents a crash before classifier completion.
        self.service.write(self.service.root / 'task-requests.json', interrupted)
        original = self.service
        self.service = integration_fixture.ai.AIService(original.runtime, self.controller, self.master,
                                                       node_factory=integration_fixture.FixtureRuntime)
        row = self.service.start_task(body)
        self.assertEqual(row['id'], body['requestId'])
        self.assertEqual(row['status'], 'INTERRUPTED')
        self.assertEqual(self.service.workspaces.tasks(), [])
        self.assertEqual(self.service.nodes['local'].calls, [])


if __name__ == '__main__':
    unittest.main()
