"""Indexed MAGIX projects using local working copies and journaled local aliases.

This module leaves the reviewed single-project core and sealed client unchanged.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

BASE_SHA='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa'
INDEX_SHA='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'
BUDDY_SHA='f4dc148f5be75929d4e323440314956f56d551e4354086d3149a1bdfa0ab1d49'
PROTOCOL='magix-multiple-projects-v1'
PREFIX='Drive_C/Users/jlewi/'
ROOTS=('OneDrive/Documents','OneDrive/Pictures','OneDrive/Desktop','Downloads',
       'OneDrive - The University of Akron/The Rialto Theatre',
       'AppData/Roaming/MAGIX/Videodeluxe27_plus_steam')
PARENTS=('OneDrive - The University of Akron','AppData','AppData/Roaming','AppData/Roaming/MAGIX')
APP=Path(r'C:\Program Files (x86)\Steam\steamapps\common\MAGIX\Video deluxe 2021 Plus Steam Edition\Videodeluxe.exe')

def need(value,message):
    if not value: raise RuntimeError(message)

def sha(data): return hashlib.sha256(data).hexdigest()

def alias_relative(relative_path):
    for root in ROOTS:
        prefix=PREFIX+root+'/'
        if relative_path.casefold().startswith(prefix.casefold()):
            return root,relative_path[len(prefix):]
    return None

def load_base(path):
    need(sha(Path(path).read_bytes())==BASE_SHA,'Reviewed base launcher changed.')
    spec=importlib.util.spec_from_file_location('magix_reviewed_base',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def load_index(path,engine,expected=None):
    need(engine.file_digest(path)==(expected or INDEX_SHA),'The reviewed project index changed.')
    value=engine.read_json(path)
    need(value.get('format')=='magix-launcher-index-v1','Unknown project index.')
    need(value.get('catalogId')=='msi-project-library-v1','Unexpected catalog identity.')
    ids=set();pairs=set()
    for row in value['projects']:
        project=engine.identifier(row['id']);need(project.casefold() not in ids,'Duplicate project ID.');ids.add(project.casefold())
        need(row['category']=='magix-project','Non-project archive in picker.')
        need(set(row['aliasRoots']).issubset(ROOTS),'Unsupported alias root.')
        for entry in row['entryPoints']:
            rel=engine.relative(entry['path'])
            need(Path(rel).suffix.casefold() in ('.mvp','.mv_','.mvd'),'Unsupported entrypoint.')
            need(any(rel.casefold().startswith((PREFIX+r+'/').casefold()) for r in row['aliasRoots']),'Entrypoint is outside its reviewed alias roots.')
            pair=(project,rel.casefold());need(pair not in pairs,'Duplicate entrypoint.');pairs.add(pair)
    need(ids and pairs,'Empty project index.')
    return value

class Projects:
    def __init__(self,base,anchor,index,profile_guard=lambda:None):
        self.b=base;self.a=anchor;self.e=anchor.e;self.c=anchor.c;self.j=anchor.j
        self.index=index;self.rows={p['id']:p for p in index['projects']}
        self.profile_guard=profile_guard
        self.journal=self.c.state/'multi-binding.json'

    def old_identity(self): return self.a.marker_identity()
    def identity(self): return dict(self.old_identity(),workflowProtocol=PROTOCOL)

    def exact_owner(self,allow_old=False):
        self.e.directory(self.c.state)
        owner=self.e.read_json(self.c.state/'OWNER.json')
        need(owner==self.identity() or (allow_old and owner==self.old_identity()),'Unknown workspace control identity.')
        return owner

    @contextlib.contextmanager
    def operation(self):
        self.exact_owner()
        with self.e.exclusive(self.c.state/'.workflow.lock',create=True):
            self.exact_owner();self.profile_guard();self.a.installation()
            try: yield
            except BaseException as exc:
                try:self.a.event('MULTI_PROJECT_BLOCKED',error=str(exc),automaticRelease=False)
                except Exception:pass
                raise

    def activate(self):
        """Preserve exact old marker bytes; do not reinterpret the anchor cache."""
        self.exact_owner(allow_old=True)
        with self.e.exclusive(self.c.state/'.workflow.lock',create=True):
            self.profile_guard();self.a.closed();self.a.installation()
            owner=self.exact_owner(allow_old=True)
            marker=self.c.state/'OWNER.json';backup=self.c.state/'OWNER-before-multiple-projects.bin'
            intent=self.c.state/'multiple-projects-activation.json'
            if owner==self.old_identity():
                # Old aliases are reconciled under their original cache lock first.
                self.a.unbind_locked()
                with self.e.reader(marker) as stream: raw=stream.read(1024*1024)
                need(json.loads(raw)==self.old_identity(),'Anchor owner changed before activation.')
                if self.e.exists(backup):
                    with self.e.reader(backup) as stream:preserved=stream.read(1024*1024)
                    need(preserved==raw,'Preserved original marker differs.')
                else:
                    with open(self.e.fs(backup),'xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
                record=dict(format=PROTOCOL,originalOwnerSha256=sha(raw),newIdentity=self.identity())
                if self.e.exists(intent):need(self.e.read_json(intent)==record,'Activation intent differs.')
                else:self.e.atomic_json(intent,record,replace=False)
                self.e.atomic_json(marker,self.identity())
            else:
                record=self.e.read_json(intent)
                need(record==dict(format=PROTOCOL,originalOwnerSha256=self.e.file_digest(backup),newIdentity=self.identity()),'Activation preservation/intent differs.')
                with self.e.reader(backup) as stream:preserved=stream.read(1024*1024)
                need(json.loads(preserved)==self.old_identity(),'Original marker backup is not the anchor identity.')
            if not self.e.exists(self.journal):
                need(not self.e.exists(self.c.link),'Cannot initialize multiple aliases over an existing link.')
                self.e.atomic_json(self.journal,dict(format=PROTOCOL,status='UNBOUND',projectId=None,aliases=[]),replace=False)
            self.read_binding()
            return dict(status='ALL_PROJECTS_READY',projects=len(self.rows),anchorCacheNeedNotBeReady=True,automaticRelease=False)

    def selected(self,project):
        need(project in self.rows,'Choose a project from the reviewed MAGIX index.')
        manager=self;Base=self.b.Workspace
        class Selected(Base):
            def operation(self):return manager.operation()
            def prepare(self):
                manager.switch_away(self.c.project)
                return super().prepare()
            def bind(self):return manager.bind(self)
            def unbind(self):return manager.unbind_selected(self.c.project)
            def unbind_locked(self):return manager.unbind_for_action(self.c.project)
            def selected_file(self,relative_path):
                relative_path=self.e.relative(relative_path)
                need(Path(relative_path).suffix.casefold() in ('.mvp','.mv_','.mvd'),'Choose an MVP or explicit MAGIX recovery file.')
                path=self.e.join(self.c.project_root/'Files',relative_path)
                need(self.e.exists(path),'Selected local project is missing. READY deletions are preserved; choose another saved/recovery file or request an explicit repair.')
                self.e.regular(path)
                return path
        return Selected(replace(self.c,project=project),self.e,processes=self.a.processes)

    def link(self,root):
        need(root in ROOTS,'Unsupported legacy root.')
        # The leaf is intentionally a reparse point. Only its ordinary parents
        # use directory guards; the leaf is opened by the no-follow Junction API.
        return self.c.legacy/Path(root)
    def target(self,project,root):
        need(project in self.rows and root in ROOTS,'Invalid alias identity.')
        # Derivation is lexical so exact owned aliases can be removed even if
        # their target vanished or became unsafe. Bind separately requires
        # every target ancestor to be an ordinary local directory.
        return self.c.cache/project/'Files'/Path(PREFIX+root)

    def read_binding(self):
        record=self.e.read_json(self.journal)
        need(record.get('format')==PROTOCOL and record.get('status') in ('UNBOUND','BINDING','BOUND','UNBINDING'),'Unknown multiple-alias journal.')
        if record['status']=='UNBOUND':
            need(record.get('projectId') is None and record.get('aliases')==[],'Invalid unbound journal.')
        else:
            need(record.get('projectId') in self.rows,'Unknown recorded active project.')
            seen=set()
            for row in record['aliases']:
                root=row['root'];need(root in ROOTS and root not in seen,'Invalid/duplicate recorded alias.');seen.add(root)
                need(row['target']==str(self.target(record['projectId'],root)),'Recorded target was not derived from its project.')
                need(row['phase'] in ('PLANNED','CREATED','BOUND','REMOVED'),'Unknown alias operation.')
                if row.get('junction'):
                    need(row['junction']['fileId']==row.get('createdId') and row['junction']['target']==self.b.NT_PREFIX+row['target'],'Recorded junction identity differs.')
            need(record['aliases'],'Active transaction contains no aliases.')
        return record

    def save_binding(self,record):self.e.atomic_json(self.journal,record)

    def private_directory(self,path):
        """Give creator rights at creation, without editing inherited target ACLs."""
        class Attributes(ctypes.Structure):
            _fields_=[('length',ctypes.c_uint32),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int)]
        sid=self.c.sid
        need(sid.startswith('S-1-5-21-') and all(c.isdigit() or c in 'S-' for c in sid),'Invalid creator SID.')
        adv=ctypes.WinDLL('advapi32',use_last_error=True)
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
        desc=ctypes.c_void_p()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:(A;;FA;;;'+sid+')(A;;FA;;;SY)(A;;FA;;;BA)',1,ctypes.byref(desc),None):raise ctypes.WinError(ctypes.get_last_error())
        k=self.e.native();k.CreateDirectoryW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(Attributes)];k.LocalFree.argtypes=[ctypes.c_void_p]
        attrs=Attributes(ctypes.sizeof(Attributes),desc,0)
        try:
            if not k.CreateDirectoryW(self.e.fs(path),ctypes.byref(attrs)):raise ctypes.WinError(ctypes.get_last_error())
        finally:k.LocalFree(desc)

    def rename_directory(self,source,destination,identity):
        """Rename the same held native object, without adopting a replacement."""
        self.e.directory(source);self.e.directory(destination.parent)
        need(not self.e.exists(destination),'Refusing an existing intermediate parent destination.')
        class Rename(ctypes.Structure):
            _fields_=[('replace',ctypes.c_uint32),('root',ctypes.c_void_p),('length',ctypes.c_uint32),('name',ctypes.c_wchar*1)]
        encoded=self.e.fs(destination).encode('utf-16-le')
        buffer=ctypes.create_string_buffer(max(ctypes.sizeof(Rename),Rename.name.offset+len(encoded)+2))
        header=Rename.from_buffer(buffer);header.replace=0;header.root=None;header.length=len(encoded)
        ctypes.memmove(ctypes.addressof(buffer)+Rename.name.offset,encoded,len(encoded))
        with self.j.handle(source,0x80|0x10000) as handle:
            info=self.j.identity(handle)
            need(info['fileId']==identity and not info['attributes']&0x400,'Intermediate parent changed before rename.')
            k=self.e.native();k.SetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
            if not k.SetFileInformationByHandle(handle,3,buffer,len(buffer)):raise ctypes.WinError(ctypes.get_last_error())
            need(self.j.identity(handle)['fileId']==identity,'Intermediate parent identity changed during rename.')
        need(self.j.directory_id(destination)==identity,'Renamed intermediate parent identity differs.')

    def scaffolding(self,required=()):
        """Known ordinary intermediate directories are never adopted by name."""
        path=self.c.state/'multi-parents.json'
        ledger=self.e.read_json(path) if self.e.exists(path) else dict(format=PROTOCOL,parents={})
        need(ledger.get('format')==PROTOCOL and set(ledger['parents']).issubset(PARENTS),'Invalid scaffolding ownership.')
        desired=[p for p in PARENTS if any(r.startswith(p+'/') for r in required)]
        for suffix in PARENTS:
            dest=self.e.join(self.c.legacy,suffix);row=ledger['parents'].get(suffix)
            if row:
                need(row['status'] in ('CREATING','READY'),'Unknown parent operation.')
                if row['status']=='CREATING':
                    temp=dest.parent/row['temporaryName']
                    need(row['temporaryName'].startswith('.MAGIX-parent-') and Path(row['temporaryName']).name==row['temporaryName'],'Invalid temporary parent name.')
                    need(row.get('fileId'),'Interrupted parent creation has no committed identity; preserve it for manual review.')
                    if self.e.exists(dest):
                        need(not self.e.exists(temp) and self.j.directory_id(dest)==row['fileId'],'Parent creation destination differs.')
                    else:
                        need(self.j.directory_id(temp)==row['fileId'],'Temporary parent identity changed.')
                        self.rename_directory(temp,dest,row['fileId'])
                    row['status']='READY';self.e.atomic_json(path,ledger)
                need(self.j.directory_id(dest)==row['fileId'],'Owned intermediate parent replaced.')
            elif suffix in desired:
                need(not self.e.exists(dest),'Unowned legacy intermediate directory exists: '+str(dest))
                with self.guard_dirs(dest.parent):
                    row=dict(status='CREATING',temporaryName='.MAGIX-parent-'+uuid.uuid4().hex,fileId=None)
                    ledger['parents'][suffix]=row;self.e.atomic_json(path,ledger)
                    temp=dest.parent/row['temporaryName'];self.private_directory(temp)
                    row['fileId']=self.j.directory_id(temp);self.e.atomic_json(path,ledger)
                    self.rename_directory(temp,dest,row['fileId']);row['status']='READY';self.e.atomic_json(path,ledger)
        return ledger

    @contextlib.contextmanager
    def guard_dirs(self,*paths):
        with contextlib.ExitStack() as stack:
            seen=set()
            for path in paths:
                for item in reversed((path,*path.parents)):
                    if item not in seen:stack.enter_context(self.e.directory_guard(item));seen.add(item)
            yield

    def check_all_links(self,record):
        recorded={r['root']:r for r in record['aliases']}
        self.scaffolding()
        for root in ROOTS:
            path=self.link(root)
            # Missing owned parent is allowed only before that root is ever used.
            if not self.e.exists(path.parent):continue
            self.e.directory(path.parent)
            row=recorded.get(root)
            if row is None or row['phase']=='REMOVED':
                need(not self.e.exists(path),'Unrecorded legacy alias/path exists: '+str(path))
            elif self.e.exists(path):
                need(row.get('createdId'),'Existing alias lacks a committed creator identity; preserve for review.')
                with self.j.handle(path) as handle:
                    info=self.j.identity(handle)
                    need(info['fileId']==row['createdId'],'Owned alias object was replaced.')
                    if info['attributes']&0x400:
                        info=self.j.info_handle(handle)
                        need(info['target']==self.b.NT_PREFIX+row['target'],'Owned alias target changed.')
                        if row.get('junction'):need(info==row['junction'],'Owned junction metadata changed.')
                    else:
                        need(row['phase']=='CREATED' and info['attributes']&0x10,'Unexpected ordinary path at alias.')
            else:
                need(record['status']=='UNBINDING' or row['phase']=='PLANNED','Active owned alias is missing; use Recover to reconcile an interrupted removal.')

    def remove_plain_created(self,path,identity):
        self.e.directory(path)
        need(not list(os.scandir(self.e.fs(path))),'Partial junction contains unexpected files; nothing removed.')
        with self.j.handle(path,0x80|0x10000) as handle:
            info=self.j.identity(handle)
            need(info['fileId']==identity and not info['attributes']&0x400,'Partial junction identity changed.')
            k=self.e.native();k.SetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
            flag=ctypes.c_int(1)
            if not k.SetFileInformationByHandle(handle,4,ctypes.byref(flag),ctypes.sizeof(flag)):raise ctypes.WinError(ctypes.get_last_error())

    def unbind(self):
        self.a.closed();record=self.read_binding()
        if record['status']=='UNBOUND':self.check_all_links(record);return
        # Durable removal intent permits exact absent-link reconciliation after a crash.
        record['status']='UNBINDING';self.save_binding(record)
        with self.guard_dirs(*(self.link(r['root']).parent for r in record['aliases'])):
            self.check_all_links(record)
            for row in record['aliases']:
                path=self.link(row['root'])
                if self.e.exists(path):
                    with self.j.handle(path) as handle:attrs=self.j.identity(handle)['attributes']
                    if attrs&0x400:
                        info=row.get('junction') or self.j.info(path)
                        need(info['fileId']==row['createdId'] and info['target']==self.b.NT_PREFIX+row['target'],'Alias changed after removal preflight.')
                        self.j.unlink(path,info)
                    else:self.remove_plain_created(path,row['createdId'])
                row['phase']='REMOVED';self.save_binding(record)
            self.save_binding(dict(format=PROTOCOL,status='UNBOUND',projectId=None,aliases=[]))

    def unbind_locked(self):
        record=self.read_binding()
        if record['status']=='UNBOUND':self.check_all_links(record);return
        selected=self.selected(record['projectId'])
        # Always use the recorded OLD project, never the newly selected project.
        with self.e.exclusive(selected.c.project_root/'.client.lock'):self.unbind()

    def switch_away(self,project):
        self.profile_guard();self.a.closed();self.unbind_locked()

    def unbind_for_action(self,project):
        self.a.closed();record=self.read_binding()
        need(record['projectId'] in (None,project),'Another project owns the active aliases. Use Recover or close its session before this operation.')
        self.unbind_locked()

    def unbind_selected(self,project):
        record=self.read_binding()
        need(record['projectId'] in (None,project),'A different project owns the active aliases.')
        self.unbind()

    def bind(self,selected):
        self.profile_guard();selected.closed();selected.ready()
        record=self.read_binding();need(record['status']=='UNBOUND','Recover the prior alias transaction before opening a project.')
        self.check_all_links(record)
        # Only existing ordinary selected-cache roots are mapped. Missing READY
        # directories remain intentional deletions, never restored from sources.
        roots=[]
        for root in ROOTS:
            target=self.target(selected.c.project,root)
            if self.e.exists(target):self.e.directory(target);roots.append(root)
        need(roots,'No supported local legacy directories exist in this working copy.')
        self.scaffolding(roots)
        paths=[self.link(r).parent for r in roots]+[self.target(selected.c.project,r) for r in roots]
        with self.guard_dirs(*paths):
            self.check_all_links(record)
            record=dict(format=PROTOCOL,status='BINDING',projectId=selected.c.project,aliases=[dict(root=r,target=str(self.target(selected.c.project,r)),phase='PLANNED',createdId=None) for r in roots])
            self.save_binding(record)
            for row in record['aliases']:
                def made(identity,row=row):
                    row.update(phase='CREATED',createdId=identity);self.save_binding(record)
                row['junction']=self.j.create(self.link(row['root']),Path(row['target']),made)
                row['phase']='BOUND';self.save_binding(record)
            self.check_all_links(record);record['status']='BOUND';self.save_binding(record)
            return record

    def recover(self):
        with self.operation():
            self.a.closed();self.unbind_locked()
            return dict(status='UNBOUND_LOCAL_DATA_RETAINED',automaticSave=False,automaticRelease=False)

    def status(self,project):
        with self.operation():
            selected=self.selected(project);path=selected.c.project_root/'state.json'
            state=self.e.read_json(path) if self.e.exists(path) else {}
            return dict(projectId=project,cacheStatus=state.get('status','ABSENT'),binding=self.read_binding(),workspace=str(selected.c.project_root/'Files'),contentVerification='Not performed by status')

    def launch_path(self,project,physical):
        """Use the short verified local alias for legacy MAGIX path limits."""
        physical=Path(physical);files=self.c.cache/project/'Files'
        relative=self.e.relative(physical.relative_to(files).as_posix())
        self.e.regular(physical)
        record=self.read_binding()
        need(record['status']=='BOUND' and record['projectId']==project,'Selected project does not own a complete active alias set.')
        self.check_all_links(record)
        mapping=alias_relative(relative)
        if mapping is None:
            need(len(str(physical))<=259,'This new project path is too long for legacy MAGIX; save it within one of the six mapped folders.')
            return physical
        root,tail=mapping
        rows=[row for row in record['aliases'] if row['root']==root]
        need(len(rows)==1 and rows[0]['phase']=='BOUND','Selected project alias is not active.')
        row=rows[0]
        need(self.j.info(self.link(root))==row['junction'],'Selected alias changed before application launch.')
        alias=self.link(root)/Path(tail)
        # Intermediate alias traversal is intentional here only after checking
        # its exact recorded junction. Both handles must identify the same leaf.
        with self.j.handle(physical) as actual,self.j.handle(alias) as mapped:
            one=self.j.identity(actual);two=self.j.identity(mapped)
            need(one==two and not one['attributes']&0x400,'Alias does not address the verified selected local file.')
        return alias

    def open(self,project,entry,prompt=input,launch=None,buddy_session=None):
        selected=self.selected(project)
        def launch_selected(path):
            alias=self.launch_path(project,path)
            if launch is not None:return launch(str(alias))
            if Path(entry).suffix.casefold()=='.mv_':
                self.e.regular(APP)
                print('Recovery selected. In MAGIX use File > Load backup project and choose:\n'+str(alias)+'\nDo not rename the backup. Save recovered work inside this selected Files working set.',flush=True)
                subprocess.Popen([str(APP)],close_fds=True)
            else:os.startfile(str(alias))
        if buddy_session:
            need(isinstance(buddy_session,str) and len(buddy_session)==32 and all(c in '0123456789abcdef' for c in buddy_session),'Invalid Buddy session identity.')
            module_path=self.c.kit/'INTERLINKED-App'/'interlinked_buddy.py'
            need(self.e.file_digest(module_path)==BUDDY_SHA,'Reviewed Buddy ingestion module changed.')
            spec=importlib.util.spec_from_file_location('interlinked_buddy_ingest',module_path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            prompt=module.magix_prompt(self,selected,project,buddy_session,self.c.kit/'INTERLINKED-GUI-State'/'buddy',prompt)
        return selected.session(entry,prompt=prompt,launch=launch_selected)

@contextlib.contextmanager
def sealed_runtime():
    kit=Path(__file__).resolve().parent
    path=kit/'INTERLINKED-App'/'interlinked_buddy.py'
    need(sha(path.read_bytes())==BUDDY_SHA,'Reviewed Buddy runtime preflight changed.')
    spec=importlib.util.spec_from_file_location('interlinked_buddy_runtime',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with module.sealed_client_reads(kit):yield

def production():
    kit=Path(__file__).resolve().parent
    base=load_base(kit/'magix_workspace.py')
    anchor=base.production()
    index=load_index(kit/'MAGIX-All-Projects-Index.json',anchor.e)
    def guard():
        # Same reviewed check as initial bootstrap; no profile or policy changes.
        stage1=kit/'MAGIX-Legacy-Path.ps1'
        need(anchor.e.file_digest(stage1)==base.STAGE1_SHA,'Reviewed profile guard changed.')
        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',f"$ErrorActionPreference='Stop'; . '{stage1}'; Initialize-Native; Assert-ProfileUnbound"],check=True,timeout=30)
    return Projects(base,anchor,index,guard)

def choose_project(manager):
    rows=manager.index['projects']
    print('\nReviewed local index: '+manager.index['catalogPublishedUtc']+'; listing/reusing READY copies does not contact MSI.')
    for n,row in enumerate(rows,1):
        state_path=manager.c.cache/row['id']/'state.json'
        status=manager.e.read_json(state_path).get('status','?') if manager.e.exists(state_path) else 'NOT FETCHED'
        print(f"{n:2}. {row['name']} | {row['bytes']/1024**3:.2f} GiB | {status} | {len(row['entryPoints'])} project/recovery entries")
    value=input('Project number (blank cancels): ').strip()
    if not value:return None
    need(value.isdigit() and 1<=int(value)<=len(rows),'Choose a displayed project number.')
    row=rows[int(value)-1]
    print('\n'+row['name']+'\n'+row['id'])
    for note in row.get('warnings',[]):print('WARNING: '+str(note))
    print('Only this family is fetched. Large conservative working sets may require substantial local space.\nSave new projects/media within this selected Files working set; files saved elsewhere are not saved to MSI.')
    return row

def choose_entry(manager,row):
    entries=row['entryPoints']
    for n,entry in enumerate(entries,1):
        label='BACKUP / use Load backup project' if Path(entry['path']).suffix.casefold()=='.mv_' else 'PROJECT'
        print(f"{n:3}. [{label}] {entry['path']}")
    print('L. Enter an existing local project path relative to this selected Files folder (for newly saved work).')
    value=input('Entry number (blank cancels): ').strip()
    if not value:return None
    if value.upper()=='L':return manager.e.relative(input('Local path relative to Files: ').strip())
    need(value.isdigit() and 1<=int(value)<=len(entries),'Choose an explicit entry number.')
    return entries[int(value)-1]['path']

def menu(manager):
    manager.activate()
    row=None
    while True:
        print('\nMAGIX local projects -- '+(row['name'] if row else 'choose a family'))
        print('1 Choose project\n2 Prepare and open project/recovery\n3 Save selected project to MSI + Verify\n4 Verify selected saved state\n5 Free selected local space\n6 Recover/unbind current aliases\n7 Selected project status\n0 Exit')
        answer=input('Choose: ').strip()
        try:
            if answer=='0':return
            if answer=='1':row=choose_project(manager);continue
            if answer=='6':result=manager.recover()
            else:
                need(row is not None,'Choose a project first.')
                selected=manager.selected(row['id'])
                if answer=='2':
                    entry=choose_entry(manager,row)
                    if entry is None:continue
                    result=manager.open(row['id'],entry)
                elif answer=='3':result=selected.save()
                elif answer=='4':result=selected.verify()
                elif answer=='5':result=selected.release(input('Type '+row['id']+' to release only its verified local files: ').strip())
                elif answer=='7':result=manager.status(row['id'])
                else:continue
            print(json.dumps(result,indent=2))
        except KeyboardInterrupt:
            print('Interrupted; no automatic save or release. Close MAGIX and use Recover.');return
        except Exception as exc:print('Blocked: '+str(exc))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='menu',choices=('menu','setup','list','open','save','verify','release','recover','status'))
    parser.add_argument('--project');parser.add_argument('--entry');parser.add_argument('--confirm');parser.add_argument('--buddy-session')
    args=parser.parse_args()
    try:
        with sealed_runtime():return run_action(args)
    except BaseException as exc:
        print(json.dumps(dict(status='BLOCKED_OR_INTERRUPTED',error=str(exc),automaticRelease=False)));return 2

def run_action(args):
    manager=production()
    if args.action=='list':result=manager.index
    elif args.action=='setup':result=manager.activate()
    elif args.action=='menu':menu(manager);return 0
    else:
        manager.activate()
        if args.action=='recover':result=manager.recover()
        else:
            need(args.project in manager.rows,'Choose --project from the index.')
            selected=manager.selected(args.project)
            if args.action=='open':
                need(args.entry,'Choose an explicit --entry relative to Files.');result=manager.open(args.project,args.entry,buddy_session=args.buddy_session)
            elif args.action=='status':result=manager.status(args.project)
            elif args.action=='release':result=selected.release(args.confirm)
            else:result=getattr(selected,args.action)()
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())

