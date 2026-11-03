"""Isolated loopback gateway acceptance; no real models or personal archive."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

import interlinked_history_gateway as gateway_module


class Controller:
    def __init__(self):
        self.calls = []
        self.models_calls = 0
        self.hold = False
        self.started, self.release, self.cancelled = threading.Event(), threading.Event(), threading.Event()
        self.result = {'content': 'Local fixture answer', 'sources': [{'id': 'a' * 64, 'kind': 'conversation',
            'title': 'Fixture source', 'timestamp': '2026-11-02T12:00:00Z', 'text': 'PRIVATE SOURCE BODY'}]}

    def gateway_models(self):
        self.models_calls += 1
        return [{'id': 'local/fixture'}, {'id': 'fake:cloud'}, {'id': 'remote', 'remote_host': 'cloud.invalid'}]

    def gateway_answer(self, messages, model, profile, cancel):
        self.calls.append({'messages': messages, 'model': model, 'profile': profile})
        self.started.set()
        if self.hold:
            while not self.release.wait(0.02):
                if cancel.is_set():
                    self.cancelled.set()
                    return {'content': 'Cancelled'}
        if self.result is None:
            raise RuntimeError('PRIVATE INTERNAL ERROR')
        return self.result


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'gateway'
        self.controller = Controller()
        self.gateway = gateway_module.HistoryGateway(self.controller, self.root, port=0)
        self.client = self.gateway.create('Fixture client', historyEnabled=True, project='Fixture')

    def tearDown(self):
        self.controller.release.set()
        self.gateway.close()
        self.temp.cleanup()

    def request(self, method='GET', route='/v1/models', body=None, token=True, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.port, timeout=4)
        request_headers = {'Authorization': 'Bearer ' + self.client['key']} if token else {}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        connection.request(method, route, body=raw if raw is not None else json.dumps(body) if body is not None else None, headers=request_headers)
        response = connection.getresponse()
        value = response.read()
        status, content_type = response.status, response.getheader('Content-Type')
        connection.close()
        return status, json.loads(value) if content_type and content_type.startswith('application/json') else value.decode()

    def chat(self, **extra):
        return self.request('POST', '/v1/chat/completions', {'model': 'local/fixture',
            'messages': [{'role': 'user', 'content': 'Fixture question'}], **extra})

    def test_lifecycle_does_not_infer_or_observe_models_at_startup(self):
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.controller.models_calls, 0)
        self.assertTrue(self.gateway.status()['running'])
        self.assertEqual(self.gateway.server.server_address[0], '127.0.0.1')

    def test_no_client_means_no_listener(self):
        other = gateway_module.HistoryGateway(self.controller, Path(self.temp.name) / 'empty', port=0)
        try:
            self.assertFalse(other.status()['running'])
            self.assertTrue(other.status()['available'])
        finally:
            other.close()

    def test_authorization_and_host_origin_gates(self):
        self.assertEqual(self.request(token=False)[0], 401)
        self.assertEqual(self.request(headers={'Authorization': 'Bearer wrong-key'})[0], 401)
        self.assertEqual(self.request(headers={'Origin': 'http://127.0.0.1'})[0], 403)
        self.assertEqual(self.request(headers={'Host': 'outside.invalid'})[0], 403)
        self.assertEqual(self.request(headers={'Transfer-Encoding': 'chunked'})[0], 403)
        self.assertEqual(self.controller.models_calls, 0)

    def test_models_filter_cloud_and_remote_records(self):
        code, value = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(value['data'], [{'id': 'local/fixture', 'object': 'model', 'owned_by': 'interlinked-local'}])
        self.assertEqual(self.controller.calls, [])

    def test_profile_is_server_owned_and_sources_are_minimized(self):
        code, value = self.chat(max_tokens=4096)
        self.assertEqual(code, 200)
        self.assertEqual(self.controller.calls[-1]['profile'], {'historyEnabled': True, 'project': 'Fixture'})
        self.assertIn('Fixture source', value['choices'][0]['message']['content'])
        self.assertNotIn('PRIVATE SOURCE BODY', json.dumps(value))
        self.assertNotIn('text', value['sources'][0])
        self.assertEqual(self.chat(historyEnabled=False)[0], 400)
        self.assertEqual(len(self.controller.calls), 1)

    def test_history_off_client_profile_is_retained(self):
        self.client = self.gateway.create('History off', historyEnabled=False, project='')
        self.assertEqual(self.chat()[0], 200)
        self.assertFalse(self.controller.calls[-1]['profile']['historyEnabled'])

    def test_unsupported_requests_do_not_dispatch(self):
        for body in [{'tools': []}, {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'fixture'}]}]},
                     {'messages': [{'role': 'tool', 'content': 'fixture'}]}, {'messages': [{'role': [], 'content': 'fixture'}]}, {'messages': []}, {'messages': [{'role': 'user', 'content': 'x', 'extra': 1}]},
                     {'model': 'thing:cloud'}, {'model': 'thing-cloud'}, {'model': 'https://upstream.invalid'},
                     {'model': 'missing'}, {'max_tokens': 128}, {'max_tokens': 4097}, {'max_tokens': True}, {'stream': 'yes'}]:
            status, _ = self.chat(**body)
            self.assertIn(status, {400, 404}, body)
        self.assertEqual(self.controller.calls, [])

    def test_bounded_json_and_no_history_admin_routes(self):
        self.assertEqual(self.request('POST', '/v1/chat/completions', raw='[bad', headers={'Content-Type': 'application/json'})[0], 400)
        # Reject from the declared size before reading a body. Sending the full
        # oversized payload races the server's intentional close on Windows and
        # can reset TCP before the client reads the otherwise valid 413 reply.
        self.assertEqual(self.request('POST', '/v1/chat/completions', raw='', headers={
            'Content-Type': 'application/json', 'Content-Length': str(gateway_module.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.chat(messages=[{'role': 'user', 'content': '\ud800'}])[0], 400)
        for route in ['/v1/responses', '/api/ai/history/source', '/api/ai/history/handoff', '/api/chat']:
            self.assertEqual(self.request('POST', route, {})[0], 404)
        self.assertEqual(self.request('OPTIONS', '/v1/chat/completions')[0], 405)
        self.assertEqual(self.controller.calls, [])

    def test_sse_is_explicitly_buffered_and_finishes(self):
        self.controller.hold = True
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.port, timeout=4)
        connection.request('POST', '/v1/chat/completions', json.dumps({'model': 'local/fixture', 'messages': [{'role': 'user', 'content': 'fixture'}], 'stream': True}),
                           {'Authorization': 'Bearer ' + self.client['key'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader('Content-Type').startswith('text/event-stream'))
        self.assertIn(b'waiting for local completion', response.readline())
        self.assertTrue(self.controller.started.wait(2))
        self.controller.release.set()
        rest = response.read().decode()
        connection.close()
        self.assertIn('chat.completion.chunk', rest)
        self.assertIn('Local fixture answer', rest)
        self.assertTrue(rest.endswith('data: [DONE]\n\n'))

    def test_disconnect_cancels_nonstream_inference(self):
        self.controller.hold = True
        conn = socket.create_connection(('127.0.0.1', self.gateway.port), timeout=4)
        body = json.dumps({'model': 'local/fixture', 'messages': [{'role': 'user', 'content': 'fixture'}]}).encode()
        request = ('POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.gateway.port) + '\r\nAuthorization: Bearer ' + self.client['key'] +
                   '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)) + '\r\n\r\n').encode() + body
        conn.sendall(request)
        self.assertTrue(self.controller.started.wait(2))
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()
        self.assertTrue(self.controller.cancelled.wait(2))

    def test_revoke_blocks_key_and_cancels_inflight(self):
        self.controller.hold = True
        finished = threading.Event()
        def call():
            try:
                self.chat()
            except (http.client.RemoteDisconnected, ConnectionError):
                pass
            finally:
                finished.set()
        thread = threading.Thread(target=call)
        thread.start()
        self.assertTrue(self.controller.started.wait(2))
        self.gateway.revoke(self.client['id'])
        self.assertTrue(self.controller.cancelled.wait(2))
        self.assertTrue(finished.wait(2))
        thread.join()
        self.assertEqual(self.request()[0], 401)

    def test_persistence_contains_hashes_only_and_retains_profiles(self):
        raw = (self.root / 'clients.json').read_text()
        self.assertNotIn(self.client['key'], raw)
        key_hash = hashlib.sha256(self.client['key'].encode()).hexdigest()
        self.assertIn(key_hash, raw)
        status = json.dumps(self.gateway.status())
        self.assertNotIn(self.client['key'], status)
        self.assertNotIn(key_hash, status)
        self.gateway.close()
        self.gateway = gateway_module.HistoryGateway(self.controller, self.root, port=0)
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.controller.calls, [])

    def test_port_conflict_is_reported_without_takeover(self):
        other = gateway_module.HistoryGateway(self.controller, Path(self.temp.name) / 'conflict', port=self.gateway.port)
        try:
            with self.assertRaises(gateway_module.GatewayError):
                other.create('Cannot bind')
            self.assertFalse(other.status()['available'])
            self.assertIn('unavailable', other.status()['error'])
            self.assertEqual(self.request()[0], 200)
        finally:
            other.close()

    def test_internal_failures_never_return_private_exception_text(self):
        self.controller.result = None
        status, value = self.chat()
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE INTERNAL ERROR', json.dumps(value))
        status, value = self.chat(stream=True)
        self.assertEqual(status, 200)
        self.assertIn('local_inference_failed', value)
        self.assertNotIn('PRIVATE INTERNAL ERROR', value)


if __name__ == '__main__':
    unittest.main()
