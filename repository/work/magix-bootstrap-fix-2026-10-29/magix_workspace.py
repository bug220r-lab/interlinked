"""External, current-project MAGIX workflow. Never modifies the sealed client.

Preparation prototype: do not deploy before local MAGIX acceptance and review.
Uses only Python 3.12 stdlib and the checksum-verified frozen client module.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import types
import uuid

ENGINE_SHA = '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d'
SEAL_SHA = '362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc'
PROJECT = 'magix-redacted-sample-test-7cfd6b1eea'
DOCS = 'Drive_C/Users/jlewi/OneDrive/Documents'
DEFAULT_MVP = DOCS + '/redacted sample test/redacted sample test .MVP'
MEDIA = DOCS + '/MAGIX/Video deluxe 2021 Plus Steam Edition/redacted sample test.mp4'
STAGE1_SHA = '3a7332607a9fd59d460ca7eb0db8c5e46e892c67b9c9c63a68b03d076120f880'
ACCESS_SHA = 'f123d1ca489841f415bf8b21cfe799c2edc093a55b96c08d2287e0aee65aa741'
NT_PREFIX = chr(92)+'??'+chr(92)

def need(value, message):
    if not value:
        raise RuntimeError(message)

def hash_bytes(data): return hashlib.sha256(data).hexdigest()

def load_engine(path):
    need(hash_bytes(Path(path).read_bytes()) == ENGINE_SHA, 'Frozen client engine hash differs.')
    spec = importlib.util.spec_from_file_location('frozen_magix_library', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def verify_sealed_client(root):
    seal = (root/'HELPER-CHECKSUMS.json').read_bytes()
    need(hash_bytes(seal) == SEAL_SHA, 'Client checksum manifest differs.')
    value = json.loads(seal)
    need(value['format'] == 'project-library-client-checksums-v1' and len(value['files']) == 52, 'Unexpected client seal.')
    engine = load_engine(root/'project_library.py')
    for row in value['files']:
        path = engine.join(root, row['path'])
        need(engine.regular(path).st_size == row['bytes'] and engine.file_digest(path) == row['sha256'], 'Sealed client file differs: '+row['path'])
    return engine

def current_sid():
    command = [str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile', '-NonInteractive', '-Command', '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value']
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    sid = result.stdout.strip()
    need(sid.startswith('S-1-5-21-') and all(c.isdigit() or c in 'S-' for c in sid), 'Unexpected Windows account SID.')
    return sid

def magix_processes():
    """Names/PIDs only; no command lines, handles or private process contents."""
    class Entry(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('usage', ctypes.c_uint32), ('pid', ctypes.c_uint32), ('heap', ctypes.c_size_t), ('module', ctypes.c_uint32), ('threads', ctypes.c_uint32), ('parent', ctypes.c_uint32), ('priority', ctypes.c_long), ('flags', ctypes.c_uint32), ('name', ctypes.c_wchar*260)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(k, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
        getattr(k, name).restype = ctypes.c_int
    handle = k.CreateToolhelp32Snapshot(2, 0)
    need(handle != ctypes.c_void_p(-1).value, 'Cannot inspect MAGIX process state.')
    rows = []
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        ok = k.Process32FirstW(handle, ctypes.byref(entry))
        if not ok and ctypes.get_last_error() != 18: raise ctypes.WinError(ctypes.get_last_error())
        while ok:
            name = entry.name.casefold()
            if name.startswith(('videodeluxe', 'movieeditpro')) and name.endswith('.exe'):
                rows.append({'pid': entry.pid, 'name': entry.name})
            ok = k.Process32NextW(handle, ctypes.byref(entry))
            if not ok and ctypes.get_last_error() != 18: raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k.CloseHandle(handle)
    return rows

@dataclass
class Config:
    kit: Path
    cache: Path
    legacy: Path
    library: str = r'\\MSI\ProjectLibrary'
    returns: str = r'\\MSI\ProjectReturns'
    project: str = PROJECT
    catalog: str = 'msi-project-library-v1'
    sid: str = ''
    test_local: bool = False
    client_path: Path | None = None
    python_path: Path | None = None

    @property
    def project_root(self): return self.cache/self.project
    @property
    def target(self): return self.project_root/'Files'/DOCS
    @property
    def parent(self): return self.legacy/'OneDrive'
    @property
    def link(self): return self.parent/'Documents'
    @property
    def state(self): return self.kit/'MAGIX-Workspace-State'

class Junction:
    """Operations address the reparse object itself, never its destination."""
    def __init__(self, engine, sid): self.e = engine; self.sid = sid

    @contextlib.contextmanager
    def handle(self, path, access=0x80):
        k = self.e.native()
        handle = k.CreateFileW(self.e.fs(path), access, 3, None, 3, 0x02300000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try: yield handle
        finally: k.CloseHandle(handle)

    def identity(self, handle):
        class Info(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint32) for n in ('attrs', 'cl', 'ch', 'al', 'ah', 'wl', 'wh', 'volume', 'sh', 'sl', 'links', 'ih', 'il')]
        k = self.e.native(); k.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(Info)]
        row = Info()
        if not k.GetFileInformationByHandle(handle, ctypes.byref(row)): raise ctypes.WinError(ctypes.get_last_error())
        return {'fileId': f'{row.volume:08x}:{row.ih:08x}{row.il:08x}', 'attributes': row.attrs}

    def info_handle(self, handle):
        identity = self.identity(handle)
        k = self.e.native(); k.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        buffer = ctypes.create_string_buffer(16384); size = ctypes.c_uint32()
        if not k.DeviceIoControl(handle, 0x900A8, None, 0, buffer, len(buffer), ctypes.byref(size), None): raise ctypes.WinError(ctypes.get_last_error())
        raw = buffer.raw[:size.value]
        need(len(raw) >= 16 and struct.unpack_from('<I', raw)[0] == 0xA0000003, 'Only an owned directory junction is accepted.')
        offset, length = struct.unpack_from('<HH', raw, 8)
        need(length % 2 == 0 and 16+offset+length <= len(raw), 'Invalid junction metadata.')
        identity['target'] = raw[16+offset:16+offset+length].decode('utf-16-le')
        need(identity['attributes'] & 0x410 == 0x410, 'Expected a directory junction.')
        return identity

    def info(self, path):
        with self.handle(path) as handle: return self.info_handle(handle)

    def directory_id(self, path):
        self.e.directory(path)
        with self.handle(path) as handle: return self.identity(handle)['fileId']

    def can_manage(self, parent, link=None):
        try:
            with self.handle(parent, 0x80 | 0x4 | 0x40): pass
            if link is not None and self.e.exists(link):
                with self.handle(link, 0x80 | 0x10000): pass
            return True
        except OSError as exc:
            if exc.winerror == 5: return False
            raise

    def create(self, path, target, made):
        self.e.directory(path.parent); self.e.directory(target)
        need(not self.e.exists(path), 'Legacy Documents already exists; refusing overwrite.')
        # The fresh junction object needs creator access even when its parent's
        # inherited child ACL is RX. This DACL never applies to its target.
        class Attributes(ctypes.Structure):
            _fields_=[('length',ctypes.c_uint32),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int)]
        need(self.sid.startswith('S-1-5-21-') and all(c.isdigit() or c in 'S-' for c in self.sid),'Invalid local creator SID.')
        advapi=ctypes.WinDLL('advapi32',use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
        descriptor=ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:(A;;FA;;;'+self.sid+')(A;;FA;;;SY)(A;;FA;;;BA)',1,ctypes.byref(descriptor),None):raise ctypes.WinError(ctypes.get_last_error())
        k=self.e.native();k.CreateDirectoryW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(Attributes)];k.LocalFree.argtypes=[ctypes.c_void_p]
        attributes=Attributes(ctypes.sizeof(Attributes),descriptor,0)
        try:
            if not k.CreateDirectoryW(self.e.fs(path),ctypes.byref(attributes)):raise ctypes.WinError(ctypes.get_last_error())
        finally:k.LocalFree(descriptor)
        with self.handle(path, 0x40000000 | 0x80) as handle:
            made(self.identity(handle)['fileId']) # durable intent before setting reparse metadata
            sub_bytes = (NT_PREFIX+str(target)).encode('utf-16-le')
            printed = str(target).encode('utf-16-le')
            payload = sub_bytes+b'\0\0'+printed+b'\0\0'
            raw = struct.pack('<IHHHHHH', 0xA0000003, 8+len(payload), 0, 0, len(sub_bytes), len(sub_bytes)+2, len(printed))+payload
            k = self.e.native(); k.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            buffer = ctypes.create_string_buffer(raw); size = ctypes.c_uint32()
            if not k.DeviceIoControl(handle, 0x900A4, buffer, len(raw), None, 0, ctypes.byref(size), None): raise ctypes.WinError(ctypes.get_last_error())
        return self.info(path)

    def unlink(self, path, expected):
        with self.handle(path, 0x80 | 0x10000) as handle:
            need(self.identity(handle)['fileId'] == expected['fileId'], 'Owned junction identity changed; nothing removed.')
            need(self.info_handle(handle) == expected, 'Owned junction target/metadata changed; nothing removed.')
            k = self.e.native(); k.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            flag = ctypes.c_int(1)
            if not k.SetFileInformationByHandle(handle, 4, ctypes.byref(flag), ctypes.sizeof(flag)): raise ctypes.WinError(ctypes.get_last_error())
        need(not self.e.exists(path), 'Junction removal not confirmed.')

class Workspace:
    def __init__(self, config, engine, processes=magix_processes):
        self.c = config; self.e = engine; self.j = Junction(engine,config.sid); self.processes = processes

    def closed(self): need(not self.processes(), 'Close all MAGIX/crash windows first. No application is closed automatically.')
    def event(self, name, **values):
        self.e.atomic_json(self.c.state/('event-'+uuid.uuid4().hex+'.json'), dict(event=name, utc=time.time(), projectId=self.c.project, **values), replace=False)

    def marker_identity(self):
        return dict(format='magix-workspace-owned-v1', projectId=self.c.project, cache=str(self.c.cache), legacy=str(self.c.legacy), target=str(self.c.target), ownerSid=self.c.sid)

    def state_root(self, create=False):
        if not self.e.exists(self.c.state):
            need(create, 'Set up the managed MAGIX workflow first.')
            self.e.directory(self.c.kit); os.mkdir(self.e.fs(self.c.state))
            self.e.atomic_json(self.c.state/'OWNER.json', self.marker_identity(), replace=False)
        need(self.e.read_json(self.c.state/'OWNER.json') == self.marker_identity(), 'Workspace control folder is unowned or belongs to another setup.')
        self.e.directory(self.c.state)

    @contextlib.contextmanager
    def operation(self):
        self.state_root()
        with self.e.exclusive(self.c.state/'.workflow.lock', create=True):
            try: yield
            except BaseException as exc:
                self.event('BLOCKED_OR_INTERRUPTED', error=str(exc), automaticRelease=False)
                raise

    def run_client(self, command, confirmation=None):
        client = self.c.client_path or self.c.kit/'Project-Library-Client/project_library.py'
        python = self.c.python_path or self.c.kit/'Project-Library-Client/Runtime/python.exe'
        need(self.e.file_digest(client) == ENGINE_SHA, 'Frozen engine changed.')
        args = [str(python), '-B', '-X', 'utf8', str(client), '--library', self.c.library, '--returns', self.c.returns, '--cache', str(self.c.cache)]
        if self.c.test_local: args.append('--test-local')
        args += [command, self.c.project]
        if confirmation is not None: args += ['--confirm', confirmation]
        # Only this known data-copy CLI's JSON is captured. Progress stays visible.
        process = subprocess.run(args, stdout=subprocess.PIPE, text=True, encoding='utf-8', errors='strict')
        result = json.loads(process.stdout)
        need(process.returncode == 0, result.get('error', 'Frozen client operation failed; cache preserved.'))
        return result

    def ready(self):
        owner = self.e.read_json(self.c.project_root/'OWNER.json')
        expected = dict(projectId=self.c.project, catalogId=self.c.catalog, library=str(Path(self.c.library)), returns=str(Path(self.c.returns)))
        need(owner == dict(format='project-library-owned-v1', **expected), 'Cache ownership differs.')
        state = self.e.read_json(self.c.project_root/'state.json')
        need(all(state.get(k) == v for k, v in expected.items()), 'Cache state identity differs.')
        need(state.get('status') == 'READY' and 'pendingSave' not in state, 'Cache is not READY or has a pending save. Preserve it and finish the client operation first.')
        return state

    def prepare(self):
        self.closed()
        state_path = self.c.project_root/'state.json'
        state = self.e.read_json(state_path) if self.e.exists(state_path) else None
        if state is None or state.get('status') in ('RELEASED', 'FETCHING'):
            self.event('FETCH_REQUESTED', previousStatus=state.get('status') if state else None)
            self.run_client('fetch')
        # READY edits AND deletions are intentional local state. Never undo them.
        return self.ready()

    @contextlib.contextmanager
    def parent_guards(self):
        with contextlib.ExitStack() as stack:
            for path in reversed((self.c.parent, *self.c.parent.parents)):
                stack.enter_context(self.e.directory_guard(path))
            yield

    def installation(self):
        installation = self.e.read_json(self.c.state/'installation.json')
        need(installation['identity'] == self.marker_identity(), 'Installation identity differs.')
        need(self.j.directory_id(self.c.legacy) == installation['rootId'] and self.j.directory_id(self.c.parent) == installation['parentId'], 'Owned legacy parent directories were replaced.')
        return installation

    def migrate_stage1(self):
        self.state_root(create=True)
        with self.operation():
            self.closed(); self.ready()
            with self.e.exclusive(self.c.project_root/'.client.lock'), self.parent_guards():
                source = self.c.legacy/'.MAGIX-Local-Junction-Owner.json'
                owner = self.e.read_json(source)
                need(owner['Format'] == 'magix-local-junction-owner-v1' and owner['Computer'] == 'THIS-MACHINE' and owner['ScriptSha256'] == STAGE1_SHA and owner['OwnerSid'] == self.c.sid and owner['ProjectId'] == self.c.project and owner['LegacyRoot'] == str(self.c.legacy) and owner['Link'] == str(self.c.link) and owner['Target'] == str(self.c.target), 'Exact reviewed Stage1 ownership is required for explicit migration.')
                root_id = self.j.directory_id(self.c.legacy); parent_id = self.j.directory_id(self.c.parent)
                need(owner['RootIdentity'] == root_id and owner['ParentIdentity'] == parent_id, 'Stage1 parent identities differ.')
                info = self.j.info(self.c.link)
                need(info['fileId'] == owner['LinkIdentity'] and info['target'] == NT_PREFIX+str(self.c.target), 'Stage1 junction identity/target differs.')
                manage = self.j.can_manage(self.c.parent, self.c.link)
                preserved = self.c.state/'stage1-owner-preserved.json'
                if self.e.exists(preserved): need(self.e.read_json(preserved) == owner, 'Preserved Stage1 evidence differs.')
                else: self.e.atomic_json(preserved, owner, replace=False)
                record = dict(format='magix-workspace-installation-v1', identity=self.marker_identity(), rootId=root_id, parentId=parent_id, stage1OwnerSha256=self.e.file_digest(source), stage1Preserved=True, normalUserManageRights=manage, accessGrantRequired=not manage)
                installation_path = self.c.state/'installation.json'
                if self.e.exists(installation_path):
                    previous = self.installation()
                    need(previous['stage1OwnerSha256'] == record['stage1OwnerSha256'], 'Interrupted migration evidence differs.')
                    record = previous
                else: self.e.atomic_json(installation_path, record, replace=False)
                binding_path = self.c.state/'binding.json'
                if self.e.exists(binding_path):
                    need(self.binding() == dict(status='BOUND', projectId=self.c.project, junction=info), 'Existing migrated binding changed; use the normal lifecycle.')
                else: self.e.atomic_json(binding_path, dict(status='BOUND', projectId=self.c.project, junction=info), replace=False)
                self.event('STAGE1_EXPLICITLY_ADOPTED', accessGrantRequired=not manage, originalMarkerChanged=False)
                return record

    def setup(self):
        self.state_root(create=True)
        complete=self.e.exists(self.c.state/'installation.json') and self.e.exists(self.c.state/'binding.json')
        if complete:
            with self.operation(): self.closed(); self.installation(); self.binding()
        else:
            self.migrate_stage1()
        self.parent_access()
        return {'status':'SETUP_READY','projectId':self.c.project,'next':'Run MAGIX-Local-Workspace.cmd and choose Prepare and open.'}

    def binding(self):
        self.installation()
        record = self.e.read_json(self.c.state/'binding.json')
        need(record['projectId'] == self.c.project, 'Another project owns this active alias.')
        if record['status'] == 'BOUND':
            need(self.j.info(self.c.link) == record['junction'], 'Active junction differs; refusing changes.')
        elif record['status'] == 'UNBOUND':
            need(not self.e.exists(self.c.link), 'Unrecorded legacy Documents path exists.')
        elif record['status'] == 'BINDING':
            info = self.j.info(self.c.link)
            need(info['fileId'] == record.get('createdId') and info['target'] == NT_PREFIX+str(self.c.target), 'Interrupted binding is not safely recoverable automatically.')
            record = dict(status='BOUND', projectId=self.c.project, junction=info)
            self.e.atomic_json(self.c.state/'binding.json', record)
            self.event('COMMITTED_JUNCTION_RECOVERED')
        elif record['status'] == 'UNBINDING':
            if self.e.exists(self.c.link):
                need(self.j.info(self.c.link) == record['junction'], 'Interrupted unbind now points to a different link.')
            else:
                record = dict(status='UNBOUND', projectId=self.c.project, previousJunction=record['junction'])
                self.e.atomic_json(self.c.state/'binding.json', record)
                self.event('EXACT_UNLINK_COMMIT_RECOVERED')
        else:
            raise RuntimeError('Unknown binding status; review retained state.')
        return record

    def bind(self):
        self.closed(); self.ready(); self.e.directory(self.c.target)
        with self.parent_guards():
            record = self.binding()
            if record['status'] == 'BOUND': return record
            need(record['status'] == 'UNBOUND', 'Finish interrupted unbinding with Recover first.')
            need(self.j.can_manage(self.c.parent), 'Normal-user parent rights are insufficient; use the reviewed one-time access setup.')
            record = dict(status='BINDING', projectId=self.c.project, target=str(self.c.target), createdId=None)
            self.e.atomic_json(self.c.state/'binding.json', record)
            def made(identity):
                record['createdId'] = identity
                self.e.atomic_json(self.c.state/'binding.json', record)
            info = self.j.create(self.c.link, self.c.target, made)
            need(info['target'] == NT_PREFIX+str(self.c.target), 'New local junction target differs.')
            record = dict(status='BOUND', projectId=self.c.project, junction=info)
            self.e.atomic_json(self.c.state/'binding.json', record)
            self.event('LOCAL_JUNCTION_BOUND', junction=info, sourceFilesChanged=False)
            return record

    def unbind(self):
        self.closed()
        with self.parent_guards():
            record = self.binding()
            if record['status'] == 'UNBOUND': return
            self.e.atomic_json(self.c.state/'binding.json', dict(status='UNBINDING', projectId=self.c.project, junction=record['junction']))
            self.j.unlink(self.c.link, record['junction'])
            self.e.atomic_json(self.c.state/'binding.json', dict(status='UNBOUND', projectId=self.c.project, previousJunction=record['junction']))
            self.event('EXACT_JUNCTION_UNBOUND', targetFilesDeleted=False)

    def unbind_locked(self):
        self.closed()
        if self.e.exists(self.c.project_root/'.client.lock'):
            with self.e.exclusive(self.c.project_root/'.client.lock'): self.unbind()
        else:
            self.unbind()

    def save(self):
        with self.operation():
            self.unbind_locked()
            saved = self.run_client('save')
            verified = self.run_client('verify')
            self.event('SAVE_AND_VERIFY_COMPLETED', save=saved, verify=verified)
            return dict(save=saved, verify=verified)

    def verify(self):
        with self.operation():
            self.unbind_locked()
            result = self.run_client('verify'); self.event('VERIFY_COMPLETED', result=result)
            return result

    def release(self, confirmation):
        need(confirmation == self.c.project, 'Free space requires typing the complete project ID. No unattended release.')
        with self.operation():
            self.unbind_locked()
            result = self.run_client('release', confirmation)
            self.event('EXPLICIT_RELEASE_COMPLETED', result=result)
            return result

    def selected_file(self, relative_path):
        relative_path = self.e.relative(relative_path)
        need(relative_path.startswith(DOCS+'/') and Path(relative_path).suffix.casefold() in ('.mvp', '.mvd', '.mv_'), 'Choose a MAGIX project/recovery file inside this local Documents working set.')
        path = self.e.join(self.c.project_root/'Files', relative_path)
        need(self.e.exists(path), 'Selected project is missing. READY deletions are preserved. Choose an existing recovery/project copy or request a specific repair; no source file was restored.')
        self.e.regular(path)
        media = self.e.join(self.c.project_root/'Files', MEDIA)
        need(self.e.exists(media), 'Required local video is missing: '+str(media)+'. READY deletions are preserved; use an explicit repair after reviewing local changes.')
        self.e.regular(media)
        return path

    def session(self, relative_path=DEFAULT_MVP, prompt=input, launch=os.startfile):
        """The user chooses Done. Shell/Steam process exit is never completion."""
        with self.operation():
            self.closed(); self.prepare()
            with self.e.exclusive(self.c.project_root/'.client.lock'), contextlib.ExitStack() as guards:
                self.closed(); self.ready()
                path = self.selected_file(relative_path)
                for parent in reversed((path.parent, *path.parent.parents)):
                    guards.enter_context(self.e.directory_guard(parent))
                self.bind()
                self.event('SESSION_STARTING', selectedFile=str(path), clientLockHeld=True, offlineReadyReuse=True)
                observer = Observer(self.processes); observer.start()
                launched=False; cancelled=False
                try:
                    launch(str(path))
                    launched=True
                    print('\nMAGIX is opening. Keep this window open while editing.\nThe cache lock stays held through Steam startup delays.\nSave in MAGIX, close all its windows, then type DONE here.\nIf no MAGIX app ever opens, dismiss pending Steam/launch dialogs and type CANCEL STARTUP.\nClosing this helper unexpectedly leaves a recovery receipt; it never frees files.\n', flush=True)
                    while True:
                        try:
                            answer = prompt('Type DONE after MAGIX closes (or CANCEL STARTUP): ').strip().upper()
                        except KeyboardInterrupt:
                            print('\nProtection remains active. Close MAGIX and type DONE; Ctrl+C does not release its cache lock.')
                            continue
                        if answer == 'CANCEL STARTUP':
                            if observer.seen.is_set() or self.processes():
                                print('MAGIX was detected. Save/close it and use DONE.'); continue
                            time.sleep(1)
                            if observer.seen.is_set() or self.processes():
                                print('MAGIX appeared during the check. Close it and use DONE.'); continue
                            cancelled=True
                            self.unbind()
                            self.event('STARTUP_CANCELLED_LOCAL_DATA_RETAINED',savedToMsi=False,automaticRelease=False)
                            print('Startup cancelled; local files are retained. If a queued app opens later, close it and reopen through this launcher.')
                            return {'status':'STARTUP_CANCELLED_LOCAL_DATA_RETAINED'}
                        if answer != 'DONE':
                            print('No action. Keep this window open while working.'); continue
                        if observer.error:
                            print('Cannot verify MAGIX process state; protection remains active: '+observer.error)
                            continue
                        if not observer.seen.is_set():
                            print('MAGIX has not been observed yet; the cache remains locked. If startup failed, dismiss pending launch dialogs and type CANCEL STARTUP.'); continue
                        if self.processes():
                            print('MAGIX is still running. Save and close it first.'); continue
                        time.sleep(1)
                        self.closed()
                        self.unbind()
                        self.event('SESSION_DONE_LOCAL_COPY_RETAINED', selectedFile=str(path), savedToMsi=False, automaticRelease=False)
                        print('Done. The alias is unbound and local work is retained. Choose Save + Verify when MSI is available.')
                        return {'status':'DONE_LOCAL_COPY_RETAINED','projectId':self.c.project}
                except BaseException:
                    try: self.event('SESSION_INTERRUPTED_BINDING_RETAINED', automaticRelease=False, recoveryRequired=True)
                    except Exception: pass
                    if launched and not cancelled:
                        print('Session reporting/input failed. Keeping the cache locked until MAGIX is confirmed closed. No save, unbind or release is automatic.',file=sys.stderr,flush=True)
                        while True:
                            try:
                                running=self.processes()
                                if running: observer.seen.set()
                                if not running and observer.seen.is_set():
                                    time.sleep(1)
                                    if not self.processes(): break
                            except BaseException:
                                pass # an inspection failure or Ctrl+C cannot mean the app closed
                            try: time.sleep(0.25)
                            except KeyboardInterrupt: pass
                    raise
                finally:
                    observer.close()

    def recover(self):
        with self.operation():
            self.closed(); self.unbind_locked()
            self.event('RECOVERY_UNBOUND_LOCAL_DATA_RETAINED', automaticSave=False, automaticRelease=False)
            return {'status':'UNBOUND_LOCAL_DATA_RETAINED'}

    def parent_access(self, restore=False):
        with self.operation():
            self.closed(); self.installation()
            incomplete_grant=self.e.exists(self.c.state/'access-grant-intent.json') and not self.e.exists(self.c.state/'access-grant.json')
            if not restore and self.j.can_manage(self.c.parent, self.c.link) and not incomplete_grant:
                return {'status':'NORMAL_USER_ACCESS_ALREADY_SUFFICIENT','aclChanged':False}
            if restore: self.unbind_locked()
            helper=self.c.kit/'MAGIX-Workspace-Access.ps1'
            need(self.e.file_digest(helper)==ACCESS_SHA,'Reviewed parent-access helper differs or is not frozen.')
            with self.e.exclusive(self.c.project_root/'.client.lock'), self.parent_guards():
                args=[str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper),'-Action','Restore' if restore else 'Grant']
                process=subprocess.run(args)
                need(process.returncode==0,'Parent-access action did not complete. Preserve its before/after receipts.')
                if not restore: need(self.j.can_manage(self.c.parent,self.c.link),'Normal-user access is still insufficient after the grant.')
            self.event('PARENT_ACCESS_RESTORED' if restore else 'PARENT_ACCESS_GRANTED', cacheAclChanged=False)
            return {'status':'PARENT_ACCESS_RESTORED' if restore else 'PARENT_ACCESS_GRANTED'}

    def status(self):
        with self.operation():
            state = self.e.read_json(self.c.project_root/'state.json') if self.e.exists(self.c.project_root/'state.json') else {}
            return dict(projectId=self.c.project, cacheStatus=state.get('status','ABSENT'), binding=self.binding()['status'], magixProcesses=self.processes(), workspace=str(self.c.project_root/'Files'), sourceAndContentVerification='Not performed by status')

class Observer:
    def __init__(self, processes):
        self.processes=processes; self.seen=threading.Event(); self.stop=threading.Event(); self.error=None
        self.thread=threading.Thread(target=self.watch,daemon=True)
    def watch(self):
        while not self.stop.is_set():
            try:
                if self.processes(): self.seen.set()
                self.error=None
            except Exception as exc: self.error=str(exc)
            self.stop.wait(0.25)
    def start(self): self.thread.start()
    def close(self): self.stop.set(); self.thread.join(timeout=2)

def production():
    need(os.name == 'nt' and os.environ.get('COMPUTERNAME','').casefold() == 'this-machine', 'Run locally on THIS-MACHINE.')
    kit = Path(__file__).resolve().parent
    need(kit == Path(r'C:\Users\Jude\Documents\MSI-Project-Library'), 'Run from the local MSI Project Library kit.')
    need(not ctypes.windll.shell32.IsUserAnAdmin(), 'Run normally, without administrator mode.')
    need(Path(os.environ['USERPROFILE']) == Path(r'C:\Users\Jude'), 'Unexpected current user profile.')
    engine = verify_sealed_client(kit/'Project-Library-Client')
    stage1=kit/'MAGIX-Legacy-Path.ps1'
    need(engine.file_digest(stage1)==STAGE1_SHA,'Reviewed Stage1 helper differs.')
    subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',f"$ErrorActionPreference='Stop'; . '{stage1}'; Initialize-Native; Assert-ProfileUnbound"],check=True,timeout=30)
    config = Config(kit,Path(r'C:\Users\Jude\AppData\Local\ProjectLibrary\Cache'),Path(r'C:\Users\jlewi'),sid=current_sid())
    return Workspace(config,engine)

def menu(workspace):
    while True:
        print('\nMAGIX local workspace -- current project only\n1 Set up / adopt verified Stage1 mapping (once)\n2 Prepare and open project\n3 Save to MSI + Verify\n4 Verify saved state\n5 Free local space (explicit confirmation)\n6 Recover/unbind after interrupted session\n7 Status\n8 Finish optional parent-access setup\n9 Restore original parent permissions\n0 Exit')
        answer=input('Choose: ').strip()
        try:
            if answer == '0': return
            if answer == '1':
                result=workspace.setup()
            elif answer == '2':
                value=input('Project path relative to Files (Enter for original project): ').strip() or DEFAULT_MVP
                result=workspace.session(value)
            elif answer == '3': result=workspace.save()
            elif answer == '4': result=workspace.verify()
            elif answer == '5': result=workspace.release(input('Type '+workspace.c.project+' to free only this verified cache: ').strip())
            elif answer == '6': result=workspace.recover()
            elif answer == '7': result=workspace.status()
            elif answer == '8':
                result=workspace.parent_access()
            elif answer == '9':
                need(input('Type RESTORE to unbind and restore the recorded parent permissions: ').strip()=='RESTORE','Cancelled.');result=workspace.parent_access(restore=True)
            else: continue
            print(json.dumps(result,indent=2))
        except KeyboardInterrupt:
            print('\nInterrupted. Data was not automatically released. Close MAGIX and use Recover before another action.'); return
        except Exception as exc:
            print('Blocked: '+str(exc))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='menu',choices=['menu','setup','open','save','verify','release','recover','status'])
    parser.add_argument('--project-file',default=DEFAULT_MVP)
    parser.add_argument('--confirm')
    args=parser.parse_args()
    try:
        workspace=production()
        if args.action=='menu': menu(workspace); return 0
        if args.action=='setup':
            need(args.confirm=='ADOPT','Use the one-time Setup command.');result=workspace.setup()
        elif args.action=='open':result=workspace.session(args.project_file)
        elif args.action=='release':result=workspace.release(args.confirm)
        else:result=getattr(workspace,args.action)()
        print(json.dumps(result,indent=2));return 0
    except BaseException as exc:
        print(json.dumps({'status':'BLOCKED_OR_INTERRUPTED','error':str(exc),'automaticRelease':False}));return 2

if __name__=='__main__':raise SystemExit(main())
