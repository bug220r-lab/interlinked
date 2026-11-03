"""Synthetic display fencing/recovery and reduced-observation tests; no USB access."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('master_display_fixture', Path(__file__).with_name('interlinked_master.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-display-fixture-')
        self.root = Path(self.temp.name)
        self.service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
            SimpleNamespace(state=lambda: dict(busy=False)), start=False)
        self.ensure = patch.object(self.service, '_ensure_worker')
        self.ensure.start()

    def tearDown(self):
        self.service._worker = None
        self.service.close()
        self.ensure.stop()
        self.temp.cleanup()

    def action(self, connect, now=100):
        if connect and self.service._worker is None:
            self.service._worker=SimpleNamespace(poll=lambda:None)
        with patch.object(m.time, 'monotonic', return_value=now):
            return self.service.display(dict(connect=connect))

    def report(self, now=101, **fields):
        with patch.object(m.time, 'monotonic', return_value=now):
            self.service.accept_worker_status(dict({'type':'status', 'requestId':self.service._request_serial}, **fields))

    def stream(self, now=100):
        self.action(True, now)
        self.report(now+1, displayStatus='connected', connected=True)
        self.report(now+2, type='stats', acknowledgedFrames=10, attemptAcknowledgedFrames=10)

    def fail(self, now=104, code='ack_timeout', retryable=True):
        self.report(now, displayStatus='unavailable', connected=False, cleanupComplete=True,
            errorCode=code, retryable=retryable, error='Fixture transport failure')

    def test_wire_ids_increase_and_late_connected_cannot_undo_disconnect(self):
        self.action(True)
        old_id = self.service._request_serial
        self.action(False)
        commands = list(self.service.commands)
        self.assertLess(commands[-2]['requestId'], commands[-1]['requestId'])
        self.service.accept_worker_status(dict(type='status', requestId=old_id, connected=True, displayStatus='connected'))
        self.assertFalse(self.service.state()['display']['connected'])
        self.assertFalse(self.service.state()['display']['desiredConnected'])
        self.report(displayStatus='disconnected', connected=False, cleanupComplete=True)
        self.assertEqual(self.service.state()['display']['status'], 'disconnected')

    def test_duplicate_connect_does_not_extend_attempt_or_reset_recovery(self):
        self.action(True)
        before = (self.service._request_serial, self.service._attempt_deadline, len(self.service.commands))
        self.action(True, 110)
        self.assertEqual(before, (self.service._request_serial, self.service._attempt_deadline, len(self.service.commands)))

    def test_initial_handshake_failure_is_manual_and_retains_real_error(self):
        self.action(True)
        error = 'Expected 480 x 480 Peerless Vision PM4 handshake was not received.'
        self.report(displayStatus='unavailable', connected=False, cleanupComplete=True,
            errorCode='handshake_timeout', retryable=True, error=error)
        state = self.service.state()['display']
        self.assertEqual(state['errorCode'], 'handshake_timeout')
        self.assertEqual(state['message'], error)
        self.assertFalse(state['desiredConnected'])
        self.assertEqual(self.service._retry_at, 0)

    def test_handshake_without_acknowledged_picture_does_not_arm_recovery(self):
        self.action(True)
        self.report(displayStatus='connected', connected=True)
        self.report(type='stats', acknowledgedFrames=100, attemptAcknowledgedFrames=0)
        self.fail()
        self.assertFalse(self.service._recovery_armed)
        self.assertEqual(self.service._retry_at, 0)

    def test_first_ack_arms_recovery_before_the_periodic_stats_event(self):
        self.action(True)
        self.report(101,displayStatus='connected',connected=True)
        self.report(101.1,type='delivery',attemptAcknowledgedFrames=1)
        self.fail(101.2)
        self.assertEqual(self.service.state()['display']['status'],'retry_wait')
        self.assertEqual(self.service._retry_at,103.2)

    def test_disconnect_with_an_unavailable_worker_never_launches_a_replacement(self):
        self.stream();self.fail()
        self.service._worker=None
        before=self.service._request_serial
        with patch.object(self.service,'_ensure_worker',side_effect=ValueError('Fixture worker unavailable')) as ensure:
            self.action(False,105)
        ensure.assert_not_called()
        self.assertGreater(self.service._request_serial,before)
        self.assertFalse(self.service.state()['display']['desiredConnected'])
        self.assertEqual(self.service.state()['display']['status'],'disconnected')
        self.service._display_tick(300)
        self.assertEqual(self.service._retry_at,0)

    def test_recovery_backoff_is_three_attempts_with_one_deadline(self):
        self.stream()
        self.fail(104)
        self.assertEqual(self.service._recovery_until, 179)
        for attempt, due in [(1,106), (2,116), (3,131)]:
            state = self.service.state()['display']
            self.assertEqual(state['status'], 'retry_wait')
            self.assertEqual(state['retryAttempt'], attempt)
            self.assertEqual(self.service._retry_at, due)
            self.assertIsInstance(state['nextRetryAt'], str)
            self.service._display_tick(due-0.1)
            previous = self.service._request_serial
            self.service._display_tick(due)
            self.assertEqual(self.service._request_serial, previous+1)
            self.assertEqual(self.service.state()['display']['status'], 'reconnecting')
            self.fail(due+5)
        self.assertEqual(self.service.state()['display']['errorCode'], 'recovery_exhausted')
        self.assertFalse(self.service.state()['display']['desiredConnected'])
        self.assertEqual(self.service._retry_at, 0)

    def test_disconnect_cancels_backoff_and_does_not_start_another_worker(self):
        self.stream(); self.fail()
        self.action(False,105)
        before = len(self.service.commands)
        self.report(106, displayStatus='disconnected', connected=False, cleanupComplete=True)
        self.service._display_tick(150)
        self.assertEqual(len(self.service.commands), before)
        self.assertFalse(self.service._recovery_armed)
        self.assertFalse(self.service.state()['display']['desiredConnected'])

    def test_policy_failure_never_retries_an_established_stream(self):
        for code in ('writer_conflict','map_ownership_conflict','pin_validation_failed','containment_failed'):
            self.stream(); self.fail(code=code,retryable=False)
            self.assertEqual(self.service._retry_at,0,code)
            self.assertFalse(self.service.state()['display']['desiredConnected'],code)

    def test_attempt_timeout_waits_for_cleanup_before_recovery(self):
        self.stream(); self.fail(104); self.service._display_tick(106)
        self.service._display_tick(126)
        self.assertEqual(self.service.state()['display']['status'],'cleaning_up')
        cancel_id = self.service._request_serial
        self.assertEqual(list(self.service.commands)[-1],dict(op='display',connect=False,requestId=cancel_id))
        self.assertEqual(self.service._retry_at,0)
        self.report(127, displayStatus='disconnected',connected=False,cleanupComplete=True)
        self.assertEqual(self.service._retry_at,132)

    def test_cleanup_deadline_retires_only_owned_worker_and_failure_is_terminal(self):
        self.action(True)
        self.service._display_tick(120)
        owned = object(); self.service._worker = owned
        with patch.object(self.service,'_retire_worker',side_effect=ValueError('Owned child remains live')) as retire:
            self.service._display_tick(125)
        retire.assert_called_once_with(owned)
        self.assertEqual(self.service.state()['display']['errorCode'],'cleanup_failed')
        self.assertFalse(self.service.state()['display']['desiredConnected'])

    def test_transport_heartbeat_is_independent_of_renderer_stats(self):
        self.stream()
        self.report(109,type='stats',acknowledgedFrames=11,attemptAcknowledgedFrames=11,renderFps=30)
        self.service._display_tick(109)
        self.assertEqual(self.service.state()['display']['errorCode'],'transport_unresponsive')
        self.assertEqual(self.service.state()['display']['status'],'cleaning_up')

    def test_stable_delivery_resets_budget_but_a_brief_success_does_not(self):
        self.stream(); self.fail(104); self.service._display_tick(106)
        self.report(107,displayStatus='connected',connected=True)
        self.report(108,type='stats',acknowledgedFrames=20,attemptAcknowledgedFrames=1)
        self.assertEqual(self.service._retry_index,1)
        for now in range(110,141,2):
            self.report(now,type='transport')
            self.report(now,type='stats',acknowledgedFrames=now,attemptAcknowledgedFrames=now)
            self.service._display_tick(now)
        self.assertEqual(self.service._retry_index,0)
        self.assertEqual(self.service._recovery_until,0)

    def test_recovery_total_window_cancels_an_inflight_attempt(self):
        self.stream(); self.fail(104)
        self.service._recovery_until=106
        self.service._display_tick(106)
        self.assertEqual(self.service.state()['display']['errorCode'],'recovery_exhausted')
        self.assertFalse(self.service._recovery_armed)
        cancel_id=self.service._request_serial
        self.service._display_tick(107)
        self.assertEqual(self.service._request_serial,cancel_id,'Expiry must not continually extend its cleanup deadline')
        self.report(107,displayStatus='disconnected',connected=False,cleanupComplete=True)
        self.assertFalse(self.service.state()['display']['desiredConnected'])

    def test_old_worker_pipe_failure_cannot_cancel_current_worker(self):
        old = SimpleNamespace(poll=lambda:None,stdin=io.BytesIO())
        current = object();self.service._worker=current
        self.service._write_worker(old)
        self.assertIsNone(self.service._pending_failure)

    def test_old_worker_status_is_rejected_even_if_request_number_matches(self):
        self.action(True)
        old=object();current=object();self.service._worker=current
        self.service.accept_worker_status(dict(type='status',requestId=self.service._request_serial,
            connected=True,displayStatus='connected'),worker=old)
        self.assertFalse(self.service.state()['display']['connected'])

    def test_pipe_failure_during_backoff_cannot_start_a_retry_before_cleanup(self):
        self.stream();self.fail(104)
        self.service._cancel_failed_attempt('command_pipe_failed','Fixture pipe failure',True,105)
        cancel_id=self.service._request_serial
        self.service._display_tick(106)
        self.assertEqual(self.service._request_serial,cancel_id)
        self.assertEqual(self.service._retry_at,0)
        self.assertEqual(self.service.state()['display']['status'],'cleaning_up')
        self.report(107,displayStatus='disconnected',connected=False,cleanupComplete=True)
        self.assertEqual(self.service._retry_at,112)

    def assert_collector_cannot_starve_watchdog(self, blocked):
        entered=threading.Event();release=threading.Event();cancelled=threading.Event()
        def refresh():
            entered.set()
            if blocked:release.wait(5)
            else:raise OSError('Fixture snapshot write failed')
        original=self.service._cancel_failed_attempt
        def cancel(*args,**kwargs):
            original(*args,**kwargs);cancelled.set()
        with patch.object(self.service,'refresh',side_effect=refresh),patch.object(self.service,'_cancel_failed_attempt',side_effect=cancel):
            collector=threading.Thread(target=self.service._loop)
            maintenance=threading.Thread(target=self.service._maintenance_loop)
            collector.start()
            try:
                self.assertTrue(entered.wait(2))
                self.action(True,time.monotonic()-21)
                maintenance.start()
                self.assertTrue(cancelled.wait(1.5),'Independent maintenance must expire the attempt while collection is blocked or failing')
                self.assertEqual(self.service.state()['display']['errorCode'],'attempt_timeout')
            finally:
                self.service.stop.set();release.set();collector.join(2)
                if maintenance.ident is not None:maintenance.join(2)
            self.assertFalse(collector.is_alive())
            self.assertFalse(maintenance.is_alive())

    def test_blocked_collector_does_not_starve_connection_watchdog(self):
        self.assert_collector_cannot_starve_watchdog(True)

    def test_failing_collector_does_not_skip_connection_watchdog(self):
        self.assert_collector_cannot_starve_watchdog(False)

    def test_ai_summary_is_collected_outside_master_lock_and_fails_closed(self):
        calls=[]
        def summary():
            self.assertFalse(self.service.lock._is_owned())
            calls.append(True)
            return dict(available=True,observedAt=m.utc(),hosts=[],flows=[],pulses=[],partial=False)
        self.service.ai_service=SimpleNamespace(observation_summary=summary)
        self.service.refresh(collect_git=False)
        self.assertEqual(len(calls),1)
        self.assertTrue(self.service.state()['ai']['available'])
        self.service.ai_service=SimpleNamespace(observation_summary=lambda: (_ for _ in ()).throw(OSError('fixture unavailable')))
        self.service.refresh(collect_git=False)
        self.assertFalse(self.service.state()['ai']['available'])

    def test_layout_defaults_and_allowlist(self):
        self.assertEqual(self.service._worker_settings()['layout'],'command_deck')
        self.service.settings(dict(layout='violet_web',expectedRevision=self.service.state()['settingsRevision']))
        self.assertEqual(self.service._worker_settings()['layout'],'violet_web')
        with self.assertRaises(ValueError):self.service._validate_settings(dict(layout='unknown'))

    def test_nonretryable_failure_ends_startup_window_without_another_acquisition(self):
        self.action(True)
        self.service._auto_start_until=250
        self.service._auto_connect_sent=True
        self.fail(code='writer_conflict',retryable=False)
        self.assertEqual(self.service._auto_start_until,0)
        self.assertFalse(self.service._auto_connect_sent)
        self.assertEqual(self.service.state()['display']['errorCode'],'writer_conflict')

    def test_configuration_change_cancels_recovery_but_preserves_stable_display(self):
        self.stream();self.fail()
        self.service._suspend_recovery('Fixture configuration changed')
        self.assertFalse(self.service._recovery_armed)
        self.assertEqual(self.service._retry_at,0)
        self.assertEqual(self.service.state()['display']['status'],'cleaning_up')
        self.report(displayStatus='disconnected',connected=False,cleanupComplete=True)
        self.assertFalse(self.service.state()['display']['desiredConnected'])
        self.action(True,120)
        self.report(121,displayStatus='connected',connected=True)
        self.service._suspend_recovery('Fixture stable display retained')
        self.assertTrue(self.service.state()['display']['connected'])
        self.report(122,type='stats',acknowledgedFrames=10,attemptAcknowledgedFrames=1)
        self.assertFalse(self.service._recovery_armed)


class GraphTests(unittest.TestCase):
    def event(self,kind,session='main',agent=None):
        value=dict(hook_event_name=kind,session_id=session,cwd=str(Path(tempfile.gettempdir())/'fixture-project'),observedAt=m.utc())
        if agent:value['agent_id']=agent
        return m.sanitize_hook(value)

    def test_confirmed_edges_are_stable_and_unknown_parent_is_retained(self):
        model=m.EventModel()
        model.accept(self.event('UserPromptSubmit'))
        model.accept(self.event('SubagentStart',agent='child'))
        model.accept(self.event('SubagentStart',session='unobserved',agent='orphan'))
        rows=[dict(id='fixture',root=str(Path(tempfile.gettempdir())/'fixture-project'))]
        graph=model.lifecycle_graph(rows)
        main=next(row for row in graph['nodes'] if row['kind']=='codex_main')
        child=next(row for row in graph['nodes'] if row.get('parentNodeId'))
        orphan=next(row for row in graph['nodes'] if row['kind']=='codex_child' and 'parentNodeId' not in row)
        self.assertEqual(child['parentNodeId'],main['nodeId'])
        self.assertEqual(orphan['confidence'],'partial')
        self.assertEqual(graph,model.lifecycle_graph(rows))
        self.assertNotIn('cwd',json.dumps(graph))
        self.assertNotIn('model',json.dumps(graph))

    def test_graph_is_bounded_and_stale_activity_has_no_live_claim(self):
        model=m.EventModel()
        for index in range(70):model.accept(self.event('UserPromptSubmit',session='session'+str(index)))
        graph=model.lifecycle_graph([],now=m.time.time()+181)
        self.assertEqual(len(graph['nodes']),64)
        self.assertEqual(graph['omittedCount'],6)
        self.assertTrue(graph['partial'])
        self.assertTrue(all(row['status']=='unknown' and row['confidence']=='stale' for row in graph['nodes']))

    def test_contradictory_cyclic_edges_remain_unknown_links(self):
        model=m.EventModel()
        model.accept(self.event('SubagentStart',session='a',agent='b'))
        model.accept(self.event('SubagentStart',session='b',agent='a'))
        graph=model.lifecycle_graph([])
        self.assertEqual(len(graph['nodes']),2)
        self.assertTrue(graph['partial'])
        self.assertTrue(all('parentNodeId' not in row for row in graph['nodes']))


if __name__=='__main__':unittest.main(verbosity=2)
