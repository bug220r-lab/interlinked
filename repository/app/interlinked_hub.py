"""Read-only library discovery and private hub preferences.

The existing sealed client remains the only fetch/save/verify/release engine.
Discovery is asynchronous; stale catalogs never imply current content validity.
"""
from __future__ import annotations

import copy
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid


def configuration_module():
    path = Path(__file__).resolve().with_name('interlinked_configuration.py')
    spec = importlib.util.spec_from_file_location('interlinked_hub_configuration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

LIBRARY = r'\\MSI\ProjectLibrary'
RETURNS = r'\\MSI\ProjectReturns'
CATALOG = 'msi-project-library-v1'
MAX_JSON = 16 * 1024 * 1024
MAX_PROJECTS = 2000
CLIENT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
UI_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,159}\Z')
DEVICE = re.compile(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z', re.I)


def need(value, message):
    if not value:
        raise ValueError(message)


def utc(value=None):
    return dt.datetime.fromtimestamp(time.time() if value is None else value, dt.timezone.utc).isoformat()


def fs(path):
    value = str(path)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def path_attributes(path, info):
    """lstat's 0x40000 may mean EA, not directory-only RECALL_ON_OPEN."""
    attrs = getattr(info, 'st_file_attributes', 0)
    if os.name == 'nt' and attrs & 0x40000:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        kernel.GetFileAttributesW.restype = ctypes.c_uint32
        current = kernel.GetFileAttributesW(fs(path))
        if current == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        return (attrs & ~0x40000) | current
    return attrs


def ordinary(path, missing=False):
    path = Path(path)
    need(path.is_absolute(), 'Absolute hub path required.')
    for item in reversed((path, *path.parents)):
        try:
            info = os.lstat(fs(item))
        except FileNotFoundError:
            if missing:
                continue
            raise
        need(not stat.S_ISLNK(info.st_mode) and not path_attributes(item, info) & 0x441400,
             'Link/cloud hub path refused: ' + str(item))
        need(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, 'Hardlinked metadata refused: ' + str(item))
    return path


def read_json(path, limit=MAX_JSON):
    ordinary(path)
    with open(fs(path), 'rb') as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    need(len(data) <= limit, 'Hub metadata is too large.')
    need((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino),
         'Hub metadata changed while reading.')
    value = json.loads(data.decode('utf-8-sig'))
    need(isinstance(value, dict), 'Hub metadata object required.')
    return value


def write_json(path, value):
    ordinary(path.parent)
    ordinary(path, missing=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with open(fs(temporary), 'x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    ordinary(path, missing=True)
    os.replace(fs(temporary), fs(path))


def original_id(value):
    need(isinstance(value, str) and CLIENT_ID.fullmatch(value) and not DEVICE.fullmatch(value), 'Invalid library project ID.')
    return value


def key_for(value):
    return 'library-' + hashlib.sha256(original_id(value).encode('utf-8')).hexdigest()[:24]


def relative(value):
    need(isinstance(value, str) and 0 < len(value) <= 32700, 'Invalid library relative path.')
    value = value.replace('\\', '/')
    for part in value.split('/'):
        need(part not in ('', '.', '..') and not part.endswith((' ', '.')) and not DEVICE.fullmatch(part), 'Unsafe library relative path.')
        need(not any(ord(c) < 32 or c in '<>:"|?*' for c in part), 'Unsafe library relative path.')
    return value


def working_relative(value):
    value = relative(value)
    need(value == 'Files' or value.startswith('Files/'), 'Workspace must stay inside managed Files.')
    return value


def text_list(value, label):
    need(isinstance(value, list) and len(value) <= 1000 and all(isinstance(v, str) and len(v) <= 16384 for v in value), 'Invalid ' + label)
    return list(value)


def validate_catalog(value):
    need(set(value) == {'catalogId', 'projects'} and value['catalogId'] == CATALOG, 'Unexpected library catalog identity/schema.')
    need(isinstance(value['projects'], list) and len(value['projects']) <= MAX_PROJECTS, 'Invalid library catalog size.')
    result = []
    seen = set()
    allowed = {'id', 'name', 'description', 'bytes', 'files', 'warnings', 'notes', 'workspaceRelative', 'workspacePaths', 'environmentHints', 'entryPoints'}
    for raw in value['projects']:
        need(isinstance(raw, dict) and set(raw) <= allowed, 'Invalid library row schema.')
        pid = original_id(raw.get('id'))
        need(pid.casefold() not in seen, 'Duplicate library project ID.')
        seen.add(pid.casefold())
        need(type(raw.get('files')) is int and raw['files'] >= 0 and type(raw.get('bytes')) is int and raw['bytes'] >= 0, 'Invalid library row totals.')
        name = raw.get('name', pid)
        description = raw.get('description', '')
        need(isinstance(name, str) and 0 < len(name) <= 2048 and isinstance(description, str) and len(description) <= 16384, 'Invalid library row text.')
        row = dict(id=pid, name=name, description=description, bytes=raw['bytes'], files=raw['files'],
                   warnings=text_list(raw.get('warnings', []), 'library warnings'), notes=text_list(raw.get('notes', []), 'library notes'),
                   workspaceRelative=working_relative(raw.get('workspaceRelative') or 'Files'))
        paths = raw.get('workspacePaths') or [row['workspaceRelative']]
        need(isinstance(paths, list) and len(paths) <= 1000, 'Invalid workspace paths.')
        row['workspacePaths'] = [working_relative(p) for p in paths]
        entries = raw.get('entryPoints') or []
        need(isinstance(entries, list) and len(entries) <= 2000, 'Invalid library entry points.')
        row['entryPoints'] = []
        for entry in entries:
            need(isinstance(entry, dict) and set(entry) <= {'path', 'type', 'action'}, 'Invalid library entry point.')
            kind, action = entry.get('type', ''), entry.get('action', '')
            need(isinstance(kind, str) and len(kind) <= 100 and isinstance(action, str) and len(action) <= 4096, 'Invalid library entry point text.')
            row['entryPoints'].append(dict(path=working_relative(entry.get('path')), type=kind, action=action))
        # Environment hints are display/setup information, never an executable.
        hints = raw.get('environmentHints') or {}
        need(isinstance(hints, dict), 'Invalid library environment hints.')
        row['environmentHints'] = {}
        if 'STATS_DATA_DIR' in hints:
            hint = hints['STATS_DATA_DIR']
            need(isinstance(hint, dict), 'Invalid Stats environment hint.')
            row['environmentHints']['STATS_DATA_DIR'] = dict(relativeToWorkingSet=working_relative(hint.get('relativeToWorkingSet')))
        result.append(row)
    return result


def project_type(row):
    if row['id'] == 'mw5-companion':
        return 'mw5'
    if any(e['type'] in ('magix-project', 'magix-recovery') for e in row['entryPoints']):
        return 'magix'
    searchable = row['id'] + ' ' + row['name'] + ' ' + row['description']
    return 'coding_data' if re.search(r'code|coding|python|jupyter|notebook|data|stats|research|script|software|modding|source|repo', searchable, re.I) else 'other'


def head_id(value):
    if value is None:
        return None
    need(isinstance(value, dict) and value.get('format') == 'project-library-head-v1' and
         value.get('catalogId') == CATALOG and isinstance(value.get('versionId'),str) and
         re.fullmatch(r'[a-f0-9]{32}', value['versionId']) and isinstance(value.get('manifestSha256'),str) and
         re.fullmatch(r'[a-f0-9]{64}', value['manifestSha256']), 'Invalid saved-version metadata.')
    return value['versionId']


class ProjectHub:
    """Additive general-library adapter; all public methods are thread-safe.

    runner(kind) may be injected by isolated tests. Production's only two
    read-only child commands are catalog discovery and fixed share/space probes.
    """
    def __init__(self, runtime, runner=None, auto_refresh=True):
        self.r = runtime
        self.lock = threading.RLock()
        self.root = Path(runtime.state) / 'hub'
        ordinary(self.root, missing=True)
        self.root.mkdir(exist_ok=True)
        self.identity = dict(format='interlinked-hub-owner-v1', kit=str(runtime.kit), cache=str(runtime.cache), demo=runtime.demo)
        marker = self.root / 'OWNER.json'
        if marker.exists():
            need(read_json(marker) == self.identity, 'Hub state belongs to a different installation.')
        else:
            need(not list(self.root.iterdir()), 'Existing hub state is unowned.')
            write_json(marker, self.identity)
        self.snapshot_path = self.root / 'catalog.json'
        self.prefs_path = self.root / 'preferences.json'
        config = configuration_module()
        self.config = config.ConfigStore(self.prefs_path, 'library', self._check_preference_owner,
            self._validate_preferences, lambda value: value,
            dict(format='interlinked-hub-preferences-v1', favorites=[], recent=[]))
        self.projects = {}
        self.magix_ids = {p['id'] for p in runtime.index['projects']}
        self.prefs = dict(format='interlinked-hub-preferences-v1', favorites=[], recent=[])
        self.health = dict(libraryStatus='NOT_CHECKED', returnsStatus='NOT_CHECKED', catalogRefreshedUtc=None,
                           catalogStale=True, refreshing=False, warning=None, storage={}, verification='Metadata only; content verification is explicit.')
        self.health['storage'] = self._local_storage()
        self.closed = False
        self.worker = None
        self.local_cache = {}
        self.local_at = 0
        self.runner = runner or self._run
        if runtime.demo:
            self._install_catalog(self._demo_catalog())
            self.health.update(libraryStatus='DEMO', returnsStatus='DEMO', catalogRefreshedUtc=utc(), catalogStale=False,
                               storage=dict(localFreeBytes=64 * 1024**3, msiFreeBytes=256 * 1024**3, scope='Synthetic preview'))
        elif self.snapshot_path.exists():
            snapshot = read_json(self.snapshot_path)
            need(set(snapshot) == {'format', 'owner', 'refreshedUtc', 'catalog'} and snapshot['format'] == 'interlinked-hub-catalog-v1' and snapshot['owner'] == self.identity,
                 'Cached hub catalog identity/schema differs.')
            self._install_catalog(snapshot['catalog'])
            self.health.update(catalogRefreshedUtc=snapshot['refreshedUtc'], libraryStatus='CACHED_NOT_CHECKED')
        self._adopt_preferences(self.config.status())
        if auto_refresh and not runtime.demo:
            self.refresh()

    @staticmethod
    def _demo_catalog():
        return dict(catalogId=CATALOG, projects=[
            dict(id='mw5-companion', name='MW5 Lancemate Companion', description='Synthetic mod workspace', bytes=1024**3, files=43,
                 workspaceRelative='Files/MW5', warnings=['DEMO — no editor or personal files are used.'], notes=[]),
            dict(id='demo-code-data', name='Synthetic coding and data', description='Synthetic Python workspace', bytes=256*1024**2, files=31,
                 workspaceRelative='Files/Code', warnings=['DEMO — no project code is executed.'], notes=[]),
        ])

    def _install_catalog(self, value):
        self.projects = self._catalog_projects(value)
        self.local_at = 0

    def _catalog_projects(self, value):
        rows = validate_catalog(value)
        projects = {}
        reviewed = {pid.casefold():pid for pid in self.magix_ids}
        for row in rows:
            if row['id'].casefold() in reviewed:
                # These are the same owned caches, not another adapter's data.
                # Exact reviewed MAGIX IDs retain the MAGIX-only workflow.
                need(row['id'] == reviewed[row['id'].casefold()], 'Library project aliases a reviewed MAGIX ID.')
                continue
            key = key_for(row['id'])
            need(key not in self.magix_ids and key not in projects, 'Hub adapter ID collision.')
            projects[key] = row
        return projects

    def _command(self, kind):
        python = str(self.r.python)
        if kind == 'catalog':
            return [python, '-I', '-B', '-X', 'utf8', str(Path(self.r.kit)/'Project-Library-Client/project_library.py'),
                    '--library', LIBRARY, '--returns', RETURNS, '--cache', str(self.r.cache), 'list']
        need(kind == 'probe', 'Unknown hub probe.')
        # Fixed code and fixed share identities; no user command enters this helper.
        script = ("import json,os,shutil; L=" + repr(str(self.r.cache.parent)) + "; R=" + repr(RETURNS) + "; "
                  "v={'returnsReadable':os.path.isdir(R),'localFreeBytes':shutil.disk_usage(L).free}; "
                  "v.update(msiFreeBytes=shutil.disk_usage(R).free) if v['returnsReadable'] else None; print(json.dumps(v))")
        return [python, '-I', '-B', '-c', script]

    def _local_storage(self):
        try:
            path = Path(self.r.cache)
            need(not str(path).startswith('\\\\'), 'Local cache volume required.')
            while not path.exists() and path != path.parent:
                path = path.parent
            ordinary(path)
            return dict(localFreeBytes=shutil.disk_usage(path).free, checkedUtc=utc(), scope='Local free-space observation only.')
        except (OSError,ValueError):
            return {}

    def _run(self, kind):
        command = self._command(kind)
        ordinary(self.r.python)
        if kind == 'catalog':
            ordinary(Path(self.r.kit)/'Project-Library-Client/project_library.py')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with tempfile.TemporaryFile(dir=self.root) as output, tempfile.TemporaryFile(dir=self.root) as error:
            child = subprocess.Popen(command, stdout=output, stderr=error, stdin=subprocess.DEVNULL,
                                     shell=False, creationflags=flags)
            deadline = time.monotonic()+12
            while child.poll() is None:
                if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > MAX_JSON or os.fstat(error.fileno()).st_size > 1024*1024:
                    # This is exclusively our read-only discovery child, never an
                    # operation-owning library process or an editing application.
                    child.kill()
                    child.wait()
                    raise OSError('Read-only MSI discovery timed out or exceeded its output limit.')
                time.sleep(.05)
            need(child.returncode == 0, 'MSI discovery unavailable; existing local catalog retained.')
            output.seek(0)
            content = output.read(MAX_JSON+1)
            need(len(content) <= MAX_JSON, 'MSI discovery output is too large.')
        result = json.loads(content.decode('utf-8-sig'))
        need(isinstance(result, dict), 'MSI discovery returned an invalid object.')
        return result

    def refresh(self):
        with self.lock:
            need(not self.closed, 'Hub is closing.')
            if self.r.demo or self.health['refreshing']:
                return self.status()
            self.health['refreshing'] = True
            self.worker = threading.Thread(target=self._refresh_worker, daemon=True, name='INTERLINKED-readonly-discovery')
            self.worker.start()
            return self.status()

    def _refresh_worker(self):
        try:
            catalog = self.runner('catalog')
            rows = validate_catalog(catalog)
            prospective = self._catalog_projects(dict(catalogId=CATALOG, projects=rows))
            refreshed = utc()
            with self.lock:
                if self.closed:
                    return
                write_json(self.snapshot_path, dict(format='interlinked-hub-catalog-v1', owner=self.identity, refreshedUtc=refreshed,
                                                    catalog=dict(catalogId=CATALOG, projects=rows)))
                self.projects=prospective;self.local_at=0
                self.health.update(libraryStatus='AVAILABLE', catalogRefreshedUtc=refreshed, catalogStale=False, warning=None)
            try:
                probe = self.runner('probe')
                need(type(probe.get('returnsReadable')) is bool, 'Invalid returns probe.')
                storage = {}
                for key in ('localFreeBytes', 'msiFreeBytes'):
                    if key in probe:
                        need(type(probe[key]) is int and probe[key] >= 0, 'Invalid space probe.')
                        storage[key] = probe[key]
                with self.lock:
                    self.health.update(returnsStatus='AVAILABLE_READ_CHECK_ONLY' if probe['returnsReadable'] else 'UNAVAILABLE',
                                       storage=dict(storage, checkedUtc=utc(), scope='Free-space observation; save/fetch recheck independently.'))
            except (OSError, ValueError, KeyError, TypeError):
                with self.lock:
                    self.health.update(returnsStatus='NOT_CHECKED', storage=self._local_storage())
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            with self.lock:
                self.health.update(libraryStatus='UNAVAILABLE', returnsStatus='NOT_CHECKED', catalogStale=True,
                                   warning=str(exc), storage=self._local_storage())
        finally:
            with self.lock:
                self.health['refreshing'] = False

    def status(self):
        with self.lock:
            return copy.deepcopy(self.health)

    def lookup(self, uiid):
        with self.lock:
            need(isinstance(uiid, str) and uiid in self.projects, 'Unknown library project.')
            row = copy.deepcopy(self.projects[uiid])
            row.update(originalId=row['id'], id=uiid, adapter='library', type=project_type(row), projectType=project_type(row))
            return row

    def original_id(self, uiid):
        return self.lookup(uiid)['originalId']

    def _cache_state(self, row):
        root = Path(self.r.cache)/row['originalId']
        ordinary(root, missing=True)
        state_path = root/'state.json'
        if not state_path.exists():
            return None
        # Match the unchanged client's Path serialization, including the trailing
        # separator Windows retains for a UNC share root. Keep identity exact.
        expected = dict(projectId=row['originalId'], catalogId=CATALOG,
                        library=str(Path(LIBRARY)), returns=str(Path(RETURNS)))
        owner = read_json(root/'OWNER.json', 1024*1024)
        need(owner.get('format') == 'project-library-owned-v1' and all(owner.get(k) == v for k,v in expected.items()), 'Library cache ownership differs.')
        state = read_json(state_path)
        need(state.get('format') == 'project-library-cache-v1' and all(state.get(k) == v for k,v in expected.items()), 'Library cache identity differs.')
        if state.get('savedHead'):
            head_id(state['savedHead'])
            need(state['savedHead'].get('projectId') == row['originalId'], 'Saved version belongs to another project.')
        return state

    def cache_state(self, uiid):
        """Owned metadata only; callers must still acquire the client operation lock."""
        return self._cache_state(self.lookup(uiid))

    def _verification(self, row, state):
        path = Path(self.r.cache)/row['originalId']/'events.jsonl'
        if not path.exists():
            return None
        ordinary(path)
        with open(fs(path), 'rb') as stream:
            stream.seek(0, 2)
            length = stream.tell()
            start = max(0, length-512*1024)
            stream.seek(start)
            lines = stream.read(512*1024+1).splitlines()
        if start and lines:
            lines = lines[1:]
        for raw in reversed(lines):
            try:
                event = json.loads(raw)
                if event.get('event') != 'VERIFY_PASSED':
                    continue
                stamp = event.get('time')
                need(type(stamp) in (int, float) and 0 < stamp <= time.time()+300, 'Invalid verification receipt time.')
                version = head_id(event.get('head'))
                return dict(utc=utc(stamp), versionId=version, currentBaseline=event.get('head') == state.get('baselineHead'),
                            scope='Recorded verification receipt; current file contents have not been rechecked.')
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
        return None

    def _local_summary(self, row):
        try:
            if self.r.demo:
                return dict(cache=dict(status='DEMO', warning=None), lastSaved=None, lastVerified=None)
            state = self._cache_state(row)
            if state is None:
                return dict(cache=dict(status='ABSENT', warning=None), lastSaved=None, lastVerified=None)
            status = 'PENDING_SAVE' if state.get('pendingSave') else state.get('status', 'UNKNOWN')
            if status == 'READY':
                working = (Path(self.r.cache)/row['originalId']).joinpath(*row['workspaceRelative'].split('/'))
                ordinary(working)
                need(working.is_dir(), 'Managed project working folder is missing.')
            saved = None
            if state.get('savedHead') and type(state.get('savedUnixTime')) in (int,float):
                saved = dict(utc=utc(state['savedUnixTime']), versionId=head_id(state['savedHead']))
            return dict(cache=dict(status=status, warning=None), lastSaved=saved, lastVerified=self._verification(row,state))
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            return dict(cache=dict(status='CHECK_REQUIRED', warning=str(exc)), lastSaved=None, lastVerified=None)

    def rows(self):
        with self.lock:
            if time.monotonic()-self.local_at > 4:
                self.local_cache = {key:self._local_summary(self.lookup(key)) for key in self.projects}
                self.local_at = time.monotonic()
            rows = []
            recents = {r['id']:r['utc'] for r in self.prefs['recent']}
            for key in self.projects:
                row = self.lookup(key)
                row.update(copy.deepcopy(self.local_cache.get(key, {})))
                status = row['cache']['status']
                ready = status in ('READY','DEMO')
                available = self.health['libraryStatus'] in ('AVAILABLE','DEMO') and not self.health['catalogStale']
                row.update(capabilities=dict(fetch=available and status in ('ABSENT','RELEASED','FETCHING'), openFolder=ready,
                           openApplication=ready and row['type']=='mw5', finish=ready, save=available and status in ('READY','PENDING_SAVE','DEMO'),
                           verify=available and ready, release=available and ready), mediaStatus='NOT_APPLICABLE', mediaSummary=None,
                           mediaCounts={}, favorite=key in self.prefs['favorites'], recentUtc=recents.get(key),
                           catalogStale=self.health['catalogStale'], catalogRefreshedUtc=self.health['catalogRefreshedUtc'],
                           contentVerification='Metadata only; explicit verification is required.')
                rows.append(row)
            return rows

    def invalidate(self):
        with self.lock:
            self.local_at = 0

    def folder_path(self, uiid):
        row = self.lookup(uiid)
        state = self._cache_state(row)
        need(state is not None and state.get('status') == 'READY' and not state.get('pendingSave'), 'Fetch and finish pending operations before opening this workspace.')
        root = Path(self.r.cache)/row['originalId']
        path = root.joinpath(*working_relative(row['workspaceRelative']).split('/'))
        ordinary(path)
        need(path.is_dir(), 'Managed project working folder is missing.')
        return path

    def arguments(self, action, uiid, confirmation=None):
        need(action in ('fetch','save','verify','release'), 'Unsupported library action.')
        row = self.lookup(uiid)
        if action == 'release':
            need(confirmation == row['originalId'], 'Type the complete original project ID to release local files.')
        command = self._command('catalog')[:-1] + [action,row['originalId']]
        if action == 'release':
            command += ['--confirm',row['originalId']]
        return command

    def _validate_preferences(self, value):
        need(set(value) == {'format','favorites','recent'} and value['format'] == 'interlinked-hub-preferences-v1', 'Unknown hub preference schema.')
        favorites, recent = value['favorites'], value['recent']
        need(isinstance(favorites,list) and len(favorites)<=500 and
             all(isinstance(v,str) and UI_ID.fullmatch(v) for v in favorites) and len(set(favorites))==len(favorites), 'Invalid favorite projects.')
        need(isinstance(recent,list) and len(recent)<=50, 'Invalid recent projects.')
        seen=set()
        for row in recent:
            need(isinstance(row,dict) and set(row)=={'id','utc'} and isinstance(row['id'],str) and UI_ID.fullmatch(row['id']) and row['id'] not in seen,
                 'Invalid recent project row.')
            need(isinstance(row['utc'],str) and len(row['utc'])<=40 and dt.datetime.fromisoformat(row['utc']).utcoffset() is not None, 'Invalid recent project time.')
            seen.add(row['id'])
        return copy.deepcopy(value)

    def _known(self, uiid):
        need(isinstance(uiid,str) and (uiid in self.projects or uiid in self.magix_ids), 'Choose a known project.')

    def _check_preference_owner(self):
        ordinary(self.root)
        need(read_json(self.root/'OWNER.json') == self.identity, 'Hub state belongs to a different installation.')
        return self.identity

    def _adopt_preferences(self, status):
        self.preference_status = copy.deepcopy(status)
        self.prefs = copy.deepcopy(status['value']) if status['available'] else dict(format='interlinked-hub-preferences-v1', favorites=[], recent=[])

    def history(self):
        return self.config.history()

    def preview_restore(self, body):
        need(isinstance(body,dict) and set(body)=={'snapshotId'}, 'Select one configuration snapshot.')
        return self.config.preview(body['snapshotId'])

    def restore(self, body):
        need(isinstance(body,dict) and set(body)=={'previewId'}, 'Select one configuration restore preview.')
        with self.lock:
            need(not self.closed, 'Hub is closing.')
            self._adopt_preferences(self.config.restore(body['previewId']))
            return self.preferences()

    def preferences(self):
        with self.lock:
            self._adopt_preferences(self.config.status())
            return copy.deepcopy(dict(favorites=self.prefs['favorites'],recent=self.prefs['recent'],
                preferencesAvailable=self.preference_status['available'], preferenceRevision=self.preference_status['revision'],
                preferencesError=self.preference_status['error']))

    def set_favorite(self, uiid, enabled):
        with self.lock:
            need(not self.closed and type(enabled) is bool, 'Invalid favorite request.')
            self._known(uiid)
            def change(value):
                value['favorites']=[v for v in value['favorites'] if v!=uiid]
                if enabled:value['favorites'].append(uiid)
                return self._validate_preferences(value)
            self._adopt_preferences(self.config.update(change, reason='Favorite changed'))
            return self.preferences()

    def mark_recent(self, uiid):
        with self.lock:
            need(not self.closed, 'Hub is closing.')
            self._known(uiid)
            def change(value):
                value['recent']=([dict(id=uiid,utc=utc())]+[v for v in value['recent'] if v['id']!=uiid])[:50]
                return self._validate_preferences(value)
            self._adopt_preferences(self.config.update(change, reason='Recent selection changed'))
            return self.preferences()

    def close(self):
        with self.lock:
            self.closed=True
