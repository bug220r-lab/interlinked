"""Bounded, same-user Windows named-pipe transport for the AI coordinator.

Message-mode overlapped I/O, explicit user-only DACLs and remote-client refusal.
No socket listener, Python object deserialization, GUI capability or service start.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

FORMAT = 'interlinked-ai-ipc-v1'
MAX_MESSAGE = 2 * 1024 * 1024
TIMEOUT = 10.0
PREFIX = '\\\\.\\pipe\\INTERLINKED-AI-'


class IPCError(RuntimeError):
    """A public error code; never contains private paths or payloads."""


def need(condition, code='invalid_request'):
    if not condition:
        raise IPCError(code)


def ordinary(path, missing=False):
    path = Path(path)
    need(path.is_absolute() and not str(path).startswith('\\\\'), 'invalid_endpoint')
    for item in reversed((path, *path.parents)):
        try:
            st = item.lstat()
        except FileNotFoundError:
            need(missing, 'coordinator_unavailable')
            continue
        need(not item.is_symlink() and not getattr(st, 'st_file_attributes', 0) & 0x441400, 'invalid_endpoint')
        need(not item.is_file() or st.st_nlink == 1, 'invalid_endpoint')
    return path


def win():
    need(os.name == 'nt', 'windows_required')
    import _winapi
    return _winapi


def libraries():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    a = ctypes.WinDLL('advapi32', use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; k.OpenProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    k.DisconnectNamedPipe.argtypes = [W.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
    a.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    a.SetFileSecurityW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.c_void_p]
    a.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, ctypes.c_int, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(W.LPWSTR), ctypes.c_void_p]
    for name in ('GetNamedPipeServerProcessId', 'GetNamedPipeClientProcessId'):
        getattr(k, name).argtypes = [W.HANDLE, ctypes.POINTER(W.ULONG)]
    return k, a


def sid_string(a, k, pointer):
    value = W.LPWSTR()
    need(a.ConvertSidToStringSidW(pointer, ctypes.byref(value)), 'identity_unavailable')
    try:
        return value.value
    finally:
        k.LocalFree(value)


def process_identity(pid=None):
    win()
    k, a = libraries()
    handle = k.OpenProcess(0x1000, False, pid or os.getpid())
    need(handle, 'identity_unavailable')
    token = W.HANDLE()
    try:
        need(a.OpenProcessToken(handle, 8, ctypes.byref(token)), 'identity_unavailable')
        size = W.DWORD()
        a.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        need(0 < size.value < 65536, 'identity_unavailable')
        raw = ctypes.create_string_buffer(size.value)
        need(a.GetTokenInformation(token, 1, raw, size, ctypes.byref(size)), 'identity_unavailable')
        sid = sid_string(a, k, ctypes.cast(raw, ctypes.POINTER(ctypes.c_void_p))[0])
        created, exited, kernel, user = (W.FILETIME() for _ in range(4))
        need(k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)), 'identity_unavailable')
        return dict(sid=sid, pid=pid or os.getpid(), created=(created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        if token: k.CloseHandle(token)
        k.CloseHandle(handle)


def security_descriptor(sid):
    k, a = libraries()
    pointer = ctypes.c_void_p()
    need(a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FA;;;' + sid + ')', 1, ctypes.byref(pointer), None), 'acl_unavailable')
    return pointer


def protect_file(path, sid):
    k, a = libraries()
    descriptor = security_descriptor(sid)
    try:
        need(a.SetFileSecurityW(str(path), 0x80000004, descriptor), 'acl_unavailable')
    finally:
        k.LocalFree(descriptor)


def verify_file(path, sid):
    ordinary(path)
    k, a = libraries()
    owner, descriptor, text = ctypes.c_void_p(), ctypes.c_void_p(), W.LPWSTR()
    need(a.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)) == 0, 'invalid_endpoint')
    try:
        need(sid_string(a, k, owner) == sid, 'wrong_owner')
        need(a.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None), 'invalid_endpoint')
        need(text.value == 'D:P(A;;FA;;;' + sid + ')', 'invalid_endpoint_acl')
    finally:
        if text: k.LocalFree(text)
        if descriptor: k.LocalFree(descriptor)


def encode(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    need(0 < len(data) <= MAX_MESSAGE, 'message_too_large')
    return data


def decode(data):
    need(0 < len(data) <= MAX_MESSAGE, 'message_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'invalid_request')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(IPCError('invalid_request')))
    except (ValueError, UnicodeError, RecursionError):
        raise IPCError('invalid_request') from None


def complete(ov, deadline, stop=None):
    api = win()
    while api.WaitForMultipleObjects([ov.event], False, 50) != api.WAIT_OBJECT_0:
        if time.monotonic() >= deadline or stop is not None and stop.is_set():
            ov.cancel()
            ov.GetOverlappedResult(True)
            raise IPCError('transport_timeout')
    count, error = ov.GetOverlappedResult(True)
    need(error == 0, 'transport_closed')
    return count


def send(handle, value, deadline, stop=None):
    data = encode(value)
    ov, _ = win().WriteFile(handle, data, overlapped=True)
    need(complete(ov, deadline, stop) == len(data), 'transport_closed')


def receive(handle, deadline, stop=None):
    ov, _ = win().ReadFile(handle, MAX_MESSAGE + 1, overlapped=True)
    size = complete(ov, deadline, stop)
    need(size <= MAX_MESSAGE, 'message_too_large')
    return decode(ov.getbuffer())


def peer_identity(handle, server):
    k, _ = libraries()
    pid = W.ULONG()
    need(getattr(k, 'GetNamedPipeServerProcessId' if server else 'GetNamedPipeClientProcessId')(handle, ctypes.byref(pid)), 'identity_unavailable')
    return process_identity(pid.value)


def read_endpoint(path):
    identity = process_identity()
    verify_file(Path(path).parent, identity['sid'])
    verify_file(path, identity['sid'])
    with ordinary(path).open('rb') as stream:
        raw = stream.read(16385)
    need(len(raw) <= 16384, 'invalid_endpoint')
    value = decode(raw)
    need(isinstance(value, dict) and set(value) == {'format', 'pipe', 'session', 'identity'}, 'invalid_endpoint')
    need(value['format'] == FORMAT and value['identity']['sid'] == identity['sid'], 'wrong_owner')
    need(isinstance(value['pipe'], str) and re.fullmatch(re.escape(PREFIX) + '[a-f0-9]{32}', value['pipe']), 'invalid_endpoint')
    need(isinstance(value['session'], str) and re.fullmatch('[a-f0-9]{64}', value['session']), 'invalid_endpoint')
    return value


def request(endpoint_path, action, body, timeout=TIMEOUT):
    need(isinstance(action, str) and len(action) <= 80 and isinstance(body, dict))
    need(type(timeout) in (int, float) and 0 < timeout <= 30)
    api = win()
    endpoint = read_endpoint(endpoint_path)
    payload = dict(format=FORMAT, session=endpoint['session'], action=action, body=body)
    encode(payload)  # Refuse oversized inputs before occupying the listener.
    deadline = time.monotonic() + timeout
    handle = None
    while handle is None:
        try:
            # Identification-only QoS prevents the server impersonating the caller.
            handle = api.CreateFile(endpoint['pipe'], 0xC0000000, 0, 0, 3, 0x40000000 | 0x00100000 | 0x00010000, 0)
        except OSError as exc:
            if exc.winerror not in (2, 231) or time.monotonic() >= deadline:
                raise IPCError('coordinator_unavailable') from None
            time.sleep(.025)
    try:
        need(peer_identity(handle, True) == endpoint['identity'], 'coordinator_changed')
        api.SetNamedPipeHandleState(handle, api.PIPE_READMODE_MESSAGE, None, None)
        send(handle, payload, deadline)
        result = receive(handle, deadline)
        send(handle, dict(ack=endpoint['session']), deadline)
        need(isinstance(result, dict) and set(result) == {'ok', 'value'}, 'invalid_response')
        if result['ok'] is not True:
            raise IPCError(result['value'] if result['value'] in ('invalid_request', 'coordinator_unavailable') else 'operation_failed')
        return result['value']
    finally:
        api.CloseHandle(handle)


class PipeServer:
    def __init__(self, endpoint_path, dispatch):
        self.endpoint_path = ordinary(Path(endpoint_path).absolute(), True)
        self.dispatch = dispatch
        self.stop = threading.Event()
        self.thread = None
        self.handle = None
        self.endpoint = None

    def start(self):
        need(self.thread is None, 'already_started')
        api = win()
        identity = process_identity()
        self.endpoint_path.parent.mkdir(parents=True, exist_ok=True)
        protect_file(self.endpoint_path.parent, identity['sid'])
        if self.endpoint_path.exists():
            old = read_endpoint(self.endpoint_path)
            try:
                live = process_identity(old['identity']['pid']) == old['identity']
            except IPCError:
                live = False
            need(not live, 'coordinator_already_running')
        self.endpoint = dict(format=FORMAT, pipe=PREFIX + secrets.token_hex(16), session=secrets.token_hex(32), identity=identity)
        class Attributes(ctypes.Structure):
            _fields_ = [('length', W.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', W.BOOL)]
        descriptor = security_descriptor(identity['sid'])
        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        try:
            self.handle = api.CreateNamedPipe(self.endpoint['pipe'], 3 | 0x40000000 | 0x00080000,
                4 | 2 | 8, 1, MAX_MESSAGE, MAX_MESSAGE, 1000, ctypes.addressof(attributes))
        finally:
            libraries()[0].LocalFree(descriptor)
        pending = self.endpoint_path.with_name('endpoint-' + secrets.token_hex(16) + '.pending')
        try:
            with pending.open('xb') as stream:
                stream.write(encode(self.endpoint)); stream.flush(); os.fsync(stream.fileno())
            protect_file(pending, identity['sid'])
            os.replace(pending, self.endpoint_path)
            self.thread = threading.Thread(target=self._run, daemon=True, name='INTERLINKED-AI-IPC')
            self.thread.start()
        except BaseException:
            api.CloseHandle(self.handle); self.handle = None
            if pending.exists(): pending.unlink()
            raise
        return self

    def _run(self):
        api = win()
        while not self.stop.is_set():
            try:
                ov = api.ConnectNamedPipe(self.handle, overlapped=True)
                complete(ov, time.monotonic() + 86400, self.stop)
                deadline = time.monotonic() + TIMEOUT
                need(peer_identity(self.handle, False)['sid'] == self.endpoint['identity']['sid'], 'wrong_owner')
                value = receive(self.handle, deadline, self.stop)
                need(isinstance(value, dict) and set(value) == {'format', 'session', 'action', 'body'})
                need(value['format'] == FORMAT and secrets.compare_digest(value['session'], self.endpoint['session']))
                need(isinstance(value['action'], str) and len(value['action']) <= 80 and isinstance(value['body'], dict))
                try:
                    reply = dict(ok=True, value=self.dispatch(value['action'], value['body']))
                    encode(reply)
                except Exception:
                    reply = dict(ok=False, value='invalid_request')
                send(self.handle, reply, deadline, self.stop)
                need(receive(self.handle, deadline, self.stop) == {'ack': self.endpoint['session']})
            except (OSError, ValueError, TypeError, KeyError, IPCError):
                pass
            finally:
                libraries()[0].DisconnectNamedPipe(self.handle)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(TIMEOUT + 1)
        if self.thread and self.thread.is_alive():
            raise IPCError('coordinator_shutdown_pending')
        if self.handle is not None:
            win().CloseHandle(self.handle); self.handle = None
        if self.endpoint and self.endpoint_path.exists():
            try:
                if read_endpoint(self.endpoint_path) == self.endpoint:
                    self.endpoint_path.unlink()
            except (OSError, ValueError, IPCError):
                pass
