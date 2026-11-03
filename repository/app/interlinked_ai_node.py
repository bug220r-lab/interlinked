"""INTERLINKED AI node v1: local Ollama and certificate-pinned HTTPS RPC.

No discovery scan, runtime installation, arbitrary URL proxy, or model download.
TLS keys and state must be provisioned in an owned, user-only directory. Tokens
are protected with Windows DPAPI; tests inject an isolated secret protector.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import ctypes
import datetime as dt
import email.utils
import hashlib
import hmac
import http.client
import importlib.util
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

PROTOCOL = 'interlinked-ai-node-v1'
MAX_BODY = 48 * 1024 * 1024
WORKER_ACTIONS = {'worker_prepare', 'worker_start', 'worker_result', 'worker_status', 'worker_cancel', 'worker_infer_result'}
OPERATION_ACTIONS = {'operation_query', 'operation_cancel'}
ACTIONS = {'inventory', 'telemetry', 'chat', 'load', 'unload', 'load_identity', 'embed', 'history'} | WORKER_ACTIONS | OPERATION_ACTIONS
ID = re.compile(r'^[a-f0-9]{32}$')
TRANSPORT = None
ROUTING_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'task': {'type': 'string', 'enum': ['writing', 'extraction', 'analysis', 'coding', 'tools']},
        'complexity': {'type': 'string', 'enum': ['simple', 'moderate', 'complex']},
        'responseFormat': {'type': 'string', 'enum': ['text', 'json', 'code']},
    },
    'required': ['task', 'complexity', 'responseFormat'],
}
RESPONSE_PROFILES = ('routing_v1', 'history_v1')


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def operation_deadline(timeout=None, deadline=None):
    """Monotonic deadlines stay on their owning machine, never on the wire."""
    for value in (timeout, deadline):
        need(value is None or type(value) in (int, float) and math.isfinite(value) and value > 0,
             'Invalid operation budget.')
    if timeout is not None:
        need(timeout <= 1810, 'Operation budget exceeds its limit.')
        limit = time.monotonic() + timeout
        deadline = min(deadline, limit) if deadline is not None else limit
    return deadline


def remaining_budget(deadline, maximum, cancel=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Request cancelled.')
    remaining = maximum if deadline is None else min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        raise Cancelled('Operation deadline expired.')
    return remaining


def ordinary(path, missing=False):
    path = Path(path)
    need(path.is_absolute() and not str(path).startswith('\\\\'), 'An ordinary local absolute path is required.')
    for item in reversed((path, *path.parents)):
        try:
            st = item.lstat()
        except FileNotFoundError:
            need(missing, 'Owned node path is missing.')
            continue
        need(not item.is_symlink() and not getattr(st, 'st_file_attributes', 0) & 0x441400,
             'Linked or offline node paths are refused.')
        if item.is_file():
            need(st.st_nlink == 1, 'Shared node files are refused.')
    return path


def write_json(path, value):
    path = ordinary(path, True)
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    need(len(data) <= MAX_BODY, 'Node record exceeds its limit.')
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path):
    with ordinary(path).open('rb') as stream:
        data = stream.read(MAX_BODY + 1)
    need(len(data) <= MAX_BODY, 'Node record exceeds its limit.')
    return json.loads(data)


def load_inference_policy(path, expected_sha256, identity):
    """Load a startup-only, hash-pinned policy; never accept request overrides."""
    need(isinstance(expected_sha256, str) and re.fullmatch('[a-fA-F0-9]{64}', expected_sha256),
         'A reviewed inference policy SHA256 is required.')
    with ordinary(Path(path).absolute()).open('rb') as stream:
        data = stream.read(65537)
    need(len(data) <= 65536, 'Inference policy exceeds its limit.')
    need(hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256.lower()),
         'Inference policy hash differs.')
    value = json.loads(data)
    need(isinstance(value, dict) and set(value) == {'format', 'identity', 'models'} and
         value['format'] == 'interlinked-ai-inference-policy-v1' and value['identity'] == identity,
         'Inference policy format or node identity differs.')
    need(isinstance(value['models'], list) and 1 <= len(value['models']) <= 64,
         'Inference policy requires bounded model entries.')
    entries, names = [], set()
    for row in value['models']:
        need(isinstance(row, dict) and set(row) == {'model', 'digest', 'think'},
             'Inference policy permits only model, digest and think.')
        name = row['model']
        need(isinstance(name, str) and 0 < len(name) <= 256 and name not in names and
             not name.endswith('-cloud') and ':cloud' not in name,
             'Inference policy requires unique installed model tags.')
        need(isinstance(row['digest'], str) and re.fullmatch('[a-f0-9]{64}', row['digest']),
             'Inference policy requires an exact model digest.')
        need(type(row['think']) is bool, 'Inference policy think must be a boolean.')
        names.add(name)
        entries.append((name, row['digest'], row['think']))
    return tuple(entries), expected_sha256.lower()


def retain_inference_policy_pin(state_root, identity, policy_sha256):
    """Keep a policy stable across restarts without changing model/version IDs.

    First enable requires operator verification that this host has no retained
    calibrations, followed by fresh calibration. Later change/removal requires a
    reviewed migration with calibration reset; no migration is implemented here.
    Call only after NodeService has verified the state's ownership record.
    """
    path = ordinary(Path(state_root) / 'inference-policy-pin.json', True)
    expected = dict(format='interlinked-ai-inference-policy-pin-v1', identity=identity,
                    sha256=policy_sha256)
    if path.exists():
        need(policy_sha256 is not None and read_json(path) == expected,
             'Retained inference policy differs or was removed; reviewed migration and calibration reset are required.')
    elif policy_sha256 is not None:
        # Exclusive creation preserves a concurrent writer's pin. A partial
        # failed write is refused on restart instead of being silently repaired.
        with path.open('xb') as stream:
            stream.write(json.dumps(expected, separators=(',', ':')).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())


def current_identity():
    need(os.name == 'nt', 'Production AI nodes require Windows.')
    result = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/whoami.exe'), '/user', '/fo', 'csv', '/nh'],
                            capture_output=True, text=True, timeout=5, creationflags=0x08000000, check=True)
    match = re.search(r'S-1-5-[0-9-]+', result.stdout)
    need(match is not None, 'Windows account identity was unavailable.')
    return {'computer': socket.gethostname().upper(), 'sid': match.group(0)}


def protect(data, decrypt=False):
    need(os.name == 'nt', 'Windows DPAPI is required for node credentials.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    need(fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)), 'Credential protection failed.')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(outgoing.data)


def save_credentials(path, value, protector=protect):
    write_json(path, {'format': PROTOCOL, 'protected': base64.b64encode(protector(json.dumps(value).encode())).decode()})


def load_credentials(path, protector=protect):
    value = read_json(path)
    need(value.get('format') == PROTOCOL, 'Credential format differs.')
    return json.loads(protector(base64.b64decode(value['protected'], validate=True), decrypt=True))


def configure_transport(config):
    """Configure only from owned server-side state, never a browser argument."""
    global TRANSPORT
    config = read_json(config) if isinstance(config, (str, Path)) else dict(config)
    need(config.get('format') == 'interlinked-ai-transport-v1' and config.get('identity') == current_identity(), 'TLS transport identity differs.')
    executable = ordinary(Path(config['python']))
    need(executable.suffix.lower() == '.exe' and hashlib.sha256(executable.read_bytes()).hexdigest() == config.get('sha256'), 'Reviewed TLS interpreter differs.')
    TRANSPORT = config


def external_transport(payload, timeout, cancel=None):
    config = dict(TRANSPORT)
    executable = ordinary(Path(config['python']))
    need(hashlib.sha256(executable.read_bytes()).hexdigest() == config['sha256'], 'Reviewed TLS interpreter changed.')
    packet = json.dumps(payload).encode()
    need(len(packet) <= MAX_BODY, 'TLS transport request exceeds its limit.')
    process = subprocess.Popen([str(executable), '-I', '-B', str(ordinary(Path(__file__).absolute())), '--transport-once'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
    result = {}
    finished = threading.Event()
    def communicate():
        try:
            result['stdout'], result['stderr'] = process.communicate(packet)
        except Exception as exc:
            result['error'] = exc
        finally:
            finished.set()
    threading.Thread(target=communicate, daemon=True).start()
    deadline = time.monotonic() + timeout + 5
    try:
        while not finished.wait(.1):
            if cancel is not None and cancel.is_set() or time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=5)
                raise Cancelled('TLS transport cancelled or timed out.')
        need(process.returncode == 0 and 'error' not in result, 'TLS transport failed; verify its reviewed runtime dependencies.')
        need(len(result['stdout']) <= MAX_BODY, 'TLS transport response exceeds its limit.')
        value = json.loads(result['stdout'])
        need(value.get('ok') is True, str(value.get('error', 'TLS transport failed.'))[:500])
        return value['result']
    finally:
        if process.poll() is None:
            process.kill()
        finished.wait(5)


class Cancelled(ValueError):
    pass


class Busy(ValueError):
    pass


class InterruptibleSocket:
    """Buffered HTTP reads with short, retryable receive timeouts on Windows.

Closing a socket from another thread does not reliably wake Windows recv/select.
The RawIO adapter retries timeouts before BufferedReader sees them, preserving
partially received HTTP headers and bodies without replaying the request.
"""
    def __init__(self, sock, cancel, timeout):
        self.sock, self.cancel = sock, cancel
        self.deadline = time.monotonic() + timeout
        self.references, self.closing = 0, False
    def __getattr__(self, name):
        return getattr(self.sock, name)
    def close(self):
        self.closing = True
        if not self.references:
            self.sock.close()
    def makefile(self, mode, buffering=None):
        need(mode == 'rb', 'Unexpected HTTP stream mode.')
        owner = self
        self.references += 1
        self.sock.settimeout(.25)
        class Reader(io.RawIOBase):
            def readable(self):
                return True
            def readinto(self, buffer):
                while True:
                    if owner.cancel is not None and owner.cancel.is_set() or time.monotonic() >= owner.deadline:
                        raise Cancelled('Node request cancelled or timed out.')
                    try:
                        return owner.sock.recv_into(buffer)
                    except socket.timeout:
                        continue
            def close(self):
                if not self.closed:
                    owner.references -= 1
                    if owner.closing and not owner.references:
                        owner.sock.close()
                super().close()
        return io.BufferedReader(Reader())


class LocalNode:
    def __init__(self, state_root, identity=None, ollama_url='http://127.0.0.1:11434',
                 inference_policy=None, inference_policy_sha256=None):
        self.root = ordinary(Path(state_root).absolute(), True)
        self.identity = identity or current_identity()
        url = urlsplit(ollama_url)
        need(url.scheme == 'http' and url.hostname in ('127.0.0.1', '::1') and not url.username and not url.query and url.path in ('', '/'),
             'Ollama must use an explicit loopback endpoint.')
        self.address = (url.hostname, url.port or 11434)
        self.lock = threading.Lock()
        self._history_init_lock = threading.Lock()
        self._schedule = threading.Condition()
        self._interactive_waiters = 0
        self._embedding_active = False
        self.owned_models = set()
        self.node_session = uuid.uuid4().hex
        self.load_sessions = {}
        self._capability_cache = {}
        self._capability_lock = threading.Lock()
        need((inference_policy is None) == (inference_policy_sha256 is None),
             'Inference policy path and reviewed hash are required together.')
        self._inference_policy, self.inference_policy_sha256 = (), None
        if inference_policy is not None:
            self._inference_policy, self.inference_policy_sha256 = load_inference_policy(
                inference_policy, inference_policy_sha256, self.identity)
        # A separate hardware sampler never acquires the inference/model lock.
        # Injected fixture identities must not probe the real workstation.
        self._telemetry = TelemetrySampler(self.identity,
            start=os.name == 'nt' and self.identity.get('computer') == socket.gethostname().upper())

    def telemetry(self):
        return self._telemetry.snapshot()

    def close(self):
        if getattr(self, '_history_service', None) is not None:
            self._history_service.close()
        self._telemetry.close()

    def embed(self, model, texts, digest, cancel=None, background=False):
        need(type(background) is bool, 'Invalid embedding scheduling mode.')
        need(model == 'embeddinggemma:300m' and isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest), 'Select the pinned local history embedding model.')
        need(isinstance(texts, list) and 0 < len(texts) <= 128 and all(isinstance(x, str) and 0 < len(x.encode('utf-8')) <= 7000 for x in texts), 'Bounded embedding text required.')
        deadline = time.monotonic() + 180
        with self._schedule:
            while (background and self._interactive_waiters) or not self.lock.acquire(False):
                need(cancel is None or not cancel.is_set(), 'Embedding cancelled.')
                need(time.monotonic() < deadline, 'Embedding host remained busy.')
                self._schedule.wait(.2)
            self._embedding_active = True
        try:
            tags = self._request('GET', '/api/tags', cancel=cancel).get('models', [])
            row = next((x for x in tags if x.get('name', x.get('model')) == model), None)
            need(row and row.get('digest') == digest and not row.get('remote_host') and not row.get('remote_model'), 'Embedding model changed or is not local.')
            result = self._request('POST', '/api/embed', dict(model=model, input=texts, truncate=False, keep_alive='5m'), cancel=cancel, timeout=180)
            values = result.get('embeddings')
            need(isinstance(values, list) and len(values) == len(texts) and all(isinstance(v, list) and 1 <= len(v) <= 4096 and all(type(n) in (int, float) and math.isfinite(n) for n in v) for v in values), 'Malformed embedding response.')
            need(len({len(v) for v in values}) == 1, 'Embedding dimensions differ.')
            return dict(embeddings=values, digest=digest, dimensions=len(values[0]))
        finally:
            with self._schedule:
                self._embedding_active = False
                self.lock.release()
                self._schedule.notify_all()

    def _model_policy(self, model, digest, fail_closed=False):
        entry = next((row for row in self._inference_policy if row[0] == model), None)
        if entry is None:
            return None
        matches = entry[1] == digest
        need(matches or not fail_closed, 'Configured inference policy model digest differs; review before inference.')
        return {'sha256': self.inference_policy_sha256, 'status': 'matched' if matches else 'digest_mismatch',
                'expectedDigest': entry[1], 'think': entry[2] if matches else None}

    def _request(self, method, path, body=None, cancel=None, timeout=15):
        connection = http.client.HTTPConnection(*self.address, timeout=min(timeout, 10))
        done = threading.Event()
        expired = threading.Event()
        def watch():
            deadline = time.monotonic() + timeout
            while not done.wait(.1):
                if cancel is not None and cancel.is_set() or time.monotonic() > deadline:
                    expired.set()
                    with contextlib.suppress(OSError):
                        if connection.sock:
                            connection.sock.shutdown(socket.SHUT_RDWR)
                    connection.close()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            if cancel is not None and cancel.is_set():
                raise Cancelled('Inference cancelled.')
            data = None if body is None else json.dumps(body).encode()
            need(data is None or len(data) <= MAX_BODY, 'Inference input exceeds its limit.')
            connection.connect()
            if expired.is_set() or cancel is not None and cancel.is_set():
                raise Cancelled('Inference cancelled or timed out.')
            connection.sock.settimeout(timeout)
            connection.sock = InterruptibleSocket(connection.sock, cancel, timeout)
            connection.request(method, path, data, {'Content-Type': 'application/json', 'Connection': 'close'})
            response = connection.getresponse()
            raw = response.read(MAX_BODY + 1)
            need(len(raw) <= MAX_BODY, 'Runtime response exceeds its limit.')
            need(response.status == 200, 'Ollama request failed (HTTP %s).' % response.status)
            result = json.loads(raw)
            need(isinstance(result, dict), 'Malformed Ollama response.')
            return result
        except (OSError, http.client.HTTPException) as exc:
            if expired.is_set():
                raise Cancelled('Inference cancelled or timed out.') from exc
            raise ValueError('Local Ollama is unavailable.') from exc
        finally:
            done.set()
            connection.close()

    def inventory(self, cancel=None, timeout=None, deadline=None):
        deadline = operation_deadline(timeout, deadline)
        def request(method, path, body=None, maximum=15):
            return self._request(method, path, body, cancel=cancel,
                                 timeout=remaining_budget(deadline, maximum, cancel))
        version = request('GET', '/api/version')
        tags = request('GET', '/api/tags').get('models')
        loaded = request('GET', '/api/ps').get('models')
        need(isinstance(tags, list) and len(tags) <= 256 and isinstance(loaded, list), 'Malformed model inventory.')
        # Cache only when the actual listener lifetime is known. Version alone
        # cannot identify a restarted/replaced runtime process.
        try:
            runtime_session = self.capability_runtime_session(remaining_budget(deadline, 3, cancel))
        except ValueError:
            runtime_session = None
        models = []
        active_keys = set()
        for item in tags:
            need(isinstance(item, dict) and isinstance(item.get('name'), str) and isinstance(item.get('digest'), str), 'Malformed model entry.')
            row = {k: item.get(k) for k in ('name', 'model', 'digest', 'size', 'details', 'modified_at', 'remote_host', 'remote_model')}
            row['capabilities'] = item.get('capabilities', [])
            if not row['capabilities']:
                key = (item['name'], item['digest'], version.get('version'), runtime_session,
                       self.inference_policy_sha256)
                active_keys.add(key)
                try:
                    with self._capability_lock:
                        cached = self._capability_cache.get(key) if runtime_session else None
                    if cached is None:
                        show = request('POST', '/api/show', {'model': item['name']}, maximum=5)
                        cached = show.get('capabilities', [])
                        need(isinstance(cached, list) and all(isinstance(x, str) for x in cached), 'Malformed advertised capabilities.')
                        if runtime_session:
                            with self._capability_lock: self._capability_cache[key] = list(cached)
                    row['capabilities'] = list(cached)
                except Cancelled:
                    raise
                except ValueError:
                    row['capabilities'] = []
            row['capabilityEvidence'] = 'advertised'
            policy = self._model_policy(item['name'], item['digest'])
            if policy is not None:
                row['inferencePolicy'] = policy
            models.append(row)
        with self._capability_lock:
            self._capability_cache = {k:v for k,v in self._capability_cache.items() if k in active_keys}
        if self.inference_policy_sha256 is not None:
            version = dict(version, inferencePolicySha256=self.inference_policy_sha256)
        remaining_budget(deadline, 3, cancel)
        observed_resources = (resources(timeout=remaining_budget(deadline, 3, cancel))
                              if deadline is not None else resources())
        remaining_budget(deadline, 3, cancel)
        return {'protocol': PROTOCOL, 'identity': self.identity, 'observedAt': utc(), 'runtime': version,
                'models': models, 'loaded': loaded, 'resources': observed_resources, 'busy': self.lock.locked(),
                'responseProfiles': list(RESPONSE_PROFILES),
                'workerOperationRecoveryV1': True,
                'inferencePolicySha256': self.inference_policy_sha256}

    def chat(self, model, messages, tools=None, options=None, cancel=None, timeout=180, audio=False,
             response_profile=None, deadline=None):
        need(isinstance(model, str) and 0 < len(model) <= 256 and '://' not in model and not model.endswith('-cloud') and ':cloud' not in model,
             'Select an installed local model.')
        need(isinstance(messages, list) and 0 < len(messages) <= 200, 'Bounded messages are required.')
        need(isinstance(timeout, (int, float)) and 1 <= timeout <= 1800, 'Invalid inference timeout.')
        need(response_profile is None or response_profile in RESPONSE_PROFILES, 'Unknown response profile.')
        if response_profile == 'history_v1':
            need(not audio and not tools, 'History profile permits only native text without tools.')
            need(options == {'num_ctx': 16384, 'num_predict': 4096},
                 'History profile requires its fixed 16384/4096 token budgets.')
        if response_profile == 'routing_v1':
            need(not audio and not tools, 'Routing profile permits only native text without tools.')
            need(options is None or isinstance(options, dict) and set(options) <= {'num_ctx', 'num_predict'} and
                 ('num_predict' not in options or options['num_predict'] == 512),
                 'Routing profile permits only context and its fixed 512-token output budget.')
            if options and 'num_ctx' in options:
                need(type(options['num_ctx']) is int and 2048 <= options['num_ctx'] <= 32768,
                     'Invalid routing context budget.')
        deadline = operation_deadline(timeout, deadline)
        remaining_budget(deadline, timeout, cancel)
        with self._schedule:
            self._interactive_waiters += 1
            try:
                while not self.lock.acquire(False):
                    if not self._embedding_active:
                        raise Busy('This node is already processing an INTERLINKED inference request.')
                    remaining_budget(deadline, timeout, cancel)
                    self._schedule.wait(.1)
            finally:
                self._interactive_waiters -= 1
        try:
            tags = self._request('GET', '/api/tags', cancel=cancel,
                                 timeout=remaining_budget(deadline, 15, cancel)).get('models', [])
            row = next((r for r in tags if r.get('name') == model), None)
            need(row is not None and not row.get('remote_host') and not row.get('remote_model'), 'Model is not installed locally.')
            policy = self._model_policy(model, row.get('digest'), fail_closed=True)
            if audio:
                need(policy is None, 'Configured inference policy is supported only by native chat; audio requires separate review.')
                body = {'model': model, 'messages': messages, 'stream': False}
                if tools:
                    body['tools'] = tools
                if options and 'num_predict' in options:
                    body['max_tokens'] = options['num_predict']
                result = self._request('POST', '/v1/chat/completions', body, cancel,
                                       remaining_budget(deadline, timeout, cancel))
                choices = result.get('choices')
                need(isinstance(choices, list) and choices and isinstance(choices[0].get('message'), dict), 'Malformed audio response.')
                return {'message': choices[0]['message'], 'done': True, 'usage': result.get('usage'), 'model': model}
            body = {'model': model, 'messages': messages, 'stream': False, 'options': options or {}, 'keep_alive': '5m', 'truncate': False}
            if response_profile == 'routing_v1':
                body['format'] = copy.deepcopy(ROUTING_SCHEMA)
                body['options'] = dict(options or {}, num_predict=512)
            if policy is not None:
                body['think'] = policy['think']
            if tools:
                body['tools'] = tools
            result = self._request('POST', '/api/chat', body, cancel,
                                   remaining_budget(deadline, timeout, cancel))
            need(isinstance(result.get('message'), dict) and result.get('done') is True, 'Incomplete Ollama chat response.')
            return result
        finally:
            with self._schedule:
                self.lock.release()
                self._schedule.notify_all()

    def capability_runtime_session(self, timeout=3):
        if os.name != 'nt' or self.identity.get('computer') != socket.gethostname().upper(): return None
        return self._runtime_session(timeout=timeout)

    def _runtime_session(self, timeout=8):
        """Bind ownership to the actual local listener process, never just its version."""
        need(os.name == 'nt', 'Runtime process identity is unavailable.')
        executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        port = int(self.address[1])
        need(1 <= port <= 65535, 'Invalid local runtime port.')
        script = ('$ErrorActionPreference="Stop"; $listeners=@(Get-NetTCPConnection -State Listen -LocalPort ' + str(port) +
                  ' | Select-Object -ExpandProperty OwningProcess -Unique); if($listeners.Count -ne 1){throw "Ambiguous runtime listener"}; '
                  '$runtimeProcess=Get-Process -Id $listeners[0]; '
                  '[string]$runtimeProcess.Id+":"+[string]$runtimeProcess.StartTime.ToUniversalTime().Ticks')
        try:
            value = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', script],
                                   capture_output=True, text=True, timeout=min(8,max(.1,timeout)), creationflags=0x08000000, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('Runtime process identity is unavailable.') from exc
        need(re.fullmatch(r'[0-9]+:[0-9]+', value), 'Runtime process identity is unavailable.')
        return value

    def _load_identity(self, model):
        owned = self.load_sessions.get(model)
        need(model in self.owned_models and owned is not None, 'Model has no current INTERLINKED load ownership.')
        tags = self._request('GET', '/api/tags').get('models', [])
        loaded = self._request('GET', '/api/ps').get('models', [])
        installed = next((r for r in tags if r.get('name') == model), None)
        running = next((r for r in loaded if r.get('name', r.get('model')) == model), None)
        current_version = self._request('GET', '/api/version').get('version')
        valid = (installed and running and not installed.get('remote_host') and not installed.get('remote_model') and
                 installed.get('digest') == owned['digest'] and running.get('digest') == owned['digest'] and
                 current_version == owned['runtimeVersion'] and self._runtime_session() == owned['runtimeSession'])
        if not valid:
            self.owned_models.discard(model)
            self.load_sessions.pop(model, None)
            raise ValueError('Model load identity changed; unload ownership was revoked.')
        return dict(owned)

    def load_identity(self, model):
        if not self.lock.acquire(False):
            raise Busy('Inference is active.')
        try:
            return self._load_identity(model)
        finally:
            self.lock.release()

    def load(self, model, cancel=None, timeout=300, expected_digest=None):
        need(isinstance(model, str) and not model.endswith('-cloud') and ':cloud' not in model, 'Select an installed local model.')
        need(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 1 <= timeout <= 1800, 'Invalid model-load timeout.')
        if not self.lock.acquire(False):
            raise Busy('Inference is active.')
        try:
            installed = self._request('GET', '/api/tags').get('models', [])
            actual = next((m for m in installed if m.get('name') == model and not m.get('remote_host') and not m.get('remote_model')), None)
            need(actual and isinstance(actual.get('digest'), str) and actual['digest'], 'Model is not installed locally.')
            need(expected_digest is None or actual['digest'] == expected_digest, 'Model changed before load; refresh and retry explicitly.')
            loaded = self._request('GET', '/api/ps').get('models', [])
            if any(m.get('name') == model for m in loaded):
                owned = False
                if model in self.owned_models:
                    with contextlib.suppress(ValueError):
                        self._load_identity(model)
                        owned = True
                return {'status': 'already_loaded', 'owned': owned}
            runtime_session = self._runtime_session()
            version = self._request('GET', '/api/version').get('version')
            result = self._request('POST', '/api/generate', {'model': model, 'stream': False, 'keep_alive': '5m'}, cancel=cancel, timeout=timeout)
            self.owned_models.add(model)
            self.load_sessions[model] = dict(model=model, digest=actual['digest'], nodeSession=self.node_session,
                                            loadSession=uuid.uuid4().hex, runtimeVersion=version, runtimeSession=runtime_session)
            identity = self._load_identity(model)
            return {'status': 'loaded', 'owned': True, 'runtime': result, 'loadIdentity': identity}
        finally:
            self.lock.release()

    def unload(self, model, confirmed_external_idle=False, expected_identity=None, cancel=None):
        need(model in self.owned_models and confirmed_external_idle is True,
             'Unload requires an INTERLINKED-loaded model and explicit confirmation that other clients are idle.')
        if not self.lock.acquire(False):
            raise Busy('Inference is active.')
        try:
            identity = self._load_identity(model)
            need(expected_identity is None or expected_identity == identity, 'Model unload confirmation identity changed.')
            result = self._request('POST', '/api/generate', {'model': model, 'stream': False, 'keep_alive': 0}, cancel=cancel)
            self.owned_models.discard(model)
            self.load_sessions.pop(model, None)
            return {'status': 'unloaded', 'runtime': result}
        finally:
            self.lock.release()

    def execute(self, action, payload=None, cancel=None, caller_identity=None):
        payload = payload or {}
        if action == 'embed': return self.embed(cancel=cancel, **payload)
        if action == 'history':
            need(self.identity.get('computer') == 'MSI', 'Only MSI owns the authoritative history archive.')
            need(set(payload) == {'action', 'body'}, 'Invalid history operation envelope.')
            with self._history_init_lock:
                if getattr(self, '_history_service', None) is None:
                    name = 'interlinked_history_runtime'
                    if name not in sys.modules:
                        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[name] = module
                        try: spec.loader.exec_module(module)
                        except BaseException:
                            sys.modules.pop(name, None)
                            raise
                    service = sys.modules[name].load_service()
                    self._history_service = service.HistoryNode(Path(os.environ['LOCALAPPDATA']) / 'INTERLINKED' / 'History', self)
            return self._history_service.execute(payload['action'], payload['body'], cancel, caller_identity=caller_identity)
        if action == 'telemetry':
            need(not payload, 'Telemetry accepts no caller options.')
            return self.telemetry()
        if action == 'inventory':
            return self.inventory(cancel=cancel, **payload)
        if action == 'chat':
            return self.chat(cancel=cancel, **payload)
        if action == 'load':
            return self.load(cancel=cancel, **payload)
        if action == 'unload':
            return self.unload(cancel=cancel, **payload)
        if action == 'load_identity':
            return self.load_identity(**payload)
        raise ValueError('Unsupported local node action.')


TELEMETRY_FIELDS = ('cpuTemperature', 'gpuTemperature', 'gpuLoad', 'vramUsed', 'vramTotal')
TELEMETRY_TTL = 5
TELEMETRY_SOURCES = {'nvidia-smi', 'reviewed-sensor-publisher', 'local-sensor-map'}
MSI_SENSOR_FOLDER = Path(r'C:\ProgramData\INTERLINKED\MSI-Sensors')
MSI_SENSOR_PUBLISHER = Path(r'C:\Program Files\INTERLINKED MSI Sensors\INTERLINKED.MSI.Sensor.exe')


def telemetry_metric(value=None, source=None, observed=None):
    return dict(value=value, status='available' if value is not None else 'unavailable',
                source=source, observedAt=observed if value is not None else None)


def clean_telemetry(value, now=None):
    """Allowlisted wire summary; stale fields cannot become current by polling."""
    now = time.time() if now is None else now
    result = dict(gpuId=None, **{name: telemetry_metric() for name in TELEMETRY_FIELDS})
    if not isinstance(value, dict):
        return result
    gpu_id = value.get('gpuId')
    if isinstance(gpu_id, str) and re.fullmatch(r'GPU-[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', gpu_id):
        result['gpuId'] = gpu_id
    for name in TELEMETRY_FIELDS:
        item = value.get(name)
        if not isinstance(item, dict):
            continue
        number, observed = item.get('value'), item.get('observedAt')
        try:
            parsed = dt.datetime.fromisoformat(observed.replace('Z', '+00:00'))
            if parsed.tzinfo is None: continue
            age = now - parsed.timestamp()
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        maximum = 150 if name.endswith('Temperature') else 100 if name == 'gpuLoad' else 2 ** 60
        valid = (type(number) in (int, float) and math.isfinite(number) and 0 <= number <= maximum
                 and item.get('status') in ('available', 'live', 'verified') and 0 <= age <= TELEMETRY_TTL
                 and item.get('source') in TELEMETRY_SOURCES)
        if name.startswith('vram'):
            valid = valid and type(number) is int
        if name != 'cpuTemperature' and result['gpuId'] is None:
            valid = False
        if valid:
            result[name] = telemetry_metric(number, item['source'], observed)
    used, total = result['vramUsed']['value'], result['vramTotal']['value']
    if used is not None and total is not None and (not total or used > total):
        result['vramUsed'], result['vramTotal'] = telemetry_metric(), telemetry_metric()
    return result


def receive_telemetry(value, http_date, request_started):
    """Project authenticated server observations conservatively onto our clock.

    HTTP Date has whole-second precision. Its upper bound and our request start
    include both rounding uncertainty and the full request duration in field age.
    All later consumers still apply the unchanged five-second local-clock TTL.
    """
    try:
        if not isinstance(http_date, str): return clean_telemetry(None)
        server_date = email.utils.parsedate_to_datetime(http_date)
        if (server_date.tzinfo is None or server_date.utcoffset() != dt.timedelta(0)
                or email.utils.format_datetime(server_date, usegmt=True) != http_date):
            return clean_telemetry(None)
        server_upper = server_date.timestamp() + 1
        result = clean_telemetry(value, now=server_upper)
        for name in TELEMETRY_FIELDS:
            item = result[name]
            if item['status'] == 'available':
                source_time = dt.datetime.fromisoformat(item['observedAt'].replace('Z', '+00:00')).timestamp()
                item['observedAt'] = dt.datetime.fromtimestamp(
                    request_started - (server_upper - source_time), dt.timezone.utc).isoformat()
        return result
    except (TypeError, ValueError, OverflowError, OSError):
        return clean_telemetry(None)


def protected_sensor_path(path, protected=False):
    """Only Admin/SYSTEM may write the fixed sensor publication family."""
    ordinary(path)
    need(os.name == 'nt', 'Verified Windows sensor permissions are unavailable.')
    from ctypes import wintypes
    advapi, kernel = ctypes.WinDLL('advapi32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    get = advapi.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get.restype = wintypes.DWORD
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor, encoded = ctypes.c_void_p(), wintypes.LPWSTR()
    try:
        need(get(str(path), 1, 5, None, None, None, None, ctypes.byref(descriptor)) == 0,
             'Sensor permissions cannot be verified.')
        need(convert(descriptor, 1, 5, ctypes.byref(encoded), None), 'Sensor permissions cannot be decoded.')
        sddl = encoded.value
        owner = re.search(r'O:([^:]+?)(?=[GDS]:|$)', sddl)
        need(owner and owner.group(1) in ('BA', 'SY'), 'Sensor file has an untrusted owner.')
        if protected:
            need(re.search(r'D:[A-Z]*P', sddl), 'Sensor directory permissions are not protected.')
        aces = re.findall(r'\(([^()]*)\)', sddl)
        need(aces, 'Sensor publication has no restrictive permissions.')
        for ace in aces:
            kind, _, rights, _, _, sid = ace.split(';')
            need(kind in ('A', 'D'), 'Unexpected sensor permission entry.')
            if kind == 'D' or sid in ('BA', 'SY'):
                continue
            if rights.startswith('0x'):
                writable = int(rights, 16) & (0x40000000 | 0x10000000 | 2 | 4 | 16 | 64 | 256 | 65536 | 262144 | 524288)
            else:
                writable = any(code not in ('FR', 'FX', 'GR', 'GX', 'RC') for code in re.findall('..', rights))
            need(not writable, 'Sensor publication is writable by an untrusted account.')
    finally:
        if encoded: kernel.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))
        if descriptor: kernel.LocalFree(descriptor)


def publisher_temperatures(identity):
    """Read the MSI-only reviewed publisher. No helper or elevation is started."""
    if identity.get('computer') != 'MSI' or os.name != 'nt':
        return {}
    folder, publisher = MSI_SENSOR_FOLDER, MSI_SENSOR_PUBLISHER
    output = folder / 'temperatures.json'
    descriptor_path = folder / 'descriptor.json'
    try:
        for directory in (folder.parent, folder, publisher.parent): protected_sensor_path(directory, True)
        for path in (descriptor_path, output, publisher): protected_sensor_path(path)
        def bounded(path):
            with ordinary(path).open('rb') as stream: data = stream.read(8193)
            need(len(data) <= 8192, 'Sensor publication exceeds its bound.')
            return json.loads(data)
        descriptor = bounded(descriptor_path)
        need(descriptor.get('format') == 'interlinked-msi-sensor-descriptor-v1'
             and descriptor.get('machine') == 'MSI' and descriptor.get('userSid') == identity.get('sid')
             and descriptor.get('publisherPath') == str(publisher) and descriptor.get('outputPath') == str(output),
             'Sensor publisher identity differs.')
        expected = descriptor.get('publisherSha256')
        need(isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected), 'Sensor publisher pin is missing.')
        with publisher.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        need(hmac.compare_digest(expected, actual), 'Sensor publisher bytes differ.')
        value = bounded(output)
        need(value.get('format') == 'interlinked-sensor-telemetry-v1' and value.get('machine') == 'MSI'
             and value.get('publisherSha256') == expected, 'Sensor observation identity differs.')
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def sample_telemetry(identity):
    result = {name: telemetry_metric() for name in TELEMETRY_FIELDS}
    result['gpuId'] = None
    if os.name == 'nt':
        executable = Path(os.environ['SystemRoot']) / 'System32/nvidia-smi.exe'
        if executable.is_file():
            try:
                # GPU zero is explicit and identified by UUID; never combine devices.
                query = 'uuid,memory.total,memory.used,utilization.gpu,temperature.gpu'
                rows = subprocess.run([str(executable), '--id=0', '--query-gpu=' + query,
                    '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=1.5,
                    creationflags=0x08000000, check=True).stdout.splitlines()
                need(len(rows) == 1, 'Primary GPU telemetry is ambiguous.')
                gpu_id, total, used, load, temperature = [item.strip() for item in rows[0].split(',')]
                result['gpuId'] = gpu_id
                observed = utc()
                for name, raw, scale in [('vramTotal', total, 1048576), ('vramUsed', used, 1048576),
                                         ('gpuLoad', load, 1), ('gpuTemperature', temperature, 1)]:
                    try: result[name] = telemetry_metric((int(raw) if name.startswith('vram') else float(raw)) * scale, 'nvidia-smi', observed)
                    except ValueError: pass
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    temperatures = publisher_temperatures(identity)
    result['cpuTemperature'] = temperatures.get('cpuTemperature') or telemetry_metric()
    if result['gpuTemperature']['value'] is None and temperatures.get('gpuId') == result['gpuId']:
        result['gpuTemperature'] = temperatures.get('gpuTemperature') or telemetry_metric()
    return clean_telemetry(result)


class TelemetrySampler:
    """One bounded sampler thread; RPC/preview reads only its immutable cache."""
    def __init__(self, identity, collector=None, start=True):
        self.identity, self.collector = dict(identity), collector or sample_telemetry
        self.lock, self.closed = threading.Lock(), threading.Event()
        self.value, self.thread = clean_telemetry(None), None
        if start:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            started = time.monotonic()
            try: value = clean_telemetry(self.collector(self.identity))
            except Exception: value = clean_telemetry(None)
            with self.lock: self.value = value
            if self.closed.wait(max(0, 2 - (time.monotonic() - started))): break

    def snapshot(self):
        with self.lock: return clean_telemetry(copy.deepcopy(self.value))

    def close(self):
        self.closed.set()
        if self.thread is not None: self.thread.join(2)


def resources(timeout=3):
    result = {'observedAt': utc(), 'memory': None, 'gpus': [], 'status': 'partial'}
    if os.name != 'nt':
        return result
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                 ('totalPhysical', 'availablePhysical', 'totalPage', 'availablePage', 'totalVirtual', 'availableVirtual', 'extended')]
    mem = Memory()
    mem.length = ctypes.sizeof(mem)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
        result['memory'] = {'total': mem.totalPhysical, 'available': mem.availablePhysical, 'loadPercent': mem.load}
    executable = Path(os.environ['SystemRoot']) / 'System32/nvidia-smi.exe'
    if executable.is_file():
        try:
            rows = subprocess.run([str(executable), '--query-gpu=name,memory.total,memory.used,utilization.gpu', '--format=csv,noheader,nounits'],
                                  capture_output=True, text=True, timeout=timeout, creationflags=0x08000000, check=True).stdout.splitlines()
            for line in rows[:8]:
                name, total, used, utilization = [x.strip() for x in line.split(',')]
                result['gpus'].append({'name': name, 'totalBytes': int(total) * 1048576, 'usedBytes': int(used) * 1048576,
                                       'utilizationPercent': int(utilization)})
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return result


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, fingerprint, timeout=15):
        need(isinstance(fingerprint, str) and re.fullmatch('[a-fA-F0-9]{64}', fingerprint), 'A SHA256 certificate fingerprint is required.')
        self.fingerprint = fingerprint.lower()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Exact certificate pin is checked before sending HTTP bytes.
        super().__init__(host, port, timeout=timeout, context=context)

    def connect(self):
        super().connect()
        if not hmac.compare_digest(hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest(), self.fingerprint):
            self.close()
            raise ValueError('AI node certificate differs from the paired fingerprint.')


def remote_request(endpoint, fingerprint, path, body=None, token=None, timeout=30, cancel=None):
    if TRANSPORT is not None:
        return external_transport(dict(endpoint=endpoint, fingerprint=fingerprint, path=path, body=body, token=token, timeout=timeout), timeout, cancel)
    request_started = time.time()
    url = urlsplit(endpoint)
    need(url.scheme == 'https' and url.hostname and not url.username and not url.password and url.path in ('', '/') and not url.query and not url.fragment,
         'An HTTPS node endpoint without a path is required.')
    connection = PinnedConnection(url.hostname, url.port or 47443, fingerprint, min(timeout, 10))
    done = threading.Event()
    def watch():
        while not done.wait(.1):
            if cancel is not None and cancel.is_set():
                with contextlib.suppress(OSError):
                    if connection.sock:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                connection.close()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        headers = {'Content-Type': 'application/json', 'Connection': 'close'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.connect()
        if cancel is not None and cancel.is_set():
            raise Cancelled('Node request cancelled.')
        connection.sock.settimeout(timeout)
        connection.sock = InterruptibleSocket(connection.sock, cancel, timeout)
        connection.request('POST', path, json.dumps(body or {}).encode(), headers)
        response = connection.getresponse()
        data = response.read(MAX_BODY + 1)
        need(len(data) <= MAX_BODY, 'Node response exceeds its limit.')
        result = json.loads(data)
        if response.status != 200:
            raise ValueError(str(result.get('error', 'Node request failed.'))[:500])
        if path == '/node/v1/rpc' and isinstance(body, dict) and body.get('action') == 'telemetry':
            result['result'] = receive_telemetry(result.get('result'), response.getheader('Date'), request_started)
        return result
    except (OSError, http.client.HTTPException) as exc:
        if cancel is not None and cancel.is_set():
            raise Cancelled('Node request cancelled.') from exc
        raise ValueError('AI node connection is unavailable.') from exc
    finally:
        done.set()
        connection.close()


def pair_remote(endpoint, fingerprint, code, expected_identity):
    result = remote_request(endpoint, fingerprint, '/node/v1/pair', {'code': code, 'expectedIdentity': expected_identity})
    need(result.get('protocol') == PROTOCOL and result.get('identity') == expected_identity, 'Paired node identity differs.')
    return dict(endpoint=endpoint, fingerprint=fingerprint.lower(), identity=result['identity'], token=result['token'])


class RemoteNode:
    def __init__(self, endpoint, fingerprint, identity, token):
        self.endpoint, self.fingerprint, self.identity, self.token = endpoint, fingerprint, identity, token
        self.worker_operation_recovery = False

    def execute(self, action, payload=None, cancel=None, timeout=None, deadline=None, request_id=None):
        need(action in ACTIONS, 'Unsupported node action.')
        request_id = request_id or uuid.uuid4().hex
        need(isinstance(request_id, str) and ID.fullmatch(request_id), 'Invalid request identity.')
        deadline = operation_deadline(timeout, deadline)
        done = threading.Event()
        def watch():
            while not done.wait(.2):
                if cancel is not None and cancel.is_set():
                    with contextlib.suppress(Exception):
                        remote_request(self.endpoint, self.fingerprint, '/node/v1/cancel', {'requestId': request_id}, self.token)
                    return
        threading.Thread(target=watch, daemon=True).start()
        try:
            if cancel is not None and cancel.is_set():
                raise Cancelled('Request cancelled.')
            value = remote_request(self.endpoint, self.fingerprint, '/node/v1/rpc',
                                   {'protocol': PROTOCOL, 'requestId': request_id, 'identity': self.identity, 'action': action, 'payload': payload or {}},
                                   self.token, timeout=remaining_budget(deadline, 1810, cancel), cancel=cancel)
            need(value.get('identity') == self.identity and value.get('protocol') == PROTOCOL and
                 value.get('requestId') == request_id, 'Node response identity differs.')
            return value['result']
        finally:
            done.set()

    def inventory(self, cancel=None, timeout=None, deadline=None):
        deadline = operation_deadline(timeout, deadline)
        payload = {} if deadline is None else {'timeout': remaining_budget(deadline, 1800, cancel)}
        result = self.execute('inventory', payload, cancel, deadline=deadline)
        self.worker_operation_recovery = result.get('workerOperationRecoveryV1') is True
        return result

    def embed(self, model, texts, digest, cancel=None, background=False):
        return self.execute('embed', dict(model=model, texts=texts, digest=digest, background=background), cancel=cancel, timeout=180)

    def telemetry(self):
        request_id = uuid.uuid4().hex
        value = remote_request(self.endpoint, self.fingerprint, '/node/v1/rpc',
            dict(protocol=PROTOCOL, requestId=request_id, identity=self.identity, action='telemetry', payload={}),
            self.token, timeout=2)
        need(value.get('protocol') == PROTOCOL and value.get('identity') == self.identity
             and value.get('requestId') == request_id, 'Telemetry response identity differs.')
        return clean_telemetry(value.get('result'))

    def chat(self, model, messages, tools=None, options=None, cancel=None, timeout=180, audio=False,
             response_profile=None, deadline=None, request_id=None):
        need(response_profile is None or response_profile in RESPONSE_PROFILES, 'Unknown response profile.')
        payload = dict(model=model, messages=messages, tools=tools, options=options, timeout=timeout, audio=audio)
        if response_profile is not None:
            payload['response_profile'] = response_profile
        if deadline is not None:
            deadline = operation_deadline(timeout, deadline)
            payload['timeout'] = remaining_budget(deadline, timeout, cancel)
            need(payload['timeout'] >= 1, 'Operation deadline expired.')
        return self.execute('chat', payload, cancel, deadline=deadline, request_id=request_id)

    def load(self, model, cancel=None, timeout=300, expected_digest=None):
        return self.execute('load', {'model': model, 'timeout': timeout, 'expected_digest': expected_digest}, cancel)

    def unload(self, model, confirmed_external_idle=False, expected_identity=None, cancel=None):
        return self.execute('unload', {'model': model, 'confirmed_external_idle': confirmed_external_idle,
                                     'expected_identity': expected_identity}, cancel)

    def load_identity(self, model):
        return self.execute('load_identity', {'model': model})


class NodeService:
    def __init__(self, local, state_root, protector=protect, worker_dispatch=None):
        self.local, self.identity, self.protector = local, local.identity, protector
        self.root = ordinary(Path(state_root).absolute(), True)
        self.root.mkdir(parents=True, exist_ok=True)
        owner = self.root / 'owner.json'
        expected = {'protocol': PROTOCOL, 'identity': self.identity}
        if owner.exists():
            need(read_json(owner) == expected, 'Node state belongs to a different identity.')
        else:
            need(not any(self.root.iterdir()), 'Unowned nonempty node state is refused.')
            write_json(owner, expected)
        retain_inference_policy_pin(self.root, self.identity, getattr(local, 'inference_policy_sha256', None))
        self.requests = self.root / 'requests'
        self.requests.mkdir(exist_ok=True)
        ordinary(self.requests)
        self.lock = threading.RLock()
        # A separate boundary serializes cancellation tombstones with worker
        # registration. The journal lock is never held across workspace calls.
        self.operation_lock = threading.Lock()
        self.running = {}
        self.code = None
        self.code_deadline = 0
        self.code_attempts = 0
        self.worker_dispatch = worker_dispatch
        self.token_path = self.root / 'credentials.json'
        self.token = load_credentials(self.token_path, protector)['token'] if self.token_path.exists() else None

    def begin_pairing(self):
        with self.lock:
            self.code = secrets.token_hex(8)
            self.code_deadline = time.monotonic() + 300
            self.code_attempts = 0
            return self.code

    def pair(self, body):
        with self.lock:
            need(isinstance(body, dict) and body.get('expectedIdentity') == self.identity, 'Node identity differs.')
            self.code_attempts += 1
            need(self.code and time.monotonic() <= self.code_deadline and self.code_attempts <= 5 and
                 hmac.compare_digest(str(body.get('code', '')), self.code), 'Pairing code is invalid or expired.')
            token = secrets.token_urlsafe(48)
            save_credentials(self.token_path, {'token': token, 'identity': self.identity}, self.protector)
            self.token = token
            self.code = None
            return {'protocol': PROTOCOL, 'identity': self.identity, 'token': token}

    def authenticate(self, header):
        need(self.token is not None and hmac.compare_digest(header or '', 'Bearer ' + self.token), 'Node authentication required.')
        return 'pair:' + hashlib.sha256((header[7:]).encode('utf-8')).hexdigest()

    def revoke(self):
        with self.lock:
            save_credentials(self.token_path, {'token': None, 'identity': self.identity}, self.protector)
            self.token = None
            self.code = None
            for event in self.running.values():
                event.set()
        if self.worker_dispatch is not None and hasattr(self.worker_dispatch, 'close'):
            self.worker_dispatch.close()

    def cancel(self, request_id):
        need(isinstance(request_id, str) and ID.fullmatch(request_id), 'Invalid request identity.')
        with self.lock:
            event = self.running.get(request_id)
            if event:
                event.set()
        return {'cancelRequested': event is not None}

    def rpc(self, body, caller_identity=None):
        need(isinstance(body, dict) and body.get('protocol') == PROTOCOL and body.get('identity') == self.identity, 'Protocol or node identity differs.')
        request_id, action, payload = body.get('requestId'), body.get('action'), body.get('payload')
        need(isinstance(request_id, str) and ID.fullmatch(request_id), 'Invalid request identity.')
        need(action in ACTIONS and isinstance(payload, dict), 'Unsupported RPC operation.')
        if action in OPERATION_ACTIONS:
            result = self.operation(payload, cancel=action == 'operation_cancel')
            return {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
        if action == 'history':
            # Archive reads stay out of the general task journal. Archive writes
            # have content-bound transfer IDs and private operation receipts.
            caller = caller_identity or 'pair:' + hashlib.sha256((self.token or '').encode('utf-8')).hexdigest()
            result = self.local.execute(action, payload, threading.Event(), caller_identity=caller)
            return {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
        if action == 'telemetry':
            need(not payload, 'Telemetry accepts no caller options.')
            result = clean_telemetry(self.local.execute(action, {}, threading.Event()))
            return {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
        if action == 'inventory':
            # Read-only inventory is unjournaled, but bounded preflight must still
            # be cancellable through the existing request cancellation endpoint.
            with self.lock:
                need(request_id not in self.running, 'Request is already running.')
                event = threading.Event()
                self.running[request_id] = event
            try:
                result = self.local.execute(action, payload, event)
                return {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
            finally:
                with self.lock:
                    self.running.pop(request_id, None)
        if action in ('worker_status', 'worker_result'):
            result = self.worker_dispatch(action, payload, threading.Event()) if action in WORKER_ACTIONS and self.worker_dispatch else self.local.execute(action, payload, threading.Event())
            return {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
        fingerprint = hashlib.sha256(json.dumps({'action': action, 'payload': payload}, sort_keys=True).encode()).hexdigest()
        if action in ('worker_prepare', 'worker_start'):
            return self.worker_mutation(request_id, action, payload, fingerprint)
        path = self.requests / (request_id + '.json')
        with self.lock:
            if path.exists():
                record = read_json(path)
                need(record['fingerprint'] == fingerprint, 'Request identity was reused with different content.')
                need(record['status'] == 'completed', 'Request is running or interrupted; uncertain operations are never replayed.')
                return record['response']
            event = threading.Event()
            self.running[request_id] = event
            write_json(path, {'status': 'running', 'fingerprint': fingerprint, 'startedAt': utc()})
        try:
            if action in WORKER_ACTIONS:
                need(self.worker_dispatch is not None, 'Workers are not configured on this node.')
                result = self.worker_dispatch(action, payload, event)
            else:
                result = self.local.execute(action, payload, event)
            response = {'protocol': PROTOCOL, 'identity': self.identity, 'requestId': request_id, 'result': result}
            write_json(path, {'status': 'completed', 'fingerprint': fingerprint, 'response': response, 'finishedAt': utc()})
            return response
        except Exception:
            write_json(path, {'status': 'interrupted', 'fingerprint': fingerprint, 'finishedAt': utc()})
            raise
        finally:
            with self.lock:
                self.running.pop(request_id, None)

    def worker_mutation(self, request_id, action, payload, fingerprint):
        need(self.worker_dispatch is not None, 'Workers are not configured on this node.')
        need(self.operation_lock.acquire(timeout=3), 'Worker registration is busy; reconcile before retrying.')
        path = self.requests / (request_id + '.json')
        try:
            with self.lock:
                if path.exists():
                    record = read_json(path)
                    need(record['fingerprint'] == fingerprint, 'Request identity was reused with different content.')
                    need(not record.get('cancelRequested'), 'Operation was cancelled; dispatch is fenced.')
                    need(record['status'] == 'completed', 'Request is running or interrupted; uncertain operations are never replayed.')
                    return record['response']
                event = threading.Event()
                self.running[request_id] = event
                write_json(path, dict(status='running', action=action, fingerprint=fingerprint, startedAt=utc()))
            try:
                result = self.worker_dispatch(action, payload, event,
                    operation=dict(id=request_id, action=action, fingerprint=fingerprint))
                response = dict(protocol=PROTOCOL, identity=self.identity, requestId=request_id, result=result)
                write_json(path, dict(status='completed', action=action, fingerprint=fingerprint, response=response, finishedAt=utc()))
                return response
            except Exception:
                write_json(path, dict(status='interrupted', action=action, fingerprint=fingerprint, finishedAt=utc()))
                raise
            finally:
                with self.lock: self.running.pop(request_id, None)
        finally:
            self.operation_lock.release()

    def operation(self, payload, cancel=False):
        need(set(payload) == {'targetRpcId', 'expectedAction', 'expectedFingerprint'}, 'Invalid operation query.')
        identifier, action, fingerprint = payload['targetRpcId'], payload['expectedAction'], payload['expectedFingerprint']
        need(isinstance(identifier, str) and ID.fullmatch(identifier) and action in ('worker_prepare', 'worker_start') and
             isinstance(fingerprint, str) and re.fullmatch('[a-f0-9]{64}', fingerprint), 'Invalid operation binding.')
        need(self.worker_dispatch is not None and hasattr(self.worker_dispatch, 'operation_effect'), 'Worker recovery is unavailable.')
        acquired = self.operation_lock.acquire(timeout=3) if cancel else False
        need(not cancel or acquired, 'Worker registration is busy; cancellation is not confirmed.')
        try:
            path = self.requests / (identifier + '.json')
            with self.lock:
                record = read_json(path) if path.exists() else None
                if record is not None:
                    need(record.get('fingerprint') == fingerprint and record.get('action', action) == action, 'Operation binding differs.')
                if cancel:
                    record = dict(record or {}, action=action, fingerprint=fingerprint, cancelRequested=True,
                                  status=(record or {}).get('status', 'cancelled'), updatedAt=utc())
                    write_json(path, record)
                    if identifier in self.running: self.running[identifier].set()
            effect = self.worker_dispatch.operation_effect(identifier, action, fingerprint)
            if cancel and effect and effect.get('taskId'):
                self.worker_dispatch.workspaces.cancel(effect['taskId'])
                effect = self.worker_dispatch.operation_effect(identifier, action, fingerprint)
            cancelling = record is not None and record.get('cancelRequested')
            active = effect and effect.get('status') in {'QUEUED','PREPARING','RUNNING','REVIEWING','APPLYING','RECOVERY_REQUIRED'}
            status = ('cancel_pending' if active else 'cancelled') if cancelling else 'committed' if effect else (
                'not_seen' if record is None else 'in_progress' if identifier in self.running else 'interrupted')
            return dict(status=status, effect=effect, dispatchFenced=bool(cancelling))
        finally:
            if acquired: self.operation_lock.release()


class NodeHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            need(self.headers.get('Origin') is None, 'Browser-origin node requests are refused.')
            need(not self.headers.get('Transfer-Encoding'), 'Chunked input is refused.')
            service = self.server.service
            caller_identity = None
            if self.path != '/node/v1/pair':
                caller_identity = service.authenticate(self.headers.get('Authorization'))
            size = int(self.headers.get('Content-Length', '-1'))
            need(0 <= size <= (4096 if self.path == '/node/v1/pair' else MAX_BODY), 'Invalid node request length.')
            self.connection.settimeout(30)
            body = json.loads(self.rfile.read(size))
            if self.path == '/node/v1/pair':
                value = service.pair(body)
            else:
                if self.path == '/node/v1/rpc':
                    value = service.rpc(body, caller_identity=caller_identity)
                elif self.path == '/node/v1/cancel':
                    value = service.cancel(body.get('requestId'))
                else:
                    raise ValueError('Unknown node route.')
            status = 200
        except Exception as exc:
            status, value = 400, {'error': str(exc)[:500]}
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(raw)


class WorkerDispatcher:
    """Remote execution with coordinator-routed inference over a pull mailbox.

Only node-local reviewed check profiles can be attached to an imported source.
An incoming bundle or task can never register a command on this machine.
"""
    def __init__(self, workspaces, check_profiles=None):
        self.workspaces = workspaces
        self.check_profiles = check_profiles or {}
        self.lock = threading.RLock()
        self.pending = {}
        self.groups = {}

    def __call__(self, action, payload, cancel, operation=None):
        if action == 'worker_prepare':
            result = self.workspaces.import_source(payload, operation=operation)
            for profile in self.check_profiles.get(payload.get('projectId'), []):
                self.workspaces.register_check(result['projectId'], profile['id'], profile['argv'], profile.get('timeoutSeconds', 120))
            return result
        if action == 'worker_start':
            group = uuid.uuid4().hex
            def infer(model, messages, tools, cancellation):
                request_id = uuid.uuid4().hex
                ready = threading.Event()
                item = {'id': request_id, 'group': group, 'model': model, 'messages': messages, 'tools': tools,
                        'ready': ready, 'response': None, 'error': None}
                with self.lock:
                    self.pending[request_id] = item
                deadline = time.monotonic() + min(payload.get('timeoutSeconds', 600), 3600)
                try:
                    while not ready.wait(.2):
                        if cancellation.is_set() or time.monotonic() > deadline:
                            raise Cancelled('Remote inference was cancelled or its coordinator stopped responding.')
                    need(not item['error'], item['error'] or 'Remote inference failed.')
                    return item['response']
                finally:
                    with self.lock:
                        self.pending.pop(request_id, None)
            task = self.workspaces.start(payload, infer, reviewer=payload.get('reviewer'), operation=operation)
            with self.lock:
                self.groups.setdefault(task['id'], group)
            return task
        if action == 'worker_status':
            task = self.workspaces.task(payload['taskId'])
            with self.lock:
                group = self.groups.get(task['id'])
                pending = [{k: v for k, v in item.items() if k in ('id', 'model', 'messages', 'tools')}
                           for item in self.pending.values() if group is not None and item['group'] == group]
            return {'task': task, 'pendingInference': pending}
        if action == 'worker_infer_result':
            with self.lock:
                item = self.pending.get(payload.get('id'))
                need(item is not None and not item['ready'].is_set(), 'Inference request is absent or already answered.')
                need(('response' in payload) != ('error' in payload), 'Supply one inference result or error.')
                item['response'], item['error'] = payload.get('response'), payload.get('error')
                item['ready'].set()
            return {'accepted': True}
        if action == 'worker_result':
            return self.workspaces.export_result(payload['taskId'])
        if action == 'worker_cancel':
            return self.workspaces.cancel(payload['taskId'])
        raise ValueError('Unknown remote worker operation.')

    def operation_effect(self, identifier, action, fingerprint):
        return self.workspaces.operation_effect(identifier, action, fingerprint)

    def close(self):
        self.workspaces.close()


def load_worker_dispatcher(state_root, check_profiles=None):
    source = ordinary(Path(__file__).absolute().with_name('interlinked_ai_workspaces.py'))
    spec = importlib.util.spec_from_file_location('interlinked_ai_workspaces', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return WorkerDispatcher(module.Workspaces(Path(state_root) / 'workspaces'), check_profiles)


def make_server(service, address, certfile, keyfile):
    server = ThreadingHTTPServer(address, NodeHandler)
    server.daemon_threads = True
    server.service = service
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(ordinary(certfile)), str(ordinary(keyfile)))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception:
        server.server_close()
        raise
    return server


def main():
    if sys.argv[1:] == ['--transport-once']:
        try:
            packet = sys.stdin.buffer.read(MAX_BODY + 1)
            need(len(packet) <= MAX_BODY, 'TLS request exceeds its limit.')
            payload = json.loads(packet)
            need(payload.get('path') in ('/node/v1/pair', '/node/v1/rpc', '/node/v1/cancel'), 'Unsupported TLS route.')
            result = remote_request(**payload)
            value = {'ok': True, 'result': result}
        except Exception as exc:
            value = {'ok': False, 'error': str(exc)[:500]}
        sys.stdout.buffer.write(json.dumps(value).encode())
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=47443)
    parser.add_argument('--certificate', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--pair', action='store_true')
    parser.add_argument('--revoke', action='store_true')
    parser.add_argument('--checks', help='Owned node-local reviewed check profile JSON; never supplied over RPC.')
    parser.add_argument('--inference-policy', help='Owned startup-only model/digest policy JSON.')
    parser.add_argument('--inference-policy-sha256', help='Independently reviewed policy file SHA256.')
    args = parser.parse_args()
    with instance_lock(Path(args.state)):
        run_service(args)


@contextlib.contextmanager
def instance_lock(state):
    path = ordinary(Path(state).absolute().with_name('.ai-node-instance.lock'), True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run_service(args):
    local = LocalNode(args.state, inference_policy=args.inference_policy,
                      inference_policy_sha256=args.inference_policy_sha256)
    if args.revoke:
        need(Path(args.state).is_dir(), 'No existing node state to revoke.')
        service = NodeService(local, args.state)
        service.revoke()
        print('Existing node credentials revoked.')
        local.close()
        return
    checks = read_json(Path(args.checks)) if args.checks else {}
    # Validate TLS material and acquire the listening endpoint before opening or
    # recovering any worker state. A failed launch must not rewrite task state.
    try:
        server = make_server(None, (args.bind, args.port), Path(args.certificate), Path(args.key))
    except Exception:
        local.close()
        raise
    workers = None
    try:
        service = NodeService(local, args.state)
        workers = load_worker_dispatcher(args.state, checks)
        service.worker_dispatch = workers
        server.service = service
        if args.pair:
            cert = ssl.PEM_cert_to_DER_cert(ordinary(Path(args.certificate)).read_text())
            print(json.dumps({'identity': service.identity, 'fingerprint': hashlib.sha256(cert).hexdigest(),
                              'pairingCode': service.begin_pairing(), 'expiresInSeconds': 300}), flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        if workers is not None:
            workers.close()
        local.close()


if __name__ == '__main__':
    main()
