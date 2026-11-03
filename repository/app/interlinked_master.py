"""Passive MASTER status collection and isolated display-worker transport.

This module never acquires project/workflow locks, starts project jobs, or
imports an editing engine. Private state and event payloads stay outside Git.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import subprocess
import threading
import time
import uuid
import unicodedata


def configuration_module():
    # Isolated MCP launches use -I and do not put the companion directory on sys.path.
    path = Path(__file__).resolve().with_name('interlinked_configuration.py')
    spec = importlib.util.spec_from_file_location('interlinked_master_configuration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

MAX_JSON = 256 * 1024
MAX_FRAME = 691136
EVENT_TTL = 180
VIOLET_TTL = 120
ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
ACTIVITY = {'working', 'waiting', 'turn_finished', 'interrupted', 'unknown'}
REPORTERS = {'dot': 'Dot-reported', 'local_task': 'Local task', 'demo': 'Demo', 'unverified': 'Unverified report'}
EVENTS = {'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse',
          'PostToolUse', 'PermissionRequest', 'Stop', 'Interrupt',
          'SubagentStart', 'SubagentStop'}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def lifecycle_node_id(kind, *parts):
    return 'codex-' + hashlib.sha256(json.dumps([kind, *parts], separators=(',', ':')).encode()).hexdigest()[:24]


class CoolerTitleResolver:
    """Read only exact active-chat metadata; never open conversation payloads.

    The cache belongs exclusively to the private native-display enrichment. A
    missing/changed schema or locked database is a label fallback, not an app
    failure. No caller can choose a database through an HTTP or plugin request.
    """
    def __init__(self, database=None):
        self.database = Path(database) if database is not None else Path.home() / '.codex' / 'state_5.sqlite'
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def title(value):
        if not isinstance(value, str):
            return None
        cleaned = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in value)
        return ' '.join(cleaned.split())[:160] or None

    def resolve(self, candidates, now=None):
        now = time.monotonic() if now is None else now
        candidates = {node: session for node, session in list(candidates.items())[:64]
                      if isinstance(node, str) and re.fullmatch(r'codex-[a-f0-9]{24}', node)
                      and isinstance(session, str) and ID.fullmatch(session)}
        with self.lock:
            requested = set(candidates.values())
            self.cache = {key: row for key, row in self.cache.items() if key in requested and row[0] > now}
            missing = sorted(requested - self.cache.keys())
            if missing:
                # Negative results are cached too, so an absent source is not
                # repeatedly opened at the renderer's polling cadence.
                self.cache.update({key: (now + 10, None) for key in missing})
                connection = None
                try:
                    path = ordinary(self.database)
                    info = path.stat()
                    if info.st_nlink != 1 or info.st_size > 512 * 1024 * 1024 or getattr(info, 'st_file_attributes', 0) & 0x1000:
                        raise ValueError('Chat metadata is unavailable.')
                    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.05)
                    deadline = time.monotonic() + .05
                    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                    placeholders = ','.join('?' for _ in missing)
                    # In the desktop's current schema, `name` is the visible
                    # chat title. The legacy `title` column can contain the
                    # initial prompt and must never be used as a fallback.
                    rows = connection.execute('SELECT id,substr(name,1,640),updated_at,archived FROM threads WHERE id IN (' + placeholders + ')', missing)
                    for session, title, updated, archived in rows:
                        if session in requested and type(archived) is int and archived == 0:
                            self.cache[session] = (now + 10, self.title(title))
                except (OSError, ValueError, sqlite3.Error):
                    pass
                finally:
                    if connection is not None:
                        connection.close()
            return {node: self.cache[session][1] for node, session in candidates.items()
                    if session in self.cache and self.cache[session][1]}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Invalid observation time.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.timestamp()
    except (ValueError, OverflowError):
        raise ValueError('Invalid observation time.') from None


class CoolerVoiceActivity:
    """Ephemeral UI heartbeats, exposed only in the native display copy.

    Sequence tombstones outlive the five-second activity lease. Receipt age is
    carried forward with a monotonic clock, so polling or wall-clock adjustment
    cannot renew microphone/speech activity. No session identity is exported.
    """
    TTL = 5
    TOMBSTONE_TTL = 120
    MAX_SESSIONS = 64

    def __init__(self, wall_clock=None, monotonic_clock=None):
        self.wall_clock = wall_clock or (lambda: time.time())
        self.monotonic_clock = monotonic_clock or (lambda: time.monotonic())
        self.lock = threading.Lock()
        self.sessions = {}

    def accept(self, body):
        fields = {'sessionId', 'sequence', 'observedAt', 'listening', 'speaking'}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError('Voice activity requires only its full heartbeat fields.')
        session, sequence, observed = body['sessionId'], body['sequence'], body['observedAt']
        if not isinstance(session, str) or re.fullmatch(r'[a-f0-9]{32}', session) is None:
            raise ValueError('Invalid voice activity session.')
        if type(sequence) is not int or not 1 <= sequence <= 9007199254740991:
            raise ValueError('Invalid voice activity sequence.')
        if (not isinstance(observed, str) or re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', observed) is None):
            raise ValueError('Voice activity requires a UTC observation time.')
        event_time = timestamp(observed)
        if type(body['listening']) is not bool or body['speaking'] not in ('none', 'browser_tts', 'windows_sapi'):
            raise ValueError('Invalid voice activity state.')
        with self.lock:
            now = self.monotonic_clock()
            age = self.wall_clock() - event_time
            # Future observations are rejected rather than receiving an
            # extended lease. At five seconds an observation is already stale.
            if not 0 <= age < self.TTL:
                raise ValueError('Voice activity observation is stale or in the future.')
            self._purge(now)
            previous = self.sessions.get(session)
            if previous is not None and sequence <= previous['sequence']:
                raise ValueError('Voice activity sequence must increase.')
            if previous is None and len(self.sessions) >= self.MAX_SESSIONS:
                raise ValueError('Voice activity session limit reached.')
            self.sessions[session] = dict(sequence=sequence, received=now, age=age,
                observedAt=observed, eventTime=event_time, listening=body['listening'], speaking=body['speaking'])
        return dict(accepted=True, sequence=sequence)

    def _purge(self, now):
        self.sessions = {session: row for session, row in self.sessions.items()
                         if now - row['received'] < self.TOMBSTONE_TTL}

    def snapshot(self):
        with self.lock:
            now = self.monotonic_clock()
            self._purge(now)
            sources = {}
            for row in self.sessions.values():
                if row['age'] + max(0, now - row['received']) >= self.TTL:
                    continue
                active = ([('microphone_capture', 'listening')] if row['listening'] else [])
                if row['speaking'] != 'none':
                    active.append((row['speaking'], 'speaking'))
                for source, phase in active:
                    if source not in sources or row['eventTime'] > sources[source][0]:
                        sources[source] = (row['eventTime'], dict(origin='assistant_ui', source=source,
                            phase=phase, observedAt=row['observedAt']))
            observed = datetime.fromtimestamp(self.wall_clock(), timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            return dict(available=True, observedAt=observed,
                sources=[sources[source][1] for source in ('microphone_capture', 'browser_tts', 'windows_sapi') if source in sources])


def ordinary(path, missing=False):
    """Reject symlinks/reparse ancestors before reading or replacing state."""
    path = Path(os.path.abspath(path))
    for ancestor in [*reversed(path.parents), path]:
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked MASTER paths are not supported.')
    return path


def read_json(path, maximum=MAX_JSON):
    path = ordinary(path)
    info = path.stat()
    if info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x1000:
        raise ValueError('MASTER metadata must be an ordinary local single-link file.')
    if info.st_size > maximum:
        raise ValueError('MASTER record is too large.')
    with path.open('r', encoding='utf-8-sig') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('MASTER record must be an object.')
    return value


def write_json(path, value):
    path = ordinary(path, missing=True)
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_JSON:
        raise ValueError('MASTER record is too large.')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        ordinary(path, missing=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def owned_directory(path, kind, create=True):
    path = ordinary(path, missing=create)
    owner = dict(format='interlinked-master-owner-v1', kind=kind,
                 path=str(path), user=os.environ.get('USERNAME') or os.environ.get('USER') or '')
    marker = path / 'OWNER.json'
    if not path.exists():
        if not create:
            raise ValueError('MASTER has not been configured.')
        path.mkdir(parents=True)
    if marker.exists():
        if read_json(marker) != owner:
            raise ValueError('MASTER state ownership differs.')
    else:
        if not create or any(path.iterdir()):
            raise ValueError('Existing MASTER directory has no ownership record.')
        write_json(marker, owner)
    return path


def common_state_root():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise ValueError('Windows local application data is unavailable.')
    return Path(base) / 'INTERLINKED' / 'MASTER-status'


def current_user_sid():
    """Match the passive Buddy publisher's per-user ownership marker."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            return None
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            return None
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return None
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def clean_id(value, label='identifier'):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid MASTER ' + label + '.')
    return value


def clean_text(value, maximum=120):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid MASTER display text.')
    return value


def sanitize_hook(payload, now=None):
    """Allowlist metadata only; prompts, command strings and results never leave here."""
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in EVENTS:
        raise ValueError('Unsupported lifecycle event.')
    observed = payload.get('observedAt') or utc()
    when = timestamp(observed)
    current = time.time() if now is None else now
    if when > current + 30 or current - when > 86400:
        raise ValueError('Lifecycle event time is outside the accepted window.')
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or len(cwd) > 1024 or not Path(cwd).is_absolute():
        raise ValueError('Lifecycle event needs an absolute local working directory.')
    if cwd.startswith(('\\\\', '//')):
        raise ValueError('Network activity roots are not supported.')
    event = dict(format='interlinked-master-event-v1', event=payload['hook_event_name'],
                 sessionId=clean_id(payload.get('session_id'), 'session ID'),
                 observedAt=observed, cwd=os.path.abspath(cwd))
    for incoming, outgoing in [('turn_id', 'turnId'), ('agent_id', 'agentId'), ('tool_use_id', 'toolUseId')]:
        if payload.get(incoming) is not None:
            event[outgoing] = clean_id(payload[incoming], outgoing)
    # Tool name is an enum-like label, never tool input/output.
    tool = payload.get('tool_name')
    if isinstance(tool, str) and len(tool) <= 100 and re.fullmatch(r'[A-Za-z0-9_.:/-]+', tool):
        event['tool'] = tool
    explicit_id = payload.get('event_id')
    event['id'] = clean_id(explicit_id, 'event ID') if explicit_id else hashlib.sha256(
        json.dumps(event, sort_keys=True).encode()).hexdigest()
    return event


def publish_event(root, event):
    root = owned_directory(root, 'event-feed')
    directory = ordinary(root / 'events', missing=True)
    directory.mkdir(exist_ok=True)
    # Bounded spool; overload drops telemetry rather than delaying coding work.
    if sum(1 for _ in directory.glob('*.json')) >= 2048:
        return False
    write_json(directory / (clean_id(event['id']) + '.json'), event)
    return True


def publish_violet(root, body):
    if not isinstance(body, dict) or set(body) - {'status', 'message', 'projectId', 'reporter'}:
        raise ValueError('Unexpected Violet status fields.')
    status = body.get('status')
    if not isinstance(status, str) or status not in ACTIVITY:
        raise ValueError('Unsupported Violet status.')
    root = owned_directory(root, 'event-feed', create=False)
    if not (root / 'snapshot.json').exists():
        raise ValueError('MASTER is not running.')
    snapshot = read_json(root / 'snapshot.json')
    if not 0 <= time.time() - timestamp(snapshot['utc']) <= 15:
        raise ValueError('MASTER status is stale.')
    reporter = body.get('reporter', 'unverified')
    if not isinstance(reporter, str) or reporter not in REPORTERS:
        raise ValueError('Unsupported reporter.')
    record = dict(format='interlinked-violet-status-v2', source=REPORTERS[reporter], reporter=reporter,
                  status=status, message=clean_text(body.get('message', ''), 120), observedAt=utc())
    if body.get('projectId'):
        record['projectId'] = clean_id(body['projectId'], 'project ID')
        if record['projectId'] not in {row['id'] for row in snapshot.get('projects', [])}:
            raise ValueError('Violet status names an unregistered project.')
    write_json(root / 'violet.json', record)
    return dict(status='published', source=record['source'], reporter=reporter, observedAt=record['observedAt'])


@contextmanager
def attention_guard(root, timeout=.5):
    """Only the owned attention metadata is locked, across service/MCP processes."""
    root = owned_directory(root, 'event-feed', create=False)
    path = ordinary(root / '.attention.lock', missing=True)
    deadline = time.monotonic() + timeout
    stream = None
    if os.name == 'nt':
        import ctypes
        import msvcrt
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        while stream is None:
            handle = kernel.CreateFileW(str(path), 0xc0000000, 0, None, 4, 0x00200000, None)
            if handle != ctypes.c_void_p(-1).value:
                try:
                    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                except BaseException:
                    kernel.CloseHandle(handle)
                    raise
                stream = os.fdopen(fd, 'r+b')
            elif ctypes.get_last_error() in (32, 33) and time.monotonic() < deadline:
                time.sleep(.01)
            else:
                raise OSError('MASTER attention metadata is busy or unavailable.')
    else:
        import fcntl
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        stream = os.fdopen(fd, 'r+b')
        try:
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise OSError('MASTER attention metadata is busy.') from None
                    time.sleep(.01)
        except BaseException:
            stream.close()
            raise
    try:
        actual, named = os.fstat(stream.fileno()), path.lstat()
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1 or named.st_nlink != 1 or
                (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino) or
                getattr(actual, 'st_file_attributes', 0) & 0x441400):
            raise ValueError('Unsafe MASTER attention lock.')
        owned_directory(root, 'event-feed', create=False)
        yield root
    finally:
        stream.close()


def _attention_acks(root):
    directory = ordinary(root / 'attention-acks', missing=True)
    if not directory.exists():
        return {}
    paths = list(directory.iterdir())
    if len(paths) > 200:
        raise ValueError('Attention acknowledgment directory needs review.')
    result = {}
    for path in paths:
        if path.suffix != '.json' or not ID.fullmatch(path.stem):
            raise ValueError('Unexpected attention acknowledgment; existing data was preserved.')
        ack = read_json(path, 4096)
        if (set(ack) != {'format', 'id', 'acknowledgedAt'} or
                ack.get('format') != 'interlinked-master-ack-v1' or ack.get('id') != path.stem):
            raise ValueError('Invalid attention acknowledgment.')
        timestamp(ack['acknowledgedAt'])
        result[path.stem] = ack
    return result


def _master_attention_unlocked(root):
    acknowledgments = _attention_acks(root)
    try:
        queue = read_json(root / 'attention.json')
    except FileNotFoundError:
        if acknowledgments:
            raise ValueError('Attention queue is missing; acknowledgment data was preserved.')
        queue = dict(format='interlinked-master-attention-v1', items=[])
    if queue.get('format') != 'interlinked-master-attention-v1' or not isinstance(queue.get('items'), list) or len(queue['items']) > 100:
        raise ValueError('Invalid attention queue.')
    items, identifiers = [], set()
    for item in queue['items']:
        if not isinstance(item, dict) or item.get('kind') not in ('failure', 'input_needed'):
            raise ValueError('Invalid attention item.')
        item = dict(id=clean_id(item['id']), projectId=clean_id(item['projectId']), kind=item['kind'],
            observedAt=item['observedAt'], source=clean_text(item.get('source', 'Project telemetry'), 80),
            summary='Build/test failed' if item['kind'] == 'failure' else 'Input requested')
        timestamp(item['observedAt'])
        if item['id'] in identifiers:
            raise ValueError('Attention queue contains duplicate identities.')
        identifiers.add(item['id'])
        ack = acknowledgments.get(item['id'])
        items.append(dict(item, acknowledged=ack is not None,
            acknowledgedAt=ack['acknowledgedAt'] if ack else None))
    return dict(available=True, items=items, pendingCount=sum(not item['acknowledged'] for item in items), delivery='Queued for next Violet check')


def master_attention(root):
    with attention_guard(root) as root:
        return _master_attention_unlocked(root)


def acknowledge_attention(root, body):
    if not isinstance(body, dict) or set(body) != {'ids'} or not isinstance(body['ids'], list) or not 1 <= len(body['ids']) <= 100:
        raise ValueError('Supply between one and 100 attention IDs.')
    ids = [clean_id(value) for value in body['ids']]
    with attention_guard(root) as root:
        queue = _master_attention_unlocked(root)
        known = {item['id']: item for item in queue['items']}
        if any(value not in known for value in ids):
            raise ValueError('Attention item is no longer retained.')
        directory = ordinary(root / 'attention-acks', missing=True)
        directory.mkdir(exist_ok=True)
        for value in set(ids):
            if not known[value]['acknowledged']:
                write_json(directory / (value + '.json'), dict(format='interlinked-master-ack-v1', id=value, acknowledgedAt=utc()))
        return dict(acknowledged=sorted(set(ids)), resolvesProjectFailure=False)


def collect_attention(root, projects):
    with attention_guard(root) as root:
        return _collect_attention_unlocked(root, projects)


def _collect_attention_unlocked(root, projects):
    queue = _master_attention_unlocked(root)
    items = [{k: v for k, v in item.items() if k not in {'acknowledged', 'acknowledgedAt'}} for item in queue['items']]
    seen = {item['id'] for item in items}
    for project in projects:
        for kind, observation in [('failure', project['health']), ('input_needed', project['activity'])]:
            if observation.get('status') != ('failed' if kind == 'failure' else 'waiting') or not observation.get('observedAt'):
                continue
            identifier = hashlib.sha256(json.dumps([project['id'], kind, observation['observedAt']], separators=(',', ':')).encode()).hexdigest()[:32]
            if identifier not in seen:
                items.append(dict(id=identifier, projectId=project['id'], kind=kind, observedAt=observation['observedAt'],
                    source=clean_text(observation.get('source', 'Project telemetry'), 80),
                    summary='Build/test failed' if kind == 'failure' else 'Input requested'))
                seen.add(identifier)
    retained = items[-100:]
    if retained != [{k: v for k, v in item.items() if k not in {'acknowledged', 'acknowledgedAt'}} for item in queue['items']]:
        write_json(Path(root) / 'attention.json', dict(format='interlinked-master-attention-v1', items=retained))
    directory = ordinary(Path(root) / 'attention-acks', missing=True)
    if directory.exists():
        retained_ids = {item['id'] for item in retained}
        for path in directory.glob('*.json'):
            if path.stem not in retained_ids:
                # Validate the old acknowledgment before pruning only owned metadata.
                ack = read_json(path, 4096)
                if (set(ack) != {'format', 'id', 'acknowledgedAt'} or
                        ack.get('format') != 'interlinked-master-ack-v1' or ack.get('id') != path.stem):
                    raise ValueError('Invalid retained attention acknowledgment.')
                timestamp(ack['acknowledgedAt'])
                ordinary(path).unlink()
    return _master_attention_unlocked(root)


def bridge_snapshot(root):
    root = owned_directory(root, 'event-feed', create=False)
    value = read_json(root / 'snapshot.json')
    # Paths, window titles, tokens and raw workflow output are not in this export.
    return value


class EventModel:
    def __init__(self):
        self.sessions = {}
        self.seen = deque(maxlen=4096)
        self.seen_set = set()
        # Child role survives generic activity updates. This is an observed,
        # bounded event model, never a complete inventory of Codex sessions.
        self.subagents = {}
        self.subagent_parent_uncertainty = {}
        self.subagent_coverage_lost = False

    @staticmethod
    def _terminal_blocks(previous, event):
        if not previous or previous['event'] not in {'Stop', 'Interrupt', 'SubagentStop', 'SessionEnd'}:
            return False
        if event['event'] in {'Stop', 'Interrupt', 'SubagentStop', 'SessionEnd'}:
            return False
        # Child-session turn IDs need not share the parent's namespace. A
        # different inferred turn ID is not proof of a new child invocation.
        if event.get('viaChildSession'):
            return True
        # An explicit new start can reuse an agent without a supplied turn ID.
        if event['event'] == 'SubagentStart' and timestamp(event['observedAt']) > timestamp(previous['observedAt']):
            return False
        return event.get('turnId') == previous.get('turnId')

    @staticmethod
    def _tie_blocks(previous, event):
        # Hook timestamps can have millisecond precision. At the same instant,
        # waiting/terminal observations beat working regardless of delivery order.
        def priority(kind):
            if kind in {'Stop', 'Interrupt', 'SubagentStop', 'SessionEnd'}:
                return 3
            if kind == 'PermissionRequest':
                return 2
            return 1 if kind in {'SubagentStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse'} else 0
        return (previous is not None and timestamp(previous['observedAt']) == timestamp(event['observedAt'])
                and priority(previous['event']) > priority(event['event']))

    def _observe_subagent(self, event):
        kind, parent = event['event'], event['sessionId']
        agent = event.get('agentId')
        when = timestamp(event['observedAt'])
        resumed_uncertain_at = 0
        if kind in {'SubagentStart', 'SubagentStop'} and not agent:
            self.subagent_coverage_lost = True
            return
        if not agent:
            # Some runtime hooks identify the child as their session rather
            # than repeating agentId. Resolve only an explicit unique edge;
            # retain the original event separately in generic activity.
            matches = [key for key in self.subagents if key[1] == parent]
            if len(matches) == 1:
                parent, agent = matches[0]
                event = dict(event, sessionId=parent, agentId=agent, viaChildSession=True)
            elif matches:
                for key in matches:
                    child = self.subagents[key]
                    possible_resume = (when > timestamp(child['observedAt'])
                        and kind not in {'Stop', 'Interrupt', 'SubagentStop', 'SessionEnd'})
                    if when >= timestamp(child['observedAt']) and (child['state'] != 'stopped' or child.get('viaChildSession') or possible_resume):
                        child['uncertainAt'] = max(child.get('uncertainAt', 0), when)
                return
        # Parent events cannot identify which child is waiting or interrupted.
        # Keep identity-specific timestamps unchanged when marking uncertainty.
        if not agent and kind in {'PermissionRequest', 'Interrupt', 'SessionEnd'}:
            # Background hook delivery can reorder a child's start behind the
            # parent's permission request. Preserve that uncertainty as well.
            if parent not in self.subagent_parent_uncertainty and len(self.subagent_parent_uncertainty) >= 256:
                oldest = min(self.subagent_parent_uncertainty, key=self.subagent_parent_uncertainty.get)
                self.subagent_parent_uncertainty.pop(oldest)
                self.subagent_coverage_lost = True
            self.subagent_parent_uncertainty[parent] = max(self.subagent_parent_uncertainty.get(parent, 0), when)
            for (session, _), child in self.subagents.items():
                if session == parent and child['state'] == 'working' and when >= timestamp(child['observedAt']):
                    child['uncertainAt'] = max(child.get('uncertainAt', 0), when)
            return
        key = (parent, agent)
        child = self.subagents.get(key)
        if child is None:
            if kind not in {'SubagentStart', 'SubagentStop'}:
                return
            if len(self.subagents) >= 256:
                oldest = min(self.subagents, key=lambda k: timestamp(self.subagents[k]['observedAt']))
                self.subagents.pop(oldest)
                self.subagent_coverage_lost = True
            main = self.sessions.get((parent, 'main'), {})
            child = dict(cwd=event['cwd'], parentCwd=main.get('cwd'), state='unknown',
                         event=kind, observedAt=event['observedAt'],
                         uncertainAt=self.subagent_parent_uncertainty.get(parent, 0))
            self.subagents[key] = child
            # An explicit role may arrive after either supported child hook
            # shape. A newer child directory never changes its original owner.
            candidates = [value for value in [self.sessions.get(key)] if value]
            generic = self.sessions.get((agent, 'main'))
            matches = [edge for edge in self.subagents if edge[1] == agent]
            if generic and len(matches) == 1:
                candidates.append(dict(generic, sessionId=parent, agentId=agent, viaChildSession=True))
            elif generic and timestamp(generic['observedAt']) >= when:
                # A second delayed relationship can reveal that a previous
                # child-session observation was ambiguous after all.
                for edge in matches:
                    other = self.subagents[edge]
                    if (edge == key or other.get('viaChildSession')) and timestamp(generic['observedAt']) >= timestamp(other['observedAt']):
                        other['uncertainAt'] = max(other.get('uncertainAt', 0), timestamp(generic['observedAt']))
            latest = None
            for candidate in candidates:
                if latest is None or timestamp(candidate['observedAt']) > timestamp(latest['observedAt']) or self._tie_blocks(candidate, latest):
                    latest = candidate
            if latest and (timestamp(latest['observedAt']) > when or self._tie_blocks(latest, event)):
                if not self._terminal_blocks(event, latest):
                    event = latest
                    kind, when = event['event'], timestamp(event['observedAt'])
                elif latest.get('viaChildSession') and timestamp(latest['observedAt']) > when:
                    # A delayed stop cannot disprove later child activity,
                    # even when that activity cannot establish a new run.
                    resumed_uncertain_at = timestamp(latest['observedAt'])
        elif (event.get('viaChildSession') and child['state'] == 'stopped'
              and when > timestamp(child['observedAt'])
              and kind not in {'Stop', 'Interrupt', 'SubagentStop', 'SessionEnd'}):
            # Follow-up turns can omit SubagentStart. Do not resurrect from
            # unrelated turn-ID namespaces, but do not retain a confident zero.
            child['uncertainAt'] = max(child.get('uncertainAt', 0), when)
            return
        elif when < timestamp(child['observedAt']) or self._terminal_blocks(child, event) or self._tie_blocks(child, event):
            return
        state = ('working' if kind in {'SubagentStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse'}
                 else 'waiting' if kind == 'PermissionRequest'
                 else 'stopped' if kind in {'SubagentStop', 'Stop', 'Interrupt', 'SessionEnd'} else 'unknown')
        child.update(state=state, event=kind, observedAt=event['observedAt'], viaChildSession=event.get('viaChildSession', False))
        if event.get('turnId') is not None:
            child['turnId'] = event['turnId']
        else:
            child.pop('turnId', None)
        if state == 'stopped' or when > child.get('uncertainAt', 0):
            child.pop('uncertainAt', None)
        if resumed_uncertain_at:
            child['uncertainAt'] = max(child.get('uncertainAt', 0), resumed_uncertain_at)

    def accept(self, event):
        if event.get('format') != 'interlinked-master-event-v1':
            raise ValueError('Unsupported event format.')
        identifier = clean_id(event.get('id'))
        if identifier in self.seen_set:
            return False
        when = timestamp(event['observedAt'])
        if when > time.time() + 30 or time.time() - when > 86400:
            raise ValueError('Event observation time is outside the accepted window.')
        clean_id(event.get('sessionId'))
        cwd = event.get('cwd')
        if not isinstance(cwd, str) or len(cwd) > 1024 or not Path(cwd).is_absolute() or cwd.startswith(('\\\\', '//')):
            raise ValueError('Invalid lifecycle working directory.')
        for key in ('turnId', 'agentId', 'toolUseId'):
            if key in event:
                clean_id(event[key])
        if event.get('event') not in EVENTS:
            raise ValueError('Unsupported lifecycle event.')
        if len(self.seen) == self.seen.maxlen:
            self.seen_set.discard(self.seen[0])
        self.seen.append(identifier)
        self.seen_set.add(identifier)
        self._observe_subagent(event)
        key = (event['sessionId'], event.get('agentId', 'main'))
        previous = self.sessions.get(key)
        if previous and when < timestamp(previous['observedAt']):
            return False
        if self._terminal_blocks(previous, event) or (event.get('agentId') is not None and self._tie_blocks(previous, event)):
            return False
        if len(self.sessions) >= 256 and key not in self.sessions:
            oldest = min(self.sessions, key=lambda k: timestamp(self.sessions[k]['observedAt']))
            self.sessions.pop(oldest)
        self.sessions[key] = deepcopy(event)
        return True

    def activity(self, roots, now=None):
        now = time.time() if now is None else now
        roots = [os.path.normcase(os.path.abspath(r)).rstrip('\\/') for r in roots if r]
        rows = []
        for event in self.sessions.values():
            cwd = os.path.normcase(os.path.abspath(event.get('cwd', '')))
            if not any(cwd == root or cwd.startswith(root + os.sep) for root in roots):
                continue
            kind = event['event']
            status = ('waiting' if kind == 'PermissionRequest' else 'interrupted' if kind == 'Interrupt'
                      else 'turn_finished' if kind in {'Stop', 'SubagentStop'} else 'unknown' if kind == 'SessionEnd'
                      else 'working' if kind in {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStart'} else 'unknown')
            stale = now - timestamp(event['observedAt']) > EVENT_TTL
            rows.append(dict(id=event.get('agentId') or event['sessionId'], status='unknown' if stale else status,
                             lastStatus=status, observedAt=event['observedAt'], stale=stale))
        priority = {'waiting': 0, 'working': 1, 'interrupted': 2, 'turn_finished': 3, 'unknown': 4}
        rows.sort(key=lambda r: (priority[r['status']], -timestamp(r['observedAt'])))
        rows = rows[:32]
        first = rows[0] if rows else {}
        return dict(status=first.get('status', 'unknown'), source='Codex lifecycle' if rows else 'Not connected',
                    observedAt=first.get('observedAt'), stale=first.get('stale', False), agents=rows)

    def subagent_counts(self, registrations, now=None):
        """Return reduced observed-working summaries, without identities or paths.

        A feed parse error can set subagent_coverage_lost as well as incrementing
        the service error counter. Once coverage is lost it remains partial for
        this collector lifetime; silence never reconciles an incomplete feed.
        """
        now = time.time() if now is None else now
        roots = []
        for order, registration in enumerate(registrations):
            for field in ('activityRoot', 'root'):
                if registration.get(field):
                    root = os.path.normcase(os.path.abspath(registration[field])).rstrip('\\/')
                    roots.append((root, order, registration['id']))
        roots.sort(key=lambda row: (-len(row[0]), row[1]))

        def match(cwd):
            if not cwd:
                return None
            path = os.path.normcase(os.path.abspath(cwd))
            return next((identifier for root, _, identifier in roots
                         if path == root or path.startswith(root + os.sep)), None)

        # Nesting is followed only through explicit child IDs, never from paths
        # that resemble UUIDs. Duplicate parent identities are ambiguous.
        by_agent = {}
        for key in self.subagents:
            by_agent.setdefault(key[1], []).append(key)

        def owner(key, visited):
            if key in visited:
                return None
            visited.add(key)
            child = self.subagents[key]
            ancestors = by_agent.get(key[0], [])
            if len(ancestors) == 1:
                inherited = owner(ancestors[0], visited)
                if inherited is not None:
                    return inherited
            # The parent's own event may arrive after the child start. Consult
            # it only when no initiating-parent directory was captured earlier.
            parent_cwd = child.get('parentCwd') or self.sessions.get((key[0], 'main'), {}).get('cwd')
            return match(parent_cwd) or match(child['cwd'])

        assigned = {row['id']: [] for row in registrations}
        for key, child in self.subagents.items():
            identifier = owner(key, set())
            if identifier in assigned:
                assigned[identifier].append(child)

        def summary(children):
            known = [child for child in children if 0 <= now - timestamp(child['observedAt']) <= EVENT_TTL
                     and child['state'] != 'unknown' and not child.get('uncertainAt')]
            observed = max((child['observedAt'] for child in known), key=timestamp, default=None)
            if not known:
                return dict(workingCount=None, status='unavailable', observedAt=None, source='Codex lifecycle (observed)')
            # A confirmed old stop is history, not an unresolved worker. It
            # cannot establish a fresh zero by itself (handled above), nor
            # should it permanently taint unrelated fresh child evidence.
            unresolved = any(child not in known and not (
                child['state'] == 'stopped' and not child.get('uncertainAt')
                and now - timestamp(child['observedAt']) > EVENT_TTL) for child in children)
            partial = self.subagent_coverage_lost or unresolved
            return dict(workingCount=sum(child['state'] == 'working' for child in known),
                        status='partial' if partial else 'observed', observedAt=observed, source='Codex lifecycle (observed)')

        projects = {identifier: summary(children) for identifier, children in assigned.items()}
        available = [value for value in projects.values() if value['workingCount'] is not None]
        if not available:
            total = summary([])
        else:
            total = dict(workingCount=sum(value['workingCount'] for value in available),
                         status='observed' if len(available) == len(projects) and all(value['status'] == 'observed' for value in available) else 'partial',
                         observedAt=max((value['observedAt'] for value in available), key=timestamp),
                         source='Codex lifecycle (observed)')
        return projects, total

    def lifecycle_graph(self, registrations, now=None):
        """Bounded observed relationships; absence of an edge is not a guessed parent."""
        now = time.time() if now is None else now
        roots = sorted(((os.path.normcase(os.path.abspath(row[field])).rstrip('\\/'), order, row['id'])
            for order, row in enumerate(registrations) for field in ('activityRoot', 'root') if row.get(field)),
            key=lambda row: (-len(row[0]), row[1]))
        def project(cwd):
            path = os.path.normcase(os.path.abspath(cwd)) if cwd else ''
            return next((identifier for root, _, identifier in roots if path == root or path.startswith(root + os.sep)), None)
        def identifier(kind, *parts):
            return lifecycle_node_id(kind, *parts)
        by_agent = {}
        for edge in self.subagents:
            by_agent.setdefault(edge[1], []).append(edge)
        mains = {key[0]: event for key, event in self.sessions.items()
                 if key[1] == 'main' and key[0] not in by_agent}
        main_ids = {session: identifier('main', session) for session in mains}
        child_ids = {edge: identifier('child', *edge) for edge in self.subagents}
        def parent_identifier(edge):
            parents = by_agent.get(edge[0], [])
            if len(parents) != 1:
                return main_ids.get(edge[0]) if not parents else None
            seen, cursor = {edge}, parents[0]
            while True:
                if cursor in seen:
                    return None
                seen.add(cursor)
                ancestors = by_agent.get(cursor[0], [])
                if len(ancestors) != 1:
                    return child_ids[parents[0]]
                cursor = ancestors[0]
        def owner(edge, seen):
            if edge in seen:
                return None
            seen.add(edge)
            parents = by_agent.get(edge[0], [])
            if len(parents) == 1:
                inherited = owner(parents[0], seen)
                if inherited is not None:
                    return inherited
            child = self.subagents[edge]
            return project(child.get('parentCwd') or mains.get(edge[0], {}).get('cwd')) or project(child['cwd'])
        def status(event):
            kind = event['event']
            return ('waiting' if kind == 'PermissionRequest' else 'interrupted' if kind == 'Interrupt'
                    else 'turn_finished' if kind in {'Stop', 'SubagentStop'}
                    else 'working' if kind in {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStart'} else 'unknown')
        nodes = []
        for session, event in mains.items():
            fresh = 0 <= now - timestamp(event['observedAt']) <= EVENT_TTL
            nodes.append(dict(nodeId=main_ids[session], kind='codex_main', projectId=project(event['cwd']),
                status=status(event) if fresh else 'unknown', observedAt=event['observedAt'],
                confidence='observed' if fresh else 'stale'))
        for edge, child in self.subagents.items():
            parent = parent_identifier(edge)
            fresh = 0 <= now - timestamp(child['observedAt']) <= EVENT_TTL
            certain = fresh and not child.get('uncertainAt')
            node = dict(nodeId=child_ids[edge], kind='codex_child', projectId=owner(edge, set()),
                status=status(child) if certain else 'unknown', observedAt=child['observedAt'],
                confidence='stale' if not fresh else 'observed' if certain and parent else 'partial')
            if parent:
                node['parentNodeId'] = parent
            nodes.append(node)
        priority = {'waiting': 0, 'working': 1, 'interrupted': 2, 'turn_finished': 3, 'unknown': 4}
        nodes.sort(key=lambda node: (priority[node['status']], -timestamp(node['observedAt']), node['nodeId']))
        partial = self.subagent_coverage_lost or len(nodes) > 64 or any(node['confidence'] == 'partial' for node in nodes)
        omitted_count = max(0, len(nodes) - 64)
        nodes = nodes[:64]
        retained = {node['nodeId'] for node in nodes}
        # Never send a dangling edge after bounded selection.
        for node in nodes:
            if node.get('parentNodeId') not in retained:
                if node.pop('parentNodeId', None) is not None:
                    node['confidence'] = 'partial'; partial = True
        return dict(available=bool(nodes), observedAt=max((node['observedAt'] for node in nodes), key=timestamp, default=None),
                    partial=partial, omittedCount=omitted_count, nodes=nodes)


def default_projects(demo=False):
    home = Path(os.environ.get('USERPROFILE') or Path.home())
    roots = ['', '', ''] if demo else [str(home / 'OneDrive/Documents/ED-IT Buddy'),
        str(home / 'Documents/Codex/MW5-MOD/Source'), str(home / 'Documents/Codex/INTERLINKED')]
    rows = [dict(id=i, label=label, root=root) for i, label, root in zip(
        ['ed-it-buddy', 'mw5-mod', 'interlinked'], ['ED-IT Buddy', 'MW5 MOD', 'INTERLINKED'], roots)]
    if not demo:
        rows[1]['activityRoot'] = str(home / 'Documents/Codex/MW5-MOD')
    return rows


def validate_projects(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError('Register between one and twelve projects.')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'id', 'label', 'root', 'activityRoot'}:
            raise ValueError('Unexpected project registration fields.')
        identifier = clean_id(row.get('id'), 'project ID')
        if identifier in seen:
            raise ValueError('Duplicate MASTER project ID.')
        seen.add(identifier)
        item = dict(id=identifier, label=clean_text(row.get('label'), 32))
        for key in ('root', 'activityRoot'):
            value = row.get(key, '')
            if not isinstance(value, str) or len(value) > 1024 or value.startswith(('\\\\', '//')):
                raise ValueError('Use an ordinary local project folder.')
            if value:
                if not Path(value).is_absolute():
                    raise ValueError('Project roots must be absolute.')
                ordinary(value, missing=True)
                value = os.path.abspath(value)
            if key == 'root' or value:
                item[key] = value
        result.append(item)
    return result


GIT_OBSERVATION_TTL = 15


def git_observation(root, stop=None):
    if not root:
        return dict(status='unavailable', branch=None, head=None, dirty=None, observedAt=None)
    try:
        ordinary(root)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if stop is not None and stop.is_set():
            raise ValueError('Git observation cancelled.')
        process = subprocess.Popen(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c',
            'core.untrackedCache=false', '-C', root, 'status', '--porcelain=v2', '--branch', '--untracked-files=normal'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + 3
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or stop is not None and stop.is_set():
                    raise ValueError('Git observation expired or cancelled.')
                try:
                    output, _ = process.communicate(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=1)
        if process.returncode or len(output) > 256 * 1024:
            raise ValueError('Git status unavailable.')
        lines = output.decode('utf-8', 'replace').splitlines()
        metadata = {line.split(' ', 2)[1]: line.split(' ', 2)[2] for line in lines if line.startswith('# branch.')}
        changes = [line for line in lines if not line.startswith('# ')]
        return dict(status='available', branch=metadata.get('branch.head'), head=metadata.get('branch.oid'),
                    dirty=bool(changes), changeCount=len(changes), observedAt=utc())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return dict(status='unavailable', branch=None, head=None, dirty=None, observedAt=utc())


class GitCollector:
    """At most two owned Git checks; pending work is coalesced by registered root."""
    def __init__(self, stop):
        self.stop = stop
        self.wake = threading.Condition()
        self.roots = {}
        self.pending = deque()
        self.queued = set()
        self.active = set()
        self.scheduled = {}
        self.observations = {}
        self.threads = []

    def schedule(self, projects, now=None):
        now = time.monotonic() if now is None else now
        with self.wake:
            if self.stop.is_set():
                return
            self.roots = {p['id']: p['root'] for p in projects}
            valid = set(self.roots.items())
            self.pending = deque(key for key in self.pending if key in valid)
            self.queued.intersection_update(valid)
            self.scheduled = {key: value for key, value in self.scheduled.items() if key in valid}
            self.observations = {key: value for key, value in self.observations.items() if key in valid}
            for key in self.roots.items():
                if key[1] and key not in self.queued and key not in self.active and (key not in self.scheduled or now - self.scheduled[key] >= 10):
                    self.pending.append(key)
                    self.queued.add(key)
                    self.scheduled[key] = now
            if not self.threads:
                for index in range(2):
                    thread = threading.Thread(target=self._run, daemon=True, name='master-git-' + str(index))
                    self.threads.append(thread)
                    thread.start()
            self.wake.notify_all()

    def snapshot(self, projects):
        with self.wake:
            return {p['id']: deepcopy(self.observations.get((p['id'], p['root']),
                dict(status='unavailable', branch=None, head=None, dirty=None, observedAt=None))) for p in projects}

    def _run(self):
        while not self.stop.is_set():
            with self.wake:
                while not self.pending and not self.stop.is_set():
                    self.wake.wait(.5)
                if self.stop.is_set():
                    return
                key = self.pending.popleft()
                self.queued.discard(key)
                self.active.add(key)
            try:
                observation = git_observation(key[1], self.stop)
            except Exception:
                observation = dict(status='unavailable', branch=None, head=None, dirty=None, observedAt=utc())
            with self.wake:
                self.active.discard(key)
                if not self.stop.is_set() and self.roots.get(key[0]) == key[1]:
                    self.observations[key] = observation
                self.wake.notify_all()

    def close(self):
        self.stop.set()
        with self.wake:
            self.pending.clear()
            self.queued.clear()
            self.wake.notify_all()
        for thread in self.threads:
            thread.join(timeout=4)


def receipt_health(receipt, git):
    unknown = dict(status='unknown', source='No verified result', observedAt=None, baseline=None, summary='No current build/test evidence')
    if not receipt:
        return unknown
    if receipt.get('format') != 'interlinked-master-result-v1' or receipt.get('result') not in {'passed', 'failed'} or receipt.get('kind') not in {'build', 'test'}:
        raise ValueError('Unsupported build/test receipt.')
    observed = receipt['observedAt']; timestamp(observed)
    baseline = receipt.get('baseline')
    if not isinstance(baseline, dict) or set(baseline) != {'head', 'clean'} or baseline.get('clean') is not True or not re.fullmatch(r'[0-9a-fA-F]{40,64}', str(baseline.get('head', ''))):
        raise ValueError('Build/test receipt needs an exact clean revision baseline.')
    summary = clean_text(receipt.get('summary', receipt['kind'] + ' ' + receipt['result']), 120)
    try:
        git_age = time.time() - timestamp(git.get('observedAt'))
    except ValueError:
        git_age = float('inf')
    fresh_git = git.get('status') == 'available' and 0 <= git_age <= GIT_OBSERVATION_TTL
    current = fresh_git and git.get('dirty') is False and git.get('head') == baseline['head']
    return dict(status=receipt['result'] if current else 'unknown', source='Recorded ' + receipt['kind'] + ' result',
                observedAt=observed, baseline=baseline, summary=summary if current else
                    'Previous result; current working state differs' if fresh_git else 'Previous result; Git observation is unavailable or stale',
                lastResult=receipt['result'], current=current)


def default_settings(runtime):
    return dict(mode='dashboard', layout='command_deck', fps=30, rotationSeconds=8, focusProject=None,
        captureRect=dict(x=0, y=0, width=240, height=240), captureEnabled=False,
        captureWindowId=None, captureWindowBounds=None, background=True,
        startAtLogin=not runtime.demo, projects=default_projects(runtime.demo))


def master_configuration(runtime, root=None):
    root = root or owned_directory(Path(runtime.state) / 'master', 'application')
    def owner_check():
        owned_directory(root, 'application', create=False)
        return read_json(root / 'OWNER.json')
    def validate(value):
        if not isinstance(value, dict) or set(value) != {'settings', 'lifecyclePreferencesKnown'}:
            raise ValueError('Unknown MASTER configuration value.')
        if type(value['lifecyclePreferencesKnown']) is not bool:
            raise ValueError('Invalid lifecycle preference availability.')
        settings = dict(default_settings(runtime), **MasterService._validate_settings(value['settings']))
        if settings['focusProject'] is not None and settings['focusProject'] not in {p['id'] for p in settings['projects']}:
            raise ValueError('Select a registered project.')
        if settings['captureEnabled'] and (not settings.get('captureWindowId') or not settings.get('captureWindowBounds')):
            raise ValueError('Select and calibrate a visible mini window first.')
        return dict(settings=settings, lifecyclePreferencesKnown=value['lifecyclePreferencesKnown'])
    def legacy(raw):
        if not isinstance(raw, dict) or set(raw) != {'format', 'settings'} or raw.get('format') != 'interlinked-master-settings-v1':
            raise ValueError('Unknown MASTER settings format.')
        return validate(dict(settings=raw['settings'], lifecyclePreferencesKnown=True))
    config = configuration_module()
    return config.ConfigStore(root / 'settings.json', 'master', owner_check, validate, legacy,
        dict(settings=default_settings(runtime), lifecyclePreferencesKnown=True))


def safe_restore_master(value, current):
    """Historical configuration is never permission to resume capture or startup."""
    proposed = deepcopy(value)
    prior = current.get('value') if current.get('available') else None
    known = bool(prior and prior.get('lifecyclePreferencesKnown'))
    settings = proposed['settings']
    if prior:
        for field in ('background', 'startAtLogin'):
            settings[field] = prior['settings'][field]
    else:
        # These placeholders are not actionable: lifecyclePreferencesKnown stays false.
        settings.update(background=False, startAtLogin=False)
    settings.update(captureEnabled=False, captureWindowId=None, captureWindowBounds=None,
                    captureRect=dict(x=0, y=0, width=240, height=240))
    proposed['lifecyclePreferencesKnown'] = known
    return proposed


class MasterService:
    def __init__(self, runtime, controller, worker_path=None, violet_asset=None, start=True):
        self.runtime, self.controller = runtime, controller
        self.root = owned_directory(Path(runtime.state) / 'master', 'application')
        self.feed = owned_directory(self.root / 'feed' if runtime.demo else common_state_root(), 'event-feed')
        for name in ('events', 'results'):
            ordinary(self.feed / name, missing=True).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.worker_start_lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Condition()
        self.commands = deque(maxlen=32)
        self.pending_snapshot = None
        self.events = EventModel()
        self._cooler_titles = CoolerTitleResolver()
        self._cooler_display = dict(observedAt=None, titles={}, resources=[], activityAvailable=False, activityPartial=True)
        self._voice_activity = CoolerVoiceActivity()
        self.event_errors = 0
        self._frame = b''
        self._worker = None
        self._thread = None
        self._maintenance_thread = None
        self.configuration = master_configuration(runtime, self.root)
        saved = self.configuration.status()
        if not saved['available']:
            raise ValueError('MASTER configuration needs explicit recovery.')
        self._settings = deepcopy(saved['value']['settings'])
        self.settings_revision = saved['revision']
        self.lifecycle_preferences_known = saved['value']['lifecyclePreferencesKnown']
        # Reopening never silently resumes a screen capture.
        self._settings['captureEnabled'] = False
        self._write_activity_roots()
        self._state = dict(format='interlinked-master-snapshot-v1', version=1, utc=utc(), demo=runtime.demo,
            projects=[], violet=dict(status='unknown', activity='unknown', connected=False, stale=False,
            source='Dot-reported', observedAt=None, reportedAt=None, message='Violet has not published an update'),
            temperatures=dict(cpu=dict(value=None, unit='C', status='unavailable'), gpu=dict(value=None, unit='C', status='unavailable'), status='unavailable', observedAt=None),
            display=dict(status='disconnected', connected=False, message='Choose Connect display after closing TRCC', error=None),
            capture=dict(valid=False, status='unavailable', message='Live mini capture is not calibrated'), captureSources=[])
        self._state['sensorPermission'] = dict(status='unavailable', message='Enable sensors explicitly after connecting the display')
        demo_worker = getattr(runtime, 'master_worker', None) if runtime.demo else None
        self.worker_path = Path(worker_path or demo_worker) if worker_path or demo_worker else Path(runtime.kit) / 'INTERLINKED-Desktop' / 'INTERLINKED.Master.Worker.exe'
        self.violet_asset = Path(violet_asset) if violet_asset else Path(runtime.kit) / 'INTERLINKED-Desktop' / 'Violet.png'
        self._git_collector = GitCollector(self.stop)
        self._ack_sample = None
        self._request_serial = 0
        self._sensor_request_serial = 0
        self._connect_intent = False
        self._attempt_deadline = 0
        self._cleanup_deadline = 0
        self._transport_heartbeat = 0
        self._pending_failure = None
        self._recovery_armed = False
        self._recovery_suspended = False
        self._recovery_until = 0
        self._retry_index = 0
        self._retry_at = 0
        self._stable_since = 0
        self._attempt_ack_baseline = 0
        self._attempt_delivered = 0
        self._last_delivery_at = 0
        self._state['display'].update(requestId=0, desiredConnected=False,
            retryAttempt=0, retryLimit=3, nextRetryAt=None, errorCode=None, retryable=False)
        # One-time administrative setup authorizes only this exact installed
        # service/worker pair. Demo checkouts never reconnect real hardware.
        self._auto_start_until = time.monotonic() + 150 if self._automatic_startup_allowed() else 0
        self._auto_sensor_until = self._auto_start_until - 90 if self._auto_start_until else 0
        self._auto_connect_until = 0
        self._auto_connect_sent = False
        self._auto_cancel_pending = False
        self._auto_start_next = 0
        self.refresh(collect_git=False)
        if start:
            self._start_worker()
            self._thread = threading.Thread(target=self._loop, name='master-status', daemon=True)
            self._thread.start()
            self._maintenance_thread = threading.Thread(target=self._maintenance_loop, name='master-display-maintenance', daemon=True)
            self._maintenance_thread.start()

    @staticmethod
    def _validate_settings(body):
        allowed = {'mode', 'layout', 'fps', 'rotationSeconds', 'focusProject', 'captureRect', 'captureEnabled',
                   'captureWindowId', 'captureWindowBounds', 'background', 'startAtLogin', 'projects'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError('Unexpected MASTER settings fields.')
        result = deepcopy(body)
        if 'mode' in result and result['mode'] not in {'dashboard', 'live_mini'}:
            raise ValueError('Unknown display mode.')
        if 'layout' in result and result['layout'] not in {'command_deck', 'violet_web'}:
            raise ValueError('Unknown cooler layout.')
        if 'fps' in result and (type(result['fps']) is not int or result['fps'] not in (5, 10, 15, 30)):
            raise ValueError('Choose a target of 5, 10, 15 or 30 FPS.')
        if 'rotationSeconds' in result and result['rotationSeconds'] != 8:
            raise ValueError('The detail rotation interval is eight seconds.')
        for key in ('captureEnabled', 'background', 'startAtLogin'):
            if key in result and type(result[key]) is not bool:
                raise ValueError(key + ' must be boolean.')
        for key in ('captureRect', 'captureWindowBounds'):
            if key not in result or result[key] is None and key == 'captureWindowBounds':
                continue
            rectangle = result[key]
            if not isinstance(rectangle, dict) or set(rectangle) != {'x', 'y', 'width', 'height'} or any(type(v) is not int for v in rectangle.values()):
                raise ValueError('Invalid capture rectangle.')
            if not (-32768 <= rectangle['x'] <= 32768 and -32768 <= rectangle['y'] <= 32768 and 16 <= rectangle['width'] <= 4096 and 16 <= rectangle['height'] <= 4096):
                raise ValueError('Capture rectangle is outside the supported screen bounds.')
        if result.get('captureWindowId') is not None and ('captureWindowId' in result) and not re.fullmatch(r'[0-9]{1,20}', str(result['captureWindowId'])):
            raise ValueError('Select a capture window.')
        if 'captureWindowId' in result and result['captureWindowId'] is not None:
            result['captureWindowId'] = str(result['captureWindowId'])
        if 'projects' in result:
            result['projects'] = validate_projects(result['projects'])
        if 'focusProject' in result and result['focusProject'] is not None:
            clean_id(result['focusProject'], 'project ID')
        return result

    def _write_activity_roots(self):
        roots = []
        for project in self._settings['projects']:
            roots.extend(project[k] for k in ('root', 'activityRoot') if project.get(k))
        write_json(self.feed / 'activity-roots.json', dict(format='interlinked-master-activity-roots-v1', roots=roots))

    def settings(self, body):
        if not isinstance(body, dict):
            raise ValueError('MASTER settings must be an object.')
        expected = body.get('expectedRevision')
        patch = self._validate_settings({k: v for k, v in body.items() if k != 'expectedRevision'})
        with self.lock:
            current = self.configuration.status()
            if not current['available'] or not isinstance(expected, str) or expected != current['revision']:
                self._configuration_conflict(current)
            # The persisted revision is authoritative even if another reviewed
            # metadata writer has committed since this service last observed it.
            baseline = deepcopy(current['value']['settings'])
            baseline['captureEnabled'] = (self._settings['captureEnabled']
                if current['revision'] == self.settings_revision else False)
            prospective = dict(baseline, **patch)
            if prospective['focusProject'] is not None and prospective['focusProject'] not in {p['id'] for p in prospective['projects']}:
                raise ValueError('Select a registered project.')
            if patch.get('captureEnabled') and (not prospective.get('captureWindowId') or not prospective.get('captureWindowBounds')):
                raise ValueError('Select and calibrate a visible mini window first.')
            if any(k in patch for k in ('captureRect', 'captureWindowId', 'captureWindowBounds')) and 'captureEnabled' not in patch:
                prospective['captureEnabled'] = False
            known = (current['value']['lifecyclePreferencesKnown']
                if current['revision'] == self.settings_revision else False)
            known = known or {'background', 'startAtLogin'} <= set(patch)
            prior_revision = self.settings_revision
            try:
                saved = self.configuration.save(dict(settings=prospective, lifecyclePreferencesKnown=known),
                    expected_revision=expected, reason='MASTER settings changed')
            except ValueError as exc:
                if hasattr(exc, 'current'):
                    self._configuration_conflict(exc.current)
                raise
            self._settings = prospective
            self.settings_revision = saved['revision']
            self.lifecycle_preferences_known = known
            if not known or not prospective.get('startAtLogin'):
                self._cancel_automatic_startup()
            if self.settings_revision == prior_revision:
                return self.state()
            self._write_activity_roots()
        self._send(dict(op='settings', settings=self._worker_settings()))
        self.refresh(collect_git=False)
        return self.state()

    def state(self):
        with self.lock:
            value = deepcopy(self._state)
            value['settings'] = deepcopy(self._settings)
            value['settingsRevision'] = self.settings_revision
            value['settingsValid'] = True
            value['lifecyclePreferencesKnown'] = self.lifecycle_preferences_known
            # Observed configuration and the active renderer are separate. An
            # external valid revision is visible to editors immediately, while
            # polls cannot replay its capture/startup choices into runtime.
            try:
                current = self.configuration.status()
                if not current['available']:
                    raise ValueError('MASTER configuration needs explicit recovery.')
                if current['revision'] != self.settings_revision:
                    value['settings'] = deepcopy(current['value']['settings'])
                    value['settings'].update(captureEnabled=False, captureWindowId=None,
                        captureWindowBounds=None, captureRect=dict(x=0, y=0, width=240, height=240))
                    value['settingsRevision'] = current['revision']
                    value['lifecyclePreferencesKnown'] = False
                    value['restartRequired'] = True
                    value['configurationNotice'] = 'Configuration changed outside this active service; review and save or restart before it is active.'
            except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                value['settings'] = {k: deepcopy(v) for k, v in self._settings.items()
                    if k not in {'background', 'startAtLogin'}}
                value['settings']['captureEnabled'] = False
                value.update(settingsRevision=None, settingsValid=False,
                    lifecyclePreferencesKnown=False, restartRequired=True,
                    configurationNotice='MASTER configuration is unavailable; existing bytes were preserved.')
            value['eventErrors'] = self.event_errors
            return value

    def _configuration_conflict(self, current):
        error = ValueError('MASTER settings changed. Review the current values and submit again.')
        error.current = dict(settings=deepcopy(current['value']['settings']) if current.get('available') else {},
            settingsRevision=current.get('revision'), settingsValid=bool(current.get('available')),
            lifecyclePreferencesKnown=bool(current.get('available') and current['value'].get('lifecyclePreferencesKnown')))
        raise error

    def history(self):
        return self.configuration.history()

    def preview_restore(self, body):
        if not isinstance(body, dict) or set(body) != {'snapshotId'}:
            raise ValueError('Select one configuration snapshot.')
        return self.configuration.preview(body['snapshotId'], safe_restore_master)

    def restore(self, body):
        if not isinstance(body, dict) or set(body) not in ({'previewId'}, {'previewId', 'expectedRevision'}):
            raise ValueError('Select a current configuration restore preview.')
        with self.lock:
            saved = self.configuration.restore(body['previewId'], expected_revision=body.get('expectedRevision'))
            # Recovery cannot leave an earlier automatic hardware request armed.
            self._cancel_automatic_startup()
            self._suspend_recovery('MASTER configuration was restored; choose Connect display to start a new recovery session.')
            self._settings = deepcopy(saved['value']['settings'])
            self.settings_revision = saved['revision']
            self.lifecycle_preferences_known = saved['value']['lifecyclePreferencesKnown']
            self._write_activity_roots()
        self._send(dict(op='settings', settings=self._worker_settings()))
        self.refresh(collect_git=False)
        return dict(status='restored', restartRequired=False, state=self.state())

    def attention(self):
        try:
            return master_attention(self.feed)
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
            return dict(available=False, items=[], pendingCount=None,
                        error='MASTER attention metadata is busy or unavailable. Existing records were preserved.')

    def acknowledge_attention(self, body):
        result = acknowledge_attention(self.feed, body)
        return dict(result, queue=self.attention())

    def frame(self):
        with self.lock:
            return self._frame

    def voice_activity(self, body):
        return self._voice_activity.accept(body)

    def display(self, body):
        # A manual action always takes precedence over the startup retry window.
        if body in ({'action': 'enable_sensors'}, {'action': 'disable_sensors'}):
            if self.runtime.demo:
                raise ValueError('Sensor setup is unavailable in a synthetic demo.')
            enable = body['action'] == 'enable_sensors'
            with self.lock:
                self._cancel_startup_for_manual_action()
                self._sensor_request_serial += 1
                sensor_request = self._sensor_request_serial
                display_request = self._request_serial
                worker = self._worker
                if enable and not self._state['display'].get('connected'):
                    raise ValueError('Connect the cooler display before enabling its sensor reader.')
                if not enable:
                    # Revocation never starts a replacement sensor/display worker.
                    if worker is not None and worker.poll() is None:
                        self._send(dict(op='sensors', enable=False))
                    return self.state()
                if worker is None or worker.poll() is not None:
                    raise ValueError('The connected display worker is unavailable; reconnect before enabling sensors.')
            # There is normally no startup here. Retain and recheck both identities
            # in case worker availability inspection or a concurrent restart waits.
            try:
                self._ensure_worker(expected_request_id=display_request, report_failure=False)
            except (OSError, ValueError):
                with self.lock:
                    if sensor_request != self._sensor_request_serial or display_request != self._request_serial or self._worker is not worker:
                        return self.state()
                raise
            with self.lock:
                if (sensor_request == self._sensor_request_serial and display_request == self._request_serial
                        and self._worker is worker and not self.stop.is_set() and self._state['display'].get('connected')):
                    self._send(dict(op='sensors', enable=True))
        elif body == {'action': 'capture_sources'}:
            with self.lock:
                self._cancel_startup_for_manual_action()
            self._ensure_worker()
            self._send(dict(op='capture_sources'))
        elif isinstance(body, dict) and set(body) == {'connect'} and type(body['connect']) is bool:
            with self.lock:
                if body['connect'] and (self._cleanup_deadline or self._connect_intent and (self._attempt_deadline or self._retry_at or self._state['display'].get('connected'))):
                    return self.state()
                self._cancel_startup_for_manual_action(supersedes_display=True)
                self._recovery_armed = False
                self._recovery_suspended = False
                self._recovery_until = self._retry_at = self._stable_since = 0
                self._retry_index = 0
                self._pending_failure = None
                request = self._request_display(body['connect'], enqueue=False)
                if not body['connect']:
                    # Fence first; cancellation never starts a replacement.
                    worker = self._worker
                    if worker is not None and worker.poll() is None:
                        self._send(dict(op='display', connect=False))
                    else:
                        self._cleanup_deadline = 0
                        self._state['display'].update(status='disconnected', message='Display disconnected.')
            if body['connect']:
                self._finish_display_connect(request, raise_errors=True)
        else:
            raise ValueError('Display takes a boolean connect field, capture_sources, enable_sensors or disable_sensors action.')
        return self.state()

    def _cancel_startup_for_manual_action(self, supersedes_display=False):
        """Caller holds self.lock, atomically with its own intent reservation."""
        if not supersedes_display:
            self._cancel_automatic_startup()
            return
        self._auto_start_until = 0
        self._auto_connect_sent = False
        self._auto_cancel_pending = False

    def _request_display(self, connect, now=None, desired=None, recovery=False, enqueue=True):
        """Fence each transport action before publishing it to the bounded queue."""
        now = time.monotonic() if now is None else now
        with self.lock:
            self._request_serial += 1
            self._sensor_request_serial += 1
            self._connect_intent = connect if desired is None else desired
            self._attempt_deadline = now + 20 if connect else 0
            self._cleanup_deadline = 0 if connect else now + 5
            self._transport_heartbeat = now
            self._stable_since = self._last_delivery_at = 0
            self._attempt_ack_baseline = self._state['display'].get('acknowledgedFrames') or 0
            self._attempt_delivered = 0
            self._state['display'].update(requestId=self._request_serial, desiredConnected=self._connect_intent,
                connected=False, status=('reconnecting' if recovery else 'connecting') if connect else 'disconnecting',
                message='Waiting for display worker' if connect else 'Releasing owned display transport',
                error=None, errorCode=None, retryable=False, nextRetryAt=None,
                deliveredFps=None, retryAttempt=self._retry_index, retryLimit=3)
            try:
                if enqueue:
                    self._send(dict(op='display', connect=connect))
            except (OSError, ValueError):
                if connect:
                    self._attempt_deadline = 0
                    self._connect_intent = False
                    self._state['display'].update(status='unavailable', desiredConnected=False,
                        message='Display command could not be queued; choose Retry display.', errorCode='command_queue_full')
                raise
            return self._request_serial

    def _finish_display_connect(self, request, now=None, automatic=False, raise_errors=False):
        """Start outside the state lock; only the reserved request may dispatch."""
        started = time.monotonic()
        now = started if now is None else now
        try:
            self._ensure_worker(expected_request_id=request)
        except (OSError, ValueError):
            with self.lock:
                if request != self._request_serial or not self._connect_intent or self.stop.is_set():
                    return
                self._display_failed('worker_unavailable', 'Reviewed display worker could not start.', False, now)
            if raise_errors:
                raise
            return
        with self.lock:
            if request != self._request_serial or not self._connect_intent or self.stop.is_set():
                return
            # Explicit now values support fixture clocks; production measures the
            # actual elapsed startup time against the original reservation.
            checked_now = now + max(0, time.monotonic() - started)
            if automatic and (not self._auto_start_until or checked_now >= self._auto_start_until
                    or self._auto_connect_until and checked_now >= self._auto_connect_until or not self._automatic_startup_allowed()):
                self._cancel_automatic_startup()
                return
            if self._recovery_until and checked_now >= self._recovery_until:
                self._recovery_armed = False
                self._connect_intent = False
                self._cancel_failed_attempt('recovery_exhausted', 'Automatic recovery reached its time limit; choose Retry display.', False, checked_now)
                return
            if not self._attempt_deadline or checked_now >= self._attempt_deadline:
                self._cancel_failed_attempt('attempt_timeout', 'The display worker did not finish connecting.', True, checked_now)
                return
            try:
                self._send(dict(op='display', connect=True))
            except (OSError, ValueError):
                self._display_failed('command_queue_full', 'Display command could not be queued; choose Retry display.', False, checked_now)
                if raise_errors:
                    raise

    def _display_failed(self, code, message, retryable=False, now=None):
        """Called only after owned cleanup or confirmed worker exit."""
        now = time.monotonic() if now is None else now
        with self.lock:
            self._attempt_deadline = self._cleanup_deadline = self._stable_since = 0
            self._pending_failure = None
            self._state['display'].update(connected=False, deliveredFps=None, errorCode=code,
                error=message, message=message, retryable=bool(retryable))
            if self._connect_intent and self._recovery_armed and retryable and not self.stop.is_set():
                if not self._recovery_until:
                    self._recovery_until = now + 75
                if self._retry_index < 3 and now < self._recovery_until:
                    delay = (2, 5, 10)[self._retry_index]
                    self._retry_index += 1
                    self._retry_at = min(now + delay, self._recovery_until)
                    next_at = datetime.fromtimestamp(time.time() + self._retry_at - now, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                    self._state['display'].update(status='retry_wait', desiredConnected=True,
                        retryAttempt=self._retry_index, nextRetryAt=next_at)
                    return
                code = 'recovery_exhausted'
                self._state['display'].update(errorCode=code, message=message + ' Automatic recovery ended; choose Retry display.')
            self._retry_at = self._recovery_until = 0
            self._connect_intent = False
            self._state['display'].update(status='unavailable', desiredConnected=False, nextRetryAt=None)

    def _suspend_recovery(self, message):
        with self.lock:
            pending = bool(self._retry_at or self._recovery_until and self._attempt_deadline)
            self._recovery_suspended = True
            self._recovery_armed = False
            self._retry_at = self._recovery_until = 0
            if self._pending_failure:
                self._pending_failure = ('configuration_changed', message, False)
                self._connect_intent = False
                self._state['display']['desiredConnected'] = False
            elif pending:
                self._connect_intent = False
                self._cancel_failed_attempt('configuration_changed', message, False)

    def _cancel_failed_attempt(self, code, message, retryable=True, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self._pending_failure = (code, message, retryable)
            self._retry_at = 0
            desired = self._connect_intent
            # Queue saturation cannot discard cancellation. The watchdog retires
            # only this owned worker if it cannot acknowledge cleanup in time.
            try:
                self._request_display(False, now, desired=desired)
            except (OSError, ValueError):
                pass
            self._state['display'].update(status='cleaning_up', errorCode=code, error=message, message=message)

    def _retire_worker(self, worker):
        """Fence and stop only this service's exact owned child, then permit retry."""
        with self.worker_start_lock:
            if worker is None or self._worker is not worker:
                return
            # Fence old frame/status threads before waiting for process cleanup.
            with self.lock:
                self._worker = None
            with self.wake:
                self.commands.clear(); self.pending_snapshot = None; self.wake.notify_all()
            try:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                # A live old worker remains authoritative; never overlap writers.
                with self.lock:
                    self._worker = worker
                raise ValueError('Owned display worker did not finish cleanup.') from None
            try:
                if worker.stdin:
                    worker.stdin.close()
            except (OSError, ValueError):
                pass
            with self.lock:
                self._frame = b''
                self._state['display']['frameObservedAt'] = None

    def _display_tick(self, now=None):
        now = time.monotonic() if now is None else now
        retire = None
        with self.lock:
            if self._cleanup_deadline and now >= self._cleanup_deadline:
                retire = self._worker
                failure_request = self._request_serial
                failure = self._pending_failure or ('worker_unresponsive', 'Display cleanup did not respond.', True)
                self._cleanup_deadline = 0
            else:
                failure = None
            if not failure and self._recovery_until and now >= self._recovery_until and self._connect_intent:
                self._recovery_armed = False
                self._retry_at = self._recovery_until = 0
                self._connect_intent = False
                self._cancel_failed_attempt('recovery_exhausted', 'Automatic recovery reached its time limit; choose Retry display.', False, now)
                return
            if not failure and self._attempt_deadline and now >= self._attempt_deadline:
                self._cancel_failed_attempt('attempt_timeout', 'The display worker did not finish connecting.', True, now)
                return
            if not failure and self._state['display'].get('connected') and self._connect_intent:
                if now - self._transport_heartbeat > 5:
                    self._cancel_failed_attempt('transport_unresponsive', 'The USB transport stopped responding.', True, now)
                    return
                if self._stable_since and now - self._stable_since >= 30 and now - self._last_delivery_at <= 5:
                    self._retry_index = 0; self._recovery_until = 0
                    self._state['display'].update(retryAttempt=0, nextRetryAt=None)
            retry = bool(not failure and not self._cleanup_deadline and self._pending_failure is None
                         and self._retry_at and now >= self._retry_at)
            if retry:
                self._retry_at = 0
                request = self._request_display(True, now, desired=True, recovery=True, enqueue=False)
        if failure:
            try:
                self._retire_worker(retire)
            except (OSError, ValueError) as exc:
                with self.lock:
                    if failure_request == self._request_serial:
                        self._display_failed('cleanup_failed', str(exc), False, now)
                return
            with self.lock:
                if failure_request == self._request_serial:
                    self._display_failed(*failure, now=now)
        elif retry:
            self._finish_display_connect(request, now)

    def _worker_settings(self):
        with self.lock:
            settings = {k: deepcopy(v) for k, v in self._settings.items() if k not in {'projects', 'background', 'startAtLogin'}}
        if self.violet_asset.is_file():
            settings['violetAtlasPath'] = str(ordinary(self.violet_asset))
        try:
            device = read_json(self.root / 'device.json', 8192)
            pin = device.get('helperSha256')
            if device.get('format') == 'interlinked-master-device-v1' and isinstance(pin, str) and re.fullmatch(r'[0-9a-fA-F]{64}', pin):
                settings['helperSha256'] = pin.lower()
            pins = device.get('helperPins')
            allowed = {'USBLCDNEW.exe', 'USBLCDNEW.dll', 'USBLCDNEW.runtimeconfig.json', 'LibUsbDotNet.LibUsbDotNet.dll', 'HWINFO.exe', 'HWiNFO64.dll'}
            if device.get('format') == 'interlinked-master-device-v1' and isinstance(pins, dict) and set(pins) <= allowed and all(isinstance(v, str) and re.fullmatch(r'[0-9a-fA-F]{64}', v) for v in pins.values()):
                settings['helperPins'] = {k: v.lower() for k, v in pins.items()}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        # Production output requires the complete reviewed transport dependency
        # set. No HTTP setting can lower this policy or supply arbitrary paths.
        settings['requireHelperPins'] = not self.runtime.demo
        return settings

    def _ensure_worker(self, expected_request_id=None, report_failure=True):
        with self.lock:
            expected_request_id = self._request_serial if expected_request_id is None else expected_request_id
        if self._worker is None or self._worker.poll() is not None:
            self._start_worker(expected_request_id, report_failure)
        if self._worker is None:
            raise ValueError('MASTER display worker is not installed. Build and publish its reviewed package first.')

    def _start_worker(self, expected_request_id=None, report_failure=True):
        # HTTP handlers may request a restart concurrently. Keep one owned
        # renderer, so no request can orphan a second display writer.
        with self.lock:
            expected_request_id = self._request_serial if expected_request_id is None else expected_request_id
        with self.worker_start_lock:
            self._start_worker_locked(expected_request_id, report_failure)

    def _start_worker_locked(self, expected_request_id=None, report_failure=True):
        with self.lock:
            if expected_request_id is not None and expected_request_id != self._request_serial:
                return
        if self.stop.is_set() or self._worker is not None and self._worker.poll() is None:
            return
        try:
            executable = ordinary(self.worker_path)
            if not self.runtime.demo:
                expected = ordinary(Path(self.runtime.kit) / 'INTERLINKED-Desktop' / 'INTERLINKED.Master.Worker.exe')
                if executable != expected:
                    raise ValueError('Use the installed reviewed display worker.')
                seal = read_json(expected.parent / 'DESKTOP-CHECKSUMS.json', 65536)
                if seal.get('format') not in {'interlinked-desktop-checksums-v1', 'interlinked-desktop-checksums-v2'}:
                    raise ValueError('Unknown display worker seal.')
                for name in ('INTERLINKED.Master.Worker.exe', 'INTERLINKED.Master.Worker.exe.config'):
                    pinned = seal.get('files', {}).get(name)
                    artifact = ordinary(expected.parent / name)
                    if not isinstance(pinned, str) or not re.fullmatch(r'[a-f0-9]{64}', pinned) or hashlib.sha256(artifact.read_bytes()).hexdigest() != pinned:
                        raise ValueError('Display worker differs from its reviewed package.')
            settings = self._worker_settings()
            with self.wake:
                # Restarted workers never replay an old Connect/capture request.
                self.commands.clear()
                self.pending_snapshot = None
            worker = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=str(executable.parent))
            with self.lock:
                self._worker = worker
            threading.Thread(target=self._read_frames, args=(worker,), daemon=True, name='master-frames').start()
            threading.Thread(target=self._read_status, args=(worker,), daemon=True, name='master-worker-status').start()
            threading.Thread(target=self._write_worker, args=(worker,), daemon=True, name='master-worker-input').start()
            self._send(dict(op='settings', settings=settings))
            self._send(dict(op='snapshot', snapshot=self.state()))
        except (OSError, ValueError):
            with self.lock:
                self._worker = None
                if report_failure and expected_request_id == self._request_serial and not self.stop.is_set():
                    self._state['display'].update(status='unavailable', connected=False, message='Reviewed display worker is not installed', error=None)

    def _connection_activity(self):
        """Observe bounded recovery without changing transport state or intent."""
        with self.lock:
            now = time.monotonic()
            pending = (self._pending_failure is not None and self._cleanup_deadline > now
                or self._recovery_until > now and (self._retry_at > 0 or self._attempt_deadline > now))
            if self._connect_intent and self._recovery_armed and not self._recovery_suspended and pending:
                return dict(source='cooler_transport', phase='reconnecting',
                    requestId=self._request_serial, observedAt=utc())
        return None

    def _send(self, value):
        if value['op'] == 'display':
            value = dict(value, requestId=self._request_serial)
        elif value['op'] == 'snapshot':
            # Titles/resources/activity are display-only: never mutate public state or
            # persist the private enrichment in snapshot.json or plugin feeds.
            snapshot = deepcopy(value['snapshot'])
            with self.lock:
                private = deepcopy(self._cooler_display)
                private['connectionActivity'] = self._connection_activity()
            private['voiceActivity'] = self._voice_activity.snapshot()
            snapshot['coolerDisplay'] = private
            value = dict(value, snapshot=snapshot)
        with self.wake:
            if value['op'] == 'snapshot':
                self.pending_snapshot = value
            else:
                if len(self.commands) == self.commands.maxlen:
                    raise ValueError('Display worker is busy; try again shortly.')
                self.commands.append(value)
            self.wake.notify_all()

    def _write_worker(self, worker):
        try:
            while self._worker is worker and worker.poll() is None:
                with self.wake:
                    if not self.commands and self.pending_snapshot is None:
                        self.wake.wait(1)
                    if self._worker is not worker:
                        return
                    value = self.commands.popleft() if self.commands else self.pending_snapshot
                    if value is self.pending_snapshot:
                        self.pending_snapshot = None
                if value is not None:
                    worker.stdin.write(json.dumps(value, separators=(',', ':')).encode('utf-8') + b'\n')
                    worker.stdin.flush()
                    if value['op'] == 'quit':
                        return
        except (BrokenPipeError, OSError, ValueError):
            with self.lock:
                if self._worker is worker and not self.stop.is_set():
                    self._cancel_failed_attempt('command_pipe_failed', 'The display command pipe stopped responding.', True)
            return

    @staticmethod
    def _read_exact(stream, length):
        chunks = bytearray()
        while len(chunks) < length:
            part = stream.read(length - len(chunks))
            if not part:
                raise EOFError()
            chunks.extend(part)
        return bytes(chunks)

    def _read_frames(self, worker):
        try:
            # Drain until process EOF even during shutdown so a full stdout pipe
            # cannot prevent the worker from reaching its graceful quit path.
            while worker.poll() is None:
                length = struct.unpack('<I', self._read_exact(worker.stdout, 4))[0]
                if not 4 <= length <= MAX_FRAME:
                    raise ValueError('Invalid worker frame size.')
                picture = self._read_exact(worker.stdout, length)
                if picture[:2] != b'\xff\xd8' or picture[-2:] != b'\xff\xd9':
                    raise ValueError('Invalid worker JPEG.')
                with self.lock:
                    if self._worker is worker:
                        self._frame = picture
                        self._state['display']['frameObservedAt'] = utc()
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self.lock:
                if self._worker is worker:
                    self._frame = b''
                    self._state['display']['frameObservedAt'] = None
                    if not self.stop.is_set():
                        self._cancel_failed_attempt('worker_stopped', 'Display worker stopped; project workflows are unaffected.', True)

    def _read_status(self, worker):
        try:
            while worker.poll() is None:
                line = worker.stderr.readline(65537)
                if not line:
                    return
                if len(line) > 65536:
                    continue
                try:
                    message = json.loads(line)
                    self.accept_worker_status(message, worker=worker)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
        except OSError:
            return

    def accept_worker_status(self, message, worker=None):
        if not isinstance(message, dict):
            return
        kind = message.get('type')
        with self.lock:
            if worker is not None and self._worker is not worker:
                return
            if kind in {'status', 'stats', 'transport', 'delivery'}:
                request_id = message.get('requestId')
                if request_id is None and self._request_serial == 0:
                    request_id = 0  # Initial preview-only protocol observations.
                if type(request_id) is not int or request_id != self._request_serial:
                    return
                if kind == 'transport':
                    self._transport_heartbeat = time.monotonic()
                    return
                if kind == 'delivery':
                    count = message.get('attemptAcknowledgedFrames')
                    if type(count) is int and count > 0 and self._connect_intent and self._state['display'].get('connected'):
                        now = time.monotonic()
                        self._recovery_armed = not self._recovery_suspended
                        self._attempt_deadline = 0
                        self._stable_since = self._last_delivery_at = now
                        self._attempt_delivered = count
                        self._state['display']['attemptAcknowledgedFrames'] = count
                    return
            if kind in {'status', 'stats'}:
                allowed = {'connected', 'displayStatus', 'error', 'errorCode', 'retryable', 'message', 'fps', 'acknowledgedFrames', 'attemptAcknowledgedFrames', 'droppedFrames', 'ackLatencyMs', 'encodeMs', 'frameBytes', 'cpuPercent', 'memoryBytes', 'renderFps', 'encodedBytes', 'ackMs', 'workingSetBytes', 'cpuTotalMs', 'frameSequence'}
                self._state['display'].update({k: v for k, v in message.items() if k in allowed and isinstance(v, (str, int, float, bool, type(None)))})
                if 'displayStatus' in message:
                    self._state['display']['status'] = message['displayStatus']
                    if 'message' not in message:
                        # The worker status supersedes the pending
                        # request text, so the UI can show its actual error.
                        self._state['display']['message'] = None
                    if message['displayStatus'] == 'connected':
                        self._transport_heartbeat = time.monotonic()
                    elif message['displayStatus'] in {'unavailable', 'disconnected'}:
                        if message.get('cleanupComplete') is False:
                            self._cancel_failed_attempt('cleanup_failed', message.get('error') or 'Display cleanup needs review.', False)
                            return
                        if self._pending_failure:
                            failure = self._pending_failure
                            self._display_failed(*failure)
                        elif message['displayStatus'] == 'unavailable':
                            if message.get('retryable') is not True:
                                # An ownership or integrity refusal cannot become
                                # an automatic sign-in acquisition loop.
                                self._auto_start_until = 0
                                self._auto_connect_sent = self._auto_cancel_pending = False
                            self._display_failed(message.get('errorCode') or 'transport_failed',
                                message.get('message') or message.get('error') or 'Display connection failed.', message.get('retryable') is True)
                        else:
                            self._attempt_deadline = self._cleanup_deadline = 0
                            self._state['display']['desiredConnected'] = self._connect_intent
                if kind == 'stats':
                    self._state['display']['targetFps'] = message.get('fps')
                    self._state['display']['fps'] = message.get('renderFps')
                    count = message.get('acknowledgedFrames')
                    if type(count) is int:
                        now = time.monotonic()
                        if self._ack_sample and count >= self._ack_sample[1] and self._state['display'].get('connected'):
                            self._state['display']['deliveredFps'] = (count-self._ack_sample[1])/max(.001,now-self._ack_sample[0])
                        else:
                            self._state['display']['deliveredFps'] = None
                        self._ack_sample = (now,count)
                        attempt_count = message.get('attemptAcknowledgedFrames')
                        if (self._state['display'].get('connected') and type(attempt_count) is int
                                and attempt_count > self._attempt_delivered and self._connect_intent):
                            self._recovery_armed = not self._recovery_suspended
                            self._attempt_deadline = 0
                            if count > self._attempt_ack_baseline and (not self._stable_since or now - self._last_delivery_at > 5):
                                self._stable_since = now
                            self._last_delivery_at = now
                            self._attempt_ack_baseline = count
                            self._attempt_delivered = attempt_count
            elif kind == 'sensors':
                payload = message.get('temperatures', message)
                for key in ('cpu', 'gpu'):
                    item = payload.get(key)
                    value = item.get('value') if isinstance(item, dict) else item
                    observed = (item.get('observedAt') or payload.get('observedAt')) if isinstance(item, dict) else payload.get('observedAt')
                    fresh = isinstance(observed, str) and 0 <= time.time() - timestamp(observed) <= 5
                    verified = message.get('verified') is True or isinstance(item, dict) and item.get('status') in {'available', 'verified', 'live'}
                    valid = isinstance(value, (int, float)) and not isinstance(value, bool) and -20 <= value <= 150 and verified and fresh
                    self._state['temperatures'][key] = dict(value=value if valid else None, unit='C', status='available' if valid else 'unavailable', observedAt=observed if valid else None)
                self._state['temperatures'].update(status='available' if any(self._state['temperatures'][k]['value'] is not None for k in ('cpu', 'gpu')) else 'unavailable', observedAt=utc())
                if isinstance(payload.get('reason'), str):
                    self._state['temperatures']['reason'] = payload['reason'][:200]
                else:
                    self._state['temperatures'].pop('reason', None)
            elif kind == 'sensor_permission':
                status = message.get('status')
                if status in {'requesting', 'running', 'canceled', 'unavailable', 'stopped'}:
                    self._state['sensorPermission'] = dict(status=status, message=str(message.get('message', ''))[:200])
            elif kind == 'capture':
                self._state['capture'] = {k: deepcopy(message[k]) for k in ('valid', 'status', 'message') if k in message}
            elif kind == 'capture_sources':
                sources = message.get('sources', [])
                if isinstance(sources, list):
                    self._state['captureSources'] = deepcopy(sources[:100])

    def _consume_events(self):
        for path in sorted((self.feed / 'events').glob('*.json'))[:256]:
            try:
                value = read_json(path, 8192)
                self.events.accept(value)
            except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
                self.event_errors += 1
                self.events.subagent_coverage_lost = True
            try:
                ordinary(path).unlink()
            except (OSError, ValueError):
                pass

    def _violet(self):
        missing = dict(status='unknown', activity='unknown', connected=False, stale=False, source='Dot-reported',
            observedAt=None, reportedAt=None, message='Violet has not published an update')
        try:
            report = read_json(self.feed / 'violet.json', 8192)
            if report.get('format') not in ('interlinked-violet-status-v1', 'interlinked-violet-status-v2') or report.get('status') not in ACTIVITY:
                return missing
            reporter = report.get('reporter', 'unverified') if report['format'].endswith('-v2') else 'unverified'
            if not isinstance(reporter, str) or reporter not in REPORTERS:
                return missing
            stale = not 0 <= time.time() - timestamp(report['observedAt']) <= VIOLET_TTL
            return dict(status='unknown' if stale else report['status'], activity='unknown' if stale else report['status'],
                connected=not stale and reporter in ('dot', 'local_task'), stale=stale, source=REPORTERS[reporter], reporter=reporter,
                connectionRoute='direct_report' if reporter == 'dot' else 'delegated_local_task' if reporter == 'local_task' else 'unverified',
                observedAt=report['observedAt'], reportedAt=report['observedAt'],
                message='Violet update is stale' if stale else clean_text(report.get('message', ''), 120))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return missing

    def refresh(self, collect_git=True):
        # Settings handlers and the passive loop share the event model.
        with self.refresh_lock:
            self._refresh_locked(collect_git)

    def _collect_cooler_display(self, graph, registrations, ai_summary):
        now = time.time()
        nodes = {row.get('nodeId'): row for row in graph.get('nodes', []) if isinstance(row, dict)}
        candidates = {}
        for (session, role), event in self.events.sessions.items():
            node_id = lifecycle_node_id('main', session)
            row = nodes.get(node_id, {}) if role == 'main' else {}
            if row.get('kind') != 'codex_main' or not row.get('projectId'):
                continue
            age = now - timestamp(row['observedAt'])
            if row.get('confidence') == 'observed' and 0 <= age <= EVENT_TTL and (
                    row.get('status') in ('working', 'waiting') or row.get('status') == 'turn_finished' and age < 3):
                candidates[node_id] = session
        for parent, child in self.events.subagents:
            node_id = lifecycle_node_id('child', parent, child)
            row = nodes.get(node_id, {})
            if not row.get('projectId') or row.get('confidence') != 'observed':
                continue
            age = now - timestamp(row['observedAt'])
            if 0 <= age <= EVENT_TTL and (row.get('status') in ('working', 'waiting') or
                    row.get('status') == 'turn_finished' and age < 3):
                candidates[node_id] = child
        titles = {} if self.runtime.demo else self._cooler_titles.resolve(candidates)
        resources = []
        ai_service = getattr(self, 'ai_service', None)
        if ai_service is not None and hasattr(ai_service, 'cooler_resources'):
            try:
                raw = ai_service.cooler_resources()
                if isinstance(raw, dict):
                    raw = raw.get('hosts', raw.get('resources', []))
                seen_hosts = set()
                for host in raw if isinstance(raw, list) else []:
                    if not isinstance(host, dict) or host.get('id') not in ('local', 'msi') or host['id'] in seen_hosts:
                        continue
                    seen_hosts.add(host['id'])
                    safe = dict(id=host['id'])
                    gpu_id = host.get('gpuId')
                    if isinstance(gpu_id, str) and re.fullmatch(r'GPU-[A-Za-z0-9-]{1,80}', gpu_id):
                        safe['gpuId'] = gpu_id
                    for key in ('cpuTemperature', 'gpuTemperature', 'gpuLoad', 'vramUsed', 'vramTotal'):
                        field = host.get(key)
                        if not isinstance(field, dict):
                            continue
                        value = field.get('value')
                        if value is not None and (type(value) not in (int, float) or not 0 <= value < float('inf')):
                            continue
                        source = field.get('source')
                        source = source if isinstance(source, str) and source in {'nvidia-smi', 'reviewed-sensor-publisher', 'local-sensor-map'} else 'unavailable'
                        observed = field.get('observedAt')
                        try:
                            age = now - timestamp(observed)
                        except ValueError:
                            observed, age = None, -1
                        valid = source != 'unavailable' and 0 <= age <= 5 and field.get('status') in ('live', 'available', 'verified')
                        safe[key] = dict(value=value if valid else None, status='available' if valid else 'unavailable',
                                         source=source, observedAt=observed)
                    resources.append(safe)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        covered = set()
        for row in nodes.values():
            try:
                if row.get('confidence') == 'observed' and 0 <= now - timestamp(row['observedAt']) <= EVENT_TTL:
                    covered.add(row.get('projectId'))
            except (ValueError, KeyError):
                continue
        try:
            ai_available = bool(ai_summary.get('available')) and 0 <= now - timestamp(ai_summary.get('observedAt')) <= 15
        except ValueError:
            ai_available = False
        return dict(observedAt=utc(), titles=titles, resources=resources,
                    activityAvailable=bool(covered) or ai_available,
                    activityPartial=bool(graph.get('partial') or ai_summary.get('partial') or self.event_errors or
                        any(row['id'] not in covered for row in registrations)))

    def _refresh_locked(self, collect_git=True):
        self._consume_events()
        with self.lock:
            settings = deepcopy(self._settings)
        if collect_git and not self.runtime.demo:
            self._git_collector.schedule(settings['projects'])
        git_observations = self._git_collector.snapshot(settings['projects'])
        # A reduced controller snapshot, without output, does not recurse into MASTER.
        try:
            operation = self.controller.state()
            controller_observed = utc() if isinstance(operation, dict) else None
            if not controller_observed:
                operation = {}
        except Exception:
            operation = {}
            controller_observed = None
        subagents_by_project, subagents = self.events.subagent_counts(settings['projects'])
        lifecycle_graph = self.events.lifecycle_graph(settings['projects'])
        # AI may inspect coordinator locks. Never call it while holding MASTER's
        # lock: AI request paths may separately read MASTER registrations.
        ai_summary = dict(available=False, observedAt=None, partial=True, hosts=[], flows=[], pulses=[], otherActiveCount=0)
        ai_service = getattr(self, 'ai_service', None)
        if ai_service is not None and hasattr(ai_service, 'observation_summary'):
            try:
                observed_ai = ai_service.observation_summary()
                if isinstance(observed_ai, dict):
                    ai_summary = deepcopy(observed_ai)
            except (ValueError, OSError, TypeError, KeyError):
                pass
        projects = []
        for registration in settings['projects']:
            identifier = registration['id']
            activity = self.events.activity([registration.get('activityRoot'), registration['root']])
            git = git_observations[identifier]
            if git.get('status') == 'available':
                try:
                    fresh_git = 0 <= time.time() - timestamp(git.get('observedAt')) <= GIT_OBSERVATION_TTL
                except ValueError:
                    fresh_git = False
                if not fresh_git:
                    git.update(status='stale', stale=True)
            receipt = None
            try:
                receipt = read_json(self.feed / 'results' / (identifier + '.json'), 16384)
                if receipt.get('projectId') != identifier:
                    raise ValueError('Result belongs to another project.')
                health = receipt_health(receipt, git)
            except FileNotFoundError:
                health = receipt_health(None, git)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                health = dict(status='unknown', source='Invalid result', observedAt=None, baseline=None, summary='Build/test evidence needs review')
            workspace = dict(status='unknown', summary='No workspace observation')
            if identifier == 'interlinked':
                workspace = (dict(status='busy' if operation.get('busy') else 'idle',
                    summary='Workspace operation active' if operation.get('busy') else 'INTERLINKED ready',
                    source='INTERLINKED controller', observedAt=controller_observed) if controller_observed else
                    dict(status='unknown', summary='INTERLINKED controller is unavailable', source='INTERLINKED controller', observedAt=None))
            elif identifier == 'mw5-mod' and (operation.get('activeSession') or {}).get('kind', '').startswith('mw5'):
                workspace = dict(status='editing', summary='Managed MW5 session active', source='INTERLINKED controller', observedAt=controller_observed)
            elif identifier == 'the-stats' and controller_observed:
                status=(operation.get('statsSession') or {}).get('status','unknown')
                workspace=dict(status=status.lower(),summary='The Stats '+status.lower().replace('_',' '),source='INTERLINKED Stats supervisor',observedAt=controller_observed)
            elif identifier == 'ed-it-buddy':
                try:
                    buddy_path = self.feed / 'ed-it-buddy.json' if self.runtime.demo else Path(os.environ.get('LOCALAPPDATA', '')) / 'ED-IT Buddy/master-status/status.json'
                    if not self.runtime.demo:
                        owner = read_json(buddy_path.parent / 'OWNER.json', 8192)
                        sid = current_user_sid()
                        if not sid or owner.get('format') != 'edit-buddy-master-status-owner-v1' or owner.get('userSid') != sid or str(owner.get('machine', '')).casefold() != os.environ.get('COMPUTERNAME', '').casefold():
                            raise ValueError('ED-IT Buddy publisher ownership differs.')
                    buddy = read_json(buddy_path, 16384)
                    age = time.time() - timestamp(buddy['observedAt'])
                    if buddy.get('format') == 'interlinked-buddy-status-v1' and buddy.get('host') == 'MSI' and 0 <= age <= 15:
                        job = buddy.get('current_job') or {}
                        online = buddy.get('online') is True
                        status = 'offline' if not online else clean_text(str(job.get('status') or 'idle'), 32)
                        step = job.get('current_step')
                        summary = 'ED-IT Buddy offline' if not online else 'ED-IT Buddy ready' if not job else 'Job ' + status + (' / step ' + str(step) if type(step) is int else '')
                        workspace = dict(status=status, summary=summary, observedAt=buddy['observedAt'], source='ED-IT Buddy coordinator')
                    else:
                        workspace = dict(status='offline', summary='ED-IT Buddy status is stale')
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    workspace = dict(status='offline', summary='ED-IT Buddy status publisher is not connected')
            active_ai = [row for row in ai_summary.get('flows', []) if row.get('projectId') == identifier
                         and row.get('phase') in {'queued', 'preparing', 'working', 'reviewing', 'applying', 'request_pending', 'running'}]
            if ai_summary.get('available') and active_ai:
                workspace = dict(status='working', summary='INTERLINKED AI: ' + str(len(active_ai)) + ' assigned flow(s)',
                    observedAt=ai_summary.get('observedAt'), source='INTERLINKED AI coordinator')
            projects.append(dict(id=identifier, label=registration['label'], activity=activity, health=health, git=git,
                workspace=workspace, subagents=subagents_by_project[identifier],
                source=activity['source'], observedAt=activity['observedAt']))
        try:
            attention = collect_attention(self.feed, projects)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            attention = dict(available=False, items=[], pendingCount=0, delivery='Attention queue unavailable')
        cooler_display = self._collect_cooler_display(lifecycle_graph, settings['projects'], ai_summary)
        with self.lock:
            self._cooler_display = cooler_display
            self._state.update(utc=utc(), projects=projects, subagents=subagents, lifecycleGraph=lifecycle_graph,
                               ai=ai_summary, violet=self._violet(), attention=attention)
            observed = self._state['temperatures'].get('observedAt')
            if observed and time.time() - timestamp(observed) > 5:
                self._state['temperatures'] = dict(cpu=dict(value=None, unit='C', status='unavailable'), gpu=dict(value=None, unit='C', status='unavailable'), status='stale', observedAt=observed)
            value = self.state()
            if not value['settingsValid'] or value.get('restartRequired'):
                self._suspend_recovery('MASTER configuration changed; review it before starting another recovery session.')
                value = self.state()
        export = {k: deepcopy(value[k]) for k in ('format', 'version', 'utc', 'demo', 'projects', 'subagents', 'lifecycleGraph', 'ai', 'violet', 'temperatures', 'attention')}
        write_json(self.feed / 'snapshot.json', export)
        if self._worker is not None and self._worker.poll() is None:
            self._send(dict(op='snapshot', snapshot=value))

    def _automatic_startup_allowed(self):
        if self.runtime.demo:
            return False
        observed = self.state()
        if not observed['settingsValid'] or not observed['lifecyclePreferencesKnown'] or not observed['settings'].get('startAtLogin'):
            return False
        expected = Path(r'C:\Users\Jude\Documents\MSI-Project-Library')
        if Path(self.runtime.kit) != expected:
            return False
        try:
            record = read_json(Path(r'C:\Program Files\INTERLINKED Cooler Startup\startup.json'), 8192)
            return (record.get('format') == 'interlinked-cooler-startup-v1'
                    and record.get('enabled') is True and record.get('machine') == 'THIS-MACHINE'
                    and record.get('user') == 'Jude'
                    and record.get('appSha256') == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                    and record.get('workerSha256') == hashlib.sha256(self.worker_path.read_bytes()).hexdigest())
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def _cancel_automatic_startup(self):
        self._auto_start_until = 0
        if self._auto_connect_sent and not self._state['display'].get('connected'):
            self._auto_cancel_pending = True
        if self._auto_cancel_pending:
            try:
                self._request_display(False)
            except ValueError:
                # A full bounded queue cannot discard the cancellation. Retry
                # it on the next tick without rearming automatic connection.
                return
            self._auto_cancel_pending = False
        self._auto_connect_sent = False

    def _automatic_startup_tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if self._auto_cancel_pending:
                self._cancel_automatic_startup()
                return
            if not self._auto_start_until:
                return
            if (not self._automatic_startup_allowed() or now >= self._auto_start_until
                    or self._auto_connect_until and now >= self._auto_connect_until
                    or self._state['display'].get('connected')):
                self._cancel_automatic_startup()
                return
            # A startup retry cannot supersede an attempt or its cleanup.
            # Maintenance owns the watchdog and recovery deadlines; preserve
            # their cancellation request until cleanup or confirmed exit.
            if (self._cleanup_deadline or self._pending_failure is not None
                    or self._attempt_deadline or self._retry_at or self._recovery_until
                    or now < self._auto_start_next
                    or self._state['display'].get('status') in {'connecting', 'reconnecting', 'cleaning_up', 'disconnecting'}):
                return
            # Let the scheduled publisher initialize first, avoiding a competing
            # normal-user vendor publisher. Display still starts if sensors fail.
            if self._state['temperatures'].get('status') != 'available' and now < self._auto_sensor_until:
                return
            if not self._auto_connect_until:
                self._auto_connect_until = now + 60
            self._auto_start_next = now + 5
            self._auto_connect_sent = True
            request = self._request_display(True, now, enqueue=False)
            self._state['display']['message'] = 'Connecting the cooler after sign-in'
        self._finish_display_connect(request, now, automatic=True)

    def _loop(self):
        while not self.stop.wait(1):
            try:
                self.refresh()
            except (ValueError, OSError):
                self.event_errors += 1

    def _maintenance_loop(self):
        # Git, AI collection and snapshot storage are deliberately absent here.
        # Their failures/delays cannot extend a connection or cleanup deadline.
        while not self.stop.wait(.25):
            try:
                self._display_tick()
            except (ValueError, OSError):
                self.event_errors += 1
            try:
                self._automatic_startup_tick()
            except (ValueError, OSError):
                self.event_errors += 1

    def close(self):
        self.stop.set()
        self._git_collector.close()
        if self._maintenance_thread is not None:
            self._maintenance_thread.join(timeout=3)
        with self.worker_start_lock:
            if self._worker is not None and self._worker.poll() is None:
                try:
                    self._send(dict(op='quit'))
                    self._worker.wait(timeout=5)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    # Only the worker owned by this service is eligible for termination.
                    self._worker.kill()
                    self._worker.wait(timeout=2)
        if self._thread is not None:
            self._thread.join(timeout=1)


class UnavailableMaster:
    """A broken display configuration cannot prevent the library from opening."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.demo = bool(runtime.demo)
        self.configuration = None
        self.restart_required = False
        try:
            root = owned_directory(Path(runtime.state) / 'master', 'application', create=False)
            self.configuration = master_configuration(runtime, root)
        except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
            # Recovery never creates ownership over an untrusted or unsafe directory.
            pass
        self.feed = Path(runtime.state) / 'master' / 'feed' if runtime.demo else common_state_root()

    def state(self):
        return dict(format='interlinked-master-snapshot-v1', version=1, utc=utc(), demo=self.demo,
            projects=[], settings=dict(mode='dashboard', fps=30, rotationSeconds=8,
                captureEnabled=False, projects=[]), settingsValid=False, lifecyclePreferencesKnown=False,
            settingsRevision=None, restartRequired=self.restart_required,
            violet=dict(status='unknown', connected=False, stale=False, source='Dot-reported', observedAt=None,
                message='MASTER configuration needs review'),
            temperatures=dict(cpu=dict(value=None, unit='C', status='unavailable'), gpu=dict(value=None, unit='C', status='unavailable'), status='unavailable', observedAt=None),
            display=dict(status='configuration_error', connected=False, error='MASTER configuration needs review; project workflows are unaffected', message='Review the existing MASTER settings and ownership records'),
            capture=dict(valid=False, status='unavailable', message='MASTER configuration needs review'),
            captureSources=[], attention=self.attention())

    def _configuration(self):
        if self.configuration is None:
            raise ValueError('MASTER ownership or paths need review. Recovery is unavailable.')
        return self.configuration

    def history(self):
        return self._configuration().history()

    def preview_restore(self, body):
        if not isinstance(body, dict) or set(body) != {'snapshotId'}:
            raise ValueError('Select one configuration snapshot.')
        return self._configuration().preview(body['snapshotId'], safe_restore_master)

    def restore(self, body):
        if not isinstance(body, dict) or set(body) not in ({'previewId'}, {'previewId', 'expectedRevision'}):
            raise ValueError('Select a current configuration restore preview.')
        self._configuration().restore(body['previewId'], expected_revision=body.get('expectedRevision'))
        self.restart_required = True
        return dict(status='restored', restartRequired=True, state=self.state())

    attention = MasterService.attention
    acknowledge_attention = MasterService.acknowledge_attention

    def frame(self):
        return b''

    def settings(self, body):
        raise ValueError('MASTER configuration needs review. Existing records have been preserved.')

    def display(self, body):
        raise ValueError('MASTER configuration needs review. Project workflows are unaffected.')

    def close(self):
        pass


def safe_service(runtime, controller, **kwargs):
    try:
        return MasterService(runtime, controller, **kwargs)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
        return UnavailableMaster(runtime)
