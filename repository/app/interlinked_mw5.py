"""Reviewed MW5 plugin transfer companion; no background synchronization.

Only the two named plugin trees participate. Callers supply an already reviewed
library engine and trusted owned-cache paths, never paths from an HTTP request.
Previews bind both trees by content; interrupted applies retain staged bytes,
backups, and a per-file journal. The cache lock remains held until explicit Done.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid

PLUGINS = ('LanceObserverDiagnosticControl', 'LanceObserverProbe')
MAX_ITEMS = 50000
MAX_DEPTH = 128
BLOCK = 4 * 1024 * 1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def editor_closed():
    """Process inspection must succeed; editor absence is never guessed."""
    need(os.name == 'nt', 'MW5 process inspection requires Windows.')
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                    ('heap', ctypes.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                    ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                    ('exe', w.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    need(handle != ctypes.c_void_p(-1).value, 'Cannot inspect running editors.')
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        need(kernel.Process32FirstW(handle, ctypes.byref(entry)), 'Cannot enumerate running editors.')
        while True:
            name = entry.exe.casefold()
            need(not re.match(r'^(ue4editor|unrealeditor|mechwarrior5editor)(?:[-.]|$)', name),
                 'Close the MW5/Unreal editor before transferring plugin files.')
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                need(ctypes.get_last_error() == 18, 'Editor process enumeration was incomplete.')
                break
    finally:
        kernel.CloseHandle(handle)


def _ordinary(engine, path, missing=False, directory=False):
    path = Path(path)
    need(path.is_absolute() and not str(path).startswith('\\\\'), 'Absolute local paths required.')
    info = engine.check_path(path)
    if info is None:
        need(missing, 'Required MW5 path is absent: ' + str(path))
    else:
        need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
             'Unexpected MW5 filesystem object: ' + str(path))
        if not directory:
            need(info.st_nlink == 1, 'Hardlinked MW5 files are refused: ' + str(path))
    return path


def _read(engine, path, limit=32 * 1024 * 1024):
    _ordinary(engine, path)
    with engine.reader(path) as stream:
        before = engine.fingerprint(stream)
        need(before['bytes'] <= limit, 'MW5 metadata exceeds its bounded size.')
        value = json.loads(stream.read().decode('utf-8-sig'), object_pairs_hook=engine.unique_keys)
        need(engine.fingerprint(stream) == before and isinstance(value, dict), 'MW5 metadata changed or is invalid.')
    return value


def discover_editor(manifests_root=None, engine=None, reviewed_config_path=None):
    """Return one matching Epic install; unrelated launcher entries are ignored."""
    need(engine is not None, 'Reviewed library engine required.')
    if reviewed_config_path is not None:
        # A server-selected private receipt is an intentional installation pin,
        # not permission to trust an arbitrary executable or an HTTP path.
        config = _read(engine, Path(reviewed_config_path), 1024 * 1024)
        need(set(config) == {'format', 'install', 'executableSha256', 'confirmedByUser'} and
             config.get('format') == 'interlinked-mw5-reviewed-editor-v1' and
             config.get('confirmedByUser') is True and isinstance(config.get('install'), str) and
             isinstance(config.get('executableSha256'), str) and
             re.fullmatch(r'[a-f0-9]{64}', config['executableSha256']),
             'The private MW5 editor review receipt is malformed or unconfirmed.')
        install = _ordinary(engine, engine.absolute(config['install']), directory=True)
        need(install != Path(install.anchor), 'Editor installation cannot be a volume root.')
        executable = _ordinary(engine, install/'Engine/Binaries/Win64/UE4Editor.exe')
        project = _ordinary(engine, install/'MW5Mercs/MW5Mercs.uproject')
        plugin_root = _ordinary(engine, install/'MW5Mercs/Plugins', directory=True)
        need(engine.file_digest(executable) == config['executableSha256'],
             'The reviewed MW5 editor executable changed; confirm and review the installation again.')
        return dict(available=True, install=str(install), executable=str(executable), project=str(project),
                    pluginRoot=str(plugin_root), executableSha256=config['executableSha256'],
                    source='reviewed-local', discoverySource='reviewed-local',
                    reviewedConfigSha256=engine.file_digest(Path(reviewed_config_path)), warnings=[])
    folder = Path(manifests_root or Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) /
                  'Epic/EpicGamesLauncher/Data/Manifests')
    if not engine.exists(folder):
        return dict(available=False, reason='MW5 editor installation metadata was not found.')
    _ordinary(engine, folder, directory=True)
    paths = sorted(folder.glob('*.item'))
    need(len(paths) <= 4096, 'Epic manifest inventory exceeds its limit.')
    candidates, warnings = [], []
    for path in paths:
        try:
            value = _read(engine, path, 2 * 1024 * 1024)
        except (ValueError, OSError, engine.LibraryError, json.JSONDecodeError):
            warnings.append('An Epic manifest could not be inspected; no installation was inferred from it.')
            continue
        label = ' '.join(str(value.get(k, '')) for k in ('DisplayName', 'AppName', 'InstallLocation'))
        if not re.search(r'(mechwarrior.?5.*editor|mw5.*editor)', label, re.I):
            continue
        install = _ordinary(engine, engine.absolute(value.get('InstallLocation', '')), directory=True)
        need(install != Path(install.anchor), 'Editor installation cannot be a volume root.')
        relative = engine.relative(value.get('LaunchExecutable', ''))
        executable = _ordinary(engine, engine.join(install, relative))
        need(executable.name.casefold() in ('ue4editor.exe', 'unrealeditor.exe', 'mechwarrior5editor.exe'),
             'Unreviewed MW5 editor executable.')
        project = _ordinary(engine, install / 'MW5Mercs/MW5Mercs.uproject')
        plugin_root = _ordinary(engine, install / 'MW5Mercs/Plugins', directory=True)
        candidates.append(dict(available=True, install=str(install), executable=str(executable),
                               project=str(project), pluginRoot=str(plugin_root),
                               executableSha256=engine.file_digest(executable),
                               manifestSha256=engine.file_digest(path), source='epic', discoverySource='epic', warnings=warnings))
    need(len(candidates) <= 1, 'Multiple MW5 editor installations require explicit review.')
    return candidates[0] if candidates else dict(available=False, reason='No installed MW5 editor was found in Epic metadata.', warnings=warnings)


def find_plugin_sources(files_root, engine):
    """Find authored plugin roots, qualified by their exact native descriptor."""
    root = _ordinary(engine, files_root, directory=True)
    found = {name: [] for name in PLUGINS}; todo = [root]; count = 0
    while todo:
        folder = todo.pop()
        need(len(folder.relative_to(root).parts) <= MAX_DEPTH, 'Plugin search exceeded depth limit.')
        _ordinary(engine, folder, directory=True)
        with os.scandir(engine.fs(folder)) as entries:
            for entry in entries:
                count += 1; need(count <= MAX_ITEMS, 'Plugin search exceeded inventory limit.')
                path = folder / entry.name
                info = engine.check_path(path)
                need(info is not None, 'Plugin inventory changed during discovery.')
                if stat.S_ISDIR(info.st_mode):
                    if path.name in PLUGINS and path.parent.name.casefold() == 'plugins':
                        # Cooked evidence can contain a Plugins/<name>/Content
                        # tree without being an authored native plugin. Require
                        # the exact descriptor instead of excluding folders by
                        # generated-looking names or selecting a basename copy.
                        descriptor = path / (path.name + '.uplugin')
                        if engine.exists(descriptor):
                            _ordinary(engine, descriptor)
                            found[path.name].append(path)
                    elif path.name.casefold() not in ('.git', '.venv', 'node_modules'):
                        todo.append(path)
    need(all(len(found[name]) == 1 for name in PLUGINS),
         'Exactly one managed Plugins directory with its exact .uplugin descriptor is required for each supported MW5 plugin.')
    return {name: str(found[name][0]) for name in PLUGINS}


class MW5Workspace:
    def __init__(self, engine, state_root, files_root, source_roots, editor_info,
                 process_guard=None, fault_hook=None, cache_guard=None):
        self.e = engine; self.state_root = Path(state_root); self.files_root = Path(files_root)
        self.sources = {name: Path(source_roots[name]) for name in PLUGINS}
        need(set(source_roots) == set(PLUGINS), 'Only the two reviewed MW5 plugins are supported.')
        self.editor = dict(editor_info); need(self.editor.get('available'), 'MW5 editor is not installed.')
        self.plugin_root = Path(self.editor['pluginRoot'])
        self.targets = {name: self.plugin_root / name for name in PLUGINS}
        self.process_guard = process_guard or editor_closed; self.fault_hook = fault_hook
        self.lock = threading.RLock(); self._cache_guard = None; self.process = None
        _ordinary(engine, self.files_root, directory=True)
        for root in self.sources.values():
            _ordinary(engine, root, directory=True)
            need(root.is_relative_to(self.files_root), 'Managed plugin escaped the owned working tree.')
        _ordinary(engine, self.plugin_root, directory=True)
        for a, b in ((self.state_root, self.files_root), (self.state_root, self.plugin_root),
                     (self.files_root, self.plugin_root)):
            need(a != b and not a.is_relative_to(b) and not b.is_relative_to(a), 'MW5 state, managed files and editor must be separate.')
        self.cache_root = self.files_root.parent
        identity = dict(format='interlinked-mw5-owner-v1', files=str(self.files_root),
                        sources={k: str(v) for k, v in self.sources.items()},
                        editor={k: self.editor[k] for k in ('install', 'executable', 'project', 'pluginRoot', 'executableSha256')})
        if engine.exists(self.state_root):
            _ordinary(engine, self.state_root, directory=True)
            need(engine.exists(self.state_root / 'OWNER.json'), 'Existing MW5 state is unowned.')
            need(_read(engine, self.state_root / 'OWNER.json') == identity, 'MW5 workspace ownership differs.')
        else:
            need(cache_guard is None, 'A recovery cache lease requires an existing owned MW5 session.')
            engine.mkdir(self.state_root); self._write('OWNER.json', identity)
        self.identity = identity
        self.journal = self.state_root / 'session.json'
        if not engine.exists(self.journal):
            need(cache_guard is None, 'A recovery cache lease requires an existing MW5 session journal.')
            self._write('session.json', dict(format='interlinked-mw5-session-v1', status='IDLE'))
        current = self._session()
        if cache_guard is not None:
            need(current['status'] != 'IDLE' and callable(getattr(cache_guard, '__exit__', None)),
                 'Only an outstanding owned MW5 session can adopt a recovery cache lease.')
            # The caller retains its reference until construction returns. Do
            # not acquire, release, or adopt that lease before every validation
            # above succeeds; the constructor has no fallible work afterward.
            self._cache_guard = cache_guard
        elif current['status'] != 'IDLE':
            self._hold_cache()

    def _write(self, name, value):
        path = self.state_root / name
        if self.e.exists(path):
            _ordinary(self.e, path)
        self.e.mkdir(path.parent); self.e.atomic_json(path, value)

    def _session(self):
        value = _read(self.e, self.journal)
        need(value.get('format') == 'interlinked-mw5-session-v1', 'Unknown MW5 session journal.')
        need(value.get('status') in ('IDLE', 'STAGED', 'EDITING', 'COLLECTED', 'APPLYING', 'REVIEW_REQUIRED'), 'Invalid MW5 session status.')
        return value

    def _hold_cache(self):
        if self._cache_guard is None:
            guard = self.e.exclusive(self.cache_root / '.client.lock', create=True)
            guard.__enter__(); self._cache_guard = guard

    @contextmanager
    def _operation(self, keep=False):
        with self.lock:
            held = self._cache_guard is not None
            self._hold_cache()
            try:
                with self.e.exclusive(self.state_root / '.adapter.lock', create=True):
                    self._closed(); yield
            finally:
                if not held and self._session()['status'] == 'IDLE':
                    self._release_cache()

    def _release_cache(self):
        if self._cache_guard is not None:
            self._cache_guard.__exit__(None, None, None); self._cache_guard = None

    def _closed(self):
        if self.process is not None:
            need(self.process.poll() is not None, 'Close the MW5 editor before continuing.')
        self.process_guard()

    def _validate_editor(self):
        for key in ('executable', 'project'):
            _ordinary(self.e, Path(self.editor[key]))
        need(self.e.file_digest(Path(self.editor['executable'])) == self.editor['executableSha256'],
             'MW5 editor executable changed; review its installation again.')

    def _inventory(self, roots, held=None):
        result = {}; seen = set(); count = 0
        for plugin, root in roots.items():
            if not self.e.exists(root):
                _ordinary(self.e, root, missing=True, directory=True); continue
            _ordinary(self.e, root, directory=True); todo = [root]
            while todo:
                folder = todo.pop(); depth = len(folder.relative_to(root).parts)
                need(depth <= MAX_DEPTH, 'MW5 plugin inventory exceeded depth limit.')
                _ordinary(self.e, folder, directory=True)
                if held is not None: held.enter_context(self.e.directory_guard(folder))
                with os.scandir(self.e.fs(folder)) as entries:
                    for entry in entries:
                        count += 1; need(count <= MAX_ITEMS, 'MW5 plugin inventory exceeded item limit.')
                        path = folder / entry.name; rel = self.e.relative(path.relative_to(root).as_posix())
                        info = self.e.check_path(path); need(info is not None, 'Plugin tree changed while scanning.')
                        if stat.S_ISDIR(info.st_mode):
                            todo.append(path); continue
                        _ordinary(self.e, path)
                        cm = self.e.reader(path)
                        if held is None:
                            with cm as stream:
                                before = self.e.fingerprint(stream); digest, size = self.e.digest(stream)
                                need(self.e.fingerprint(stream) == before, 'Plugin changed while hashing.')
                        else:
                            stream = held.enter_context(cm); before = self.e.fingerprint(stream)
                            digest, size = self.e.digest(stream)
                            need(self.e.fingerprint(stream) == before, 'Plugin changed while hashing.')
                        key = plugin + '/' + rel
                        need(key.casefold() not in seen, 'Aliased MW5 plugin paths.'); seen.add(key.casefold())
                        result[key] = dict(sha256=digest, bytes=size)
        return result

    def _path(self, roots, key):
        need(isinstance(key, str) and '/' in key, 'Invalid MW5 plugin member.')
        name, rel = key.split('/', 1); need(name in PLUGINS, 'Unsupported MW5 plugin member.')
        return self.e.join(roots[name], rel)

    def _hash(self, value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def state(self):
        with self.lock:
            current = self._session()
            return dict(status=current['status'], sessionId=current.get('sessionId'),
                        outstanding=current['status'] != 'IDLE', canFinish=current['status'] == 'COLLECTED',
                        resumablePreviewId=current.get('previewId') if current['status'] == 'APPLYING' else None,
                        resumableDirection=current.get('direction') if current['status'] == 'APPLYING' else None,
                        editor=self.editor, plugins=list(PLUGINS),
                        nextAction={'IDLE': 'Preview plugin staging', 'STAGED': 'Open MW5 editor or preview collection',
                                    'EDITING': 'Save and close MW5, then preview collection',
                                    'COLLECTED': 'Finish session, then Save to MSI + verify',
                                    'APPLYING': 'Resume the reviewed transfer',
                                    'REVIEW_REQUIRED': 'Review the preserved transaction and backups'}[current['status']])

    def resume_preview(self):
        """Expose only the exact interrupted transaction, never caller paths."""
        with self.lock:
            session = self._session()
            if session['status'] != 'APPLYING': return None
            preview_id = session.get('previewId'); direction = session.get('direction')
            need(isinstance(preview_id, str) and re.fullmatch(r'[a-f0-9]{32}', preview_id) and
                 direction in ('stage', 'collect'), 'Interrupted MW5 preview identity is invalid.')
            value = _read(self.e, self.state_root/'previews'/(preview_id+'.json'))
            need(value.get('format') == 'interlinked-mw5-preview-v1' and value.get('previewId') == preview_id and
                 value.get('direction') == direction and value.get('identitySha256') == self._hash(self.identity),
                 'Interrupted MW5 preview differs from its owned session.')
            need(isinstance(value.get('changes'), list) and isinstance(value.get('conflicts'), list) and
                 isinstance(value.get('warnings'), list) and type(value.get('applicable')) is bool,
                 'Interrupted MW5 preview is malformed.')
            return {k: value[k] for k in ('previewId', 'direction', 'changes', 'conflicts', 'warnings', 'applicable')}

    def readiness(self):
        try:
            self._validate_editor(); self._closed()
            return dict(ready=True, **self.state())
        except (ValueError, OSError, self.e.LibraryError) as exc:
            return dict(ready=False, reason=str(exc), **self.state())

    def _preview(self, direction):
        with self._operation():
            self._validate_editor(); session = self._session()
            need(session['status'] == 'IDLE' if direction == 'stage' else session['status'] in ('STAGED', 'EDITING'),
                 'Finish the current MW5 transfer/session before creating this preview.')
            managed = self._inventory(self.sources); editor = self._inventory(self.targets)
            changes = []; conflicts = []
            if direction == 'stage':
                for key, row in sorted(managed.items()):
                    if editor.get(key) != row:
                        changes.append(dict(path=key, action='replace' if key in editor else 'add', bytes=row['bytes']))
                warnings = ['Editor-only files are preserved. Existing differing files are backed up before replacement.']
            else:
                before_m, before_e = session['managedBaseline'], session['editorBaseline']
                for key in sorted(set(before_e) | set(editor)):
                    if editor.get(key) == before_e.get(key):
                        continue
                    if managed.get(key) != before_m.get(key) and managed.get(key) != editor.get(key):
                        conflicts.append(dict(path=key, reason='Managed and editor copies both changed since the session baseline.'))
                    elif managed.get(key) != editor.get(key):
                        action = 'delete' if key not in editor else 'replace' if key in managed else 'add'
                        changes.append(dict(path=key, action=action, bytes=editor.get(key, {}).get('bytes', 0)))
                warnings = ['Collection only updates the managed plugin trees. Save to MSI remains a separate action.',
                            'Previewed deletions preserve the previous managed bytes in the transaction backup.']
            target_roots = self.targets if direction == 'stage' else self.sources
            for change in changes:
                try:
                    target = self._path(target_roots, change['path'])
                    info = self.e.check_path(target)
                    need(info is None or stat.S_ISREG(info.st_mode), 'A directory occupies the reviewed file path.')
                    parent = target.parent
                    plugin = change['path'].split('/', 1)[0]
                    while parent.is_relative_to(target_roots[plugin]):
                        parent_info = self.e.check_path(parent)
                        need(parent_info is None or stat.S_ISDIR(parent_info.st_mode), 'A file occupies a required directory path.')
                        if parent == target_roots[plugin]: break
                        parent = parent.parent
                except (ValueError, OSError, self.e.LibraryError) as exc:
                    conflicts.append(dict(path=change['path'], reason=str(exc)))
            preview_id = uuid.uuid4().hex
            value = dict(format='interlinked-mw5-preview-v1', previewId=preview_id, direction=direction,
                         createdUnixTime=time.time(), sessionId=session.get('sessionId'), identitySha256=self._hash(self.identity),
                         managed=managed, editor=editor, changes=changes, conflicts=conflicts, warnings=warnings,
                         applicable=not conflicts)
            self._write('previews/' + preview_id + '.json', value)
            return {k: value[k] for k in ('previewId', 'direction', 'changes', 'conflicts', 'warnings', 'applicable')}

    def preview_stage(self):
        return self._preview('stage')

    def preview_collect(self):
        return self._preview('collect')

    def _copy(self, source, target, expected, resume=False):
        _ordinary(self.e, source); _ordinary(self.e, target, missing=True)
        self.e.mkdir(target.parent); self.e.space(target.parent, expected['bytes'])
        with self.e.reader(source) as incoming:
            before = self.e.fingerprint(incoming)
            digest = hashlib.sha256(); size = 0
            if self.e.exists(target):
                need(resume, 'MW5 transfer destination already exists.')
                with self.e.reader(target) as partial:
                    length = self.e.fingerprint(partial)['bytes']
                    need(length <= expected['bytes'], 'Owned MW5 partial exceeds the reviewed payload.')
                    while size < length:
                        saved = partial.read(min(BLOCK, length-size)); original = incoming.read(len(saved))
                        need(saved and saved == original, 'Owned MW5 partial differs from the reviewed source; preserved for review.')
                        digest.update(saved); size += len(saved)
            with open(self.e.fs(target), 'ab' if size else 'xb' if not self.e.exists(target) else 'ab') as outgoing:
                while True:
                    block = incoming.read(BLOCK)
                    if not block: break
                    self.e.space(target.parent, len(block)); outgoing.write(block); digest.update(block); size += len(block)
                outgoing.flush(); os.fsync(outgoing.fileno())
            need(dict(sha256=digest.hexdigest(), bytes=size) == expected and self.e.fingerprint(incoming) == before,
                 'Source changed during MW5 transfer; staged bytes preserved for review.')
        need(self.e.file_digest(target) == expected['sha256'], 'MW5 copied-file readback failed.')

    def _current_file(self, path):
        if not self.e.exists(path): return None
        _ordinary(self.e, path)
        with self.e.reader(path) as stream:
            before = self.e.fingerprint(stream); digest, size = self.e.digest(stream)
            need(self.e.fingerprint(stream) == before, 'MW5 destination changed during inspection.')
            return dict(sha256=digest, bytes=size)

    def _apply(self, preview_id, direction):
        need(isinstance(preview_id, str) and re.fullmatch(r'[a-f0-9]{32}', preview_id), 'Invalid MW5 preview identifier.')
        with self._operation(keep=True), ExitStack() as source_locks:
            self._validate_editor(); preview = _read(self.e, self.state_root / 'previews' / (preview_id + '.json'))
            need(preview.get('format') == 'interlinked-mw5-preview-v1' and preview['previewId'] == preview_id and
                 preview['direction'] == direction and preview['identitySha256'] == self._hash(self.identity), 'MW5 preview identity differs.')
            need(preview['applicable'] and not preview['conflicts'], 'Conflicts must be resolved before applying this preview.')
            session = self._session(); resuming = session['status'] == 'APPLYING'
            if resuming:
                need(session['previewId'] == preview_id and session['direction'] == direction, 'Resume only the exact interrupted MW5 preview.')
            else:
                need(session['status'] == 'IDLE' if direction == 'stage' else session['status'] in ('STAGED', 'EDITING'), 'MW5 session is not ready for this transfer.')
                need(preview['sessionId'] == session.get('sessionId'), 'MW5 preview belongs to an older session.')
            source_roots = self.sources if direction == 'stage' else self.targets
            target_roots = self.targets if direction == 'stage' else self.sources
            source_before = preview['managed' if direction == 'stage' else 'editor']
            target_before = preview['editor' if direction == 'stage' else 'managed']
            for roots, snapshot in ((self.sources, preview['managed']), (self.targets, preview['editor'])):
                need(isinstance(snapshot, dict), 'Invalid MW5 preview inventory.')
                self.e.paths_unique(snapshot)
                for key, row in snapshot.items():
                    self._path(roots, key)
                    need(set(row) == {'sha256', 'bytes'} and type(row['bytes']) is int and row['bytes'] >= 0 and
                         isinstance(row['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', row['sha256']), 'Invalid MW5 content fingerprint.')
            expected_changes = []
            baseline = session['previous'] if resuming else session
            if direction == 'stage':
                for key, row in sorted(preview['managed'].items()):
                    if preview['editor'].get(key) != row:
                        expected_changes.append(dict(path=key, action='replace' if key in preview['editor'] else 'add', bytes=row['bytes']))
            else:
                for key in sorted(set(baseline['editorBaseline']) | set(preview['editor'])):
                    managed, editor = preview['managed'].get(key), preview['editor'].get(key)
                    if editor == baseline['editorBaseline'].get(key): continue
                    need(managed == baseline['managedBaseline'].get(key) or managed == editor,
                         'Both MW5 copies changed; conflicts must be preserved.')
                    if managed != editor:
                        expected_changes.append(dict(path=key, action='delete' if editor is None else 'replace' if managed is not None else 'add', bytes=(editor or {}).get('bytes', 0)))
            need(preview['changes'] == expected_changes, 'MW5 preview operations differ from its exact content inventories.')
            need(self._inventory(source_roots, source_locks) == source_before, 'Source plugin tree changed since preview; create a new preview.')
            if not resuming:
                need(self._inventory(target_roots) == target_before, 'Destination plugin tree changed since preview; create a new preview.')
                original = dict(session)
                session.update(status='APPLYING', previewId=preview_id, direction=direction,
                               sessionId=session.get('sessionId') or uuid.uuid4().hex, previous=original,
                               operations=[dict(row, phase='PENDING') for row in preview['changes']])
                self._write('session.json', session)
            # Stage every incoming file and every replaced/deleted destination
            # before changing either working tree. A partial private copy may be
            # retried only if its verified bytes are exact.
            for op in session['operations']:
                key = op['path']; desired = source_before.get(key)
                if desired is not None:
                    staged = self.state_root / 'transactions' / preview_id / 'Incoming' / key
                    self._copy(self._path(source_roots, key), staged, desired, resume=True)
                old = target_before.get(key)
                if old is not None:
                    backup = self.state_root / 'transactions' / preview_id / 'Backup' / key
                    if self.e.exists(backup) and self._current_file(backup) == old:
                        pass
                    else:
                        need(self._current_file(self._path(target_roots, key)) == old, 'Destination changed before backup; transfer preserved.')
                        self._copy(self._path(target_roots, key), backup, old, resume=True)
            expected_target = dict(target_before)
            for op in session['operations']:
                key = op['path']; desired = source_before.get(key); old = target_before.get(key)
                target = self._path(target_roots, key)
                self.e.mkdir(target.parent)
                source_locks.enter_context(self.e.directory_guard(target.parent))
                actual = self._current_file(target)
                if op['phase'] == 'DONE':
                    need(actual == desired, 'Previously transferred file changed; review preserved transaction.')
                elif op['phase'] == 'WRITING' and actual == desired:
                    pass  # Crash after replacement but before durable per-file completion.
                else:
                    need(actual == old or (op['phase'] == 'WRITING' and actual is None), 'Destination changed; both variants are preserved.')
                    op['phase'] = 'WRITING'; self._write('session.json', session)
                    self._closed()
                    temporary = target.with_name('.INTERLINKED-MW5-' + preview_id + '-' + self._hash(key)[:16] + '.pending')
                    if desired is not None:
                        incoming = self.state_root / 'transactions' / preview_id / 'Incoming' / key
                        self._copy(incoming, temporary, desired, resume=True)
                    if actual is not None:
                        # Delete only this exact, read-locked file after its
                        # independent backup has been verified; never recurse.
                        with self.e.reader(target, delete=True) as held:
                            digest, size = self.e.digest(held)
                            need(dict(sha256=digest, bytes=size) == old, 'Destination changed before replacement.')
                            self.e.delete_held(held)
                    if desired is not None:
                        # Same-directory rename cannot expose partial bytes.
                        # Windows rename refuses a concurrently appeared target.
                        need(not self.e.exists(target), 'MW5 destination appeared before commit; pending bytes retained.')
                        os.rename(self.e.fs(temporary), self.e.fs(target))
                        need(self._current_file(target) == desired, 'MW5 committed-file readback failed.')
                op['phase'] = 'DONE'; self._write('session.json', session)
                if desired is None: expected_target.pop(key, None)
                else: expected_target[key] = desired
                if self.fault_hook: self.fault_hook('after_file', key)
            need(self._inventory(source_roots) == source_before, 'Source tree membership changed during transfer.')
            need(self._inventory(target_roots) == expected_target, 'Destination tree membership changed during transfer.')
            if direction == 'stage':
                session.update(status='STAGED', managedBaseline=source_before, editorBaseline=expected_target,
                               stagedUnixTime=time.time())
            else:
                session.update(status='COLLECTED', collectedUnixTime=time.time())
            session.pop('previous', None); self._write('session.json', session)
            return dict(status=session['status'], sessionId=session['sessionId'], files=len(session['operations']),
                        automaticSave=False, automaticRelease=False, backupsPreserved=True)

    def apply_stage(self, previewId):
        return self._apply(previewId, 'stage')

    def apply_collect(self, previewId):
        return self._apply(previewId, 'collect')

    def start_session(self, launch=None):
        with self._operation(keep=True):
            self._validate_editor(); session = self._session()
            need(session['status'] == 'STAGED', 'Apply the plugin staging preview before opening MW5.')
            need(self._inventory(self.sources) == session['managedBaseline'] and
                 self._inventory(self.targets) == session['editorBaseline'], 'Plugin staging changed before editor launch; collect/review first.')
            factory = launch or subprocess.Popen
            session.update(status='EDITING', launchedUnixTime=time.time())
            self._write('session.json', session)
            try:
                with self.e.reader(Path(self.editor['executable'])) as executable:
                    need(self.e.digest(executable)[0] == self.editor['executableSha256'], 'MW5 editor changed before launch.')
                    self.process = factory([self.editor['executable'], self.editor['project']],
                                           cwd=self.editor['install'], shell=False)
            except BaseException:
                session['status'] = 'STAGED'; self._write('session.json', session)
                raise
            return dict(status='EDITING', sessionId=session['sessionId'], automaticSave=False)

    def finish_session(self):
        with self._operation(keep=True):
            session = self._session()
            need(session['status'] == 'COLLECTED', 'Preview and collect plugin changes before finishing the MW5 session.')
            # Preserve complete session history outside the project cache.
            self._write('sessions/' + session['sessionId'] + '.json', session)
            self._write('session.json', dict(format='interlinked-mw5-session-v1', status='IDLE',
                                            lastSessionId=session['sessionId'], lastCollectedUnixTime=session['collectedUnixTime']))
            self._release_cache()
            return dict(status='DONE_LOCAL_COPY_RETAINED', automaticSave=False, automaticRelease=False)

