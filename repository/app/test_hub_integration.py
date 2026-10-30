"""Real loopback hub API with synthetic catalog/editor operations only."""
from pathlib import Path
import argparse
from contextlib import contextmanager
import http.client
import importlib.util
import io
import json
import sys
import threading
import time
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('gui_hub_integration',HERE/'interlinked_gui.py')
g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)


def recovery_guard_checks(root,check,orchestration):
    """Native owned locks around synthetic recovery and child-output failures."""
    baseline=HERE.parent/'outputs/Project-Library-2026-10-29/Client/project_library.py'
    engine_spec=importlib.util.spec_from_file_location('hub_recovery_native_engine',baseline)
    native=importlib.util.module_from_spec(engine_spec);sys.modules[engine_spec.name]=native;engine_spec.loader.exec_module(native)
    hubmod=g.load_companion('interlinked_hub');mw5mod=g.load_companion('interlinked_mw5')
    original_engine=g.client_engine
    original_discover=mw5mod.discover_editor;original_sources=mw5mod.find_plugin_sources;original_workspace=mw5mod.MW5Workspace
    events=[]
    # Keep native fixture paths short enough for the unchanged GUI atomic-writer
    # contract on Windows installations without globally enabled long paths.
    native_root=root.parent/('native-'+uuid.uuid4().hex[:8]);native_root.mkdir()

    class LeaseEngine:
        @contextmanager
        def exclusive(self,path,create=False):
            role='workflow' if Path(path).name=='.workflow.lock' else 'cache'
            with native.exclusive(path,create=create):
                events.append(('acquire',role))
                try:yield
                finally:events.append(('release',role))
    proxy=LeaseEngine()

    def held(path):
        try:
            with native.exclusive(path,create=False):pass
        except (OSError,native.LibraryError):return True
        return False

    def fixture(kind):
        base=native_root/(kind+'-'+uuid.uuid4().hex[:6])
        kit=base/'kit';cache=base/'cache';state=base/'state'
        for p in (kit/'MAGIX-Workspace-State',cache,state):p.mkdir(parents=True)
        workflow=kit/'MAGIX-Workspace-State/.workflow.lock';workflow.write_bytes(b'')
        for name in ('binding.json','multi-binding.json'):
            g.write_json(kit/'MAGIX-Workspace-State'/name,dict(status='UNBOUND'))
        runtime=g.Runtime(kit,cache,state,HERE/'web',dict(projects=[dict(id='reviewed-magix',name='Synthetic MAGIX family',bytes=1,files=1,entryPoints=[],warnings=[],notes=[])]),demo=False,python=Path(sys.executable))
        g.write_json(state/'OWNER.json',dict(format='interlinked-gui-state-v1',kit=str(kit),demo=False))
        pid='mw5-companion' if kind=='mw5' else 'code-recovery'
        project=cache/pid;files=project/'Files';files.mkdir(parents=True)
        clientlock=project/'.client.lock';clientlock.write_bytes(b'')
        owner=dict(projectId=pid,catalogId=hubmod.CATALOG,library=str(Path(hubmod.LIBRARY)),returns=str(Path(hubmod.RETURNS)))
        g.write_json(project/'OWNER.json',dict(format='project-library-owned-v1',**owner))
        g.write_json(project/'state.json',dict(format='project-library-cache-v1',status='READY',**owner))
        hub=hubmod.ProjectHub(runtime,runner=lambda kind:None,auto_refresh=False)
        hub._install_catalog(dict(catalogId=hubmod.CATALOG,projects=[dict(id=pid,name='Synthetic recovery project',bytes=1,files=1,warnings=[],notes=[],workspaceRelative='Files')]))
        key=hubmod.key_for(pid)
        if kind=='mw5':
            (state/'mw5').mkdir();g.write_json(state/'mw5/session.json',dict(status='STAGED'))
            for plugin in mw5mod.PLUGINS:(files/plugin).mkdir()
        else:
            g.write_json(state/'folder-session.json',dict(format='interlinked-folder-session-v1',status='ACTIVE',projectId=key,operationId='e'*32))
        return runtime,hub,key,workflow,clientlock,files

    class AdoptingWorkspace:
        def __init__(self,engine,state_root,files,sources,editor,cache_guard=None):
            check(cache_guard is not None,'Recovered MW5 cache lease is passed into the adapter instead of being reacquired')
            check(held(Path(files).parent/'.client.lock'),'Native cache lease remains held during MW5 adapter adoption')
            self.guard=cache_guard;self.status='STAGED';self.state_root=Path(state_root);self.editor={}
            events.append(('adopt','cache'))
        def state(self):return dict(status=self.status)
        def readiness(self):return dict(ready=True,status=self.status)
        def finish_session(self):
            assert self.status=='COLLECTED'
            if self.guard is not None:self.guard.__exit__(None,None,None);self.guard=None
            self.status='IDLE';g.write_json(self.state_root/'session.json',dict(status='IDLE'))
            return dict(status='DONE_LOCAL_COPY_RETAINED',automaticSave=False)

    try:
        g.client_engine=lambda runtime:proxy
        mw5mod.discover_editor=lambda **kw:dict(available=True)
        mw5mod.find_plugin_sources=lambda files,engine:{name:Path(files)/name for name in mw5mod.PLUGINS}
        mw5mod.MW5Workspace=AdoptingWorkspace
        cls=orchestration.controller_class(g)
        runtime,hub,key,workflow,clientlock,files=fixture('mw5')
        controller=cls(runtime,hub=hub)
        try:
            check(events[:2]==[('acquire','workflow'),('acquire','cache')],'Restart reacquires workflow before cache for outstanding MW5 state')
            check(controller.busy() and held(workflow) and held(clientlock),'Recovered MW5 session eagerly protects both native locks before an editor check')
            adapter=controller._ensure_mw5(key)
            check(events==[('acquire','workflow'),('acquire','cache'),('adopt','cache')],'MW5 recovery has no release/reacquire gap during adapter construction')
            check(controller.mw5_recovery_guard is None and held(clientlock),'Adapter adoption transfers ownership while preserving its native cache lease')
            adapter.status='COLLECTED';controller._finish_mw5(key)
            check(not held(clientlock) and not held(workflow) and not controller.busy(),'Explicit recovered MW5 Finish releases cache then workflow only after collection')
        finally:
            if controller.mw5 is not None and controller.mw5.guard is not None:
                controller.mw5.status='COLLECTED';controller.mw5.finish_session()
            controller.active=None;controller.close()

        events.clear()
        runtime,hub,key,workflow,clientlock,files=fixture('folder')
        controller=cls(runtime,hub=hub)
        try:
            check(events==[('acquire','workflow'),('acquire','cache')],'Restart reacquires both generic folder-session locks in the same global order')
            check(controller.busy() and held(workflow) and held(clientlock),'Interrupted generic editing is protected before user confirmation')
            try:controller.session_input('DONE','not-the-operation',1)
            except ValueError:pass
            else:raise AssertionError('Stale recovered Done accepted')
            check(held(workflow) and held(clientlock),'Rejected recovered Done cannot release either native session lock')
            controller.session_input('DONE','e'*32,1)
            check(not held(workflow) and not held(clientlock) and not controller.busy(),'Exact recovered Done releases the native folder cache and workflow leases')
            check(events==[('acquire','workflow'),('acquire','cache'),('release','cache'),('release','workflow')],'Generic recovery keeps uninterrupted lock ownership through explicit Done')
        finally:
            if controller.active:controller.session_input('DONE','e'*32,1)
            controller.close()

        # A successful collect followed by a failing session-receipt finish must
        # retain protection; otherwise another editor could start during recovery.
        class FinishFailure(orchestration.SyntheticMW5):
            def apply_collect(self,key):self.status='COLLECTED';return self.state()
            def finish_session(self):raise OSError('Synthetic receipt flush failure after collect')
        demo=g.demo_runtime(root/('finish-failure-'+uuid.uuid4().hex))
        controller=cls(demo)
        try:
            key=hubmod.key_for('mw5-companion');controller.mw5=FinishFailure();controller.mw5_project=key
            controller.active=dict(kind='mw5',projectId=key,nextAction='Synthetic editor session')
            workflow_held=[False]
            controller._workflow_acquire=lambda:workflow_held.__setitem__(0,True)
            controller._workflow_release=lambda:workflow_held.__setitem__(0,False)
            try:controller._apply_mw5(key,'f'*32,'collect')
            except OSError:pass
            else:raise AssertionError('Finish failure swallowed')
            check(controller.mw5.state()['status']=='COLLECTED' and controller.active is not None,'Collection receipt failure preserves the recovered COLLECTED session')
            check(controller.busy() and workflow_held[0],'Collected-session finish failure cannot release workflow or permit app exit')
        finally:
            controller.active=None;controller.close()

        events.clear()
        runtime,hub,key,workflow,clientlock,files=fixture('overflow')
        # This fixture has no interrupted session. Its fake child acquires the
        # actual cache lease and refuses to exit until the test authorizes it.
        g.write_json(runtime.state/'folder-session.json',dict(format='interlinked-folder-session-v1',status='IDLE'))
        reached_wait=threading.Event();exit_gate=threading.Event();created=[]
        class Output:
            def __init__(self):self.remaining=8*1024*1024+16384
            def read(self,size):
                if not self.remaining:events.append(('drained','stdout'));return ''
                count=min(size,self.remaining);self.remaining-=count;return 'x'*count
        class Child:
            def __init__(self):
                self.stdout=Output();self.stderr=io.StringIO('');self.code=None
                self.guard=proxy.exclusive(clientlock,create=False);self.guard.__enter__()
            def wait(self):
                reached_wait.set();exit_gate.wait(6)
                if not exit_gate.is_set():raise OSError('Synthetic child still active')
                check(self.stdout.remaining==0,'Oversized engine response is fully drained before waiting for confirmed exit')
                if self.guard is not None:self.guard.__exit__(None,None,None);self.guard=None
                self.code=0;events.append(('confirmed','exit'));return 0
            def poll(self):return self.code
        def factory(*a,**kw):
            child=Child();created.append(child);return child
        controller=cls(runtime,hub=hub,process_factory=factory)
        try:
            controller._schedule('fetch',key,lambda:controller._client_action('fetch',key,None))
            check(reached_wait.wait(4),'Oversized response reaches a live-child confirmed-exit wait')
            check(controller.busy() and held(workflow) and held(clientlock),'Output overflow leaves both workflow and child cache protected while the engine still runs')
            check(('release','workflow') not in events,'Response size error cannot release workflow before confirmed child exit')
            exit_gate.set()
            end=time.monotonic()+6
            while controller.transferring and time.monotonic()<end:time.sleep(.01)
            check(not controller.transferring and controller.job['status']=='BLOCKED','Oversized response reports a bounded error after the synthetic child finishes')
            check(events.index(('confirmed','exit'))<events.index(('release','workflow')),'Workflow is released only after actual child exit is confirmed')
        finally:
            exit_gate.set()
            end=time.monotonic()+6
            while controller.transferring and time.monotonic()<end:time.sleep(.01)
            controller.close()
    finally:
        g.client_engine=original_engine
        mw5mod.discover_editor=original_discover;mw5mod.find_plugin_sources=original_sources;mw5mod.MW5Workspace=original_workspace


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/('hub-integration-'+uuid.uuid4().hex);root.mkdir(parents=True)
    runtime=g.demo_runtime(root/'state')
    orchestration=g.load_companion('interlinked_controller')
    cls=orchestration.controller_class(g)
    controller=cls(runtime)
    server=g.Server(controller,token='synthetic-hub-integration-token')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    checks=[]

    def check(value,label):
        assert value,label
        checks.append(label)
    def request(method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
        data=json.dumps(body).encode('utf-8') if body is not None else None
        all_headers={'X-INTERLINKED-Token':server.token}
        if data is not None:all_headers.update({'Origin':server.origin,'Content-Type':'application/json'})
        if headers:all_headers.update(headers)
        connection.request(method,path,data,all_headers)
        response=connection.getresponse();content=response.read();code=response.status;connection.close()
        return code,json.loads(content)
    def post(path,body):return request('POST',path,body)
    def state():
        code,value=request('GET','/api/state');assert code==200
        return value
    def until(fn,label):
        end=time.monotonic()+8
        while time.monotonic()<end:
            value=fn()
            if value:return value
            time.sleep(.01)
        raise AssertionError(label)
    def operation_done():
        return until(lambda:(lambda s:s if not s.get('transferring') else None)(state()),'Synthetic operation did not finish')
    def job_ok():
        s=operation_done()
        check(s['operation']['exitCode']==0,'Operation completes successfully: '+s['operation']['action'])
        return s

    try:
        code,catalog=request('GET','/api/catalog')
        check(code==200 and len(catalog['projects'])==6,'Unified endpoint retains four MAGIX families plus two general-library demo projects')
        magix=next(p for p in catalog['projects'] if p['adapter']=='magix')['id']
        code_project=next(p for p in catalog['projects'] if p['type']=='coding_data')['id']
        mw5=next(p for p in catalog['projects'] if p['type']=='mw5')['id']
        check(catalog['hub']['libraryStatus']=='DEMO' and catalog['hub']['catalogStale'] is False,'Unified catalog distinguishes synthetic health from live verified status')
        check(all(p['originalId'] for p in catalog['projects']),'Each adapter exposes its original identity separately from routing identity')
        check(request('GET','/api/catalog',headers={'X-INTERLINKED-Token':'invalid'})[0]==403,'Unified catalog remains capability-token protected')
        check(post('/api/preferences',dict(favorite=code_project,enabled=True))[0]==200,'Generic project favorite can be saved through authenticated endpoint')
        check(post('/api/preferences',dict(favorite=magix,enabled=True))[0]==200,'MAGIX favorite uses the same private preferences endpoint')
        check(post('/api/preferences',dict(recent=mw5))[0]==200,'Recent project selection is stored outside project files')
        prefs=request('GET','/api/catalog')[1]['preferences']
        check(set(prefs['favorites'])=={code_project,magix} and prefs['recent'][0]['id']==mw5,'Both adapter preferences appear consistently in catalog result')
        check(post('/api/preferences',dict(favorite=code_project,enabled='yes'))[0]==409,'Non-Boolean favorite toggle rejected')
        check(post('/api/preferences',dict(favorite='unknown-project',enabled=True))[0]==409,'Unknown project cannot enter preferences')
        check(request('POST','/api/preferences',dict(recent=mw5),headers={'Origin':'https://evil.invalid'})[0]==409,'Cross-origin preferences cannot mutate local state')
        check(post('/api/action',dict(action='folder',project='unknown-project'))[0]==409,'Unknown project action is rejected before scheduling')
        for bad in ([],{},None,42):
            check(post('/api/action',dict(action='folder',project=bad))[0]==409,'Non-scalar project ID rejected: '+type(bad).__name__)
        check(post('/api/action',dict(action=['fetch'],project=code_project))[0]==409,'Non-scalar action rejected')
        check(post('/api/action',dict(action='folder',project=code_project,arbitraryPath='C:\\Windows'))[0]==409,'HTTP action cannot supply arbitrary filesystem path')
        check(request('GET','/api/entries?project='+code_project)[1]['entries']==[],'Generic project cannot inherit MAGIX file-selection semantics')
        check(post('/api/hub/refresh',{})[0]==200,'Explicit demo health refresh is harmless and available')
        check(post('/api/hub/refresh',dict(path='foreign'))[0]==409,'Refresh endpoint cannot accept arbitrary probe paths')
        check(request('GET','/api/mw5?project='+code_project)[0]==403,'Coding/data project cannot display another adapter MW5 state')

        # Exercise metadata isolation with owned synthetic paths only. No engine
        # is constructed and no real cache is inspected in this temporary mode.
        saved_cache=runtime.cache
        runtime.cache=root/'synthetic-cache';runtime.cache.mkdir()
        damaged=runtime.cache/magix;damaged.mkdir()
        (damaged/'OWNER.json').write_text('{"format":"foreign-owner"}',encoding='utf-8')
        (damaged/'state.json').write_text('{"status":"READY"}',encoding='utf-8')
        runtime.demo=False;controller.cache_at=0;controller.hub.invalidate()
        try:
            code,with_damage=request('GET','/api/catalog')
            check(code==200 and len(with_damage['projects'])==6,'One malformed MAGIX cache cannot hide the unified project library')
            check(next(r for r in with_damage['projects'] if r['id']==magix)['cache']['status']=='CHECK_REQUIRED','Damaged cache stays visible with its own warning')
        finally:
            runtime.demo=True;runtime.cache=saved_cache;controller.cache_at=0;controller.hub.invalidate()

        check(post('/api/action',dict(action='fetch',project=code_project))[0]==200,'Generic fetch dispatches through adapter')
        job_ok()
        check(post('/api/action',dict(action='folder',project=code_project))[0]==200,'Generic folder workflow starts one managed editing session')
        active=until(lambda:(lambda s:s if s.get('activeSession') and s['operation']['awaitingDone'] else None)(state()),'Generic session did not become ready')
        check(active['busy'] and not active['canQuit'] and active['activeSession']['projectId']==code_project,'Active folder session is visible and prevents shutdown')
        check(post('/api/quit',{})[0]==409,'Quit refused during generic editing session')
        check(post('/api/action',dict(action='open',project=magix,entry='anything'))[0]==409,'Generic session prevents competing MAGIX launch')
        prompt=active['operation']
        check(post('/api/session',dict(response='DONE',operationId='stale',promptGeneration=prompt['promptGeneration']))[0]==409,'Stale finish cannot release generic session')
        check(post('/api/session',dict(response='DONE',operationId=prompt['id'],promptGeneration=prompt['promptGeneration']))[0]==200,'Explicit Finish session releases generic synthetic session')
        done=job_ok()
        check(not done['busy'] and done['operation']['result']['automaticSave'] is False,'Finishing generic editing does not automatically save or release')

        check(post('/api/action',dict(action='open',project=mw5))[0]==200,'MW5 open is dispatched for reviewed adapter type')
        blocked=operation_done()
        check(blocked['operation']['status']=='BLOCKED' and not blocked['busy'],'MW5 cannot open before explicit plugin staging')
        check(post('/api/mw5/check',dict(project=mw5))[0]==200,'MW5 editor check uses reviewed project identity')
        job_ok()
        check(request('GET','/api/mw5?project='+mw5)[1]['ready'],'MW5 readiness is exposed after explicit check')
        check(post('/api/mw5/preview',dict(project=mw5,direction='stage'))[0]==200,'MW5 staging requires an explicit preview operation')
        job_ok()
        preview=request('GET','/api/mw5?project='+mw5)[1]['preview']
        check(preview['direction']=='stage' and preview['applicable'],'Staging preview exposes changes without applying them')
        check(post('/api/mw5/apply',dict(project=mw5,previewId='stale',confirmation='STAGE'))[0]==409,'Stale transfer preview cannot be applied')
        check(post('/api/mw5/apply',dict(project=mw5,previewId=preview['id'],confirmation='COLLECT'))[0]==409,'Transfer direction must match its reviewed preview')
        check(post('/api/mw5/apply',dict(project=mw5,previewId=preview['id'],confirmation='STAGE'))[0]==200,'Explicit staging confirmation starts the synthetic transfer')
        staged=job_ok()
        check(staged['activeSession']['kind']=='mw5' and staged['busy'],'Staged MW5 plugin session keeps app protected between operations')
        check(post('/api/quit',{})[0]==409,'Quit refused while MW5 plugins are staged')
        check(post('/api/action',dict(action='save',project=mw5))[0]==409,'Save-back is blocked until staged MW5 changes are collected')
        check(post('/api/action',dict(action='open',project=mw5))[0]==200,'Staged MW5 editor can open via its reviewed adapter')
        editing=job_ok()
        check('collect' in editing['activeSession']['nextAction'].lower(),'Active MW5 session prominently explains collection before save-back')
        check(post('/api/session',dict(response='DONE',operationId=editing['operation']['id'],promptGeneration=0))[0]==409,'Generic Done cannot bypass MW5 collection')
        check(post('/api/mw5/preview',dict(project=mw5,direction='collect'))[0]==200,'MW5 collection has its own explicit preview')
        job_ok()
        preview=request('GET','/api/mw5?project='+mw5)[1]['preview']
        check(post('/api/mw5/apply',dict(project=mw5,previewId=preview['id'],confirmation='COLLECT'))[0]==200,'Collection requires a matching reviewed preview and explicit confirmation')
        collected=job_ok()
        check(not collected['busy'] and collected['activeSession'] is None,'Collected MW5 session releases managed editing state')
        check(post('/api/action',dict(action='save',project=mw5))[0]==200,'Save-back remains an explicit separate operation after MW5 collection')
        job_ok()
        check(post('/api/action',dict(action='release',project=mw5,confirmation=mw5,expectedRevision=-1))[0]==409,'Stale UI revision cannot release MW5 managed cache')
        check(request('GET','/api/mw5?project=not-known')[0]==403,'MW5 state cannot expose arbitrary project paths')
        check(post('/api/mw5/check',dict(project=mw5,executable='C:\\Windows\\cmd.exe'))[0]==409,'Editor discovery cannot take an executable from HTTP')

        class RecoverableDemo(orchestration.SyntheticMW5):
            def __init__(self):
                super().__init__();self.status='APPLYING'
                self.pending=dict(id='d'*32,previewId='d'*32,direction='stage',changes=[],conflicts=[],warnings=['Synthetic resumed transfer'],applicable=True)
                self.previews[self.pending['id']]=self.pending
            def resume_preview(self):return dict(self.pending) if self.status=='APPLYING' else None
        controller.mw5=RecoverableDemo();controller.active=dict(kind='mw5-recovery',projectId=mw5,nextAction='Synthetic interrupted transfer');controller.last_preview=None
        check(post('/api/mw5/check',dict(project=mw5))[0]==200,'Interrupted MW5 session can be checked without discarding its journal')
        job_ok()
        resumed=request('GET','/api/mw5?project='+mw5)[1]
        check(resumed['preview']['id']=='d'*32 and resumed['session']['status']=='APPLYING','Restart exposes the exact interrupted preview instead of requiring a new one')
        check(not resumed['canStage'] and not resumed['canCollect'],'Interrupted transfer cannot offer incompatible new previews')
        check(post('/api/mw5/apply',dict(project=mw5,previewId='d'*32,confirmation='STAGE'))[0]==200,'User can explicitly resume the same reviewed transfer ID')
        job_ok()
        controller.mw5.status='COLLECTED';controller.active=dict(kind='mw5-recovery',projectId=mw5,nextAction='Synthetic completed collection before interruption');controller.last_preview=None
        check(post('/api/mw5/check',dict(project=mw5))[0]==200,'Completed collection receipt survives app interruption')
        checked=job_ok();recovered=request('GET','/api/mw5?project='+mw5)[1]
        check(checked['busy'] and recovered['session']['canFinish'],'Recovered COLLECTED state retains session protection until explicit Finish')
        check(post('/api/mw5/finish',dict(project=mw5))[0]==200,'Recovered collected session has an explicit Finish endpoint')
        finished=job_ok()
        check(not finished['busy'] and finished['activeSession'] is None,'Explicit recovered Finish releases session without automatic save-back')
        check(post('/api/quit',{})[0]==200,'Idle unified service accepts guarded shutdown')
        thread.join(timeout=4)
        check(not thread.is_alive(),'Unified server stops cleanly after guarded quit')
        recovery_guard_checks(root,check,orchestration)
        result=dict(status='PASS',count=len(checks),checks=checks,scope='Real loopback API; synthetic catalog, editor and library operations only; no MSI or real editors')
        receipt=root/'hub-integration-results.json';receipt.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(status='PASS',count=len(checks),receipt=str(receipt))))
    finally:
        # Cleanup only this owned in-process synthetic fixture, using its normal
        # confirmation interfaces rather than terminating a workflow process.
        if controller.active and controller.active.get('kind')=='folder':
            controller.session_input('DONE',controller.job['id'],controller.job['promptGeneration'])
            until(lambda:not controller.busy(),'Synthetic folder cleanup failed')
        if controller.active and controller.active.get('kind')=='mw5':
            controller.mw5.status='COLLECTED';controller.mw5.finish_session();controller.active=None
        server.shutdown();server.server_close();controller.close()


if __name__=='__main__':main()
