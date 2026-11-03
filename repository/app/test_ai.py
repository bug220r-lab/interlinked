"""Isolated coordinator tests; no installed models, projects or network calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_ai as ai


class FakeNode:
    def __init__(self, root):
        self.digest = 'a' * 64
        self.calls = []
    def inventory(self):
        return dict(runtime={'version':'fixture-1'}, inferencePolicySha256=None, models=[dict(name='fixture', digest=self.digest, size=1, capabilities=['completion', 'tools'])], loaded=[], resources={})
    def chat(self, model, messages, tools=None, **kwargs):
        self.calls.append(messages)
        prompt = messages[-1]['content']
        if tools:
            return dict(message=dict(content='', tool_calls=[dict(function=dict(name='lookup_project', arguments=dict(project_id=prompt.split('set to ')[1].split('.')[0])))]))
        for marker, answer in [('latest_failed','{"latest_failed":2}'), ('owner is unknown','{"owner":null}'), ('def f(xs)', '[4,16]'), ('IndexError','{"fixed":"range(len(xs))"}')]:
            if marker in prompt: return dict(message=dict(content=answer))
        return dict(message=dict(content='[1,3,5]' if 'range(' in prompt else '{"project":"Lantern","pending":7}' if 'exactly 7' in prompt else 'Answer from fixture.'))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = SimpleNamespace(state=self.root, kit=self.root, demo=True)
        self.service = ai.AIService(self.runtime, node_factory=FakeNode)
    def tearDown(self):
        self.service.close()
        self.temp.cleanup()
    def calibrate(self):
        self.service.refresh()
        for cap in ('text', 'coding', 'tools'):
            self.assertTrue(self.service.calibrate(dict(host='local', model='fixture', capability=cap))['passed'])
    def test_unvalidated_model_cannot_route(self):
        self.service.refresh()
        with self.assertRaisesRegex(ValueError, 'capability tests'):
            self.service.route({'text'})
    def test_calibration_routes_and_changed_digest_revokes(self):
        self.calibrate()
        model = self.service.route({'text', 'coding', 'tools'})
        self.service.nodes['local'].digest = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.infer(model, [dict(role='user', content='Hello')])
        self.service.refresh()
        with self.assertRaises(ValueError): self.service.route({'text'})
    def test_chat_persisted_and_no_unrequested_tools(self):
        self.calibrate()
        row = self.service.chat(dict(text='Hello'))
        deadline = time.monotonic() + 5
        while self.service.conversations[row['id']]['status'] in ('running','queued') and time.monotonic() < deadline:
            time.sleep(.01)
        row = self.service.conversations[row['id']]
        self.assertEqual(row['status'], 'completed', row)
        self.assertEqual(row['messages'][-1]['role'], 'assistant')
        self.assertTrue((self.root / 'ai/conversations' / (row['id'] + '.json')).exists())
    def test_settings_revision_and_disabled_model(self):
        self.calibrate()
        current = self.service.settings_value()
        value = copy.deepcopy(current['value']); value['disabledModels'] = ['local:fixture']
        self.service.post('settings', dict(settingsRevision=current['revision'],value=value))
        with self.assertRaises(ValueError): self.service.route({'text'})
        with self.assertRaises(ValueError): self.service.post('settings', dict(settingsRevision=current['revision'], value=value))
    def test_bad_attachment_and_path_identity_rejected(self):
        with self.assertRaises(ValueError): self.service.attach(dict(name='file.wav',mime='audio/wav',data='YWJj'))
        with self.assertRaises(ValueError): self.service.with_attachments(dict(role='user',content='x'), ['../owner'])
    def test_cancelled_inference_and_stale_inventory(self):
        self.calibrate()
        model = self.service.route({'text'})
        event = threading.Event(); event.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'): self.service.infer(model, [], cancel_event=event)
        self.service.hosts['local']['refreshed'] = 0
        with self.assertRaises(ValueError): self.service.route({'text'})
    def test_demo_has_no_model_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ai.AIService(SimpleNamespace(state=Path(tmp),kit=Path(tmp),demo=True))
            self.assertFalse(service.nodes)
            service.close()


if __name__ == '__main__': unittest.main()
