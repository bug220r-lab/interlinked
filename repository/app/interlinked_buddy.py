"""Scoped ED-IT Buddy companion and additive, lock-owner asset ingestion.

Standard library only. This module never launches MAGIX, saves/releases caches,
patches project files or accepts shell commands. Request payloads stay in owned
local state; the active launcher receives only an opaque request identifier.
"""
from __future__ import annotations
from contextlib import contextmanager, ExitStack
import ctypes
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

FORMAT='interlinked-buddy-v1'
ID=re.compile(r'^[a-f0-9]{32}$')
PREFIX='Drive_C/Users/jlewi/'
MEDIA_ROOTS=('OneDrive/Documents','Downloads','OneDrive/Desktop','OneDrive/Pictures')
MAX_MANIFEST=1024*1024
CLIENT_SEAL_SHA='362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc'

def need(value,message):
    if not value:raise ValueError(message)
def utc():return dt.datetime.now(dt.timezone.utc).isoformat()
def rid(value):
    need(isinstance(value,str) and ID.fullmatch(value),'request_id must be32 lowercase hex characters.');return value
def ordinary(path,missing=False):
    path=Path(path);need(path.is_absolute(),'Absolute local path required.')
    need(':' not in str(path)[len(path.anchor):] and not any(ord(c)<32 for c in str(path)),'Alternate streams and control characters are refused.')
    for p in reversed((path,*path.parents)):
        try:s=os.lstat(p)
        except FileNotFoundError:
            if missing:continue
            raise
        a=getattr(s,'st_file_attributes',0)
        if os.name=='nt' and a&0x40000:
            k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetFileAttributesW.argtypes=[ctypes.c_wchar_p];k.GetFileAttributesW.restype=ctypes.c_uint32
            current=k.GetFileAttributesW(str(p));need(current!=0xffffffff,'Cannot inspect path attributes.');a=(a&~0x40000)|current
        need(not stat.S_ISLNK(s.st_mode) and not a&(0x400|0x1000|0x40000|0x400000),'Links/offline placeholders are refused: '+str(p))
    return path
def leaf(path):
    path=ordinary(path);s=path.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'An ordinary non-hardlinked file is required.');return path
def digest(path):
    result=hashlib.sha256()
    with leaf(path).open('rb') as src:
        for block in iter(lambda:src.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

@contextmanager
def _runtime_reader(path):
    """Ordinary read with a retained no-write/no-delete native file handle."""
    path=leaf(path)
    if os.name!='nt':
        with path.open('rb') as stream:yield stream
        return
    import msvcrt
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=k.CreateFileW(str(path),0x80000000,1,None,3,0x08200000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    class Info(ctypes.Structure):
        _pack_=4
        _fields_=[('attrs',ctypes.c_uint32),('created',ctypes.c_uint64),('accessed',ctypes.c_uint64),('written',ctypes.c_uint64),('volume',ctypes.c_uint32),('hiSize',ctypes.c_uint32),('loSize',ctypes.c_uint32),('links',ctypes.c_uint32),('hiId',ctypes.c_uint32),('loId',ctypes.c_uint32)]
    k.GetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.POINTER(Info)]
    try:
        info=Info()
        if not k.GetFileInformationByHandle(handle,ctypes.byref(info)):raise ctypes.WinError(ctypes.get_last_error())
        need(not info.attrs&(0x400|0x1000|0x40000|0x400000) and info.links==1,'Sealed runtime handle is linked/offline/recall.')
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY);handle=None
        try:stream=os.fdopen(fd,'rb')
        except BaseException:os.close(fd);raise
        with stream:yield stream
    finally:
        if handle is not None:k.CloseHandle(handle)

@contextmanager
def _sealed_reads(root,seal_sha256,count):
    """Prepare only the immutable accepted client set; never relax path guards."""
    root=ordinary(root)
    with ExitStack() as held:
        seal=held.enter_context(_runtime_reader(root/'HELPER-CHECKSUMS.json'));raw=seal.read(MAX_MANIFEST+1)
        need(len(raw)<=MAX_MANIFEST and hashlib.sha256(raw).hexdigest()==seal_sha256,'Immutable client seal differs.')
        manifest=json.loads(raw.decode('utf-8-sig'))
        need(manifest.get('format')=='project-library-client-checksums-v1' and len(manifest.get('files',[]))==count,'Unexpected sealed client set.')
        names=set()
        for row in manifest['files']:
            rel=relative(row['path']);need(str(rel).casefold() not in names,'Duplicate sealed client path.');names.add(str(rel).casefold())
            need(type(row['bytes']) is int and 0<=row['bytes']<=128*1024*1024 and re.fullmatch('[a-f0-9]{64}',row['sha256']),'Invalid sealed client entry.')
            stream=held.enter_context(_runtime_reader(root.joinpath(*rel.parts)));result=hashlib.sha256();size=0
            for block in iter(lambda:stream.read(1024*1024),b''):
                size+=len(block);need(size<=row['bytes'],'Sealed client file grew.');result.update(block)
            need(size==row['bytes'] and result.hexdigest()==row['sha256'],'Sealed client file differs: '+str(rel))
        yield

@contextmanager
def sealed_client_reads(kit):
    """Retain verified ordinary runtime files through the unchanged launcher."""
    with _sealed_reads(Path(kit)/'Project-Library-Client',CLIENT_SEAL_SHA,52):yield
def read(path,limit=MAX_MANIFEST):
    with leaf(path).open('rb') as stream:raw=stream.read(limit+1)
    need(len(raw)<=limit,'Metadata exceeds its size limit.')
    value=json.loads(raw.decode('utf-8-sig'));need(isinstance(value,dict),'Metadata object required.');return value
def write(path,value):
    path=ordinary(path,missing=True);ordinary(path.parent)
    if path.exists():leaf(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8',newline='\n') as out:
        json.dump(value,out,ensure_ascii=False,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
    os.replace(temporary,path)
def relative(value):
    need(isinstance(value,str) and value and '\\' not in value and ':' not in value and '\0' not in value,'Portable relative asset path required.')
    path=PurePosixPath(value)
    need(not path.is_absolute() and all(p not in ('','.','..') for p in value.split('/')),'Unsafe relative asset path.')
    need(all(not p.endswith((' ','.')) and not re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',p) for p in path.parts),'Reserved Windows asset name.')
    return path
def sid():
    if os.name!='nt':return str(os.getuid())
    value=subprocess.check_output(['whoami.exe','/user','/fo','csv','/nh'],text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    match=re.search(r'S-1-5-21-[0-9-]+',value);need(match,'Current user SID unavailable.');return match.group(0)
def private_directory(path):
    ordinary(path.parent)
    if path.exists():ordinary(path);return
    if os.name!='nt':path.mkdir(mode=0o700);return
    class Attr(ctypes.Structure):_fields_=[('length',ctypes.c_uint32),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int)]
    adv=ctypes.WinDLL('advapi32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    desc=ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FA;;;'+sid()+')(A;;FA;;;SY)',1,ctypes.byref(desc),None):raise ctypes.WinError(ctypes.get_last_error())
    k.CreateDirectoryW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(Attr)];k.LocalFree.argtypes=[ctypes.c_void_p]
    try:
        if not k.CreateDirectoryW(str(path),ctypes.byref(Attr(ctypes.sizeof(Attr),desc,0))):raise ctypes.WinError(ctypes.get_last_error())
    finally:k.LocalFree(desc)
def private_acl(path):
    """Check the protected same-user capability boundary, not just its marker."""
    if os.name!='nt':
        need(path.stat().st_mode&0o077==0,'Private directory permissions changed.');return
    adv=ctypes.WinDLL('advapi32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes=[ctypes.c_wchar_p,ctypes.c_int,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    desc=ctypes.c_void_p();string=ctypes.c_void_p();k.LocalFree.argtypes=[ctypes.c_void_p]
    code=adv.GetNamedSecurityInfoW(str(path),1,4,None,None,None,None,ctypes.byref(desc))
    if code:raise OSError(code,'Cannot inspect companion directory ACL.')
    try:
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(desc,1,4,ctypes.byref(string),None):raise ctypes.WinError(ctypes.get_last_error())
        text=ctypes.wstring_at(string);aces=re.findall(r'\(([^)]+)\)',text)
        allowed={'SY',sid()}
        def permitted(ace):
            fields=ace.split(';')
            return len(fields)==6 and fields[0]=='A' and fields[1] in ('','OI','CI','OICI') and fields[2]=='FA' and fields[3:5]==['',''] and fields[5] in allowed
        need(text.startswith('D:P') and aces and all(permitted(a) for a in aces),'Private directory ACL changed.')
    finally:
        if string:k.LocalFree(string)
        k.LocalFree(desc)
def owned_state(root,identity):
    root=Path(root);ordinary(root.parent);private_directory(root)
    marker=root/'OWNER.json'
    if marker.exists():need(read(marker)==identity,'Buddy state ownership differs.')
    else:
        need(not list(root.iterdir()),'Existing Buddy state is unowned.');write(marker,identity)
    for name in ('requests','operations','reports','actions'):
        path=root/name
        if not path.exists():private_directory(path)
        ordinary(path)
    return root

def load_manifest(path,staging_root,request_id,project_id):
    path=leaf(Path(os.path.abspath(path)));root=ordinary(Path(os.path.abspath(staging_root)))
    private_acl(root)
    need(read(root/'OWNER.json')==dict(format='buddy-assistant-staging-v1',sid=sid(),computer=os.environ.get('COMPUTERNAME','')),'Buddy staging ownership differs.')
    need(path.is_relative_to(root),'Manifest is outside Buddy staging.')
    value=read(path)
    need(set(value)=={'format','request_id','project_id','files'} and value['format']=='buddy-asset-manifest-v1','Unsupported asset manifest.')
    need(value['request_id']==request_id and value['project_id']==project_id,'Manifest request/project differs.')
    need(isinstance(value['files'],list) and 0<len(value['files'])<=1000,'Manifest needs1–1000 files.')
    seen=set();total=0
    for row in value['files']:
        need(isinstance(row,dict) and set(row)=={'path','bytes','sha256'},'Unexpected asset fields.')
        rel=relative(row['path']);key=str(rel).casefold();need(key not in seen,'Duplicate asset path.');seen.add(key)
        need(type(row['bytes']) is int and 0<row['bytes']<=2**43 and isinstance(row['sha256'],str) and re.fullmatch('[a-f0-9]{64}',row['sha256']),'Invalid asset length/hash.')
        source=leaf(path.parent/Path(rel));need(source.stat().st_size==row['bytes'] and digest(source)==row['sha256'],'Staged asset differs: '+str(rel));total+=row['bytes']
    need(not any(a.startswith(b+'/') for a in seen for b in seen if a!=b),'A file cannot also be a parent directory.')
    return value,digest(path),total

def verify_directory(directory,files):
    ordinary(directory)
    expected={str(relative(row['path'])).casefold():row for row in files};actual=set()
    for parent,dirs,names in os.walk(directory,followlinks=False):
        ordinary(parent)
        for name in dirs:ordinary(Path(parent)/name)
        for name in names:
            p=leaf(Path(parent)/name);rel=p.relative_to(directory).as_posix().casefold();need(rel in expected,'Unexpected published file.')
            row=expected[rel];need(p.stat().st_size==row['bytes'] and digest(p)==row['sha256'],'Published asset differs.');actual.add(rel)
    need(actual==set(expected),'Published assets are incomplete.')

def ingest_files(state,request,destination,files_root,alias_root=None):
    """Caller MUST own the reviewed workflow and selected cache locks."""
    request_id=rid(request['request_id']);project=request['project_id']
    identity=read(state/'OWNER.json');need(identity.get('format')==FORMAT,'Unknown Buddy state.')
    record_path=state/'operations'/(request_id+'.json')
    manifest,manifest_sha,total=load_manifest(Path(request['manifest_path']),Path(request['staging_root']),request_id,project)
    need(manifest_sha==request['manifest_sha256'],'Manifest changed after submission.')
    destination=ordinary(destination);files_root=ordinary(files_root)
    need(destination.is_relative_to(files_root),'Destination is outside the selected Files tree.')
    parent=destination/'BuddyAssets'
    if not parent.exists():parent.mkdir()
    ordinary(parent)
    final=parent/request_id;temporary=parent/('.incoming-'+request_id)
    intent=dict(format='interlinked-buddy-ingest-operation-v1',request_id=request_id,project_id=project,manifest_sha256=manifest_sha,destination=str(final),status='COPYING',updated_at=utc())
    prior=None
    if record_path.exists():
        prior=read(record_path)
        need(all(prior.get(k)==intent[k] for k in ('request_id','project_id','manifest_sha256')) and prior.get('destination',str(final))==str(final),'Request identity/destination differs.')
        if final.exists():
            need(prior.get('destination')==str(final),'Existing delivery directory has no matching publication intent.')
            verify_directory(final,manifest['files'])
        else:need(prior['status'] in ('QUEUED','COPYING','UNCERTAIN','FAILED'),'Committed output is missing; recovery required.')
    else:
        need(not final.exists() and not temporary.exists(),'Existing delivery directories have no ownership intent; preserved for review.')
        write(record_path,intent)
    if not final.exists():
        need(shutil.disk_usage(destination).free>=total+16*1024*1024,'Not enough space for the staged asset delivery.')
        if temporary.exists():need(prior and prior.get('destination')==str(final),'Existing incoming directory has no matching transaction intent.')
        else:temporary.mkdir()
        ordinary(temporary)
        allowed={row['path'] for row in manifest['files']}
        for p in temporary.rglob('*'):
            ordinary(p)
            if p.is_file():need(p.relative_to(temporary).as_posix() in allowed or p.relative_to(temporary).as_posix().removesuffix('.buddy-part') in allowed,'Unexpected transaction file; preserved for review.')
        write(record_path,intent)
        for row in manifest['files']:
            rel=Path(relative(row['path']));source=leaf(Path(request['manifest_path']).parent/rel);target=temporary/rel
            if target.exists():
                need(leaf(target).stat().st_size==row['bytes'] and digest(target)==row['sha256'],'Interrupted output differs; preserved.');continue
            target.parent.mkdir(parents=True,exist_ok=True);ordinary(target.parent)
            partial=target.with_name(target.name+'.buddy-part')
            if partial.exists():leaf(partial).unlink()
            with source.open('rb') as src,partial.open('xb') as out:
                shutil.copyfileobj(src,out,1024*1024);out.flush();os.fsync(out.fileno())
            need(partial.stat().st_size==row['bytes'] and digest(partial)==row['sha256'],'Copied asset verification failed.')
            partial.rename(target)
        verify_directory(temporary,manifest['files']);need(not final.exists(),'Destination collision; existing files preserved.')
        temporary.rename(final)
    verify_directory(final,manifest['files'])
    result=dict(intent,status='COMPLETED',updated_at=utc(),files=[dict(row,project_relative_path=(final/Path(relative(row['path']))).relative_to(files_root).as_posix(),physical_path=str(final/Path(relative(row['path']))),magix_path=str(Path(alias_root)/'BuddyAssets'/request_id/Path(relative(row['path'])))) if alias_root else dict(row,project_relative_path=(final/Path(relative(row['path']))).relative_to(files_root).as_posix(),physical_path=str(final/Path(relative(row['path'])))) for row in manifest['files']],automatic_save=False,automatic_release=False)
    write(record_path,result);return result

def magix_prompt(manager,selected,project,session_id,state,prompt):
    state=ordinary(state);expected=manager.c.kit/'INTERLINKED-GUI-State'/'buddy' if hasattr(manager.c,'kit') else None
    # The caller pins the sibling module and fixes this state path in its CLI.
    need(ID.fullmatch(session_id),'Invalid Buddy session identity.')
    def wrapped(message):
        while True:
            value=prompt(message)
            match=re.fullmatch(r'BUDDY_INGEST ([a-f0-9]{32})',value)
            if not match:return value
            request_id=match.group(1)
            try:
                request=read(state/'requests'/(request_id+'.json'))
                need(request['session_id']==session_id and request['project_id']==project,'Ingest request belongs to another managed session.')
                manager.exact_owner();selected.ready()
                binding=manager.read_binding();need(binding['status']=='BOUND' and binding['projectId']==project,'Selected aliases are not bound.')
                manager.check_all_links(binding)
                choices=[root for root in MEDIA_ROOTS if any(a['root']==root and a['phase']=='BOUND' for a in binding['aliases'])]
                need(choices,'No existing mapped media root is available for this project.')
                root=choices[0];files=manager.c.cache/project/'Files'
                result=ingest_files(state,request,manager.target(project,root),files,manager.link(root))
            except Exception as exc:
                result=dict(format='interlinked-buddy-ingest-error-v1',request_id=request_id,status='FAILED',error=str(exc),updated_at=utc())
                # Keep an existing intent for crash reconciliation rather than
                # replacing its destination/provenance fields with an error.
                path=state/'operations'/(request_id+'.json')
                if path.exists():result=dict(read(path),status='FAILED',error=str(exc),updated_at=utc())
                write(path,result)
            print('\nBUDDY_RECEIPT '+json.dumps(dict(request_id=request_id,status=result['status']))+'\n',flush=True)
    return wrapped

class Companion:
    def __init__(self,g,controller,endpoint_root=None):
        self.g=g;self.c=controller;self.lock=threading.RLock();self.pending=None
        self.identity=dict(format=FORMAT,gui_owner=g.read_json(controller.r.state/'OWNER.json'))
        self.state=owned_state(controller.r.state/'buddy',self.identity)
        self.staging=Path(os.environ.get('LOCALAPPDATA',str(controller.r.state)))/'ED-IT Buddy'/'assistant'/'staging'
        self.endpoint_root=Path(endpoint_root) if endpoint_root else Path(os.environ.get('LOCALAPPDATA',str(controller.r.state)))/'INTERLINKED'/'buddy-companion'
        self.token=secrets.token_urlsafe(32);self.endpoint=None
    def publish(self,origin):
        if self.c.r.demo:return
        parent=self.endpoint_root.parent;ordinary(parent,missing=True);parent.mkdir(parents=True,exist_ok=True);ordinary(parent)
        private_directory(self.endpoint_root);private_acl(self.endpoint_root);owner=self.endpoint_root/'OWNER.json'
        identity=dict(format='interlinked-buddy-endpoint-owner-v1',sid=sid(),computer=os.environ.get('COMPUTERNAME',''),kit=str(self.c.r.kit))
        if owner.exists():need(read(owner)==identity,'Companion discovery belongs to another kit/account.')
        else:
            need(not list(self.endpoint_root.iterdir()),'Companion discovery directory is unowned.');write(owner,identity)
        self.endpoint=self.endpoint_root/'endpoint.json';write(self.endpoint,dict(format='interlinked-buddy-endpoint-v1',base_url=origin,token=self.token,pid=os.getpid(),created_at=utc()))
    def close(self):
        if self.endpoint and self.endpoint.exists() and read(self.endpoint).get('token')==self.token:self.endpoint.unlink()
    def projects(self):
        rows=[]
        for row in self.c.catalog()['projects']:
            original=row.get('originalId',row['id']);rows.append(dict(id=original,original_id=original,name=row['name'],cache=row.get('cache'),adapter=row.get('adapter'),capabilities=dict(ingest=True,live_ingest=not self.c.r.demo)))
        return dict(projects=rows,active_session=self.c.state().get('operation'),pending_ingest=self.pending)
    def project(self,project):
        rows=self.c.catalog()['projects'];matches=[r for r in rows if r.get('originalId',r['id'])==project];need(len(matches)==1,'Unknown or ambiguous project.');return matches[0]
    def project_detail(self,project):
        row=self.project(project);entries=self.c.entries(row['id']).get('entries',[])
        return dict(project_id=project,name=row['name'],entries=[dict(entry_id=hashlib.sha256((project+'\0'+entry['path']).encode('utf-8')).hexdigest(),label=Path(entry['path']).name,kind=entry.get('kind'),origin=entry.get('origin','INDEXED')) for entry in entries[:1000]],total_entries=len(entries),entries_truncated=len(entries)>1000)
    def action(self,body):
        need(isinstance(body,dict) and body.get('action') in ('open','save','verify'),'Only explicit open, save and verify actions are supported.')
        allowed={'request_id','project_id','action','entry_id'} if body['action']=='open' else {'request_id','project_id','action'}
        need(set(body)==allowed,'Unexpected lifecycle action fields.')
        request_id=rid(body['request_id']);project=body['project_id'];row=self.project(project);entry=None
        if body['action']=='open':
            need(isinstance(body['entry_id'],str) and re.fullmatch('[a-f0-9]{64}',body['entry_id']),'Choose an enumerated entry ID.')
            entries=self.c.entries(row['id']).get('entries',[])
            matches=[item for item in entries if hashlib.sha256((project+'\0'+item['path']).encode('utf-8')).hexdigest()==body['entry_id']]
            need(len(matches)==1,'Entry does not belong to the selected project or is no longer available.');entry=matches[0]['path']
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest();path=self.state/'actions'/(request_id+'.json')
        with self.c.lock,self.lock:
            need(not (self.state/'requests'/(request_id+'.json')).exists(),'Request ID already belongs to an asset delivery.')
            if path.exists():
                prior=read(path);need(prior.get('fingerprint')==fingerprint,'Lifecycle request ID reused with different work.');return self.operation(request_id)
            need(not self.pending,'Wait for the asset delivery to finish.')
            record=dict(format='interlinked-buddy-action-v1',request_id=request_id,project_id=project,action=body['action'],fingerprint=fingerprint,status='STARTING',updated_at=utc())
            write(path,record)
            try:
                job=self.c.start(body['action'],row['id'],entry,None,None)
                record.update(operation_id=job['id'],operation_receipt=str(self.c.run/('operation-'+job['id']+'.json')),status=job['status']);write(path,record)
                return self.operation(request_id)
            except Exception as exc:
                record.update(status='FAILED',error=str(exc));write(path,record);raise
    def session_response(self,body):
        need(set(body)=={'project_id','operation_id','prompt_generation','response'} and body['response']=='DONE','Only an explicit project-bound DONE response is supported.')
        row=self.project(body['project_id']);rid(body['operation_id'])
        need(type(body['prompt_generation']) is int and body['prompt_generation']>=1,'Current prompt generation required.')
        with self.c.lock:
            need(self.c.job and self.c.job.get('projectId')==row['id'],'Session belongs to another project.')
            return self.c.session_input('DONE',body['operation_id'],body['prompt_generation'])
    def operation(self,request_id):
        path=self.state/'operations'/(rid(request_id)+'.json')
        if not path.exists():
            action_path=self.state/'actions'/(request_id+'.json')
            if not action_path.exists():return dict(request_id=request_id,status='NOT_FOUND')
            value=read(action_path)
            current=self.c.job if self.c.job and self.c.job.get('id')==value.get('operation_id') else None
            if current is not None:
                value=dict(value,status=current.get('status','UNKNOWN'),operation={k:current.get(k) for k in ('id','action','projectId','status','awaitingDone','promptGeneration','exitCode','error')})
            elif value.get('operation_receipt'):
                receipt=Path(value['operation_receipt']);need(receipt.is_relative_to(self.c.r.state),'Lifecycle receipt is outside owned GUI state.')
                if receipt.exists():
                    job=read(receipt);need(job.get('id')==value.get('operation_id'),'Lifecycle operation identity differs.')
                    value=dict(value,status=job.get('status','UNKNOWN'),operation={k:job.get(k) for k in ('id','action','projectId','status','awaitingDone','promptGeneration','exitCode','error')})
            return {k:v for k,v in value.items() if k not in ('operation_receipt','fingerprint')}
        result=read(path)
        if self.pending==request_id and result.get('status') in ('COMPLETED','FAILED','UNCERTAIN'):
            with self.lock:self.pending=None
        return result
    def ingest(self,body):
        need(set(body)=={'request_id','project_id','manifest_path'},'Unexpected ingest fields.')
        request_id=rid(body['request_id']);project=body['project_id'];row=self.project(project)
        manifest,sha,_=load_manifest(Path(body['manifest_path']),self.staging,request_id,project)
        with self.c.lock,self.lock:
            need(not self.c.closing,'INTERLINKED is closing.')
            need(not (self.state/'actions'/(request_id+'.json')).exists(),'Request ID already belongs to a lifecycle action.')
            request=dict(body,format='interlinked-buddy-request-v1',manifest_sha256=sha,staging_root=str(self.staging),session_id=self.c.job['id'] if self.c.job and self.c.job.get('action')=='open' else None)
            path=self.state/'requests'/(request_id+'.json')
            if path.exists():
                prior=read(path);need(all(prior.get(k)==request[k] for k in ('project_id','manifest_path','manifest_sha256')),'Request ID reused with different assets.');return self.operation(request_id)
            need(self.pending is None,'Another delivery is running.')
            active=self.c.job and self.c.job.get('status')=='WAITING_FOR_MAGIX'
            folder=getattr(self.c,'active',None)
            if active:
                need(self.c.job['projectId']==row['id'] and self.c.child and self.c.child.poll() is None,'The selected project does not own the active session.')
                need(getattr(self.c,'buddy_session_capable',False),'This session predates Buddy delivery support. Finish it and open a new managed session.')
            elif folder:
                need(folder.get('kind')=='folder' and folder.get('projectId')==row['id'],'Finish the other active application session before delivery.')
            else:need(not self.c.busy(),'Another project operation is running.')
            write(path,request);write(self.state/'operations'/(request_id+'.json'),dict(format='interlinked-buddy-ingest-operation-v1',request_id=request_id,project_id=project,manifest_sha256=sha,status='QUEUED',updated_at=utc()))
            self.pending=request_id
            if active:
                self.c.child.stdin.write('BUDDY_INGEST '+request_id+'\n');self.c.child.stdin.flush()
            else:
                threading.Thread(target=self._idle_ingest,args=(request,row,bool(folder)),daemon=False).start()
            return self.operation(request_id)
    def _idle_ingest(self,request,row,folder):
        try:
            files=self.c.r.cache/request['project_id']/'Files'
            engine=self.c.engine
            def perform():
                if row.get('adapter')=='magix':
                    state=self.c.cache_state(request['project_id']);need(state and state.get('status')=='READY' and not state.get('pendingSave'),'Selected cache is not ready.')
                    choices=[files/Path(PREFIX+root) for root in MEDIA_ROOTS if (files/Path(PREFIX+root)).exists()];need(choices,'No existing media root is available.');destination=choices[0]
                else:destination=self.c.hub.folder_path(row['id'])
                return ingest_files(self.state,request,destination,files)
            if folder:perform() # original folder thread keeps its native locks
            else:
                self.c._workflow_acquire()
                try:
                    with engine.exclusive(self.g.ordinary(self.c.r.cache/request['project_id']/'.client.lock'),create=False):perform()
                finally:self.c._workflow_release()
        except Exception as exc:
            path=self.state/'operations'/(request['request_id']+'.json');record=read(path);write(path,dict(record,status='FAILED',error=str(exc),updated_at=utc()))
        finally:
            with self.lock:self.pending=None
    def report(self,body):
        need(set(body)=={'project_id','summary'},'Unexpected report fields.');self.project(body['project_id'])
        need(isinstance(body['summary'],dict) and len(json.dumps(body['summary']).encode('utf-8'))<=64*1024,'Project summary exceeds its bound.')
        value=dict(format='interlinked-buddy-project-report-v1',project_id=body['project_id'],summary=body['summary'],observed_at=utc())
        write(self.state/'reports'/(body['project_id']+'.json'),value);return value
    def gui_report(self,ui_project):
        """Reduced report for existing authenticated GUI; no capability token."""
        rows=self.c.catalog()['projects'];matches=[r for r in rows if r['id']==ui_project]
        need(len(matches)==1,'Choose one known project.')
        project=matches[0].get('originalId',ui_project);path=self.state/'reports'/(project+'.json')
        empty=dict(format='interlinked-buddy-gui-report-v1',project_id=project,available=False,observed_at=None,counts={},tasks=[],truncated=False)
        if not path.exists():return empty
        record=read(path,128*1024)
        need(record.get('format')=='interlinked-buddy-project-report-v1' and record.get('project_id')==project and isinstance(record.get('summary'),dict),'Buddy report identity differs.')
        summary=record['summary'];tasks=summary.get('tasks',[])
        need(isinstance(tasks,list) and len(tasks)<=1000,'Buddy task report exceeds its bound.')
        counts=dict(total=0,prepared=0,delivered=0,attention=0,running=0,queued=0,stored=0,searchable=0)
        reduced=[]
        def text(value,limit):return value[:limit] if isinstance(value,str) else ''
        for task in tasks:
            if not isinstance(task,dict):continue
            status=text(task.get('status'),40);delivery=text(task.get('delivery_status'),40)
            counts['total']+=1
            counts['prepared']+=status=='completed' and task.get('kind')!='local_import'
            storage=text(task.get('storage_status'),40);indexing=text(task.get('indexing_status'),40)
            counts['stored']+=storage=='stored';counts['searchable']+=indexing=='searchable'
            counts['delivered']+=delivery=='COMPLETED'
            attention=indexing in ('failed','needs_review') or status in ('failed','uncertain','needs_review') or delivery in ('FAILED','UNCERTAIN','needs_review')
            counts['attention']+=attention;counts['running']+=status=='running';counts['queued']+=status=='queued'
            outputs=task.get('outputs',[]);outputs=outputs if isinstance(outputs,list) else []
            references=[]
            for item in outputs[:10]:
                if not isinstance(item,dict):continue
                label=text(item.get('project_relative_path') or item.get('path'),2048)
                if label:references.append(dict(path=label,sha256=text(item.get('sha256'),64)))
            reduced.append(dict(task_id=text(task.get('task_id'),80),kind=text(task.get('kind'),80),status=status,storage_status=storage,indexing_status=indexing,delivery_status=delivery,message=text(task.get('message'),1000),attention=attention,outputs=references,outputs_truncated=len(outputs)>10,reviewed_by_user_at=text(task.get('reviewed_by_user_at'),80)))
        # Attention comes first without concealing finished tasks. Counters are
        # calculated before reducing the displayed task list.
        reduced.sort(key=lambda task:not task['attention'])
        return dict(empty,available=True,observed_at=text(record.get('observed_at'),80),source_observed_at=text(summary.get('observed_at'),80),counts=counts,tasks=reduced[:100],truncated=len(reduced)>100)

def install(g):
    """Install explicit hooks without changing the frozen workspace engine."""
    if getattr(g,'_buddy_installed',False):return
    g._buddy_installed=True
    original_init=g.Controller.__init__;original_busy=g.Controller.busy;original_run=g.Controller._run;original_read=g.Controller._read;original_session=g.Controller.session_input;original_start=g.Controller.start;original_finish=g.Controller.finish
    def init(self,*args,**kwargs):original_init(self,*args,**kwargs);self.buddy=Companion(g,self);self.buddy_session_capable=False
    def busy(self):return original_busy(self) or bool(getattr(self,'buddy',None) and self.buddy.pending)
    def run(self,args):
        if self.job['action']=='open':
            args=list(args)+['--buddy-session',self.job['id']];self.buddy_session_capable=True
        return original_run(self,args)
    def read_stream(self,stream,label,log,job_id=None):
        # Wrap read without changing the launcher's prompt parser or log path.
        service=self.buddy
        class Observed:
            tail=''
            def read(_,count):
                value=stream.read(count)
                if label=='stdout':
                    _.tail=(_.tail+value)[-4096:]
                    if value=='\n':
                        line=_.tail.strip()
                        if line.startswith('BUDDY_RECEIPT '):
                            try:service.operation(json.loads(line[len('BUDDY_RECEIPT '):])['request_id'])
                            except (ValueError,OSError,KeyError):pass
                        _.tail=''
                return value
        return original_read(self,Observed(),label,log,job_id)
    def session(self,*args,**kwargs):need(not self.buddy.pending,'Wait for the current asset delivery to finish.');return original_session(self,*args,**kwargs)
    def start(self,*args,**kwargs):need(not self.buddy.pending,'Wait for the current asset delivery to finish.');return original_start(self,*args,**kwargs)
    def finish(self,*args,**kwargs):
        if self.buddy.pending:
            request_id=self.buddy.pending;result=self.buddy.operation(request_id)
            if result.get('status') not in ('COMPLETED','FAILED'):
                write(self.buddy.state/'operations'/(request_id+'.json'),dict(result,status='UNCERTAIN',error='Managed launcher exited before delivery acknowledgement. Inspect the retained transaction before retry.',updated_at=utc()))
            self.buddy.pending=None
        self.buddy_session_capable=False
        return original_finish(self,*args,**kwargs)
    g.Controller.__init__=init;g.Controller.busy=busy;g.Controller._run=run;g.Controller._read=read_stream;g.Controller.session_input=session;g.Controller.start=start;g.Controller.finish=finish
    old_init=g.Server.__init__;old_close=g.Server.server_close;old_get=g.Handler.do_GET;old_post=g.Handler.do_POST
    def server_init(self,*args,**kwargs):old_init(self,*args,**kwargs);self.controller.buddy.publish(self.origin)
    def server_close(self):
        try:self.controller.buddy.close()
        finally:old_close(self)
    def gate(handler):
        need(handler.headers.get('Host')==handler.server.host,'Unexpected Host.')
        need(handler.headers.get('Origin') is None,'Companion requests are native-client only.')
        need(handler.headers.get('Sec-Fetch-Site') is None,'Browser companion requests are refused.')
        need(hmac.compare_digest(handler.headers.get('X-INTERLINKED-Buddy-Token','').encode(),handler.server.controller.buddy.token.encode()),'Buddy capability required.')
    def get(handler):
        if urlsplit(handler.path).path=='/api/buddy-report':
            try:
                handler.gate(api=True)
                query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Choose one report project.')
                handler.respond(200,handler.server.controller.buddy.gui_report(query['project'][0]))
            except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:handler.respond(403,dict(error=str(exc)))
            return
        if not handler.path.startswith('/api/buddy/'):return old_get(handler)
        try:
            gate(handler);parsed=urlsplit(handler.path);service=handler.server.controller.buddy
            if parsed.path=='/api/buddy/projects' and not parsed.query:result=service.projects()
            elif parsed.path=='/api/buddy/project':
                query=parse_qs(parsed.query,strict_parsing=True);need(set(query)=={'project_id'} and len(query['project_id'])==1,'Choose one project.');result=service.project_detail(query['project_id'][0])
            elif parsed.path=='/api/buddy/operation':
                query=parse_qs(parsed.query,strict_parsing=True);need(set(query)=={'request_id'} and len(query['request_id'])==1,'Select one request.');result=service.operation(query['request_id'][0])
            elif parsed.path=='/api/buddy/reports' and not parsed.query:result=dict(reports=[read(p) for p in service.state.joinpath('reports').glob('*.json')])
            else:raise ValueError('Unknown companion route.')
            handler.respond(200,result)
        except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:handler.respond(403,dict(error=str(exc)))
    def post(handler):
        if not handler.path.startswith('/api/buddy/'):return old_post(handler)
        try:
            gate(handler);need(handler.headers.get('Content-Type','').split(';')[0]=='application/json' and handler.headers.get('Transfer-Encoding') is None,'Bounded JSON body required.')
            length=int(handler.headers.get('Content-Length','-1'));need(0<=length<=MAX_MANIFEST,'Body exceeds its bound.')
            body=json.loads(handler.rfile.read(length));need(isinstance(body,dict),'Object body required.')
            service=handler.server.controller.buddy
            if handler.path=='/api/buddy/ingest':result=service.ingest(body)
            elif handler.path=='/api/buddy/action':result=service.action(body)
            elif handler.path=='/api/buddy/session':result=service.session_response(body)
            elif handler.path=='/api/buddy/report':result=service.report(body)
            else:raise ValueError('Unknown companion route.')
            handler.respond(200,result)
        except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:handler.respond(409,dict(error=str(exc)))
    g.Server.__init__=server_init;g.Server.server_close=server_close;g.Handler.do_GET=get;g.Handler.do_POST=post
