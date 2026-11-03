"""AI HTTP gate and request validation against an isolated demo service."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_gui as gui


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = gui.Controller(gui.demo_runtime(Path(self.temp.name) / 'state'))
        self.server = gui.Server(self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.controller.close()
        self.temp.cleanup()
    def request(self, path, body=None, token=True, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Origin': origin or self.server.origin}
        if token: headers['X-INTERLINKED-Token'] = self.server.token
        if body is not None: headers['Content-Type'] = 'application/json'
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse(); data = response.read(); status = response.status; conn.close()
        return status, json.loads(data)
    def test_state_requires_token_and_origin(self):
        self.assertEqual(self.request('/api/ai/state', token=False)[0], 403)
        self.assertEqual(self.request('/api/ai/state', origin='http://elsewhere.invalid')[0], 403)
        code, state = self.request('/api/ai/state')
        self.assertEqual(code, 200); self.assertTrue(state['demo']); self.assertEqual(state['models'], [])
    def test_malformed_actions_return_json(self):
        for route, body in [('chat',{}),('model',{}),('tasks/start',{}),('attachments',{}),('calibrate',{}),('pair',{})]:
            code, value = self.request('/api/ai/' + route, body)
            self.assertEqual(code, 409, (route, value)); self.assertIn('error', value)

    def test_task_pages_and_reconcile_retain_http_authority(self):
        engine = self.server.ai.workspaces
        for i in range(3):
            ident = f'{i:032x}'
            engine.state['tasks'][ident] = dict(id=ident,requestId=ident,projectId='fixture',status='RECOVERY_REQUIRED',
                createdAt=str(i),result=dict(private='retained detail'),prompt='fixture')
        self.assertEqual(self.request('/api/ai/tasks?limit=1',token=False)[0],403)
        code,page = self.request('/api/ai/tasks?limit=1')
        self.assertEqual(code,200); self.assertEqual(len(page['tasks']),1)
        self.assertNotIn('result',page['tasks'][0]); self.assertEqual(page['total'],3)
        code,second = self.request('/api/ai/tasks?limit=1&after='+page['nextCursor'])
        self.assertEqual(code,200); self.assertNotEqual(second['tasks'][0]['id'],page['tasks'][0]['id'])
        self.assertEqual(self.request('/api/ai/tasks?limit=0')[0],403)
        ident=page['tasks'][0]['id']
        self.assertIn('result',self.request('/api/ai/task?id='+ident)[1])
        with mock.patch.object(self.server.ai,'reconcile_task',return_value=dict(id=ident,status='RECOVERY_REQUIRED')) as reconcile:
            route='/api/ai/tasks/reconcile'; body=dict(id=ident)
            self.assertEqual(self.request(route,body,token=False)[0],409)
            self.assertEqual(self.request(route,body,origin='http://elsewhere.invalid')[0],409)
            reconcile.assert_not_called()
            self.assertEqual(self.request(route,body)[0],200)
            reconcile.assert_called_once_with(ident)
    def test_ai_settings_history_revision(self):
        _, state = self.request('/api/ai/state')
        state['settings']['speakReplies'] = True
        body = dict(settingsRevision=state['settingsRevision'], value=state['settings'])
        self.assertEqual(self.request('/api/ai/settings', body)[0], 200)
        self.assertEqual(self.request('/api/ai/settings', body)[0], 409)
        self.assertEqual(self.request('/api/configuration/history?scope=ai')[0], 200)

    def test_bridge_confirmation_requires_authenticated_ui(self):
        original = self.server.ai_bridge
        calls = []
        class Confirmation:
            def confirm_unload(self, identifier):
                calls.append(identifier)
                return dict(id=identifier, status='queued')
        self.server.ai_bridge = Confirmation()
        try:
            route = '/api/ai/bridge/confirm-unload'
            body = dict(id='a' * 32)
            self.assertEqual(self.request(route, body, token=False)[0], 409)
            self.assertEqual(self.request(route, body, origin='http://elsewhere.invalid')[0], 409)
            self.assertEqual(self.request(route, dict(body, confirmedExternalIdle=True))[0], 409)
            self.assertEqual(calls, [])
            self.assertEqual(self.request(route, body)[0], 200)
            self.assertEqual(calls, ['a' * 32])
        finally:
            self.server.ai_bridge = original

    def test_bridge_status_uses_same_read_gate(self):
        self.assertEqual(self.request('/api/ai/bridge/status', token=False)[0], 403)
        code, state = self.request('/api/ai/bridge/status')
        self.assertEqual(code, 200)
        self.assertIn('connectionAvailable', state)
        self.assertNotIn(self.server.token, json.dumps(state))

    def test_fixture_approval_is_local_authenticated_operation(self):
        original = self.server.ai_bridge
        calls = []
        class Fixtures:
            def register_fixture(self, *args): calls.append(args)
        self.server.ai_bridge = Fixtures()
        try:
            route = '/api/ai/bridge/register-fixture'
            body = dict(id='b' * 32, attachmentId='c' * 32, capability='vision', expected='red')
            self.assertEqual(self.request(route, body, token=False)[0], 409)
            self.assertEqual(self.request(route, dict(body, path='C:/secret'))[0], 409)
            self.assertEqual(calls, [])
            self.assertEqual(self.request(route, body)[0], 200)
            self.assertEqual(calls, [('b' * 32, 'c' * 32, 'vision', 'red')])
        finally:
            self.server.ai_bridge = original


if __name__ == '__main__': unittest.main()
