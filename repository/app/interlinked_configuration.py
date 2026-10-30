"""Owned local configuration revisions and explicit, bounded recovery.

No project data, commands, devices or registry access. Domain validators and the
installation's owner checker remain authoritative; history is private metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

LIMIT = 1024 * 1024
RECOVERY_LIMIT = 16 * LIMIT
FORMAT = 'interlinked-configuration-v2'
HISTORY = 'interlinked-configuration-snapshot-v1'
ID = re.compile(r'^[a-f0-9]{32}$')


class Conflict(ValueError):
    def __init__(self, current):
        super().__init__('Configuration changed. Review the current values before saving again.')
        self.current = deepcopy(current)
        self.code = 'configuration_conflict'


class UnsafeConfiguration(ValueError):
    """An ownership/path refusal must never become a recoverable empty view."""


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fs(path):
    value = str(path)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def ordinary(path, missing=False):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith('\\\\') or any(ord(c) < 32 for c in str(path)):
        raise UnsafeConfiguration('Configuration requires an ordinary absolute local path.')
    if ':' in str(path)[len(path.anchor):]:
        raise UnsafeConfiguration('Alternate configuration streams are refused.')
    for item in reversed((path, *path.parents)):
        try:
            info = os.lstat(fs(item))
        except FileNotFoundError:
            if missing:
                continue
            raise
        attributes = getattr(info, 'st_file_attributes', 0)
        if os.name == 'nt' and attributes & 0x40000:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
            kernel.GetFileAttributesW.restype = ctypes.c_uint32
            current = kernel.GetFileAttributesW(fs(item))
            if current == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = (attributes & ~0x40000) | current
        if stat.S_ISLNK(info.st_mode) or attributes & 0x441400 or stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise UnsafeConfiguration('Linked, shared or offline configuration paths are refused.')
    return path


def read_bytes(path, limit=LIMIT):
    path = ordinary(path)
    # Deny write/delete sharing on Windows, and inspect the actual opened leaf.
    if os.name == 'nt':
        import msvcrt
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        handle = kernel.CreateFileW(fs(path), 0x80000000, 1, None, 3, 0x08200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
            raise
        stream = os.fdopen(descriptor, 'rb')
    else:
        stream = open(fs(path), 'rb')
    with stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise UnsafeConfiguration('Configuration leaf is not an ordinary private file.')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        now = os.lstat(fs(path))
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (after.st_dev, after.st_ino) != (now.st_dev, now.st_ino):
            raise UnsafeConfiguration('Configuration changed while reading.')
        ordinary(path)
    if len(data) > limit:
        raise ValueError('Configuration metadata exceeds its size limit.')
    return data


def atomic(path, data, limit=LIMIT):
    path = ordinary(path, missing=True)
    ordinary(path.parent)
    if len(data) > limit:
        raise ValueError('Configuration metadata exceeds its size limit.')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(fs(temporary), 'xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        ordinary(path, missing=True)
        os.replace(fs(temporary), fs(path))
    finally:
        if temporary.exists():
            ordinary(temporary).unlink()


class ConfigStore:
    def __init__(self, path, namespace, owner_check, validate, legacy_reader, default):
        self.path = Path(path)
        if namespace not in ('master', 'library'):
            raise ValueError('Unknown configuration namespace.')
        self.namespace, self.owner_check = namespace, owner_check
        self.validate, self.legacy_reader = validate, legacy_reader
        self.default = deepcopy(default)
        self.lock = threading.RLock()
        self.previews = {}
        self.history_root = self.path.parent / ('.' + self.path.stem + '-history')

    def _owner(self):
        ordinary(self.path.parent)
        value = self.owner_check()
        return deepcopy(value) if value is not None else dict(namespace=self.namespace, path=str(self.path))

    @contextmanager
    def guarded(self):
        with self.lock:
            self._owner()
            path = ordinary(self.path.with_name('.' + self.path.name + '.lock'), missing=True)
            deadline = time.monotonic() + .5
            stream = None
            while stream is None:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
                        kernel.CreateFileW.restype = ctypes.c_void_p
                        handle = kernel.CreateFileW(fs(path), 0xc0000000, 0, None, 4, 0x00200000, None)
                        if handle == ctypes.c_void_p(-1).value:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                        except BaseException:
                            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                            kernel.CloseHandle(handle)
                            raise
                        stream = os.fdopen(fd, 'r+b')
                    else:
                        import fcntl
                        candidate = open(fs(path), 'a+b')
                        try:
                            fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BaseException:
                            candidate.close()
                            raise
                        stream = candidate
                except OSError:
                    if time.monotonic() >= deadline:
                        raise OSError('Configuration metadata is busy; retry after the other request finishes.')
                    time.sleep(.01)
            try:
                info = os.fstat(stream.fileno())
                named = os.lstat(fs(path))
                # Lock the named ordinary leaf, not a replacement object opened
                # between the path preflight and CreateFile/open. On Windows
                # the retained zero-share handle then prevents its replacement.
                ordinary(path)
                attributes = getattr(info, 'st_file_attributes', 0)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                        not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or
                        (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino) or
                        attributes & (0x400 | 0x1000 | 0x400000)):
                    raise UnsafeConfiguration('Configuration lock is not an ordinary private file.')
                self._owner()
                yield
            finally:
                stream.close()

    def _status(self):
        self._owner()
        try:
            raw = read_bytes(self.path, RECOVERY_LIMIT)
        except FileNotFoundError:
            if self.history_root.exists() and any(self.history_root.glob('????????????????????????????????.json')):
                return dict(available=False, revision=None, value=None, token='missing', error='Configuration is missing; review recovery history.')
            value = self.validate(deepcopy(self.default))
            return dict(available=True, revision='initial:' + digest(encoded(value)), value=value, token='missing', error=None)
        except UnsafeConfiguration:
            raise
        except ValueError:
            return dict(available=False, revision=None, value=None, token='oversized-unrecoverable', error='Configuration exceeds the bounded recovery limit; existing bytes require manual review.')
        token = 'sha256:' + digest(raw)
        if len(raw) > LIMIT:
            return dict(available=False, revision=None, value=None, token=token, error='Configuration content exceeds its size limit; existing bytes were preserved.')
        try:
            parsed = json.loads(raw.decode('utf-8-sig'))
            if isinstance(parsed, dict) and parsed.get('format') == FORMAT:
                if parsed.get('namespace') != self.namespace:
                    raise UnsafeConfiguration('Configuration belongs to a different namespace.')
                if set(parsed) != {'format', 'namespace', 'revision', 'value'} or not isinstance(parsed['revision'], str) or not ID.fullmatch(parsed['revision']):
                    raise ValueError('Configuration envelope identity differs.')
                value = self.validate(deepcopy(parsed['value']))
                revision = parsed['revision']
            else:
                value = self.validate(self.legacy_reader(parsed))
                revision = 'legacy:' + digest(raw)
            return dict(available=True, revision=revision, value=value, token=token, error=None)
        except UnsafeConfiguration:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError):
            return dict(available=False, revision=None, value=None, token=token, error='Configuration content needs recovery; existing bytes were preserved.')

    def status(self):
        with self.guarded():
            return deepcopy(self._status())

    def _history_directory(self, create=False):
        owner = self._owner()
        if not self.history_root.exists():
            if not create:
                return None
            ordinary(self.history_root, missing=True).mkdir()
            atomic(self.history_root / 'OWNER.json', encoded(dict(format='interlinked-configuration-history-owner-v1', namespace=self.namespace, owner=owner, primary=str(self.path))))
        ordinary(self.history_root)
        marker = json.loads(read_bytes(self.history_root / 'OWNER.json'))
        expected = dict(format='interlinked-configuration-history-owner-v1', namespace=self.namespace, owner=owner, primary=str(self.path))
        if marker != expected:
            raise UnsafeConfiguration('Configuration history belongs to a different installation.')
        return self.history_root

    def _snapshots(self):
        directory = self._history_directory()
        if directory is None:
            return []
        paths = list(directory.iterdir())
        if len(paths) > 100:
            raise ValueError('Configuration history requires review; too many records.')
        records = []
        for path in paths:
            if path.name == 'OWNER.json' or path.name.startswith('corrupt-') and path.suffix == '.bin':
                continue
            if path.suffix != '.json' or not ID.fullmatch(path.stem):
                raise ValueError('Unexpected configuration history record; preserved for review.')
            raw = read_bytes(path)
            row = json.loads(raw.decode('utf-8'))
            if not isinstance(row, dict) or set(row) != {'format', 'namespace', 'owner', 'id', 'revision', 'observedAt', 'reason', 'sha256', 'value'} or row['format'] != HISTORY or row['namespace'] != self.namespace or row['owner'] != self._owner() or row['id'] != path.stem:
                raise ValueError('Configuration history identity differs.')
            if not isinstance(row['revision'], str) or len(row['revision']) > 80 or not isinstance(row['reason'], str) or not 0 < len(row['reason']) <= 120:
                raise ValueError('Invalid configuration history metadata.')
            stamp = dt.datetime.fromisoformat(row['observedAt'])
            if stamp.utcoffset() is None or digest(encoded(row['value'])) != row['sha256']:
                raise ValueError('Configuration history verification failed.')
            row['value'] = self.validate(deepcopy(row['value']))
            row['_recordHash'] = digest(raw)
            records.append(row)
        return sorted(records, key=lambda row: (row['observedAt'], row['id']), reverse=True)

    def history(self):
        with self.guarded():
            current = self._status()
            try:
                rows = self._snapshots()
                return dict(available=True, scope=self.namespace, current=current,
                            items=[{k: deepcopy(row[k]) for k in ('id', 'revision', 'observedAt', 'reason', 'sha256')} for row in rows[:3]])
            except UnsafeConfiguration:
                raise
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                return dict(available=False, scope=self.namespace, current=current, items=[], error='Configuration history needs review; records were preserved.')

    def _snapshot(self, current, reason):
        if current['token'] == 'missing' or not current['available']:
            return None
        records = self._snapshots()
        if any(row['revision'] == current['revision'] for row in records):
            return None
        directory = self._history_directory(create=True)
        identifier = uuid.uuid4().hex
        row = dict(format=HISTORY, namespace=self.namespace, owner=self._owner(), id=identifier, revision=current['revision'], observedAt=utc(), reason=reason,
                   sha256=digest(encoded(current['value'])), value=current['value'])
        atomic(directory / (identifier + '.json'), encoded(row))
        if json.loads(read_bytes(directory / (identifier + '.json'))) != row:
            raise OSError('Configuration snapshot readback failed.')
        return identifier

    def _commit(self, value, current, reason, force=False):
        value = self.validate(deepcopy(value))
        if current['available'] and value == current['value'] and not force:
            return current
        if not isinstance(reason, str) or not 0 < len(reason) <= 120:
            raise ValueError('Invalid configuration change reason.')
        self._snapshots()  # Never overwrite/prune questionable history.
        previous = self._snapshot(current, reason)
        revision = uuid.uuid4().hex
        record = dict(format=FORMAT, namespace=self.namespace, revision=revision, value=value)
        try:
            atomic(self.path, encoded(record))
            result = self._status()
            if not result['available'] or result['revision'] != revision or result['value'] != value:
                raise OSError('Configuration commit readback failed; refresh its observed state before retrying.')
        except BaseException:
            # A failure before replacement did not create a previous version.
            if previous is not None and self._status()['token'] == current['token']:
                ordinary(self.history_root / (previous + '.json')).unlink()
            raise
        for row in self._snapshots()[3:]:
            ordinary(self.history_root / (row['id'] + '.json')).unlink()
        return result

    def save(self, value, expected_revision=None, reason='Settings changed'):
        with self.guarded():
            current = self._status()
            if expected_revision is not None and expected_revision != current['revision']:
                raise Conflict(current)
            if not current['available']:
                raise ValueError(current['error'])
            return deepcopy(self._commit(value, current, reason))

    def update(self, change, reason='Settings changed'):
        """Apply a server-owned delta to the current value in one transaction."""
        with self.guarded():
            current = self._status()
            if not current['available']:
                raise ValueError(current['error'])
            value = change(deepcopy(current['value']))
            return deepcopy(self._commit(value, current, reason))

    def preview(self, snapshot_id, transform=None):
        if not isinstance(snapshot_id, str) or not ID.fullmatch(snapshot_id):
            raise ValueError('Select a retained configuration snapshot.')
        with self.guarded():
            current = self._status()
            if current['token'] == 'oversized-unrecoverable':
                raise ValueError(current['error'])
            row = next((row for row in self._snapshots() if row['id'] == snapshot_id), None)
            if row is None:
                raise ValueError('Configuration snapshot is no longer retained.')
            proposed = deepcopy(row['value'])
            if transform is not None:
                proposed = transform(proposed, deepcopy(current))
            proposed = self.validate(proposed)
            self.previews = {key: value for key, value in self.previews.items() if time.monotonic() - value['created'] < 600}
            if len(self.previews) >= 20:
                self.previews.pop(next(iter(self.previews)))
            identifier = uuid.uuid4().hex
            self.previews[identifier] = dict(created=time.monotonic(), token=current['token'], snapshotId=row['id'], recordHash=row['_recordHash'], value=proposed)
            return dict(previewId=identifier, scope=self.namespace, snapshotId=row['id'], currentRevision=current['revision'], currentToken=current['token'], current=deepcopy(current), proposed=deepcopy(proposed), expiresInSeconds=600)

    def restore(self, preview_id, expected_revision=None):
        if not isinstance(preview_id, str) or not ID.fullmatch(preview_id):
            raise ValueError('Select a current configuration restore preview.')
        with self.guarded():
            preview = self.previews.get(preview_id)
            if preview is None or time.monotonic() - preview['created'] >= 600:
                raise ValueError('Restore preview expired; preview the configuration again.')
            current = self._status()
            if current['token'] != preview['token'] or expected_revision is not None and current['revision'] != expected_revision:
                raise Conflict(current)
            row = next((row for row in self._snapshots() if row['id'] == preview['snapshotId']), None)
            if row is None or row['_recordHash'] != preview['recordHash']:
                raise ValueError('The previewed snapshot changed or is no longer retained.')
            if not current['available'] and current['token'] != 'missing':
                directory = self._history_directory(create=True)
                data = read_bytes(self.path, RECOVERY_LIMIT)
                preserved = directory / ('corrupt-' + uuid.uuid4().hex + '.bin')
                atomic(preserved, data, limit=RECOVERY_LIMIT)
                if read_bytes(preserved, RECOVERY_LIMIT) != data:
                    raise OSError('Corrupt configuration preservation failed.')
            result = self._commit(preview['value'], current, 'Restored configuration', force=True)
            self.previews.pop(preview_id, None)
            return deepcopy(result)
