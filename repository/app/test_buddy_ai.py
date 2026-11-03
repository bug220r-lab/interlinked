"""Portable merged Buddy AI regression checks.

Exercises repository/app/interlinked_buddy.py directly with synthetic model
responses, temporary state, and loopback HTTP. No overlays, other workspaces,
installed models, production transcripts, or project payloads are accessed.
"""
import copy
from contextlib import contextmanager
import http.client
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import secrets
import sys
import uuid
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_buddy as buddy
from interlinked_buddy import BuddyAI, AI_FORMAT, AI_SYSTEM, AI_TOOL, AI_MAX_RECEIPT, ai_messages, install_ai

SOURCE = Path(__file__).resolve().parents[2]
PROJECT_ID = 'buddy-ai-acceptance-fixture'


@contextmanager
def fixture_urlopen(request, timeout=3):
    """Native HTTP fixtures do not use proxies or construct unrelated TLS contexts."""
    target = urlsplit(request.full_url)
    if target.scheme != 'http' or target.hostname != '127.0.0.1' or target.username or target.password:
        raise ValueError('Only the isolated loopback HTTP fixture is permitted.')
    connection = http.client.HTTPConnection('127.0.0.1', target.port, timeout=timeout)
    response = None
    try:
        connection.request(request.get_method(), target.path + ('?' + target.query if target.query else ''),
                           body=request.data, headers=dict(request.header_items()))
        response = connection.getresponse()
        if response.status >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status, response.reason,
                                         response.headers, io.BytesIO(response.read()))
        yield response
    finally:
        if response is not None:
            response.close()
        connection.close()


def load_service(source, state, node_factory=None):
    """Construct the real coordinator against new synthetic state only."""
    path = Path(source) / 'repository/app/interlinked_ai.py'
    spec = importlib.util.spec_from_file_location('_buddy_test_coordinator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = types.SimpleNamespace(state=state, kit=state, demo=True)
    return module.AIService(runtime, node_factory=node_factory)


def native_server(ai):
    """Small native HTTP harness using the merged module's actual route installer."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, code, value):
            data = json.dumps(value).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            self.respond(404, dict(error='Unknown fixture route.'))
        def do_POST(self):
            self.respond(404, dict(error='Unknown fixture route.'))
    def project(project_id):
        if project_id != PROJECT_ID:
            raise ValueError('Unknown fixture project.')
        return dict(id=PROJECT_ID)
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def __init__(self):
            super().__init__(('127.0.0.1', 0), Handler)
            self.host = '127.0.0.1:' + str(self.server_port)
            self.ai = ai
            self.controller = types.SimpleNamespace(buddy=types.SimpleNamespace(
                token=secrets.token_urlsafe(32), project=project))
    install_ai(types.SimpleNamespace(Server=Server, Handler=Handler))
    return Server()


class FakeAI:
    def __init__(self, root):
        self.root = root
        self.configmod = types.SimpleNamespace(ordinary=lambda path, **kw: path)
        self.hosts = dict(local=dict(id='local', label='THIS-MACHINE', available=True, error=None))
        self.calls, self.started, self.release = [], threading.Event(), threading.Event()
        self.block = False
        self.settings = dict(revision='settings-one', value=dict(contextTokens=8192, responseTokens=2048, requestSeconds=180, queueSeconds=600))
        self.answer = dict(role='assistant', content='The supplied checkpoint reports two pending checks.')
        self.model = dict(host='local', id='fixture:small', digest='a' * 64, eligible=True, stale=False,
                          verified=dict(text=True, tools=True), capabilities=['completion', 'tools'], loaded=False)

    def write(self, path, row):
        path.write_text(json.dumps(row), 'utf-8')

    def read(self, path):
        return json.loads(path.read_text('utf-8'))

    def refresh(self):
        self.calls.append(('refresh',))

    def models(self):
        return [self.model]

    def route(self, capabilities, override):
        self.calls.append(('route', capabilities, override))
        return self.model

    def settings_value(self):
        return copy.deepcopy(self.settings)

    def select_for_request(self, capabilities, body, prompt, **kwargs):
        self.calls.append(('select', capabilities, prompt))
        return self.model, dict(version=1, mode=body.get('routingMode', 'balanced'), reason='Fixture Auto selection', model=self.model)

    def infer(self, model, messages, tools, cancel_event, settings_snapshot=None, **kwargs):
        self.calls.append(('infer', copy.deepcopy(messages), tools, copy.deepcopy(settings_snapshot)))
        if not settings_snapshot or settings_snapshot['revision'] != self.settings['revision']:
            raise ValueError('configuration_changed')
        self.started.set()
        if self.block:
            while not self.release.wait(.01):
                if cancel_event.is_set():
                    raise ValueError('Cancelled by fixture')
        return self.answer

    def chat(self, body):
        raise AssertionError('Broad AI chat must never be called')


class BuddyAITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='buddy-ai-')
        self.ai = FakeAI(Path(self.temp.name))
        def project(value):
            if value != 'project-one':
                raise ValueError('Unknown project')
            return dict(id=value)
        self.projects = project
        self.service = BuddyAI(self.ai, project)
        self.body = dict(request_id='b' * 32, project_id='project-one', host='local', model='fixture:small',
                         digest='a' * 64, messages=[dict(role='user', content='Summarize this selected checkpoint.')])

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def done(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = self.service.job(self.body['request_id'])
            if row['status'] not in ('queued', 'running'):
                return row
            time.sleep(.01)
        self.fail('Fixture did not finish')

    def test_explicit_context_only_and_exact_provenance(self):
        self.service.submit(self.body)
        row = self.done()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['model'], dict(host='local', id='fixture:small', digest='a' * 64))
        self.assertNotIn('request_hash', row)
        call = next(c for c in self.ai.calls if c[0] == 'infer')
        self.assertEqual(call[1], [dict(role='system', content=AI_SYSTEM)] + self.body['messages'])
        self.assertEqual(call[2], [AI_TOOL])
        self.assertEqual(call[3], self.ai.settings)
        self.assertEqual(row['settings_revision'], self.ai.settings['revision'])
        self.assertEqual(row['effective_settings'], self.ai.settings['value'])
        self.assertEqual(next(c for c in self.ai.calls if c[0] == 'route')[1], {'text', 'tools'})

    def test_duplicate_submission_is_idempotent_and_changed_arguments_refused(self):
        self.service.submit(self.body)
        self.done()
        self.service.submit(self.body)
        self.assertEqual(sum(c[0] == 'infer' for c in self.ai.calls), 1)
        with self.assertRaisesRegex(ValueError, 'different arguments'):
            self.service.submit(dict(self.body, messages=[dict(role='user', content='Changed')]))

    def test_auto_is_durable_before_classification_and_idempotent(self):
        self.body = {k: v for k, v in self.body.items() if k not in ('host', 'model', 'digest')}
        self.body['routingMode'] = 'fast'
        original = self.ai.select_for_request
        def select(*args, **kwargs):
            saved = self.ai.read(self.service.root / (self.body['request_id'] + '.json'))
            self.assertEqual(saved['format'], buddy.AI_AUTO_FORMAT)
            self.assertIsNone(saved['model'])
            self.assertEqual(saved['routingPhase'], 'selecting')
            return original(*args, **kwargs)
        self.ai.select_for_request = select
        self.service.submit(self.body)
        row = self.done()
        self.assertEqual(row['status'], 'completed', row)
        self.assertEqual(row['routing']['mode'], 'fast')
        self.service.submit(self.body)
        self.assertEqual(sum(c[0] == 'select' for c in self.ai.calls), 1)

    def test_auto_continuation_reuses_exact_model_and_rejects_changed_exchange(self):
        self.body = {k: v for k, v in self.body.items() if k not in ('host', 'model', 'digest')}
        self.ai.answer = dict(content='', tool_calls=[dict(function=dict(name='buddy_read', arguments=dict(operation='tasks', parameters={})))])
        self.service.submit(self.body)
        first = self.done()
        self.assertEqual(first['status'], 'completed', first)
        messages = self.body['messages'] + [first['message'], dict(role='tool', tool_name='buddy_read', content='{"tasks":[]}')]
        continuation = dict(self.body, request_id='c' * 32, continuationOf=self.body['request_id'], messages=messages)
        with self.assertRaisesRegex(ValueError, 'recorded tool exchange'):
            self.service.submit(dict(continuation, messages=[dict(role='user', content='Different request')] + messages[1:]))
        self.ai.answer = dict(content='No cached tasks.')
        self.body = continuation
        self.service.submit(self.body)
        second = self.done()
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(first['model'], second['model'])
        self.assertEqual(sum(c[0] == 'select' for c in self.ai.calls), 1)
        self.assertNotIn('continuation_prefix', second)

    def test_restart_recovers_pending_auto_without_classification(self):
        row = dict(format=buddy.AI_AUTO_FORMAT, request_id='d' * 32, request_hash='0' * 64, status='running',
                   project_id='project-one', model=None, routingPhase='selecting')
        self.ai.write(self.service.root / ('d' * 32 + '.json'), row)
        reopened = BuddyAI(self.ai, self.projects)
        try:
            self.assertEqual(reopened.job('d' * 32)['status'], 'interrupted')
            self.assertFalse(self.ai.calls)
        finally: reopened.close()

    def test_wrong_project_model_digest_and_unknown_fields_fail_before_inference(self):
        for change in (dict(project_id='other'), dict(digest='c' * 64), dict(command='whoami'), dict(tools=[]), dict(host='cloud')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.service.submit(dict(self.body, **change))
        self.assertFalse(any(c[0] == 'infer' for c in self.ai.calls))

    def test_readonly_tool_request_returned_but_never_executed(self):
        self.ai.answer = dict(content='', tool_calls=[dict(function=dict(name='buddy_read', arguments=dict(operation='tasks', parameters={})) )])
        self.service.submit(self.body)
        row = self.done()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['message']['tool_calls'][0]['function']['name'], 'buddy_read')

    def test_mutating_or_arbitrary_model_tools_refused(self):
        self.ai.answer = dict(content='', tool_calls=[dict(function=dict(name='shell', arguments=dict(command='whoami')))])
        self.service.submit(self.body)
        row = self.done()
        self.assertEqual(row['status'], 'failed')
        self.assertNotIn('message', row)

    def test_cancel_retains_receipt_without_replay(self):
        self.ai.block = True
        self.service.submit(self.body)
        self.assertTrue(self.ai.started.wait(1))
        self.service.cancel(dict(request_id=self.body['request_id']))
        self.assertEqual(self.done()['status'], 'cancelled')
        self.service.submit(self.body)
        self.assertEqual(sum(c[0] == 'infer' for c in self.ai.calls), 1)

    def test_restart_marks_incomplete_receipt_interrupted(self):
        row = dict(format=AI_FORMAT, request_id='d' * 32, request_hash='0' * 64, status='running',
                   project_id='project-one', model=dict(host='local', id='fixture:small', digest='a' * 64))
        self.ai.write(self.service.root / ('d' * 32 + '.json'), row)
        reopened = BuddyAI(self.ai, self.projects)
        try:
            self.assertEqual(reopened.job('d' * 32)['status'], 'interrupted')
            self.assertFalse(any(c[0] == 'infer' for c in self.ai.calls))
        finally:
            reopened.close()

    def test_settings_are_captured_before_dispatch_and_changed_configuration_fails(self):
        expected = copy.deepcopy(self.ai.settings)
        route = self.ai.route
        def changing_route(*args):
            # Simulate a settings change after acceptance but before execution.
            result = route(*args)
            self.ai.settings = dict(revision='settings-two', value=dict(expected['value'], responseTokens=1024))
            return result
        self.ai.route = changing_route
        self.service.submit(self.body)
        row = self.done()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error'], 'configuration_changed')
        self.assertEqual(row['settings_revision'], expected['revision'])
        self.assertEqual(row['effective_settings'], expected['value'])
        self.assertEqual(next(c for c in self.ai.calls if c[0] == 'infer')[3], expected)
        # Settings are execution evidence, not part of client-request identity.
        self.service.submit(self.body)
        self.assertEqual(sum(c[0] == 'infer' for c in self.ai.calls), 1)

    def test_model_discovery_is_separate_from_explicit_refresh(self):
        state = self.service.models()
        self.assertTrue(state['models'][0]['available'])
        self.assertFalse(self.ai.calls)
        self.service.models(refresh=True)
        self.assertEqual(self.ai.calls, [('refresh',)])

    def test_discovery_context_metadata_matches_coordinator_utf8_admission(self):
        state = self.service.models()
        settings = self.ai.settings_value()['value']
        self.assertEqual(state['input_budget_bytes'], max(512, settings['contextTokens'] - settings['responseTokens'] - 512))
        explicit = [dict(role='user', content='Describe café 🎬.'),
                    dict(role='tool', tool_name='buddy_read', content='{"status":"queued"}')]
        actual_infer_bytes = len(('\n'.join([AI_SYSTEM] + [row['content'] for row in explicit]) +
                                 json.dumps([AI_TOOL], ensure_ascii=False)).encode('utf-8'))
        caller_bytes = len('\n'.join(row['content'] for row in explicit).encode('utf-8'))
        self.assertEqual(state['request_overhead_bytes'] + caller_bytes, actual_infer_bytes)
        self.assertGreater(caller_bytes, len('\n'.join(row['content'] for row in explicit)))
        self.assertFalse(self.ai.calls)

    def test_history_rejects_images_unknown_tools_and_oversized_context(self):
        for messages in ([dict(role='user', content='x', images=['data'])],
                         [dict(role='tool', content='x', tool_name='shell')],
                         [dict(role='assistant', tool_calls=[dict(function=dict(name='buddy_read', arguments=dict(operation='download', parameters={})))])],
                         [dict(role='user', content='x' * 24000)] * 3):
            with self.subTest(messages=str(messages)[:90]), self.assertRaises(ValueError):
                ai_messages(messages)


class NativeGateTest(unittest.TestCase):
    def test_corrupt_oversized_and_unreadable_retained_receipts_disable_only_optional_facade(self):
        for failure in ('malformed', 'oversized', 'unreadable', 'wrong-shape'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(prefix='buddy-ai-bad-receipt-') as temp:
                ai = FakeAI(Path(temp))
                records = Path(temp) / 'buddy-requests'
                records.mkdir()
                valid = dict(format=AI_FORMAT, request_id='a' * 32, request_hash='0' * 64, status='running',
                             project_id='project-one', model=dict(host='local', id='fixture', digest='a' * 64))
                good_path, bad_path = records / ('a' * 32 + '.json'), records / ('z' * 32 + '.json')
                good_bytes = json.dumps(valid).encode()
                good_path.write_bytes(good_bytes)
                bad_bytes = b'{malformed' if failure == 'malformed' else b'x' * (AI_MAX_RECEIPT + 1) if failure == 'oversized' else b'[]' if failure == 'wrong-shape' else b'{}'
                bad_path.write_bytes(bad_bytes)
                if failure == 'unreadable':
                    original_read = ai.read
                    def unreadable(path):
                        if path == bad_path:
                            raise PermissionError('Retained receipt is unreadable.')
                        return original_read(path)
                    ai.read = unreadable
                server = native_server(ai)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                def request(route, payload=None):
                    req = urllib.request.Request('http://' + server.host + route,
                        headers={'X-INTERLINKED-Buddy-Token': server.controller.buddy.token, 'Content-Type': 'application/json'},
                        data=None if payload is None else json.dumps(payload).encode())
                    with fixture_urlopen(req, timeout=3) as response:
                        return json.load(response)
                try:
                    self.assertIs(server.ai, ai)
                    status = request('/api/buddy/ai/models')
                    self.assertFalse(status['available'])
                    self.assertEqual(status['diagnostic']['code'], 'buddy_ai_unavailable')
                    self.assertTrue(status['error'])
                    diagnostic = json.loads((Path(temp) / 'buddy-facade-diagnostic.json').read_text('utf-8'))
                    self.assertEqual(diagnostic, status['diagnostic'])
                    self.assertEqual(bad_path.read_bytes(), bad_bytes)
                    self.assertEqual(good_path.read_bytes(), good_bytes)
                    self.assertFalse(request('/api/buddy/ai/refresh', {})['available'])
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request('/api/buddy/ai/submit', {})
                    self.assertEqual(error.exception.code, 409)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request('/existing-core-route')
                    self.assertEqual(error.exception.code, 404)
                    self.assertEqual(sum(c[0] == 'infer' for c in ai.calls), 0)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_authentication_route_boundaries_and_existing_routes(self):
        temp = tempfile.TemporaryDirectory(prefix='buddy-ai-http-')
        ai = FakeAI(Path(temp.name))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, code, value):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                self.respond(200, dict(existing=True))
            def do_POST(self):
                self.respond(200, dict(existing=True))
        class Server(ThreadingHTTPServer):
            def __init__(self):
                super().__init__(('127.0.0.1', 0), Handler)
                self.host = '127.0.0.1:' + str(self.server_port)
                self.ai = ai
                self.controller = types.SimpleNamespace(buddy=types.SimpleNamespace(token='fixture-token', project=lambda p: {}))
        g = types.SimpleNamespace(Server=Server, Handler=Handler)
        install_ai(g)
        server = Server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(route, headers=None, data=None):
            req = urllib.request.Request('http://' + server.host + route, headers=headers or {},
                data=None if data is None else json.dumps(data).encode())
            with fixture_urlopen(req, timeout=3) as reply:
                return json.load(reply)
        try:
            self.assertEqual(request('/existing'), dict(existing=True))
            for headers in ({}, {'X-INTERLINKED-Buddy-Token': 'gui-token'},
                            {'X-INTERLINKED-Buddy-Token': 'fixture-token', 'Origin': 'http://' + server.host},
                            {'X-INTERLINKED-Buddy-Token': 'fixture-token', 'Sec-Fetch-Site': 'same-origin'}):
                with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError):
                    request('/api/buddy/ai/models', headers)
            native = {'X-INTERLINKED-Buddy-Token': 'fixture-token', 'Content-Type': 'application/json'}
            self.assertEqual(request('/api/buddy/ai/models', native)['format'], AI_FORMAT)
            self.assertTrue(request('/api/buddy/ai/refresh', native, {})['available'])
            for route in ('/api/buddy/ai/tasks/start', '/api/buddy/ai/models/unload', '/api/buddy/ai/submit?extra=1'):
                with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError):
                    request(route, native, {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            temp.cleanup()


class RealCoordinatorSeamTest(unittest.TestCase):
    def test_real_service_calibration_native_routes_and_read_tool_roundtrip(self):
        source = SOURCE
        class Node:
            def __init__(self, root):
                self.messages = []
            def inventory(self):
                return dict(inferencePolicySha256=None, runtime=dict(version='fixture-v1'), models=[dict(name='fixture', digest='e' * 64,
                    size=1, capabilities=['completion', 'tools'])], loaded=[], resources={})
            def chat(self, model, messages, tools=None, **kwargs):
                self.messages.append(copy.deepcopy(messages))
                text = messages[-1]['content']
                if tools and tools[0]['function']['name'] == 'lookup_project':
                    return dict(message=dict(content='', tool_calls=[dict(function=dict(name='lookup_project',
                        arguments=dict(project_id=text.split('set to ')[1].split('.')[0])))]))
                if tools:
                    if messages[-1]['role'] == 'tool':
                        return dict(message=dict(content='The fixture task is queued; it has not completed.'))
                    return dict(message=dict(content='', tool_calls=[dict(function=dict(name='buddy_read',
                        arguments=dict(operation='tasks', parameters={})))]))
                answer = '{"owner":null}' if 'owner is unknown' in text else '{"latest_failed":2}' if 'latest_failed' in text else '{"project":"Lantern","pending":7}'
                return dict(message=dict(content=answer))
        with tempfile.TemporaryDirectory(prefix='buddy-ai-real-seam-') as temp:
            service = load_service(source, Path(temp), node_factory=Node)
            server = None
            try:
                service.refresh()
                for capability in ('text', 'tools'):
                    self.assertTrue(service.calibrate(dict(host='local', model='fixture', capability=capability))['passed'])
                def forbidden(*args, **kwargs):
                    raise AssertionError('Facade called broad chat, project search, or worker execution')
                service.chat = service.workspaces.search = service.start_task = forbidden
                server = native_server(service)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                def request(route, payload=None):
                    headers = {'X-INTERLINKED-Buddy-Token': server.controller.buddy.token, 'Content-Type': 'application/json'}
                    req = urllib.request.Request('http://' + server.host + route, headers=headers,
                        data=None if payload is None else json.dumps(payload).encode())
                    with fixture_urlopen(req, timeout=3) as response:
                        return json.load(response)
                models = request('/api/buddy/ai/models')
                self.assertTrue(models['models'][0]['verified']['tools'])
                messages = [dict(role='user', content='Read the current fixture task status.')]
                body = dict(request_id='f' * 32, project_id=PROJECT_ID, host='local', model='fixture', digest='e' * 64, messages=messages)
                def infer(body):
                    row = request('/api/buddy/ai/submit', body)
                    for _ in range(300):
                        if row['status'] not in ('queued', 'running'):
                            break
                        time.sleep(.01)
                        row = request('/api/buddy/ai/job?request_id=' + body['request_id'])
                    self.assertEqual(row['status'], 'completed', row)
                    return row['message']
                first = infer(body)
                self.assertEqual(first['tool_calls'][0]['function']['name'], 'buddy_read')
                messages += [first, dict(role='tool', tool_name='buddy_read', content='{"tasks":[{"task_id":"fixture","status":"queued"}]}')]
                second = infer(dict(body, request_id='1' * 32))
                self.assertIn('queued', second['content'])
                self.assertEqual(service.conversations, {})
                self.assertEqual(service.workspaces.tasks(), [])
                # Exercise the actual coordinator's queued settings guard,
                # rather than only the injected FakeAI signature check.
                snapshot = service.settings_value()
                slot = threading.Lock()
                slot.acquire()
                service.slots['local'] = slot
                before_calls = len(service.nodes['local'].messages)
                try:
                    queued = request('/api/buddy/ai/submit', dict(body, request_id='2' * 32))
                    self.assertEqual(queued['settings_revision'], snapshot['revision'])
                    changed = dict(snapshot['value'], responseTokens=snapshot['value']['responseTokens'] + 1)
                    service.post('settings', dict(settingsRevision=snapshot['revision'], value=changed))
                    for _ in range(300):
                        queued = request('/api/buddy/ai/job?request_id=' + '2' * 32)
                        if queued['status'] not in ('queued', 'running'):
                            break
                        time.sleep(.01)
                    self.assertEqual(queued['status'], 'failed', queued)
                    self.assertEqual(queued['error'], 'configuration_changed')
                    self.assertEqual(queued['effective_settings'], snapshot['value'])
                    self.assertEqual(len(service.nodes['local'].messages), before_calls)
                finally:
                    slot.release()
            finally:
                if server:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)
                service.close()


class RealGuiStartupTest(unittest.TestCase):
    def test_corrupt_buddy_receipt_keeps_real_demo_server_and_core_ai_available(self):
        # Give this test its own GUI class definitions: install() wraps them in
        # place, and must not change other tests' imported GUI singleton.
        module_name = '_buddy_gui_startup_' + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(
            module_name, SOURCE / 'repository/app/interlinked_gui.py')
        gui = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = gui  # dataclasses resolves its defining module
        controller = server = seed = thread = None
        try:
            spec.loader.exec_module(gui)
            buddy.install(gui)
            with tempfile.TemporaryDirectory(prefix='buddy-ai-real-startup-') as temp:
                try:
                    runtime = gui.demo_runtime(Path(temp) / 'state')
                    controller_type = gui.load_companion('interlinked_controller').controller_class(gui)
                    controller = controller_type(runtime)
                    coordinator = gui.load_companion('interlinked_ai')
                    seed = coordinator.AIService(runtime)
                    ai_root = seed.root
                    seed.close()
                    seed = None
                    records = ai_root / 'buddy-requests'
                    records.mkdir()
                    good_path = records / ('a' * 32 + '.json')
                    bad_path = records / ('z' * 32 + '.json')
                    good_bytes = json.dumps(dict(format=AI_FORMAT, request_id='a' * 32,
                        request_hash='0' * 64, status='running', project_id='demo-project-1',
                        model=dict(host='local', id='fixture', digest='a' * 64))).encode()
                    bad_bytes = b'{retained corrupt Buddy receipt'
                    good_path.write_bytes(good_bytes)
                    bad_path.write_bytes(bad_bytes)
                    server = gui.Server(controller)
                    self.assertIsInstance(server.ai, coordinator.AIService)
                    self.assertIs(server.master.ai_service, server.ai)
                    self.assertTrue(controller.r.demo)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    def request(route, native=False):
                        headers = ({'X-INTERLINKED-Buddy-Token': controller.buddy.token} if native
                            else {'X-INTERLINKED-Token': server.token, 'Origin': server.origin})
                        req = urllib.request.Request(server.origin + route, headers=headers)
                        with fixture_urlopen(req, timeout=3) as response:
                            return json.load(response)
                    state = request('/api/state')
                    self.assertFalse(state['busy'])
                    core = request('/api/ai/state')
                    self.assertTrue(core['available'])
                    self.assertTrue(core['demo'])
                    self.assertEqual(core['models'], [])
                    self.assertEqual(core['tasks'], [])
                    self.assertEqual(core['conversations'], [])
                    projects = request('/api/buddy/projects', native=True)
                    self.assertTrue(projects['projects'])
                    optional = request('/api/buddy/ai/models', native=True)
                    self.assertFalse(optional['available'])
                    self.assertEqual(optional['diagnostic']['code'], 'buddy_ai_unavailable')
                    self.assertEqual(good_path.read_bytes(), good_bytes)
                    self.assertEqual(bad_path.read_bytes(), bad_bytes)
                    self.assertEqual(server.ai.nodes, {})
                    self.assertEqual(server.ai.conversations, {})
                    self.assertEqual(server.ai.workspaces.tasks(), [])
                finally:
                    if seed is not None:
                        seed.close()
                    if server is not None:
                        if thread is not None:
                            server.shutdown()
                            thread.join(timeout=3)
                        server.server_close()
                    if controller is not None:
                        controller.close()
        finally:
            sys.modules.pop(module_name, None)


if __name__ == '__main__':
    unittest.main()
