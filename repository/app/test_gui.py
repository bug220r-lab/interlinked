"""Local synthetic GUI tests: fake children, real loopback HTTP, no media/apps."""
from pathlib import Path
import argparse
import hashlib
import http.client
import importlib.util
import io
import json
import queue
import sys
import threading
import time
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('gui_under_test',HERE/'interlinked_gui.py')
g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
root=args.output/('fixture-'+uuid.uuid4().hex);root.mkdir(parents=True)
kit=root/'kit';kit.mkdir();cache=root/'cache';cache.mkdir();state=root/'state'
launcher=kit/'magix_projects.py';launcher.write_text('# synthetic fixture, never executed\n')
g.MAGIX_SHA=hashlib.sha256(launcher.read_bytes()).hexdigest()
PID='demo-project-one';ENTRY=g.PREFIX+'OneDrive/Documents/original.MVP'
index=dict(projects=[dict(id=PID,name='Synthetic project',bytes=20,files=1,entryPoints=[dict(path=ENTRY,kind='project')],notes=[],warnings=[])])
audit=dict(format='magix-media-audit-gui-v1',projects=[dict(id=PID,referenceIds=['unused'],references=[dict(id='ref',path='old clip.mp4',classification='UNVERIFIED')],proposedFixes=['fix-1'])],proposedFixes=[dict(id='fix-1',automaticSafe=False,reason='Manual review only')])
runtime=g.Runtime(kit,cache,state,HERE/'web',index,audit,False,Path(sys.executable))
checks=[]
def check(value,label):
    assert value,label
    checks.append(label)
def rejects(fn,label):
    try:fn()
    except (ValueError,OSError):checks.append(label)
    else:raise AssertionError(label)
def until(fn):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if fn():return
        time.sleep(.01)
    raise AssertionError('Fixture condition timed out')

class Pipe:
    def __init__(self):self.q=queue.Queue()
    def feed(self,text):
        for c in text:self.q.put(c)
    def read(self,n=1):
        value=self.q.get();return '' if value is None else value
    def end(self):self.q.put(None)
class Input:
    def __init__(self,process):self.process=process;self.responses=[]
    def write(self,value):
        self.responses.append(value)
        if len(self.responses)==1:self.process.stdout.feed('MAGIX is still running.\n'+g.PROMPT)
        else:self.process.complete(0)
    def flush(self):pass
class FakeProcess:
    def __init__(self):
        self.stdout=Pipe();self.stderr=Pipe();self.stdin=Input(self);self.done=threading.Event();self.code=None
        self.stdout.feed('Synthetic launcher started\n'+g.PROMPT);self.wait_failure=True
    def wait(self):
        if self.wait_failure:self.wait_failure=False;raise OSError('synthetic uncertain wait')
        self.done.wait();return self.code
    def poll(self):return self.code
    def complete(self,code):self.code=code;self.stdout.end();self.stderr.end();self.done.set()
created=[];calls=[]
def factory(command,**kw):
    calls.append((command,kw));p=FakeProcess();created.append(p);return p
controller=g.Controller(runtime,factory)
server=None
try:
    rejects(lambda:g.Controller(runtime,factory),'Second GUI instance for same kit is rejected')
    for value in ([],{},None,1):rejects(lambda value=value:controller.arguments(value,PID,ENTRY,None),'Non-scalar action rejected '+str(type(value)))
    rejects(lambda:controller.arguments('open',[],ENTRY,None),'Non-scalar project rejected')
    rejects(lambda:controller.arguments('open',PID,{},None),'Non-scalar entry rejected')
    rejects(lambda:controller.arguments('open',PID,r'C:\Windows\system.ini',None),'Arbitrary external entry rejected')
    rejects(lambda:controller.arguments('release',PID,None,'YES'),'Release requires complete selected ID')
    result=controller.audit(PID)
    check(result['references'][0]['id']=='ref' and result['fixes'][0]['id']=='fix-1','Actual nested audit rows and global proposal IDs integrate')

    # Metadata-only discovery of a new long-path Save As file.
    project=cache/PID;project.mkdir();files=project/'Files';files.mkdir()
    identity=dict(projectId=PID,catalogId='msi-project-library-v1',library=str(Path(r'\\MSI\ProjectLibrary')),returns=str(Path(r'\\MSI\ProjectReturns')))
    g.write_json(project/'OWNER.json',dict(format='project-library-owned-v1',**identity))
    g.write_json(project/'state.json',dict(format='project-library-cache-v1',**identity,status='READY'))
    deep=files/(g.PREFIX+'OneDrive/Documents')/('nested-'+('x'*55))/('Unicode-'+('y'*50))
    deep.mkdir(parents=True);saved=deep/'Saved as Ω new.MVP';saved.write_bytes(b'fixture only')
    entries=controller.entries(PID)['entries'];relative=saved.relative_to(files).as_posix()
    check(len(str(saved))>259 and any(r['path']==relative and r['origin']=='LOCAL_SAVE_AS' for r in entries),'New Unicode Save As entry longer than260 chars is safely discovered')
    check(controller.arguments('open',PID,relative,None)[-1]==relative,'Discovered exact relative path is preserved in argv')
    saved.unlink();rejects(lambda:controller.arguments('open',PID,relative,None),'Removed Save As entry is rejected without restoration')
    check(not saved.exists(),'READY deletion stays deleted')
    original_cap=g.ENTRY_CAP;g.ENTRY_CAP=1
    try:rejects(lambda:controller.entries(PID),'Local metadata traversal limit fails closed')
    finally:g.ENTRY_CAP=original_cap
    bad=dict(format='project-library-cache-v1',**identity,status='READY');bad['library']='untrusted'
    g.write_json(project/'state.json',bad)
    rejects(lambda:controller.entries(PID),'Wrong cache library identity blocks discovery')
    g.write_json(project/'state.json',dict(format='project-library-cache-v1',**identity,status='READY'))

    # No child exists when initial receipt persistence fails.
    record=controller.record
    controller.record=lambda:(_ for _ in ()).throw(OSError('synthetic disk full'))
    rejects(lambda:controller.start('open',PID,ENTRY),'Prelaunch receipt failure refuses process start')
    check(not controller.busy() and not created and controller.job['status']=='FAILED_NO_PROCESS','Prelaunch failure cannot leave a phantom busy operation')
    controller.record=record

    # Real loopback listener validates all HTTP boundaries before dispatch.
    server=g.Server(controller,token='fixture-token-only');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        data=json.dumps(body).encode() if body is not None else None
        h={'X-INTERLINKED-Token':server.token}
        if data is not None:h.update({'Content-Type':'application/json','Origin':server.origin})
        if headers:h.update(headers)
        connection.request(method,path,data,h);response=connection.getresponse();content=response.read();status=response.status;connection.close();return status,content
    check(request('GET','/',headers={'Sec-Fetch-Site':'cross-site'})[0]==200,'Cross-site navigation can load public static shell only')
    check(request('GET','/api/state',headers={'Sec-Fetch-Site':'cross-site'})[0]==403,'Cross-site data API is rejected')
    check(request('GET','/api/state',headers={'Host':'evil.invalid'})[0]==403,'DNS rebinding Host is rejected')
    check(request('GET','/api/state',headers={'X-INTERLINKED-Token':'wrong'})[0]==403,'Wrong capability token is rejected')
    check(request('POST','/api/action',dict(action='open',project=PID,entry=ENTRY),{'Origin':'https://evil.invalid'})[0]==409,'Cross-origin mutation is rejected')
    check(request('GET','/../../private')[0]==404,'No arbitrary static file route')
    check(request('POST','/api/action',dict(action='unknown',project=PID))[0]==409,'Unknown command is rejected')

    job=controller.start('open',PID,ENTRY)
    until(lambda:controller.job.get('awaitingDone'))
    until(lambda:'confirmed launcher exit' in controller.job.get('reportingWarning',''))
    check(controller.busy() and controller.child is created[0],'Uncertain child wait retains busy state and live control until confirmed exit')
    check(calls[-1][1]['shell'] is False and calls[-1][0][-1]==ENTRY,'Fixed argument list uses shell false and exact selected entry')
    rejects(lambda:controller.start('save',PID),'Busy child serializes all subsequent actions')
    check(request('POST','/api/quit',{})[0]==409,'Quit cannot close interface during active child')
    observation=dict(project=PID,path='=not-a-command.mp4',source='user_dialog',note='Synthetic dialog only',requestId=uuid.uuid4().hex)
    check(request('POST','/api/missing/report',observation,{'Origin':'https://evil.invalid'})[0]==409,'Missing report requires same-origin token-protected POST')
    check(request('GET','/api/missing?project='+PID,headers={'X-INTERLINKED-Token':'wrong'})[0]==403,'Report history remains capability-protected')
    check(request('GET','/api/missing/export?project='+PID+'&format=csv',headers={'Host':'evil.invalid'})[0]==403,'Report export rejects unexpected Host')
    check(request('POST','/api/missing/report',observation)[0]==200 and controller.busy(),'Authenticated report can append while active child remains busy')
    check(json.loads(request('GET','/api/missing?project='+PID)[1])['records'][0]['path']==observation['path'],'Report read returns only literal user data')
    check(json.loads(request('GET','/api/missing/export?project='+PID+'&format=csv')[1])['text'].find('Text: =not-a-command.mp4')>=0,'Authenticated CSV export has neutral display prefix')
    check(request('GET','/api/missing/export?project='+PID+'&format=../../file')[0]==403,'Export format cannot choose a filesystem path')
    check(request('POST','/api/missing/report',dict(observation,command='ignored?'))[0]==409,'Unexpected report command field rejected')
    generation=controller.job['promptGeneration'];operation=controller.job['id']
    rejects(lambda:controller.session_input('DONE','old',generation),'Stale operation cannot answer current session')
    controller.session_input('DONE',operation,generation)
    until(lambda:controller.job['promptGeneration']>generation)
    check(controller.busy(),'Rejected Done keeps child and cache session alive')
    rejects(lambda:controller.session_input('DONE',operation,generation),'Stale prompt generation cannot repeat a response')
    # stderr old prompt and logging failure do not manufacture a new prompt.
    class BadLog:
        def write(self,value):raise OSError('synthetic output disk full')
        def flush(self):pass
    before=controller.job['promptGeneration']
    controller._read(io.StringIO(g.PROMPT),'stderr',BadLog())
    check(controller.job['promptGeneration']==before,'Stderr prompt text cannot re-arm Done')
    controller.session_input('DONE',operation,before)
    until(lambda:not controller.busy())
    check(controller.job['status']=='COMPLETED' and created[0].stdin.responses==['DONE\n','DONE\n'],'Session completion waits for actual child exit, with stdin retained')
    # Prompt recognition survives log failures, without reading a real process.
    controller.job.update(action='open',status='RUNNING',awaitingDone=False)
    controller._read(io.StringIO(g.PROMPT),'stdout',BadLog())
    check(controller.job['awaitingDone'] and controller.job['promptGeneration']==before+1,'Log failure still drains stdout and detects new prompt')
    controller.job.update(status='COMPLETED',awaitingDone=False)
    rejects(lambda:controller.start('release',PID,confirmation=PID,expected_revision=-1),'Stale browser revision cannot release a project')
    check(request('POST','/api/quit',{})[0]==200,'Idle quit is accepted')
    rejects(lambda:controller.start('save',PID),'Closing gate blocks a race to start after quit')
finally:
    for child in created:
        if child.poll() is None:child.complete(2)
    if server:server.shutdown();server.server_close()
    until(lambda:not controller.busy())
    controller.close()

result=dict(status='PASS',count=len(checks),checks=checks,appSha256=hashlib.sha256((HERE/'interlinked_gui.py').read_bytes()).hexdigest(),fixtureRoot=str(root),fakeSubprocessOnly=True,noProductionData=True,noActualApplicationLaunch=True,networkScope='Synthetic loopback listener only')
args.output.mkdir(parents=True,exist_ok=True)
(args.output/'backend-test-results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
