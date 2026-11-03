"""Isolated reconnect ownership and startup/shutdown contracts; no USB or sensors."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('master_reconnect_fixture', Path(__file__).with_name('interlinked_master.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReconnectContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-reconnect-contract-')
        self.root = Path(self.temp.name)
        self.service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
            SimpleNamespace(state=lambda: dict(busy=False)), start=False)

    def tearDown(self):
        self.service._worker = None
        self.service.close()
        self.temp.cleanup()

    def action(self, connect, now=100):
        with patch.object(self.service, '_ensure_worker'), patch.object(m.time, 'monotonic', return_value=now):
            return self.service.display(dict(connect=connect))

    def report(self, now=101, **fields):
        with patch.object(m.time, 'monotonic', return_value=now):
            self.service.accept_worker_status(dict({'type':'status', 'requestId':self.service._request_serial}, **fields))

    def stream(self):
        self.action(True)
        self.report(101, displayStatus='connected', connected=True)
        self.report(102, type='delivery', attemptAcknowledgedFrames=1)

    def failure(self, now=104):
        self.report(now, displayStatus='unavailable', connected=False, cleanupComplete=True,
            errorCode='ack_timeout', retryable=True, error='Synthetic acknowledgment timeout')

    def arm_startup(self):
        self.service._auto_start_until = 250
        self.service._auto_sensor_until = 0
        self.service._auto_connect_until = 200
        self.service._auto_start_next = 105
        self.service._auto_connect_sent = True

    def test_startup_does_not_replace_disconnect_while_cleanup_is_pending(self):
        self.action(True)
        self.arm_startup()
        self.service._display_tick(120)
        serial = self.service._request_serial
        deadline = self.service._cleanup_deadline
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure:
            self.service._automatic_startup_tick(120)
        self.assertEqual(self.service._request_serial, serial, 'Startup must not fence away its watchdog Disconnect')
        self.assertEqual(self.service._cleanup_deadline, deadline, 'Startup must retain the original five-second cleanup deadline')
        self.assertEqual(self.service.state()['display']['status'], 'cleaning_up')
        ensure.assert_not_called()

    def test_startup_waits_for_explicit_disconnect_cleanup(self):
        self.service._worker = SimpleNamespace(poll=lambda:None)
        self.action(False, 100)
        self.arm_startup()
        serial = self.service._request_serial
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure:
            self.service._automatic_startup_tick(106)
        ensure.assert_not_called()
        self.assertEqual(self.service._request_serial, serial)
        self.assertEqual(self.service._cleanup_deadline, 105)

    def test_startup_does_not_create_an_extra_attempt_during_recovery_backoff(self):
        self.stream()
        self.failure()
        self.arm_startup()
        serial = self.service._request_serial
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure:
            self.service._automatic_startup_tick(105)
        ensure.assert_not_called()
        self.assertEqual(self.service._request_serial, serial)
        self.assertEqual(self.service._retry_at, 106)

    def test_retirement_waits_for_predecessor_exit_before_another_start(self):
        entered = threading.Event()
        release = threading.Event()
        started = threading.Event()
        exited = threading.Event()
        errors = []
        class Worker:
            stdin = io.BytesIO()
            def poll(self): return 0 if exited.is_set() else None
            def kill(self): pass
            def wait(self, timeout):
                entered.set()
                if not release.wait(timeout): raise subprocess.TimeoutExpired('synthetic-worker', timeout)
                exited.set()
                return 0
        old = Worker()
        self.service._worker = old
        def run(action):
            try: action()
            except BaseException as exc: errors.append(exc)
        def start_locked(*args):
            self.assertTrue(exited.is_set(), 'A replacement cannot start while its predecessor remains alive')
            started.set()
        with patch.object(self.service, '_start_worker_locked', side_effect=start_locked):
            retire = threading.Thread(target=run, args=(lambda:self.service._retire_worker(old),))
            start = threading.Thread(target=run, args=(self.service._start_worker,))
            retire.start()
            try:
                self.assertTrue(entered.wait(1))
                start.start()
                self.assertFalse(started.wait(.05), 'The worker start lock must remain held throughout exit confirmation')
            finally:
                release.set()
                retire.join(2)
                if start.ident is not None: start.join(2)
        self.assertFalse(retire.is_alive())
        self.assertFalse(start.is_alive())
        self.assertTrue(started.is_set())
        self.assertEqual(errors, [])

    def test_failed_retirement_retains_the_live_owned_worker_and_blocks_replacement(self):
        old = SimpleNamespace(poll=lambda:None, kill=lambda:None,
            wait=lambda timeout:(_ for _ in ()).throw(subprocess.TimeoutExpired('synthetic-worker', timeout)))
        self.service._worker = old
        with self.assertRaisesRegex(ValueError, 'did not finish cleanup'):
            self.service._retire_worker(old)
        self.assertIs(self.service._worker, old)
        with patch.object(m.subprocess, 'Popen') as launch:
            self.service._ensure_worker()
        launch.assert_not_called()

    def test_normal_shutdown_allows_the_full_cleanup_budget_before_forced_kill(self):
        waits = []
        killed = []
        old = SimpleNamespace(poll=lambda:None, kill=lambda:killed.append(True), wait=lambda timeout:waits.append(timeout))
        self.service._worker = old
        self.service.close()
        self.assertEqual(waits, [5], 'Graceful helper exit and bounded fallback need the five-second shutdown allowance')
        self.assertEqual(killed, [])
        self.assertEqual(list(self.service.commands)[-1], dict(op='quit'))

    def test_broken_command_pipe_fences_the_attempt_and_waits_for_cleanup(self):
        class BrokenInput:
            def write(self, data): raise BrokenPipeError('synthetic pipe closed')
            def flush(self): pass
        self.action(True)
        serial = self.service._request_serial
        old = SimpleNamespace(poll=lambda:None, stdin=BrokenInput())
        self.service._worker = old
        with patch.object(m.time, 'monotonic', return_value=105):
            self.service._write_worker(old)
        state = self.service.state()['display']
        self.assertEqual(state['errorCode'], 'command_pipe_failed')
        self.assertEqual(state['status'], 'cleaning_up')
        self.assertGreater(self.service._request_serial, serial)
        self.assertEqual(self.service._cleanup_deadline, 110)
        self.assertEqual(self.service._retry_at, 0)

    def test_cleanup_refusal_never_schedules_an_established_stream_retry(self):
        self.stream()
        self.report(104, displayStatus='unavailable', connected=False, cleanupComplete=False,
            errorCode='cleanup_failed', retryable=False, error='Synthetic owned helper remains live')
        self.assertEqual(self.service.state()['display']['status'], 'cleaning_up')
        self.assertEqual(self.service._retry_at, 0)
        self.report(105, displayStatus='disconnected', connected=False, cleanupComplete=True)
        state = self.service.state()['display']
        self.assertEqual(state['errorCode'], 'cleanup_failed')
        self.assertFalse(state['desiredConnected'])
        self.assertEqual(self.service._retry_at, 0)

    def test_manual_connect_starts_a_fresh_retry_budget_after_exhaustion(self):
        self.stream()
        self.service._retry_index = 3
        self.failure(104)
        self.assertEqual(self.service.state()['display']['errorCode'], 'recovery_exhausted')
        previous = self.service._request_serial
        self.action(True, 105)
        self.assertGreater(self.service._request_serial, previous)
        self.assertEqual(self.service._retry_index, 0)
        self.assertEqual(self.service._recovery_until, 0)
        self.assertFalse(self.service._recovery_armed)
        self.assertEqual(self.service.state()['display']['status'], 'connecting')

    def test_delivery_gap_restarts_the_thirty_second_stability_interval(self):
        self.stream()
        self.failure(104)
        with patch.object(self.service, '_ensure_worker'):
            self.service._display_tick(106)
        self.report(107, displayStatus='connected', connected=True)
        self.report(108, type='stats', acknowledgedFrames=20, attemptAcknowledgedFrames=1)
        for now in (110, 112, 114, 116, 118, 120, 127, 129, 131, 133, 135, 137, 139):
            self.report(now, type='transport')
            self.report(now, type='stats', acknowledgedFrames=now, attemptAcknowledgedFrames=now)
            self.service._display_tick(now)
        self.assertEqual(self.service._retry_index, 1, 'Seven-second delivery loss must prevent an old stable timer resetting the budget')
        self.assertEqual(self.service._stable_since, 127)

    def pending(self, action, ensure):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Fixture release barrier expired')
            return ensure(*args, **kwargs)
        def run():
            try:
                action()
            except BaseException as exc:
                errors.append(exc)
        patcher = patch.object(self.service, '_ensure_worker', side_effect=blocked)
        patcher.start()
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(1))
        return release, thread, errors, patcher

    def finish_pending(self, pending):
        release, thread, errors, patcher = pending
        release.set()
        thread.join(4)
        patcher.stop()
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_disconnect_fences_pending_manual_connect_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                def ensure(**kwargs):
                    if fail:
                        raise ValueError('Synthetic unavailable worker')
                pending = self.pending(lambda:self.service.display(dict(connect=True)), ensure)
                try:
                    reserved = self.service._request_serial
                    self.assertTrue(self.service.state()['display']['desiredConnected'])
                    self.service.display(dict(connect=False))
                    before = self.service.state()['display']
                    self.assertGreater(self.service._request_serial, reserved)
                finally:
                    self.finish_pending(pending)
                self.assertEqual(self.service.state()['display'], before)
                self.assertFalse(any(c.get('connect') for c in self.service.commands))

    def test_older_connect_cannot_override_a_newer_manual_connect(self):
        calls = []
        entered, release = threading.Event(), threading.Event()
        errors = []
        def ensure(**kwargs):
            calls.append(kwargs['expected_request_id'])
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError('Fixture release barrier expired')
        def first():
            try:self.service.display(dict(connect=True))
            except BaseException as exc:errors.append(exc)
        with patch.object(self.service, '_ensure_worker', side_effect=ensure):
            thread = threading.Thread(target=first);thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.service.display(dict(connect=False))
                self.service.display(dict(connect=True))
                newest = self.service._request_serial
                before = self.service.state()['display']
            finally:
                release.set();thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(self.service.state()['display'], before)
        self.assertEqual([c for c in self.service.commands if c.get('connect')],
            [dict(op='display', connect=True, requestId=newest)])

    def test_duplicate_connect_during_startup_does_not_extend_its_identity_or_deadline(self):
        pending = self.pending(lambda:self.service.display(dict(connect=True)), lambda **kwargs:None)
        try:
            before = (self.service._request_serial, self.service._attempt_deadline)
            self.service.display(dict(connect=True))
            self.assertEqual((self.service._request_serial, self.service._attempt_deadline), before)
        finally:
            self.finish_pending(pending)
        self.assertEqual(len([c for c in self.service.commands if c.get('connect')]), 1)

    def test_late_startup_cannot_dispatch_before_watchdog_has_run(self):
        clock = [100.0]
        with patch.object(m.time, 'monotonic', side_effect=lambda:clock[0]):
            request = self.service._request_display(True, enqueue=False)
            with patch.object(self.service, '_ensure_worker', side_effect=lambda **kwargs:clock.__setitem__(0, 121.0)):
                self.service._finish_display_connect(request)
        self.assertEqual(self.service.state()['display']['errorCode'], 'attempt_timeout')
        self.assertFalse(any(c.get('connect') for c in self.service.commands))

    def test_shutdown_fences_a_connect_waiting_for_startup(self):
        pending = self.pending(lambda:self.service.display(dict(connect=True)), lambda **kwargs:None)
        try:self.service.stop.set()
        finally:self.finish_pending(pending)
        self.assertFalse(any(c.get('connect') for c in self.service.commands))

    def test_worker_start_failure_cannot_publish_over_a_newer_disconnect(self):
        worker_path = self.root/'synthetic-worker'
        worker_path.write_bytes(b'not an executable; process start is never reached')
        self.service.worker_path = worker_path
        entered, release = threading.Event(), threading.Event()
        errors = []
        def settings():
            entered.set()
            if not release.wait(3):raise AssertionError('Fixture release barrier expired')
            raise ValueError('Synthetic seal/settings failure')
        def run():
            try:self.service.display(dict(connect=True))
            except BaseException as exc:errors.append(exc)
        with patch.object(self.service, '_worker_settings', side_effect=settings), patch.object(m.subprocess, 'Popen') as launch:
            thread = threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.service.display(dict(connect=False))
                before = self.service.state()['display']
            finally:release.set();thread.join(4)
        self.assertEqual(errors, [])
        launch.assert_not_called()
        self.assertEqual(self.service.state()['display'], before)

    def test_waiter_carries_identity_from_before_the_worker_start_lock(self):
        entered = threading.Event()
        errors = []
        original = self.service._start_worker
        def start(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)
        def run():
            try:self.service.display(dict(connect=True))
            except BaseException as exc:errors.append(exc)
        with patch.object(self.service, '_start_worker', side_effect=start), patch.object(m.subprocess, 'Popen') as launch:
            with self.service.worker_start_lock:
                thread = threading.Thread(target=run);thread.start()
                self.assertTrue(entered.wait(1))
                self.service.display(dict(connect=False))
                before = self.service.state()['display']
            thread.join(4)
        self.assertEqual(errors, [])
        launch.assert_not_called()
        self.assertEqual(self.service.state()['display'], before)

    def test_manual_disconnect_fences_recovery_and_startup_success_or_failure(self):
        for automatic in (False, True):
            for fail in (False, True):
                with self.subTest(automatic=automatic, fail=fail):
                    self.service.commands.clear()
                    self.service._cleanup_deadline = 0
                    self.service._attempt_deadline = 0
                    self.service._connect_intent = False
                    self.service._recovery_until = 0
                    if automatic:
                        self.service._auto_start_until = 200
                        self.service._auto_sensor_until = 0
                        self.service._auto_connect_until = 0
                        self.service._auto_start_next = 0
                        self.service._state['temperatures']['status'] = 'available'
                        action = lambda:self.service._automatic_startup_tick(100)
                    else:
                        self.service._connect_intent = True
                        self.service._recovery_armed = True
                        self.service._recovery_until = 175
                        self.service._retry_at = 100
                        action = lambda:self.service._display_tick(100)
                    def ensure(**kwargs):
                        if fail:raise ValueError('Synthetic unavailable worker')
                    with patch.object(self.service, '_automatic_startup_allowed', return_value=True):
                        pending = self.pending(action, ensure)
                        try:
                            self.service.display(dict(connect=False))
                            before = self.service.state()['display']
                        finally:self.finish_pending(pending)
                    self.assertEqual(self.service.state()['display'], before)
                    self.assertFalse(any(c.get('connect') for c in self.service.commands))

    def test_revoked_automatic_opt_in_prevents_pending_connect(self):
        allowed = [True]
        self.service._auto_start_until = 200
        self.service._auto_sensor_until = 0
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', side_effect=lambda:allowed[0]):
            pending = self.pending(lambda:self.service._automatic_startup_tick(100), lambda **kwargs:None)
            try:allowed[0] = False
            finally:self.finish_pending(pending)
        self.assertFalse(any(c.get('connect') for c in self.service.commands))
        self.assertEqual(self.service._auto_start_until, 0)

    def test_duplicate_manual_connect_does_not_cancel_the_reserved_automatic_attempt(self):
        self.service._auto_start_until = 200
        self.service._auto_sensor_until = 0
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True):
            pending = self.pending(lambda:self.service._automatic_startup_tick(100), lambda **kwargs:None)
            try:
                before = (self.service._request_serial, self.service._attempt_deadline, self.service._auto_start_until)
                self.service.display(dict(connect=True))
                self.assertEqual((self.service._request_serial, self.service._attempt_deadline, self.service._auto_start_until), before)
            finally:self.finish_pending(pending)
        self.assertEqual(len([c for c in self.service.commands if c.get('connect')]), 1)

    def test_current_startup_and_queue_failures_clear_only_their_reserved_intent(self):
        with patch.object(self.service, '_ensure_worker', side_effect=ValueError('Synthetic startup refusal')):
            with self.assertRaises(ValueError):self.service.display(dict(connect=True))
        self.assertFalse(self.service._connect_intent)
        self.assertEqual(self.service._attempt_deadline, 0)
        self.assertEqual(self.service.state()['display']['errorCode'], 'worker_unavailable')
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send', side_effect=ValueError('Synthetic queue refusal')):
            with self.assertRaises(ValueError):self.service.display(dict(connect=True))
        self.assertFalse(self.service._connect_intent)
        self.assertEqual(self.service._attempt_deadline, 0)
        self.assertEqual(self.service.state()['display']['errorCode'], 'command_queue_full')

    def test_sensor_disable_disconnect_and_replacement_fence_delayed_enable(self):
        self.service.runtime.demo = False
        for change in ('disable', 'disconnect', 'replace'):
            with self.subTest(change=change):
                self.service.commands.clear()
                self.service._worker = SimpleNamespace(poll=lambda:None)
                self.service._state['display']['connected'] = True
                self.service._connect_intent = True
                pending = self.pending(lambda:self.service.display(dict(action='enable_sensors')), lambda **kwargs:None)
                try:
                    if change == 'replace':
                        with self.service.lock:self.service._worker = SimpleNamespace(poll=lambda:None)
                    else:
                        self.service.display(dict(action='disable_sensors') if change == 'disable' else dict(connect=False))
                finally:self.finish_pending(pending)
                self.assertFalse(any(c.get('op') == 'sensors' and c.get('enable') for c in self.service.commands))
        self.service.runtime.demo = True

    def test_sensor_disable_does_not_start_an_absent_worker(self):
        self.service.runtime.demo = False
        with patch.object(self.service, '_ensure_worker') as ensure:
            self.service.display(dict(action='disable_sensors'))
        ensure.assert_not_called()
        self.service.runtime.demo = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        if args.output.exists(): raise SystemExit('Use an absent private output directory.')
        args.output.mkdir(parents=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReconnectContractTests))
    if args.output:
        record = dict(format='interlinked-master-reconnect-contract-v1', tests=result.testsRun,
            failures=len(result.failures), errors=len(result.errors), ok=result.wasSuccessful(),
            hardwareTested=False, usbRequested=False, productionWorkerStarted=False)
        (args.output/'report.json').write_text(json.dumps(record, indent=2)+'\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': raise SystemExit(main())
