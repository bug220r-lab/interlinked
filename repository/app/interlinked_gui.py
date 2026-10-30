"""INTERLINKED: loopback-only GUI for the unchanged reviewed MAGIX launcher.

Python 3.12 standard library. No automatic save, release, application termination,
credential storage, or public network listener. --demo never invokes the launcher.
"""
from __future__ import annotations
import argparse
from collections import deque
import ctypes
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import datetime as dt
import hashlib
import hmac
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

MAGIX_SHA='6c2ea58d9ac89a20c4b2bb42347b79f5802b049b2c81812314e09412578de1bf'
BASE_SHA='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa'
INDEX_SHA='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'
CLIENT_SEAL_SHA='362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc'
KIT=Path(r'C:\Users\Jude\Documents\MSI-Project-Library')
CACHE=Path(r'C:\Users\Jude\AppData\Local\ProjectLibrary\Cache')
STATIC={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
ACTIONS={'open','save','verify','release','recover'}
MAX_BODY=16*1024
PROMPT='Type DONE after MAGIX closes (or CANCEL STARTUP): '
ROOTS=('OneDrive/Documents','OneDrive/Pictures','OneDrive/Desktop','Downloads','OneDrive - The University of Akron/The Rialto Theatre','AppData/Roaming/MAGIX/Videodeluxe27_plus_steam')
PREFIX='Drive_C/Users/jlewi/'
ENTRY_CAP=25000

def need(value,message):
    if not value:raise ValueError(message)
def utc():return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fs(path):
    value=str(path)
    if os.name!='nt' or value.startswith('\\\\?\\'):return value
    return '\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
def exists(path):
    try:os.lstat(fs(path));return True
    except FileNotFoundError:return False

def path_attributes(path,info):
    """Disambiguate lstat's EA bit from directory-only RECALL_ON_OPEN."""
    attrs=getattr(info,'st_file_attributes',0)
    if os.name=='nt' and attrs&0x40000:
        # Windows aliases FILE_ATTRIBUTE_EA and RECALL_ON_OPEN. Python's
        # lstat can report EA; GetFileAttributesW supplies path attributes
        # without interpreting an extended-attribute record as a placeholder.
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetFileAttributesW.argtypes=[ctypes.c_wchar_p]
        kernel.GetFileAttributesW.restype=ctypes.c_uint32
        current=kernel.GetFileAttributesW(fs(path))
        if current==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
        return (attrs&~0x40000)|current
    return attrs

def ordinary(path,allow_missing=False):
    """Metadata only: no links, junctions, recall or offline paths."""
    path=Path(path)
    need(path.is_absolute(),'Absolute local path required.')
    for p in reversed((path,*path.parents)):
        try:s=os.lstat(fs(p))
        except FileNotFoundError:
            if allow_missing:continue
            raise
        need(not stat.S_ISLNK(s.st_mode) and not path_attributes(p,s)&(0x400|0x1000|0x40000|0x400000),'Link/cloud placeholder not accepted: '+str(p))
    return path

def read_json(path,limit=32*1024*1024):
    ordinary(path)
    with open(fs(path),'rb') as stream:data=stream.read(limit+1)
    need(len(data)<=limit,'Metadata file is too large.')
    value=json.loads(data.decode('utf-8-sig'));need(isinstance(value,dict),'Metadata object required.')
    return value

def write_json(path,value):
    ordinary(path.parent);ordinary(path,allow_missing=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(fs(temporary),'x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(fs(temporary),fs(path))

def identifier(value):
    need(isinstance(value,str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,159}',value),'Invalid project identifier.')
    return value

def load_companion(name):
    need(name in ('interlinked_controller','interlinked_hub','interlinked_mw5','interlinked_master','interlinked_jobs'),'Unknown companion module.')
    if name in sys.modules:return sys.modules[name]
    path=Path(__file__).resolve().parent/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,ordinary(path))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    try:spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name,None);raise
    return module

def client_engine(runtime):
    """Import only the original sealed engine; never install into its runtime."""
    path=ordinary(runtime.kit/'Project-Library-Client/project_library.py')
    name='interlinked_reviewed_client'
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        spec.loader.exec_module(module)
    return sys.modules[name]

class MissingMediaLedger:
    """User reports only. Reported paths are never opened or resolved.

    Import API: MissingMediaLedger(state, expected_owner, project_ids).report(
      project, path, request_id=<UUID>, source='user_dialog', note='...').
    This lock is independent of the GUI instance and all project/job locks.
    """
    LIMIT=8*1024*1024
    STATUSES={'reported_missing','user_marked_unavailable','resolved'}
    def __init__(self,state,owner,project_ids):
        self.state=Path(state);self.owner=owner
        self.projects={identifier(p) for p in project_ids};self.lock=threading.RLock()
        self.path=self.state/'missing-media.events.jsonl'
        self.marker=self.state/'missing-media-owner.json'
        self.identity=dict(format='interlinked-missing-media-v1',guiOwner=owner)
        ordinary(self.state)
        need(read_json(self.state/'OWNER.json')==owner,'Missing-media state ownership differs.')

    @staticmethod
    def text(value,limit,multiline=False):
        need(isinstance(value,str) and len(value)<=limit,'Report text is missing or too long.')
        value=value.replace('\r\n','\n').replace('\r','\n') if multiline else value.strip()
        need(all((ord(c)>=32 and ord(c)!=127 and not 0xd800<=ord(c)<=0xdfff) or (multiline and c in '\n\t') for c in value),'Report text contains unsupported control characters.')
        return value

    @classmethod
    def key(cls,project,path):
        path=cls.text(path,2048);need(path,'Enter a filename or the path shown by MAGIX.')
        normalized=path.replace('/','\\').casefold()
        return path,normalized,hashlib.sha256((project+'\0'+normalized).encode('utf-8')).hexdigest()

    @staticmethod
    def request(value):
        need(isinstance(value,str) and re.fullmatch(r'[a-fA-F0-9-]{32,36}',value),'A request UUID is required.')
        return uuid.UUID(value).hex

    @contextmanager
    def file(self,path,create=False):
        """Open a regular local leaf without following a replaced reparse point."""
        ordinary(path,allow_missing=create)
        if os.name=='nt':
            import msvcrt
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
            handle=k.CreateFileW(fs(path),0xc0000000,0,None,1 if create else 3,0x00300000,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            try:fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
            except BaseException:
                k.CloseHandle.argtypes=[ctypes.c_void_p];k.CloseHandle(handle);raise
            stream=os.fdopen(fd,'r+b')
        else:stream=open(path,'x+b' if create else 'r+b')
        try:
            s=os.fstat(stream.fileno());now=os.lstat(fs(path))
            need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and now.st_nlink==1 and (s.st_dev,s.st_ino)==(now.st_dev,now.st_ino) and not (path_attributes(path,s)|path_attributes(path,now))&(0x400|0x1000|0x40000|0x400000),'Ledger leaf changed, is hardlinked or is not an ordinary local file.')
            yield stream
        finally:stream.close()

    @contextmanager
    def guarded(self):
        with self.lock:
            ordinary(self.state);need(read_json(self.state/'OWNER.json')==self.owner,'Missing-media state ownership differs.')
            lock=self.state/'.missing-media.lock'
            # The permanent empty lock is distinct from the journal/ownership marker.
            try:
                with self.file(lock,create=True):pass
            except FileExistsError:pass
            except OSError as exc:
                if not exists(lock):raise
            with self.file(lock):
                if not exists(self.path):
                    need(not exists(self.marker),'Missing-media journal is missing. Preserve its ownership marker and recover the journal before adding reports.')
                    header=dict(**self.identity,ledgerId=uuid.uuid4().hex)
                    raw=(json.dumps(header,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
                    with self.file(self.path,create=True) as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                with self.file(self.path) as stream:
                    raw=stream.read(self.LIMIT+1)
                    need(0<len(raw)<=self.LIMIT and raw.endswith(b'\n'),'Missing-media journal is empty, oversized or has an incomplete append. It was preserved; no reports were added.')
                    lines=raw.splitlines();need(len(lines)<=10001,'Missing-media event limit reached.')
                    header=json.loads(lines[0]);need(isinstance(header,dict) and set(header)=={'format','guiOwner','ledgerId'} and all(header.get(k)==v for k,v in self.identity.items()) and isinstance(header.get('ledgerId'),str) and re.fullmatch(r'[a-f0-9]{32}',header['ledgerId']),'Missing-media journal identity differs.')
                    marker=dict(format='interlinked-missing-media-owner-v1',ledgerId=header['ledgerId'],headerSha256=hashlib.sha256(lines[0]+b'\n').hexdigest(),guiOwner=self.owner)
                    if exists(self.marker):need(read_json(self.marker)==marker,'Missing-media ownership marker differs.')
                    else:
                        need(len(lines)==1,'Missing-media ownership marker is absent from an established journal. No reset was performed.')
                        with self.file(self.marker,create=True) as dest:dest.write((json.dumps(marker,ensure_ascii=False)+'\n').encode('utf-8'));dest.flush();os.fsync(dest.fileno())
                    records={};requests={};previous=marker['headerSha256']
                    for seq,line in enumerate(lines[1:],1):
                        event=json.loads(line);self.validate(event,seq,previous);previous=event['sha256']
                        need(event['requestId'] not in requests,'Duplicate request ID in journal.')
                        requests[event['requestId']]=event
                        prior=records.get(event['recordId'])
                        if event['operation']=='status':need(prior is not None and prior['path']==event['path'],'Status event has no matching prior report.')
                        records[event['recordId']]=dict(event,firstReportedUtc=prior['firstReportedUtc'] if prior else event['utc'],firstSource=prior['firstSource'] if prior else event['source'],firstNote=prior['firstNote'] if prior else event['note'],reportCount=(prior['reportCount'] if prior else 0)+(event['operation']=='report'))
                    yield stream,records,requests,len(lines),previous

    @staticmethod
    def event_hash(event):return hashlib.sha256(json.dumps({k:v for k,v in event.items() if k!='sha256'},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

    def validate(self,event,seq,previous):
        fields={'format','sequence','requestId','utc','operation','projectId','recordId','path','normalizedPath','status','source','note','auditReferenceId','previousSha256','sha256'}
        need(isinstance(event,dict) and set(event)==fields and event['format']=='interlinked-missing-media-event-v1' and event['sequence']==seq,'Invalid missing-media event schema or sequence.')
        need(event['previousSha256']==previous and event['sha256']==self.event_hash(event),'Missing-media event hash chain differs. Journal preserved; no append permitted.')
        need(isinstance(event['projectId'],str) and event['projectId'] in self.projects,'Unknown project in missing-media event.')
        path,normalized,key=self.key(event['projectId'],event['path'])
        need((path,normalized,key)==(event['path'],event['normalizedPath'],event['recordId']),'Missing-media identity is inconsistent.')
        need(self.request(event['requestId'])==event['requestId'],'Invalid missing-media request identity.')
        need(isinstance(event['utc'],str) and len(event['utc'])<=40 and dt.datetime.fromisoformat(event['utc']).utcoffset() is not None,'Invalid event time.')
        need(isinstance(event['status'],str) and event['status'] in self.STATUSES,'Invalid report status.')
        need((event['operation']=='report' and event['source'] in ('user_dialog','manual_flag','audit_flag') and event['status']=='reported_missing') or (event['operation']=='status' and event['source']=='user_status_change'),'Invalid report operation/source.')
        need(self.text(event['note'],2000,True)==event['note'],'Invalid report note.')
        ref=event['auditReferenceId'];need(ref is None or isinstance(ref,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ref),'Invalid audit reference ID.')

    def project(self,project):
        need(isinstance(project,str) and project in self.projects,'Unknown report project.');return project

    def snapshot(self,project):
        self.project(project)
        with self.guarded() as (_,records,requests,__,___):
            return dict(format='interlinked-missing-media-report-v1',projectId=project,records=[r for r in records.values() if r['projectId']==project],events=[r for r in requests.values() if r['projectId']==project],notice='User reports only. Resolved and unavailable statuses are user-marked, not verified repairs. No reported path was opened.')

    def report(self,project,path,*,request_id,source='manual_flag',note='',audit_reference_id=None):
        self.project(project);path,normalized,key=self.key(project,path)
        payload=dict(operation='report',projectId=project,recordId=key,path=path,normalizedPath=normalized,status='reported_missing',source=source,note=self.text(note,2000,True),auditReferenceId=audit_reference_id)
        return self.append(payload,request_id)

    def change_status(self,project,record_id,status,*,request_id,note=''):
        self.project(project);need(isinstance(record_id,str) and re.fullmatch(r'[a-f0-9]{64}',record_id),'Invalid report ID.')
        with self.guarded() as context:
            prior=context[1].get(record_id);need(prior is not None and prior['projectId']==project,'Report does not belong to this project.')
            payload={k:prior[k] for k in ('projectId','recordId','path','normalizedPath','auditReferenceId')}
            payload.update(operation='status',status=status,source='user_status_change',note=self.text(note,2000,True))
            return self.append(payload,request_id,context)

    def append(self,payload,request_id,context=None):
        if context is None:
            with self.guarded() as held:return self.append(payload,request_id,held)
        stream,records,requests,sequence,previous=context;request_id=self.request(request_id)
        if request_id in requests:
            prior=requests[request_id];need(all(prior.get(k)==v for k,v in payload.items()),'Request ID was already used for a different report.')
            return dict(status='RECORDED',duplicateRequest=True,record=records[prior['recordId']])
        event=dict(format='interlinked-missing-media-event-v1',sequence=sequence,requestId=request_id,utc=utc(),previousSha256=previous,**payload);event['sha256']=self.event_hash(event)
        self.validate(event,sequence,previous)
        data=(json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        stream.seek(0,2);need(stream.tell()+len(data)<=self.LIMIT and sequence<=10000,'Missing-media journal capacity reached; export it before requesting explicit archival maintenance.')
        offset=stream.tell();need(stream.write(data)==len(data),'Incomplete missing-media append; preserve journal for recovery.')
        stream.flush();os.fsync(stream.fileno());stream.seek(offset)
        need(stream.read(len(data))==data,'Missing-media append readback differs; journal preserved.')
        return dict(status='RECORDED',duplicateRequest=False,record=event)

    def export(self,project,kind):
        need(kind in ('json','csv'),'Choose JSON or CSV.');value=self.snapshot(project)
        if kind=='json':text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        else:
            output=io.StringIO(newline='');writer=csv.writer(output,quoting=csv.QUOTE_ALL)
            columns=('projectId','path','status','source','note','auditReferenceId','firstReportedUtc','utc','reportCount')
            writer.writerow(columns)
            # A visible prefix survives ordinary spreadsheet save/reopen cycles.
            # JSON retains original strings; CSV is a display-oriented export.
            for row in value['records']:writer.writerow(['Text: '+str(row.get(k) or '') for k in columns])
            text=output.getvalue()
        return dict(filename='INTERLINKED-missing-media-'+project+'.'+kind,mime='application/json' if kind=='json' else 'text/csv',text=text)

@dataclass
class Runtime:
    kit:Path
    cache:Path
    state:Path
    web:Path
    index:dict
    audit:dict|None=None
    demo:bool=False
    python:Path|None=None

def demo_runtime(state):
    projects=[]
    for i,(name,size,status) in enumerate((('Neon signals',2.9,'Review needed'),('Night drive',12.4,'References covered'),('Recovery reel',0.4,'Recovery files'),('After the rain',4.8,'Missing media')),1):
        project='demo-project-'+str(i)
        projects.append(dict(id=project,name=name,bytes=int(size*1024**3),files=120+i*43,entryPoints=[dict(path='Drive_C/Users/example/Documents/'+name+'.MVP',kind='project'),dict(path='Drive_C/Users/example/Documents/'+name+'_BAK0.MV_',kind='recovery')],warnings=['Synthetic preview. No personal files, media or applications are accessed.'],notes=[],demoHealth=status))
    return Runtime(Path(__file__).parent,Path(__file__).parent/'NO_REAL_CACHE',state,Path(__file__).parent/'web',dict(projects=projects,catalogPublishedUtc='Synthetic design preview'),demo=True)

def production_runtime():
    need(os.name=='nt' and os.environ.get('COMPUTERNAME','').casefold()=='this-machine','Run this kit locally on THIS-MACHINE, or use --demo for a synthetic preview.')
    need(not ctypes.windll.shell32.IsUserAnAdmin(),'Run as your normal account, without administrator mode.')
    need(Path(os.environ.get('USERPROFILE',''))==Path(r'C:\Users\Jude'),'Unexpected Windows profile.')
    app=Path(__file__).resolve().parent
    need(app==KIT/'INTERLINKED-App','Use the local deployed INTERLINKED kit.')
    ordinary(app);ordinary(KIT)
    seal=read_json(app/'GUI-CHECKSUMS.json',1024*1024)
    expected={'interlinked_gui.py','interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','web/index.html','web/app.js','web/style.css'}
    need(seal.get('format')=='interlinked-gui-checksums-v4' and set(seal.get('files',{}))==expected,'GUI seal is missing or incomplete.')
    for rel,sha in seal['files'].items():
        path=ordinary(app/rel);need(digest(path)==sha,'GUI file differs: '+rel)
    for name,sha in (('magix_projects.py',MAGIX_SHA),('magix_workspace.py',BASE_SHA),('MAGIX-All-Projects-Index.json',INDEX_SHA)):
        need(digest(ordinary(KIT/name))==sha,'Reviewed dependency differs: '+name)
    index=read_json(KIT/'MAGIX-All-Projects-Index.json',2*1024*1024)
    need(len(index.get('projects',[]))==43,'Unexpected reviewed project index.')
    audit=None
    if seal.get('auditSha256'):
        path=ordinary(KIT/'MAGIX-Media-Audit.json')
        need(digest(path)==seal['auditSha256'],'Media audit differs from its reviewed snapshot.')
        audit=read_json(path)
        need(audit.get('format')=='magix-media-audit-gui-v1','Unknown reviewed media audit format.')
    python=ordinary(KIT/'Project-Library-Client/Runtime/python.exe')
    client=ordinary(KIT/'Project-Library-Client')
    seal_path=ordinary(client/'HELPER-CHECKSUMS.json')
    need(digest(seal_path)==CLIENT_SEAL_SHA,'Original reviewed client seal differs.')
    client_seal=read_json(seal_path)
    need(client_seal.get('format')=='project-library-client-checksums-v1' and len(client_seal.get('files',[]))==52,'Original client seal is incomplete.')
    seen=set()
    for row in client_seal['files']:
        rel=row['path'];need(isinstance(rel,str) and rel not in seen and not rel.startswith(('/','\\')) and '\\' not in rel and ':' not in rel and all(p not in ('','.','..') for p in rel.split('/')),'Unsafe original client seal path.')
        seen.add(rel);path=ordinary(client/rel)
        need(path.stat().st_size==row['bytes'] and digest(path)==row['sha256'],'Reviewed client file differs: '+rel)
    return Runtime(KIT,CACHE,KIT/'INTERLINKED-GUI-State',app/'web',index,audit,False,python)

class Controller:
    def __init__(self,runtime,process_factory=subprocess.Popen):
        self.r=runtime;self.factory=process_factory;self.lock=threading.RLock()
        self.projects={identifier(p['id']):p for p in runtime.index['projects']}
        need(len(self.projects)==len(runtime.index['projects']),'Duplicate project IDs.')
        self.job=None;self.child=None;self.output=deque(maxlen=16000);self.demo_done=threading.Event()
        self.closing=False;self.revision=0;self.instance=None
        ordinary(runtime.state,allow_missing=True)
        runtime.state.mkdir(parents=True,exist_ok=True)
        self.owner=runtime.state/'OWNER.json'
        identity=dict(format='interlinked-gui-state-v1',kit=str(runtime.kit),demo=runtime.demo)
        if self.owner.exists():need(read_json(self.owner)==identity,'GUI state directory belongs to another kit.')
        else:
            need(not list(runtime.state.iterdir()),'Existing GUI state is unowned.')
            write_json(self.owner,identity)
        # One GUI instance per owned kit; underlying launcher locks remain
        # authoritative against the separate console workflow.
        if os.name=='nt':
            path=ordinary(runtime.state/'.gui-instance.lock',allow_missing=True)
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
            handle=k.CreateFileW(str(path),0x80000000|0x40000000,0,None,4,0x00300000,None)
            need(handle!=ctypes.c_void_p(-1).value,'Another INTERLINKED GUI is already running, or its state lock is unavailable. Use its existing local browser link.')
            self.instance=(k,handle)
        self.run=runtime.state/('run-'+uuid.uuid4().hex);self.run.mkdir()
        self.previous=[]
        for path in sorted(runtime.state.glob('run-*/operation.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:5]:
            try:
                value=read_json(path,1024*1024)
                if value.get('status') in ('STARTING','RUNNING','WAITING_FOR_MAGIX'):
                    self.previous.append(dict(id=value.get('id'),action=value.get('action'),projectId=value.get('projectId'),receipt=str(path)))
            except (ValueError,OSError):pass
        self.cache_summary={};self.cache_at=0
        self.missing=MissingMediaLedger(runtime.state,identity,self.projects)

    def report_missing(self,body):
        with self.lock:
            need(not self.closing,'Interface is closing; no new reports accepted.')
            return self._report_missing(body)

    def missing_view(self,project,kind=None):
        with self.lock:
            need(not self.closing,'Interface is closing; report access has stopped.')
            return self.missing.export(project,kind) if kind is not None else self.missing.snapshot(project)

    def _report_missing(self,body):
        need(set(body).issubset({'project','path','source','note','requestId','auditReferenceId'}),'Unexpected report fields.')
        project=body.get('project');self.missing.project(project)
        source=body.get('source','manual_flag');need(source in ('manual_flag','user_dialog','audit_flag'),'Unknown report evidence source.')
        ref=body.get('auditReferenceId');path=body.get('path')
        if source=='audit_flag':
            need(isinstance(ref,str),'Select one reviewed audit reference.')
            project_audit=next((p for p in (self.r.audit or {}).get('projects',[]) if p.get('id')==project),{})
            reference=next((r for r in project_audit.get('references',[]) if r.get('id')==ref),None)
            need(reference is not None,'Reference is not part of this project audit.')
            path=reference.get('path') or reference.get('value') or reference.get('name')
            need('path' not in body,'An audit flag derives its text from the reviewed reference.')
        else:need(ref is None,'Manual reports do not assert an audit-reference identity.')
        return self.missing.report(project,path,request_id=body.get('requestId'),source=source,note=body.get('note',''),audit_reference_id=ref)

    def status_missing(self,body):
        with self.lock:
            need(not self.closing,'Interface is closing; no report updates accepted.')
            need(set(body).issubset({'project','recordId','status','note','requestId'}),'Unexpected report status fields.')
            return self.missing.change_status(body.get('project'),body.get('recordId'),body.get('status'),request_id=body.get('requestId'),note=body.get('note',''))

    def busy(self):return self.job is not None and self.job['status'] in ('STARTING','RUNNING','WAITING_FOR_MAGIX')
    def record(self):
        write_json(self.run/'operation.json',self.job)
        write_json(self.run/('operation-'+self.job['id']+'.json'),self.job)
    def report(self):
        try:self.record()
        except (OSError,ValueError) as exc:
            self.job['reportingWarning']='Receipt write failed; active launcher control is retained: '+str(exc)
    def request_quit(self):
        with self.lock:
            need(not self.busy(),'An operation or MAGIX session is still active. Close MAGIX and confirm Done first.')
            self.closing=True
    def close(self):
        self.request_quit()
        if self.instance:
            k,handle=self.instance;k.CloseHandle.argtypes=[ctypes.c_void_p];k.CloseHandle(handle);self.instance=None
    def state(self):
        with self.lock:
            return dict(name='INTERLINKED',demo=self.r.demo,busy=self.busy(),closing=self.closing,revision=self.revision,operation=dict(self.job) if self.job else None,output=''.join(self.output),previousIncomplete=self.previous,logDirectory=str(self.run),canQuit=not self.busy())

    def catalog(self):
        with self.lock:
            if time.monotonic()-self.cache_at>4:
                self.cache_summary={}
                for pid in self.projects:
                    status='ABSENT';warning=None
                    if self.r.demo:status='DEMO'
                    else:
                        try:
                            state=self.cache_state(pid)
                            if state is not None:
                                status=state.get('status','UNKNOWN')
                                if state.get('pendingSave'):status='PENDING_SAVE'
                        except (ValueError,OSError,json.JSONDecodeError) as exc:status='CHECK_REQUIRED';warning=str(exc)
                    self.cache_summary[pid]=dict(status=status,warning=warning)
                self.cache_at=time.monotonic()
            audit_projects={p['id']:p for p in (self.r.audit or {}).get('projects',[])}
            rows=[]
            for pid,p in self.projects.items():
                audit=audit_projects.get(pid,{})
                rows.append(dict(id=pid,name=p['name'],bytes=p['bytes'],files=p['files'],entryPoints=p['entryPoints'],warnings=p.get('warnings',[]),notes=p.get('notes',[]),cache=self.cache_summary[pid],mediaStatus=audit.get('health',audit.get('status',p.get('demoHealth','NOT_AUDITED'))),mediaSummary=audit.get('summary'),mediaCounts=audit.get('counts',{})))
            return dict(projects=rows,indexDate=self.r.index.get('catalogPublishedUtc'),auditDate=(self.r.audit or {}).get('generatedUtc'),auditAvailable=self.r.audit is not None,demo=self.r.demo)

    def cache_state(self,project):
        need(isinstance(project,str) and project in self.projects,'Unknown project.')
        root=self.r.cache/project;state_path=root/'state.json'
        if not exists(state_path):return None
        identity=dict(projectId=project,catalogId='msi-project-library-v1',library=str(Path(r'\\MSI\ProjectLibrary')),returns=str(Path(r'\\MSI\ProjectReturns')))
        need(read_json(root/'OWNER.json',1024*1024)==dict(format='project-library-owned-v1',**identity),'Cache ownership differs.')
        state=read_json(state_path)
        need(state.get('format')=='project-library-cache-v1' and all(state.get(k)==v for k,v in identity.items()),'Cache state identity differs.')
        return state

    def entries(self,project):
        """Bounded metadata enumeration within six physical selected-cache roots."""
        need(isinstance(project,str) and project in self.projects,'Unknown project.')
        indexed=self.projects[project]['entryPoints'];rows={r['path'].casefold():dict(r,origin='INDEXED') for r in indexed}
        if self.r.demo:return dict(entries=list(rows.values()),warnings=['Synthetic indexed entries only.'],scanned=0)
        state=self.cache_state(project)
        if state is None or state.get('status') not in ('READY','PENDING_SAVE'):
            return dict(entries=list(rows.values()),warnings=[],scanned=0)
        files=self.r.cache/project/'Files';ordinary(files)
        stack=[];warnings=[];scanned=0
        for suffix in ROOTS:
            root=files/(PREFIX+suffix)
            if exists(root):ordinary(root);stack.append(root)
        while stack:
            directory=stack.pop();ordinary(directory)
            need(len(directory.relative_to(files).parts)<=128,'Local entry scan exceeded the supported directory depth.')
            with os.scandir(fs(directory)) as children:
                for item in children:
                    scanned+=1
                    need(scanned<=ENTRY_CAP,'Local entry scan exceeded25,000 metadata items. Use the console launcher for this larger working tree.')
                    info=item.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&(0x400|0x1000|0x40000|0x400000):
                        warnings.append('Skipped a link or cloud placeholder: '+str(directory/item.name));continue
                    path=directory/item.name
                    if stat.S_ISDIR(info.st_mode):stack.append(path)
                    elif stat.S_ISREG(info.st_mode) and path.suffix.casefold() in ('.mvp','.mv_','.mvd'):
                        relative=path.relative_to(files).as_posix()
                        if relative.casefold() not in rows:rows[relative.casefold()]=dict(path=relative,kind='recovery' if path.suffix.casefold()=='.mv_' else 'project',origin='LOCAL_SAVE_AS')
        return dict(entries=list(rows.values()),warnings=warnings,scanned=scanned)

    def audit(self,project):
        need(isinstance(project,str) and project in self.projects,'Unknown project.')
        if self.r.demo:
            return dict(project=dict(id=project,status='SYNTHETIC_PREVIEW'),references=[dict(id='demo-ref-1',classification='UNVERIFIED_SAME_NAME',value='Example missing clip.mp4',reason='A same-name file is only a candidate; no replacement is applied.')],fixes=[],limitations=['Synthetic demo data only.'])
        audit=self.r.audit
        if audit is None:return dict(project=None,references=[],fixes=[],limitations=['No reviewed media audit is installed. This does not establish dependency completeness.'])
        row=next((p for p in audit.get('projects',[]) if p.get('id')==project),None)
        need(row is not None,'Project is absent from this audit snapshot.')
        ids={f for f in row.get('proposedFixes',[]) if isinstance(f,str)}
        fixes=[f for f in audit.get('proposedFixes',[]) if f.get('id') in ids]
        return dict(project=row,references=row.get('references',[]),fixes=fixes,limitations=audit.get('limitations',[]),generatedUtc=audit.get('generatedUtc'))

    def arguments(self,action,project,entry,confirmation):
        need(isinstance(action,str) and action in ACTIONS,'Unknown action.')
        if action!='recover':need(isinstance(project,str) and project in self.projects,'Choose a reviewed project.')
        if action=='open':
            need(isinstance(entry,str),'Choose a project or recovery entry.')
            indexed={e['path'] for e in self.projects[project]['entryPoints']}
            if entry not in indexed:
                need(entry in {e['path'] for e in self.entries(project)['entries']},'Choose an indexed or safely enumerated local project/recovery entry.')
                ordinary(self.r.cache/project/'Files'/entry)
        if action=='release':need(isinstance(confirmation,str) and confirmation==project,'Type the complete selected project ID to free its verified local files.')
        args=[str(self.r.python),'-I','-B','-X','utf8',str(self.r.kit/'magix_projects.py'),action]
        if action!='recover':args+=['--project',project]
        if action=='open':args+=['--entry',entry]
        if action=='release':args+=['--confirm',confirmation]
        return args

    def start(self,action,project=None,entry=None,confirmation=None,expected_revision=None):
        with self.lock:
            need(not self.closing,'INTERLINKED is closing; no new operation can start.')
            need(not self.busy(),'Another operation is active. Finish its MAGIX session before starting another.')
            if action=='release':need(expected_revision==self.revision,'The operation state changed. Review the selected project and confirm Free space again.')
            args=self.arguments(action,project,entry,confirmation)
            if not self.r.demo:
                need(digest(ordinary(self.r.kit/'magix_projects.py'))==MAGIX_SHA,'Reviewed launcher changed; no command started.')
            self.revision+=1
            self.job=dict(id=uuid.uuid4().hex,action=action,projectId=project,entry=entry,status='STARTING',startedUtc=utc(),finishedUtc=None,exitCode=None,awaitingDone=False,promptGeneration=0,demo=self.r.demo)
            self.output.clear();self.demo_done.clear()
            try:
                self.record()
                worker=threading.Thread(target=self._demo if self.r.demo else self._run,args=(args,),daemon=False)
                worker.start()
            except BaseException as exc:
                self.job.update(status='FAILED_NO_PROCESS',exitCode=2,finishedUtc=utc(),error=str(exc));self.revision+=1
                raise
            return dict(self.job)

    def append(self,text):
        with self.lock:
            self.output.extend(text)

    def _demo(self,args):
        try:
            with self.lock:self.job['status']='RUNNING';self.report()
            self.append('DEMO ONLY — no launcher, personal files or editor are used.\n')
            for message in ('Checking the selected working set…\n','Preparing a synthetic operation…\n'):
                time.sleep(.6);self.append(message)
            if self.job['action']=='open':
                with self.lock:self.job.update(status='WAITING_FOR_MAGIX',awaitingDone=True,promptGeneration=self.job['promptGeneration']+1);self.report()
                self.append('Demo session active. Choose “I saved and closed MAGIX” to finish this preview.\n')
                self.demo_done.wait()
            else:time.sleep(.7)
            self.finish(0,'DEMO_COMPLETE')
        except BaseException as exc:self.finish(2,'FAILED',str(exc))

    def _read(self,stream,label,log,job_id=None):
        # read(1) works for input() prompts that have no trailing newline.
        tail='';logging=True
        while True:
            chunk=stream.read(1)
            if not chunk:break
            if logging:
                try:log.write(chunk);log.flush()
                except (OSError,ValueError) as exc:
                    logging=False
                    with self.lock:self.job['reportingWarning']='Output log failed; prompt control is still active: '+str(exc)
            with self.lock:
                if job_id is not None and self.job['id']!=job_id:continue
            self.append(chunk)
            tail=(tail+chunk)[-len(PROMPT):]
            with self.lock:
                if label=='stdout' and self.job['action']=='open' and tail==PROMPT:
                    self.job['status']='WAITING_FOR_MAGIX';self.job['awaitingDone']=True;self.job['promptGeneration']+=1
                    tail=''

    def _run(self,args):
        process=None
        try:
            with open(self.run/(self.job['id']+'-stdout.log'),'x',encoding='utf-8',buffering=1) as out,open(self.run/(self.job['id']+'-stderr.log'),'x',encoding='utf-8',buffering=1) as err:
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
                process=self.factory(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,cwd=str(self.r.kit),shell=False,creationflags=flags)
                with self.lock:self.child=process;self.job['status']='RUNNING'
                readers=[threading.Thread(target=self._read,args=(process.stdout,'stdout',out,self.job['id']),daemon=True),threading.Thread(target=self._read,args=(process.stderr,'stderr',err,self.job['id']),daemon=True)]
                for thread in readers:thread.start()
                with self.lock:self.report()
                code=self._wait_confirmed(process)
                for thread in readers:thread.join(timeout=5)
                self.finish(code,'COMPLETED' if code==0 else 'BLOCKED')
        except BaseException as exc:
            # Never terminate the child merely because reporting/browser failed.
            if process is not None:
                self.append('\nGUI reporting failed; launcher remains active. Keep MAGIX open only while protected, then close it. Recovery may be required.\n')
                self._wait_confirmed(process)
            self.finish(2,'FAILED',str(exc))

    def _wait_confirmed(self,process):
        while True:
            try:return process.wait()
            except Exception as exc:
                with self.lock:
                    self.job['reportingWarning']='Waiting for confirmed launcher exit; control remains active: '+str(exc)
                try:
                    code=process.poll()
                    if code is not None:return code
                except Exception:pass
                time.sleep(.1)

    def finish(self,code,status,error=None):
        with self.lock:
            self.job.update(status=status,exitCode=code,finishedUtc=utc(),awaitingDone=False)
            if error:self.job['error']=error
            self.child=None;self.cache_at=0;self.revision+=1;self.report()

    def session_input(self,value,operation_id,prompt_generation):
        with self.lock:
            need(isinstance(value,str) and value in ('DONE','CANCEL STARTUP'),'Unknown session response.')
            need(self.busy() and self.job['action']=='open' and self.job.get('awaitingDone'),'No active MAGIX confirmation prompt.')
            need(operation_id==self.job['id'] and prompt_generation==self.job['promptGeneration'],'This confirmation is stale; use the current session prompt.')
            if self.r.demo:self.demo_done.set()
            else:
                need(self.child is not None and self.child.poll() is None,'Launcher already exited; inspect its result.')
                self.child.stdin.write(value+'\n');self.child.stdin.flush()
            self.job['awaitingDone']=False;self.job['status']='RUNNING';self.report()
            return dict(status='RESPONSE_SENT',automaticSave=False,automaticRelease=False)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,controller,port=0,token=None):
        super().__init__(('127.0.0.1',port),Handler)
        self.controller=controller;self.token=token or secrets.token_urlsafe(32)
        self.host='127.0.0.1:'+str(self.server_port);self.origin='http://'+self.host
        self.master=load_companion('interlinked_master').safe_service(controller.r,controller,
            violet_asset=getattr(controller.r,'master_violet',None) if controller.r.demo else None)
        self.jobs=load_companion('interlinked_jobs').safe_service(controller.r)

    def server_close(self):
        jobs=getattr(self,'jobs',None)
        if jobs is not None:jobs.close()
        master=getattr(self,'master',None)
        if master is not None:master.close()
        super().server_close()

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass # no token, payload or private path request logging
    def respond(self,code,value,mime='application/json; charset=utf-8'):
        data=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def gate(self,api=False,write=False):
        need(self.headers.get('Host')==self.server.host,'Unexpected Host.')
        # The public static shell contains no catalog/state. Browser extension
        # navigation can be cross-site; every data/action route still requires
        # the private token and strict same-origin checks below.
        if not api and not write:return
        origin=self.headers.get('Origin')
        need(origin is None or origin==self.server.origin,'Cross-origin request rejected.')
        need(self.headers.get('Sec-Fetch-Site') in (None,'same-origin','none'),'Cross-site request rejected.')
        if api:
            need(hmac.compare_digest(self.headers.get('X-INTERLINKED-Token','').encode('utf-8'),self.server.token.encode('ascii')),'Session token required. Open the original local launch link.')
        if write:need(origin==self.server.origin,'Same-origin request required.')
    def do_GET(self):
        try:
            parsed=urlsplit(self.path);path=parsed.path
            self.gate(api=path.startswith('/api/'))
            if path in STATIC:
                need(not parsed.query or path=='/' and parsed.query in ('view=master','view=jobs'),'Unexpected query.')
                name,mime=STATIC[path];self.respond(200,ordinary(self.server.controller.r.web/name).read_bytes(),mime)
            elif path=='/audit':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project audit.')
                identifier(query['project'][0])
                self.respond(200,ordinary(self.server.controller.r.web/'index.html').read_bytes(),'text/html; charset=utf-8')
            elif path=='/api/state' and not parsed.query:self.respond(200,self.server.controller.state())
            elif path=='/api/msi/jobs' and not parsed.query:self.respond(200,self.server.jobs.view())
            elif path=='/api/msi/jobs/log':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'job','stream'} and all(len(v)==1 for v in query.values()),'Select one job and log stream.')
                self.respond(200,self.server.jobs.log(query['job'][0],query['stream'][0]))
            elif path=='/api/master/state' and not parsed.query:self.respond(200,self.server.master.state())
            elif path=='/api/master/settings' and not parsed.query:self.respond(200,self.server.master.state()['settings'])
            elif path=='/api/master/frame' and not parsed.query:
                picture=self.server.master.frame()
                if picture:self.respond(200,picture,'image/jpeg')
                else:self.respond(503,dict(error='MASTER preview is not available yet.'))
            elif path=='/api/catalog' and not parsed.query:self.respond(200,self.server.controller.catalog())
            elif path=='/api/mw5':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one MW5 project.')
                self.respond(200,self.server.controller.mw5_view(query['project'][0]))
            elif path=='/api/entries':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project.')
                self.respond(200,self.server.controller.entries(query['project'][0]))
            elif path=='/api/audit':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project.')
                self.respond(200,self.server.controller.audit(query['project'][0]))
            elif path in ('/api/missing','/api/missing/export'):
                query=parse_qs(parsed.query,strict_parsing=True)
                expected={'project','format'} if path.endswith('/export') else {'project'}
                need(set(query)==expected and all(len(v)==1 for v in query.values()),'Select one report project and export format.')
                result=self.server.controller.missing_view(query['project'][0],query['format'][0] if path.endswith('/export') else None)
                self.respond(200,result)
            else:self.respond(404,dict(error='Unknown route.'))
        except (ValueError,OSError,json.JSONDecodeError) as exc:self.respond(403,dict(error=str(exc)))
    def do_POST(self):
        try:
            self.gate(api=True,write=True)
            need(self.path in ('/api/action','/api/session','/api/quit','/api/missing/report','/api/missing/status','/api/preferences','/api/hub/refresh','/api/mw5/check','/api/mw5/preview','/api/mw5/apply','/api/mw5/finish','/api/master/settings','/api/master/display','/api/msi/jobs/refresh'),'Unknown action route.')
            need(self.headers.get('Content-Type','').split(';')[0]=='application/json','JSON content type required.')
            need(self.headers.get('Transfer-Encoding') is None,'Chunked request bodies are not accepted.')
            length=int(self.headers.get('Content-Length','-1'));need(0<=length<=MAX_BODY,'Request too large or missing length.')
            body=json.loads(self.rfile.read(length));need(isinstance(body,dict),'JSON object required.')
            if self.path=='/api/action':
                need(set(body).issubset({'action','project','entry','confirmation','expectedRevision'}),'Unexpected action fields.')
                result=self.server.controller.start(body.get('action'),body.get('project'),body.get('entry'),body.get('confirmation'),body.get('expectedRevision'))
            elif self.path=='/api/session':
                need(set(body)=={'response','operationId','promptGeneration'},'Unexpected session fields.');result=self.server.controller.session_input(body['response'],body['operationId'],body['promptGeneration'])
            elif self.path=='/api/missing/report':result=self.server.controller.report_missing(body)
            elif self.path=='/api/missing/status':result=self.server.controller.status_missing(body)
            elif self.path=='/api/preferences':result=self.server.controller.preferences(body)
            elif self.path=='/api/hub/refresh':result=self.server.controller.refresh_hub(body)
            elif self.path=='/api/msi/jobs/refresh':
                need(body=={},'Job refresh takes no parameters.')
                result=self.server.jobs.refresh()
            elif self.path=='/api/master/settings':result=self.server.master.settings(body)
            elif self.path=='/api/master/display':result=self.server.master.display(body)
            elif self.path.startswith('/api/mw5/'):result=self.server.controller.mw5_request(self.path.rsplit('/',1)[1],body)
            else:
                need(body=={},'Quit takes no parameters.')
                self.server.controller.request_quit()
                result=dict(status='SERVER_STOPPING');threading.Thread(target=self.server.shutdown,daemon=True).start()
            self.respond(200,result)
        except (ValueError,OSError,json.JSONDecodeError) as exc:self.respond(409,dict(error=str(exc)))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true');parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--desktop',action='store_true',help='Private startup handshake for the INTERLINKED desktop host; never opens a browser.')
    parser.add_argument('--demo-state',type=Path)
    parser.add_argument('--master-worker',type=Path,help='Explicit display worker for isolated demo acceptance only.')
    parser.add_argument('--master-violet',type=Path,help='Approved private Violet artwork for isolated demo acceptance only.')
    args=parser.parse_args()
    need(0<=args.port<=65535,'Invalid loopback port.')
    need(args.demo or args.demo_state is None,'--demo-state is available only in synthetic demo mode.')
    need(args.demo or (args.master_worker is None and args.master_violet is None),'Custom MASTER paths are available only in synthetic demo mode.')
    runtime=demo_runtime(args.demo_state or Path(os.environ.get('TEMP','.'))/('INTERLINKED-Demo-'+uuid.uuid4().hex)) if args.demo else production_runtime()
    if args.demo:
        runtime.master_worker=args.master_worker;runtime.master_violet=args.master_violet
    controller=load_companion('interlinked_controller').controller_class(sys.modules[__name__])(runtime);server=Server(controller,args.port)
    write_json(controller.run/'startup.json',dict(format='interlinked-gui-startup-v1',utc=utc(),mode='DEMO' if args.demo else 'PRODUCTION',projectCount=len(controller.projects),appSha256=digest(Path(__file__)),webSha256={name:digest(runtime.web/name) for name in ('index.html','app.js','style.css')},auditSha256=digest(KIT/'MAGIX-Media-Audit.json') if runtime.audit is not None else None,loopbackOnly=True,port=server.server_port,busy=False,canQuit=True,tokenRecorded=False))
    url=server.origin+'/#'+server.token
    if args.desktop:
        # The host captures this pipe in memory. Never persist the capability URL
        # in receipts, command-line arguments, WebView profiles, or logs.
        print(json.dumps(dict(format='interlinked-desktop-startup-v1',url=url)),flush=True)
    else:
        print(('SYNTHETIC DEMO — ' if args.demo else '')+'INTERLINKED\n'+url+'\nKeep this service window open. Browser tabs may be closed without stopping work.',flush=True)
    if not args.no_browser and not args.desktop:webbrowser.open(url)
    try:
        while True:
            try:server.serve_forever(poll_interval=.25);break
            except KeyboardInterrupt:
                try:controller.request_quit();break
                except ValueError:print('Operation active: protection retained. Close MAGIX and use Done in the browser. No process was terminated.',flush=True)
    finally:server.server_close();controller.close()
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as exc:print('INTERLINKED blocked: '+str(exc),file=sys.stderr);raise SystemExit(2)
