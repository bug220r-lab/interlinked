"""INTERLINKED: loopback-only GUI for the unchanged reviewed MAGIX launcher.

Python 3.12 standard library. No automatic save, release, application termination,
credential storage, or public network listener. --demo never invokes the launcher.
"""
from __future__ import annotations
import argparse
from collections import deque
import ctypes
from dataclasses import dataclass
import datetime as dt
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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

def ordinary(path,allow_missing=False):
    """Metadata only: no links, junctions, recall or offline paths."""
    path=Path(path)
    need(path.is_absolute(),'Absolute local path required.')
    for p in reversed((path,*path.parents)):
        try:s=os.lstat(fs(p))
        except FileNotFoundError:
            if allow_missing:continue
            raise
        need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&(0x400|0x1000|0x40000|0x400000),'Link/cloud placeholder not accepted: '+str(p))
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
    with open(temporary,'x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def identifier(value):
    need(isinstance(value,str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,159}',value),'Invalid project identifier.')
    return value

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
    expected={'interlinked_gui.py','web/index.html','web/app.js','web/style.css'}
    need(seal.get('format')=='interlinked-gui-checksums-v1' and set(seal.get('files',{}))==expected,'GUI seal is missing or incomplete.')
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
                need(not parsed.query,'Unexpected query.')
                name,mime=STATIC[path];self.respond(200,ordinary(self.server.controller.r.web/name).read_bytes(),mime)
            elif path=='/audit':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project audit.')
                identifier(query['project'][0])
                self.respond(200,ordinary(self.server.controller.r.web/'index.html').read_bytes(),'text/html; charset=utf-8')
            elif path=='/api/state' and not parsed.query:self.respond(200,self.server.controller.state())
            elif path=='/api/catalog' and not parsed.query:self.respond(200,self.server.controller.catalog())
            elif path=='/api/entries':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project.')
                self.respond(200,self.server.controller.entries(query['project'][0]))
            elif path=='/api/audit':
                query=parse_qs(parsed.query,strict_parsing=True)
                need(set(query)=={'project'} and len(query['project'])==1,'Select one project.')
                self.respond(200,self.server.controller.audit(query['project'][0]))
            else:self.respond(404,dict(error='Unknown route.'))
        except (ValueError,OSError,json.JSONDecodeError) as exc:self.respond(403,dict(error=str(exc)))
    def do_POST(self):
        try:
            self.gate(api=True,write=True)
            need(self.path in ('/api/action','/api/session','/api/quit'),'Unknown action route.')
            need(self.headers.get('Content-Type','').split(';')[0]=='application/json','JSON content type required.')
            need(self.headers.get('Transfer-Encoding') is None,'Chunked request bodies are not accepted.')
            length=int(self.headers.get('Content-Length','-1'));need(0<=length<=MAX_BODY,'Request too large or missing length.')
            body=json.loads(self.rfile.read(length));need(isinstance(body,dict),'JSON object required.')
            if self.path=='/api/action':
                need(set(body).issubset({'action','project','entry','confirmation','expectedRevision'}),'Unexpected action fields.')
                result=self.server.controller.start(body.get('action'),body.get('project'),body.get('entry'),body.get('confirmation'),body.get('expectedRevision'))
            elif self.path=='/api/session':
                need(set(body)=={'response','operationId','promptGeneration'},'Unexpected session fields.');result=self.server.controller.session_input(body['response'],body['operationId'],body['promptGeneration'])
            else:
                need(body=={},'Quit takes no parameters.')
                self.server.controller.request_quit()
                result=dict(status='SERVER_STOPPING');threading.Thread(target=self.server.shutdown,daemon=True).start()
            self.respond(200,result)
        except (ValueError,OSError,json.JSONDecodeError) as exc:self.respond(409,dict(error=str(exc)))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true');parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--demo-state',type=Path)
    args=parser.parse_args()
    need(0<=args.port<=65535,'Invalid loopback port.')
    need(args.demo or args.demo_state is None,'--demo-state is available only in synthetic demo mode.')
    runtime=demo_runtime(args.demo_state or Path(os.environ.get('TEMP','.'))/('INTERLINKED-Demo-'+uuid.uuid4().hex)) if args.demo else production_runtime()
    controller=Controller(runtime);server=Server(controller,args.port)
    write_json(controller.run/'startup.json',dict(format='interlinked-gui-startup-v1',utc=utc(),mode='DEMO' if args.demo else 'PRODUCTION',projectCount=len(controller.projects),appSha256=digest(Path(__file__)),webSha256={name:digest(runtime.web/name) for name in ('index.html','app.js','style.css')},auditSha256=digest(KIT/'MAGIX-Media-Audit.json') if runtime.audit is not None else None,loopbackOnly=True,port=server.server_port,busy=False,canQuit=True,tokenRecorded=False))
    url=server.origin+'/#'+server.token
    print(('SYNTHETIC DEMO — ' if args.demo else '')+'INTERLINKED\n'+url+'\nKeep this service window open. Browser tabs may be closed without stopping work.',flush=True)
    if not args.no_browser:webbrowser.open(url)
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
