"""Transient voice/recovery contracts; isolated state and in-memory HTTP, no devices."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('master_voice_fixture', HERE / 'interlinked_master.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
spec = importlib.util.spec_from_file_location('master_voice_gui_fixture', HERE / 'interlinked_gui.py')
g = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = g
spec.loader.exec_module(g)


class Clock:
    def __init__(self):
        self.wall = 1793649600.0
        self.mono = 100.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def stamp(self, offset=0):
        return datetime.fromtimestamp(self.wall + offset, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class VoiceActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.voice = m.CoolerVoiceActivity(lambda: self.clock.wall, lambda: self.clock.mono)

    def heartbeat(self, sequence=1, session=1, **fields):
        return dict(sessionId=f'{session:032x}', sequence=sequence,
            observedAt=self.clock.stamp(), listening=True, speaking='none', **fields)

    def test_initial_and_restart_are_empty_and_success_is_minimal(self):
        self.assertEqual(self.voice.snapshot()['sources'], [])
        self.assertEqual(self.voice.accept(self.heartbeat()), dict(accepted=True, sequence=1))
        fresh = m.CoolerVoiceActivity(lambda: self.clock.wall, lambda: self.clock.mono)
        self.assertEqual(fresh.snapshot()['sources'], [])

    def test_strict_full_state_and_utc_schema_rejects_text_and_unsafe_values(self):
        invalid = [None, [], {}, dict(self.heartbeat(), text='PRIVATE TRANSCRIPT'),
            {key: value for key, value in self.heartbeat().items() if key != 'listening'}]
        for key, values in {
            'sessionId': ['', 'a' * 31, 'A' * 32, 'g' * 32, 1, 'a' * 33],
            'sequence': [True, False, 0, -1, 1.0, '1', 9007199254740992],
            'observedAt': [None, 1, 'yesterday', '2026-11-02', '2026-11-02T12:00:00',
                '2026-11-02T13:00:00+01:00', '2026-02-30T12:00:00Z', self.clock.stamp() + '\n'],
            'listening': [None, 0, 1, 'true', {}],
            'speaking': [True, None, '', 'tts', 'PRIVATE TEXT', [], {}],
        }.items():
            invalid.extend(dict(self.heartbeat(), **{key: value}) for value in values)
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.voice.accept(body)
        self.assertEqual(self.voice.sessions, {})
        self.voice.accept(dict(self.heartbeat(), sequence=9007199254740991,
            observedAt=self.clock.stamp().replace('Z', '+00:00')))

    def test_future_and_five_second_old_events_are_rejected_without_a_lease(self):
        for offset in (.000001, 1, 60, -5, -6):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.voice.accept(dict(self.heartbeat(), observedAt=self.clock.stamp(offset)))
        self.assertEqual(self.voice.snapshot()['sources'], [])
        self.voice.accept(dict(self.heartbeat(), observedAt=self.clock.stamp(-4.99)))
        self.assertEqual(len(self.voice.snapshot()['sources']), 1)

    def test_exact_five_second_expiry_includes_event_age_and_polling_does_not_renew(self):
        event = dict(self.heartbeat(), observedAt=self.clock.stamp(-2))
        self.voice.accept(event)
        self.clock.advance(2.999999)
        for _ in range(4):
            self.assertEqual(self.voice.snapshot()['sources'][0]['observedAt'], event['observedAt'])
        self.clock.advance(.000001)
        self.assertEqual(self.voice.snapshot()['sources'], [])
        self.assertEqual(self.voice.sessions[event['sessionId']]['received'], 100)

    def test_wall_clock_changes_do_not_extend_activity(self):
        self.voice.accept(self.heartbeat())
        self.clock.wall -= 3600
        self.clock.mono += 5
        self.assertEqual(self.voice.snapshot()['sources'], [])

    def test_duplicate_and_out_of_order_do_not_renew_or_change_state(self):
        self.voice.accept(self.heartbeat(sequence=2))
        original = dict(self.voice.sessions[f'{1:032x}'])
        self.clock.advance(4)
        for sequence in (1, 2):
            with self.assertRaises(ValueError):
                self.voice.accept(dict(self.heartbeat(sequence=sequence), listening=False, speaking='browser_tts'))
        self.assertEqual(self.voice.sessions[f'{1:032x}'], original)
        self.clock.advance(1)
        self.assertEqual(self.voice.snapshot()['sources'], [])
        self.voice.accept(dict(self.heartbeat(sequence=3), listening=False, speaking='windows_sapi'))
        self.assertEqual(self.voice.snapshot()['sources'][0]['source'], 'windows_sapi')

    def test_full_state_stop_is_immediate_and_sources_are_allowlisted_and_deduplicated(self):
        first = self.heartbeat()
        self.voice.accept(dict(first, speaking='browser_tts'))
        self.clock.advance(1)
        second = self.heartbeat(session=2)
        self.voice.accept(dict(second, speaking='windows_sapi'))
        value = self.voice.snapshot()
        self.assertEqual([row['source'] for row in value['sources']],
            ['microphone_capture', 'browser_tts', 'windows_sapi'])
        self.assertEqual(value['sources'][0]['observedAt'], second['observedAt'])
        for row in value['sources']:
            self.assertEqual(set(row), {'origin', 'source', 'phase', 'observedAt'})
            self.assertEqual(row['origin'], 'assistant_ui')
        self.assertNotIn('sessionId', json.dumps(value))
        self.voice.accept(dict(self.heartbeat(sequence=2), listening=False))
        self.voice.accept(dict(self.heartbeat(sequence=2, session=2), listening=False))
        self.assertEqual(self.voice.snapshot()['sources'], [])

    def test_capacity_retains_tombstones_for_120_seconds_and_replay_stays_stale(self):
        for session in range(64):
            self.voice.accept(self.heartbeat(session=session))
        self.clock.advance(119.999)
        with self.assertRaises(ValueError):
            self.voice.accept(self.heartbeat(session=64))
        with self.assertRaises(ValueError):
            self.voice.accept(self.heartbeat(session=0))
        self.assertEqual(len(self.voice.sessions), 64)
        self.clock.advance(.001)
        with self.assertRaises(ValueError):
            self.voice.accept(dict(self.heartbeat(session=0), observedAt=Clock().stamp()))
        self.voice.accept(self.heartbeat(session=64))
        self.assertEqual(len(self.voice.sessions), 1)

    def test_concurrent_duplicate_sequences_have_one_winner_and_bounded_capacity(self):
        def accept(body):
            try:
                self.voice.accept(body)
                self.voice.snapshot()
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(accept, [self.heartbeat()] * 40)), 1)
            self.assertEqual(sum(pool.map(accept, [self.heartbeat(session=i) for i in range(2, 100)])), 63)
        self.assertEqual(len(self.voice.sessions), 64)


class MasterPrivateActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-voice-contract-')
        self.root = Path(self.temp.name)
        self.service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
            SimpleNamespace(state=lambda: dict(busy=False)), start=False)
        self.clock = Clock()
        self.service._voice_activity = m.CoolerVoiceActivity(lambda: self.clock.wall, lambda: self.clock.mono)

    def tearDown(self):
        self.service._worker = None
        self.service.close()
        self.temp.cleanup()

    def heartbeat(self, sequence=1):
        return dict(sessionId='a'*32, sequence=sequence, observedAt=self.clock.stamp(), listening=True, speaking='none')

    def private(self, now=100):
        with patch.object(m.time, 'monotonic', return_value=now):
            self.service._send(dict(op='snapshot', snapshot=self.service.state()))
        return self.service.pending_snapshot['snapshot']['coolerDisplay']

    def action(self, connect, now=100):
        with patch.object(self.service, '_ensure_worker'), patch.object(m.time, 'monotonic', return_value=now):
            self.service.display(dict(connect=connect))

    def report(self, now, **fields):
        with patch.object(m.time, 'monotonic', return_value=now):
            self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial, **fields))

    def established(self):
        self.action(True)
        self.report(101, displayStatus='connected', connected=True)
        with patch.object(m.time, 'monotonic', return_value=102):
            self.service.accept_worker_status(dict(type='delivery', requestId=self.service._request_serial,
                attemptAcknowledgedFrames=1))

    def drop(self, now=104, cleanup=False):
        if cleanup:
            with patch.object(m.time, 'monotonic', return_value=now):
                self.service._cancel_failed_attempt('ack_timeout', 'Synthetic timeout', True)
        else:
            self.report(now, displayStatus='unavailable', connected=False, cleanupComplete=True,
                errorCode='ack_timeout', retryable=True, error='Synthetic timeout')

    def test_voice_is_only_enriched_worker_copy_and_never_public_persisted_or_lifecycle(self):
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(self.service.voice_activity(self.heartbeat()), dict(accepted=True, sequence=1))
        state = self.service.state()
        self.assertNotIn('coolerDisplay', state)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        self.assertEqual(len(self.private()['voiceActivity']['sources']), 1)
        self.assertNotIn('coolerDisplay', state, 'The caller snapshot must not be mutated')
        self.service.refresh(collect_git=False)
        export = m.read_json(self.service.feed / 'snapshot.json')
        plugin = m.bridge_snapshot(self.service.feed)
        for value in (export, plugin, self.service.state(), self.service.events.lifecycle_graph([])):
            self.assertNotIn('voiceActivity', json.dumps(value))
            self.assertNotIn('assistant_ui', json.dumps(value))
        self.clock.advance(5)
        self.assertEqual(self.private()['voiceActivity']['sources'], [])

    def test_private_connection_activity_follows_actual_recovery_actions_only(self):
        self.action(True)
        self.assertIsNone(self.private()['connectionActivity'], 'Initial connect is not recovery')
        self.report(101, displayStatus='connected', connected=True)
        self.assertIsNone(self.private(101)['connectionActivity'], 'A stream without ACK cannot arm recovery')
        with patch.object(m.time, 'monotonic', return_value=102):
            self.service.accept_worker_status(dict(type='delivery', requestId=self.service._request_serial,
                attemptAcknowledgedFrames=1))
        self.assertIsNone(self.private(102)['connectionActivity'])
        self.drop(104, cleanup=True)
        private = self.private(104)['connectionActivity']
        self.assertEqual(set(private), {'source', 'phase', 'requestId', 'observedAt'})
        self.assertEqual(private['source'], 'cooler_transport')
        self.assertEqual(private['phase'], 'reconnecting')
        self.assertEqual(private['requestId'], self.service._request_serial)
        self.assertIsNone(self.private(109)['connectionActivity'], 'Expired cleanup is not an active action')
        self.report(105, displayStatus='disconnected', connected=False, cleanupComplete=True)
        self.assertIsNotNone(self.private(105)['connectionActivity'])
        with patch.object(self.service, '_ensure_worker'):
            self.service._display_tick(107)
        self.assertIsNotNone(self.private(107)['connectionActivity'])
        self.report(108, displayStatus='connected', connected=True)
        self.assertIsNotNone(self.private(108)['connectionActivity'], 'Retry still awaits first acknowledged frame')
        with patch.object(m.time, 'monotonic', return_value=109):
            self.service.accept_worker_status(dict(type='delivery', requestId=self.service._request_serial,
                attemptAcknowledgedFrames=1))
        self.assertGreater(self.service._recovery_until, 109)
        self.assertIsNone(self.private(109)['connectionActivity'], 'A leftover window does not mean reconnecting')

    def test_connection_observation_does_not_mutate_state_and_never_leaks(self):
        self.established()
        self.drop()
        before = (self.service._retry_at, self.service._retry_index, self.service._request_serial,
            self.service._attempt_deadline, self.service._connect_intent, self.service._pending_failure)
        for _ in range(3):
            self.assertIsNotNone(self.private(105)['connectionActivity'])
        self.assertEqual(before, (self.service._retry_at, self.service._retry_index, self.service._request_serial,
            self.service._attempt_deadline, self.service._connect_intent, self.service._pending_failure))
        self.assertNotIn('connectionActivity', json.dumps(self.service.state()))
        self.assertNotIn('connectionActivity', (self.service.feed/'snapshot.json').read_text())

    def test_initial_failure_and_elapsed_overall_recovery_do_not_claim_reconnecting(self):
        self.action(True)
        self.drop()
        self.assertFalse(self.service._connect_intent)
        self.assertIsNone(self.private(104)['connectionActivity'])
        self.established()
        self.drop()
        until = self.service._recovery_until
        self.service._display_tick(until)
        self.assertFalse(self.service._connect_intent)
        self.assertIsNone(self.private(until)['connectionActivity'])

    def test_disconnect_exhaustion_configuration_and_writer_conflict_stop_connection_activity(self):
        self.established()
        self.drop()
        self.action(False, 105)
        self.assertIsNone(self.private(105)['connectionActivity'])
        self.established()
        self.service._retry_index = 3
        self.drop()
        self.assertEqual(self.service.state()['display']['errorCode'], 'recovery_exhausted')
        self.assertIsNone(self.private(105)['connectionActivity'])
        self.established()
        self.drop()
        with patch.object(m.time, 'monotonic', return_value=105):
            self.service._suspend_recovery('Synthetic configuration conflict')
        self.assertIsNone(self.private(105)['connectionActivity'])
        self.service._cleanup_deadline = 0
        self.established()
        self.report(104, displayStatus='unavailable', connected=False, cleanupComplete=True,
            errorCode='writer_conflict', retryable=False, error='Synthetic competing writer')
        self.assertIsNone(self.private(105)['connectionActivity'])


class VoiceHandlerTests(unittest.TestCase):
    def setUp(self):
        self.voice = m.CoolerVoiceActivity()
        self.server = SimpleNamespace(host='127.0.0.1:12345', origin='http://127.0.0.1:12345',
            token='fixture-token', master=SimpleNamespace(voice_activity=self.voice.accept))

    def request(self, overrides=None, path='/api/master/voice-activity', body=None, raw=None):
        headers = {'Host': self.server.host, 'Origin': self.server.origin,
            'X-INTERLINKED-Token': self.server.token, 'Content-Type': 'application/json'}
        headers.update(overrides or {})
        body = body or dict(sessionId='a'*32, sequence=1, observedAt=m.utc(), listening=True, speaking='none')
        payload = (json.dumps(body) if raw is None else raw).encode('utf-8')
        headers['Content-Length'] = str(len(payload))
        handler = g.Handler.__new__(g.Handler)
        handler.server, handler.path = self.server, path
        handler.headers = Message()
        for key, value in headers.items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        handler.request_version = 'HTTP/1.1'
        handler.command, handler.requestline = 'POST', 'POST ' + path + ' HTTP/1.1'
        handler.close_connection = False
        handler.do_POST()
        head, data = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
        lines = head.decode('ascii').split('\r\n')
        return int(lines[0].split()[1]), dict(line.split(': ', 1) for line in lines[1:]), json.loads(data)

    def test_route_uses_existing_host_token_origin_fetch_and_body_guards(self):
        for headers in ({'Host': 'foreign.invalid'}, {'X-INTERLINKED-Token': ''},
            {'Origin': 'https://foreign.invalid'}, {'Origin': ''}, {'Sec-Fetch-Site': 'cross-site'},
            {'Content-Type': 'text/plain'}, {'Transfer-Encoding': 'chunked'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers)[0], 409)
                self.assertEqual(self.voice.sessions, {})
        self.assertEqual(self.request(path='/api/master/voice-activity?token=fixture-token')[0], 409)
        self.assertEqual(self.request(body=dict(sessionId='a'*32, sequence=1, observedAt=m.utc(),
            listening=True, speaking='none', text='PRIVATE TEXT'))[0], 409)
        self.assertEqual(self.request(raw='{"sequence":1} trailing private text')[0], 409)
        self.assertEqual(self.voice.sessions, {})
        status, headers, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(value, dict(accepted=True, sequence=1))
        self.assertEqual(self.request()[0], 409)


if __name__ == '__main__':
    unittest.main()
