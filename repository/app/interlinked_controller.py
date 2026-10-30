"""Unified application orchestration; accepted MAGIX/client engines stay unchanged.

No user supplied paths or commands cross the HTTP boundary. Generic editing and
MW5 staging hold the original workflow/cache locks until explicitly finished.
"""
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid


def controller_class(g):
    hub_module=g.load_companion('interlinked_hub')
    mw5_module=g.load_companion('interlinked_mw5')

    class HubController(g.Controller):
        def __init__(self,runtime,process_factory=subprocess.Popen,hub=None,mw5_factory=None):
            super().__init__(runtime,process_factory)
            self.hub=hub or hub_module.ProjectHub(runtime)
            self.mw5_factory=mw5_factory
            self.mw5=None;self.mw5_project=None;self.mw5_error=None
            self.active=None;self.workflow=None;self.folder_recovery_guard=None;self.mw5_recovery_guard=None;self.session_done=threading.Event()
            self.last_preview=None;self.transferring=False
            self.engine=None if runtime.demo else g.client_engine(runtime)
            self.stats=g.load_companion('interlinked_stats').StatsService(self,g)
            # An interrupted MW5 session must be reconciled, never forgotten on
            # restart. The adapter's owned receipt is authoritative.
            self.mw5_root=runtime.state/'mw5'
            if not runtime.demo and self.mw5_root.exists() and (not (self.mw5_root/'session.json').exists() or g.read_json(self.mw5_root/'session.json').get('status')!='IDLE'):
                self.mw5_error='A previous MW5 transfer may need reconciliation. Check MW5 before starting another project.'
                project=hub_module.key_for('mw5-companion')
                self.hub.folder_path(project)
                self.active=dict(kind='mw5-recovery',projectId=project,nextAction=self.mw5_error)
                self._workflow_acquire()
                guard=self.engine.exclusive(g.ordinary(runtime.cache/'mw5-companion/.client.lock'),create=False)
                guard.__enter__();self.mw5_recovery_guard=guard
            self.folder_journal=runtime.state/'folder-session.json'
            if self.folder_journal.exists():
                prior=g.read_json(self.folder_journal)
                g.need(prior.get('format')=='interlinked-folder-session-v1' and prior.get('status') in ('ACTIVE','IDLE'),'Folder-session journal needs review.')
                if prior['status']=='ACTIVE':
                    g.need(self.active is None,'Multiple interrupted sessions need explicit review.')
                    self.active=dict(kind='folder-recovery',projectId=prior['projectId'],nextAction='A previous folder session was interrupted. Close all its editors and terminals, then confirm Finish session.')
                    self.job=dict(id=prior['operationId'],action='folder',projectId=prior['projectId'],status='WAITING_FOR_APP',awaitingDone=True,promptGeneration=1,demo=runtime.demo)
                    if not runtime.demo:
                        self.hub.folder_path(prior['projectId'])
                        self._workflow_acquire()
                        guard=self.engine.exclusive(g.ordinary(runtime.cache/self.hub.original_id(prior['projectId'])/'.client.lock'),create=False)
                        guard.__enter__();self.folder_recovery_guard=guard

        def busy(self):
            return super().busy() or getattr(self,'active',None) is not None or getattr(self,'transferring',False)

        def _operation_running(self):
            return self.job is not None and self.job['status'] in ('STARTING','RUNNING')

        def _row(self,project):
            g.need(isinstance(project,str),'Choose a project.')
            return self.hub.lookup(project)

        def catalog(self):
            result=super().catalog()
            for row in result['projects']:
                row.update(adapter='magix',type='magix',projectType='magix',originalId=row['id'],
                           capabilities=dict(fetch=False,openFolder=False,openApplication=True,finish=True,save=True,verify=True,release=True))
                try:
                    state={} if self.r.demo else (self.cache_state(row['id']) or {})
                    row['lastSaved']=state.get('savedUnixTime')
                except (ValueError,OSError,json.JSONDecodeError) as exc:
                    # One damaged cache must not hide all other projects. The
                    # accepted adapter will continue refusing actions on it.
                    row['lastSaved']=None
                    row['cache']=dict(status='CHECK_REQUIRED',warning=str(exc))
                row['lastVerified']=None
            result['projects']+=self.hub.rows()
            for row in result['projects']:
                if row.get('originalId')=='stats-active':
                    row['projectType']=row['type']='stats'
                    row['capabilities']['openApplication']=True
                    row['capabilities']['openFolder']=False
            health=self.hub.status()
            result['hub']=dict(health,connectivity=health.get('libraryStatus','UNKNOWN'),lastRefreshUtc=health.get('catalogRefreshedUtc'))
            result['preferences']=self.hub.preferences()
            return result

        def state(self):
            with self.lock:
                result=super().state()
                result['activeSession']=dict(self.active) if self.active else None
                result['busy']=self.busy();result['canQuit']=not self.busy() and not self.stats.blocks_quit()
                result['statsSession']=dict(status=self.stats.status,revision=self.stats.revision)
                result['transferring']=self.transferring
                return result

        def preferences(self,body):
            with self.lock:
                g.need(not self.closing,'Interface is closing.')
                if set(body)=={'favorite','enabled'}:
                    g.need(type(body['enabled']) is bool,'Favorite state must be boolean.')
                    self.hub.set_favorite(body['favorite'],body['enabled'])
                elif set(body)=={'recent'}:self.hub.mark_recent(body['recent'])
                else:raise ValueError('Unexpected preference fields.')
                return self.hub.preferences()

        def refresh_hub(self,body):
            g.need(body=={} and not self.closing,'Refresh takes no parameters.')
            self.hub.refresh();return self.hub.status()

        def entries(self,project):
            if project in self.projects:return super().entries(project)
            self._row(project);return dict(entries=[],warnings=[],scanned=0)

        def audit(self,project):
            if project in self.projects:return super().audit(project)
            self._row(project);return dict(project=None,references=[],fixes=[],limitations=['Media audit applies to reviewed MAGIX families.'])

        def _workflow_acquire(self):
            if self.r.demo or self.workflow is not None:return
            path=g.ordinary(self.r.kit/'MAGIX-Workspace-State/.workflow.lock')
            context=self.engine.exclusive(path,create=False)
            context.__enter__();self.workflow=context
            try:
                for name in ('binding.json','multi-binding.json'):
                    g.need(g.read_json(self.r.kit/'MAGIX-Workspace-State'/name).get('status')=='UNBOUND','Finish the recorded MAGIX session before opening another editor.')
            except BaseException:
                self._workflow_release();raise

        def _workflow_release(self):
            if self.workflow is not None:
                self.workflow.__exit__(None,None,None);self.workflow=None

        def _schedule(self,action,project,callback,allow_active=False):
            with self.lock:
                g.need(not self.closing and not self._operation_running() and not self.transferring,'Another operation is running.')
                if self.active:
                    g.need(allow_active and self.active['kind'].startswith('mw5') and self.active.get('projectId') in (None,project),'Finish the current managed session first.')
                else:g.need(not super().busy(),'Finish the current operation first.')
                self.revision+=1;self.transferring=True
                self.job=dict(id=uuid.uuid4().hex,action=action,projectId=project,status='STARTING',startedUtc=g.utc(),finishedUtc=None,exitCode=None,awaitingDone=False,promptGeneration=0,demo=self.r.demo)
                self.output.clear()
                try:self.record()
                except BaseException:
                    self.transferring=False;self.job.update(status='FAILED_NO_PROCESS',exitCode=2);raise
                job=dict(self.job)
                def work():
                    try:
                        with self.lock:self.job['status']='RUNNING';self.report()
                        result=callback()
                        with self.lock:
                            if result is not None:self.job['result']=result
                        self.finish(0,'DEMO_COMPLETE' if self.r.demo else 'COMPLETED')
                    except BaseException as exc:
                        self.finish(2,'BLOCKED',str(exc))
                    finally:
                        with self.lock:self.transferring=False;self.cache_at=0
                threading.Thread(target=work,daemon=False).start()
                return job

        def start(self,action,project=None,entry=None,confirmation=None,expected_revision=None):
            g.need(isinstance(action,str) and (project is None or isinstance(project,str)) and (entry is None or isinstance(entry,str)) and (confirmation is None or isinstance(confirmation,str)),'Action, project, entry and confirmation must be text.')
            if action=='recover' or project in self.projects:
                with self.lock:g.need(not self.active and not self.transferring,'Finish the active managed editor session first.')
                return super().start(action,project,entry,confirmation,expected_revision)
            row=self._row(project)
            g.need(row.get('originalId')!='stats-active','Use the independent Stats controls for this project.')
            g.need(action in ('fetch','folder','open','save','verify','release'),'Unsupported project action.')
            if action=='release':g.need(expected_revision==self.revision,'Review the current project and confirm release again.')
            if action=='open':
                g.need(row.get('projectType',row.get('type'))=='mw5','This project has no reviewed application adapter. Open its working folder.')
                return self._schedule('open',project,lambda:self._open_mw5(project),allow_active=True)
            if action=='folder':
                return self._schedule('folder',project,lambda:self._folder_session(project))
            return self._schedule(action,project,lambda:self._client_action(action,project,confirmation))

        def _client_action(self,action,project,confirmation):
            args=self.hub.arguments(action,project,confirmation)
            if self.r.demo:
                self.append('DEMO: '+action+' uses synthetic files only.\n');time.sleep(.1)
                return dict(status='DEMO_COMPLETE',action=action)
            self._workflow_acquire()
            try:
                output=self._client_command(args)
                if action=='save':
                    self.append('Save committed. Verifying the saved version…\n')
                    output=self._client_command(self.hub.arguments('verify',project,None))
                try:self.hub.mark_recent(project)
                except (ValueError,OSError) as exc:self.job['reportingWarning']='Recent-project preference was not recorded: '+str(exc)
                self.hub.invalidate();self.hub.refresh()
                return output
            finally:self._workflow_release()

        def _client_command(self,args):
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            process=self.factory(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',cwd=str(self.r.kit),shell=False,creationflags=flags)
            self.child=process;lines=[];count=0;overflow=False;read_error=None
            def drain():
                for line in process.stderr:self.append(line)
            reader=threading.Thread(target=drain,daemon=True);reader.start()
            try:
                while True:
                    chunk=process.stdout.read(8192)
                    if not chunk:break
                    count+=len(chunk)
                    if count<=8*1024*1024:lines.append(chunk)
                    else:overflow=True
            except BaseException as exc:read_error=exc
            # A reporting failure never releases the workflow lock early. The
            # accepted child retains its cache lock until actual confirmed exit.
            code=self._wait_confirmed(process);reader.join(timeout=5)
            if read_error:raise read_error
            g.need(not overflow,'Library result exceeded its bounded response; operation exit was confirmed.')
            result=json.loads(''.join(lines));g.need(code==0,result.get('error','Library operation was blocked.'))
            return result

        def _folder_session(self,project):
            folder=None if self.r.demo else self.hub.folder_path(project)
            self._workflow_acquire()
            self.session_done.clear()
            launched=False
            try:
                with ExitStack() as stack:
                    if not self.r.demo:
                        pid=self.hub.original_id(project)
                        stack.enter_context(self.engine.exclusive(g.ordinary(self.r.cache/pid/'.client.lock'),create=False))
                        # Revalidate after obtaining the original client lock;
                        # the earlier UI readiness observation may be stale.
                        folder=self.hub.folder_path(project)
                    g.write_json(self.folder_journal,dict(format='interlinked-folder-session-v1',status='ACTIVE',projectId=project,operationId=self.job['id']))
                    if folder is not None:
                        subprocess.Popen([str(Path(os.environ['SystemRoot'])/'explorer.exe'),str(folder)],shell=False)
                    launched=True
                    with self.lock:
                        self.active=dict(kind='folder',projectId=project,nextAction='Close editors and terminals using this working copy, then confirm Finish session.')
                        self.job.update(status='WAITING_FOR_APP',awaitingDone=True,promptGeneration=1);self.report()
                    try:self.hub.mark_recent(project)
                    except (ValueError,OSError) as exc:self.job['reportingWarning']='Recent-project preference was not recorded: '+str(exc)
                    self.append('Working copy protected. Close all writers before confirming Finish session. Save-back is separate.\n')
                    self.session_done.wait()
                    g.write_json(self.folder_journal,dict(format='interlinked-folder-session-v1',status='IDLE'))
                    self.active=None
                return dict(status='SESSION_FINISHED',automaticSave=False)
            finally:
                if not launched:
                    self.active=None
                    if self.folder_journal.exists():g.write_json(self.folder_journal,dict(format='interlinked-folder-session-v1',status='IDLE'))
                if self.active is None:self._workflow_release()

        def session_input(self,value,operation_id,prompt_generation):
            g.need(not getattr(getattr(self,'buddy',None),'pending',None),'Wait for the current asset delivery to finish.')
            if self.active and self.active['kind']=='folder-recovery':
                with self.lock:
                    g.need(value=='DONE' and self.job['id']==operation_id and prompt_generation==1,'Stale or invalid recovered-session confirmation.')
                    if not self.r.demo:self.hub.folder_path(self.active['projectId'])
                    g.write_json(self.folder_journal,dict(format='interlinked-folder-session-v1',status='IDLE'))
                    self.active=None;self.finish(0,'COMPLETED')
                    if self.folder_recovery_guard:
                        self.folder_recovery_guard.__exit__(None,None,None);self.folder_recovery_guard=None
                    self._workflow_release()
                    return dict(status='RECOVERED_SESSION_FINISHED',automaticSave=False,automaticRelease=False)
            if self.active and self.active['kind']=='folder':
                with self.lock:
                    g.need(value=='DONE' and self.job['id']==operation_id and self.job.get('promptGeneration')==prompt_generation and self.job.get('awaitingDone'),'Stale or invalid session confirmation.')
                    self.job['awaitingDone']=False;self.job['status']='RUNNING';self.session_done.set()
                    return dict(status='SESSION_FINISH_REQUESTED',automaticSave=False,automaticRelease=False)
            if self.active and self.active['kind'].startswith('mw5'):
                raise ValueError('Close the MW5 editor, preview and collect its changes, then finish the MW5 session.')
            return super().session_input(value,operation_id,prompt_generation)

        def _ensure_mw5(self,project):
            row=self._row(project)
            g.need(row.get('projectType',row.get('type'))=='mw5','Choose the reviewed MW5 project.')
            if self.mw5 is None:
                if self.mw5_factory:self.mw5=self.mw5_factory(project)
                elif self.r.demo:self.mw5=SyntheticMW5()
                else:
                    pid=self.hub.original_id(project)
                    g.need(pid=='mw5-companion','Only the reviewed Lancemate Companion project is supported.')
                    self.hub.folder_path(project) # verifies cache ownership before adapter discovery
                    files=g.ordinary(self.r.cache/pid/'Files')
                    reviewed=self.r.state/'MW5-Editor-Reviewed.json'
                    editor=mw5_module.discover_editor(engine=self.engine,reviewed_config_path=reviewed if reviewed.exists() else None)
                    sources=mw5_module.find_plugin_sources(files,self.engine)
                    # Every adapter acquires workflow before cache. Reopening an
                    # outstanding journal may acquire the cache in its constructor.
                    held=self.workflow is not None
                    self._workflow_acquire()
                    try:
                        candidate=mw5_module.MW5Workspace(self.engine,self.mw5_root,files,sources,editor,cache_guard=self.mw5_recovery_guard)
                        self.mw5=candidate
                        self.mw5_recovery_guard=None
                        if candidate.state().get('status')=='IDLE' and not held:self._workflow_release()
                    except BaseException:
                        if self.mw5 is None and not held:self._workflow_release()
                        raise
                self.mw5_project=project;self.mw5_error=None
            g.need(self.mw5_project==project,'Another project owns the MW5 session.')
            return self.mw5

        def mw5_view(self,project):
            row=self._row(project)
            g.need(row.get('projectType',row.get('type'))=='mw5','Choose the reviewed MW5 project.')
            g.need(self.mw5_project in (None,project),'Another project owns the MW5 session.')
            if self.mw5 is None:
                return dict(ready=False,editor=dict(installed=False),reason=self.mw5_error or 'Check editor to discover its installation and the managed plugins.',session=dict(status='NOT_CHECKED',canOpen=False,canCollect=False),canStage=False,canCollect=False,preview=self.last_preview,busy=self.transferring)
            state=self.mw5.state();status=state.get('status','UNKNOWN')
            editor=getattr(self.mw5,'editor',{})
            return dict(ready=True,editor=dict(installed=True,path=editor.get('executable'),source=editor.get('discoverySource')),session=dict(status=status,canOpen=status=='STAGED',canCollect=status in ('STAGED','EDITING'),canFinish=status=='COLLECTED'),canStage=status=='IDLE',canCollect=status in ('STAGED','EDITING'),preview=self.last_preview,busy=self.transferring,error=self.mw5_error)

        def mw5_request(self,kind,body):
            project=body.get('project');self._row(project)
            if kind=='check':
                g.need(set(body)=={'project'},'Unexpected editor-check fields.')
                return self._schedule('mw5-check',project,lambda:self._check_mw5(project),allow_active=True)
            if kind=='finish':
                g.need(set(body)=={'project'},'Unexpected MW5 finish fields.')
                return self._schedule('mw5-finish',project,lambda:self._finish_mw5(project),allow_active=True)
            if kind=='preview':
                g.need(set(body)=={'project','direction'} and body['direction'] in ('stage','collect'),'Choose staging or collection.')
                return self._schedule('mw5-preview',project,lambda:self._preview_mw5(project,body['direction']),allow_active=True)
            g.need(set(body)=={'project','previewId','confirmation'} and body['confirmation'] in ('STAGE','COLLECT'),'Unexpected transfer confirmation.')
            g.need(self.last_preview and body['previewId']==self.last_preview['id'] and body['confirmation']==self.last_preview['direction'].upper(),'Review the current transfer preview before confirming.')
            return self._schedule('mw5-apply',project,lambda:self._apply_mw5(project,body['previewId'],body['confirmation'].lower()),allow_active=True)

        def _check_mw5(self,project):
            try:
                adapter=self._ensure_mw5(project)
                state=adapter.state()
                if state.get('status')!='IDLE':
                    self._workflow_acquire()
                    next_action='Close MW5, preview and collect changes, then finish the session.'
                    if state.get('status')=='COLLECTED':next_action='Collection completed before the interruption. Confirm Finish MW5 session; save-back remains separate.'
                    if state.get('status')=='APPLYING':
                        preview=adapter.resume_preview()
                        g.need(preview is not None,'Interrupted MW5 transfer preview is unavailable; retain its journal for review.')
                        preview=dict(preview);preview.setdefault('id',preview.get('previewId'))
                        self.last_preview=preview
                        next_action='Review and resume the exact interrupted '+preview['direction']+' transfer.'
                    self.active=dict(kind='mw5',projectId=project,nextAction=next_action)
                elif self.active and self.active['kind']=='mw5-recovery':
                    self.active=None;self._workflow_release()
                return adapter.readiness()
            except BaseException as exc:
                self.mw5_error=str(exc);raise

        def _finish_mw5(self,project):
            adapter=self._ensure_mw5(project)
            g.need(adapter.state().get('status')=='COLLECTED','Collect plugin changes before finishing the MW5 session.')
            result=adapter.finish_session()
            self.active=None;self.last_preview=None;self._workflow_release()
            try:self.hub.mark_recent(project)
            except (ValueError,OSError) as exc:self.job['reportingWarning']='Recent-project preference was not recorded: '+str(exc)
            return result

        def _preview_mw5(self,project,direction):
            adapter=self._ensure_mw5(project)
            preview=adapter.preview_stage() if direction=='stage' else adapter.preview_collect()
            preview=dict(preview);preview.setdefault('id',preview.get('previewId'));preview['direction']=direction
            self.last_preview=preview;return preview

        def _apply_mw5(self,project,preview_id,direction):
            adapter=self._ensure_mw5(project)
            self._workflow_acquire()
            try:
                result=adapter.apply_stage(preview_id) if direction=='stage' else adapter.apply_collect(preview_id)
                if direction=='stage':
                    self.active=dict(kind='mw5',projectId=project,nextAction='Open the MW5 editor. After editing, close it and preview collecting changes.')
                else:
                    adapter.finish_session();self.active=None;self._workflow_release()
                    try:self.hub.mark_recent(project)
                    except (ValueError,OSError) as exc:self.job['reportingWarning']='Recent-project preference was not recorded: '+str(exc)
                    self.hub.invalidate();self.hub.refresh()
                self.last_preview=None
                return result
            except BaseException:
                status=adapter.state().get('status')
                if status!='IDLE':
                    self.active=dict(kind='mw5',projectId=project,nextAction='Collection completed. Confirm Finish MW5 session; save-back remains separate.' if status=='COLLECTED' else 'Transfer stopped. Review its journal and preview collection; work remains protected.')
                else:self._workflow_release()
                raise

        def _open_mw5(self,project):
            adapter=self._ensure_mw5(project)
            g.need(self.active and self.active['kind']=='mw5','Preview and apply plugin staging before opening MW5.')
            result=adapter.start_session()
            self.active['nextAction']='Save and close MW5, then preview and collect plugin changes. Save to MSI follows collection.'
            try:self.hub.mark_recent(project)
            except (ValueError,OSError) as exc:self.job['reportingWarning']='Recent-project preference was not recorded: '+str(exc)
            return result

        def request_quit(self):
            with self.lock:
                g.need(not self.stats.blocks_quit(),'Stop Stats or reconcile its interrupted session before exiting.')
                g.need(not self.busy(),'A managed operation or editor session is active. Finish it before exiting.')
                self.closing=True

        def close(self):
            self.request_quit();self.hub.close();self._workflow_release();super().close()

    return HubController


class SyntheticMW5:
    """UI demo only; real transfer logic is tested in separate file fixtures."""
    def __init__(self):self.status='IDLE';self.previews={}
    def state(self):return dict(status=self.status,demo=True)
    def readiness(self):return dict(ready=True,demo=True)
    def _preview(self,direction):
        value=dict(id=uuid.uuid4().hex,direction=direction,changes=[dict(path='LanceObserverProbe/Content/Synthetic.uasset',action='copy',bytes=64)],conflicts=[],warnings=['DEMO: no editor or project files are accessed.'],applicable=True)
        self.previews[value['id']]=value;return value
    def preview_stage(self):return self._preview('stage')
    def preview_collect(self):return self._preview('collect')
    def apply_stage(self,key):
        if self.previews[key]['direction']!='stage':raise ValueError('Wrong preview direction.')
        self.status='STAGED';return self.state()
    def start_session(self):
        if self.status!='STAGED':raise ValueError('Stage plugins first.')
        self.status='EDITING';return self.state()
    def apply_collect(self,key):
        if self.previews[key]['direction']!='collect':raise ValueError('Wrong preview direction.')
        self.status='COLLECTED';return self.state()
    def finish_session(self):
        if self.status!='COLLECTED':raise ValueError('Collect changes first.')
        self.status='IDLE';return self.state()
