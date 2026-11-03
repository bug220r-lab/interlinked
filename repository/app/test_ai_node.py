"""Isolated AI node protocol, ownership and runtime adapter tests."""
import importlib.util
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import ssl
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import uuid
import copy

spec = importlib.util.spec_from_file_location('ai_node', Path(__file__).with_name('interlinked_ai_node.py'))
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
IDENTITY = {'computer': 'FIXTURE', 'sid': 'S-1-5-21-1-2-3-1001'}
TLS_FIXTURE = None
SEALED_PYTHON = None


def fixture_protect(data, decrypt=False):
    return data[::-1]


class FakeLocal:
    identity = IDENTITY
    def __init__(self):
        self.calls = 0
    def execute(self, action, payload, cancel):
        self.calls += 1
        return {'action': action, 'payload': payload}


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = FakeLocal()
        self.service = n.NodeService(self.local, self.root / 'node', fixture_protect)
    def tearDown(self):
        self.temp.cleanup()
    def envelope(self, action='inventory', payload=None, request_id=None):
        return dict(protocol=n.PROTOCOL, identity=IDENTITY, requestId=request_id or uuid.uuid4().hex, action=action, payload=payload or {})
    def test_pair_once_and_identity(self):
        code = self.service.begin_pairing()
        with self.assertRaises(ValueError):
            self.service.pair({'code': code, 'expectedIdentity': {'computer': 'OTHER'}})
        result = self.service.pair({'code': code, 'expectedIdentity': IDENTITY})
        self.service.authenticate('Bearer ' + result['token'])
        with self.assertRaises(ValueError):
            self.service.pair({'code': code, 'expectedIdentity': IDENTITY})
        self.assertNotIn(result['token'], self.service.token_path.read_text())
    def test_expired_pairing(self):
        code = self.service.begin_pairing()
        self.service.code_deadline = 0
        with self.assertRaises(ValueError):
            self.service.pair({'code': code, 'expectedIdentity': IDENTITY})
    def test_pair_attempt_bound(self):
        code = self.service.begin_pairing()
        for i in range(5):
            with self.assertRaises(ValueError):
                self.service.pair({'code': 'wrong', 'expectedIdentity': IDENTITY})
        with self.assertRaises(ValueError):
            self.service.pair({'code': code, 'expectedIdentity': IDENTITY})
    def test_revoke_survives_restart(self):
        token = self.service.pair({'code': self.service.begin_pairing(), 'expectedIdentity': IDENTITY})['token']
        self.service.revoke()
        other = n.NodeService(self.local, self.root / 'node', fixture_protect)
        with self.assertRaises(ValueError):
            other.authenticate('Bearer ' + token)
    def test_owner_identity(self):
        self.local.identity = {'computer': 'OTHER', 'sid': IDENTITY['sid']}
        with self.assertRaises(ValueError):
            n.NodeService(self.local, self.root / 'node', fixture_protect)
    def test_unowned_nonempty_refused(self):
        folder = self.root / 'unowned'
        folder.mkdir()
        (folder / 'other.txt').write_text('preserve')
        with self.assertRaises(ValueError):
            n.NodeService(self.local, folder, fixture_protect)
    def test_completed_rpc_is_idempotent(self):
        body = self.envelope('load', {'model': 'fixture'})
        first = self.service.rpc(body)
        self.assertEqual(self.service.rpc(body), first)
        self.assertEqual(self.local.calls, 1)
        body['payload'] = {'different': True}
        with self.assertRaises(ValueError):
            self.service.rpc(body)
    def test_interrupted_rpc_not_replayed(self):
        body = self.envelope('load', {'model': 'fixture'})
        with mock.patch.object(self.local, 'execute', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.service.rpc(body)
        with self.assertRaises(ValueError):
            self.service.rpc(body)
        self.assertEqual(self.local.calls, 0)
    def test_unknown_actions_and_identity(self):
        for body in (self.envelope('shell', {'cmd': 'bad'}), {**self.envelope(), 'identity': {}}):
            with self.assertRaises(ValueError):
                self.service.rpc(body)
    def test_worker_hook_only_allowlist(self):
        self.service.worker_dispatch = mock.Mock(return_value={'id': 'fixture'})
        self.assertEqual(self.service.rpc(self.envelope('worker_status', {'taskId': 'a'}))['result']['id'], 'fixture')
        with self.assertRaises(ValueError):
            self.service.rpc(self.envelope('worker_shell'))
    def test_telemetry_is_read_only_allowlisted_and_unjournaled(self):
        observed = n.utc()
        value = dict(gpuId='GPU-12345678-1234-1234-1234-123456789abc', gpuLoad=n.telemetry_metric(35, 'nvidia-smi', observed),
                     token='PRIVATE', path='PRIVATE', prompt='PRIVATE')
        with mock.patch.object(self.local, 'execute', return_value=value) as execute:
            before = list(self.service.requests.iterdir())
            result = self.service.rpc(self.envelope('telemetry'))['result']
            self.assertEqual(result['gpuLoad']['value'], 35)
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(list(self.service.requests.iterdir()), before)
            self.assertEqual(execute.call_args.args[:2], ('telemetry', {}))
            with self.assertRaises(ValueError):
                self.service.rpc(self.envelope('telemetry', {'path': 'arbitrary'}))
    def test_loopback_runtime_only(self):
        for url in ('http://MSI:11434', 'https://127.0.0.1', 'http://user:pass@127.0.0.1'):
            with self.assertRaises(ValueError):
                n.LocalNode(self.root, IDENTITY, url)
    def test_local_ownership_and_busy(self):
        local = n.LocalNode(self.root, IDENTITY)
        with self.assertRaises(ValueError):
            local.unload('outside', True)
        local.lock.acquire()
        try:
            with self.assertRaises(n.Busy):
                local.chat('model', [{'role': 'user', 'content': 'hello'}])
        finally:
            local.lock.release()
    def test_local_chat_and_audio_wire(self):
        local = n.LocalNode(self.root, IDENTITY)
        replies = [{'models': [{'name': 'model'}]}, {'message': {'content': 'hello'}, 'done': True},
                   {'models': [{'name': 'model'}]}, {'choices': [{'message': {'content': 'audio'}}]}]
        with mock.patch.object(local, '_request', side_effect=replies) as request:
            self.assertEqual(local.chat('model', [{'role': 'user', 'content': 'hello'}])['message']['content'], 'hello')
            audio = [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {'data': 'UklGRg==', 'format': 'wav'}}]}]
            self.assertEqual(local.chat('model', audio, audio=True)['message']['content'], 'audio')
            self.assertEqual(request.call_args_list[-1].args[1], '/v1/chat/completions')
            self.assertEqual(request.call_args_list[-1].args[2]['messages'], audio)
    def test_routing_profile_has_fixed_schema_and_preserves_runtime_metrics(self):
        local = n.LocalNode(self.root, IDENTITY)
        result = dict(message={'content': '{"task":"analysis","complexity":"simple","responseFormat":"text"}'},
                      done=True, total_duration=123, load_duration=45, eval_count=19)
        options = {'num_ctx': 8192, 'num_predict': 512}
        with mock.patch.object(local, '_request', side_effect=[{'models': [{'name': 'model'}]}, result]) as request:
            actual = local.chat('model', [{'role': 'user', 'content': 'Classify this prompt.'}],
                                options=options, response_profile='routing_v1')
        self.assertIs(actual, result, 'Return the full runtime envelope for coordinator metrics.')
        wire = request.call_args_list[-1].args[2]
        self.assertEqual(wire['format'], n.ROUTING_SCHEMA)
        self.assertFalse(wire['format']['additionalProperties'])
        self.assertEqual(set(wire['format']['required']), {'task', 'complexity', 'responseFormat'})
        self.assertEqual(wire['options'], {'num_ctx': 8192, 'num_predict': 512})
        self.assertIsNot(wire['format'], n.ROUTING_SCHEMA)
        self.assertNotIn('think', wire)
        self.assertFalse(wire['truncate'])
        self.assertEqual(options, {'num_ctx': 8192, 'num_predict': 512})
    def test_routing_profile_refuses_tools_audio_and_policy_options(self):
        local = n.LocalNode(self.root, IDENTITY)
        cases = [dict(response_profile='arbitrary'), dict(response_profile={}),
                 dict(response_profile='routing_v1', audio=True),
                 dict(response_profile='routing_v1', tools=[{}]),
                 dict(response_profile='routing_v1', options={'think': False}),
                 dict(response_profile='routing_v1', options={'temperature': 0}),
                 dict(response_profile='routing_v1', options={'num_predict': 513}),
                 dict(response_profile='routing_v1', options={'num_ctx': True})]
        with mock.patch.object(local, '_request') as request:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    local.chat('model', [{'role': 'user', 'content': 'prompt'}], **kwargs)
            request.assert_not_called()
        self.assertFalse(local.lock.locked())
    def test_inventory_advertises_fixed_profile_and_propagates_budget(self):
        local = n.LocalNode(self.root, IDENTITY)
        replies = [{'version': 'fixture'}, {'models': [{'name': 'model', 'digest': 'a'*64,
                    'capabilities': ['completion']}]}, {'models': []}]
        cancel = threading.Event()
        with mock.patch.object(local, '_request', side_effect=replies) as request, \
             mock.patch.object(n, 'resources', return_value={}):
            inventory = local.inventory(cancel=cancel, timeout=2)
        self.assertEqual(inventory['responseProfiles'], ['routing_v1', 'history_v1'])
        self.assertIsNone(inventory['inferencePolicySha256'])
        for call in request.call_args_list:
            self.assertIs(call.kwargs['cancel'], cancel)
            self.assertGreater(call.kwargs['timeout'], 0)
            self.assertLessEqual(call.kwargs['timeout'], 2)
    def test_inventory_cancellation_is_not_swallowed_as_missing_capability(self):
        local = n.LocalNode(self.root, IDENTITY)
        cancel = threading.Event()
        replies = [{'version': 'fixture'}, {'models': [{'name': 'model', 'digest': 'a'*64}]},
                   {'models': []}, n.Cancelled('cancelled')]
        with mock.patch.object(local, '_request', side_effect=replies) as request:
            with self.assertRaises(n.Cancelled):
                local.inventory(cancel=cancel, timeout=2)
        self.assertEqual(request.call_count, 4)
    def test_chat_deadline_includes_tag_preflight_and_releases_lock(self):
        local = n.LocalNode(self.root, IDENTITY)
        clock = [100.0]
        def tags(*args, **kwargs):
            clock[0] = 103.0
            return {'models': [{'name': 'model'}]}
        with mock.patch.object(n.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(local, '_request', side_effect=tags) as request:
            with self.assertRaises(n.Cancelled):
                local.chat('model', [{'role': 'user', 'content': 'prompt'}], timeout=2,
                           response_profile='routing_v1')
        self.assertEqual(request.call_count, 1, 'Expired preflight must not start generation.')
        self.assertFalse(local.lock.locked())
    def test_inventory_rpc_is_cancellable_without_journaling(self):
        entered = threading.Event()
        body = self.envelope('inventory', {'timeout': 2})
        observed = []
        def inventory(action, payload, cancel):
            entered.set()
            observed.append(cancel.wait(2))
            return {}
        with mock.patch.object(self.local, 'execute', side_effect=inventory):
            thread = threading.Thread(target=lambda: self.service.rpc(body), daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.service.cancel(body['requestId'])['cancelRequested'])
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, [True])
        self.assertNotIn(body['requestId'], self.service.running)
        self.assertEqual(list(self.service.requests.iterdir()), [])
    def test_remote_profile_uses_relative_budget_and_stable_child_identity(self):
        remote = n.RemoteNode('https://MSI:47443', 'a'*64, IDENTITY, 'PRIVATE')
        request_id = 'b'*32
        def reply(endpoint, fingerprint, path, body, token, **kwargs):
            self.assertEqual(body['requestId'], request_id)
            self.assertEqual(body['payload']['response_profile'], 'routing_v1')
            self.assertNotIn('deadline', body['payload'])
            self.assertGreater(body['payload']['timeout'], 0)
            self.assertLessEqual(body['payload']['timeout'], 2)
            self.assertLessEqual(kwargs['timeout'], 2)
            return dict(protocol=n.PROTOCOL, identity=IDENTITY, requestId=request_id,
                        result={'message': {'content': '{}'}, 'total_duration': 123})
        with mock.patch.object(n, 'remote_request', side_effect=reply):
            result = remote.chat('model', [{'role': 'user', 'content': 'prompt'}],
                                 response_profile='routing_v1', deadline=time.monotonic()+2,
                                 request_id=request_id)
        self.assertEqual(result['total_duration'], 123)
    def test_remote_ordinary_chat_omits_new_profile_for_old_nodes(self):
        remote = n.RemoteNode('https://MSI:47443', 'a'*64, IDENTITY, 'PRIVATE')
        def reply(endpoint, fingerprint, path, body, token, **kwargs):
            self.assertEqual(set(body['payload']), {'model', 'messages', 'tools', 'options', 'timeout', 'audio'})
            return dict(protocol=n.PROTOCOL, identity=IDENTITY, requestId=body['requestId'], result={})
        with mock.patch.object(n, 'remote_request', side_effect=reply):
            remote.chat('model', [{'role': 'user', 'content': 'prompt'}])
    def test_remote_inventory_budget_is_relative_and_cancel_reaches_transport(self):
        remote = n.RemoteNode('https://MSI:47443', 'a'*64, IDENTITY, 'PRIVATE')
        cancel = threading.Event()
        def reply(endpoint, fingerprint, path, body, token, **kwargs):
            self.assertEqual(body['action'], 'inventory')
            self.assertEqual(set(body['payload']), {'timeout'})
            self.assertGreater(body['payload']['timeout'], 0)
            self.assertLessEqual(body['payload']['timeout'], 2)
            self.assertIs(kwargs['cancel'], cancel)
            self.assertLessEqual(kwargs['timeout'], 2)
            return dict(protocol=n.PROTOCOL, identity=IDENTITY, requestId=body['requestId'], result={})
        with mock.patch.object(n, 'remote_request', side_effect=reply):
            remote.inventory(cancel=cancel, timeout=2)
    def test_cloud_missing_and_incomplete_rejected(self):
        local = n.LocalNode(self.root, IDENTITY)
        with self.assertRaises(ValueError):
            local.chat('model:cloud', [{'role': 'user', 'content': 'hello'}])
        with self.assertRaises(ValueError):
            local.load('model:cloud')
        with mock.patch.object(local, '_request', return_value={'models': [{'name': 'model', 'remote_model': 'remote'}]}):
            with self.assertRaises(ValueError):
                local.load('model')
        with mock.patch.object(local, '_request', return_value={'models': []}):
            with self.assertRaises(ValueError):
                local.chat('absent', [{'role': 'user', 'content': 'hello'}])
        with mock.patch.object(local, '_request', side_effect=[{'models': [{'name': 'model'}]}, {'message': {}, 'done': False}]):
            with self.assertRaises(ValueError):
                local.chat('model', [{'role': 'user', 'content': 'hello'}])
    def test_certificate_checked_before_http(self):
        connection = n.PinnedConnection('localhost', 443, '0' * 64)
        connection.sock = mock.Mock()
        connection.sock.getpeercert.return_value = b'wrong-certificate'
        with mock.patch('http.client.HTTPSConnection.connect'), mock.patch.object(connection, 'close') as close:
            with self.assertRaises(ValueError):
                connection.connect()
            close.assert_called_once()
    def test_remote_url_shape(self):
        with self.assertRaises(ValueError):
            n.remote_request('https://user:secret@MSI', '0' * 64, '/node/v1/rpc')
    def test_cancel_only_owned_request(self):
        request_id = uuid.uuid4().hex
        event = threading.Event()
        self.service.running[request_id] = event
        self.assertTrue(self.service.cancel(request_id)['cancelRequested'])
        self.assertTrue(event.is_set())
        self.assertFalse(self.service.cancel(uuid.uuid4().hex)['cancelRequested'])
    def test_credential_roundtrip(self):
        path = self.root / 'peer.json'
        value = {'endpoint': 'https://MSI:47443', 'token': 'private-token'}
        n.save_credentials(path, value, fixture_protect)
        self.assertEqual(n.load_credentials(path, fixture_protect), value)

    def test_stalled_local_response_cancelled(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        entered, release = threading.Event(), threading.Event()
        class Stalled(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                entered.set()
                release.wait(5)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Stalled)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        local = n.LocalNode(self.root, IDENTITY, 'http://127.0.0.1:%d' % server.server_port)
        cancel = threading.Event()
        failures = []
        def request():
            try:
                local._request('GET', '/stall', cancel=cancel, timeout=180)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=request, daemon=True)
        try:
            worker.start()
            self.assertTrue(entered.wait(3))
            cancel.set()
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'Cancellation must interrupt getresponse, not wait for its 180-second timeout.')
            self.assertIsInstance(failures[0], n.Cancelled)
        finally:
            release.set()
            server.shutdown()
            server.server_close()


class TelemetryTests(unittest.TestCase):
    def wire_metrics(self, server_time, **ages):
        stamp = lambda age: n.dt.datetime.fromtimestamp(server_time - age, n.dt.timezone.utc).isoformat()
        values = dict(cpuTemperature=62, gpuTemperature=50.25, gpuLoad=40.5,
                      vramUsed=4*1024**3, vramTotal=8*1024**3)
        return dict(gpuId='GPU-12345678-1234-1234-1234-123456789abc', **{
            key: n.telemetry_metric(value, 'reviewed-sensor-publisher' if key == 'cpuTemperature'
                                    else 'nvidia-smi', stamp(ages.get(key, .25)))
            for key, value in values.items()})

    def test_authenticated_date_normalizes_both_clock_directions_and_independent_fields(self):
        request_started = 1_700_000_000.125
        for skew in (-86400, 86400):
            server_seconds = 1_700_000_000 + skew
            http_date = n.email.utils.formatdate(server_seconds, usegmt=True)
            wire = self.wire_metrics(server_seconds+1, cpuTemperature=2.5, gpuTemperature=.75)
            original = copy.deepcopy(wire)
            result = n.receive_telemetry(wire, http_date, request_started)
            current = n.clean_telemetry(result, request_started+.5)
            self.assertEqual(wire, original)
            self.assertEqual(current['gpuId'], wire['gpuId'])
            for field in n.TELEMETRY_FIELDS:
                self.assertEqual(current[field]['value'], wire[field]['value'])
                self.assertEqual(current[field]['source'], wire[field]['source'])
                translated = n.dt.datetime.fromisoformat(result[field]['observedAt']).timestamp()
                expected_age = 2.5 if field == 'cpuTemperature' else .75 if field == 'gpuTemperature' else .25
                self.assertAlmostEqual(request_started-translated, expected_age)

    def test_date_normalization_never_revives_stale_future_or_invalid_fields(self):
        server_seconds, request_started = 1_700_000_000, 1_600_000_000
        wire = self.wire_metrics(server_seconds+1, cpuTemperature=5.01, gpuTemperature=-.01)
        wire['gpuLoad']['source'] = 'PRIVATE PATH'
        value = n.receive_telemetry(wire, n.email.utils.formatdate(server_seconds, usegmt=True), request_started)
        for key in ('cpuTemperature', 'gpuTemperature', 'gpuLoad'):
            self.assertEqual(value[key]['status'], 'unavailable')
        self.assertEqual(value['vramTotal']['status'], 'available')
        for bad in (None, '', 'not a date', 'Tue, 14 Nov 2023 22:13:20',
                    'Tue, 14 Nov 2023 22:13:20 GMT, Tue, 14 Nov 2023 22:13:21 GMT',
                    'Wed, 14 Nov 2023 22:13:20 GMT'):
            result = n.receive_telemetry(wire, bad, request_started)
            self.assertTrue(all(result[key]['status'] == 'unavailable' for key in n.TELEMETRY_FIELDS))

    def test_date_normalization_charges_whole_second_rounding_and_full_roundtrip(self):
        server_seconds, request_started = 1_700_000_000, 1_600_000_000
        wire = self.wire_metrics(server_seconds+1)
        value = n.receive_telemetry(wire, n.email.utils.formatdate(server_seconds, usegmt=True), request_started)
        self.assertEqual(n.clean_telemetry(value, request_started+4.74)['gpuLoad']['status'], 'available')
        self.assertEqual(n.clean_telemetry(value, request_started+4.76)['gpuLoad']['status'], 'unavailable')
        # A sample at the beginning of the Date second has already spent a full second.
        wire = self.wire_metrics(server_seconds, gpuLoad=0)
        value = n.receive_telemetry(wire, n.email.utils.formatdate(server_seconds, usegmt=True), request_started)
        self.assertEqual(n.clean_telemetry(value, request_started+4.01)['gpuLoad']['status'], 'unavailable')

    def test_remote_request_normalizes_only_telemetry_after_pinned_response(self):
        server_seconds, request_started = 1_700_000_000, 1_600_000_000
        wire = self.wire_metrics(server_seconds+1)
        connection = mock.Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(dict(result=wire)).encode()
        response.getheader.return_value = n.email.utils.formatdate(server_seconds, usegmt=True)
        with mock.patch.object(n, 'TRANSPORT', None), \
             mock.patch.object(n, 'PinnedConnection', return_value=connection), \
             mock.patch.object(n, 'InterruptibleSocket', side_effect=lambda sock, *args: sock), \
             mock.patch.object(n.time, 'time', return_value=request_started):
            for action in ('inventory', 'chat', 'telemetry'):
                result = n.remote_request('https://MSI:47443', 'a'*64, '/node/v1/rpc', {'action': action})
                if action == 'telemetry':
                    self.assertEqual(n.clean_telemetry(result['result'], request_started)['gpuLoad']['value'], 40.5)
                    connection.connect.assert_called()
                    response.getheader.assert_called_once_with('Date')
                else:
                    self.assertEqual(result['result'], wire)
                    response.getheader.assert_not_called()
            response.getheader.return_value = None
            result = n.remote_request('https://MSI:47443', 'a'*64, '/node/v1/rpc', {'action': 'telemetry'})
            self.assertTrue(all(result['result'][key]['status'] == 'unavailable' for key in n.TELEMETRY_FIELDS))

    def test_gpu_sampler_has_exact_device_identity_and_no_model_requests(self):
        row = 'GPU-12345678-1234-1234-1234-123456789abc, 8192, 3072, 43.5, 58.25\n'
        with mock.patch.object(n.subprocess, 'run', return_value=type('R', (), {'stdout': row})()) as run, \
             mock.patch.object(n.Path, 'is_file', return_value=True), \
             mock.patch.object(n, 'publisher_temperatures', return_value={}):
            value = n.sample_telemetry(IDENTITY)
        self.assertEqual(value['gpuId'], 'GPU-12345678-1234-1234-1234-123456789abc')
        self.assertEqual(value['vramTotal']['value'], 8*1024**3)
        self.assertEqual(value['vramUsed']['value'], 3*1024**3)
        self.assertEqual((value['gpuLoad']['value'], value['gpuTemperature']['value']), (43.5, 58.25))
        argv = run.call_args.args[0]
        self.assertIn('--id=0', argv)
        self.assertEqual(run.call_args.kwargs['timeout'], 1.5)
        self.assertFalse(any('ollama' in argument.lower() for argument in argv))

    @unittest.skipUnless(os.name == 'nt', 'Windows sensor publisher identity/ACL contract')
    def test_publisher_descriptor_identity_hash_and_acl_are_required(self):
        with tempfile.TemporaryDirectory(prefix='interlinked-sensor-reader-') as tmp:
            folder = Path(tmp)/'output'; folder.mkdir()
            publisher = Path(tmp)/'publisher.exe'; publisher.write_bytes(b'fixture')
            descriptor = dict(format='interlinked-msi-sensor-descriptor-v1', machine='MSI',
                userSid=IDENTITY['sid'], publisherPath=str(publisher), outputPath=str(folder/'temperatures.json'),
                publisherSha256=hashlib.sha256(b'fixture').hexdigest())
            value = dict(format='interlinked-sensor-telemetry-v1', machine='MSI',
                publisherSha256=descriptor['publisherSha256'],
                cpuTemperature=n.telemetry_metric(60, 'reviewed-sensor-publisher', n.utc()))
            (folder/'descriptor.json').write_text(json.dumps(descriptor))
            (folder/'temperatures.json').write_text(json.dumps(value))
            with mock.patch.object(n, 'MSI_SENSOR_FOLDER', folder), \
                 mock.patch.object(n, 'MSI_SENSOR_PUBLISHER', publisher), \
                 mock.patch.object(n, 'protected_sensor_path') as permissions:
                identity = dict(IDENTITY, computer='MSI')
                self.assertEqual(n.publisher_temperatures(identity)['cpuTemperature']['value'], 60)
                self.assertGreaterEqual(permissions.call_count, 5)
                descriptor['userSid'] = 'OTHER'
                (folder/'descriptor.json').write_text(json.dumps(descriptor))
                self.assertFalse(n.publisher_temperatures(identity))
                descriptor['userSid'] = IDENTITY['sid']
                (folder/'descriptor.json').write_text(json.dumps(descriptor))
                publisher.write_bytes(b'changed')
                self.assertFalse(n.publisher_temperatures(identity))
                publisher.write_bytes(b'fixture')
                permissions.side_effect = ValueError('Untrusted permissions')
                self.assertFalse(n.publisher_temperatures(identity))

    def test_independent_freshness_identity_bounds_and_source_allowlist(self):
        now = time.time()
        stamp = lambda seconds=0: n.dt.datetime.fromtimestamp(now+seconds-.01, n.dt.timezone.utc).isoformat()
        value = dict(gpuId='GPU-12345678-1234-1234-1234-123456789abc',
            cpuTemperature=n.telemetry_metric(62, 'reviewed-sensor-publisher', stamp()),
            gpuTemperature=n.telemetry_metric(50, 'nvidia-smi', stamp(-6)),
            gpuLoad=n.telemetry_metric(40, 'nvidia-smi', stamp()),
            vramUsed=n.telemetry_metric(4*1024**3, 'nvidia-smi', stamp()),
            vramTotal=n.telemetry_metric(8*1024**3, 'nvidia-smi', stamp()))
        clean = n.clean_telemetry(value, now)
        self.assertEqual(clean['cpuTemperature']['value'], 62)
        self.assertIsNone(clean['gpuTemperature']['value'])
        self.assertEqual(clean['gpuLoad']['value'], 40)
        for bad in (True, float('nan'), float('inf'), -1, 101):
            self.assertIsNone(n.clean_telemetry(dict(value, gpuLoad=n.telemetry_metric(bad, 'nvidia-smi', stamp())), now)['gpuLoad']['value'])
        self.assertIsNone(n.clean_telemetry(dict(value, gpuId=None), now)['gpuLoad']['value'])
        self.assertEqual(n.clean_telemetry(dict(value, gpuId=None), now)['cpuTemperature']['value'], 62)
        self.assertIsNone(n.clean_telemetry(dict(value, gpuLoad=n.telemetry_metric(40, 'PRIVATE PATH', stamp())), now)['gpuLoad']['value'])
        self.assertIsNone(n.clean_telemetry(dict(value, gpuLoad=n.telemetry_metric(40, 'nvidia-smi', stamp(1))), now)['gpuLoad']['value'])
        value['vramUsed']['value'] = 9*1024**3
        self.assertIsNone(n.clean_telemetry(value, now)['vramUsed']['value'])

    def test_sampler_reads_cache_does_not_collect_or_extend_timestamps(self):
        collector = mock.Mock(side_effect=AssertionError('Snapshot must not query hardware.'))
        sampler = n.TelemetrySampler(IDENTITY, collector=collector, start=False)
        try:
            sampler.value = dict(gpuId='GPU-12345678-1234-1234-1234-123456789abc', gpuLoad=n.telemetry_metric(20, 'nvidia-smi', n.utc()))
            self.assertEqual(sampler.snapshot()['gpuLoad']['value'], 20)
            sampler.snapshot()['gpuLoad']['value'] = 99
            self.assertEqual(sampler.snapshot()['gpuLoad']['value'], 20)
            with mock.patch.object(n.time, 'time', return_value=time.time()+6):
                self.assertIsNone(sampler.snapshot()['gpuLoad']['value'])
            collector.assert_not_called()
        finally:
            sampler.close()

    def test_single_background_sampler_never_overlaps(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def collect(identity):
            calls.append(identity)
            entered.set()
            release.wait(3)
            return {}
        sampler = n.TelemetrySampler(IDENTITY, collector=collect)
        try:
            self.assertTrue(entered.wait(1))
            for _ in range(20): sampler.snapshot()
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            sampler.close()

    def test_remote_telemetry_uses_pinned_auth_and_short_timeout(self):
        remote = n.RemoteNode('https://MSI:47443', 'a'*64, IDENTITY, 'PRIVATE')
        def reply(endpoint, fingerprint, path, body, token, **kwargs):
            self.assertEqual((endpoint, fingerprint, path, token), (remote.endpoint, remote.fingerprint, '/node/v1/rpc', 'PRIVATE'))
            self.assertEqual((body['action'], body['payload'], kwargs['timeout']), ('telemetry', {}, 2))
            return dict(protocol=n.PROTOCOL, requestId=body['requestId'], identity=IDENTITY, result={})
        with mock.patch.object(n, 'remote_request', side_effect=reply):
            self.assertIsNone(remote.telemetry()['gpuLoad']['value'])
        with mock.patch.object(n, 'remote_request', return_value=dict(protocol=n.PROTOCOL, requestId='wrong', identity=IDENTITY, result={})):
            with self.assertRaises(ValueError): remote.telemetry()


class InferencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'inference-policy.json'
        self.digest = 'a' * 64
        self.policy = dict(format='interlinked-ai-inference-policy-v1', identity=IDENTITY,
                           models=[dict(model='fixture:4b', digest=self.digest, think=False)])
    def tearDown(self):
        self.temp.cleanup()
    def write(self, value=None):
        self.path.write_text(json.dumps(self.policy if value is None else value), encoding='utf-8')
        return hashlib.sha256(self.path.read_bytes()).hexdigest()
    def node(self, value=None):
        return n.LocalNode(self.root / 'state', IDENTITY, inference_policy=self.path,
                           inference_policy_sha256=self.write(value))
    def start(self, local):
        return n.NodeService(local, self.root / 'state', fixture_protect)
    def test_retained_pin_allows_identical_policy_restart(self):
        first = self.node()
        self.start(first)
        pin_path = self.root / 'state' / 'inference-policy-pin.json'
        original = pin_path.read_bytes()
        self.start(self.node())
        self.assertEqual(pin_path.read_bytes(), original)
        self.assertEqual(json.loads(original)['sha256'], first.inference_policy_sha256)
    def test_retained_pin_refuses_changed_policy_even_with_new_valid_file_hash(self):
        self.start(self.node())
        pin_path = self.root / 'state' / 'inference-policy-pin.json'
        original = pin_path.read_bytes()
        changed = copy.deepcopy(self.policy)
        changed['models'][0]['think'] = True
        local = self.node(changed)
        self.assertNotEqual(local.inference_policy_sha256, json.loads(original)['sha256'])
        with self.assertRaisesRegex(ValueError, 'reviewed migration and calibration reset'):
            self.start(local)
        self.assertEqual(pin_path.read_bytes(), original)
    def test_retained_pin_refuses_policy_removal_and_legacy_stays_unchanged(self):
        legacy = n.LocalNode(self.root / 'state', IDENTITY)
        self.start(legacy)
        pin_path = self.root / 'state' / 'inference-policy-pin.json'
        self.assertFalse(pin_path.exists())
        self.start(self.node())
        original = pin_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'was removed'):
            self.start(n.LocalNode(self.root / 'state', IDENTITY))
        self.assertEqual(pin_path.read_bytes(), original)
    def test_policy_hash_and_identity_are_required(self):
        pin = self.write()
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            n.LocalNode(self.root, IDENTITY, inference_policy=self.path, inference_policy_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            n.LocalNode(self.root, dict(IDENTITY, computer='OTHER'), inference_policy=self.path, inference_policy_sha256=pin)
        for kwargs in (dict(inference_policy=self.path), dict(inference_policy_sha256=pin)):
            with self.assertRaisesRegex(ValueError, 'required together'):
                n.LocalNode(self.root, IDENTITY, **kwargs)
    def test_policy_schema_rejects_non_boolean_and_extra_options(self):
        for value in ('false', 0, 1, None, []):
            policy = copy.deepcopy(self.policy)
            policy['models'][0]['think'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'boolean'):
                self.node(policy)
        for changes in (dict(num_gpu=0), dict(digest='not-a-digest')):
            policy = copy.deepcopy(self.policy)
            policy['models'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.node(policy)
        policy = copy.deepcopy(self.policy)
        policy['models'].append(dict(policy['models'][0]))
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.node(policy)
    def test_policy_matches_wire_and_is_frozen_until_restart(self):
        local = self.node()
        pin = local.inference_policy_sha256
        changed = copy.deepcopy(self.policy)
        changed['models'][0]['think'] = True
        self.write(changed)
        replies = [{'models': [{'name': 'fixture:4b', 'digest': self.digest}]},
                   {'message': {'content': 'ok'}, 'done': True}]
        with mock.patch.object(local, '_request', side_effect=replies) as request:
            local.chat('fixture:4b', [{'role': 'user', 'content': 'hello'}], options={'num_ctx': 8192})
            wire = request.call_args_list[-1].args[2]
            self.assertIs(wire['think'], False)
            self.assertEqual(wire['options'], {'num_ctx': 8192})
            self.assertFalse(wire['stream'])
            self.assertFalse(wire['truncate'])
        self.assertEqual(local.inference_policy_sha256, pin)
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            n.LocalNode(self.root, IDENTITY, inference_policy=self.path, inference_policy_sha256=pin)
    def test_true_policy_and_unconfigured_model_behavior(self):
        policy = copy.deepcopy(self.policy)
        policy['models'][0]['think'] = True
        local = self.node(policy)
        for name, digest, configured in [('fixture:4b', self.digest, True), ('other:4b', 'b' * 64, False)]:
            replies = [{'models': [{'name': name, 'digest': digest}]}, {'message': {'content': 'ok'}, 'done': True}]
            with mock.patch.object(local, '_request', side_effect=replies) as request:
                local.chat(name, [{'role': 'user', 'content': 'hello'}])
                wire = request.call_args_list[-1].args[2]
                if configured:
                    self.assertIs(wire['think'], True)
                else:
                    self.assertNotIn('think', wire)
        unconfigured = n.LocalNode(self.root, IDENTITY)
        self.assertIsNone(unconfigured.inference_policy_sha256)
        self.assertIsNone(unconfigured._model_policy('fixture:4b', self.digest))
    def test_configured_digest_mismatch_refuses_before_generation(self):
        local = self.node()
        with mock.patch.object(local, '_request', return_value={'models': [{'name': 'fixture:4b', 'digest': 'b' * 64}]}) as request:
            with self.assertRaisesRegex(ValueError, 'model digest differs'):
                local.chat('fixture:4b', [{'role': 'user', 'content': 'hello'}])
            self.assertEqual(request.call_count, 1)
        self.assertFalse(local.lock.locked())
    def test_routing_profile_keeps_pinned_thinking_policy(self):
        local = self.node()
        pin = local.inference_policy_sha256
        replies = [{'models': [{'name': 'fixture:4b', 'digest': self.digest}]},
                   {'message': {'content': '{}'}, 'done': True}]
        with mock.patch.object(local, '_request', side_effect=replies) as request:
            local.chat('fixture:4b', [{'role': 'user', 'content': 'classify'}],
                       response_profile='routing_v1', options={'num_ctx': 8192})
        wire = request.call_args_list[-1].args[2]
        self.assertIs(wire['think'], False)
        self.assertEqual(wire['options'], {'num_ctx': 8192, 'num_predict': 512})
        self.assertEqual(wire['format'], n.ROUTING_SCHEMA)
        self.assertEqual(local.inference_policy_sha256, pin)
    def test_configured_audio_is_refused_without_silent_policy_loss(self):
        local = self.node()
        with mock.patch.object(local, '_request', return_value={'models': [{'name': 'fixture:4b', 'digest': self.digest}]}) as request:
            with self.assertRaisesRegex(ValueError, 'native chat'):
                local.chat('fixture:4b', [{'role': 'user', 'content': 'hello'}], audio=True)
            self.assertEqual(request.call_count, 1)
    def test_inventory_keeps_real_version_and_digest_with_policy_metadata(self):
        local = self.node()
        for digest in (self.digest, 'b' * 64):
            replies = [{'version': '0.40.13'}, {'models': [{'name': 'fixture:4b', 'digest': digest,
                        'capabilities': ['completion']}]}, {'models': []}]
            with mock.patch.object(local, '_request', side_effect=replies), mock.patch.object(n, 'resources', return_value={}):
                inventory = local.inventory()
            self.assertEqual(inventory['runtime']['version'], '0.40.13')
            self.assertEqual(inventory['runtime']['inferencePolicySha256'], local.inference_policy_sha256)
            self.assertEqual(inventory['inferencePolicySha256'], local.inference_policy_sha256)
            row = inventory['models'][0]
            self.assertEqual(row['digest'], digest)
            self.assertEqual(row['inferencePolicy']['status'], 'matched' if digest == self.digest else 'digest_mismatch')
            self.assertEqual(row['inferencePolicy']['think'], False if digest == self.digest else None)


@unittest.skipUnless(os.name == 'nt', 'Native launcher checks require Windows.')
class InferencePolicyLauncherTests(unittest.TestCase):
    def test_launcher_pin_and_optional_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / 'interlinked_ai_node.py'
            stub.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
            manifest = root / 'NODE-CHECKSUMS.json'
            names = ['interlinked_ai_node.py', 'interlinked_ai_workspaces.py', 'interlinked_stats_process.py', 'interlinked_configuration.py',
                     'Initialize-Node.ps1', 'Start-Node.ps1', 'Set-NodeNetwork.ps1', 'Set-TransportRuntime.ps1', 'README.md',
                     'interlinked_history_import.py', 'interlinked_history_store.py', 'interlinked_history_service.py',
                     'interlinked_history_runtime.py', 'interlinked_history_gateway.py', 'Migrate-Node.ps1']
            for name in names:
                if name != stub.name:
                    (root / name).write_text('# Synthetic sealed companion; never executed.\n', encoding='utf-8')
            manifest.write_text(json.dumps({'format': 'interlinked-ai-node-package-v2', 'files': [
                {'path': name, 'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest()} for name in names]}), encoding='utf-8')
            identity = n.current_identity()
            record = dict(format='interlinked-ai-node-install-v1', computer=identity['computer'], sid=identity['sid'],
                          python=sys.executable, pythonSha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                          manifestSha256=hashlib.sha256(manifest.read_bytes()).hexdigest(), bind='127.0.0.1', port=47443)
            launcher = Path(__file__).resolve().parent.parent / 'ai-node' / 'Start-Node.ps1'
            command = [str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                       '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(launcher), '-InstallDirectory', str(root)]
            child_env = os.environ.copy()
            # A PS7 parent can pass a PSModulePath without native PS5 modules.
            child_env['PSModulePath'] = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules')
            def run():
                (root / 'node.json').write_text(json.dumps(record), encoding='utf-8')
                return subprocess.run(command, capture_output=True, text=True, timeout=20,
                                      creationflags=0x08000000, env=child_env)
            plain = run()
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertNotIn('--inference-policy', json.loads(plain.stdout))
            policy = root / 'inference-policy.json'
            policy.write_text('{}', encoding='utf-8')
            record['inferencePolicySha256'] = hashlib.sha256(policy.read_bytes()).hexdigest()
            pinned = run()
            self.assertEqual(pinned.returncode, 0, pinned.stderr)
            args = json.loads(pinned.stdout)
            self.assertEqual(args[args.index('--inference-policy') + 1], str(policy))
            self.assertEqual(args[args.index('--inference-policy-sha256') + 1], record['inferencePolicySha256'])
            policy.write_text('{"changed":true}', encoding='utf-8')
            mismatch = run()
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn('Inference policy hash differs', mismatch.stderr)
            self.assertEqual(mismatch.stdout.strip(), '')


class TLSTests(unittest.TestCase):
    def setUp(self):
        if TLS_FIXTURE is None:
            self.skipTest('Pass --tls-fixture with an initialized isolated package to exercise real TLS and DPAPI.')
        self.temp = tempfile.TemporaryDirectory()
        self.local = FakeLocal()
        self.service = n.NodeService(self.local, Path(self.temp.name) / 'state')
        cert, key = TLS_FIXTURE / 'Secrets/node-cert.pem', TLS_FIXTURE / 'Secrets/node-key.pem'
        self.pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        self.server = n.make_server(self.service, ('127.0.0.1', 0), cert, key)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = 'https://127.0.0.1:%d' % self.server.server_port
    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.temp.cleanup()
    def test_real_pair_rpc_revoke(self):
        peer = n.pair_remote(self.endpoint, self.pin, self.service.begin_pairing(), IDENTITY)
        remote = n.RemoteNode(**peer)
        self.assertEqual(remote.inventory()['action'], 'inventory')
        self.service.revoke()
        with self.assertRaises(ValueError):
            remote.inventory()
    def test_wrong_pin_and_identity_before_dispatch(self):
        code = self.service.begin_pairing()
        with self.assertRaises(ValueError):
            n.pair_remote(self.endpoint, '0' * 64, code, IDENTITY)
        self.assertEqual(self.service.code_attempts, 0, 'Incorrect certificate must be rejected before the pairing code is sent.')
        with self.assertRaises(ValueError):
            n.pair_remote(self.endpoint, self.pin, code, {'computer': 'WRONG'})
        self.assertEqual(self.local.calls, 0)
    def test_dpapi_bound_credential(self):
        path = Path(self.temp.name) / 'peer.json'
        payload = {'token': 'fixture-private-token', 'identity': IDENTITY}
        n.save_credentials(path, payload)
        self.assertNotIn(payload['token'], path.read_text())
        self.assertEqual(n.load_credentials(path), payload)
    def test_sealed_python_uses_pinned_external_transport(self):
        if SEALED_PYTHON is None:
            self.skipTest('Pass --sealed-python to verify the sealed coordinator runtime through an external TLS helper.')
        code = """
import importlib.util,json,pathlib,sys
p=json.loads(sys.stdin.buffer.read())
s=importlib.util.spec_from_file_location('fixture_node',p['source']);n=importlib.util.module_from_spec(s);s.loader.exec_module(n)
n.configure_transport(dict(format='interlinked-ai-transport-v1',python=p['python'],sha256=p['sha256'],identity=n.current_identity()))
peer=n.pair_remote(p['endpoint'],p['fingerprint'],p['code'],p['identity'])
r=n.RemoteNode(**peer).inventory()
assert r['action']=='inventory'
print('SEALED_TRANSPORT_VERIFIED')
"""
        payload = dict(source=str(Path(n.__file__).absolute()), python=sys.executable,
                       sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(), endpoint=self.endpoint,
                       fingerprint=self.pin, code=self.service.begin_pairing(), identity=IDENTITY)
        result = subprocess.run([str(SEALED_PYTHON), '-I', '-B', '-c', code], input=json.dumps(payload).encode(),
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.strip(), b'SEALED_TRANSPORT_VERIFIED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--tls-fixture')
    parser.add_argument('--sealed-python')
    args, remaining = parser.parse_known_args()
    TLS_FIXTURE = Path(args.tls_fixture).absolute() if args.tls_fixture else None
    SEALED_PYTHON = Path(args.sealed_python).absolute() if args.sealed_python else None
    unittest.main(argv=[sys.argv[0], *remaining])
