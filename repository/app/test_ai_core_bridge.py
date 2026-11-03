"""Synthetic tests for frozen jobs, calibration cancellation and load ownership."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_ai as ai
import interlinked_ai_node as node
from test_ai import FakeNode


class VersionedNode(FakeNode):
    def __init__(self, root):
        super().__init__(root)
        self.version = 'fixture-1'
        self.on_chat = None
        self.options = []

    def inventory(self):
        return dict(super().inventory(), runtime=dict(version=self.version))

    def chat(self, model, messages, tools=None, **kwargs):
        self.options.append(kwargs)
        result = super().chat(model, messages, tools=tools, **kwargs)
        if self.on_chat:
            self.on_chat()
        return result


class CoreBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ai.AIService(SimpleNamespace(state=root, kit=root, demo=True), node_factory=VersionedNode)
        self.service.refresh()
        self.node = self.service.nodes['local']

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def change_settings(self):
        current = self.service.settings_value()
        value = copy.deepcopy(current['value'])
        value['responseTokens'] += 128
        self.service.configuration.save(value, current['revision'])

    def calibrate(self):
        return self.service.calibrate(dict(host='local', model='fixture', capability='text'))

    def test_calibration_cancel_does_not_publish(self):
        cancel = threading.Event()
        self.node.on_chat = cancel.set
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.service.calibrate(dict(host='local', model='fixture', capability='text'), cancel_event=cancel)
        self.assertEqual(len(self.node.calls), 1)
        self.assertEqual(self.service.calibration, {})

    def test_cancelled_recalibration_preserves_previous_evidence(self):
        self.calibrate()
        previous = copy.deepcopy(self.service.calibration)
        cancel = threading.Event()
        self.node.on_chat = cancel.set
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.service.calibrate(dict(host='local', model='fixture', capability='text'), cancel_event=cancel)
        self.assertEqual(self.service.calibration, previous)

    def test_calibration_configuration_change_cannot_publish(self):
        self.node.on_chat = self.change_settings
        with self.assertRaisesRegex(ValueError, 'configuration_changed'):
            self.calibrate()
        self.assertEqual(len(self.node.calls), 1)
        self.assertEqual(self.service.calibration, {})

    def test_calibration_runtime_change_cannot_publish(self):
        self.node.on_chat = lambda: setattr(self.node, 'version', 'fixture-2')
        with self.assertRaisesRegex(ValueError, 'Runtime changed'):
            self.calibrate()
        self.assertEqual(self.service.calibration, {})

    def test_final_trial_digest_change_cannot_publish(self):
        self.node.on_chat = lambda: setattr(self.node, 'digest', 'b' * 64) if len(self.node.calls) == 3 else None
        with self.assertRaisesRegex(ValueError, 'changed during calibration'):
            self.calibrate()
        self.assertEqual(self.service.calibration, {})

    def test_final_trial_policy_change_cannot_publish(self):
        original = self.node.inventory
        policy = {'value':None}
        self.node.inventory = lambda:dict(original(),inferencePolicySha256=policy['value'])
        self.node.on_chat = lambda:policy.update(value='f'*64) if len(self.node.calls)==3 else None
        with self.assertRaisesRegex(ValueError,'changed during calibration'):
            self.calibrate()
        self.assertEqual(self.service.calibration,{})

    def test_expired_deadline_never_calls_model(self):
        with self.assertRaisesRegex(ValueError, 'deadline expired'):
            self.service.calibrate(dict(host='local', model='fixture', capability='text'), deadline=time.monotonic() - 1)
        self.assertEqual(self.node.calls, [])

    def test_running_inference_retains_captured_settings(self):
        self.calibrate()
        model = self.service.route({'text'})
        snapshot = self.service.settings_value()
        self.node.on_chat = self.change_settings
        result = self.service.infer(model, [dict(role='user', content='Hello')], settings_snapshot=snapshot)
        self.assertEqual(result['content'], 'Answer from fixture.')
        self.assertEqual(self.node.options[-1]['options']['num_predict'], snapshot['value']['responseTokens'])
        with self.assertRaisesRegex(ValueError, 'configuration_changed'):
            self.service.infer(model, [dict(role='user', content='Next')], settings_snapshot=snapshot)

    def test_queued_inference_refuses_changed_revision(self):
        self.calibrate()
        model = self.service.route({'text'})
        snapshot = self.service.settings_value()
        before = len(self.node.calls)
        slot = self.service.slots['local']
        slot.acquire()
        failures = []
        def run():
            try:
                self.service.infer(model, [dict(role='user', content='Hello')], settings_snapshot=snapshot)
            except ValueError as exc:
                failures.append(str(exc))
        thread = threading.Thread(target=run)
        thread.start()
        self.change_settings()
        slot.release()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, ['configuration_changed'])
        self.assertEqual(len(self.node.calls), before)


class OwnedNode(node.LocalNode):
    def __init__(self, root):
        super().__init__(root, identity={'computer': 'fixture', 'sid': 'fixture'})
        self.digest = 'a' * 64
        self.version = 'fixture-1'
        self.process_session = '100:1000'
        self.loaded = False
        self.generations = []

    def _runtime_session(self):
        return self.process_session

    def _request(self, method, path, body=None, cancel=None, timeout=15):
        if path == '/api/tags':
            return dict(models=[dict(name='fixture', digest=self.digest)])
        if path == '/api/ps':
            return dict(models=[dict(name='fixture', digest=self.digest)] if self.loaded else [])
        if path == '/api/version':
            return dict(version=self.version)
        if path == '/api/generate':
            self.generations.append(body)
            self.loaded = body['keep_alive'] != 0
            return dict(done=True)
        raise AssertionError(path)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.node = OwnedNode(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_unload_requires_current_exact_owned_identity(self):
        result = self.node.load('fixture')
        identity = result['loadIdentity']
        self.assertEqual(identity, self.node.load_identity('fixture'))
        with self.assertRaisesRegex(ValueError, 'explicit confirmation'):
            self.node.unload('fixture', expected_identity=identity)
        bad = dict(identity, loadSession='b' * 32)
        with self.assertRaisesRegex(ValueError, 'confirmation identity changed'):
            self.node.unload('fixture', True, bad)
        self.assertEqual(len(self.node.generations), 1)
        self.node.unload('fixture', True, identity)
        self.assertFalse(self.node.loaded)
        with self.assertRaisesRegex(ValueError, 'load ownership'):
            self.node.load_identity('fixture')

    def test_runtime_restart_invalidates_ownership_even_same_model_version(self):
        self.node.load('fixture')
        self.node.process_session = '100:2000'
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.node.load_identity('fixture')
        self.assertNotIn('fixture', self.node.owned_models)
        self.assertEqual(len(self.node.generations), 1)

    def test_digest_change_invalidates_ownership(self):
        identity = self.node.load('fixture')['loadIdentity']
        self.node.digest = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.node.unload('fixture', True, identity)
        self.assertEqual(len(self.node.generations), 1)

    def test_external_load_does_not_acquire_ownership(self):
        self.node.loaded = True
        self.assertEqual(self.node.load('fixture'), dict(status='already_loaded', owned=False))
        with self.assertRaisesRegex(ValueError, 'load ownership'):
            self.node.load_identity('fixture')

    def test_changed_tag_refuses_load_before_side_effect(self):
        with self.assertRaisesRegex(ValueError, 'changed before load'):
            self.node.load('fixture', expected_digest='b' * 64)
        self.assertEqual(self.node.generations, [])


if __name__ == '__main__':
    unittest.main()
