"""Independent Stats session; fixed source connection and original cache engine.

This adapter never imports application modules into INTERLINKED's runtime.
Only the contained project interpreter opens the writable application library.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import subprocess
import threading
import time
import uuid
import webbrowser

PROJECT = 'stats-active'
ACTIVE = {'STARTING', 'RUNNING', 'STOPPING', 'RECOVERY_REQUIRED'}


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def validate_library(data, h, *, integrity=False):
    """Read registry paths and declared hashes; never instantiate a writable Store."""
    data = h.ordinary(Path(data))
    h.need(not str(data).startswith(('\\\\', '//')), 'Stats data must be on a local disk.')

    def target(value):
        h.need(isinstance(value, str) and value and '\\' not in value, 'Invalid Stats registry path.')
        rel = PurePosixPath(value)
        h.need(not rel.is_absolute() and not PureWindowsPath(value).drive and '..' not in rel.parts,
               'Stats registry target escapes its library.')
        result = h.ordinary(data.joinpath(*rel.parts))
        h.need(result.exists(), 'Registered Stats target is missing: '+value)
        return result

    database = target('stats.sqlite3')
    registries = {}
    databases = {database}
    for name in ('archive_registry.json', 'youtube_collections.json', 'shopify_collections.json',
                 'linktree_collections.json', 'tiktok_collections.json'):
        path = h.ordinary(data/name)
        value = h.read_json(path)
        rows = []
        if name == 'archive_registry.json':
            rows = value.get('archives', []) + value.get('analysis_versions', [])
            if value.get('analysis_package'):
                rows = rows + [value['analysis_package']]
        else:
            rows = value.get('collections', [])
        h.need(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), 'Invalid Stats registry entries.')
        for row in rows:
            for key in ('database', 'evidence_root', 'source_dir', 'package_dir'):
                if row.get(key):
                    item = target(row[key])
                    if key == 'database':
                        h.need(item.is_file(), 'Registered Stats database is not a file.')
                        databases.add(item)
                        if row.get('database_sha256'):
                            h.need(digest(item) == row['database_sha256'], 'Registered Stats database hash differs.')
            # Package-level hashes describe original ZIP payloads, not directory
            # hashes. Platform loaders validate those when reading collections.
        registries[name] = value
    if integrity:
        for path in databases:
            with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
                h.need(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Stats database integrity failed: '+path.name)
    return dict(registries=registries, databases=len(databases), writableDatabase=str(database))


class StatsService:
    def __init__(self, controller, g, *, tree_runner=None, browser=webbrowser.open):
        self.c, self.g = controller, g
        self.h = g.load_companion('interlinked_hub')
        self.runner = tree_runner or g.load_companion('interlinked_stats_process').run_process_tree
        self.browser = browser
        self.root = self.h.ordinary(controller.r.state/'stats', missing=True)
        self.root.mkdir(exist_ok=True)
        owner = dict(format='interlinked-stats-owner-v1', kit=str(controller.r.kit), demo=controller.r.demo)
        if (self.root/'OWNER.json').exists():
            self.h.need(self.h.read_json(self.root/'OWNER.json') == owner, 'Stats state belongs to another installation.')
        else:
            self.h.need(not any(self.root.iterdir()), 'Unowned Stats state refused.')
            self.h.write_json(self.root/'OWNER.json', owner)
        self.lock = threading.RLock()
        self.status = 'IDLE'
        self.revision = 0
        self.operation = None
        self.warning = None
        self.url = None
        self.pid = None
        self.control = None
        self.stop_requested = False
        self.thread = None
        self.library_validation = None
        self.config_identity = None
        if (self.root/'session.json').exists():
            prior = self.h.read_json(self.root/'session.json')
            self.h.need(prior.get('format') == 'interlinked-stats-session-v1', 'Unknown Stats session journal.')
            if prior.get('status') != 'IDLE':
                self.status = 'RECOVERY_REQUIRED'
                self.warning = 'Previous Stats session was interrupted. Confirm all Stats writers are closed, then acknowledge recovery.'

    def blocks_quit(self):
        with self.lock:
            return self.status in ACTIVE or bool(self.thread and self.thread.is_alive())

    def _project(self, project):
        self.h.need(self.c.hub.original_id(project) == PROJECT, 'Choose the reviewed stats-active project.')
        return self.c.hub.lookup(project)

    def _configuration(self):
        value = self.h.read_json(self.root/'connection.json')
        self.h.need(set(value) == {'format', 'sourceRoot', 'python', 'launcherSha256', 'pythonSha256', 'lockSha256', 'port'}
                    and value['format'] == 'interlinked-stats-connection-v1', 'Invalid reviewed Stats connection.')
        source = self.h.ordinary(Path(value['sourceRoot']))
        python = self.h.ordinary(Path(value['python']))
        self.h.need(not str(source).startswith(('\\\\', '//')) and python == source/'.venv/Scripts/python.exe', 'Stats needs its local project environment.')
        self.h.need(type(value['port']) is int and 1024 <= value['port'] <= 65535, 'Invalid Stats port.')
        for path, key in ((source/'scripts/launch.py', 'launcherSha256'), (python, 'pythonSha256'), (source/'requirements.lock.txt', 'lockSha256')):
            self.h.ordinary(path)
            self.h.need(digest(path) == value[key], 'Reviewed Stats runtime changed: '+path.name)
        self.h.need((source/'app.py').is_file(), 'Stats application is missing.')
        return value, source, python

    def _data(self, project):
        row = self._project(project)
        self.c.hub.folder_path(project)
        rel = row.get('environmentHints', {}).get('STATS_DATA_DIR', {}).get('relativeToWorkingSet')
        self.h.need(isinstance(rel, str), 'Stats catalog has no reviewed data hint.')
        rel = self.h.working_relative(rel)
        root = self.h.ordinary(self.c.r.cache/PROJECT)
        path = self.h.ordinary(root.joinpath(*rel.split('/')))
        self.h.need(path.is_dir(), 'Stats library is missing. Fetch stats-active first.')
        return path

    def view(self, project):
        row = self._project(project)
        if hasattr(self.c.hub, '_local_summary'):
            row.update(self.c.hub._local_summary(row))
        with self.lock:
            result = dict(status=self.status, revision=self.revision, operation=dict(self.operation) if self.operation else None,
                          url=self.url, pid=self.pid, warning=self.warning, demo=self.c.r.demo,
                          saveScope='MSI saves the managed library. Checkout code requires Git commit/push.',
                          lastSaved=row.get('lastSaved'), lastVerified=row.get('lastVerified'))
            try:
                config, source, python = self._configuration()
                data = self._data(project)
                result.update(ready=True, sourceRoot=str(source), dataRoot=str(data), port=config['port'])
            except (OSError, ValueError, KeyError) as exc:
                result.update(ready=False, reason=str(exc))
            result['running'] = self.status in {'STARTING', 'RUNNING', 'STOPPING'}
            return result

    def _journal(self, status, **extra):
        self.h.write_json(self.root/'session.json', dict(format='interlinked-stats-session-v1', status=status,
                                                       projectId=PROJECT, utc=self.h.utc(), **extra))

    def _lease(self):
        return self.c.engine.exclusive(self.h.ordinary(self.c.r.cache/PROJECT/'.client.lock'), create=False)

    def request(self, body):
        self.h.need(set(body).issubset({'project', 'action', 'expectedRevision', 'confirmation'}) and
                    {'project', 'action', 'expectedRevision'}.issubset(body), 'Unexpected Stats action fields.')
        project, action = body['project'], body['action']
        self._project(project)
        self.h.need(not self.c.r.demo, 'Stats native operations are unavailable in the synthetic demo.')
        self.h.need(isinstance(action, str) and action in {'fetch', 'start', 'open-browser', 'stop', 'save', 'verify', 'release', 'acknowledge'}, 'Unsupported Stats action.')
        # Controller -> Stats is the single lock ordering used by HTTP/state.
        with self.c.lock, self.lock:
            self.h.need(not self.c.closing, 'INTERLINKED is closing.')
            self.h.need(type(body['expectedRevision']) is int and body['expectedRevision'] == self.revision, 'Stats changed; refresh before retrying.')
            if action == 'open-browser':
                self.h.need(self.status == 'RUNNING' and self.url, 'Stats is not running.')
                self.browser(self.url)
                return dict(status='BROWSER_REQUESTED')
            if action == 'stop':
                self.h.need(self.status in {'STARTING', 'RUNNING'}, 'Stats is not running.')
                self.stop_requested = True
                self.status = 'STOPPING'
                self.revision += 1
                if self.control is not None:
                    self.control.write(b'{"action":"stop"}\n');self.control.flush()
                return dict(status='STOP_REQUESTED')
            self.h.need(not self.thread or not self.thread.is_alive(), 'A Stats operation is already active.')
            if action == 'acknowledge':
                self.h.need(self.status == 'RECOVERY_REQUIRED' and body.get('confirmation') == PROJECT, 'Confirm stats-active after closing all Stats writers.')
                with self._lease():
                    validate_library(self._data(project), self.h, integrity=True)
                    self._journal('IDLE')
                self.status = 'IDLE';self.warning = None;self.revision += 1
                return dict(status='RECOVERY_ACKNOWLEDGED', automaticSave=False)
            self.h.need(self.status not in ACTIVE, 'Stop Stats or reconcile its interrupted session first.')
            if action == 'release':
                self.h.need(body.get('confirmation') == PROJECT, 'Type stats-active to release its local library.')
            self.revision += 1
            self.warning = None
            self.operation = dict(id=uuid.uuid4().hex, action=action, status='STARTING', startedUtc=self.h.utc())
            if action == 'start':
                self.status = 'STARTING'
                self.stop_requested = False
                callback = lambda: self._run(project)
            else:
                # Transfer reservation is shared with ordinary hub/MW5 work,
                # while an unrelated editor's workflow lease remains intact.
                self.h.need(not self.c.transferring and not self.c._operation_running(), 'Another transfer is running.')
                self.c.transferring = True
                callback = lambda: self._transfer(action, project, body.get('confirmation'))
            def work():
                try:
                    callback()
                    with self.lock:self.operation.update(status='COMPLETED', finishedUtc=self.h.utc())
                except BaseException as exc:
                    with self.lock:
                        self.warning = str(exc)
                        self.operation.update(status='BLOCKED', error=str(exc), finishedUtc=self.h.utc())
                        if self.status == 'STARTING':self.status = 'IDLE'
                finally:
                    if action != 'start':
                        with self.c.lock:self.c.transferring = False
                    with self.lock:self.revision += 1
                    self.c.hub.invalidate()
            self.thread = threading.Thread(target=work, name='interlinked-stats-'+action, daemon=False)
            self.thread.start()
            return dict(self.operation)

    def _transfer(self, action, project, confirmation):
        # The unchanged client acquires its own project and library locks.
        args = self.c.hub.arguments(action, project, confirmation)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        with open(self.root/'transfer.stdout.json', 'wb') as stdout, open(self.root/'transfer.stderr.log', 'wb') as stderr:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                     cwd=self.c.r.kit, shell=False, creationflags=flags)
            code = child.wait()
        self.h.need(code == 0, 'Stats '+action+' was blocked. See the private transfer log.')
        if action == 'save':
            self._transfer('verify', project, None)
        self.c.hub.refresh()

    def _run(self, project):
        with ExitStack() as stack:
            stack.enter_context(self._lease())
            config, source, python = self._configuration()
            data = self._data(project)
            # Recheck the complete registry under the authoritative cache lock.
            self.library_validation = validate_library(data, self.h)
            self.warning = None
            identity = uuid.uuid4().hex
            outpath = self.root/(identity+'.events.jsonl')
            errpath = self.root/(identity+'.stderr.log')
            stdout = stack.enter_context(open(outpath, 'w+b', buffering=0))
            stderr = stack.enter_context(open(errpath, 'wb', buffering=0))
            reader = stack.enter_context(open(outpath, 'rb', buffering=0))
            readfd, writefd = os.pipe()
            stdin = stack.enter_context(os.fdopen(readfd, 'rb', buffering=0))
            control = stack.enter_context(os.fdopen(writefd, 'wb', buffering=0))
            environment = os.environ.copy()
            for key in ('STATS_IMPORT_DIR', 'STATS_APPROVED_USERS_FILE', 'STATS_EXPECTED_ISSUER', 'PYTHONPATH', 'PYTHONHOME'):
                environment.pop(key, None)
            environment.update(STATS_MODE='local', STATS_DATA_DIR=str(data), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            argv = [str(python), '-B', str(source/'scripts/launch.py'), '--managed', '--no-browser', '--port', str(config['port'])]
            ready = False
            exit_event = None
            buffer = b''
            began = time.monotonic()
            def started(pid):
                with self.lock:
                    self.pid = pid;self.control = control
                    self._journal('STARTING', pid=pid, operationId=self.operation['id'])
                    if self.stop_requested:
                        control.write(b'{"action":"stop"}\n');control.flush()
            def poll(count):
                nonlocal buffer, ready, exit_event
                chunk = reader.read(65536)
                buffer += chunk
                self.h.need(len(buffer) < 65536, 'Stats protocol line exceeded its limit.')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    self.h.need(event.get('protocol') == 'stats-managed-v1', 'Unexpected Stats launcher protocol.')
                    if event.get('type') == 'ready':
                        url = 'http://127.0.0.1:'+str(config['port'])
                        self.h.need(event.get('url') == url and not ready, 'Unexpected Stats readiness URL.')
                        with self.lock:
                            ready = True;self.url = url
                            if not self.stop_requested:self.status = 'RUNNING'
                            self.revision += 1;self._journal(self.status, pid=self.pid)
                        if not self.stop_requested:self.browser(url)
                    elif event.get('type') == 'error':
                        self.warning = str(event.get('message', 'Stats launch failed.'))[:1000]
                    elif event.get('type') == 'exit':exit_event = event
                    else:raise ValueError('Unexpected Stats launcher event.')
                self.h.need(ready or time.monotonic()-began < 75 or count == 0, 'Stats readiness timed out.')
            self._journal('STARTING', operationId=self.operation['id'])
            code = None
            try:
                code = self.runner(argv, cwd=source, env=environment, stdin=stdin, stdout=stdout, stderr=stderr,
                                   on_started=started, on_poll=poll)
                poll(0)
                clean = code == 0 and exit_event is not None and exit_event.get('clean') is True
                with self.lock:
                    self.status = 'IDLE' if clean else 'RECOVERY_REQUIRED'
                    self._journal(self.status, exitCode=code)
                    if not clean:self.warning = self.warning or 'Stats ended unexpectedly. Reconcile its library before another operation.'
            except BaseException:
                with self.lock:
                    self.status = 'RECOVERY_REQUIRED'
                    self._journal(self.status, exitCode=code)
                raise
            finally:
                with self.lock:self.control = None;self.pid = None;self.url = None
            self.h.need(clean, self.warning or 'Stats requires recovery.')
