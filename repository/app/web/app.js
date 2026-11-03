'use strict';
const $=id=>document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('interlinked-token')||'';
if(token){sessionStorage.setItem('interlinked-token',token);history.replaceState(null,'',location.pathname+location.search);}
let projects=[], selected=null, state={busy:false,revision:0}, filter='all', auditLoaded=null, releaseTarget=null, lastCompleted=null, stopped=false;
let mutation=false,pollSequence=0,appliedPoll=0,entrySequence=0,auditSequence=0;
let pollPending=null,pollAgain=false,pollTimer=null,catalogPending=null,catalogAgain=false;
const entryRefreshTimes=new Map();
let requestedAction=null;
let statsSnapshot=null,statsSequence=0;
let missingSequence=0,notePending=false,missingRecords=[];
let hub={},preferences={favorites:[],recent:[]},mw5Readiness=null,mw5Preview=null,mw5Sequence=0,lastCatalogRefresh=0;
const chosenEntries=new Map();
const initialProject=new URLSearchParams(location.search).get('project');
const human=value=>String(value||'Not audited').replaceAll('_',' ').toLowerCase().replace(/\b\w/g,c=>c.toUpperCase());
const size=bytes=>bytes>=1024**3?(bytes/1024**3).toFixed(2)+' GiB':bytes>=1024**2?(bytes/1024**2).toFixed(1)+' MiB':bytes.toLocaleString()+' B';
function showError(error){$('error').textContent=String(error.message||error);$('error').hidden=false;}
function clearError(){$('error').hidden=true;}
async function api(path,body){
 const options={headers:{'X-INTERLINKED-Token':token}};
 if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options),value=await response.json();
 if(!response.ok){const error=new Error(value.error||'The local service rejected this request.');error.status=response.status;error.payload=value;throw error;}
 return value;
}
function safe(fn){return async(...args)=>{clearError();try{await fn(...args);}catch(error){showError(error);}};}
async function mutate(fn,request=null){if(mutation)throw new Error('Please wait for the current request.');mutation=true;requestedAction=request;renderState();try{return await fn();}finally{mutation=false;requestedAction=null;renderState();}}
// BEGIN PURE OPEN FEEDBACK
function terminalCliError(output){
 const text=String(output||'');
 const candidates=text.split(/\r?\n/).map(line=>line.trim()).reverse();
 // The pinned CLI normally prints one JSON line. Also accept its indented
 // terminal object, without executing output or trusting a success status.
 for(let at=text.lastIndexOf('{');at>=0;at=text.lastIndexOf('{',at-1)){
  candidates.push(text.slice(at).trim());if(at===0)break;
 }
 for(const candidate of candidates){
  if(!candidate.startsWith('{'))continue;
  try{const value=JSON.parse(candidate);if(['BLOCKED_OR_INTERRUPTED','BLOCKED','FAILED'].includes(value.status)&&typeof value.error==='string')return value.error;}catch(_){/* Output is data, not code. */}
 }
 return '';
}
function openFeedbackModel(snapshot,project,request){
 if(!project)return null;
 if(request==='open')return {tone:'pending',title:'Starting your request…',text:'Waiting for the local launcher. Keep this interface service open.',progress:''};
 const job=snapshot.operation;
 if(!job||job.action!=='open'||job.projectId!==project.id)return null;
 if(snapshot.busy){
  const lines=String(snapshot.output||'').split(/[\r\n]+/).map(line=>line.trim());
  const progress=lines.filter(line=>/^(Fetch preview:|Fetching:|Copying:|Verifying:)/i.test(line)).pop()||'';
  return {tone:'pending',title:job.awaitingDone?'Working copy protected — confirmation stays in Activity':'Preparing your local working copy…',text:job.awaitingDone?'After MAGIX opens, save your work and close all its windows. Then confirm Done in Activity.':'The selected family may need fetching and verification before MAGIX opens. This can take time; do not start another copy.',progress:progress.length>220?progress.slice(0,217)+'…':progress};
 }
 if(['BLOCKED','FAILED','FAILED_NO_PROCESS'].includes(job.status)&&Number.isInteger(job.exitCode)&&job.exitCode!==0){
  const reason=job.error||terminalCliError(snapshot.output)||'The launcher stopped before completing this request. Open Activity details for its result.';
  const heldLock=/\.workflow\.lock/i.test(reason)&&/(used by another|in use|sharing violation|winerror 32)/i.test(reason);
  const text=heldLock?'Another MAGIX launcher may still hold the workspace lock. Save and close MAGIX, then type DONE in that original console. After it finishes, retry Prepare & open.':reason;
  return {tone:'blocked',title:'Prepare & open did not complete',text,progress:''};
 }
 if(job.status==='COMPLETED'&&job.exitCode===0)return {tone:'complete',title:'Launcher finished',text:'Local work was not automatically saved to MSI or freed. Use Save to MSI + verify when you are ready.',progress:''};
 return null;
}
let lastOpenFeedbackSignature='';
function renderOpenFeedback(){
 const model=openFeedbackModel(state,selected,requestedAction);
 const signature=JSON.stringify({project:selected?.id,operation:state.operation?.id,model});
 if(signature===lastOpenFeedbackSignature)return;
 lastOpenFeedbackSignature=signature;
 $('open-feedback').hidden=!model;
 if(!model)return;
 $('open-feedback').className='open-feedback '+model.tone;
 $('open-feedback-title').textContent=model.title;$('open-feedback-text').textContent=model.text;
 $('open-feedback-progress').textContent=model.progress;$('open-feedback-progress').hidden=!model.progress;
 $('open-feedback-link').textContent=state.busy?'Go to Activity / session controls ↓':'View Activity details ↓';
}
// END PURE OPEN FEEDBACK
// BEGIN HUB MODELS
function projectType(p){return p?.type||p?.projectType||(p?.adapter==='library'?'other':'magix');}
function isMagix(p){return !!p&&p.adapter!=='library'&&projectType(p)==='magix';}
function isLocal(p){return ['READY','PENDING_SAVE','FETCHING','RELEASING','RELEASE_PARTIAL','DEMO'].includes(p?.cache?.status);}
function needsReview(p){const text=String(p.mediaStatus).toLowerCase();return (isMagix(p)&&!text.includes('covered')&&!text.includes('ready'))||(p.warnings||[]).length>0||!!p.cache?.warning;}
function recentIds(prefs){return (prefs?.recent||[]).map(row=>typeof row==='string'?row:row.id);}
function projectMatches(p,kind,search,prefs){
 const text=(p.name+' '+p.id+' '+(p.originalId||'')+' '+(p.description||'')).toLowerCase();
 if(search&&!text.includes(search.toLowerCase().trim()))return false;
 return kind==='all'||kind==='local'&&isLocal(p)||kind==='review'&&needsReview(p)||kind==='favorites'&&((prefs?.favorites||[]).includes(p.id)||p.favorite)||kind==='recent'&&(recentIds(prefs).includes(p.id)||p.recentUtc)||['magix','mw5','coding_data'].includes(kind)&&projectType(p)===kind||kind==='coding_data'&&projectType(p)==='stats';
}
function hasCapability(p,key){
 if(!p)return false;
 if(p.capabilities)return p.capabilities[key]===true;
 return isMagix(p)&&['openApplication','finish','save','verify','release'].includes(key);
}
function canSelectProject(p,snapshot,mutating){
 if(!p||mutating||snapshot.closing)return false;
 if(!snapshot.busy||p.originalId==='stats-active')return true;
 const active=snapshot.activeSession;
 return active?.projectId===p.id||active?.kind==='mw5-recovery'&&!active.projectId&&projectType(p)==='mw5';
}
function releaseConfirmation(p){return p?.originalId||p?.id||'';}
function evidenceText(evidence,label){
 if(!evidence)return 'No '+label+' evidence recorded';
 if(typeof evidence==='number'&&Number.isFinite(evidence))return new Date(evidence*1000).toISOString()+' · saved snapshot; current files are not certified';
 if(typeof evidence==='string')return evidence;
 const when=evidence.utc||evidence.savedUtc||evidence.timestamp;
 const version=evidence.versionId?String(evidence.versionId).slice(0,12):'';
 const scope=evidence.scope||'';
 return [when||'Time not recorded',version?'version '+version:'',scope,evidence.currentBaseline===false?'Previous baseline; current files are not certified':''].filter(Boolean).join(' · ');
}
function transferCanApply(preview){return !!preview&&preview.applicable===true&&!(preview.conflicts||[]).length&&typeof preview.id==='string'&&['stage','collect'].includes(preview.direction);}

function statsControls(info,snapshot){
 const changing=!!info?.operation&&['STARTING','RUNNING'].includes(info.operation.status)&&!info.running;
 const idle=!!info&&!info.running&&info.status!=='RECOVERY_REQUIRED'&&!changing;
 return {start:idle&&info.ready&&!snapshot.transferring,'open-browser':info?.status==='RUNNING',stop:['STARTING','RUNNING'].includes(info?.status),fetch:idle&&!snapshot.transferring,save:idle&&info.ready&&!snapshot.transferring,verify:idle&&info.ready&&!snapshot.transferring,release:idle&&info.ready&&!snapshot.transferring,acknowledge:info?.status==='RECOVERY_REQUIRED'&&!changing};
}
// END HUB MODELS
function renderList(){
 const search=$('search').value.toLowerCase().trim();
 const rows=projects.filter(p=>projectMatches(p,filter,search,preferences));
 if(filter==='recent'){const order=recentIds(preferences);rows.sort((a,b)=>{const ai=order.indexOf(a.id),bi=order.indexOf(b.id);return (ai<0?9999:ai)-(bi<0?9999:bi);});}
 $('project-count').textContent=projects.length;$('project-list').replaceChildren();
 for(const p of rows){
  const button=document.createElement('button');button.type='button';button.disabled=!canSelectProject(p,state,mutation);button.dataset.project=p.id;button.className='project-item'+(selected?.id===p.id?' active':'');button.setAttribute('aria-pressed',String(selected?.id===p.id));
  const name=document.createElement('strong');name.textContent=((preferences.favorites||[]).includes(p.id)||p.favorite?'★ ':'')+p.name;
  const sub=document.createElement('span');sub.className='subline';
  const bytes=document.createElement('span');bytes.textContent=size(p.bytes);
  const local=document.createElement('span');local.textContent=p.cache.status==='READY'?'● LOCAL':p.cache.status==='DEMO'?'PREVIEW':human(p.cache.status);
  sub.append(bytes,local);const entries=document.createElement('span');entries.className='entry-count';entries.textContent=isMagix(p)?(p.entryPoints||[]).length+' project / recovery entries':human(projectType(p))+' · '+(p.originalId||p.id);
  button.append(name,sub,entries);button.addEventListener('click',()=>select(p.id,true));$('project-list').append(button);
 }
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No projects match this view.';$('project-list').append(empty);}
}
function renderDetail(){
 if(!selected)return;
 const magix=isMagix(selected),mw5=projectType(selected)==='mw5',stats=selected.originalId==='stats-active';
 $('project-code').textContent=selected.originalId||selected.id;$('project-name').textContent=selected.name;
 $('project-kind').textContent=human(projectType(selected))+' · LOCAL FIRST · EXPLICIT SAVE';
 $('project-subtitle').textContent=selected.description||(selected.cache.status==='READY'?'Your local working copy is retained. Finish the editing session before save-back.':magix?'Prepare one family at a time. Keep its media local while you work.':'Fetch a managed working copy before opening this project.');
 $('size').textContent=size(selected.bytes);$('file-count').textContent=selected.files.toLocaleString()+' selected files';
 $('cache-state').textContent=selected.cache.status==='ABSENT'?'Not fetched':human(selected.cache.status);
 $('cache-note').textContent=selected.cache.warning||'Metadata status, not verification';
 $('media-health').textContent=magix?human(selected.mediaStatus):'Not a MAGIX audit';
 const count=selected.mediaCounts?.mediaReferenceCandidates??selected.mediaCounts?.referenceCandidates;
 $('audit-count').textContent=count!=null?count.toLocaleString()+' strings':'';
 $('audit-tab').hidden=!magix;$('missing-panel').hidden=!magix;$('entry-fields').hidden=!magix;$('mw5-panel').hidden=!mw5;
 if(!magix&&!$('audit-panel').hidden)setTab(false);
 $('stats-panel').hidden=!stats;
 $('workspace-kind').textContent=mw5?'MW5 MOD PLUGINS':magix?'LOCAL MEDIA':'MANAGED FILES';
 $('open').hidden=!hasCapability(selected,'openApplication')&&!mw5;
 $('open').firstElementChild.textContent=magix?'Prepare & open MAGIX':mw5?'Open MW5 Editor':'Open application';
 $('fetch').hidden=magix||!hasCapability(selected,'fetch');$('folder').hidden=magix||!hasCapability(selected,'openFolder');$('recover').hidden=!magix;
 $('work-footnote').textContent=magix?'Fetches only this family when needed. Existing READY edits and deletions are preserved.':'Files outside this managed working copy are outside save-back coverage. Existing edits and deletions are preserved.';
 $('save-guidance').textContent=mw5?'Save and close the editor, collect its plugin changes, then save a verified version to MSI.':'Save in your application, close all writers, and finish the session. Then save a verified version to MSI.';
 $('last-save').textContent=evidenceText(selected.lastSaved||selected.lastSave,'committed save');$('last-verify').textContent=evidenceText(selected.lastVerified||selected.lastVerification,'content verification');
 const favorite=(preferences.favorites||[]).includes(selected.id)||selected.favorite;
 $('favorite-toggle').textContent=favorite?'★ Favorite':'☆ Favorite';$('favorite-toggle').setAttribute('aria-pressed',String(!!favorite));
 const entry=chosenEntries.get(selected.id);$('entry').replaceChildren();
 for(const item of selected.entryPoints||[]){const option=document.createElement('option');option.value=item.path;const name=item.path.split('/').pop();option.textContent=(item.origin==='LOCAL_SAVE_AS'?'[LOCAL SAVE AS] ':item.kind==='recovery'?'[BACKUP] ':'[PROJECT] ')+name;option.title=item.path;$('entry').append(option);}
 if(entry&&selected.entryPoints.some(e=>e.path===entry))$('entry').value=entry;
 else if(entry){const missing=document.createElement('option');missing.value='';missing.textContent=selected.loadingEntries?'Checking your previous selection…':'Previous selection unavailable — choose another entry';missing.disabled=true;missing.selected=true;$('entry').prepend(missing);}
 else if($('entry').value)chosenEntries.set(selected.id,$('entry').value);
 $('warnings').replaceChildren();
 for(const warning of [...(selected.warnings||[]),...(selected.notes||[])]){const li=document.createElement('li');li.textContent=String(warning);$('warnings').append(li);}
 if(stats){$('save-guidance').textContent='Stop Stats before saving the managed library to MSI. Checkout code requires Git commit/push.';$('entry-note').textContent='Use the independent Stats controls below. The dashboard runs in your browser.';for(const id of ['open','fetch','folder','save','verify','release'])$(id).hidden=true;}else{for(const id of ['save','verify','release'])$(id).hidden=false;}
 updateEntry();renderState();
}
function select(id,record=false){const previous=selected?.id;selected=projects.find(p=>p.id===id)||projects[0];auditLoaded=null;auditSequence++;if(previous!==selected?.id){mw5Readiness=null;mw5Preview=null;mw5Sequence++;}if(selected)selected.loadingEntries=isMagix(selected);renderList();renderDetail();if(!selected)return;if(isMagix(selected)){loadEntries(selected.id).catch(error=>{if(selected?.id===id){selected.loadingEntries=false;renderState();}showError(error);});if(previous!==selected.id){$('missing-path').value='';$('missing-note').value='';}loadMissing().catch(missingError);if(!$('audit-panel').hidden)loadAudit().catch(showError);}if(selected.originalId==='stats-active')loadStats().catch(showError);if(projectType(selected)==='mw5')loadMw5().catch(showError);if(record)recordRecent(selected.id).catch(showError);if($('buddy-report-panel'))buddyLoadReport();}
async function loadEntries(id){entryRefreshTimes.set(id,Date.now());const sequence=++entrySequence,result=await api('/api/entries?project='+encodeURIComponent(id));if(selected?.id!==id||sequence!==entrySequence)return;selected.entryPoints=result.entries;selected.loadingEntries=false;renderDetail();if(result.warnings.length)$('entry-note').textContent+=' '+result.warnings.join(' ');}
function updateEntry(){if(!isMagix(selected)){$('entry-note').textContent=projectType(selected)==='mw5'?'Stage the reviewed mod plugins below before opening the editor. Collect changes after the editor closes.':'Open working folder starts a protected editing session. Close your editors and running tools before finishing it.';return;}const item=selected?.entryPoints.find(e=>e.path===$('entry').value);$('entry-note').textContent=item?.kind==='recovery'?'Backup selected: MAGIX opens, then choose File > Load backup project using the exact local path shown in Activity. The backup is not renamed.':'The reviewed launcher uses a short local path to the selected cached project. It does not stream media from MSI.';}
async function loadCatalog({refreshSelection=false}={}){
 if(catalogPending){if(refreshSelection)catalogAgain=true;return catalogPending;}
 catalogPending=(async()=>{
  let refresh=refreshSelection;
  do{
   catalogAgain=false;
   let catalog;try{catalog=await api('/api/catalog');}catch(error){if(!catalogAgain||stopped)throw error;refresh=true;continue;}if(stopped)return;const previous=selected;
   projects=catalog.projects;for(const project of projects)project.catalogEntryKey=JSON.stringify(project.entryPoints||[]);
   hub=catalog.hub||{};preferences=catalog.preferences||preferences;lastCatalogRefresh=Date.now();renderHub();
   $('demo-banner').hidden=!catalog.demo;$('mode').lastChild.textContent=catalog.demo?'DEMO · NO REAL FILES':'LOCAL CONTROL';
   $('snapshot-date').textContent='CATALOG '+String(catalog.indexDate||'snapshot').split('T')[0]+' · AUDIT '+(catalog.auditAvailable?'AVAILABLE':'NOT INSTALLED');
   const current=projects.find(project=>project.id===previous?.id);
   if(current&&!refresh&&current.catalogEntryKey===previous.catalogEntryKey&&current.cache?.status===previous.cache?.status){
    // Keep discovered Save As entries and in-flight selection reads across an
    // unchanged catalog. Actions still revalidate the selected entry server-side.
    current.entryPoints=previous.entryPoints;current.loadingEntries=previous.loadingEntries;selected=current;renderList();renderDetail();
    if(projectType(current)==='mw5')loadMw5().catch(showError);
   }else select(current?.id||initialProject||projects[0]?.id);
   refresh=catalogAgain;
  }while(catalogAgain&&!stopped);
 })().finally(()=>{catalogPending=null;});
 return catalogPending;
}
async function action(name){
 if(!selected&&name!=='recover')throw new Error('Choose a project first.');
 if(name==='recover'&&!confirm('Close all MAGIX windows first. Recover removes only the recorded local mappings; it does not save or free project files. Continue?'))return;
 await mutate(async()=>{await api('/api/action',{action:name,project:selected?.id,entry:name==='open'&&isMagix(selected)?$('entry').value:undefined,expectedRevision:state.revision});await poll();},name);
}
function renderState(){
 const job=state.operation,busy=state.busy,locked=busy||mutation||state.closing;
 for(const id of ['open','save','verify','release','recover','fetch','folder'])$(id).disabled=locked||!selected;
 $('quit').disabled=locked||notePending||state.canQuit===false;$('entry').disabled=locked||selected?.loadingEntries;
 for(const button of document.querySelectorAll('.project-item'))button.disabled=!canSelectProject(projects.find(p=>p.id===button.dataset.project),state,mutation);
 if(selected&&!locked){$('open').disabled=!hasCapability(selected,'openApplication')||(isMagix(selected)&&(selected.loadingEntries||!$('entry').value));$('fetch').disabled=!hasCapability(selected,'fetch');$('folder').disabled=!hasCapability(selected,'openFolder');$('save').disabled=!hasCapability(selected,'save')||!['READY','PENDING_SAVE','DEMO'].includes(selected.cache.status);$('verify').disabled=!hasCapability(selected,'verify');$('release').disabled=!hasCapability(selected,'release')||!['READY','DEMO','RELEASING'].includes(selected.cache.status);}
 if(state.activeSession?.kind==='mw5'&&state.activeSession.projectId===selected?.id)$('open').disabled=mutation||state.transferring||state.closing||mw5Readiness?.busy||mw5Readiness?.session?.canOpen!==true;
 $('favorite-toggle').disabled=locked||!selected||preferences.preferencesAvailable===false;
 document.querySelector('.activity').classList.toggle('busy',busy);
 $('operation-status').textContent=job?human(job.status):'Ready when you are';
 const runningName=projects.find(p=>p.id===job?.projectId)?.name||job?.projectId||'owned mappings';
 $('operation-description').textContent=job?(human(job.action)+' · '+runningName+(job.error?' · '+job.error:'')):'Actions run locally. Nothing is saved or freed without your request.';
 const sessionProject=projects.find(p=>p.id===job?.projectId),magixSession=isMagix(sessionProject)||!sessionProject&&job?.adapter!=='library';
 $('session-controls').hidden=state.activeSession?.kind==='mw5'||!(busy&&['open','folder'].includes(job?.action));
 $('done').disabled=mutation||!job?.awaitingDone;$('cancel-startup').disabled=mutation||!job?.awaitingDone;
 $('release-confirm').disabled=mutation||$('release-id').value!==releaseTarget?.confirmation;
 $('done').textContent=job?.awaitingDone?(magixSession?'I saved and closed MAGIX':'I saved and closed my application'):'Waiting for the launcher…';
 $('cancel-startup').hidden=!magixSession;$('session-guidance').textContent=magixSession?'Keep this interface running while editing. Save in MAGIX and close all its windows before confirming.':'Keep this interface running while editing. Save your files and close all editors and running tools before finishing. MW5 changes must then be collected before save-back.';
 renderSessionBanner();renderMw5Controls();renderStatsControls();
 const log=$('operation-log'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<35;log.textContent=state.output||'No operation output yet.';if(atEnd)log.scrollTop=log.scrollHeight;
 $('log-warning').hidden=!job?.reportingWarning;$('log-warning').textContent=job?.reportingWarning||'';
 $('recovery-note').hidden=!state.previousIncomplete?.length;
 $('recovery-note').textContent='A previous service run ended with an unfinished operation. Do not assume it stopped. Close MAGIX and check the prior launcher before using Recover. No files were automatically released.';
 if(isMagix(selected))renderOpenFeedback();else $('open-feedback').hidden=true;
}
async function pollOnce(){
 if(stopped)return;
 const sequence=++pollSequence,value=await api('/api/state');if(stopped||sequence<appliedPoll)return;appliedPoll=sequence;state=value;renderState();
 if(state.operation&&!state.transferring&&!['STARTING','RUNNING','WAITING_FOR_MAGIX'].includes(state.operation.status)&&state.operation.id!==lastCompleted){const completed=state.operation.id;await loadCatalog({refreshSelection:true});lastCompleted=completed;}
 else if(Date.now()-lastCatalogRefresh>8000&&!mutation){await loadCatalog();}
 if(isMagix(selected)&&!document.hidden&&!selected.loadingEntries&&Date.now()-(entryRefreshTimes.get(selected.id)||0)>30000){const id=selected.id;try{await loadEntries(id);}catch(error){if(selected?.id===id)selected.loadingEntries=false;throw error;}}
 if(selected?.originalId==='stats-active')await loadStats();
 if($('buddy-report-panel')&&!document.hidden&&Date.now()-buddyReportRefreshed>5000)await buddyLoadReport();
}
async function poll({background=false}={}){
 if(stopped||background&&document.hidden)return;
 if(pollPending){if(!background)pollAgain=true;return pollPending;}
 pollPending=(async()=>{do{pollAgain=false;try{await pollOnce();}catch(error){if(!pollAgain||stopped)throw error;}}while(pollAgain&&!stopped);})().finally(()=>{pollPending=null;});
 return pollPending;
}
function pollError(error){if(!stopped)showError('Local interface connection lost. Closing a tab does not stop a running launcher. Keep its service window open; use the original launcher Recover only after closing MAGIX. '+error.message);}
function schedulePoll(){
 clearTimeout(pollTimer);pollTimer=null;if(stopped||document.hidden)return;
 pollTimer=setTimeout(async()=>{pollTimer=null;try{await poll({background:true});}catch(error){pollError(error);}finally{schedulePoll();}},1200);
}
async function sendResponse(response){
 const job=state.operation;if(!job)throw new Error('No active session.');
 if(response==='CANCEL STARTUP'&&!confirm('Use this only if MAGIX never appeared. Dismiss any pending Steam or launch dialogs first. This keeps all local files. Continue?'))return;
 await mutate(async()=>{await api('/api/session',{response,operationId:job.id,promptGeneration:job.promptGeneration});await poll();});
}
async function setTab(audit){
 $('working-panel').hidden=audit;$('audit-panel').hidden=!audit;
 $('workspace-tab').classList.toggle('active',!audit);$('audit-tab').classList.toggle('active',audit);
 $('workspace-tab').setAttribute('aria-selected',String(!audit));$('audit-tab').setAttribute('aria-selected',String(audit));
 if(audit)await loadAudit();
}
function asText(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
async function loadAudit(){
 if(!selected||auditLoaded===selected.id)return;
 const id=selected.id,sequence=++auditSequence;$('audit-summary').textContent='Loading the reviewed reference scan…';$('audit-references').replaceChildren();$('audit-fixes').replaceChildren();
 const audit=await api('/api/audit?project='+encodeURIComponent(id));if(selected.id!==id||sequence!==auditSequence)return;
 auditLoaded=id;
 const explanation=audit.project?.summary||'This is a historical reference scan, not a playback test. Exact selected files can be available while historical references remain unresolved.';
 $('audit-summary').textContent=asText(explanation)+(audit.limitations?.length?' '+audit.limitations.map(asText).join(' '):'');
 $('audit-references').replaceChildren();
 for(const ref of audit.references){
  const details=document.createElement('details');details.className='reference';details.dataset.search=JSON.stringify(ref).toLowerCase();
  const summary=document.createElement('summary'),name=document.createElement('span'),tag=document.createElement('span');
  name.textContent=ref.name||ref.value||ref.path||ref.id||'Reference';tag.className='classification';tag.textContent=human(ref.classification||ref.status||'Review');summary.append(name,tag);
  const reason=document.createElement('p');reason.textContent=ref.reason||ref.explanation||'Review the preserved path and candidate evidence before changing a project.';
  const expected=document.createElement('p');expected.textContent='Expected path: '+(ref.path||ref.value||'Not recorded');
  details.append(summary,expected,reason);
  const flag=document.createElement('button');flag.type='button';flag.className='quiet small';flag.textContent='Flag missing';flag.dataset.missingAction='true';flag.addEventListener('click',()=>flagAudit(id,ref.id).catch(missingError));details.append(flag);
  for(const candidate of ref.candidatePaths||[]){const p=document.createElement('p');p.textContent='Unverified candidate: '+(candidate.cacheRelativePath||candidate.sourcePath||'Path unavailable')+' — same name only; no substitution applied. Local presence must be checked.';details.append(p);}
  const evidence=document.createElement('details'),label=document.createElement('summary'),pre=document.createElement('pre');label.textContent='Technical evidence';pre.textContent=JSON.stringify(ref,null,2);evidence.append(label,pre);details.append(evidence);$('audit-references').append(details);
 }
 if(!audit.references.length){const p=document.createElement('p');p.className='muted';p.textContent='No media-reference rows are available in this audit view. That does not establish complete dependencies.';$('audit-references').append(p);}
 $('audit-fixes').replaceChildren();
 for(const fix of audit.fixes){const box=document.createElement('details'),title=document.createElement('summary'),p=document.createElement('p'),pre=document.createElement('pre');title.textContent=human(fix.type||'Manual candidate review');p.textContent=fix.reason||'Review identity before making any change. This proposal is not applied automatically.';pre.textContent=JSON.stringify(fix,null,2);box.append(title,p,pre);$('audit-fixes').append(box);}
 if(!audit.fixes.length)$('audit-fixes').textContent='No new automatic repair is proposed. Existing verified local-path mappings remain in place during the selected session. Uncertain candidates require review.';
 filterReferences();
}
function filterReferences(){const query=$('reference-search').value.trim().toLowerCase();for(const row of document.querySelectorAll('.reference'))row.hidden=query&&!row.dataset.search.includes(query);}
// BEGIN HUB INTERFACE
function renderHub(){
 const connection=hub.connectivity,library=typeof connection==='string'?connection:connection?.library||hub.libraryStatus||'UNKNOWN';
 const returns=typeof connection==='object'?connection?.returns:hub.returnsStatus;
 $('hub-connection').textContent=human(library)+(returns?' · Returns '+human(returns):'');
 const refreshed=hub.lastRefreshUtc||hub.catalogRefreshedUtc;
 $('hub-refresh-time').textContent=(hub.refreshing?'Refreshing… · ':'')+(refreshed?'Catalog checked '+refreshed:'No successful catalog refresh yet')+(hub.catalogStale?' · cached snapshot':'');
 if(hub.warning)$('hub-refresh-time').textContent+=' · '+hub.warning;
 const storage=hub.storage||{},local=storage.localFreeBytes??storage.local?.freeBytes,remote=storage.msiFreeBytes??storage.returnsFreeBytes??storage.msi?.freeBytes;
 $('hub-storage').textContent='Local '+(Number.isFinite(local)?size(local):'unknown')+' · MSI '+(Number.isFinite(remote)?size(remote):'unknown');
 $('hub-refresh').disabled=!!hub.refreshing||mutation||state.closing;
 renderPreferencesAvailability();
}
function renderSessionBanner(){
 const job=state.operation,active=state.busy,session=state.activeSession,project=projects.find(p=>p.id===(session?.projectId||job?.projectId));
 $('session-banner').classList.toggle('busy',!!active);$('session-project').hidden=!active||!project;
 $('session-done').hidden=session?.kind==='mw5'||!(active&&job?.awaitingDone&&['open','folder'].includes(job?.action));$('session-done').disabled=mutation||!job?.awaitingDone;
 $('session-title').textContent=active?(project?.name||job?.projectId||'Managed operation'):'No editing session active';
 $('session-next').textContent=session?.nextAction|| (active?(job?.awaitingDone?(isMagix(project)?'Next: save and close MAGIX, then finish the session.':'Next: save your work, close the application and its tools, then finish the session.'):'Next: wait for '+human(job?.action||'the current operation')+' to finish. Your working copy stays protected.'):(projectType(selected)==='mw5'?'Next: check readiness, stage the plugins, and open the MW5 Editor.':'Next: choose a project and prepare its managed working copy.'));
}
function adoptPreferences(result){const value=result.preferences||result;if(Array.isArray(value.favorites)&&Array.isArray(value.recent))preferences=value;renderPreferencesAvailability();}
function renderPreferencesAvailability(){const notice=$('library-preferences-notice');if(notice){notice.hidden=preferences.preferencesAvailable!==false;notice.textContent='Library favorites and recent projects are unavailable. Open Settings history to review recovery options. Project workflows remain available.';}}
async function recordRecent(id){if(preferences.preferencesAvailable===false)return;adoptPreferences(await api('/api/preferences',{recent:id}));renderList();}
async function toggleFavorite(){
 if(!selected)return;if(preferences.preferencesAvailable===false)throw new Error('Recover library preferences in Settings history before changing favorites.');const id=selected.id,enabled=!((preferences.favorites||[]).includes(id)||selected.favorite);
 adoptPreferences(await api('/api/preferences',{favorite:id,enabled}));const row=projects.find(p=>p.id===id);if(row)row.favorite=enabled;renderList();if(selected?.id===id)renderDetail();
}
async function refreshHub(){await api('/api/hub/refresh',{});await loadCatalog({refreshSelection:true});}
async function loadMw5(){
 const id=selected?.id,sequence=++mw5Sequence;if(!id||projectType(selected)!=='mw5')return;
 const value=await api('/api/mw5?project='+encodeURIComponent(id));if(selected?.id!==id||sequence!==mw5Sequence)return;
 mw5Readiness=value;mw5Preview=value.preview||null;renderMw5Controls();renderMw5Preview();
}
async function checkMw5(){
 if(projectType(selected)!=='mw5')return;const id=selected.id;
 await mutate(async()=>{await api('/api/mw5/check',{project:id});await poll();if(selected?.id===id)await loadMw5();},'mw5-check');
}
function renderMw5Controls(){
 if(projectType(selected)!=='mw5')return;
 const info=mw5Readiness,active=state.activeSession,ownSession=active?.kind?.startsWith('mw5')&&(!active.projectId||active.projectId===selected.id),locked=mutation||state.transferring||state.closing||info?.busy||(state.busy&&!ownSession);
 const editor=info?.editor;
 $('mw5-readiness').textContent=!info?'Checking the editor and managed plugins…':info.error||info.reason||info.message||(editor?.installed||info.ready?'Editor found'+(editor?.version?' · '+editor.version:'')+(editor?.path?' · '+editor.path:''):'Editor or managed plugins need attention before staging.');
 $('mw5-session').textContent=info?.session?'Plugin session: '+human(info.session.status||'not started')+(info.session.message?' · '+info.session.message:''):'No plugin editing session recorded.';
 $('mw5-refresh').disabled=locked;$('mw5-stage').disabled=locked||info?.canStage!==true;$('mw5-collect').disabled=locked||info?.canCollect!==true;
 $('mw5-finish').hidden=info?.session?.canFinish!==true;$('mw5-finish').disabled=locked||info?.session?.canFinish!==true;
 $('open').disabled=locked||info?.session?.canOpen!==true;
 $('mw5-apply').disabled=locked||!transferCanApply(mw5Preview);
}
function renderMw5Preview(){
 const preview=mw5Preview;$('mw5-preview').hidden=!preview;if(!preview)return;
 $('mw5-preview-title').textContent=preview.direction==='collect'?'Collect editor changes into the managed copy':'Stage managed plugins into the editor';
 const changes=preview.changes||[];let bytes=0;for(const change of changes)if(Number.isFinite(change.bytes))bytes+=change.bytes;
 $('mw5-preview-summary').textContent=changes.length+' file change'+(changes.length===1?'':'s')+' · '+size(bytes)+' of listed payload. Existing differing files are preserved before reviewed replacement.';
 $('mw5-preview-changes').replaceChildren();
 for(const change of changes){const li=document.createElement('li');li.textContent=human(change.action||change.kind||'Change')+' · '+String(change.path||change.relativePath||'Recorded file')+(Number.isFinite(change.bytes)?' · '+size(change.bytes):'');$('mw5-preview-changes').append(li);}
 if(!changes.length){const li=document.createElement('li');li.textContent='No file changes in this preview.';$('mw5-preview-changes').append(li);}
 $('mw5-preview-conflicts').replaceChildren();
 for(const conflict of preview.conflicts||[]){const p=document.createElement('p');p.textContent='Conflict — '+(typeof conflict==='string'?conflict:(conflict.path||'Recorded file')+': '+(conflict.reason||'Both copies changed. Both are preserved for review.'));$('mw5-preview-conflicts').append(p);}
 $('mw5-preview-warnings').textContent=(preview.warnings||[]).map(asText).join(' ');
 $('mw5-apply').textContent=preview.direction==='collect'?'Collect reviewed changes':'Stage reviewed plugins';
 renderMw5Controls();
}
async function previewMw5(direction){
 if(projectType(selected)!=='mw5')throw new Error('Choose the MW5 project first.');
 const id=selected.id;mw5Preview=null;$('mw5-preview').hidden=true;
 await mutate(async()=>{const result=await api('/api/mw5/preview',{project:id,direction});if(selected?.id===id){if(result.preview)mw5Preview=result.preview;else if(result.id&&result.direction)mw5Preview=result;renderMw5Preview();}await poll();if(selected?.id===id)await loadMw5();},'mw5-'+direction+'-preview');
}
async function applyMw5(){
 if(projectType(selected)!=='mw5'||!transferCanApply(mw5Preview))throw new Error('Refresh and review an applicable plugin preview first.');
 const id=selected.id,previewId=mw5Preview.id,direction=mw5Preview.direction;
 if(!confirm(direction==='stage'?'Stage these reviewed plugin changes into the closed MW5 Editor? Existing differing files will be preserved.':'Collect these reviewed changes, including listed deletions, into the managed working copy? Existing differing files will be preserved. This does not save to MSI.'))return;
 await mutate(async()=>{await api('/api/mw5/apply',{project:id,previewId,confirmation:direction==='stage'?'STAGE':'COLLECT'});mw5Preview=null;$('mw5-preview').hidden=true;await poll();if(selected?.id===id)await loadMw5();},'mw5-'+direction+'-apply');
}
async function finishMw5(){
 if(projectType(selected)!=='mw5'||mw5Readiness?.session?.canFinish!==true)throw new Error('Only a recovered, fully collected MW5 session can be finished here.');
 const id=selected.id;
 if(!confirm('Finish this already-collected MW5 session? This releases its session protection. Saving to MSI remains a separate action.'))return;
 await mutate(async()=>{await api('/api/mw5/finish',{project:id});await poll();if(selected?.id===id)await loadMw5();},'mw5-finish');
}
// END HUB INTERFACE
// BEGIN MISSING MEDIA PANEL
function missingError(error){$('missing-feedback').textContent=String(error.message||error);$('missing-feedback').className='warning';}
function missingControls(){for(const id of ['missing-report','missing-json','missing-csv','missing-path','missing-note','missing-source'])$(id).disabled=notePending||!selected||state.closing;for(const button of document.querySelectorAll('[data-missing-action]'))button.disabled=notePending||state.closing;$('quit').disabled=notePending||state.busy||mutation||state.closing;}
async function noteRequest(fn){if(notePending)throw new Error('Please wait for the report to be recorded.');notePending=true;missingControls();try{return await fn();}finally{notePending=false;missingControls();}}
async function loadMissing(){
 const id=selected?.id,sequence=++missingSequence;if(!id)return;
 $('missing-project').textContent='Reports for '+selected.name;$('missing-feedback').className='muted';$('missing-feedback').textContent='Loading your reports…';$('missing-records').replaceChildren();$('missing-count').textContent='…';
 const result=await api('/api/missing?project='+encodeURIComponent(id));if(selected?.id!==id||sequence!==missingSequence)return;
 missingRecords=result.records;renderMissing();$('missing-feedback').textContent=missingRecords.length?'Reports are saved separately from project files.':'No user reports recorded for this family.';missingControls();
}
function renderMissing(){
 $('missing-records').replaceChildren();$('missing-count').textContent=missingRecords.length;
 const labels={reported_missing:'Reported missing — unresolved',user_marked_unavailable:'Unavailable — you marked',resolved:'Resolved — you marked'};
 for(const row of missingRecords){
  const box=document.createElement('div');box.className='missing-record';const name=document.createElement('strong'),status=document.createElement('span'),note=document.createElement('p'),actions=document.createElement('div');
  name.textContent=row.path;status.className='classification';status.textContent=labels[row.status]||row.status;note.textContent='First report: '+human(row.firstSource)+(row.firstNote?' — '+row.firstNote:'')+(row.note&&row.note!==row.firstNote?'\nLatest note: '+row.note:'');actions.className='missing-actions';
  for(const [value,label] of [['resolved','Mark resolved'],['user_marked_unavailable','Mark unavailable'],['reported_missing','Reopen report']]){
   if(value===row.status)continue;const button=document.createElement('button');button.type='button';button.className='quiet small';button.dataset.missingAction='true';button.textContent=label;
   button.addEventListener('click',()=>noteRequest(async()=>{const text=prompt('Optional note. This is your status, not a verified repair.','');if(text===null)return;await api('/api/missing/status',{project:row.projectId,recordId:row.recordId,status:value,note:text,requestId:crypto.randomUUID()});if(selected?.id===row.projectId)await loadMissing();}).catch(missingError));actions.append(button);
  }
  box.append(name,status,note,actions);$('missing-records').append(box);
 }
}
async function reportManual(){
 const project=selected?.id,path=$('missing-path').value,note=$('missing-note').value,source=$('missing-source').value;if(!project)throw new Error('Select a project.');
 await noteRequest(async()=>{await api('/api/missing/report',{project,path,note,source,requestId:crypto.randomUUID()});if(selected?.id===project){$('missing-path').value='';$('missing-note').value='';await loadMissing();$('missing-feedback').textContent='Report recorded. No media or project files were changed.';}});
}
async function flagAudit(project,reference){
 await noteRequest(async()=>{await api('/api/missing/report',{project,source:'audit_flag',auditReferenceId:reference,note:'Manually flagged from the reference scan; no automatic dialog capture or repair.',requestId:crypto.randomUUID()});if(selected?.id===project){await loadMissing();$('missing-feedback').textContent='Audit reference flagged for your review. This does not prove the file is permanently unavailable.';}});
}
async function exportMissing(format){
 const project=selected?.id;if(!project)return;
 await noteRequest(async()=>{const result=await api('/api/missing/export?project='+encodeURIComponent(project)+'&format='+format);const blob=new Blob([result.text],{type:result.mime+';charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=result.filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
}
// END MISSING MEDIA PANEL
$('missing-report').addEventListener('click',()=>reportManual().catch(missingError));
$('missing-json').addEventListener('click',()=>exportMissing('json').catch(missingError));$('missing-csv').addEventListener('click',()=>exportMissing('csv').catch(missingError));
$('search').addEventListener('input',renderList);
for(const button of document.querySelectorAll('[data-filter]'))button.addEventListener('click',()=>{filter=button.dataset.filter;for(const other of document.querySelectorAll('[data-filter]'))other.classList.toggle('active',other===button);renderList();});
$('entry').addEventListener('change',()=>{if(selected)chosenEntries.set(selected.id,$('entry').value);updateEntry();renderState();});
$('reference-search').addEventListener('input',filterReferences);
for(const name of ['open','save','verify','recover','fetch','folder'])$(name).addEventListener('click',safe(()=>action(name)));
$('favorite-toggle').addEventListener('click',safe(toggleFavorite));$('hub-refresh').addEventListener('click',safe(refreshHub));
$('session-project').addEventListener('click',()=>{const id=state.activeSession?.projectId||state.operation?.projectId;if(id)select(id);});$('session-done').addEventListener('click',safe(()=>sendResponse('DONE')));
$('mw5-refresh').addEventListener('click',safe(checkMw5));$('mw5-stage').addEventListener('click',safe(()=>previewMw5('stage')));$('mw5-collect').addEventListener('click',safe(()=>previewMw5('collect')));$('mw5-apply').addEventListener('click',safe(applyMw5));$('mw5-finish').addEventListener('click',safe(finishMw5));
$('done').addEventListener('click',safe(()=>sendResponse('DONE')));$('cancel-startup').addEventListener('click',safe(()=>sendResponse('CANCEL STARTUP')));
$('workspace-tab').addEventListener('click',safe(()=>setTab(false)));$('audit-tab').addEventListener('click',safe(()=>setTab(true)));
$('audit-window').addEventListener('click',()=>{if(selected)window.open('/audit?project='+encodeURIComponent(selected.id)+'#'+token,'_blank','noopener');});
$('toggle-log').addEventListener('click',()=>{$('operation-log').hidden=!$('operation-log').hidden;$('toggle-log').textContent=$('operation-log').hidden?'Show details':'Hide details';});
$('open-feedback-link').addEventListener('click',()=>{$('operation-log').hidden=false;$('toggle-log').textContent='Hide details';document.querySelector('.activity').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});});
$('release').addEventListener('click',()=>{releaseTarget={id:selected.id,confirmation:releaseConfirmation(selected),name:selected.name,revision:state.revision};$('release-description').textContent=selected.name+' · '+size(selected.bytes)+' selected working set estimate.';$('release-expected').textContent=releaseTarget.confirmation;$('release-id').value='';$('release-confirm').disabled=true;$('release-dialog').showModal();});
$('release-id').addEventListener('input',()=>{$('release-confirm').disabled=$('release-id').value!==releaseTarget?.confirmation;});
$('release-confirm').addEventListener('click',safe(()=>mutate(async()=>{if(!releaseTarget)throw new Error('Choose a project again.');await api(releaseTarget.stats?'/api/stats/action':'/api/action',{action:'release',project:releaseTarget.id,confirmation:$('release-id').value,expectedRevision:releaseTarget.revision});$('release-dialog').close();releaseTarget=null;await poll();})));
$('quit').addEventListener('click',safe(()=>mutate(async()=>{await api('/api/quit',{});stopped=true;state.closing=true;$('mode').textContent='SERVICE STOPPED';$('operation-description').textContent='The interface service has stopped. You may close this tab.';document.querySelectorAll('button').forEach(button=>button.disabled=true);})));
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}});
async function initialize(){
 if(!token)throw new Error('Open the local link printed by INTERLINKED.cmd. Its private session token is required.');
 await loadCatalog();await poll();if(location.pathname==='/audit')await setTab(true);
 schedulePoll();
}
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(pollTimer);pollTimer=null;}else poll().catch(pollError).finally(schedulePoll);});
initialize().catch(showError);

// BEGIN MSI JOB MODELS
function jobsWords(value){return String(value||'Unknown').replaceAll('_',' ').toLowerCase().replace(/\b\w/g,c=>c.toUpperCase());}
function jobsWhen(value){const stamp=Date.parse(value||'');return Number.isFinite(stamp)?new Date(stamp).toLocaleString():'Not recorded';}
function jobsModel(job,stale=false){
 const status=String(job.status||'UNKNOWN'),review=!!job.warning||/FAILED|REVIEW|INTERRUPTED/.test(status);
 const complete=status==='COMPLETED'&&job.exitCode===0&&job.taskState==='Ready'&&job.lastTaskResult===0&&Number.isFinite(Date.parse(job.finishedUtc||''));
 return {label:String(job.projectId||'Unknown project'),status:(stale?'Last observed: ':'Runner: ')+jobsWords(status),
  tone:review?'attention':stale?'quiet':complete?'good':'quiet',
  exit:Number.isInteger(job.exitCode)?String(job.exitCode):'Not recorded',
  task:String(job.taskState||'Unknown'),taskExit:Number.isInteger(job.lastTaskResult)?String(job.lastTaskResult):'Not recorded',
  warning:String(job.warning||''),updated:jobsWhen(job.updatedUtc)};
}
function jobsLogModel(log){
 const stale=log.stale?'Stale observation. ':'';
 return {text:log.available===true?String(log.text||''):'',note:log.available!==true?stale+'This log was unavailable at the observation.':
  stale+(log.truncated?'Showing the retained tail; earlier output remains on MSI. ':log.text?'Retained log output. ':'The log was empty at the observation. ')+'Observed '+jobsWhen(log.observedAt)+'.'};
}
// END MSI JOB MODELS

// BEGIN MSI JOB INTERFACE
let jobsSnapshot=null,jobsVisible=false,jobsPollBusy=false,jobsRefreshPending=false,jobsSelected=null,jobsLogSequence=0,jobsLogSnapshotKey='',jobsLocalUnavailable=false;
function jobsElement(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function jobsShowError(error){$('msi-jobs-error').textContent=String(error?.message||error);$('msi-jobs-error').hidden=false;}
function jobsHide(){jobsVisible=false;$('msi-jobs-view').hidden=true;$('jobs-view-tab').classList.toggle('active',false);$('jobs-view-tab').setAttribute('aria-pressed','false');}
function jobsSetView(){
 if(typeof masterSetView==='function')masterSetView(false);
 jobsVisible=true;$('msi-jobs-view').hidden=false;$('library-shell').hidden=true;$('library-hub-bar').hidden=true;$('master-view').hidden=true;
 for(const id of ['library-view-tab','master-view-tab','jobs-view-tab']){const on=id==='jobs-view-tab';$(id).classList.toggle('active',on);$(id).setAttribute('aria-pressed',String(on));}
 document.body.classList.toggle('master-open',false);jobsPoll().catch(jobsShowError);
}
function jobsRender(){
 const s=jobsSnapshot||{},connection=s.connection||{},stale=!!s.stale||jobsLocalUnavailable,rows=Array.isArray(s.jobs)?s.jobs:[];
 if(!rows.some(row=>row.jobId===jobsSelected))jobsSelected=rows[0]?.jobId||null;
 const observed=s.observedAt?'Observed '+jobsWhen(s.observedAt):'No observation received';
 $('msi-jobs-connection').textContent=s.refreshing?'Refresh in progress':jobsLocalUnavailable?'Local status unavailable':connection.ssh==='connected'?(stale?'Previous SSH observation':'SSH observed'):connection.ssh==='unavailable'?'SSH unavailable':connection.ssh==='connecting'?'Connecting to MSI':'Not refreshed';
 $('msi-jobs-message').textContent=connection.message||'Choose Refresh from MSI to request a new observation.';
 $('msi-jobs-worker').textContent=connection.worker==='observed'?'Job records observed. Worker readiness is not checked.':'Worker readiness is unknown.';
 $('msi-jobs-observed').textContent=observed+(s.observedAt?(stale?' · STALE':' · Snapshot'):'');
 $('msi-jobs-refresh').disabled=jobsRefreshPending||!!s.refreshing;$('msi-jobs-refresh').textContent=s.refreshing?'Refreshing…':'Refresh from MSI';$('msi-jobs-demo').hidden=!s.demo;
 $('msi-jobs-count').textContent=String(rows.length);$('msi-jobs-empty').hidden=!!rows.length;
 $('msi-jobs-empty').textContent=!s.observedAt?'No job observation has been received.':stale?'No jobs in the previous observation. Refresh from MSI to check again.':'No owned jobs were reported in this observation.';
 $('msi-jobs-limits').textContent=[s.truncated?'Only the most recent 50 job records are shown.':'Up to 50 job records and a bounded tail of each log are read.',s.rejected?String(s.rejected)+' record(s) could not be read safely.':'',s.notice||''].filter(Boolean).join(' ');
 $('msi-jobs-list').replaceChildren();
 for(const row of rows){
  const p=jobsModel(row,stale),button=jobsElement('button','msi-job-row '+p.tone+(row.jobId===jobsSelected?' active':''));button.type='button';button.setAttribute('aria-pressed',String(row.jobId===jobsSelected));
  button.append(jobsElement('strong','',p.label),jobsElement('span','msi-job-state',p.status),jobsElement('small','',String(row.jobId)),jobsElement('small','','Updated '+p.updated));
  button.addEventListener('click',()=>{jobsSelected=row.jobId;jobsLogSnapshotKey='';jobsRender();jobsReadLog().catch(jobsShowError);});$('msi-jobs-list').append(button);
 }
 const selected=rows.find(row=>row.jobId===jobsSelected),p=selected?jobsModel(selected,stale):null;
 $('msi-jobs-detail-title').textContent=p?p.label:'Choose a recorded job';$('msi-jobs-detail-id').textContent=selected?'Job '+String(selected.jobId):'';$('msi-jobs-stream').disabled=!selected;$('msi-jobs-facts').replaceChildren();
 if(selected&&stale&&jobsLogSnapshotKey&&!$('msi-jobs-log-note').textContent.startsWith('Stale observation. '))$('msi-jobs-log-note').textContent='Stale observation. '+$('msi-jobs-log-note').textContent;
 if(selected){
  const facts=[['Runner receipt',p.status],['Runner exit code',p.exit],['Task Scheduler',p.task],['Scheduler result',p.taskExit],['Started',jobsWhen(selected.startedUtc)],['Finished',jobsWhen(selected.finishedUtc)]];
  for(const [name,value] of facts)$('msi-jobs-facts').append(jobsElement('dt','',name),jobsElement('dd','',value));
 }
 $('msi-jobs-review').hidden=!p?.warning;$('msi-jobs-review').textContent=p?.warning||'';
 if(!selected){jobsLogSequence++;jobsLogSnapshotKey='';$('msi-jobs-log').textContent='';$('msi-jobs-log-note').textContent='Select a job to read its retained log tail.';}
 if(s.error){$('msi-jobs-error').textContent=String(s.error);$('msi-jobs-error').hidden=false;}
}
async function jobsReadLog(){
 if(!jobsSelected)return;const job=jobsSelected,stream=$('msi-jobs-stream').value||'stdout',sequence=++jobsLogSequence,key=[jobsSnapshot?.observedAt||'',job,stream].join('/');
 $('msi-jobs-log').textContent='';$('msi-jobs-log-note').textContent='Reading the locally retained log tail…';
 try{
  const log=await api('/api/msi/jobs/log?job='+encodeURIComponent(job)+'&stream='+encodeURIComponent(stream));
  if(sequence!==jobsLogSequence||job!==jobsSelected||stream!==($('msi-jobs-stream').value||'stdout'))return;
  if(log.readOnly!==true||log.jobId!==job||log.stream!==stream)throw new Error('The retained log response did not match the selected job and stream.');
  const model=jobsLogModel({...log,stale:log.stale||jobsLocalUnavailable});$('msi-jobs-log').textContent=model.text;$('msi-jobs-log-note').textContent=model.note;jobsLogSnapshotKey=key;
 }catch(error){if(sequence===jobsLogSequence){$('msi-jobs-log').textContent='';$('msi-jobs-log-note').textContent='The retained log could not be read.';}throw error;}
}
async function jobsPoll(){
 if(jobsPollBusy||stopped)return;jobsPollBusy=true;let statusReceived=false;
 try{const snapshot=await api('/api/msi/jobs');if(snapshot.readOnly!==true)throw new Error('Unexpected MSI job status format.');jobsSnapshot=snapshot;statusReceived=true;jobsLocalUnavailable=false;$('msi-jobs-error').hidden=true;jobsRender();const key=[jobsSnapshot.observedAt||'',jobsSelected,$('msi-jobs-stream').value||'stdout'].join('/');if(jobsSelected&&key!==jobsLogSnapshotKey)await jobsReadLog();}
 catch(error){if(!statusReceived){jobsLocalUnavailable=true;jobsRender();}throw error;}finally{jobsPollBusy=false;}
}
async function jobsRefresh(){
 if(jobsRefreshPending||jobsSnapshot?.refreshing)return;jobsRefreshPending=true;$('msi-jobs-error').hidden=true;jobsRender();
 try{await api('/api/msi/jobs/refresh',{});await jobsPoll();}finally{jobsRefreshPending=false;jobsRender();}
}
// END MSI JOB INTERFACE

// BEGIN AI MODELS
function aiWords(value){return String(value||'Unknown').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());}
function aiModelKey(model){return JSON.stringify([model.host||model.hostId,model.id||model.name||model.model]);}
function aiOverride(value){if(!value)return {};const [host,model]=JSON.parse(value);return {host,model};}
function aiRoutingMode(value){return ['balanced','fast','best_quality'].includes(value)?value:'balanced';}
function aiRouteText(value){const r=value.routing||value.routingDecision||{},m=value.model||r.model;return [r.mode?aiWords(r.mode):'',m?m.host+' / '+m.id:'',r.reason||'',r.limitedEvidence?'Limited evidence':'',r.fallback?'Classification fallback':''].filter(Boolean).join(' · ')||value.error||value.queueReason||'Auto · Balanced';}
function aiRestoreChatSelection(value){$('ai-routing-mode').value=aiRoutingMode(value.routingMode);const choice=value.manualOverride?.model?JSON.stringify([value.manualOverride.host||'local',value.manualOverride.model]):'',select=$('ai-model-override');if(choice&&!Array.from(select.children).some(x=>x.value===choice)){const item=aiElement('option','','Selected model unavailable');item.value=choice;select.append(item);}select.value=choice;}
function aiRunning(status){return ['QUEUED','STARTING','RUNNING','WORKING','REVIEWING','CORRECTING'].includes(String(status||'').toUpperCase());}
function aiTaskModel(task){
 const result=task.result||{},review=task.review||result.review||{},status=String(task.status||'UNKNOWN').toUpperCase();
 return {id:task.id||task.taskId,title:String(task.prompt||task.instruction||task.projectId||'Assigned task').slice(0,100),status:aiWords(status),running:aiRunning(status),recoverable:task.recoveryAvailable===true||status==='RECOVERY_REQUIRED',
  review:typeof review==='string'?review:String(review.summary||[review.status,review.reason,...(review.findings||[])].filter(Boolean).join(' · ')||'Independent review not recorded'),
  resultHash:task.resultHash||result.resultHash||result.sha256||result.hash||'',
  canApply:!aiRunning(status)&&['READY_FOR_REVIEW','COMPLETED','REVIEWED','READY'].includes(status)&&!!(task.resultHash||result.resultHash||result.sha256||result.hash)&&!task.applied};
}
function aiWav(chunks,sampleRate){
 const count=chunks.reduce((n,row)=>n+row.length,0),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);let offset=44;
 const word=(at,text)=>{for(let i=0;i<text.length;i++)view.setUint8(at+i,text.charCodeAt(i));};
 word(0,'RIFF');view.setUint32(4,36+count*2,true);word(8,'WAVE');word(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,'data');view.setUint32(40,count*2,true);
 for(const chunk of chunks)for(const value of chunk){const sample=Math.max(-1,Math.min(1,value));view.setInt16(offset,sample<0?sample*32768:sample*32767,true);offset+=2;}
 return buffer;
}
function aiAudioKind(value){return value==='general'?'general':'speech';}
function aiAttachmentValid(file){return !!file&&file.size>0&&file.size<=12*1024*1024&&['image/png','image/jpeg','audio/wav','audio/x-wav'].includes(file.type);}
// END AI MODELS

// BEGIN AI INTERFACE
let aiVisible=false,aiSnapshot=null,aiPollBusy=false,aiConversation=null,aiConversationValue=null,aiTask=null,aiTaskValue=null,aiChatSequence=0,aiTaskSequence=0,aiPending=false,aiAttachments=[],aiRecording=null,aiRecordStarting=false,aiRecordGeneration=0,aiLastSpoken='',aiNativeVoices=[],aiView='assistant';
let aiTaskPage=null,aiTaskPageBusy=false,aiTaskPageSequence=0,aiTaskDetailRevision=null;
function aiElement(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function aiError(error){$('ai-error').textContent=String(error?.message||error);$('ai-error').hidden=false;}
function aiAction(fn){return async(...args)=>{try{$('ai-error').hidden=true;await fn(...args);}catch(error){aiError(error);}};}
function aiHide(){aiVisible=false;if($('ai-view'))$('ai-view').hidden=true;if($('ai-view-tab')){$('ai-view-tab').classList.toggle('active',false);$('ai-view-tab').setAttribute('aria-pressed','false');}aiStopRecording(false).catch(aiError);aiStopSpeech();}
function aiSetView(){masterSetView(false);jobsHide();aiVisible=true;$('ai-view').hidden=false;$('library-shell').hidden=true;$('library-hub-bar').hidden=true;$('master-view').hidden=true;for(const id of ['library-view-tab','master-view-tab','jobs-view-tab','ai-view-tab']){$(id).classList.toggle('active',id==='ai-view-tab');$(id).setAttribute('aria-pressed',String(id==='ai-view-tab'));}aiPoll().catch(aiError);aiLoadVoices();}
function aiTab(name){aiView=name;for(const value of ['assistant','history','memories','sync','handoff','workers','models']){$('ai-panel-'+value).hidden=value!==name;$('ai-tab-'+value).classList.toggle('active',value===name);$('ai-tab-'+value).setAttribute('aria-pressed',String(value===name));}if(name!=='assistant'){aiStopRecording(false).catch(aiError);aiStopSpeech();}if(name==='memories')aiHistoryPoll().catch(aiError);}
function aiSelectOptions(id,rows,automatic){const select=$(id),old=select.value;select.replaceChildren();if(automatic){const first=aiElement('option','',automatic);first.value='';select.append(first);}for(const row of rows){const option=aiElement('option','',row.label);option.value=row.value;select.append(option);}if(old&&id.endsWith('model-override')&&!rows.some(row=>row.value===old)){const unavailable=aiElement('option','','Selected model unavailable');unavailable.value=old;select.append(unavailable);}if(rows.some(row=>row.value===old)||automatic&&old===''||old&&id.endsWith('model-override'))select.value=old;}
function aiRender(){
 const s=aiSnapshot||{},models=s.models||[],hosts=s.hosts||[];$('ai-connection').textContent=s.available===false?'UNAVAILABLE':hosts.length?hosts.filter(h=>h.available).length+' HOSTS AVAILABLE':'NO HOST OBSERVATION';$('ai-notice').textContent=s.error||s.notice||(s.demo?'SYNTHETIC DEMO · No model inference or personal project access.':'Model capability checks are explicit. Your installed models stay on their hosts.');
 $('ai-hosts').replaceChildren();for(const host of hosts){const card=aiElement('article','ai-card'),inventory=host.inventory||{};card.append(aiElement('h2','',host.label||host.id),aiElement('strong','',host.available?'Available':'Unavailable'),aiElement('p','master-help',host.error||inventory.version&&'Ollama '+inventory.version||'Runtime not observed'));const info=host.resources||inventory.resources;if(info)card.append(aiElement('pre','ai-result',JSON.stringify(info,null,2)));$('ai-hosts').append(card);}
 const options=models.map(m=>({value:aiModelKey(m),label:(m.host||m.hostId)+' / '+(m.id||m.name||m.model)}));aiSelectOptions('ai-model-override',options,'Auto');aiSelectOptions('ai-task-model-override',options,'Auto');aiSelectOptions('ai-calibrate-model',options,null);
 aiSelectOptions('ai-task-project',(s.projects||[]).map(p=>({value:p.id,label:p.label||p.name||p.id})),null);aiRenderChecks();
 $('ai-context-projects').replaceChildren();for(const project of s.projects||[]){const row=aiElement('div','ai-context-row'),label=aiElement('label'),input=aiElement('input');input.type='checkbox';input.checked=Array.isArray(s.approvals)?s.approvals.some(p=>(typeof p==='string'?p:p.projectId||p.id)===project.id&&(typeof p==='string'||p.enabled!==false)):!!s.approvals?.[project.id];input.addEventListener('change',aiAction(async()=>{await api('/api/ai/context/approve',{projectId:project.id,enabled:input.checked});await aiPoll();}));label.append(input,aiElement('span','',project.label||project.id));const index=aiElement('button','quiet small','Index');index.type='button';index.disabled=!input.checked;index.addEventListener('click',aiAction(async()=>{const result=await api('/api/ai/context/index',{projectId:project.id});$('ai-notice').textContent=result.message||String(result.files||0)+' text files indexed; '+String(result.excluded||0)+' excluded.';}));row.append(label,index);$('ai-context-projects').append(row);}
 $('ai-conversations').replaceChildren();for(const c of s.conversations||[]){const button=aiElement('button','ai-list-row'+(c.id===aiConversation?' active':''),c.title||c.id);button.type='button';button.addEventListener('click',aiAction(async()=>{aiConversation=c.id;aiConversationValue=null;await aiReadConversation();}));$('ai-conversations').append(button);}
 $('ai-models').replaceChildren();for(const model of models){const card=aiElement('article','ai-card'),name=model.id||model.name||model.model;card.append(aiElement('h3','',name),aiElement('p','master-help',(model.host||model.hostId)+' · '+(model.loaded?'Loaded':'Not loaded')),aiElement('code','ai-digest',model.digest||'Digest unavailable'),aiElement('p','','Advertised: '+(model.capabilities||[]).join(', ')),aiElement('p','','Passed fixture checks: '+(Object.keys(model.verified||{}).filter(key=>model.verified[key]===true||model.verified[key]?.passed===true).join(', ')||'No passing checks')),aiElement('p','master-help',model.stale?'Inventory is stale; refresh this host.':model.eligible===false?'Unavailable or excluded from routing':'Fixture scores are limited evidence, not general model quality'));
  const eligibility=aiElement('label'),toggle=aiElement('input');toggle.type='checkbox';toggle.checked=!(s.settings?.disabledModels||[]).includes(model.host+':'+name);toggle.addEventListener('change',aiAction(async()=>{const disabled=new Set(aiSnapshot.settings.disabledModels);if(toggle.checked)disabled.delete(model.host+':'+name);else disabled.add(model.host+':'+name);await api('/api/ai/settings',{settingsRevision:aiSnapshot.settingsRevision,value:{...aiSnapshot.settings,disabledModels:[...disabled]}});await aiPoll();}));eligibility.append(toggle,aiElement('span','','Eligible for automatic routing after checks'));card.append(eligibility);const actions=aiElement('div','ai-compose-actions');for(const action of ['load','unload']){const button=aiElement('button','quiet small',aiWords(action));button.type='button';button.addEventListener('click',aiAction(async()=>{if(action==='unload'&&!confirm('Unload this INTERLINKED-loaded model? Confirm that other Ollama clients are idle.'))return;await api('/api/ai/model',{host:model.host||model.hostId,model:name,action,confirmedExternalIdle:action==='unload'});await aiPoll();}));actions.append(button);}card.append(actions);$('ai-models').append(card);}
  aiRenderTasks();
 if(!(s.tasks||[]).length)$('ai-task-list').append(aiElement('p','master-help','No assigned tasks yet.'));
}
function aiRenderChecks(){const profiles=aiSnapshot?.checkProfiles||[],selected=new Set(Array.from($('ai-check-profiles').selectedOptions||[]).map(x=>x.value)),rows=Array.isArray(profiles)?profiles:Object.entries(profiles).map(([id,p])=>({id,...p}));const select=$('ai-check-profiles');select.replaceChildren();for(const p of rows){if(p.projectId&&p.projectId!==$('ai-task-project').value)continue;const option=aiElement('option','',p.label||p.id);option.value=p.id;option.selected=selected.has(p.id);select.append(option);}select.disabled=!$('ai-allow-check').checked;$('ai-check-note').textContent=rows.length?'Only registered commands can run.':'No reviewed check profiles are registered. Register an exact command on the execution host before assigning checks.';}
async function aiBridgePoll(){
 const panel=$('ai-bridge-requests');if(!panel)return;
 try{const state=await api('/api/ai/bridge/status');panel.replaceChildren(aiElement('h2','','Violet model requests'));
  if(!state.connectionAvailable)panel.append(aiElement('p','master-help',state.connectionError||'The AI plugin connection is unavailable.'));
  const pending=state.pendingUnloads||[];if(!pending.length)panel.append(aiElement('p','master-help','No model unload requests need confirmation.'));
  for(const job of pending){const row=aiElement('article','ai-card');row.append(aiElement('p','',job.host+' / '+job.model),aiElement('code','ai-digest',job.digest));
   const button=aiElement('button','quiet small','Confirm other clients are idle and unload');button.type='button';button.addEventListener('click',aiAction(async()=>{if(!confirm('Confirm that other Ollama clients are idle. Unload only this exact INTERLINKED-loaded model?'))return;await api('/api/ai/bridge/confirm-unload',{id:job.id});await aiBridgePoll();}));row.append(button);panel.append(row);}
 }catch(error){panel.replaceChildren(aiElement('p','master-help','Violet model requests are unavailable.'));}
}
function aiRenderTasks(){
 const rows=aiTaskPage?.tasks||aiSnapshot?.tasks||[],page=aiTaskPage||aiSnapshot?.tasksPage||{};
 $('ai-task-list').replaceChildren();for(const task of rows){const model=aiTaskModel(task),button=aiElement('button','ai-list-row'+(model.id===aiTask?' active':''));button.type='button';button.append(aiElement('strong','',model.title),aiElement('small','',model.status));button.addEventListener('click',aiAction(async()=>{aiTask=model.id;aiTaskValue=null;aiTaskDetailRevision=null;await aiReadTask();}));$('ai-task-list').append(button);}
 $('ai-tasks-count').textContent=rows.length+' shown'+(Number.isSafeInteger(page.total)?' of '+page.total:'')+(aiTaskPage?' · Older tasks':' · Latest tasks');
 $('ai-tasks-next').disabled=aiTaskPageBusy||!page.nextCursor;$('ai-tasks-first').disabled=aiTaskPageBusy||!aiTaskPage;
}
async function aiLoadTaskPage(){
 const after=(aiTaskPage||aiSnapshot?.tasksPage)?.nextCursor;if(!after||aiTaskPageBusy)return;
 const sequence=++aiTaskPageSequence;aiTaskPageBusy=true;aiRenderTasks();
 try{const page=await api('/api/ai/tasks?after='+encodeURIComponent(after)+'&limit=50');if(sequence!==aiTaskPageSequence)return;aiTaskPage=page;}
 catch(error){if(sequence===aiTaskPageSequence)throw error;}
 finally{if(sequence===aiTaskPageSequence){aiTaskPageBusy=false;aiRenderTasks();}}
}
function aiLatestTasks(){aiTaskPageSequence++;aiTaskPageBusy=false;aiTaskPage=null;aiRenderTasks();}
function aiTaskRevision(){const summary=(aiSnapshot?.tasks||[]).find(task=>task.id===aiTask);return summary?summary.revision??summary.updatedAt:null;}
function aiTaskNeedsRead(){const revision=aiTaskRevision();return !aiTaskValue||aiTaskValue.id!==aiTask||revision==null||revision!==aiTaskDetailRevision;}
async function aiPoll(){if(aiPollBusy||stopped)return;aiPollBusy=true;try{aiSnapshot=await api('/api/ai/state');aiRender();await aiBridgePoll();await aiHistoryPoll({background:true});if(aiConversation)await aiReadConversation();if(aiTask&&aiTaskNeedsRead())await aiReadTask();}catch(error){$('ai-connection').textContent='CONNECTION LOST';throw error;}finally{aiPollBusy=false;}}
async function aiReadConversation(){const id=aiConversation,sequence=++aiChatSequence;if(!id)return;const value=await api('/api/ai/conversation?id='+encodeURIComponent(id));if(id!==aiConversation||sequence!==aiChatSequence)return;if(!aiConversationValue||aiConversationValue.id!==id){aiRestoreChatSelection(value);}aiConversationValue=value;aiRenderConversation(value);}
function aiRenderConversation(value){$('ai-chat-title').textContent=value.title||'Conversation';$('ai-chat-status').textContent=aiWords(value.status);$('ai-chat-cancel').disabled=!aiRunning(value.status);$('ai-send').disabled=aiPending||aiRunning(value.status);$('ai-route').textContent=aiRouteText(value);$('ai-messages').replaceChildren();for(const message of value.messages||[]){if(message.role==='system'||message.role==='tool')continue;const article=aiElement('article','ai-message '+(message.role==='user'?'from-user':'from-assistant'));article.append(aiElement('strong','',message.role==='user'?'You':'Assistant'),aiElement('p','',message.content||message.text||''));if(message.routing||message.routingDecision)article.append(aiElement('small','ai-citation',aiRouteText(message)));for(const citation of message.citations||message.sources||[]){if(citation.kind&&aiHistorySourceId(citation)){const button=aiElement('button','quiet small ai-citation',aiHistorySourceLabel(citation));button.type='button';button.addEventListener('click',aiAction(async()=>{aiTab('history');await aiHistoryOpenActiveSource(citation);}));article.append(button);}else article.append(aiElement('small','ai-citation',[citation.projectId,citation.path,citation.line?'line '+citation.line:'',citation.available===false?'Source unavailable':citation.stale?'Stale snapshot':''].filter(Boolean).join(' · ')));}$('ai-messages').append(article);}if(value.error)$('ai-messages').append(aiElement('p','master-error',value.error));const last=(value.messages||[]).at(-1),key=value.id+':'+(value.messages||[]).length;if(last?.role==='assistant'&&!aiRunning(value.status)&&key!==aiLastSpoken){aiLastSpoken=key;if($('ai-speak').checked)aiSpeak(last.content||last.text||'');}}
function aiRenderAttachments(){$('ai-attachments').replaceChildren();for(const item of aiAttachments){const row=aiElement('span','ai-attachment',item.name||item.id),button=aiElement('button','quiet small','Remove');button.type='button';button.addEventListener('click',()=>{aiAttachments=aiAttachments.filter(x=>x.id!==item.id);aiRenderAttachments();});row.append(button);$('ai-attachments').append(row);}}
async function aiUpload(file){if(!aiAttachmentValid(file))throw new Error('Attach a PNG, JPEG, or WAV file, up to 12 MiB.');const buffer=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<buffer.length;i+=8192)binary+=String.fromCharCode(...buffer.subarray(i,i+8192));return await api('/api/ai/attachments',{name:file.name,mime:file.type,data:btoa(binary)});}
async function aiSend(){const entered=$('ai-prompt').value.trim(),text=entered||(aiAttachments.length?(aiAttachments.some(a=>String(a.mime).startsWith('audio/'))?(aiAudioKind($('ai-audio-kind').value)==='speech'?'Listen to the recording and respond to the spoken request.':'Describe the sounds in this audio, including music and speech when present.'):'Describe the attached image.'):'');if(aiPending||aiRunning(aiConversationValue?.status))return;if(!text)throw new Error('Enter a message or describe what to examine in the attachment.');if(text.length>24000)throw new Error('Keep each message within 24,000 characters.');aiPending=true;$('ai-send').disabled=true;try{const value=await api('/api/ai/chat',{conversationId:aiConversation||undefined,text,attachments:aiAttachments.map(a=>a.id),audioKind:aiAudioKind($('ai-audio-kind').value),routingMode:aiRoutingMode($('ai-routing-mode').value),...aiHistoryChatOptions(),...aiOverride($('ai-model-override').value)});aiConversation=value.id;aiConversationValue=value;aiHistoryContextReset=false;$('ai-prompt').value='';aiAttachments=[];aiRenderAttachments();aiRenderConversation(value);await aiPoll();}finally{aiPending=false;$('ai-send').disabled=aiRunning(aiConversationValue?.status);}}
async function aiSearch(){const result=await api('/api/ai/search',{query:$('ai-search-query').value.trim()});$('ai-search-results').replaceChildren();for(const hit of Array.isArray(result)?result:result.results||result.hits||[]){const card=aiElement('article','ai-search-hit');card.append(aiElement('strong','',[hit.projectId,hit.path].filter(Boolean).join(' / ')),aiElement('p','',hit.excerpt||hit.text||''),aiElement('small','',hit.stale?'Stale source snapshot':'Snapshot '+(hit.sha256||hit.snapshotId||'recorded')));$('ai-search-results').append(card);}if(!$('ai-search-results').children.length)$('ai-search-results').textContent='No matching approved text.';}
async function aiStartTask(){const permissions=['read'];if($('ai-allow-write').checked)permissions.push('write');if($('ai-allow-check').checked)permissions.push('check');const value=await api('/api/ai/tasks/start',{projectId:$('ai-task-project').value,prompt:$('ai-task-prompt').value.trim(),permissions,checkProfiles:permissions.includes('check')?Array.from($('ai-check-profiles').selectedOptions).map(x=>x.value):[],maxSteps:Number($('ai-max-steps').value),timeoutSeconds:Number($('ai-timeout').value),executionHost:$('ai-execution').value,routingMode:aiRoutingMode($('ai-task-routing-mode').value),requestId:crypto.randomUUID().replaceAll('-',''),...aiOverride($('ai-task-model-override').value)});aiTask=value.id||value.taskId;await aiPoll();}
async function aiReadTask(){const id=aiTask,sequence=++aiTaskSequence,revision=aiTaskRevision();if(!id)return;const value=await api('/api/ai/task?id='+encodeURIComponent(id));if(id!==aiTask||sequence!==aiTaskSequence)return;aiTaskValue=value;aiTaskDetailRevision=revision;const model=aiTaskModel(value);$('ai-task-detail').hidden=false;$('ai-task-title').textContent=model.title;$('ai-task-status').textContent=model.status;$('ai-task-summary').textContent=[value.error||value.summary||value.queueReason||'',aiRouteText(value)].filter(Boolean).join(' · ');$('ai-task-review').textContent='Independent review: '+model.review;$('ai-task-result').textContent=JSON.stringify(value.result||{baseline:value.baseline,checks:value.checks,events:value.events},null,2);$('ai-task-cancel').disabled=!model.running&&!model.recoverable;$('ai-task-reconcile').hidden=!model.recoverable;$('ai-task-reconcile').disabled=!model.recoverable;$('ai-task-apply').disabled=!model.canApply;}
async function aiReconcileTask(){const id=aiTask;if(!id)return;await api('/api/ai/tasks/reconcile',{id});aiTaskDetailRevision=null;if(aiTask===id)await aiReadTask();await aiPoll();}
async function aiApplyTask(){if(!aiTaskValue)return;const model=aiTaskModel(aiTaskValue);if(!model.canApply)return;if(!confirm('Apply the displayed result to '+String(aiTaskValue.projectId)+'? The baseline and active-session checks will run again. This does not save to MSI.'))return;await api('/api/ai/tasks/apply',{id:model.id,expectedResultHash:model.resultHash});await aiReadTask();}
async function aiCalibrate(){const body={...aiOverride($('ai-calibrate-model').value),capability:$('ai-capability').value};if(['vision','audio_speech','audio_general'].includes(body.capability)){const file=$('ai-calibrate-file').files[0];if(!file||!$('ai-calibrate-expected').value.trim())throw new Error('Choose a fixture and enter its expected answer.');body.attachmentId=(await aiUpload(file)).id;body.expected=$('ai-calibrate-expected').value.trim();}$('ai-calibration-result').textContent='Checking the selected model…';const result=await api('/api/ai/calibrate',body);if(body.attachmentId&&$('ai-register-fixture').checked){const id=crypto.randomUUID().replaceAll('-','');await api('/api/ai/bridge/register-fixture',{id,attachmentId:body.attachmentId,capability:body.capability,expected:body.expected});result.violetFixtureId=id;}$('ai-calibration-result').textContent=JSON.stringify(result,null,2);await aiPoll();}
function aiLoadVoices(){const select=$('ai-voice'),old=select.value;select.replaceChildren();const voices=globalThis.speechSynthesis?.getVoices?.().filter(v=>v.localService)||[];for(const voice of voices){const option=aiElement('option','',voice.name+' · '+voice.lang);option.value=voice.voiceURI;select.append(option);}for(const voice of aiNativeVoices){const option=aiElement('option','',voice.name);option.value='native:'+voice.id;select.append(option);}if(!select.children.length)select.append(aiElement('option','','No installed voice available'));if(Array.from(select.children).some(x=>x.value===old))select.value=old;$('ai-speak').disabled=!voices.length&&!aiNativeVoices.length;}
// Content-free, transient observations. Starting/queuing a request is not proof
// of capture or playback. A stalled producer expires instead of being renewed.
let aiVoiceSessionId='',aiVoiceSequence=0,aiVoiceTimer=null,aiVoiceLastState='',aiVoicePending=null,aiVoicePosting=false;
let aiSpeechGeneration=0,aiSpeechKind='none',aiSpeechUtterance=null,aiBrowserSpeechActive=false,aiNativeSpeechActive=false,aiNativeSpeechStream=0,aiNativeSpeechObservedAt=0,aiNativeSpeechDeadline=0,aiNativeSpeechWallAt=0,aiNativeSpeechProducerAt=0;
function aiVoiceSession(){if(!aiVoiceSessionId){aiVoiceSessionId=crypto.randomUUID().replaceAll('-','');document.documentElement.dataset.aiVoiceSession=aiVoiceSessionId;}return aiVoiceSessionId;}
function aiVoiceClock(){return globalThis.performance?.now?.()??Date.now();}
function aiVoiceState(){if(!aiVisible||aiView!=='assistant'||document.hidden)return {listening:false,speaking:'none'};const now=aiVoiceClock(),wall=Date.now(),record=aiRecording,sampleAge=record?now-record.lastSampleAt:NaN,sampleWallAge=record?wall-record.lastSampleWall:NaN;const listening=!!(record&&record.capturing&&sampleAge>=0&&sampleAge<2000&&sampleWallAge>=0&&sampleWallAge<2000&&record.context.state==='running'&&record.stream.getTracks().some(t=>t.kind!=='video'&&t.readyState==='live'&&!t.muted));let speaking='none';if(aiSpeechKind==='browser_tts'&&aiBrowserSpeechActive&&aiSpeechUtterance&&globalThis.speechSynthesis?.speaking===true&&!globalThis.speechSynthesis?.paused)speaking='browser_tts';if(aiSpeechKind==='windows_sapi'&&aiNativeSpeechActive&&now>=aiNativeSpeechObservedAt&&now<aiNativeSpeechDeadline&&wall>=aiNativeSpeechWallAt)speaking='windows_sapi';return {listening,speaking};}
async function aiVoiceDrain(){if(aiVoicePosting)return;aiVoicePosting=true;try{while(aiVoicePending){const body=aiVoicePending;aiVoicePending=null;try{await api('/api/master/voice-activity',body);}catch(_){/* Best effort; the server's five-second lease expires independently. */}}}finally{aiVoicePosting=false;}}
function aiVoiceObserve(heartbeat=false){const value=aiVoiceState(),active=value.listening||value.speaking!=='none',key=String(value.listening)+':'+value.speaking;if(active&&aiVoiceTimer===null)aiVoiceTimer=setInterval(()=>aiVoiceObserve(true),1000);if(!active&&aiVoiceTimer!==null){clearInterval(aiVoiceTimer);aiVoiceTimer=null;}if(!heartbeat&&key===aiVoiceLastState)return;if(!active&&!aiVoiceSessionId)return;aiVoiceLastState=key;if(aiVoiceSequence>=Number.MAX_SAFE_INTEGER){aiVoiceSessionId='';aiVoiceSequence=0;}aiVoicePending={sessionId:aiVoiceSession(),sequence:++aiVoiceSequence,observedAt:new Date().toISOString(),listening:value.listening,speaking:value.speaking};void aiVoiceDrain();}
function aiNativeSpeechStatus(value){if(value?.type!=='interlinked-ai-speech-status'||value.sessionId!==aiVoiceSessionId||value.generation!==aiSpeechGeneration||aiSpeechKind!=='windows_sapi'||!Number.isSafeInteger(value.streamId)||value.streamId<=0||typeof value.speaking!=='boolean')return;if(aiNativeSpeechStream&&aiNativeSpeechStream!==value.streamId)return;const producerAt=Date.parse(value.observedAt),wall=Date.now(),age=wall-producerAt;if(!Number.isFinite(age)||age<0||producerAt<aiNativeSpeechProducerAt||(value.speaking&&age>=2000))return;aiNativeSpeechProducerAt=producerAt;aiNativeSpeechStream=value.streamId;aiNativeSpeechActive=value.speaking&&aiVisible&&!document.hidden&&aiView==='assistant'&&$('ai-speak').checked;aiNativeSpeechObservedAt=aiVoiceClock();aiNativeSpeechWallAt=wall;aiNativeSpeechDeadline=aiNativeSpeechObservedAt+Math.max(0,2000-age);aiVoiceObserve();}
function aiStopSpeech(){aiSpeechGeneration++;aiSpeechKind='none';aiSpeechUtterance=null;aiBrowserSpeechActive=false;aiNativeSpeechActive=false;aiNativeSpeechStream=0;aiNativeSpeechProducerAt=0;globalThis.speechSynthesis?.cancel?.();if(aiVoiceSessionId)globalThis.chrome?.webview?.postMessage({type:'interlinked-ai-speech',action:'stop',sessionId:aiVoiceSessionId,generation:aiSpeechGeneration});aiVoiceObserve();}
function aiSpeak(text){if(!aiVisible||document.hidden||aiView!=='assistant'||!text||!$('ai-speak').checked)return;aiStopSpeech();const generation=++aiSpeechGeneration,sessionId=aiVoiceSession(),voiceId=$('ai-voice').value;if(voiceId.startsWith('native:')){aiSpeechKind='windows_sapi';globalThis.chrome?.webview?.postMessage({type:'interlinked-ai-speech',action:'speak',sessionId,generation,voice:voiceId.slice(7),text:String(text).slice(0,16000)});return;}const voice=globalThis.speechSynthesis?.getVoices?.().find(v=>v.localService&&v.voiceURI===voiceId);if(!voice){$('ai-mic-status').textContent='Installed voice unavailable.';return;}const utterance=new SpeechSynthesisUtterance(String(text).slice(0,16000));utterance.voice=voice;aiSpeechKind='browser_tts';aiSpeechUtterance=utterance;const current=()=>generation===aiSpeechGeneration&&aiSpeechUtterance===utterance;const active=()=>{if(current()&&aiVisible&&!document.hidden&&aiView==='assistant'&&$('ai-speak').checked){aiBrowserSpeechActive=true;aiVoiceObserve();}};const inactive=()=>{if(current()){aiBrowserSpeechActive=false;aiVoiceObserve();}};utterance.onstart=active;utterance.onresume=active;utterance.onpause=inactive;utterance.onend=inactive;utterance.onerror=inactive;try{globalThis.speechSynthesis.speak(utterance);}catch(error){inactive();throw error;}}
async function aiStartRecording(){if(aiRecording||aiRecordStarting||document.hidden)return;if(!navigator.mediaDevices?.getUserMedia)throw new Error('Microphone capture is unavailable in this browser. Attach a WAV recording instead.');aiStopSpeech();aiRecordStarting=true;const generation=++aiRecordGeneration;document.documentElement.dataset.aiMicrophoneArmed='true';let stream=null,context=null;try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});if(generation!==aiRecordGeneration||!aiVisible||document.hidden||aiView!=='assistant'){stream.getTracks().forEach(t=>t.stop());return;}context=new AudioContext({sampleRate:16000});await context.resume();if(generation!==aiRecordGeneration||!aiVisible||document.hidden||aiView!=='assistant'){stream.getTracks().forEach(t=>t.stop());await context.close();return;}const source=context.createMediaStreamSource(stream),processor=context.createScriptProcessor(4096,1,1),gain=context.createGain();gain.gain.value=0;const record={stream,context,source,processor,gain,chunks:[],samples:0,timer:null,capturing:false,lastSampleAt:0,lastSampleWall:0,cleanup:[]};const captureLost=()=>{if(aiRecording!==record)return;record.capturing=false;aiVoiceObserve();};const stopLost=()=>{if(aiRecording===record)aiStopRecording(false).catch(aiError);};for(const track of stream.getTracks()){track.addEventListener?.('ended',stopLost);track.addEventListener?.('mute',captureLost);record.cleanup.push(()=>{track.removeEventListener?.('ended',stopLost);track.removeEventListener?.('mute',captureLost);});}const contextChanged=()=>{if(context.state!=='running')captureLost();if(context.state==='closed')stopLost();};context.addEventListener?.('statechange',contextChanged);record.cleanup.push(()=>context.removeEventListener?.('statechange',contextChanged));processor.onaudioprocess=event=>{if(aiRecording!==record)return;try{const data=new Float32Array(event.inputBuffer.getChannelData(0));if(document.hidden||context.state!=='running'||!stream.getTracks().some(t=>t.kind!=='video'&&t.readyState==='live'&&!t.muted)){captureLost();return;}if(data.length){record.capturing=true;record.lastSampleAt=aiVoiceClock();record.lastSampleWall=Date.now();aiVoiceObserve();}record.chunks.push(data);record.samples+=data.length;if(record.samples>=context.sampleRate*60)aiStopRecording(true).catch(aiError);}catch(error){stopLost();aiError(error);}};source.connect(processor);processor.connect(gain);gain.connect(context.destination);aiRecording=record;record.timer=setTimeout(()=>aiStopRecording(true).catch(aiError),60000);$('ai-microphone').textContent='Finish recording';$('ai-microphone').setAttribute('aria-pressed','true');$('ai-mic-status').textContent='Recording · maximum 60 seconds. Finish to attach, then Send.';}catch(error){stream?.getTracks().forEach(t=>t.stop());if(context)await context.close();throw error;}finally{aiRecordStarting=false;delete document.documentElement.dataset.aiMicrophoneArmed;}}
async function aiStopRecording(attach){aiRecordGeneration++;const record=aiRecording;aiRecording=null;delete document.documentElement.dataset.aiMicrophoneArmed;aiVoiceObserve();if(!record)return;clearTimeout(record.timer);for(const cleanup of record.cleanup)cleanup();record.processor.onaudioprocess=null;record.source.disconnect();record.processor.disconnect();record.gain.disconnect();record.stream.getTracks().forEach(t=>t.stop());const rate=record.context.sampleRate;await record.context.close();$('ai-microphone').textContent='Record voice';$('ai-microphone').setAttribute('aria-pressed','false');$('ai-mic-status').textContent=attach?'Preparing recording…':'Recording stopped.';if(attach&&record.samples){const file=new File([aiWav(record.chunks,rate)],'Voice-'+new Date().toISOString().replaceAll(':','-')+'.wav',{type:'audio/wav'});aiAttachments.push(await aiUpload(file));$('ai-audio-kind').value='speech';aiRenderAttachments();$('ai-mic-status').textContent='Recording attached. Review and Send.';}}
// BEGIN AI HISTORY
let aiHistoryState=null,aiHistoryPollBusy=false,aiHistoryInitialized=false,aiHistoryContextReset=false,aiHistorySourceSequence=0,aiHistorySearchSequence=0,aiHistorySourceValue=null,aiHistorySelection=new Map(),aiHandoffText='',aiHandoffSequence=0,aiMemoryRenderSignature='',aiHistoryPolicySignature='',aiHistoryReviewGeneration='';
let aiMemoryPage=null,aiMemoryAfter=null,aiMemoryPageBusy=false,aiMemoryPageSequence=0;
let aiMemoryRecoveryBusy=false;
let aiHistoryPollPending=null,aiHistoryPollAgain=false;
function aiHistorySourceId(source){return String(source?.sourceId||source?.source_id||source?.id||'');}
function aiHistorySourceLabel(source){const meta=source?.metadata||{};return [source?.title||source?.path||'Archive source',source?.kind?aiWords(source.kind):'',source?.timestamp||source?.date||'',meta.speaker||'',meta.page?'Page '+meta.page:'',meta.slide?'Slide '+meta.slide:'',meta.sheet?'Sheet '+meta.sheet:'',meta.row?'Row '+meta.row:'',Array.isArray(meta.cells)&&meta.cells.length?'Cells '+meta.cells.join(', '):'',meta.gitPath||''].filter(Boolean).join(' · ');}
function aiHistorySourceRequest(reference){
 const source=typeof reference==='string'?{id:reference}:reference,request={id:aiHistorySourceId(source)};if(!request.id)throw new Error('A source identity is required.');
 if(source.revision!==undefined){if(typeof source.revision!=='string'||!/^[a-f0-9]{64}$/.test(source.revision))throw new Error('This source citation has an invalid revision.');request.revision=source.revision;}
 if(source.start!==undefined||source.end!==undefined){if(!Number.isSafeInteger(source.start)||!Number.isSafeInteger(source.end)||source.start<0||source.end<=source.start)throw new Error('This source citation has invalid passage offsets.');request.start=source.start;request.end=source.end;}
 return request;
}
function aiHistorySourcePassage(source,request){
 if(aiHistorySourceId(source)!==request.id)throw new Error('The returned source identity differs from this citation.');
 if(request.revision&&source.revision!==request.revision)throw new Error('The requested source revision is unavailable.');
 const text=String(source.text??source.excerpt??'');if(request.start===undefined)return {text,label:'Full source'};
 const passage=[];let offset=0;for(const character of text){if(offset>=request.start)passage.push(character);offset++;if(offset===request.end)break;}
 if(offset<request.end)throw new Error('The cited passage falls outside this source revision.');
 return {text:passage.join(''),label:'Passage characters '+(request.start+1)+'–'+request.end+' · Unicode offsets ['+request.start+', '+request.end+')'};
}
function aiHistoryGeneration(){const value=aiHistoryState?.generation;return typeof value==='string'?value:String(value?.id||value?.generationId||value?.generation||'');}
function aiHistoryOnline(){return !!aiHistoryState&&aiHistoryState.available!==false&&aiHistoryState?.offline!==true;}
function aiHistoryReadScope(){return aiHistoryReviewGeneration?{generation:aiHistoryReviewGeneration}:{};}
function aiHistorySetReviewGeneration(generation){aiHistoryReviewGeneration=String(generation||'');aiHistorySourceSequence++;aiHistorySearchSequence++;aiHistorySelection.clear();aiHistoryInvalidateHandoff();$('ai-history-source-view').hidden=true;$('ai-history-results').replaceChildren();aiHistoryRender();}
async function aiHistoryOpenActiveSource(reference){if(aiHistoryReviewGeneration)aiHistorySetReviewGeneration('');await aiHistoryOpenSource(reference);}
function aiHistoryBusy(status){return ['QUEUED','RUNNING','STAGING','COPYING','PARSING','INDEXING','EMBEDDING','BUILDING','VERIFYING','SYNCING','PREPARING'].includes(String(status||'').toUpperCase());}
function aiHistoryChatOptions(){return {historyEnabled:!!aiHistoryGeneration()&&$('ai-history-enabled').checked,historyProject:$('ai-history-project').value.trim(),historyContextReset:aiHistoryContextReset};}
function aiHistoryScopeChanged(){aiHistoryInitialized=true;aiHistoryContextReset=true;aiStopSpeech();$('ai-history-context-note').textContent='The next message starts a fresh model context. Your visible transcript is retained.';}
function aiHistoryInvalidateHandoff(){aiHandoffSequence++;aiHandoffText='';$('ai-handoff-preview').value='';$('ai-handoff-copy').disabled=true;$('ai-handoff-status').textContent='Prepare a new preview after changing the note or sources.';}
function aiHistorySelect(source,selected){const id=aiHistorySourceId(source);if(!id||selected&&aiHistoryReviewGeneration)return;if(selected)aiHistorySelection.set(id,source);else aiHistorySelection.delete(id);aiHistoryInvalidateHandoff();aiHistoryRenderSelection();}
function aiHistoryRenderSelection(){
 const rows=[...aiHistorySelection.values()],restricted=rows.some(row=>row.memory_allowed===false||row.memoryAllowed===false),count=rows.length;
 $('ai-history-selection').textContent=aiHistoryReviewGeneration?'Review passages and exclusions before activation. Handoff and memory selection become available after activation.':count?count+' source'+(count===1?'':'s')+' selected for memory or handoff.':'No sources selected.';
 $('ai-memory-source-note').textContent=!count?'Select supporting sources in History first.':restricted?'This selection includes a source that cannot supply standing memory.':count+' supporting source'+(count===1?'':'s')+' selected.';
 $('ai-memory-propose').disabled=!count||restricted||!aiHistoryOnline()||!!aiHistoryReviewGeneration;$('ai-handoff-prepare').disabled=!count||!!aiHistoryReviewGeneration;
 $('ai-handoff-sources').replaceChildren();for(const row of rows){const item=aiElement('div','ai-history-selected'),remove=aiElement('button','quiet small','Remove');remove.type='button';remove.addEventListener('click',()=>{aiHistorySelect(row,false);});item.append(aiElement('span','',aiHistorySourceLabel(row)),remove);$('ai-handoff-sources').append(item);}
}
function aiHistoryRender(){
 const s=aiHistoryState||{},generation=aiHistoryGeneration(),last=s.lastSync||s.last_sync||s.syncedAt,stamp=last?String(last):'Never synced';
 const policy=JSON.stringify([generation,s.exclusions||[]]);if(aiHistoryPolicySignature&&policy!==aiHistoryPolicySignature){aiHistorySourceSequence++;aiHistorySearchSequence++;aiHistorySelection.clear();aiHistoryInvalidateHandoff();$('ai-history-source-view').hidden=true;$('ai-history-results').replaceChildren();aiHistoryScopeChanged();}aiHistoryPolicySignature=policy;
 $('ai-history-banner').textContent=s.available===false?'History unavailable · '+String(s.error||'The archive service is not connected.'):generation?(s.offline?'Offline history · ':'Local history · ')+(s.generationCreatedAt||s.generation?.createdAt||generation.slice(0,12))+' · Last sync: '+stamp:'No active history archive · Import and review a snapshot in History.';
 $('ai-history-banner').classList.toggle('offline',!!s.offline);$('ai-history-enabled').disabled=!generation;
 if(generation&&!aiHistoryInitialized){$('ai-history-enabled').checked=true;aiHistoryInitialized=true;}
 if(!aiHistoryContextReset)$('ai-history-context-note').textContent=generation?($('ai-history-enabled').checked?'History and reviewed memories are used locally.':'History and memory retrieval are off.'):'Activate a verified archive to use history.';
 $('ai-history-project-options').replaceChildren();for(const p of s.projects||[]){const option=aiElement('option');option.value=typeof p==='string'?p:String(p.id||p.name||p.label||'');$('ai-history-project-options').append(option);}
 if(aiHistoryReviewGeneration===generation)aiHistoryReviewGeneration='';
 $('ai-history-review-status').textContent=aiHistoryReviewGeneration?'Reviewing prepared snapshot · '+aiHistoryReviewGeneration+(s.offline?' · MSI is offline; reconnect to continue review.':' · Search and exclude passages before activation.'):'Browsing the active local archive.';
 $('ai-history-review-return').hidden=!aiHistoryReviewGeneration;
 $('ai-history-import').disabled=!aiHistoryOnline();$('ai-history-search').disabled=aiHistoryReviewGeneration?!aiHistoryOnline():!generation;
 const peerStatus=('peerStateStale' in s||s.lastPeerObservedAt||s.peerRefreshInFlight)?'MSI job status: '+(s.peerStateStale?'stale':s.lastPeerObservedAt?'observed':'not yet observed')+' · Last observation: '+(s.lastPeerObservedAt||'None')+(s.peerRefreshInFlight?' · Refreshing…':''):'';
 $('ai-history-jobs').replaceChildren();if(peerStatus)$('ai-history-jobs').append(aiElement('p','master-help',peerStatus));
 for(const job of s.jobs||[]){
  const item=aiElement('article','ai-list-row'),progress=job.progress&&typeof job.progress==='object'?job.progress:job,phase=String(job.phase||progress.phase||''),counter=key=>Number.isSafeInteger(progress[key])&&progress[key]>=0?progress[key].toLocaleString('en-US'):null,metrics=[];
  const pair=(done,unit)=>{const value=counter(done),total=counter('total');if(value!==null)metrics.push(value+(total!==null?' / '+total:'')+' '+unit);};
  if(['intake','transferring'].includes(phase))pair('completed','files');
  else if(['embedding','resuming'].includes(phase))pair('records','chunks');
  else if(phase==='syncing')pair('bytes','bytes');
  else if(phase==='proposing')pair('completed','sources');
  else if(counter('records')!==null)metrics.push(counter('records')+' records');
  if(counter('files')!==null&&!['intake','transferring'].includes(phase))metrics.push(counter('files')+' files');
  if(counter('bytes')!==null&&phase!=='syncing')metrics.push(counter('bytes')+' bytes');
  item.append(aiElement('strong','',aiWords(job.action||job.kind||'Import')+' · '+aiWords(job.status)),aiElement('p','master-help','Phase: '+(aiWords(phase)||'Not reported')));
  if(metrics.length)item.append(aiElement('p','master-help',metrics.join(' · ')));
  if(job.message||job.error||job.errorCode)item.append(aiElement('p','master-help',job.message||job.error||job.errorCode));
  if(aiHistoryBusy(job.status)){const cancel=aiElement('button','quiet small','Cancel');cancel.type='button';cancel.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/cancel',{id:job.id});await aiHistoryPoll();}));item.append(cancel);}$('ai-history-jobs').append(item);
 }
 const ready=s.preparedGenerations||s.readyGenerations||s.generations||[];$('ai-history-ready').replaceChildren();for(const row of ready){const id=typeof row==='string'?row:row.id||row.generationId||row.generation;if(!id||id===generation||row.status&& !['READY','PREPARED','REVIEW_READY'].includes(String(row.status).toUpperCase()))continue;const card=aiElement('article','ai-list-row'),review=aiElement('button','quiet small','Review passages and exclusions'),activate=aiElement('button','quiet small','Activate this reviewed snapshot');review.type=activate.type='button';review.disabled=activate.disabled=!aiHistoryOnline();review.addEventListener('click',()=>{aiHistorySetReviewGeneration(id);$('ai-history-query').focus?.();});activate.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/activate',{generation:id});aiHistorySetReviewGeneration('');await aiHistoryPoll();aiHistoryScopeChanged();}));card.append(aiElement('strong','','Verified snapshot ready'),aiElement('small','',String(id)),review,activate);$('ai-history-ready').append(card);}
 const reviewed=ready.find(row=>(typeof row==='string'?row:row.id||row.generationId||row.generation)===aiHistoryReviewGeneration),coverage=reviewed?.coverage||s.coverage||s.generation?.coverage;
 $('ai-history-coverage').textContent=coverage?JSON.stringify(coverage,null,2):'No import coverage yet.';
 $('ai-history-exclusions').replaceChildren();const exclusions=Array.isArray(s.exclusions)?s.exclusions.filter(row=>typeof row==='string'||row.enabled!==false&&row.enabled!==0):Object.entries(s.exclusions||{}).filter(([,v])=>v!==false&&v?.enabled!==false).map(([id,value])=>({id,...(typeof value==='object'?value:{})}));
 for(const row of exclusions){const id=typeof row==='string'?row:row.id||row.sourceId;if(!id)continue;const item=aiElement('div','ai-history-selected'),restore=aiElement('button','quiet small','Include again');restore.type='button';restore.disabled=(!generation&&!aiHistoryReviewGeneration)||!!aiHistoryReviewGeneration&&!aiHistoryOnline();restore.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/exclude',{id,enabled:false,...aiHistoryReadScope()});aiHistoryScopeChanged();await aiHistoryPoll();}));item.append(aiElement('span','',row.title||String(id)),restore);$('ai-history-exclusions').append(item);}
 if(!exclusions.length)$('ai-history-exclusions').append(aiElement('p','master-help','No excluded sources.'));
 $('ai-history-sync-status').textContent=s.offline?'MSI unavailable. Using the last verified local copy.':s.available===false?'Archive service unavailable.':'Archive connection available · '+(s.role==='primary'?'MSI primary':'THIS-MACHINE replica');
 if(peerStatus)$('ai-history-sync-status').textContent+=' · '+peerStatus;
 const sync=s.exclusionSync||{},pending=Number(sync.pendingCount)||0,unresolved=Number(sync.unresolvedCount)||0;
 $('ai-history-policy-status').textContent=unresolved?'Some privacy changes need review on MSI. '+unresolved+' unresolved; '+pending+' pending.':sync.capabilityRequired?'Update the paired history service to confirm privacy changes. '+pending+' pending; local restrictions remain active.':pending?pending+' privacy changes waiting for MSI confirmation. Local restrictions remain active.':s.exclusionSync?'No pending privacy changes.':'Privacy confirmation unavailable.';
 $('ai-history-sync-generation').textContent='Snapshot: '+(generation||'None')+' · Last sync: '+stamp;$('ai-history-sync').disabled=s.available===false;$('ai-history-sync-detail').textContent=s.sync?JSON.stringify(s.sync,null,2):s.notice||'';
 aiHistoryRenderMemories();aiHistoryRenderGateway();aiHistoryRenderSelection();aiHistoryRenderProfiles();
}
function aiMemoryScope(value){return JSON.stringify([value?.generation?.generationId||value?.generationId,value?.exclusionSync?.localPolicyRevision]);}
async function aiHistoryPollOnce(){try{const previous=aiHistoryState,value=await api('/api/ai/history/state');if(stopped)return;aiHistoryState=value;if(previous&&aiMemoryScope(previous)!==aiMemoryScope(aiHistoryState))aiLatestMemories();if(aiMemoryPage&&aiView==='memories'&&!aiMemoryPageBusy){try{await aiHistoryLoadMemoryPage(aiMemoryAfter,false);}catch(error){aiLatestMemories();aiError(error);}}aiHistoryRender();}catch(error){if(stopped)return;aiHistoryState={...(aiHistoryState||{}),available:false,error:String(error?.message||error)};aiHistoryRender();}}
async function aiHistoryPoll({background=false}={}){
 if(stopped||!$('ai-history-banner')||background&&document.hidden)return;
 if(aiHistoryPollPending){if(!background)aiHistoryPollAgain=true;return aiHistoryPollPending;}
 aiHistoryPollBusy=true;aiHistoryPollPending=(async()=>{do{aiHistoryPollAgain=false;await aiHistoryPollOnce();}while(aiHistoryPollAgain&&!stopped);})().finally(()=>{aiHistoryPollPending=null;aiHistoryPollBusy=false;});
 return aiHistoryPollPending;
}
async function aiHistoryLoadMemoryPage(after=(aiMemoryPage||aiHistoryState?.memoryPage)?.nextCursor,render=true){
 if(!after||aiMemoryPageBusy)return;const sequence=++aiMemoryPageSequence;aiMemoryPageBusy=true;if(render)aiHistoryRenderMemories();
 try{const page=await api('/api/ai/history/memories',{after,limit:100});if(sequence!==aiMemoryPageSequence)return;aiMemoryPage=page;aiMemoryAfter=after;}
 catch(error){if(sequence===aiMemoryPageSequence)throw error;}
 finally{if(sequence===aiMemoryPageSequence){aiMemoryPageBusy=false;if(render)aiHistoryRenderMemories();}}
}
function aiLatestMemories(){aiMemoryPageSequence++;aiMemoryPageBusy=false;aiMemoryPage=null;aiMemoryAfter=null;aiHistoryRenderMemories();}
async function aiHistoryImport(){const source=$('ai-history-source-path').value.trim(),hash=$('ai-history-manifest').value.trim().toLowerCase();if(!source)throw new Error('Enter the export folder or ZIP path.');if(hash&&!/^[a-f0-9]{64}$/.test(hash))throw new Error('The expected manifest hash must contain 64 hexadecimal characters.');$('ai-history-import').disabled=true;try{await api('/api/ai/history/import',{source,...(hash?{expectedManifestSha256:hash}:{})});await aiHistoryPoll();}finally{$('ai-history-import').disabled=!aiHistoryOnline();}}
async function aiHistorySearch(){const query=$('ai-history-query').value.trim(),dateFrom=$('ai-history-date-from').value.trim(),dateTo=$('ai-history-date-to').value.trim();if(!query)throw new Error('Enter a history question or search terms.');if(dateFrom&&dateTo&&dateFrom>dateTo)throw new Error('Choose a From date on or before the Through date.');const sequence=++aiHistorySearchSequence;const value=await api('/api/ai/history/search',{query,project:$('ai-history-search-project').value.trim(),limit:12,...(dateFrom?{dateFrom}:{}),...(dateTo?{dateTo}:{}),...aiHistoryReadScope()});if(sequence!==aiHistorySearchSequence)return;const rows=Array.isArray(value)?value:value.results||value.sources||value.hits||[];aiHistoryRenderResults(rows);}
function aiHistoryRenderResults(rows){
 $('ai-history-results').replaceChildren();for(const source of rows){const id=aiHistorySourceId(source);if(!id)continue;const card=aiElement('article','ai-search-hit'),select=aiElement('input'),label=aiElement('label'),open=aiElement('button','quiet small','Open source');select.type='checkbox';select.checked=aiHistorySelection.has(id);select.disabled=!!aiHistoryReviewGeneration;select.addEventListener('change',()=>aiHistorySelect(source,select.checked));label.append(select,aiElement('span','',aiHistorySourceLabel(source)));open.type='button';open.addEventListener('click',aiAction(()=>aiHistoryOpenSource(source)));card.append(label,aiElement('p','',source.excerpt||source.text||''));if(source.memory_allowed===false||source.memoryAllowed===false)card.append(aiElement('small','ai-citation','Searchable source · excluded from standing memory'));card.append(open);$('ai-history-results').append(card);}
 if(!$('ai-history-results').children.length)$('ai-history-results').append(aiElement('p','master-help','No matching included history.'));
}
async function aiHistoryOpenSource(reference){
 const sequence=++aiHistorySourceSequence,scope=aiHistoryReadScope();$('ai-history-source-view').hidden=true;const request=aiHistorySourceRequest(reference),id=request.id,value=await api('/api/ai/history/source',{...request,...scope});if(sequence!==aiHistorySourceSequence)return;
 const source=Object.prototype.hasOwnProperty.call(value,'source')?value.source:value;if(!source||typeof source!=='object'||!aiHistorySourceId(source)){$('ai-history-source-view').hidden=true;throw new Error('This source is unavailable or excluded. Search the current archive again.');}
 const passage=aiHistorySourcePassage(source,request);aiHistorySourceValue=source;$('ai-history-source-view').hidden=false;$('ai-history-source-title').textContent=source.title||'Archive source';$('ai-history-source-meta').textContent=[aiHistorySourceLabel(source),source.revision?'Revision '+source.revision.slice(0,12):'',passage.label].filter(Boolean).join(' · ');$('ai-history-source-text').textContent=passage.text;$('ai-history-source-actions').replaceChildren();
 const select=aiElement('button','quiet small','Select for handoff or memory'),exclude=aiElement('button','quiet small',source.kind==='conversation'?'Exclude this conversation':['group','group_chat'].includes(source.kind)?'Exclude this group chat':'Exclude this source');select.type=exclude.type='button';select.disabled=!!scope.generation;exclude.disabled=!!scope.generation&&!aiHistoryOnline();select.addEventListener('click',()=>{aiHistorySelect(source,true);});
 exclude.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/exclude',{id:aiHistorySourceId(source)||id,enabled:true,...scope});aiHistorySelect(source,false);aiHistoryScopeChanged();$('ai-history-source-view').hidden=true;$('ai-history-results').replaceChildren();await aiHistoryPoll();}));
 const projectLabel=aiElement('label','ai-source-project','Project tag'),projectInput=aiElement('input'),saveProject=aiElement('button','quiet small','Save project tag');projectInput.value=source.project||'';projectInput.maxLength=200;projectInput.setAttribute('list','ai-history-project-options');projectInput.setAttribute('aria-label','Source project tag');saveProject.type='button';saveProject.disabled=!aiHistoryOnline()||!!scope.generation;projectInput.disabled=!!scope.generation;projectLabel.append(projectInput);
 saveProject.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/project',{id:aiHistorySourceId(source)||id,project:projectInput.value.trim()});aiHistoryScopeChanged();await aiHistoryPoll();await aiHistoryOpenSource(request);}));$('ai-history-source-actions').append(select,exclude,projectLabel,saveProject);
 if(request.start!==undefined){const full=aiElement('button','quiet small','Show full source');full.type='button';full.addEventListener('click',aiAction(()=>aiHistoryOpenSource({id,...(source.revision?{revision:source.revision}:{})})));$('ai-history-source-actions').append(full);}
 $('ai-history-source-view').scrollIntoView?.({block:'nearest'});
}
function aiHistoryRenderMemories(){
 const rows=aiMemoryPage?.memories||aiHistoryState?.memories||[],page=aiMemoryPage||aiHistoryState?.memoryPage||{};
 $('ai-memories-count').textContent=rows.length+' shown'+(Number.isSafeInteger(page.total)?' of '+page.total:'');$('ai-memories-next').disabled=aiMemoryPageBusy||!page.nextCursor;$('ai-memories-first').disabled=aiMemoryPageBusy||!aiMemoryPage;
 const signature=JSON.stringify([rows,aiHistoryOnline()]);if(signature===aiMemoryRenderSignature)return;aiMemoryRenderSignature=signature;$('ai-memory-list').replaceChildren();for(const memory of rows){const card=aiElement('article','ai-list-row'),text=aiElement('textarea'),buttons=aiElement('div','ai-compose-actions'),approve=aiElement('button','quiet small','Approve'),save=aiElement('button','quiet small','Save edit'),reject=aiElement('button','quiet small',String(memory.status).toLowerCase()==='approved'?'Deactivate':'Reject');text.value=memory.text||'';text.maxLength=4000;text.setAttribute('aria-label','Memory proposal');approve.type=save.type=reject.type='button';approve.disabled=!aiHistoryOnline()||['approved','blocked','stale'].includes(String(memory.effectiveStatus||memory.status).toLowerCase());save.disabled=true;reject.disabled=!aiHistoryOnline();text.addEventListener('input',()=>{approve.disabled=true;save.disabled=!aiHistoryOnline();});
  for(const [button,action] of [[approve,'approve'],[save,'edit'],[reject,'reject']])button.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/memory-review',{id:memory.id,action,...(action==='edit'?{text:text.value.trim()}:{})});await aiHistoryPoll();}));
  card.append(aiElement('strong','',aiWords(memory.effectiveStatus||memory.status||'proposed')+' · '+aiWords(memory.category||'memory')),text);for(const reference of memory.sources||memory.sourceIds||memory.source_ids||[]){const open=aiElement('button','quiet small','Supporting source');open.type='button';open.addEventListener('click',aiAction(async()=>{aiTab('history');await aiHistoryOpenActiveSource(reference);}));card.append(open);}buttons.append(save,approve,reject);card.append(buttons);$('ai-memory-list').append(card);
 }if(!rows.length)$('ai-memory-list').append(aiElement('p','master-help',page.nextCursor?'No proposals on this page. Continue to the next page.':'No memory proposals on this page. Select eligible history sources to propose one.'));
}
async function aiHistoryProposeMemory(){if(aiHistoryReviewGeneration)throw new Error('Activate the reviewed snapshot before selecting memory sources.');const rows=[...aiHistorySelection.values()];if(!rows.length||rows.some(row=>row.memory_allowed===false||row.memoryAllowed===false))throw new Error('Select sources that are eligible for standing memory.');const text=$('ai-memory-text').value.trim();if(!text)throw new Error('Enter a memory proposal.');await api('/api/ai/history/memory-propose',{text,sourceIds:[...aiHistorySelection.keys()],category:$('ai-memory-category').value,project:$('ai-memory-project').value.trim()});$('ai-memory-text').value='';await aiHistoryPoll();}
async function aiHistoryPrepareHandoff(){if(aiHistoryReviewGeneration)throw new Error('Activate the reviewed snapshot before selecting handoff sources.');if(!aiHistorySelection.size)throw new Error('Select source passages in History first.');const sequence=++aiHandoffSequence;$('ai-handoff-copy').disabled=true;const value=await api('/api/ai/history/handoff',{sourceIds:[...aiHistorySelection.keys()],text:$('ai-handoff-note').value.trim()});if(sequence!==aiHandoffSequence)return;const text=typeof value==='string'?value:value.text;if(typeof text!=='string'||!text)throw new Error('The handoff service returned an empty preview.');aiHandoffText=text;$('ai-handoff-preview').value=text;$('ai-handoff-status').textContent='Review this exact text before copying. Nothing has been shared.';$('ai-handoff-copy').disabled=false;}
async function aiHistoryCopyHandoff(){if(!aiHandoffText||$('ai-handoff-preview').value!==aiHandoffText)throw new Error('Prepare a new preview before copying.');if(!navigator.clipboard?.writeText){$('ai-handoff-status').textContent='Clipboard access is unavailable. Select and copy the exact preview manually.';$('ai-handoff-preview').focus?.();$('ai-handoff-preview').select?.();return;}await navigator.clipboard.writeText(aiHandoffText);$('ai-handoff-status').textContent='Copied. Paste into ChatGPT or Codex when you choose.';}
function aiHistoryRenderGateway(){const gateway=aiHistoryState?.gateway||{};$('ai-gateway-status').textContent=gateway.error?String(gateway.error):gateway.available===false?'Local client gateway is unavailable.':gateway.endpoint||gateway.baseUrl||(gateway.available===true?'Gateway endpoint: http://127.0.0.1:47444/v1':'Local client gateway is not configured.');$('ai-gateway-create').disabled=aiHistoryState?.available===false||gateway.available===false;$('ai-gateway-clients').replaceChildren();for(const client of gateway.clients||[]){const item=aiElement('div','ai-history-selected'),revoke=aiElement('button','quiet small','Revoke key');revoke.type='button';revoke.disabled=!!client.revoked;revoke.addEventListener('click',aiAction(async()=>{await api('/api/ai/history/gateway-revoke',{id:client.id});$('ai-gateway-key').textContent='';$('ai-gateway-key').hidden=true;await aiHistoryPoll();}));item.append(aiElement('span','',client.label||client.id),revoke);$('ai-gateway-clients').append(item);}}
async function aiHistoryCreateGateway(){const label=$('ai-gateway-label').value.trim();if(!label)throw new Error('Enter a label for this client.');const value=await api('/api/ai/history/gateway-create',{label,historyEnabled:$('ai-gateway-history').checked,project:$('ai-gateway-project').value.trim()});const key=value.key||value.apiKey;if(typeof key!=='string'||!key)throw new Error('The gateway did not return a client key.');$('ai-gateway-key').textContent=key;$('ai-gateway-key').hidden=false;$('ai-gateway-key-note').textContent='Copy this key into your local client. It is shown only when created.';await aiHistoryPoll();}
function aiHistoryRenderProfiles(){
 const models=(aiSnapshot?.models||[]).filter(m=>m.eligible!==false&&(m.capabilities||[]).includes('completion'));
 aiSelectOptions('ai-history-calibrate-model',models.map(m=>({value:aiModelKey(m),label:(m.host||m.hostId)+' / '+(m.id||m.name||m.model)})),null);
 const profiles=aiHistoryState?.profiles||[];$('ai-history-profiles').replaceChildren();for(const profile of profiles){const row=aiElement('article','ai-list-row');row.append(aiElement('strong','',String(profile.host||'')+' / '+String(profile.model||'')),aiElement('small','',(profile.passed?'Passed saved history check':'History check did not pass')+' · '+String(profile.passedCases||0)+' / '+String(profile.cases||0)+' · '+String(profile.observedAt||'')));$('ai-history-profiles').append(row);}
 const jobs=aiHistoryState?.jobs||[],running=jobs.some(j=>aiHistoryBusy(j.status));$('ai-history-calibrate').disabled=running||!models.length||aiHistoryState?.available===false;
 const currentPass=profiles.some(p=>p.passed&&models.some(m=>m.host===p.host&&m.digest===p.digest&&!m.stale));$('ai-memory-generate').disabled=!aiHistoryGeneration()||!aiHistoryOnline()||running||!currentPass||aiMemoryRecoveryBusy;
 aiHistoryRenderMemoryRecovery();
 const job=[...jobs].reverse().find(j=>j.kind==='memory-proposals');$('ai-memory-generation-status').textContent=job?'Memory suggestions · '+aiWords(job.status)+' · '+aiWords(job.phase||''):(currentPass?'Generate local proposals, then review each one before use.':'Verify a history model in Sync before generating suggestions.');
}
async function aiHistoryCalibrate(){const choice=aiOverride($('ai-history-calibrate-model').value);if(!choice.host||!choice.model)throw new Error('Choose a local model to verify for history.');await api('/api/ai/history/calibrate',choice);await aiHistoryPoll();}
async function aiHistoryGenerateMemories(){await api('/api/ai/history/memory-generate',{project:$('ai-memory-generate-project').value.trim()});await aiHistoryPoll();}
function aiHistoryRenderMemoryRecovery(){
 const recovery=aiHistoryState?.memoryRecovery||{},ids=[...new Set((recovery.uncertainRequestIds||[]).filter(id=>typeof id==='string'&&/^[a-f0-9]{32}$/.test(id)))].slice(0,20),panel=$('ai-memory-recovery');panel.hidden=!ids.length;panel.replaceChildren();if(!ids.length)return;
 panel.append(aiElement('h3','','Interrupted suggestions'),aiElement('p','master-help',ids.length+' shown'+(Number.isSafeInteger(recovery.total)?' of '+recovery.total:'')+'. These requests may have run without a confirmed result. Keep the original project scope and review each request before retrying.'));
 for(const id of ids){const row=aiElement('div','ai-history-selected'),retry=aiElement('button','quiet small','Retry this request');retry.type='button';retry.disabled=aiMemoryRecoveryBusy||$('ai-memory-generate').disabled;retry.addEventListener('click',aiAction(()=>aiHistoryRetryMemory(id)));row.append(aiElement('code','ai-digest',id),retry);panel.append(row);}
}
async function aiHistoryRetryMemory(id){
 if(aiMemoryRecoveryBusy||$('ai-memory-generate').disabled)throw new Error('Wait for the current work and verify a history model before retrying.');
 if(!/^[a-f0-9]{32}$/.test(id)||!(aiHistoryState?.memoryRecovery?.uncertainRequestIds||[]).includes(id))throw new Error('This interrupted request is no longer listed. Refresh its status before retrying.');
 if(!confirm('This suggestion may already have been generated. Retry this exact request? Another model call may repeat earlier work. Review new proposals before approving them.'))return;
 aiMemoryRecoveryBusy=true;aiHistoryRenderProfiles();
 try{await api('/api/ai/history/memory-generate',{project:$('ai-memory-generate-project').value.trim(),retryUncertainRequestIds:[id]});await aiHistoryPoll();}
 finally{aiMemoryRecoveryBusy=false;aiHistoryRenderProfiles();}
}
// END AI HISTORY
// END AI INTERFACE

// BEGIN AI BINDINGS
if($('ai-view-tab')){
 $('ai-view-tab').addEventListener('click',aiSetView);for(const name of ['assistant','history','memories','sync','handoff','workers','models'])$('ai-tab-'+name).addEventListener('click',()=>aiTab(name));
 $('ai-refresh').addEventListener('click',aiAction(async()=>{await api('/api/ai/refresh',{});await aiPoll();}));
 $('ai-history-enabled').addEventListener('change',aiHistoryScopeChanged);$('ai-history-project').addEventListener('change',aiHistoryScopeChanged);
 $('ai-history-import-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiHistoryImport();}));$('ai-history-search-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiHistorySearch();}));
 $('ai-history-source-close').addEventListener('click',()=>{aiHistorySourceSequence++;$('ai-history-source-view').hidden=true;});
 $('ai-history-review-return').addEventListener('click',()=>aiHistorySetReviewGeneration(''));
 $('ai-memory-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiHistoryProposeMemory();}));$('ai-history-sync').addEventListener('click',aiAction(async()=>{await api('/api/ai/history/sync',{});await aiHistoryPoll();}));
 $('ai-history-calibrate-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiHistoryCalibrate();}));$('ai-memory-generate').addEventListener('click',aiAction(aiHistoryGenerateMemories));
 $('ai-handoff-note').addEventListener('input',aiHistoryInvalidateHandoff);$('ai-handoff-prepare').addEventListener('click',aiAction(aiHistoryPrepareHandoff));$('ai-handoff-copy').addEventListener('click',aiAction(aiHistoryCopyHandoff));
 $('ai-gateway-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiHistoryCreateGateway();}));
 $('ai-chat-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiSend();}));$('ai-search-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await aiSearch();}));
 $('ai-new-chat').addEventListener('click',()=>{aiConversation=null;aiConversationValue=null;aiChatSequence++;$('ai-routing-mode').value='balanced';$('ai-model-override').value='';aiAttachments=[];aiRenderAttachments();aiRenderConversation({title:'Start a conversation',status:'READY',messages:[]});aiStopSpeech();});
 $('ai-files').addEventListener('change',aiAction(async()=>{for(const file of $('ai-files').files){if(aiAttachments.length>=4)throw new Error('Use up to four attachments per message.');aiAttachments.push(await aiUpload(file));if(file.type.startsWith('audio/'))$('ai-audio-kind').value='general';}aiRenderAttachments();$('ai-files').value='';}));
 $('ai-chat-cancel').addEventListener('click',aiAction(async()=>{if(aiConversation){await api('/api/ai/chat/cancel',{id:aiConversation});await aiReadConversation();}aiStopSpeech();}));
 $('ai-task-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();$('ai-task-start').disabled=true;try{await aiStartTask();}finally{$('ai-task-start').disabled=false;}}));$('ai-task-project').addEventListener('change',aiRenderChecks);$('ai-allow-check').addEventListener('change',aiRenderChecks);
 $('ai-task-cancel').addEventListener('click',aiAction(async()=>{await api('/api/ai/tasks/cancel',{id:aiTask});await aiReadTask();}));$('ai-task-apply').addEventListener('click',aiAction(aiApplyTask));
 $('ai-task-reconcile').addEventListener('click',aiAction(aiReconcileTask));$('ai-tasks-next').addEventListener('click',aiAction(aiLoadTaskPage));$('ai-tasks-first').addEventListener('click',aiLatestTasks);
 $('ai-memories-next').addEventListener('click',aiAction(()=>aiHistoryLoadMemoryPage()));$('ai-memories-first').addEventListener('click',aiLatestMemories);
 $('ai-pair-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();await api('/api/ai/pair',{endpoint:$('ai-pair-endpoint').value.trim(),fingerprint:$('ai-pair-fingerprint').value.trim(),code:$('ai-pair-code').value.trim(),expectedIdentity:{computer:$('ai-pair-computer').value.trim(),sid:$('ai-pair-sid').value.trim()}});$('ai-pair-code').value='';await aiPoll();}));
 $('ai-calibrate-form').addEventListener('submit',aiAction(async event=>{event.preventDefault();$('ai-calibrate-run').disabled=true;try{await aiCalibrate();}finally{$('ai-calibrate-run').disabled=false;}}));
 $('ai-microphone').addEventListener('click',aiAction(async()=>{if(aiRecording)await aiStopRecording(true);else await aiStartRecording();}));$('ai-speech-stop').addEventListener('click',()=>{$('ai-speak').checked=false;aiStopSpeech();});$('ai-speak').addEventListener('change',()=>{if(!$('ai-speak').checked)aiStopSpeech();});
 globalThis.speechSynthesis?.addEventListener?.('voiceschanged',aiLoadVoices);globalThis.chrome?.webview?.addEventListener('message',event=>{if(event.data?.type==='interlinked-ai-voices'){aiNativeVoices=Array.isArray(event.data.voices)?event.data.voices:[];aiLoadVoices();}if(event.data?.type==='interlinked-ai-speech-status')aiNativeSpeechStatus(event.data);if(event.data?.type==='interlinked-ai-speech-error')$('ai-mic-status').textContent=String(event.data.error||'Installed voice unavailable.');});globalThis.chrome?.webview?.postMessage({type:'interlinked-ai-speech',action:'voices'});
 document.addEventListener('visibilitychange',()=>{if(document.hidden){aiStopRecording(false).catch(aiError);aiStopSpeech();}});globalThis.addEventListener('pagehide',()=>{aiStopRecording(false).catch(()=>{});aiStopSpeech();});setInterval(()=>{if(aiVisible&&!document.hidden&&!stopped)aiPoll().catch(aiError);},1500);
}
// END AI BINDINGS

// BEGIN MASTER MODELS
function masterStatus(value){return String(typeof value==='object'&&value!==null?value.status||'unknown':value||'unknown').toLowerCase();}
function masterTone(value){const v=masterStatus(value);return /fail|error|blocked/.test(v)?'danger':/waiting|approval|attention|uncertain/.test(v)?'attention':/working|running|active|editing|testing|connected|live/.test(v)?'active':/pass|success|complete|finished|verified|ready/.test(v)?'good':'quiet';}
function masterWords(value){return String(value||'Unknown').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());}
function masterWhen(value,now=Date.now()){
 const stamp=Date.parse(value||'');if(!Number.isFinite(stamp))return 'No observation';
 const seconds=Math.max(0,Math.floor((now-stamp)/1000));return seconds<5?'Just now':seconds<60?seconds+'s ago':seconds<3600?Math.floor(seconds/60)+'m ago':Math.floor(seconds/3600)+'h ago';
}
function masterTemperature(reading,now=Date.now()){
 if(!reading||!Number.isFinite(reading.value)||!['live','ok','available','verified','connected'].includes(masterStatus(reading)))return '—';
 const stamp=Date.parse(reading.observedAt||reading.utc||'');if(!Number.isFinite(stamp)||now-stamp>15000||reading.stale)return '—';
 return Math.round(reading.value)+'°C';
}
function masterFrameAge(display,lastReceived,error='',now=Date.now()){
 const observed=Date.parse(display?.frameObservedAt||''),hasObservation=Number.isFinite(observed),stamp=hasObservation?observed:lastReceived;
 return stamp?(hasObservation?'OBSERVED ':'RECEIVED ')+Math.max(0,Math.floor((now-stamp)/1000))+'s ago'+(error?' · '+error:''):'No frame received';
}
function masterSubagents(summary,snapshotUtc,now=Date.now()){
 const age=now-Date.parse(snapshotUtc),observedAge=now-Date.parse(summary?.observedAt),count=summary?.workingCount;
 const valid=Number.isFinite(age)&&age>=0&&age<=15000&&Number.isFinite(observedAge)&&observedAge>=0&&observedAge<=180000&&Number.isInteger(count)&&count>=0&&count<=256&&['observed','partial'].includes(summary?.status);
 const partial=valid&&summary.status==='partial';
 return {text:valid?String(count):'—',tone:valid?(partial?'attention':'active'):'quiet',
  explanation:valid?'Observed working subagents'+(partial?'; tracking is incomplete.':'; event observations are not a complete live inventory.'):'Working subagent count unavailable; missing or stale observations do not mean zero.',
  observedAt:summary?.observedAt,source:String(summary?.source||'Codex lifecycle (observed)')};
}
function masterProjectModel(project){
 const activity=project.activity||{},health=project.health||{},git=project.git||{},workspace=project.workspace||{};
 const activityStatus=activity.stale?'stale':masterStatus(activity);
 return {id:String(project.id||''),label:String(project.label||project.name||'Unnamed project'),activity:masterWords(activityStatus),tone:masterTone(activityStatus),
  health:masterWords(masterStatus(health)),healthTone:masterTone(health),summary:String(health.summary||'No build or test evidence received.'),
  healthSource:String(health.source||'No verified result'),healthObservedAt:health.observedAt,
  workspace:masterWords(masterStatus(workspace)),workspaceTone:masterTone(workspace),workspaceSummary:String(workspace.summary||'No workspace observation'),workspaceSource:String(workspace.source||'Source not labeled'),workspaceObservedAt:workspace.observedAt,
  source:String(activity.source||project.source||'Not connected'),observedAt:activity.observedAt||project.observedAt,
  branch:git.branch?String(git.branch):'Git not observed',dirty:typeof git.dirty==='boolean'?(git.dirty?'Working changes':'Clean at observation'):'Changes unknown',
  baseline:health.baseline?String(typeof health.baseline==='string'?health.baseline:health.baseline.head||health.baseline.revision||'Recorded baseline'):'No tested baseline',
  agents:Array.isArray(activity.agents)?activity.agents.length:0};
}
function masterCapturePatch(settings,source,values){
 const rect={};for(const key of ['x','y','width','height']){const value=Number(values[key]);if(!Number.isInteger(value)||Math.abs(value)>32768||(['width','height'].includes(key)&&value<1))throw new Error('Use whole-number screen coordinates and a positive capture size.');rect[key]=value;}
 if(values.enabled&&!source)throw new Error('Select Violet’s visible source window before enabling capture.');
 return {mode:'live_mini',captureRect:rect,captureWindowId:source?String(source.id):'',captureWindowBounds:source?.bounds||null,captureEnabled:!!values.enabled};
}
function masterInboxModel(queue,filters={}){
 const items=Array.isArray(queue?.items)?queue.items:[];
 const rows=items.filter(item=>(!filters.project||item.projectId===filters.project)&&(!filters.kind||item.kind===filters.kind)&&
  (filters.review==='all'||filters.review==='reviewed'&&item.acknowledged||(!filters.review||filters.review==='unreviewed')&&!item.acknowledged));
 rows.sort((a,b)=>(a.kind==='failure'?0:1)-(b.kind==='failure'?0:1)||(Date.parse(b.observedAt)||0)-(Date.parse(a.observedAt)||0)||String(a.id).localeCompare(String(b.id)));
 return {available:queue?.available===true,rows,items,pending:items.filter(item=>!item.acknowledged).length};
}
function configurationDescribe(value){
 if(value?.available===false)return 'Current settings are unavailable. '+String(value.error||'Review a validated previous version.');
 value=value?.value||value||{};if(value.settings)value={...value.settings,...(typeof value.lifecyclePreferencesKnown==='boolean'?{lifecyclePreferencesKnown:value.lifecyclePreferencesKnown}:{})};
 const labels={layout:'Cooler layout',mode:'Violet source',fps:'Target frame rate',rotationSeconds:'Detail rotation (seconds)',focusProject:'Focused project',background:'Keep running in tray',startAtLogin:'Start at login',captureEnabled:'Capture enabled',captureRect:'Capture area',captureWindowId:'Selected capture window',captureWindowBounds:'Observed source bounds',projects:'Project stations',favorites:'Favorite projects',recent:'Recent projects',lifecyclePreferencesKnown:'Startup and background choices known'};
 const describe=item=>item===null?'Not configured':typeof item==='boolean'?(item?'Yes':'No'):String(item);
 const lines=[];for(const [key,item]of Object.entries(value)){
  if(!Object.prototype.hasOwnProperty.call(labels,key))continue;
  if(key==='projects'){lines.push(labels[key]+':');for(const row of item||[])lines.push('  '+String(row.label||row.id)+' · '+String(row.root||'No source folder')+(row.activityRoot?' · Activity: '+String(row.activityRoot):''));if(!item?.length)lines.push('  None');}
  else if(key==='favorites'||key==='recent'){lines.push(labels[key]+':');for(const row of item||[])lines.push('  '+(typeof row==='string'?row:String(row.id)+' · '+String(row.utc||'Date not recorded')));if(!item?.length)lines.push('  None');}
  else if(typeof item==='object'&&item!==null)lines.push(labels[key]+': '+Object.entries(item).map(([name,n])=>name+' '+describe(n)).join(', '));
  else lines.push(labels[key]+': '+describe(item));
 }
 return lines.join('\n')||'No saved values available.';
}
// END MASTER MODELS

// BEGIN MASTER INTERFACE
let masterSnapshot=null,masterVisible=false,masterBusy=false,masterSettingsLoaded=false,masterPollBusy=false,masterFrameBusy=false,masterLastFrame=0,masterFrameError='',masterFrameSequence=0,masterSourcesKey='';
const masterEvidenceOpen=new Set();
const masterDrafts={display:{dirty:false,revision:null},capture:{dirty:false,revision:null},projects:{dirty:false,revision:null,rows:[]}};
let masterConflict=null,masterAttentionQueue=null,masterAttentionBusy=false,masterAttentionReading=false;
const masterAttentionSelected=new Set();
const masterLifecycleTouched=new Set();
let configurationPreview=null,configurationBusy=false,configurationSequence=0;
function masterElement(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function masterError(error){$('master-error').textContent=String(error?.message||error);$('master-error').hidden=false;}
function masterClearError(){$('master-error').hidden=true;}
function masterSetView(enabled){
 if(typeof jobsHide==='function')jobsHide();
 if(typeof aiHide==='function')aiHide();
 masterVisible=!!enabled;$('master-view').hidden=!enabled;$('library-shell').hidden=enabled;$('library-hub-bar').hidden=enabled;
 for(const [id,on] of [['master-view-tab',enabled],['library-view-tab',!enabled]]){$(id).classList.toggle('active',on);$(id).setAttribute('aria-pressed',String(on));}
 document.body.classList.toggle('master-open',enabled);
 if(enabled){masterPoll().catch(masterError);masterFetchFrame().catch(masterError);masterPollAttention().catch(masterError);}
}
function masterRenderProjects(){
 const rows=masterSnapshot?.projects||[];$('master-project-count').textContent=String(rows.length).padStart(2,'0');$('master-projects').replaceChildren();
 const total=masterSubagents(masterSnapshot?.subagents,masterSnapshot?.utc);
 $('master-subagent-total').textContent='SUBAGENTS WORKING '+total.text;$('master-subagent-total').className='master-station-summary tone-'+total.tone;
 $('master-subagent-total').setAttribute('aria-label','Subagents working: '+total.text+'. '+total.explanation);$('master-subagent-total').setAttribute('title',total.explanation);
 if(!rows.length){$('master-projects').append(masterElement('p','master-muted','No project stations are registered. Add a source folder in settings.'));return;}
 for(const [index,row] of rows.entries()){
  const p=masterProjectModel(row),card=masterElement('article','master-project-station tone-'+p.tone);
  const glyph=masterElement('div','master-station-glyph');glyph.setAttribute('aria-hidden','true');glyph.append(masterElement('span','',String(index+1).padStart(2,'0')));for(let i=0;i<3;i++)glyph.append(masterElement('i','station-terminal'));
  const content=masterElement('div','master-station-content'),heading=masterElement('div','master-station-heading');heading.append(masterElement('h3','',p.label),masterElement('span','master-activity-chip',p.activity));
  const health=masterElement('div','master-health-line');health.append(masterElement('span','', 'BUILD / TEST'),masterElement('strong','tone-'+p.healthTone,p.health));
  const workspace=masterElement('div','master-health-line');workspace.append(masterElement('span','','WORKSPACE'),masterElement('strong','tone-'+p.workspaceTone,p.workspace));
  const subs=masterSubagents(row.subagents,masterSnapshot?.utc),subagentLine=masterElement('div','master-health-line');subagentLine.append(masterElement('span','','SUBAGENTS WORKING'),masterElement('strong','tone-'+subs.tone,subs.text));subagentLine.setAttribute('aria-label','Subagents working: '+subs.text+'. '+subs.explanation);subagentLine.setAttribute('title',subs.explanation);
  content.append(heading,health,masterElement('p','master-station-summary',p.summary),workspace,masterElement('p','master-station-summary',p.workspaceSummary),subagentLine);
  const meta=masterElement('div','master-station-meta');meta.append(masterElement('span','',p.branch),masterElement('span','',p.dirty));
  const evidence=masterElement('details','master-evidence');evidence.open=masterEvidenceOpen.has(p.id);evidence.addEventListener('toggle',()=>{if(evidence.open)masterEvidenceOpen.add(p.id);else masterEvidenceOpen.delete(p.id);});evidence.append(masterElement('summary','','Source & evidence'));
  evidence.append(masterElement('p','','Activity: '+p.source+' · '+masterWhen(p.observedAt)),masterElement('p','','Build/test result: '+p.healthSource+' · '+masterWhen(p.healthObservedAt)),masterElement('p','','Build/test baseline: '+p.baseline),masterElement('p','','Workspace: '+p.workspaceSource+' · '+masterWhen(p.workspaceObservedAt)),masterElement('p','','Observed agents: '+p.agents));
  evidence.append(masterElement('p','','Subagents: '+subs.source+' · '+masterWhen(subs.observedAt)+' · '+subs.explanation));
  content.append(meta,evidence);card.append(glyph,content);$('master-projects').append(card);
 }
}
function masterRenderSources(){
 const sources=masterSnapshot?.captureSources||[],key=JSON.stringify(sources);if(key===masterSourcesKey)return;masterSourcesKey=key;
 const select=$('master-capture-window'),chosen=masterDrafts.capture.dirty?select.value:select.value||masterSnapshot?.settings?.captureWindowId||'';select.replaceChildren(masterElement('option','','Choose a visible mini window…'));select.children[0].value='';
 for(const source of sources){const option=masterElement('option','',String(source.title||'Untitled window')+(source.process?' · '+source.process:''));option.value=String(source.id);select.append(option);}select.value=chosen;
}
function masterLoadSettings(){
 const s=masterSnapshot?.settings||{},valid=masterSnapshot?.settingsValid!==false;
 if(!valid){$('master-settings-feedback').textContent='MASTER settings are unavailable. Review saved settings history to recover them.';return;}
 for(const section of ['display','capture','projects']){
  const draft=masterDrafts[section];if(draft.dirty)continue;draft.revision=masterSnapshot.settingsRevision??null;
  if(section==='display'){$('master-layout').value=s.layout||'command_deck';$('master-mode').value=s.mode||'dashboard';$('master-fps-setting').value=String(s.fps||30);$('master-background').checked=!!s.background;$('master-login').checked=!!s.startAtLogin;for(const id of ['master-background','master-login'])$(id).indeterminate=masterSnapshot.lifecyclePreferencesKnown===false;masterLifecycleTouched.clear();}
  if(section==='capture'){for(const key of ['x','y','width','height'])$('master-capture-'+key).value=String(s.captureRect?.[key]??(['width','height'].includes(key)?240:0));$('master-capture-enabled').checked=!!s.captureEnabled;}
  if(section==='projects')draft.rows=(s.projects||[]).map(row=>({...row}));
 }
 $('master-lifecycle-notice').hidden=masterSnapshot.lifecyclePreferencesKnown!==false;
 $('master-lifecycle-notice').textContent='Startup and background choices are unknown. Choose them explicitly before saving preferences.';
 masterSettingsLoaded=true;
}
function masterRenderRegistered(){
 const rows=masterSnapshot?.settings?.projects||masterSnapshot?.projects||[];$('master-registered-projects').replaceChildren();
 for(const row of rows){const line=masterElement('div','master-registered-project'),content=masterElement('div');content.append(masterElement('strong','',row.label||row.name||row.id),masterElement('small','',row.root||'Source folder not configured'));const remove=masterElement('button','master-remove','Remove');remove.type='button';remove.setAttribute('aria-label','Remove '+String(row.label||row.name||row.id)+' station');remove.disabled=masterBusy||masterSnapshot?.settingsValid===false;const revision=masterSnapshot?.settingsRevision;remove.addEventListener('click',()=>masterRemoveProject(row.id,rows,revision).catch(masterError));line.append(content,remove);$('master-registered-projects').append(line);}
}
function masterMarkDraft(section){masterDrafts[section].dirty=true;}
function masterShowConflict(section,attempted,current){
 masterConflict={section,attempted,current};$('master-settings-conflict').hidden=false;
 $('master-conflict-current').textContent=configurationDescribe(current?.settings||current?.value||{});
 $('master-conflict-draft').textContent=configurationDescribe(attempted);
}
function masterReloadConflict(){
 if(!masterConflict)return;const current=masterConflict.current;
 if(current?.settings)masterSnapshot={...masterSnapshot,...current};
 masterDrafts[masterConflict.section].dirty=false;masterConflict=null;$('master-settings-conflict').hidden=true;masterLoadSettings();
 $('master-settings-feedback').textContent='Current values loaded. Review your changes, then save again.';
}
async function masterSavePatch(section,patch,revision=masterDrafts[section].revision){
 if(masterSnapshot?.settingsValid===false)throw new Error('Recover the unavailable MASTER settings before editing them.');
 if(typeof revision!=='string'||!revision)throw new Error('Settings version is unavailable. Reload the interface before saving.');
 masterMarkDraft(section);
 try{const saved=await api('/api/master/settings',{...patch,expectedRevision:revision});masterDrafts[section].dirty=false;
  if(masterConflict?.section===section){masterConflict=null;$('master-settings-conflict').hidden=true;}
  if(saved?.settings)masterSnapshot=saved;
  await masterPoll();
 }catch(error){const payload=error.payload||{};if(error.status===409&&payload.current){masterShowConflict(section,patch,payload.current);error.message='Settings changed in another window. Your edit is retained; review the current values before saving again.';}throw error;}
}
async function masterRemoveProject(id,rows,revision){
 await masterRequest(()=>masterSavePatch('projects',{projects:rows.filter(row=>row.id!==id).map(row=>({...row}))},revision));
}
function masterRender(){
 const s=masterSnapshot;if(!s)return;
 const display=s.display||{},connected=!!display.connected;
 $('master-source-status').textContent=s.demo?'DEMO FEED':'LOCAL OBSERVATORY';$('master-source-status').className='master-status '+(s.demo?'attention':'active');
 $('master-observed').textContent='Snapshot '+masterWhen(s.observedAt||s.utc||s.generatedAt);$('master-demo-note').hidden=!s.demo;
 const configurationNotice=String(s.configurationNotice||(s.restartRequired?'Restart INTERLINKED to activate the changed MASTER configuration.':''));$('master-configuration-notice').textContent=configurationNotice;$('master-configuration-notice').hidden=!configurationNotice;
 const phase=display.status||'disconnected',connectionPending=['connecting','reconnecting','retry_wait','disconnecting','canceling','cancelling','cleaning_up'].includes(phase),desired=display.desiredConnected===true;
 const retrySeconds=display.nextRetryAt?Math.max(0,Math.ceil((Date.parse(display.nextRetryAt)-Date.now())/1000)):null;
 const phaseLabels={connecting:'Connecting',reconnecting:'Reconnecting',retry_wait:Number.isFinite(retrySeconds)?'Retrying in '+retrySeconds+'s':'Waiting to retry',disconnecting:'Disconnecting',canceling:'Canceling connection',cancelling:'Canceling connection',cleaning_up:'Cleaning up connection',unavailable:'Connection failed',failed:'Connection failed'};
 $('master-display-status').textContent=connected?'LCD connected':phaseLabels[phase]||masterWords(phase);
 const lastError=display.error||'',message=display.message||lastError||(connected?'INTERLINKED owns the cooler display.':connectionPending?'Waiting for the display transport.':'Choose Connect display to request the cooler.');
 const retry=Number.isInteger(display.retryAttempt)&&display.retryAttempt>0?'Recovery attempt '+display.retryAttempt+' of '+(display.retryLimit||3)+'. ':'';
 $('master-display-message').textContent=retry+message+(lastError&&!message.includes(lastError)?' Last error: '+lastError:'');
 $('master-display-led').className='master-led '+(connected?'active':connectionPending?'attention':'');$('master-connect').disabled=masterBusy||connected||connectionPending||desired||!!s.demo;$('master-disconnect').disabled=masterBusy||!!s.demo||!(connected||desired||connectionPending);
 $('master-connect').textContent=['unavailable','failed'].includes(phase)?'Retry display':'Connect display';
 $('master-screen-title').textContent=s.settings?.layout==='violet_web'?'Violet Web':'The command deck';
 const stats=display.stats||s.stats||display;$('master-fps').textContent='DELIVERY FPS '+(connected&&Number.isFinite(stats.deliveredFps)?stats.deliveredFps.toFixed(1):'—');$('master-dropped').textContent='DROPPED '+(Number.isFinite(stats.droppedFrames)?stats.droppedFrames:'—');
 $('master-preview-fps').textContent='PREVIEW FPS '+(Number.isFinite(stats.renderFps)?stats.renderFps.toFixed(1):'—');
 $('master-cpu').textContent=masterTemperature(s.temperatures?.cpu);$('master-gpu').textContent=masterTemperature(s.temperatures?.gpu);
 $('master-sensor-note').textContent=s.temperatures?.message||s.temperatures?.reason||(($('master-cpu').textContent==='—'||$('master-gpu').textContent==='—')?'Unverified or unavailable readings stay blank.':'Live verified readings · '+masterWhen(s.temperatures?.observedAt));
 $('master-enable-sensors').disabled=masterBusy||!connected||!!s.demo;
 $('master-sensor-permission').textContent=s.sensorPermission?.message||'';$('master-sensor-permission').hidden=!s.sensorPermission?.message;
 const violet=s.violet||{};$('master-violet-status').textContent=violet.stale?'Violet’s status update is stale':violet.connected?masterWords(violet.activity||violet.status):'Waiting for Violet’s status bridge';const attention=s.attention||{};const pending=(attention.items||[]).filter(item=>!item.acknowledged);$('master-violet-message').textContent=(violet.source||'Unverified report')+' · '+(violet.message||'No fresh Violet report')+' · '+(attention.available?String(attention.pendingCount||0)+' queued for next Violet check'+(pending.length?': '+pending.slice(0,3).map(item=>item.projectId+' — '+item.summary).join('; '):''):'Attention queue unavailable');$('master-violet-freshness').textContent=violet.stale?'STALE UPDATE':violet.connected?masterWhen(violet.observedAt||violet.reportedAt):'NOT CONNECTED';$('master-violet-freshness').className='master-status '+(violet.connected&&!violet.stale?'active':'quiet');
 $('master-capture-status').textContent=s.capture?.message||s.capture?.reason||masterWords(s.capture?.status||'Live mini is not calibrated');
 masterRenderProjects();masterRenderSources();masterLoadSettings();masterRenderRegistered();
 for(const id of ['master-save-settings','master-save-capture'])$(id).disabled=masterBusy||s.settingsValid===false;
 if(!masterAttentionReading&&s.attention)masterAttentionQueue=s.attention;masterRenderAttention();
}
async function masterPoll(){
 if(masterPollBusy||stopped)return;masterPollBusy=true;
 try{masterSnapshot=await api('/api/master/state');masterRender();}catch(error){masterRender();throw error;}finally{masterPollBusy=false;}
}
async function masterRequest(fn){if(masterBusy)throw new Error('Please wait for the current display request.');masterBusy=true;masterClearError();masterRender();try{return await fn();}finally{masterBusy=false;masterRender();}}
async function masterEnableSensors(){
 if(masterSnapshot?.demo||!masterSnapshot?.display?.connected)throw new Error('Connect the display before enabling temperatures.');
 await masterRequest(async()=>{await api('/api/master/display',{action:'enable_sensors'});await masterPoll();});
}
async function masterSaveSettings(){
 if(masterSnapshot?.lifecyclePreferencesKnown===false&&(!masterLifecycleTouched.has('master-background')||!masterLifecycleTouched.has('master-login')))throw new Error('Choose both background and startup preferences explicitly before saving recovered settings.');
 const patch={layout:$('master-layout').value,mode:$('master-mode').value,fps:Number($('master-fps-setting').value),background:$('master-background').checked,startAtLogin:$('master-login').checked};masterMarkDraft('display');
 await masterRequest(async()=>{await masterSavePatch('display',patch);$('master-settings-feedback').textContent='Preferences saved. TRCC startup is unchanged.';});
}
async function masterSaveCapture(){
 const source=(masterSnapshot?.captureSources||[]).find(v=>String(v.id)===$('master-capture-window').value),values={enabled:$('master-capture-enabled').checked};for(const key of ['x','y','width','height'])values[key]=$('master-capture-'+key).value;
 const patch=masterCapturePatch(masterSnapshot?.settings||{},source,values);
 masterMarkDraft('capture');await masterRequest(async()=>{await masterSavePatch('capture',patch);if(!masterDrafts.display.dirty)$('master-mode').value='live_mini';});
}
function masterOpenCapturePad(){
 const pad=$('master-capture-pad');if(!pad.open)pad.show();
 document.body.classList.toggle('master-pad-open',true);$('master-black-pad').setAttribute('aria-expanded','true');
}
function masterCloseCapturePad(){
 $('master-capture-pad').close();document.body.classList.toggle('master-pad-open',false);$('master-black-pad').setAttribute('aria-expanded','false');
}
async function masterAddProject(event){
 event.preventDefault();const label=$('master-project-label').value.trim(),root=$('master-project-root').value.trim();if(!label||!root)throw new Error('Enter the project name and its source folder.');
 const id=label.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,48);if(!id)throw new Error('Use at least one letter or number in the project name.');
 const current=masterDrafts.projects.rows;masterMarkDraft('projects');
 await masterRequest(async()=>{await masterSavePatch('projects',{projects:[...current.map(p=>({...p})),{id,label,root}]});$('master-project-label').value='';$('master-project-root').value='';});
}
function masterRenderAttention(){
 const model=masterInboxModel(masterAttentionQueue,{project:$('master-attention-project').value,kind:$('master-attention-kind').value,review:$('master-attention-review').value||'unreviewed'});
 const selectedProject=$('master-attention-project').value||'',labels=new Map((masterSnapshot?.projects||[]).map(row=>[row.id,row.label||row.id]));
 const ids=[...new Set([...model.items.map(row=>row.projectId),...(selectedProject?[selectedProject]:[])])].sort();
 $('master-attention-project').replaceChildren(masterElement('option','','All projects'));$('master-attention-project').children[0].value='';
 for(const id of ids){const option=masterElement('option','',labels.get(id)||id);option.value=id;$('master-attention-project').append(option);}$('master-attention-project').value=selectedProject;
 const retained=new Set(model.items.map(item=>item.id)),expired=[...masterAttentionSelected].filter(id=>!retained.has(id));
 $('master-attention-count').textContent=model.available?String(model.pending)+' unreviewed':'Unavailable';
 $('master-attention-notice').textContent=!model.available?'Attention records are unavailable. Existing records and selections are preserved.':expired.length?'Some selected items are no longer retained. Clear the selection and review the current list.':String(masterAttentionSelected.size)+' selected · Reviewing an observation does not resolve a project failure.';
 $('master-attention-ack').disabled=masterAttentionBusy||!model.available||!masterAttentionSelected.size||!!expired.length;
 $('master-attention-clear').disabled=masterAttentionBusy||!masterAttentionSelected.size;
 $('master-attention-list').replaceChildren();
 if(!model.available||!model.rows.length){$('master-attention-list').append(masterElement('p','master-muted',model.available?'No observations match these filters.':'The inbox will return when its records can be read safely.'));return;}
 for(const item of model.rows){
  const row=masterElement('article','master-attention-item'+(item.acknowledged?' reviewed':'')),choice=masterElement('input');choice.type='checkbox';choice.checked=masterAttentionSelected.has(item.id);choice.disabled=masterAttentionBusy;choice.setAttribute('aria-label','Select observation for '+(labels.get(item.projectId)||item.projectId));choice.addEventListener('change',()=>{if(choice.checked)masterAttentionSelected.add(item.id);else masterAttentionSelected.delete(item.id);masterRenderAttention();});
  const content=masterElement('div','master-attention-content');content.append(masterElement('strong','',labels.get(item.projectId)||item.projectId),masterElement('p','',item.summary||'Historical observation'),masterElement('small','',String(item.source||'Source not recorded')+' · '+String(item.observedAt||'Observation time unavailable')),masterElement('small','',item.acknowledged?'Reviewed observation':'Unreviewed observation'));
  const review=masterElement('button','master-secondary master-compact',item.acknowledged?'Reviewed':'Mark reviewed');review.type='button';review.disabled=masterAttentionBusy||item.acknowledged;review.addEventListener('click',()=>masterAcknowledge([item.id]).catch(masterError));row.append(choice,content,review);$('master-attention-list').append(row);
 }
}
async function masterPollAttention(){
 if(masterAttentionReading||stopped)return;masterAttentionReading=true;
 try{const result=await api('/api/master/attention');masterAttentionQueue=result?.queue||result;}
 catch(error){masterAttentionQueue={...masterAttentionQueue,available:false};throw error;}
 finally{masterAttentionReading=false;masterRenderAttention();}
}
async function masterAcknowledge(ids=[...masterAttentionSelected]){
 if(masterAttentionBusy)throw new Error('Please wait for the current review request.');
 const known=new Set((masterAttentionQueue?.items||[]).map(item=>item.id));if(!ids.length||ids.some(id=>!known.has(id)))throw new Error('A selected observation is no longer retained. Refresh the inbox and review your selection.');
 masterAttentionBusy=true;masterRenderAttention();
 try{const result=await api('/api/master/attention/acknowledge',{ids:[...ids]});if(result?.queue){masterAttentionQueue=result.queue;if(masterSnapshot)masterSnapshot.attention=result.queue;}else await masterPollAttention();for(const id of result.acknowledged||ids)masterAttentionSelected.delete(id);}
 catch(error){await masterPollAttention().catch(()=>{});throw error;}
 finally{masterAttentionBusy=false;masterRenderAttention();}
}
function masterOpenInbox(){masterSetView(true);$('master-attention').scrollIntoView?.({behavior:'smooth',block:'start'});$('master-attention').focus?.();masterPollAttention().catch(masterError);}
function configurationMessage(message){$('configuration-feedback').textContent=message;}
async function configurationOpen(scope='master'){
 $('configuration-scope').value=scope;const dialog=$('configuration-dialog');if(!dialog.open)dialog.showModal();await configurationHistory();
}
async function configurationHistory(){
 const scope=$('configuration-scope').value||'master',sequence=++configurationSequence;configurationPreview=null;$('configuration-preview').hidden=true;$('configuration-restore').disabled=true;configurationMessage('Reading saved settings…');
 try{const result=await api('/api/configuration/history?scope='+encodeURIComponent(scope));if(sequence!==configurationSequence)return;$('configuration-list').replaceChildren();
  const items=Array.isArray(result.items)?result.items:[];configurationMessage(result.available===false?'Settings history needs review. Existing records were preserved.':result.current?.available===false?'Current settings are unavailable. Preview a valid previous version to recover them.':'Up to three previous validated versions are retained for this settings group.');
  if(!items.length)$('configuration-list').append(masterElement('p','master-muted','No saved previous versions are available.'));
  for(const item of items){const row=masterElement('div','configuration-history-row'),content=masterElement('div');content.append(masterElement('strong','',String(item.observedAt||item.savedAt||item.utc||'Saved time not recorded')),masterElement('small','',String(item.reason||'Settings changed')));const button=masterElement('button','master-secondary master-compact','Preview');button.type='button';button.addEventListener('click',()=>configurationShowPreview(scope,item.id).catch(error=>configurationMessage(error.message)));row.append(content,button);$('configuration-list').append(row);}
 }catch(error){if(sequence!==configurationSequence)return;configurationMessage('Settings history is unavailable. '+String(error.message||error));$('configuration-list').replaceChildren();}
}
async function configurationShowPreview(scope,snapshotId){
 const sequence=++configurationSequence;configurationPreview=null;$('configuration-restore').disabled=true;const result=await api('/api/configuration/preview',{scope,snapshotId});
 if(sequence!==configurationSequence||scope!==($('configuration-scope').value||'master'))return;
 configurationPreview={...result,scope};$('configuration-current').textContent=configurationDescribe(result.current||result.currentValue||{});$('configuration-proposed').textContent=configurationDescribe(result.proposed||result.value||{});$('configuration-preview').hidden=false;$('configuration-restore').disabled=!result.previewId;
 configurationMessage('Review the proposed values. Restoring changes preferences only; project files and sessions stay intact.');
}
async function configurationRestore(){
 if(configurationBusy||!configurationPreview?.previewId)throw new Error('Preview a saved version before restoring it.');
 const chosen=configurationPreview;configurationBusy=true;$('configuration-restore').disabled=true;
 try{const result=await api('/api/configuration/restore',{scope:chosen.scope,previewId:chosen.previewId});configurationPreview=null;
  if(chosen.scope==='library'){const catalog=await api('/api/catalog');if(typeof adoptPreferences==='function')adoptPreferences(catalog.preferences||{});if(typeof renderList==='function')renderList();}
  else await masterPoll();
  $('configuration-preview').hidden=true;configurationMessage(result.restartRequired?'Settings recovered. Restart INTERLINKED to activate MASTER. Capture and startup have not been activated.':'Settings restored. Project files, the selected project and active sessions are unchanged.');
 }catch(error){configurationPreview=null;configurationMessage('Restore was not confirmed. Review the current history before trying again. '+String(error.message||error));throw error;}
 finally{configurationBusy=false;}
}
async function masterFetchFrame(){
 if(masterFrameBusy||!masterVisible||document.hidden||stopped)return;masterFrameBusy=true;const sequence=++masterFrameSequence;
 try{
  const response=await fetch('/api/master/frame',{headers:{'X-INTERLINKED-Token':token},cache:'no-store'});
  if(!response.ok)throw new Error(response.status===503?'Renderer not available yet.':'The frame could not be loaded.');
  if(!(response.headers.get('Content-Type')||'').startsWith('image/jpeg'))throw new Error('Unexpected frame format.');
  const blob=await response.blob();if(blob.size>691136)throw new Error('Frame exceeds the LCD image limit.');
  const bitmap=await createImageBitmap(blob);try{if(bitmap.width!==480||bitmap.height!==480)throw new Error('Expected a 480 × 480 frame.');if(sequence===masterFrameSequence&&masterVisible){const context=$('master-preview').getContext('2d');if(context){context.drawImage(bitmap,0,0);masterLastFrame=Date.now();masterFrameError='';$('master-frame-empty').hidden=true;}}}finally{bitmap.close();}
 }catch(error){masterFrameError=String(error.message||error);if(!masterLastFrame){$('master-frame-empty').hidden=false;$('master-frame-empty').querySelector('strong').textContent=masterFrameError;}}
 finally{masterFrameBusy=false;}
}
// END MASTER INTERFACE
if($('master-view')){
 $('library-view-tab').addEventListener('click',()=>masterSetView(false));$('master-view-tab').addEventListener('click',()=>masterSetView(true));
 $('master-connect').addEventListener('click',()=>masterRequest(async()=>{await api('/api/master/display',{connect:true});await masterPoll();}).catch(masterError));$('master-disconnect').addEventListener('click',()=>masterRequest(async()=>{await api('/api/master/display',{connect:false});await masterPoll();}).catch(masterError));
 $('master-save-settings').addEventListener('click',()=>masterSaveSettings().catch(masterError));$('master-save-capture').addEventListener('click',()=>masterSaveCapture().catch(masterError));$('master-add-project').addEventListener('submit',event=>masterAddProject(event).catch(masterError));
 $('master-refresh-sources').addEventListener('click',()=>masterRequest(async()=>{await api('/api/master/display',{action:'capture_sources'});await masterPoll();}).catch(masterError));
 $('master-enable-sensors').addEventListener('click',()=>masterEnableSensors().catch(masterError));
 for(const id of ['master-layout','master-mode','master-fps-setting','master-background','master-login'])$(id).addEventListener('input',()=>masterMarkDraft('display'));
 for(const id of ['master-background','master-login'])$(id).addEventListener('input',()=>{masterLifecycleTouched.add(id);$(id).indeterminate=false;});
 for(const id of ['master-capture-window','master-capture-enabled','master-capture-x','master-capture-y','master-capture-width','master-capture-height'])$(id).addEventListener('input',()=>masterMarkDraft('capture'));
 for(const id of ['master-project-label','master-project-root'])$(id).addEventListener('input',()=>masterMarkDraft('projects'));
 $('master-conflict-reload').addEventListener('click',masterReloadConflict);
 $('master-inbox-link').addEventListener('click',masterOpenInbox);
 for(const id of ['master-attention-project','master-attention-kind','master-attention-review'])$(id).addEventListener('change',masterRenderAttention);
 $('master-attention-ack').addEventListener('click',()=>masterAcknowledge().catch(masterError));
 $('master-attention-clear').addEventListener('click',()=>{masterAttentionSelected.clear();masterRenderAttention();});
 $('master-attention-refresh').addEventListener('click',()=>masterPollAttention().catch(masterError));
 $('master-capture-window').addEventListener('change',()=>{const selected=(masterSnapshot?.captureSources||[]).find(v=>String(v.id)===$('master-capture-window').value);if(selected?.bounds)for(const key of ['x','y','width','height'])$('master-capture-'+key).value=String(selected.bounds[key]??0);$('master-capture-enabled').checked=false;});
 $('master-black-pad').addEventListener('click',masterOpenCapturePad);$('master-close-pad').addEventListener('click',masterCloseCapturePad);
 setInterval(()=>{if(masterVisible&&!stopped)masterPoll().catch(error=>{masterError(error);$('master-source-status').textContent='CONNECTION LOST';$('master-source-status').className='master-status danger';});},1500);
 setInterval(()=>{if(masterVisible&&!document.hidden&&!stopped)masterPollAttention().catch(masterError);},3000);
 setInterval(()=>masterFetchFrame(),100);
 setInterval(()=>{if(masterVisible)$('master-frame-age').textContent=masterFrameAge(masterSnapshot?.display,masterLastFrame,masterFrameError);},1000);
 if(new URLSearchParams(location.search).get('view')==='master')masterSetView(true);
}
if($('configuration-dialog')){
 $('configuration-open').addEventListener('click',()=>configurationOpen(masterVisible?'master':'library').catch(error=>configurationMessage(error.message)));
 $('configuration-close').addEventListener('click',()=>{$('configuration-dialog').close();configurationSequence++;configurationPreview=null;$('configuration-restore').disabled=true;});
 $('configuration-scope').addEventListener('change',()=>configurationHistory());$('configuration-refresh').addEventListener('click',()=>configurationHistory());
 $('configuration-restore').addEventListener('click',()=>configurationRestore().catch(()=>{}));
}
if($('msi-jobs-view')){
 $('jobs-view-tab').addEventListener('click',jobsSetView);
 $('msi-jobs-refresh').addEventListener('click',()=>jobsRefresh().catch(jobsShowError));
 $('msi-jobs-stream').addEventListener('change',()=>{jobsLogSnapshotKey='';jobsReadLog().catch(jobsShowError);});
 setInterval(()=>{if(jobsVisible&&!document.hidden&&!stopped)jobsPoll().catch(jobsShowError);},1500);
 if(new URLSearchParams(location.search).get('view')==='jobs')jobsSetView();
}





// BEGIN BUDDY PROJECT REPORT
let buddyReportSequence=0,buddyReportRefreshed=0,buddyReportProject=null,buddyReportSnapshot=null;
function buddyReportModel(value){
 const available=value?.available===true,raw=value?.counts||{};
 const number=key=>Number.isSafeInteger(raw[key])&&raw[key]>=0?raw[key]:0;
 return {available,observed:String(value?.source_observed_at||value?.observed_at||''),counts:{total:number('total'),stored:number('stored'),searchable:number('searchable'),prepared:number('prepared'),delivered:number('delivered'),attention:number('attention'),running:number('running'),queued:number('queued')},tasks:(Array.isArray(value?.tasks)?value.tasks:[]).slice(0,100),truncated:value?.truncated===true};
}
function buddyRenderReport(value){
 const model=buddyReportModel(value),panel=$('buddy-report-panel');if(!panel)return;
 for(const key of ['prepared','delivered','attention','queued'])$('buddy-report-'+key).textContent=model.available?String(model.counts[key]):'—';
 $('buddy-report-observed').textContent=model.available?'Last Buddy report · '+(model.observed||'observation time not recorded'):'No Buddy report received for this project.';
 $('buddy-report-empty').hidden=model.available&&model.tasks.length>0;
 $('buddy-report-empty').textContent=model.available?'The latest report contains no tasks.':'Run work in ED-IT Buddy and publish its project report to see results here.';
 $('buddy-report-list').replaceChildren();
 for(const task of model.tasks){
  if(!task||typeof task!=='object')continue;
  const item=document.createElement('li');item.className='buddy-report-task'+(task.attention?' attention':'');
  const title=document.createElement('strong');title.textContent=String(task.kind||'Task')+' · '+String(task.status||'unknown');
  const delivery=document.createElement('span');delivery.className='buddy-report-delivery';delivery.textContent=(task.storage_status?'MSI storage: '+String(task.storage_status)+' · Indexing: '+String(task.indexing_status||'not_queued')+' · ':'')+'Project delivery: '+String(task.delivery_status||'not_delivered');
  item.append(title,delivery);
  if(task.message){const message=document.createElement('p');message.textContent=String(task.message).slice(0,1000);item.append(message);}
  const outputs=Array.isArray(task.outputs)?task.outputs:[];
  if(outputs.length){
   const references=document.createElement('ul');references.className='buddy-report-outputs';
   for(const output of outputs.slice(0,10)){
    const row=document.createElement('li');row.textContent=String(output?.path||'Recorded output').slice(0,2048)+(typeof output?.sha256==='string'&&/^[a-f0-9]{64}$/.test(output.sha256)?' · SHA-256 '+output.sha256.slice(0,12):'');references.append(row);
   }
   item.append(references);
  }
  if(task.outputs_truncated){const note=document.createElement('small');note.textContent='Additional output references remain in Buddy’s complete report.';item.append(note);}
  if(task.reviewed_by_user_at){const review=document.createElement('small');review.textContent='User marked reviewed: '+String(task.reviewed_by_user_at);item.append(review);}
  $('buddy-report-list').append(item);
 }
 $('buddy-report-limits').textContent=(model.truncated?'Showing up to 100 tasks, with attention items first. ':'')+'Stored on MSI, searchable, delivered, and project save-back are separate outcomes. Save-back remains explicit.';
}
async function buddyLoadReport(){
 const id=selected?.id,sequence=++buddyReportSequence;if(!$('buddy-report-panel'))return;
 buddyReportRefreshed=Date.now();
 if(buddyReportProject!==id){buddyReportProject=id;buddyReportSnapshot=null;buddyRenderReport(null);}
 $('buddy-report-error').hidden=true;$('buddy-report-refresh').disabled=!id;
 if(!id)return;
 try{
  const value=await api('/api/buddy-report?project='+encodeURIComponent(id));
  if(selected?.id!==id||sequence!==buddyReportSequence)return;
  buddyReportSnapshot=value;buddyRenderReport(value);
 }catch(error){
  if(selected?.id!==id||sequence!==buddyReportSequence)return;
  $('buddy-report-error').textContent='Buddy report unavailable. '+String(error.message||error);$('buddy-report-error').hidden=false;
  if(buddyReportSnapshot)$('buddy-report-observed').textContent='Retained report · local refresh failed. Last source observation: '+String(buddyReportSnapshot.source_observed_at||buddyReportSnapshot.observed_at||'unknown');
 }
}
if($('buddy-report-panel'))$('buddy-report-refresh').addEventListener('click',()=>buddyLoadReport());
// END BUDDY PROJECT REPORT

async function loadStats(){
 const id=selected?.id;if(selected?.originalId!=='stats-active')return;
 const sequence=++statsSequence,info=await api('/api/stats?project='+encodeURIComponent(id));
 if(selected?.id!==id||sequence!==statsSequence)return;statsSnapshot=info;renderStatsControls();
}
function renderStatsControls(){
 if(!$('stats-panel')||selected?.originalId!=='stats-active')return;
 const info=statsSnapshot,controls=statsControls(info,state);
 $('stats-status').textContent=human(info?.status||'Checking');
 $('stats-detail').textContent=info?.warning||info?.operation?.error||info?.reason||(info?.running?'The local dashboard is protected. Stop Stats before saving its library.':'Ready to launch against the managed local library.');
 $('stats-paths').textContent=info?.dataRoot?('Library: '+info.dataRoot+' · Code: '+info.sourceRoot):'Fetch the library and configure the reviewed local runtime.';
 for(const [name,enabled] of Object.entries(controls))$('stats-'+name).disabled=mutation||state.closing||!enabled;
 $('stats-acknowledge').hidden=info?.status!=='RECOVERY_REQUIRED';
}
async function statsAction(name){
 await loadStats();const id=selected.id;
 let confirmation;
 if(name==='acknowledge'){if(!confirm('Close every Stats dashboard, script and terminal using this library. Confirm recovery for stats-active?'))return;confirmation='stats-active';}
 await mutate(async()=>{await api('/api/stats/action',{project:id,action:name,expectedRevision:statsSnapshot.revision,confirmation});await poll();await loadStats();},'stats-'+name);
}
for(const name of ['start','open-browser','stop','fetch','save','verify','acknowledge'])$('stats-'+name).addEventListener('click',safe(()=>statsAction(name)));
$('stats-release').addEventListener('click',safe(async()=>{await loadStats();releaseTarget={id:selected.id,confirmation:releaseConfirmation(selected),name:selected.name,revision:statsSnapshot.revision,stats:true};$('release-description').textContent='The Stats managed local library. The separate Git checkout is outside release coverage.';$('release-expected').textContent=releaseTarget.confirmation;$('release-id').value='';$('release-confirm').disabled=true;$('release-dialog').showModal();}));
