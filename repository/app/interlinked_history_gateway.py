"""Authenticated loopback Chat Completions adapter for explicitly local clients.

This listener owns no model runtime. It delegates through the existing controller,
has separate client credentials, and never forwards requests to arbitrary URLs.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import threading
import time
import uuid

import interlinked_configuration as files


FORMAT = 'interlinked-history-gateway-v1'
MAX_BODY = 256 * 1024
MAX_REPLY = 512 * 1024
ID = re.compile(r'^[a-f0-9]{32}$')
HASH = re.compile(r'^[a-f0-9]{64}$')


class GatewayError(ValueError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def require(value, message, code='invalid_request', status=400):
    if not value:
        raise GatewayError(status, code, message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def safe_model(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and
            not any(ord(c) < 32 for c in value) and '://' not in value and
            not value.endswith('-cloud') and ':cloud' not in value)


def error_value(code, message):
    return {'error': {'message': message, 'type': 'invalid_request_error' if code == 'invalid_request' else 'gateway_error', 'code': code}}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class HistoryGateway:
    def __init__(self, controller, root, port=47444):
        require(type(port) is int and 0 <= port <= 65535, 'Invalid loopback port.')
        self.controller, self.root, self.port = controller, files.ordinary(Path(root).absolute(), missing=True), port
        self.root.mkdir(parents=True, exist_ok=True)
        files.ordinary(self.root)
        self.lock, self.capacity = threading.RLock(), threading.BoundedSemaphore(8)
        self.server = self.thread = None
        self.closed = False
        self.error = None
        self.requests = {}
        self._sid = None
        if os.name == 'nt':
            import importlib.util
            import sys
            if 'interlinked_ai_ipc' not in sys.modules:
                spec = importlib.util.spec_from_file_location('interlinked_ai_ipc', Path(__file__).with_name('interlinked_ai_ipc.py'))
                module = importlib.util.module_from_spec(spec)
                sys.modules['interlinked_ai_ipc'] = module
                try: spec.loader.exec_module(module)
                except BaseException:
                    sys.modules.pop('interlinked_ai_ipc', None)
                    raise
            ipc = sys.modules['interlinked_ai_ipc']
            self._ipc = ipc
            self._sid = ipc.process_identity()['sid']
        self.path = self.root / 'clients.json'
        owner = {'computer': socket.gethostname().upper(), 'account': self._sid or str(os.getuid()), 'root': str(self.root)}
        if self.path.exists():
            self._verify_private(self.root)
            self._verify_private(self.path)
            state = json.loads(files.read_bytes(self.path))
            require(isinstance(state, dict) and set(state) == {'format', 'owner', 'clients'} and
                    state['format'] == FORMAT and state['owner'] == owner, 'Gateway state ownership differs.', 'invalid_state', 409)
            clients = state['clients']
            require(isinstance(clients, dict) and len(clients) <= 128, 'Invalid client registry.', 'invalid_state', 409)
            for cid, row in clients.items():
                require(ID.fullmatch(cid) and isinstance(row, dict) and
                        set(row) == {'id', 'label', 'keyHash', 'historyEnabled', 'project', 'createdAt', 'revoked'} and
                        row['id'] == cid and isinstance(row['keyHash'], str) and HASH.fullmatch(row['keyHash']) and
                        isinstance(row['label'], str) and 1 <= len(row['label']) <= 100 and
                        isinstance(row['project'], str) and len(row['project']) <= 200 and
                        type(row['historyEnabled']) is bool and type(row['revoked']) is bool,
                        'Invalid client registry.', 'invalid_state', 409)
            self.state = state
        else:
            require(not any(self.root.iterdir()), 'Gateway state directory is not empty.', 'invalid_state', 409)
            if os.name == 'nt':
                self._ipc.protect_file(self.root, self._sid)
            else:
                os.chmod(self.root, 0o700)
            self.state = {'format': FORMAT, 'owner': owner, 'clients': {}}
            self._save()
        if any(not row['revoked'] for row in self.state['clients'].values()):
            try:
                self.start()
            except GatewayError:
                pass  # Visible status, not loss of the retained registry.

    def _verify_private(self, path):
        if os.name == 'nt':
            self._ipc.verify_file(path, self._sid)
        else:
            st = path.stat()
            require(st.st_uid == os.getuid() and not st.st_mode & 0o077, 'Gateway state permissions differ.', 'invalid_state', 409)

    def _save(self):
        files.atomic(self.path, encoded(self.state))
        if os.name == 'nt':
            self._ipc.protect_file(self.path, self._sid)
        else:
            os.chmod(self.path, 0o600)
        self._verify_private(self.path)

    def start(self):
        with self.lock:
            require(not self.closed, 'Gateway is closed.', 'gateway_unavailable', 503)
            if self.server:
                return
            try:
                server = _Server(('127.0.0.1', self.port), _Handler)
            except OSError:
                self.error = 'The local client gateway port is unavailable. Check the application using port ' + str(self.port) + '.'
                raise GatewayError(503, 'gateway_unavailable', self.error) from None
            server.gateway = self
            self.server, self.port, self.error = server, server.server_port, None
            self.thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True, name='history-gateway')
            self.thread.start()

    def create(self, label, historyEnabled=True, project=''):
        require(isinstance(label, str) and 1 <= len(label.strip()) <= 100 and not any(ord(c) < 32 for c in label), 'Enter a client label of 1–100 characters.')
        require(type(historyEnabled) is bool and isinstance(project, str) and len(project) <= 200 and not any(ord(c) < 32 for c in project), 'Invalid client history profile.')
        with self.lock:
            require(len(self.state['clients']) < 128, 'The client registry is full.', 'client_limit', 409)
            self.start()
            cid, key = uuid.uuid4().hex, 'ilh_' + secrets.token_urlsafe(32)
            self.state['clients'][cid] = {'id': cid, 'label': label.strip(), 'keyHash': hashlib.sha256(key.encode()).hexdigest(),
                'historyEnabled': historyEnabled, 'project': project.strip(), 'createdAt': time.time(), 'revoked': False}
            self._save()
        return {'id': cid, 'key': key}

    def revoke(self, cid):
        with self.lock:
            require(isinstance(cid, str) and cid in self.state['clients'], 'Unknown client.', 'unknown_client', 404)
            self.state['clients'][cid]['revoked'] = True
            self._save()
            for owner, cancel in self.requests.values():
                if owner == cid:
                    cancel.set()
        return {'id': cid, 'revoked': True}

    def status(self):
        with self.lock:
            return {'available': not self.closed and self.error is None, 'running': self.server is not None,
                    'endpoint': 'http://127.0.0.1:' + str(self.port) + '/v1', 'error': self.error,
                    'clients': [{k: row[k] for k in ('id', 'label', 'historyEnabled', 'project', 'createdAt', 'revoked')}
                                for row in self.state['clients'].values()]}

    def authenticate(self, bearer):
        require(isinstance(bearer, str) and bearer.startswith('Bearer ') and 20 < len(bearer) < 200,
                'A valid local client key is required.', 'invalid_api_key', 401)
        candidate = hashlib.sha256(bearer[7:].encode()).hexdigest()
        with self.lock:
            for row in self.state['clients'].values():
                if hmac.compare_digest(candidate, row['keyHash']) and not row['revoked']:
                    return copy.deepcopy(row)
        raise GatewayError(401, 'invalid_api_key', 'A valid local client key is required.')

    def models(self):
        rows = self.controller.gateway_models()
        require(isinstance(rows, list) and len(rows) <= 256, 'Local models are unavailable.', 'models_unavailable', 503)
        result = []
        for row in rows:
            if isinstance(row, dict) and safe_model(row.get('id')) and not row.get('remote_host') and not row.get('remote_model'):
                result.append({'id': row['id'], 'object': 'model', 'owned_by': 'interlinked-local'})
        return result

    def close(self):
        with self.lock:
            self.closed = True
            for _, cancel in self.requests.values():
                cancel.set()
            server, thread = self.server, self.thread
            self.server = self.thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'INTERLINKED'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # No prompts, responses, keys, URLs or request headers in HTTP logs.

    def send_error(self, code, message=None, explain=None):
        self._json(code, error_value('invalid_request', 'Unsupported HTTP request.'))

    def _json(self, status, value):
        raw = encoded(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)
        self.wfile.flush()

    def _gate(self):
        require(self.client_address[0] == '127.0.0.1', 'Loopback access required.', 'access_denied', 403)
        require(not self.headers.get_all('Origin') and not self.headers.get_all('Transfer-Encoding'), 'Browser origins and transfer encoding are not supported.', 'access_denied', 403)
        require(len(self.headers) <= 64 and len(self.headers.get_all('Host', [])) == 1 and
                self.headers['Host'] in {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)},
                'Unexpected local host.', 'access_denied', 403)
        require(len(self.headers.get_all('Authorization', [])) == 1, 'A valid local client key is required.', 'invalid_api_key', 401)
        return self.server.gateway.authenticate(self.headers['Authorization'])

    def do_GET(self):
        self.close_connection = True
        try:
            self._gate()
            require(self.path == '/v1/models', 'Unsupported API route.', 'not_found', 404)
            require(not self.headers.get('Content-Length') or self.headers.get('Content-Length') == '0', 'GET request bodies are not supported.')
            self._json(200, {'object': 'list', 'data': self.server.gateway.models()})
        except GatewayError as exc:
            self._json(exc.status, error_value(exc.code, exc.message))
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass
        except Exception:
            self._json(503, error_value('models_unavailable', 'Local models are unavailable. Check INTERLINKED.'))

    def do_POST(self):
        self.close_connection = True
        try:
            client = self._gate()
            require(self.path == '/v1/chat/completions', 'Unsupported API route.', 'not_found', 404)
            require(len(self.headers.get_all('Content-Length', [])) == 1, 'A bounded Content-Length is required.')
            require(self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() == 'application/json', 'Use application/json.')
            try:
                size = int(self.headers['Content-Length'])
            except ValueError:
                raise GatewayError(400, 'invalid_request', 'Invalid Content-Length.') from None
            require(0 < size <= MAX_BODY, 'Request exceeds the 256 KiB limit.', 'request_too_large', 413)
            raw = self.rfile.read(size)
            require(len(raw) == size, 'Incomplete request body.')
            try:
                body = json.loads(raw.decode('utf-8'))
            except (UnicodeError, ValueError):
                raise GatewayError(400, 'invalid_request', 'Malformed JSON request.') from None
            require(isinstance(body, dict) and set(body) <= {'model', 'messages', 'stream', 'max_tokens'} and
                    {'model', 'messages'} <= set(body), 'Only model, messages, stream and max_tokens are supported.')
            require(safe_model(body['model']), 'Choose an installed local model.')
            require(type(body.get('stream', False)) is bool, 'stream must be a boolean.')
            tokens = body.get('max_tokens', 4096)
            require(type(tokens) is int and tokens == 4096, 'This calibrated profile supports max_tokens=4096. Omit max_tokens or use 4096.')
            messages = body['messages']
            require(isinstance(messages, list) and 1 <= len(messages) <= 200, 'Provide 1–200 text messages.')
            require(all(isinstance(m, dict) and set(m) == {'role', 'content'} and isinstance(m['role'], str) and m['role'] in {'system', 'user', 'assistant'} and
                        isinstance(m['content'], str) and '\0' not in m['content'] for m in messages), 'Only plain-text system, user and assistant messages are supported.')
            try:
                encoded(messages)
            except UnicodeError:
                raise GatewayError(400, 'invalid_request', 'Messages must contain valid Unicode text.') from None
            require(body['model'] in {m['id'] for m in self.server.gateway.models()}, 'The selected local model is unavailable.', 'model_not_found', 404)
            self._answer(client, messages, body['model'], body.get('stream', False), tokens)
        except GatewayError as exc:
            self._json(exc.status, error_value(exc.code, exc.message))
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass
        except Exception:
            self._json(503, error_value('local_inference_failed', 'Local answer failed. Check INTERLINKED status.'))

    def do_OPTIONS(self):
        self._json(405, error_value('method_not_allowed', 'Browser preflight and this HTTP method are not supported.'))

    def _disconnected(self):
        readable, _, _ = select.select([self.connection], [], [], 0)
        return bool(readable and not self.connection.recv(1, socket.MSG_PEEK))

    def _answer(self, client, messages, model, stream, tokens):
        gateway = self.server.gateway
        require(gateway.capacity.acquire(False), 'The local request queue is full.', 'queue_full', 429)
        cancel, done = threading.Event(), threading.Event()
        request_id, answer = uuid.uuid4().hex, {}
        with gateway.lock:
            gateway.requests[request_id] = (client['id'], cancel)
            if gateway.closed or gateway.state['clients'][client['id']]['revoked']:
                cancel.set()
        profile = {'historyEnabled': client['historyEnabled'], 'project': client['project']}
        def run():
            try:
                if cancel.is_set():
                    return
                answer['result'] = gateway.controller.gateway_answer(messages, model, profile, cancel)
            except Exception:
                answer['error'] = True
            finally:
                with gateway.lock:
                    gateway.requests.pop(request_id, None)
                gateway.capacity.release()
                done.set()
        worker = threading.Thread(target=run, daemon=True, name='history-client-answer')
        stream_started = worker_started = False
        try:
            if stream:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                stream_started = True
                self.wfile.write(b': waiting for local completion\n\n')
                self.wfile.flush()
            worker.start()
            worker_started = True
            deadline, keepalive = time.monotonic() + 1800, time.monotonic()
            while not done.wait(0.1):
                if cancel.is_set() or gateway.closed or self._disconnected():
                    cancel.set()
                    return
                if time.monotonic() > deadline:
                    cancel.set()
                    raise GatewayError(504, 'request_timeout', 'The local answer timed out.')
                if stream and time.monotonic() - keepalive >= 1:
                    self.wfile.write(b': waiting for local completion\n\n')
                    self.wfile.flush()
                    keepalive = time.monotonic()
            if cancel.is_set():
                return
            require(not answer.get('error'), 'Local answer failed. Check INTERLINKED status.', 'local_inference_failed', 503)
            result = answer.get('result')
            require(isinstance(result, dict) and isinstance(result.get('content'), str) and len(result['content'].encode('utf-8')) <= MAX_REPLY,
                    'Local answer was unavailable or exceeded its limit.', 'invalid_local_response', 503)
            content, sources = result['content'], []
            raw_sources = result.get('sources') or []
            require(isinstance(raw_sources, list) and len(raw_sources) <= 20, 'Local source references are invalid.', 'invalid_local_response', 503)
            for source in raw_sources:
                if not isinstance(source, dict):
                    continue
                item = {k: str(source[k])[:500] for k in ('id', 'title', 'kind', 'timestamp') if source.get(k) is not None}
                if item:
                    sources.append(item)
            if sources:
                labels = ['[' + str(i) + '] ' + ' · '.join(' '.join(str(s.get(k, '')).split()) for k in ('title', 'kind', 'timestamp') if s.get(k)) for i, s in enumerate(sources, 1)]
                content += '\n\nSources:\n' + '\n'.join(labels)
            completion_id = 'chatcmpl-' + request_id
            base = {'id': completion_id, 'created': int(time.time()), 'model': model}
            if stream:
                for value in [dict(base, object='chat.completion.chunk', choices=[{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}], sources=sources),
                              dict(base, object='chat.completion.chunk', choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}])]:
                    self.wfile.write(b'data: ' + encoded(value) + b'\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            else:
                self._json(200, dict(base, object='chat.completion', choices=[{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}], sources=sources))
        except GatewayError as exc:
            if stream_started:
                self.wfile.write(b'data: ' + encoded(error_value(exc.code, exc.message)) + b'\n\ndata: [DONE]\n\n')
                self.wfile.flush()
            else:
                raise
        finally:
            cancel.set()
            if not worker_started:
                with gateway.lock:
                    gateway.requests.pop(request_id, None)
                gateway.capacity.release()
