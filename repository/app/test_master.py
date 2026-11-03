"""Synthetic MASTER tests. No hardware writes, MSI access, or project actions."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('master_under_test', ROOT / 'interlinked_master.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
OUT = None
WORKER = None


class Controller:
    def __init__(self): self.busy = False
    def state(self):
        return dict(busy=self.busy, output=['PRIVATE PROMPT AND COMMAND'], activeSession=None)


class MasterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fixture-', dir=OUT)
        self.path = Path(self.temp.name)
        self.runtime = SimpleNamespace(state=self.path / 'state', kit=self.path / 'kit', demo=True)
        self.controller = Controller()
        self.service = m.MasterService(self.runtime, self.controller, start=False)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def event(self, event='UserPromptSubmit', **fields):
        return m.sanitize_hook(dict(hook_event_name=event, session_id='session1', turn_id='turn1',
            cwd=str(self.path / 'project'), observedAt=m.utc(), **fields))

    def register(self):
        self.settings(dict(projects=[dict(id='project', label='Fixture', root=str(self.path / 'project'))]))

    def settings(self, body):
        return self.service.settings(dict(body, expectedRevision=self.service.state()['settingsRevision']))

    def test_metadata_never_contains_prompt_or_tool_input(self):
        event = self.event(prompt='TOP SECRET', tool_input={'command': 'SECRET COMMAND'},
            tool_response='PRIVATE RESULT', transcript_path='private-transcript')
        text = json.dumps(event)
        for secret in ('TOP SECRET', 'SECRET COMMAND', 'PRIVATE RESULT', 'private-transcript', 'tool_input'):
            self.assertNotIn(secret, text)

    def test_demo_never_authorizes_automatic_startup(self):
        self.assertFalse(self.service._automatic_startup_allowed())
        self.assertEqual(self.service._auto_start_until, 0)

    def test_automatic_startup_requires_exact_opt_in_and_installed_bytes(self):
        self.service.runtime.demo = False
        self.service.runtime.kit = m.Path(r'C:\Users\Jude\Documents\MSI-Project-Library')
        self.service.lifecycle_preferences_known = True
        self.service._settings['startAtLogin'] = True
        pin = m.hashlib.sha256(b'fixture').hexdigest()
        valid = dict(format='interlinked-cooler-startup-v1', enabled=True, machine='THIS-MACHINE',
                     user='Jude', appSha256=pin, workerSha256=pin)
        original_read = m.read_json
        original_bytes = m.Path.read_bytes
        startup_path = m.Path(r'C:\Program Files\INTERLINKED Cooler Startup\startup.json')
        def startup_read(record):
            return lambda path, *args: record if path == startup_path else original_read(path, *args)
        def pinned_bytes(path):
            return b'fixture' if path in (m.Path(m.__file__), self.service.worker_path) else original_bytes(path)
        with patch.object(m.Path, 'read_bytes', pinned_bytes), patch.object(m, 'read_json', side_effect=startup_read(valid)):
            self.assertTrue(self.service._automatic_startup_allowed())
            for field, value in [('enabled', False), ('machine', 'MSI'), ('user', 'different'),
                                 ('format', 'unknown'), ('appSha256', '0'*64), ('workerSha256', '0'*64)]:
                with patch.object(m, 'read_json', side_effect=startup_read(dict(valid, **{field: value}))):
                    self.assertFalse(self.service._automatic_startup_allowed(), field)
            self.service._settings['startAtLogin'] = False
            self.assertFalse(self.service._automatic_startup_allowed())
            self.service._settings['startAtLogin'] = True
            with patch.object(self.service.configuration, 'status', return_value={'available': False}):
                self.assertFalse(self.service._automatic_startup_allowed())
            self.service.lifecycle_preferences_known = False
            self.assertFalse(self.service._automatic_startup_allowed())

    def test_automatic_startup_waits_for_sensor_initialization(self):
        self.service._auto_start_until = 100
        self.service._auto_sensor_until = 80
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure, patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            ensure.assert_not_called(); send.assert_not_called()
            self.service._automatic_startup_tick(96)
            send.assert_called_once_with(dict(op='display', connect=True))

    def test_automatic_startup_connects_once_when_sensors_ready(self):
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            self.service._automatic_startup_tick(21)
            send.assert_called_once_with(dict(op='display', connect=True))
            self.service._state['display']['connected'] = True
            self.service._automatic_startup_tick(22)
            self.assertEqual(self.service._auto_start_until, 0)

    def test_manual_disconnect_cancels_automatic_startup(self):
        self.service._auto_start_until = 100
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send'):
            self.service.display(dict(connect=False))
            self.assertEqual(self.service._auto_start_until, 0)

    def test_automatic_startup_retry_is_bounded_and_rate_limited(self):
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial,
                displayStatus='unavailable', connected=False, cleanupComplete=True, retryable=True,
                errorCode='handshake_timeout', error='Synthetic handshake timeout'))
            self.service._automatic_startup_tick(21)
            self.service._automatic_startup_tick(26)
            self.assertEqual(send.call_count, 2)
            self.service._automatic_startup_tick(101)
            self.assertEqual(self.service._auto_start_until, 0)
            self.assertEqual(send.call_count, 3)
            self.assertEqual(send.call_args.args[0], dict(op='display', connect=False))

    def test_delayed_sensors_leave_a_full_usb_retry_window(self):
        self.service._auto_start_until = 200
        self.service._auto_sensor_until = 80
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(79)
            send.assert_not_called()
            self.service._automatic_startup_tick(80)
            self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial,
                displayStatus='unavailable', connected=False, cleanupComplete=True, retryable=True,
                errorCode='handshake_timeout', error='Synthetic handshake timeout'))
            self.service._automatic_startup_tick(93)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(self.service._auto_connect_until, 140)
            self.service._automatic_startup_tick(141)
            self.assertEqual(self.service._auto_start_until, 0)

    def test_startup_opt_out_or_invalid_configuration_cancels_pending_connect(self):
        self.service._auto_start_until = 100
        with patch.object(self.service, '_automatic_startup_allowed', return_value=False), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            send.assert_not_called()
            self.assertEqual(self.service._auto_start_until, 0)

    def test_saving_startup_opt_out_immediately_cancels_pending_connect(self):
        self.service._auto_start_until = 100
        self.settings(dict(background=True, startAtLogin=False))
        self.assertEqual(self.service._auto_start_until, 0)

    def test_startup_opt_out_during_worker_start_cancels_command(self):
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', side_effect=[True, False]), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            self.assertFalse(any(call.args[0].get('connect') is True for call in send.call_args_list))
            self.assertEqual(self.service._auto_start_until, 0)

    def test_saving_opt_out_cancels_in_flight_automatic_usb_request(self):
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            self.settings(dict(background=True, startAtLogin=False))
            commands = [call.args[0] for call in send.call_args_list if call.args[0].get('op') == 'display']
            self.assertEqual(commands, [dict(op='display', connect=True), dict(op='display', connect=False)])

    def test_invalidated_configuration_cancels_in_flight_automatic_usb_request(self):
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        with patch.object(self.service, '_automatic_startup_allowed', side_effect=[True, True, False]), patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            self.service._automatic_startup_tick(21)
            self.assertEqual([call.args[0] for call in send.call_args_list], [dict(op='display', connect=True), dict(op='display', connect=False)])

    def test_saturated_queue_retains_automatic_disconnect_until_it_can_be_sent(self):
        self.service._auto_start_until = 100
        self.service._auto_connect_sent = True
        self.service.commands.clear()
        for _ in range(self.service.commands.maxlen):
            self.service.commands.append(dict(op='capture_sources'))
        before = list(self.service.commands)
        self.service._cancel_automatic_startup()
        self.assertEqual(list(self.service.commands), before)
        self.assertTrue(self.service._auto_cancel_pending)
        self.assertEqual(self.service._auto_start_until, 0)
        # The pending auto-connect may report success while its cancellation
        # waits for space; it still needs the already requested disconnect.
        self.service._state['display']['connected'] = True
        self.service.commands.clear()
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure:
            self.service._automatic_startup_tick(20)
            self.service._automatic_startup_tick(21)
            ensure.assert_not_called()
        self.assertEqual(list(self.service.commands), [dict(op='display', connect=False, requestId=self.service._request_serial)])
        self.assertFalse(self.service._auto_cancel_pending)
        self.assertFalse(self.service._auto_connect_sent)

    def test_manual_connection_supersedes_pending_automatic_cancellation(self):
        self.service._auto_start_until = 0
        self.service._auto_connect_sent = True
        self.service._auto_cancel_pending = True
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send') as send:
            self.service.display(dict(connect=True))
            self.service._automatic_startup_tick(20)
            send.assert_called_once_with(dict(op='display', connect=True))
        self.assertFalse(self.service._auto_cancel_pending)
        self.assertFalse(self.service._auto_connect_sent)

    def test_duplicate_event_does_not_change_state(self):
        event = self.event()
        self.assertTrue(self.service.events.accept(event))
        self.assertFalse(self.service.events.accept(event))

    def test_out_of_order_event_does_not_revert_terminal(self):
        start = self.event()
        terminal = dict(start, id='terminal', event='Stop')
        self.service.events.accept(terminal)
        self.assertFalse(self.service.events.accept(dict(start, id='late-start')))
        self.assertEqual(self.service.events.activity([str(self.path / 'project')])['status'], 'turn_finished')

    def test_new_turn_can_follow_terminal(self):
        event = self.event('Stop')
        self.service.events.accept(event)
        self.service.events.accept(dict(event, id='next', event='UserPromptSubmit', turnId='turn2'))
        self.assertEqual(self.service.events.activity([str(self.path / 'project')])['status'], 'working')

    def test_stale_activity_is_unknown_not_completed(self):
        event = self.event()
        self.service.events.accept(event)
        result = self.service.events.activity([str(self.path / 'project')], now=time.time()+181)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['stale'])

    def test_parallel_waiting_agent_remains_visible(self):
        self.service.events.accept(self.event())
        child = self.event('PermissionRequest', agent_id='child')
        self.service.events.accept(child)
        result = self.service.events.activity([str(self.path / 'project')])
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(len(result['agents']), 2)

    def test_event_spool_drained_without_project_actions(self):
        self.register()
        m.publish_event(self.service.feed, self.event())
        self.service.refresh(collect_git=False)
        self.assertEqual(self.service.state()['projects'][0]['activity']['status'], 'working')
        self.assertEqual(list((self.service.feed / 'events').glob('*.json')), [])

    def test_corrupt_event_is_isolated(self):
        (self.service.feed / 'events' / 'bad.json').write_text('{', encoding='utf-8')
        self.service.refresh(collect_git=False)
        self.assertEqual(self.service.event_errors, 1)
        self.assertEqual(len(self.service.state()['projects']), 3)

    def test_subagent_summary_reaches_service_and_reduced_bridge(self):
        self.register()
        m.publish_event(self.service.feed, self.event())
        m.publish_event(self.service.feed, self.event('SubagentStart', agent_id='child'))
        self.service.refresh(collect_git=False)
        state = self.service.state()
        self.assertEqual(state['subagents']['workingCount'], 1)
        self.assertEqual(state['projects'][0]['subagents']['workingCount'], 1)
        exported = m.bridge_snapshot(self.service.feed)
        self.assertEqual(exported['subagents'], state['subagents'])
        self.assertEqual(exported['projects'][0]['subagents'], state['projects'][0]['subagents'])
        reduced = json.dumps(exported['subagents'])
        for private in ('cwd', 'sessionId', 'agentId', str(self.path)):
            self.assertNotIn(private, reduced)

    def test_corrupt_feed_degrades_observed_subagent_coverage(self):
        self.register()
        self.service.events.accept(self.event('SubagentStart', agent_id='child'))
        (self.service.feed / 'events' / 'broken.json').write_text('{', encoding='utf-8')
        self.service.refresh(collect_git=False)
        self.assertEqual(self.service.state()['subagents']['workingCount'], 1)
        self.assertEqual(self.service.state()['subagents']['status'], 'partial')
        self.assertEqual(self.service.state()['projects'][0]['subagents']['status'], 'partial')

    def test_collector_restart_does_not_restore_a_fabricated_zero(self):
        self.register()
        self.service.events.accept(self.event('SubagentStart', agent_id='child'))
        self.service.refresh(collect_git=False)
        self.assertEqual(self.service.state()['subagents']['workingCount'], 1)
        self.service.close()
        self.service = m.MasterService(self.runtime, self.controller, start=False)
        self.assertIsNone(self.service.state()['subagents']['workingCount'])
        self.assertEqual(self.service.state()['subagents']['status'], 'unavailable')

    def test_controller_output_is_not_exported_to_dot(self):
        exported = m.bridge_snapshot(self.service.feed)
        self.assertNotIn('PRIVATE PROMPT', json.dumps(exported))
        self.assertNotIn('settings', exported)
        self.assertNotIn('captureSources', exported)

    def test_missing_controller_observation_is_not_reported_idle(self):
        with patch.object(self.controller,'state',side_effect=OSError('Fixture observation unavailable')):
            self.service.refresh(collect_git=False)
        row=next(row for row in self.service.state()['projects'] if row['id']=='interlinked')
        self.assertEqual(row['workspace']['status'],'unknown')
        self.assertIsNone(row['workspace']['observedAt'])

    def test_owner_mismatch_preserves_existing_content(self):
        root = self.path / 'other'; root.mkdir()
        (root / 'precious.txt').write_text('keep')
        with self.assertRaises(ValueError): m.owned_directory(root, 'application')
        self.assertEqual((root / 'precious.txt').read_text(), 'keep')

    def test_corrupt_master_configuration_does_not_block_library(self):
        m.write_json(self.service.root/'settings.json',dict(format='unsupported',settings={}))
        before=(self.service.root/'settings.json').read_bytes()
        fallback=m.safe_service(self.runtime,self.controller,start=False)
        self.assertEqual(fallback.state()['display']['status'],'configuration_error')
        self.assertFalse(fallback.state()['display']['connected'])
        self.assertEqual(before,(self.service.root/'settings.json').read_bytes())
        self.assertFalse(self.controller.state()['busy'])

    def test_private_transport_pins_are_not_http_settings(self):
        with self.assertRaises(ValueError):self.settings({'helperPins':{'USBLCDNEW.dll':'a'*64}})
        m.write_json(self.service.root/'device.json',dict(format='interlinked-master-device-v1',helperPins={'USBLCDNEW.dll':'a'*64}))
        self.assertEqual(self.service._worker_settings()['helperPins']['USBLCDNEW.dll'],'a'*64)

    def test_settings_reject_unknown_capabilities_and_network_roots(self):
        for payload in ({'executable': 'bad.exe'}, {'fps': 20}, {'mode': 'arbitrary'},
                        {'projects': [dict(id='x', label='Bad', root=r'\\MSI\ProjectLibrary')]}):
            with self.assertRaises(ValueError): self.settings(payload)

    def test_settings_add_project_roundtrip(self):
        self.register()
        result = self.service.state()
        self.assertEqual(result['settings']['projects'][0]['label'], 'Fixture')
        reopened = m.MasterService(self.runtime, self.controller, start=False)
        try: self.assertEqual(reopened.state()['settings']['projects'][0]['id'], 'project')
        finally: reopened.close()

    def test_thirty_fps_default_and_supported_rate_persistence(self):
        self.assertEqual(self.service.state()['settings']['fps'], 30)
        self.assertEqual(self.service._worker_settings()['fps'], 30)
        for fps in (5, 10, 15, 30):
            with self.subTest(fps=fps):
                self.settings(dict(fps=fps))
                reopened = m.MasterService(self.runtime, self.controller, start=False)
                try:
                    self.assertEqual(reopened.state()['settings']['fps'], fps)
                    self.assertEqual(reopened._worker_settings()['fps'], fps)
                finally:
                    reopened.close()

    def test_existing_lower_rate_preferences_are_not_upgraded_on_read(self):
        for fps in (5, 10, 15):
            with self.subTest(fps=fps):
                m.write_json(self.service.root / 'settings.json',
                             dict(format='interlinked-master-settings-v1', settings=dict(fps=fps)))
                before = (self.service.root / 'settings.json').read_bytes()
                reopened = m.MasterService(self.runtime, self.controller, start=False)
                try:
                    self.assertEqual(reopened.state()['settings']['fps'], fps)
                    self.assertEqual((self.service.root / 'settings.json').read_bytes(), before)
                finally:
                    reopened.close()

    def test_diagnostic_and_noninteger_rates_are_not_normal_preferences(self):
        for fps in (20, 45, 60, 30.0, True, '30'):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                self.settings(dict(fps=fps))

    def test_capture_cannot_claim_live_without_window_calibration(self):
        with self.assertRaises(ValueError): self.settings({'mode': 'live_mini', 'captureEnabled': True})
        self.assertFalse(self.service.state()['capture']['valid'])

    def test_capture_is_disabled_on_reopen(self):
        self.settings(dict(mode='live_mini', captureWindowId='123', captureWindowBounds=dict(x=0,y=0,width=240,height=240), captureEnabled=True))
        reopened = m.MasterService(self.runtime, self.controller, start=False)
        try: self.assertFalse(reopened.state()['settings']['captureEnabled'])
        finally: reopened.close()

    def test_settings_revision_conflict_and_noop_preserve_history(self):
        original = self.service.state()['settingsRevision']
        with self.assertRaises(ValueError) as missing:
            self.service.settings(dict(fps=5))
        self.assertEqual(missing.exception.current['settingsRevision'], original)
        self.settings(dict(fps=5))
        saved = (self.service.root / 'settings.json').read_bytes()
        revision = self.service.state()['settingsRevision']
        self.assertNotEqual(revision, original)
        with self.assertRaises(ValueError) as stale:
            self.service.settings(dict(fps=15, expectedRevision=original))
        self.assertEqual(stale.exception.current['settings']['fps'], 5)
        self.assertEqual((self.service.root / 'settings.json').read_bytes(), saved)
        with patch.object(self.service, '_send') as send:
            self.settings(dict(fps=5))
            send.assert_not_called()
        self.assertEqual(self.service.state()['settingsRevision'], revision)
        self.assertEqual(self.service.history()['items'], [])

    def test_legacy_settings_migrate_only_on_write(self):
        value = dict(format='interlinked-master-settings-v1', settings=dict(fps=5))
        m.write_json(self.service.root / 'settings.json', value)
        before = (self.service.root / 'settings.json').read_bytes()
        reopened = m.MasterService(self.runtime, self.controller, start=False)
        try:
            self.assertEqual((self.service.root / 'settings.json').read_bytes(), before)
            self.assertTrue(reopened.state()['settingsRevision'].startswith('legacy:'))
            reopened.settings(dict(fps=15, expectedRevision=reopened.state()['settingsRevision']))
            self.assertEqual(len(reopened.history()['items']), 1)
            self.assertEqual(m.read_json(self.service.root / 'settings.json')['format'], 'interlinked-configuration-v2')
        finally:
            reopened.close()

    def test_restore_retains_lifecycle_disables_capture_and_keeps_activity_root(self):
        project = dict(id='project', label='Fixture', root=str(self.path / 'project'),
                       activityRoot=str(self.path / 'activity'))
        self.settings(dict(projects=[project], fps=5, background=True, startAtLogin=False,
            captureEnabled=True, captureWindowId='123', captureWindowBounds=dict(x=0,y=0,width=240,height=240)))
        self.settings(dict(fps=15, background=False, startAtLogin=True))
        history = self.service.history()
        preview = self.service.preview_restore(dict(snapshotId=history['items'][0]['id']))
        self.assertEqual(preview['proposed']['settings']['fps'], 5)
        with patch.object(self.service, 'display') as display:
            result = self.service.restore(dict(previewId=preview['previewId']))
            display.assert_not_called()
        settings = result['state']['settings']
        self.assertEqual(settings['projects'][0]['activityRoot'], project['activityRoot'])
        self.assertFalse(settings['background'])
        self.assertTrue(settings['startAtLogin'])
        self.assertFalse(settings['captureEnabled'])
        self.assertIsNone(settings['captureWindowId'])
        self.assertIsNone(settings['captureWindowBounds'])
        self.assertFalse(self.controller.state()['busy'])

    def test_restore_cancels_pending_automatic_display_connection(self):
        self.settings(dict(fps=5))
        self.settings(dict(fps=15))
        preview = self.service.preview_restore(dict(snapshotId=self.service.history()['items'][0]['id']))
        self.service._auto_start_until = 100
        self.service._state['temperatures']['status'] = 'available'
        self.service.restore(dict(previewId=preview['previewId']))
        with patch.object(self.service, '_automatic_startup_allowed', return_value=True), patch.object(self.service, '_ensure_worker') as ensure, patch.object(self.service, '_send') as send:
            self.service._automatic_startup_tick(20)
            ensure.assert_not_called()
            send.assert_not_called()
        self.assertEqual(self.service._auto_start_until, 0)


    def test_corrupt_settings_restore_requires_restart_and_preserves_bad_bytes(self):
        self.settings(dict(fps=5))
        self.settings(dict(fps=15))
        bad = b'{corrupt settings fixture'
        (self.service.root / 'settings.json').write_bytes(bad)
        fallback = m.safe_service(self.runtime, self.controller, start=False)
        state = fallback.state()
        self.assertFalse(state['settingsValid'])
        self.assertNotIn('startAtLogin', state['settings'])
        self.assertNotIn('background', state['settings'])
        snapshot = fallback.history()['items'][0]['id']
        preview = fallback.preview_restore(dict(snapshotId=snapshot))
        self.assertFalse(preview['proposed']['lifecyclePreferencesKnown'])
        result = fallback.restore(dict(previewId=preview['previewId']))
        self.assertTrue(result['restartRequired'])
        self.assertFalse(result['state']['settingsValid'])
        preserved = list(fallback.configuration.history_root.glob('corrupt-*.bin'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), bad)
        reopened = m.MasterService(self.runtime, self.controller, start=False)
        try:
            self.assertTrue(reopened.state()['settingsValid'])
            self.assertFalse(reopened.state()['lifecyclePreferencesKnown'])
            self.assertFalse(reopened.state()['settings']['captureEnabled'])
        finally:
            reopened.close()

    def test_restore_preview_refuses_intervening_save_and_history_is_bounded(self):
        for fps in (5, 15, 10, 5, 15):
            self.settings(dict(fps=fps))
        self.assertEqual(len(self.service.history()['items']), 3)
        preview = self.service.preview_restore(dict(snapshotId=self.service.history()['items'][0]['id']))
        self.settings(dict(fps=10))
        before = (self.service.root / 'settings.json').read_bytes()
        with self.assertRaises(ValueError):
            self.service.restore(dict(previewId=preview['previewId']))
        self.assertEqual((self.service.root / 'settings.json').read_bytes(), before)

    def test_settings_merge_uses_current_disk_revision_not_stale_service_values(self):
        self.settings(dict(fps=5))
        external = self.service.configuration.status()
        external['value']['settings']['projects'] = [dict(id='external', label='External fixture',
            root=str(self.path / 'external'), activityRoot=str(self.path / 'other-activity'))]
        saved = self.service.configuration.save(external['value'], expected_revision=external['revision'])
        observed = self.service.state()
        self.assertEqual(observed['settingsRevision'], saved['revision'])
        self.assertEqual(observed['settings']['projects'][0]['id'], 'external')
        self.assertFalse(observed['lifecyclePreferencesKnown'])
        self.assertTrue(observed['restartRequired'])
        self.service.settings(dict(fps=15, expectedRevision=saved['revision']))
        settings = self.service.state()['settings']
        self.assertEqual(settings['fps'], 15)
        self.assertEqual(settings['projects'][0]['id'], 'external')
        self.assertIn('activityRoot', settings['projects'][0])

    def test_midrun_corruption_is_not_an_actionable_settings_snapshot(self):
        self.settings(dict(fps=5, startAtLogin=True, background=True))
        (self.service.root / 'settings.json').write_bytes(b'{midrun corruption')
        with patch.object(self.service, '_send') as send:
            state = self.service.state()
            send.assert_not_called()
        self.assertFalse(state['settingsValid'])
        self.assertFalse(state['lifecyclePreferencesKnown'])
        self.assertIsNone(state['settingsRevision'])
        self.assertNotIn('startAtLogin', state['settings'])
        self.assertNotIn('background', state['settings'])
        self.assertFalse(state['settings']['captureEnabled'])

    def test_wrong_owner_never_acquires_configuration_recovery(self):
        self.settings(dict(fps=5))
        self.settings(dict(fps=15))
        marker = self.service.root / 'OWNER.json'
        m.write_json(marker, dict(format='foreign-fixture'))
        before = marker.read_bytes()
        fallback = m.safe_service(self.runtime, self.controller, start=False)
        self.assertFalse(fallback.state()['settingsValid'])
        with self.assertRaises(ValueError):
            fallback.history()
        self.assertEqual(marker.read_bytes(), before)

    def test_build_receipt_requires_matching_clean_revision(self):
        head = 'a'*40
        receipt = dict(format='interlinked-master-result-v1', kind='test', result='passed', observedAt=m.utc(), baseline=dict(head=head, clean=True))
        for git in (dict(status='available',head='b'*40,dirty=False), dict(status='available',head=head,dirty=True)):
            self.assertEqual(m.receipt_health(receipt, git)['status'], 'unknown')
        self.assertEqual(m.receipt_health(receipt, dict(status='available',head=head,dirty=False,observedAt=m.utc()))['status'], 'passed')

    def test_turn_finished_is_not_build_success(self):
        self.register();m.publish_event(self.service.feed, self.event('Stop'));self.service.refresh(collect_git=False)
        row = self.service.state()['projects'][0]
        self.assertEqual(row['activity']['status'], 'turn_finished')
        self.assertEqual(row['health']['status'], 'unknown')

    def test_violet_real_report_is_labelled_and_expires(self):
        self.assertFalse(self.service.state()['violet']['connected'])
        m.publish_violet(self.service.feed, dict(status='working', reporter='dot', message='Reviewing project status'))
        self.service.refresh(collect_git=False)
        self.assertTrue(self.service.state()['violet']['connected'])
        self.assertEqual(self.service.state()['violet']['source'], 'Dot-reported')
        record = m.read_json(self.service.feed/'violet.json')
        record['observedAt']=(datetime.now(timezone.utc)-timedelta(seconds=121)).isoformat()
        m.write_json(self.service.feed/'violet.json',record)
        self.service.refresh(collect_git=False)
        self.assertFalse(self.service.state()['violet']['connected'])
        self.assertTrue(self.service.state()['violet']['stale'])

    def test_violet_cannot_request_project_operations(self):
        with self.assertRaises(ValueError): m.publish_violet(self.service.feed,dict(status='working', command='release'))

    def test_display_failure_replaces_pending_message_with_worker_error(self):
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send'):
            self.service.display(dict(connect=True))
        self.assertEqual(self.service.state()['display']['message'], 'Waiting for display worker')
        self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial, connected=False,
            displayStatus='unavailable', error='Vendor LCD helper exited.'))
        display = self.service.state()['display']
        self.assertEqual(display['status'], 'unavailable')
        self.assertFalse(display['connected'])
        self.assertEqual(display['message'], 'Vendor LCD helper exited.')
        self.assertEqual(display.get('message') or display.get('error'), 'Vendor LCD helper exited.')

    def test_connected_display_clears_pending_message(self):
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send'):
            self.service.display(dict(connect=True))
        self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial, connected=True,
            displayStatus='connected', error=None))
        display = self.service.state()['display']
        self.assertEqual(display['status'], 'connected')
        self.assertTrue(display['connected'])
        self.assertIsNone(display['message'])
        self.assertIsNone(display['error'])

    def test_display_status_preserves_explicit_worker_message(self):
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send'):
            self.service.display(dict(connect=True))
        self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial, connected=False,
            displayStatus='unavailable', message='Close the other LCD writer.', error='Writer conflict'))
        self.assertEqual(self.service.state()['display']['message'], 'Close the other LCD writer.')

    def test_display_fast_worker_response_is_not_overwritten_by_pending_state(self):
        def reply(_command):
            self.service.accept_worker_status(dict(type='status', requestId=self.service._request_serial, connected=False,
                displayStatus='unavailable', error='Vendor LCD helper exited.'))
        with patch.object(self.service, '_ensure_worker'), patch.object(self.service, '_send', side_effect=reply):
            display = self.service.display(dict(connect=True))['display']
        self.assertEqual(display['status'], 'unavailable')
        self.assertEqual(display['message'], 'Vendor LCD helper exited.')
        self.assertEqual(display['error'], 'Vendor LCD helper exited.')

    def test_sensor_validation_requires_fresh_verified_value(self):
        self.service.accept_worker_status(dict(type='sensors',temperatures=dict(cpu=dict(value=55,status='available',observedAt=m.utc()),gpu=dict(value=900,status='available',observedAt=m.utc()))))
        self.assertEqual(self.service.state()['temperatures']['cpu']['value'],55)
        self.assertIsNone(self.service.state()['temperatures']['gpu']['value'])

    def test_sensor_payload_timestamp_is_retained_per_reading(self):
        observed=m.utc()
        self.service.accept_worker_status(dict(type='sensors',temperatures=dict(observedAt=observed,
            cpu=dict(value=55,status='live'),gpu=dict(value=42,status='live'),reason='Fixture publisher')))
        value=self.service.state()['temperatures']
        self.assertEqual(value['cpu']['observedAt'],observed)
        self.assertEqual(value['gpu']['value'],42)
        self.assertEqual(value['reason'],'Fixture publisher')
        self.service.accept_worker_status(dict(type='sensors',temperatures=dict(observedAt=m.utc(),
            cpu=dict(value=55,status='live'))))
        self.assertNotIn('reason',self.service.state()['temperatures'])

    def test_sensor_permission_status_is_separate_from_readings(self):
        self.service.accept_worker_status(dict(type='sensor_permission',status='canceled',message='Setup canceled'))
        self.assertEqual(self.service.state()['sensorPermission']['status'],'canceled')
        self.assertIsNone(self.service.state()['temperatures']['cpu']['value'])

    def test_sensor_setup_requires_production_and_connected_lcd(self):
        with patch.object(self.service,'_ensure_worker') as ensure, patch.object(self.service,'_send') as send:
            with self.assertRaises(ValueError):self.service.display({'action':'enable_sensors'})
            self.runtime.demo=False
            try:
                with self.assertRaises(ValueError):self.service.display({'action':'enable_sensors'})
                ensure.assert_not_called();send.assert_not_called()
                self.service._state['display']['connected']=True
                self.service._worker=SimpleNamespace(poll=lambda:None)
                self.service.display({'action':'enable_sensors'})
                ensure.assert_called_once_with(expected_request_id=self.service._request_serial,report_failure=False)
                send.assert_called_once_with({'op':'sensors','enable':True})
            finally:self.runtime.demo=True;self.service._worker=None

    def test_sensor_staleness_clears_value(self):
        self.service.accept_worker_status(dict(type='sensors',verified=True,cpu=dict(value=55,observedAt=m.utc())))
        self.service._state['temperatures']['observedAt']=(datetime.now(timezone.utc)-timedelta(seconds=6)).isoformat()
        self.service.refresh(collect_git=False)
        self.assertIsNone(self.service.state()['temperatures']['cpu']['value'])

    def test_frame_parser_rejects_truncated_input(self):
        with self.assertRaises(EOFError): m.MasterService._read_exact(io.BytesIO(b'12'),4)

    def test_isolated_hook_invocation_discards_sensitive_fields(self):
        self.register()
        payload=dict(hook_event_name='UserPromptSubmit',session_id='hook-session',turn_id='turn',cwd=str(self.path/'project'),prompt='SECRET PROMPT')
        process=subprocess.run([sys.executable,'-I','-B',str(ROOT/'master_hook.py'),'--state-root',str(self.service.feed)],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(process.returncode,0);self.assertEqual(process.stdout,b'')
        files=list((self.service.feed/'events').glob('*.json'))
        self.assertEqual(len(files),1);self.assertNotIn('SECRET PROMPT',files[0].read_text())

    def test_global_hook_drops_unregistered_chat(self):
        self.register()
        payload=dict(hook_event_name='UserPromptSubmit',session_id='unrelated',turn_id='turn',cwd=str(self.path/'unrelated'),prompt='PRIVATE')
        process=subprocess.run([sys.executable,'-I','-B',str(ROOT/'master_hook.py'),'--state-root',str(self.service.feed)],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(process.returncode,0)
        self.assertEqual(list((self.service.feed/'events').glob('*.json')),[])

    def test_hook_merge_preserves_unrelated_definitions_and_is_idempotent(self):
        hook_spec=importlib.util.spec_from_file_location('hook_under_test',ROOT/'master_hook.py')
        hook=importlib.util.module_from_spec(hook_spec);hook_spec.loader.exec_module(hook)
        original=dict(description='Keep this description',hooks=dict(Stop=[dict(hooks=[dict(type='command',command='existing-policy')])]))
        merged=hook.merged_config(original)
        self.assertEqual(merged['hooks']['Stop'][0],original['hooks']['Stop'][0])
        self.assertEqual(merged['description'],original['description'])
        self.assertEqual(hook.merged_config(merged),merged)

    def test_passive_buddy_status_is_fresh_and_does_not_change_agent_health(self):
        m.write_json(self.service.feed/'ed-it-buddy.json',dict(format='interlinked-buddy-status-v1',host='MSI',online=True,observedAt=m.utc(),current_job=dict(status='running',current_step=2)))
        self.service.refresh(collect_git=False)
        project=next(p for p in self.service.state()['projects'] if p['id']=='ed-it-buddy')
        self.assertEqual(project['workspace']['status'],'running')
        self.assertEqual(project['activity']['status'],'unknown')
        self.assertEqual(project['health']['status'],'unknown')

    def test_mcp_initialize_list_and_real_fixture_tools(self):
        requests=[dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2025-03-26'}),
            dict(jsonrpc='2.0',id=2,method='tools/list'),
            dict(jsonrpc='2.0',id=3,method='tools/call',params={'name':'get_master_status','arguments':{}}),
            dict(jsonrpc='2.0',id=4,method='tools/call',params={'name':'publish_violet_status','arguments':{'status':'waiting','reporter':'demo','message':'Fixture only'}})]
        process=subprocess.run([sys.executable,'-I','-B',str(ROOT/'master_bridge.py'),'--state-root',str(self.service.feed)],
            input=('\n'.join(json.dumps(r) for r in requests)+'\n').encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(process.returncode,0)
        responses=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r['id'] for r in responses],[1,2,3,4])
        self.assertEqual([tool['name'] for tool in responses[1]['result']['tools']],['get_master_status','publish_violet_status','get_master_attention','acknowledge_master_attention'])
        self.assertFalse(responses[2]['result']['isError']);self.assertFalse(responses[3]['result']['isError'])
        self.service.refresh(collect_git=False);self.assertEqual(self.service.state()['violet']['status'],'waiting')

    def test_missing_worker_is_honest_and_cannot_run_project(self):
        with self.assertRaises(ValueError): self.service.display({'connect':True})
        self.assertEqual(self.service.state()['display']['status'],'unavailable')
        self.assertFalse(self.service.state()['display']['connected'])

    def test_replaced_worker_cannot_take_new_worker_commands(self):
        old = SimpleNamespace(poll=lambda:None, stdin=io.BytesIO())
        self.service._worker = object()
        self.service.commands.append(dict(op='display', connect=False))
        self.service._write_worker(old)
        self.assertEqual(list(self.service.commands), [dict(op='display', connect=False)])
        self.assertEqual(old.stdin.getvalue(), b'')
        self.service._worker = None

    def test_reporter_defaults_legacy_and_demo_never_claim_dot_connection(self):
        for reporter in (None, 'demo', 'local_task', 'dot'):
            body = dict(status='working')
            if reporter: body['reporter'] = reporter
            m.publish_violet(self.service.feed, body)
            self.service.refresh(collect_git=False)
            report = self.service.state()['violet']
            self.assertEqual(report['reporter'], reporter or 'unverified')
            self.assertEqual(report['connected'], reporter in ('dot', 'local_task'))
        m.write_json(self.service.feed/'violet.json', dict(format='interlinked-violet-status-v1', status='working', observedAt=m.utc()))
        self.service.refresh(collect_git=False)
        self.assertEqual(self.service.state()['violet']['source'], 'Unverified report')
        self.assertFalse(self.service.state()['violet']['connected'])
        for reporter in ('invalid', [], {}):
            with self.assertRaises(ValueError): m.publish_violet(self.service.feed, dict(status='working', reporter=reporter))

    def attention_project(self, observed=None, status='waiting'):
        return dict(id='project', activity=dict(status=status, observedAt=observed or m.utc(), source='Codex hook'),
                    health=dict(status='unknown', observedAt=None))

    def test_attention_dedup_acknowledgment_and_restart(self):
        projects = [self.attention_project()]
        first = m.collect_attention(self.service.feed, projects)
        self.assertEqual(first['pendingCount'], 1)
        self.assertEqual(m.collect_attention(self.service.feed, projects)['pendingCount'], 1)
        identifier = first['items'][0]['id']
        result = m.acknowledge_attention(self.service.feed, dict(ids=[identifier]))
        self.assertFalse(result['resolvesProjectFailure'])
        self.assertEqual(m.collect_attention(self.service.feed, projects)['pendingCount'], 0)
        self.assertEqual(m.master_attention(self.service.feed)['pendingCount'], 0)
        self.assertEqual(projects[0]['activity']['status'], 'waiting')
        with self.assertRaises(ValueError): m.acknowledge_attention(self.service.feed, dict(ids=['unknown']))
        with self.assertRaises(ValueError): m.acknowledge_attention(self.service.feed, dict(ids=['../escape']))
        with self.assertRaises(ValueError): m.acknowledge_attention(self.service.feed, dict(ids=[], command='release'))

    def test_attention_ack_is_idempotent_and_bad_batch_writes_nothing(self):
        first = m.collect_attention(self.service.feed, [self.attention_project()])
        identifier = first['items'][0]['id']
        with self.assertRaises(ValueError):
            m.acknowledge_attention(self.service.feed, dict(ids=[identifier, 'unknown']))
        self.assertFalse(m.master_attention(self.service.feed)['items'][0]['acknowledged'])
        m.acknowledge_attention(self.service.feed, dict(ids=[identifier]))
        path = self.service.feed / 'attention-acks' / (identifier + '.json')
        before = path.read_bytes()
        m.acknowledge_attention(self.service.feed, dict(ids=[identifier, identifier]))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(m.master_attention(self.service.feed)['items'][0]['acknowledgedAt'], json.loads(before)['acknowledgedAt'])

    def test_corrupt_ack_is_preserved_and_reports_unavailable(self):
        item = m.collect_attention(self.service.feed, [self.attention_project()])['items'][0]
        directory = self.service.feed / 'attention-acks'
        directory.mkdir(exist_ok=True)
        path = directory / (item['id'] + '.json')
        path.write_bytes(b'{corrupt acknowledgment')
        with self.assertRaises(ValueError):
            m.acknowledge_attention(self.service.feed, dict(ids=[item['id']]))
        self.assertFalse(self.service.attention()['available'])
        self.assertEqual(path.read_bytes(), b'{corrupt acknowledgment')

    def test_missing_queue_or_orphan_corruption_never_prunes_acknowledgments(self):
        item = m.collect_attention(self.service.feed, [self.attention_project()])['items'][0]
        m.acknowledge_attention(self.service.feed, dict(ids=[item['id']]))
        queue_path = self.service.feed / 'attention.json'
        queue_bytes = queue_path.read_bytes()
        ack = self.service.feed / 'attention-acks' / (item['id'] + '.json')
        ack_bytes = ack.read_bytes()
        queue_path.unlink()
        with self.assertRaises(ValueError):
            m.collect_attention(self.service.feed, [])
        self.assertEqual(ack.read_bytes(), ack_bytes)
        queue_path.write_bytes(queue_bytes)
        orphan = ack.with_name('orphan.json')
        orphan.write_bytes(b'{orphan corrupt fixture')
        with self.assertRaises(ValueError):
            m.collect_attention(self.service.feed, [])
        self.assertEqual(orphan.read_bytes(), b'{orphan corrupt fixture')
        self.assertEqual(queue_path.read_bytes(), queue_bytes)

    def test_duplicate_attention_queue_identity_is_corruption(self):
        item = m.collect_attention(self.service.feed, [self.attention_project()])['items'][0]
        raw = {k: v for k, v in item.items() if k not in {'acknowledged', 'acknowledgedAt'}}
        m.write_json(self.service.feed / 'attention.json', dict(format='interlinked-master-attention-v1', items=[raw, raw]))
        self.assertFalse(self.service.attention()['available'])

    def test_attention_lock_is_bounded_and_never_touches_project_locks(self):
        before = set(self.path.rglob('*'))
        with m.attention_guard(self.service.feed):
            started = time.monotonic()
            with self.assertRaises(OSError):
                m.master_attention(self.service.feed)
            self.assertLess(time.monotonic() - started, 2)
        created = set(self.path.rglob('*')) - before
        self.assertTrue((self.service.feed / '.attention.lock').is_file())
        self.assertTrue({p.name for p in created} <= {'.attention.lock'})

    def test_concurrent_process_attention_collection_and_review(self):
        initial = m.collect_attention(self.service.feed, [self.attention_project()])['items'][0]
        wrapper = """import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('queue_fixture',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=Path(sys.argv[2]);action=sys.argv[3]
import time
for i in range(8):
 for attempt in range(10):
  try:
   if action=='ack':m.acknowledge_attention(root,dict(ids=[sys.argv[4]]))
   else:m.collect_attention(root,[dict(id='process-'+action,health=dict(status='failed',observedAt='2026-01-01T00:00:%02d+00:00'%i,source='Fixture'),activity=dict(status='unknown'))])
   break
  except OSError:
   if attempt==9:raise
   time.sleep(.02)
print('ok')
"""
        processes = [subprocess.Popen([sys.executable, '-I', '-B', '-c', wrapper,
            str(ROOT / 'interlinked_master.py'), str(self.service.feed), action, initial['id']],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE) for action in ('one', 'two', 'ack', 'ack')]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stderr.decode('utf-8', errors='replace'))
                self.assertEqual(stdout.strip(), b'ok')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=5)
        queue = m.master_attention(self.service.feed)
        self.assertEqual(len(queue['items']), 17)
        self.assertTrue(next(row for row in queue['items'] if row['id'] == initial['id'])['acknowledged'])

    def test_attention_capacity_and_ack_retention(self):
        for i in range(105):
            observed = (datetime.now(timezone.utc) - timedelta(seconds=105-i)).isoformat()
            queue = m.collect_attention(self.service.feed, [self.attention_project(observed=observed)])
            m.acknowledge_attention(self.service.feed, dict(ids=[queue['items'][-1]['id']]))
        self.assertEqual(len(m.master_attention(self.service.feed)['items']), 100)
        self.assertEqual(len(list((self.service.feed/'attention-acks').glob('*.json'))), 100)

    def test_attention_only_evidenced_failures_and_input(self):
        project = self.attention_project(status='unknown')
        self.assertEqual(m.collect_attention(self.service.feed, [project])['pendingCount'], 0)
        project['health'] = dict(status='failed', observedAt=m.utc(), source='Test receipt')
        first = m.collect_attention(self.service.feed, [project])
        self.assertEqual(first['items'][0]['kind'], 'failure')
        m.acknowledge_attention(self.service.feed, dict(ids=[first['items'][0]['id']]))
        self.assertEqual(project['health']['status'], 'failed')
        project['health']['status'] = 'unknown'
        self.assertEqual(m.collect_attention(self.service.feed, [project])['pendingCount'], 0)

    def test_corrupt_queue_does_not_interrupt_project_collection(self):
        m.write_json(self.service.feed/'attention.json', dict(format='wrong', items=[]))
        self.service.refresh(collect_git=False)
        self.assertFalse(self.service.state()['attention']['available'])
        self.assertTrue(self.service.state()['projects'])

    def test_attention_tools_roundtrip_on_private_fixture(self):
        spec = importlib.util.spec_from_file_location('bridge_fixture', ROOT/'master_bridge.py')
        bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
        queued = m.collect_attention(self.service.feed, [self.attention_project()])
        def call(name, arguments):
            return bridge.handle(dict(jsonrpc='2.0', id=1, method='tools/call', params=dict(name=name, arguments=arguments)), m, self.service.feed)
        read = call('get_master_attention', {})
        self.assertEqual(json.loads(read['content'][0]['text'])['pendingCount'], 1)
        self.assertFalse(call('acknowledge_master_attention', dict(ids=[queued['items'][0]['id']]))['isError'])
        self.assertTrue(call('get_master_attention', dict(command='release'))['isError'])
        self.assertEqual(m.master_attention(self.service.feed)['pendingCount'], 0)

    def test_mcp_metadata_uses_utf8_even_with_ascii_console(self):
        snapshot = m.bridge_snapshot(self.service.feed)
        snapshot['projects'][0]['label'] = 'Violet — 日本語 💜'
        m.write_json(self.service.feed/'snapshot.json', snapshot)
        request = dict(jsonrpc='2.0', id=1, method='tools/call', params=dict(name='get_master_status', arguments={}))
        wrapper = "import sys,runpy;sys.stdout.reconfigure(encoding='ascii');sys.argv=" + repr([str(ROOT/'master_bridge.py'), '--state-root', str(self.service.feed)]) + ";runpy.run_path(sys.argv[0],run_name='__main__')"
        process = subprocess.run([sys.executable, '-I', '-B', '-c', wrapper],
            input=(json.dumps(request)+'\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(process.stderr)
        reply = json.loads(process.stdout.decode('utf-8'))
        result = json.loads(reply['result']['content'][0]['text'])
        self.assertEqual(result['projects'][0]['label'], snapshot['projects'][0]['label'])

    def test_real_native_worker_preview_only(self):
        if not WORKER:
            self.skipTest('Pass --worker for the native preview integration check')
        service=m.MasterService(self.runtime,self.controller,worker_path=WORKER,start=False)
        try:
            barrier=threading.Barrier(8)
            def start():
                barrier.wait()
                service._start_worker()
            with patch.object(m.subprocess,'Popen',wraps=subprocess.Popen) as launch:
                starts=[threading.Thread(target=start) for _ in range(8)]
                for thread in starts:thread.start()
                for thread in starts:thread.join(timeout=5)
                self.assertTrue(all(not thread.is_alive() for thread in starts))
                self.assertEqual(launch.call_count,1,'Concurrent HTTP restart requests must create only one owned renderer')
            deadline=time.time()+8
            while time.time()<deadline and not service.frame():time.sleep(.05)
            self.assertTrue(service.frame().startswith(b'\xff\xd8'))
            self.assertFalse(service.state()['display']['connected'])
        finally:service.close()


def main():
    global OUT, WORKER
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--worker',type=Path)
    args=parser.parse_args();OUT=args.output.absolute();WORKER=args.worker
    if OUT.exists() and any(OUT.iterdir()):raise SystemExit('Use a new empty private fixture directory.')
    OUT.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(MasterTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (OUT/'master-tests.json').write_text(json.dumps(dict(format='interlinked-master-tests-v1',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),ok=result.wasSuccessful()),indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
