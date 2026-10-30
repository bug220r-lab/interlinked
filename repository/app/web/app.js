'use strict';
const $=id=>document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('interlinked-token')||'';
if(token){sessionStorage.setItem('interlinked-token',token);history.replaceState(null,'',location.pathname+location.search);}
let projects=[], selected=null, state={busy:false,revision:0}, filter='all', auditLoaded=null, releaseTarget=null, lastCompleted=null, stopped=false;
let mutation=false,pollSequence=0,appliedPoll=0,entrySequence=0,auditSequence=0;
let requestedAction=null;
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
 if(!response.ok)throw new Error(value.error||'The local service rejected this request.');
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
 return kind==='all'||kind==='local'&&isLocal(p)||kind==='review'&&needsReview(p)||kind==='favorites'&&((prefs?.favorites||[]).includes(p.id)||p.favorite)||kind==='recent'&&(recentIds(prefs).includes(p.id)||p.recentUtc)||['magix','mw5','coding_data'].includes(kind)&&projectType(p)===kind;
}
function hasCapability(p,key){
 if(!p)return false;
 if(p.capabilities)return p.capabilities[key]===true;
 return isMagix(p)&&['openApplication','finish','save','verify','release'].includes(key);
}
function canSelectProject(p,snapshot,mutating){
 if(!p||mutating||snapshot.closing)return false;
 if(!snapshot.busy)return true;
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
 const magix=isMagix(selected),mw5=projectType(selected)==='mw5';
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
 updateEntry();renderState();
}
function select(id,record=false){const previous=selected?.id;selected=projects.find(p=>p.id===id)||projects[0];auditLoaded=null;auditSequence++;if(previous!==selected?.id){mw5Readiness=null;mw5Preview=null;mw5Sequence++;}if(selected)selected.loadingEntries=isMagix(selected);renderList();renderDetail();if(!selected)return;if(isMagix(selected)){loadEntries(selected.id).catch(error=>{if(selected?.id===id){selected.loadingEntries=false;renderState();}showError(error);});if(previous!==selected.id){$('missing-path').value='';$('missing-note').value='';}loadMissing().catch(missingError);if(!$('audit-panel').hidden)loadAudit().catch(showError);}if(projectType(selected)==='mw5')loadMw5().catch(showError);if(record)recordRecent(selected.id).catch(showError);if($('buddy-report-panel'))buddyLoadReport();}
async function loadEntries(id){const sequence=++entrySequence,result=await api('/api/entries?project='+encodeURIComponent(id));if(selected?.id!==id||sequence!==entrySequence)return;selected.entryPoints=result.entries;selected.loadingEntries=false;renderDetail();if(result.warnings.length)$('entry-note').textContent+=' '+result.warnings.join(' ');}
function updateEntry(){if(!isMagix(selected)){$('entry-note').textContent=projectType(selected)==='mw5'?'Stage the reviewed mod plugins below before opening the editor. Collect changes after the editor closes.':'Open working folder starts a protected editing session. Close your editors and running tools before finishing it.';return;}const item=selected?.entryPoints.find(e=>e.path===$('entry').value);$('entry-note').textContent=item?.kind==='recovery'?'Backup selected: MAGIX opens, then choose File > Load backup project using the exact local path shown in Activity. The backup is not renamed.':'The reviewed launcher uses a short local path to the selected cached project. It does not stream media from MSI.';}
async function loadCatalog(){
 const catalog=await api('/api/catalog');projects=catalog.projects;hub=catalog.hub||{};preferences=catalog.preferences||preferences;lastCatalogRefresh=Date.now();renderHub();
 $('demo-banner').hidden=!catalog.demo;$('mode').lastChild.textContent=catalog.demo?'DEMO · NO REAL FILES':'LOCAL CONTROL';
 $('snapshot-date').textContent='CATALOG '+String(catalog.indexDate||'snapshot').split('T')[0]+' · AUDIT '+(catalog.auditAvailable?'AVAILABLE':'NOT INSTALLED');
 select(selected?.id||initialProject||projects[0]?.id);
}
async function action(name){
 if(!selected&&name!=='recover')throw new Error('Choose a project first.');
 if(name==='recover'&&!confirm('Close all MAGIX windows first. Recover removes only the recorded local mappings; it does not save or free project files. Continue?'))return;
 await mutate(async()=>{await api('/api/action',{action:name,project:selected?.id,entry:name==='open'&&isMagix(selected)?$('entry').value:undefined,expectedRevision:state.revision});await poll();},name);
}
function renderState(){
 const job=state.operation,busy=state.busy,locked=busy||mutation||state.closing;
 for(const id of ['open','save','verify','release','recover','fetch','folder'])$(id).disabled=locked||!selected;
 $('quit').disabled=locked||notePending;$('entry').disabled=locked||selected?.loadingEntries;
 for(const button of document.querySelectorAll('.project-item'))button.disabled=!canSelectProject(projects.find(p=>p.id===button.dataset.project),state,mutation);
 if(selected&&!locked){$('open').disabled=!hasCapability(selected,'openApplication')||(isMagix(selected)&&(selected.loadingEntries||!$('entry').value));$('fetch').disabled=!hasCapability(selected,'fetch');$('folder').disabled=!hasCapability(selected,'openFolder');$('save').disabled=!hasCapability(selected,'save')||!['READY','PENDING_SAVE','DEMO'].includes(selected.cache.status);$('verify').disabled=!hasCapability(selected,'verify');$('release').disabled=!hasCapability(selected,'release')||!['READY','DEMO','RELEASING'].includes(selected.cache.status);}
 if(state.activeSession?.kind==='mw5'&&state.activeSession.projectId===selected?.id)$('open').disabled=mutation||state.transferring||state.closing||mw5Readiness?.busy||mw5Readiness?.session?.canOpen!==true;
 $('favorite-toggle').disabled=locked||!selected;
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
 renderSessionBanner();renderMw5Controls();
 const log=$('operation-log'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<35;log.textContent=state.output||'No operation output yet.';if(atEnd)log.scrollTop=log.scrollHeight;
 $('log-warning').hidden=!job?.reportingWarning;$('log-warning').textContent=job?.reportingWarning||'';
 $('recovery-note').hidden=!state.previousIncomplete?.length;
 $('recovery-note').textContent='A previous service run ended with an unfinished operation. Do not assume it stopped. Close MAGIX and check the prior launcher before using Recover. No files were automatically released.';
 if(isMagix(selected))renderOpenFeedback();else $('open-feedback').hidden=true;
}
async function poll(){
 if(stopped)return;
 const sequence=++pollSequence,value=await api('/api/state');if(sequence<appliedPoll)return;appliedPoll=sequence;state=value;renderState();
 if(state.operation&&!state.transferring&&!['STARTING','RUNNING','WAITING_FOR_MAGIX'].includes(state.operation.status)&&state.operation.id!==lastCompleted){const completed=state.operation.id;await loadCatalog();lastCompleted=completed;}
 else if(Date.now()-lastCatalogRefresh>8000&&!mutation){await loadCatalog();}
 if($('buddy-report-panel')&&!document.hidden&&Date.now()-buddyReportRefreshed>5000)await buddyLoadReport();
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
}
function renderSessionBanner(){
 const job=state.operation,active=state.busy,session=state.activeSession,project=projects.find(p=>p.id===(session?.projectId||job?.projectId));
 $('session-banner').classList.toggle('busy',!!active);$('session-project').hidden=!active||!project;
 $('session-done').hidden=session?.kind==='mw5'||!(active&&job?.awaitingDone&&['open','folder'].includes(job?.action));$('session-done').disabled=mutation||!job?.awaitingDone;
 $('session-title').textContent=active?(project?.name||job?.projectId||'Managed operation'):'No editing session active';
 $('session-next').textContent=session?.nextAction|| (active?(job?.awaitingDone?(isMagix(project)?'Next: save and close MAGIX, then finish the session.':'Next: save your work, close the application and its tools, then finish the session.'):'Next: wait for '+human(job?.action||'the current operation')+' to finish. Your working copy stays protected.'):(projectType(selected)==='mw5'?'Next: check readiness, stage the plugins, and open the MW5 Editor.':'Next: choose a project and prepare its managed working copy.'));
}
function adoptPreferences(result){const value=result.preferences||result;if(Array.isArray(value.favorites)&&Array.isArray(value.recent))preferences=value;}
async function recordRecent(id){adoptPreferences(await api('/api/preferences',{recent:id}));renderList();}
async function toggleFavorite(){
 if(!selected)return;const id=selected.id,enabled=!((preferences.favorites||[]).includes(id)||selected.favorite);
 adoptPreferences(await api('/api/preferences',{favorite:id,enabled}));const row=projects.find(p=>p.id===id);if(row)row.favorite=enabled;renderList();if(selected?.id===id)renderDetail();
}
async function refreshHub(){await api('/api/hub/refresh',{});await loadCatalog();}
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
$('release-confirm').addEventListener('click',safe(()=>mutate(async()=>{if(!releaseTarget)throw new Error('Choose a project again.');await api('/api/action',{action:'release',project:releaseTarget.id,confirmation:$('release-id').value,expectedRevision:releaseTarget.revision});$('release-dialog').close();releaseTarget=null;await poll();})));
$('quit').addEventListener('click',safe(()=>mutate(async()=>{await api('/api/quit',{});stopped=true;state.closing=true;$('mode').textContent='SERVICE STOPPED';$('operation-description').textContent='The interface service has stopped. You may close this tab.';document.querySelectorAll('button').forEach(button=>button.disabled=true);})));
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}});
async function initialize(){
 if(!token)throw new Error('Open the local link printed by INTERLINKED.cmd. Its private session token is required.');
 await loadCatalog();await poll();if(location.pathname==='/audit')await setTab(true);
 setInterval(()=>poll().catch(error=>{if(!stopped)showError('Local interface connection lost. Closing a tab does not stop a running launcher. Keep its service window open; use the original launcher Recover only after closing MAGIX. '+error.message);}),1200);
}
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
// END MASTER MODELS

// BEGIN MASTER INTERFACE
let masterSnapshot=null,masterVisible=false,masterBusy=false,masterSettingsLoaded=false,masterPollBusy=false,masterFrameBusy=false,masterLastFrame=0,masterFrameError='',masterFrameSequence=0,masterSourcesKey='';
const masterEvidenceOpen=new Set();
function masterElement(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function masterError(error){$('master-error').textContent=String(error?.message||error);$('master-error').hidden=false;}
function masterClearError(){$('master-error').hidden=true;}
function masterSetView(enabled){
 if(typeof jobsHide==='function')jobsHide();
 masterVisible=!!enabled;$('master-view').hidden=!enabled;$('library-shell').hidden=enabled;$('library-hub-bar').hidden=enabled;
 for(const [id,on] of [['master-view-tab',enabled],['library-view-tab',!enabled]]){$(id).classList.toggle('active',on);$(id).setAttribute('aria-pressed',String(on));}
 document.body.classList.toggle('master-open',enabled);
 if(enabled){masterPoll().catch(masterError);masterFetchFrame().catch(masterError);}
}
function masterRenderProjects(){
 const rows=masterSnapshot?.projects||[];$('master-project-count').textContent=String(rows.length).padStart(2,'0');$('master-projects').replaceChildren();
 if(!rows.length){$('master-projects').append(masterElement('p','master-muted','No project stations are registered. Add a source folder in settings.'));return;}
 for(const [index,row] of rows.entries()){
  const p=masterProjectModel(row),card=masterElement('article','master-project-station tone-'+p.tone);
  const glyph=masterElement('div','master-station-glyph');glyph.setAttribute('aria-hidden','true');glyph.append(masterElement('span','',String(index+1).padStart(2,'0')));for(let i=0;i<3;i++)glyph.append(masterElement('i','station-terminal'));
  const content=masterElement('div','master-station-content'),heading=masterElement('div','master-station-heading');heading.append(masterElement('h3','',p.label),masterElement('span','master-activity-chip',p.activity));
  const health=masterElement('div','master-health-line');health.append(masterElement('span','', 'BUILD / TEST'),masterElement('strong','tone-'+p.healthTone,p.health));
  const workspace=masterElement('div','master-health-line');workspace.append(masterElement('span','','WORKSPACE'),masterElement('strong','tone-'+p.workspaceTone,p.workspace));
  content.append(heading,health,masterElement('p','master-station-summary',p.summary),workspace,masterElement('p','master-station-summary',p.workspaceSummary));
  const meta=masterElement('div','master-station-meta');meta.append(masterElement('span','',p.branch),masterElement('span','',p.dirty));
  const evidence=masterElement('details','master-evidence');evidence.open=masterEvidenceOpen.has(p.id);evidence.addEventListener('toggle',()=>{if(evidence.open)masterEvidenceOpen.add(p.id);else masterEvidenceOpen.delete(p.id);});evidence.append(masterElement('summary','','Source & evidence'));
  evidence.append(masterElement('p','','Activity: '+p.source+' · '+masterWhen(p.observedAt)),masterElement('p','','Build/test result: '+p.healthSource+' · '+masterWhen(p.healthObservedAt)),masterElement('p','','Build/test baseline: '+p.baseline),masterElement('p','','Workspace: '+p.workspaceSource+' · '+masterWhen(p.workspaceObservedAt)),masterElement('p','','Observed agents: '+p.agents));
  content.append(meta,evidence);card.append(glyph,content);$('master-projects').append(card);
 }
}
function masterRenderSources(){
 const sources=masterSnapshot?.captureSources||[],key=JSON.stringify(sources);if(key===masterSourcesKey)return;masterSourcesKey=key;
 const select=$('master-capture-window'),chosen=select.value||masterSnapshot?.settings?.captureWindowId||'';select.replaceChildren(masterElement('option','','Choose a visible mini window…'));select.children[0].value='';
 for(const source of sources){const option=masterElement('option','',String(source.title||'Untitled window')+(source.process?' · '+source.process:''));option.value=String(source.id);select.append(option);}select.value=chosen;
}
function masterLoadSettings(){
 if(masterSettingsLoaded)return;const s=masterSnapshot?.settings||{};
 $('master-mode').value=s.mode||'dashboard';$('master-fps-setting').value=String(s.fps||10);$('master-background').checked=!!s.background;$('master-login').checked=!!s.startAtLogin;
 for(const key of ['x','y','width','height'])$('master-capture-'+key).value=String(s.captureRect?.[key]??(['width','height'].includes(key)?240:0));
 $('master-capture-enabled').checked=!!s.captureEnabled;masterSettingsLoaded=true;
}
function masterRenderRegistered(){
 const rows=masterSnapshot?.settings?.projects||masterSnapshot?.projects||[];$('master-registered-projects').replaceChildren();
 for(const row of rows){const line=masterElement('div','master-registered-project'),content=masterElement('div');content.append(masterElement('strong','',row.label||row.name||row.id),masterElement('small','',row.root||'Source folder not configured'));const remove=masterElement('button','master-remove','Remove');remove.type='button';remove.setAttribute('aria-label','Remove '+String(row.label||row.name||row.id)+' station');remove.disabled=masterBusy;remove.addEventListener('click',()=>masterRequest(async()=>{await api('/api/master/settings',{projects:rows.filter(p=>p.id!==row.id).map(p=>({id:p.id,label:p.label||p.name||p.id,root:p.root||''}))});await masterPoll();}).catch(masterError));line.append(content,remove);$('master-registered-projects').append(line);}
}
function masterRender(){
 const s=masterSnapshot;if(!s)return;
 const display=s.display||{},connected=!!display.connected;
 $('master-source-status').textContent=s.demo?'DEMO FEED':'LOCAL OBSERVATORY';$('master-source-status').className='master-status '+(s.demo?'attention':'active');
 $('master-observed').textContent='Snapshot '+masterWhen(s.observedAt||s.utc||s.generatedAt);$('master-demo-note').hidden=!s.demo;
 $('master-display-status').textContent=connected?'LCD connected':masterWords(display.status||'disconnected');$('master-display-message').textContent=display.message||display.error||'Close TRCC, then choose Connect display.';
 $('master-display-led').className='master-led '+(connected?'active':'');$('master-connect').disabled=masterBusy||connected||!!s.demo;$('master-disconnect').disabled=masterBusy||!connected;
 const stats=display.stats||s.stats||display;$('master-fps').textContent='LCD FPS '+(connected&&Number.isFinite(stats.deliveredFps)?stats.deliveredFps.toFixed(1):'—');$('master-dropped').textContent='DROPPED '+(Number.isFinite(stats.droppedFrames)?stats.droppedFrames:'—');
 $('master-cpu').textContent=masterTemperature(s.temperatures?.cpu);$('master-gpu').textContent=masterTemperature(s.temperatures?.gpu);
 $('master-sensor-note').textContent=s.temperatures?.message||s.temperatures?.reason||(($('master-cpu').textContent==='—'||$('master-gpu').textContent==='—')?'Unverified or unavailable readings stay blank.':'Live verified readings · '+masterWhen(s.temperatures?.observedAt));
 $('master-enable-sensors').disabled=masterBusy||!connected||!!s.demo;
 $('master-sensor-permission').textContent=s.sensorPermission?.message||'';$('master-sensor-permission').hidden=!s.sensorPermission?.message;
 const violet=s.violet||{};$('master-violet-status').textContent=violet.stale?'Violet’s status update is stale':violet.connected?masterWords(violet.activity||violet.status):'Waiting for Violet’s status bridge';const attention=s.attention||{};const pending=(attention.items||[]).filter(item=>!item.acknowledged);$('master-violet-message').textContent=(violet.source||'Unverified report')+' · '+(violet.message||'No fresh Violet report')+' · '+(attention.available?String(attention.pendingCount||0)+' queued for next Violet check'+(pending.length?': '+pending.slice(0,3).map(item=>item.projectId+' — '+item.summary).join('; '):''):'Attention queue unavailable');$('master-violet-freshness').textContent=violet.stale?'STALE UPDATE':violet.connected?masterWhen(violet.observedAt||violet.reportedAt):'NOT CONNECTED';$('master-violet-freshness').className='master-status '+(violet.connected&&!violet.stale?'active':'quiet');
 $('master-capture-status').textContent=s.capture?.message||s.capture?.reason||masterWords(s.capture?.status||'Live mini is not calibrated');
 masterRenderProjects();masterRenderSources();masterLoadSettings();masterRenderRegistered();
}
async function masterPoll(){
 if(masterPollBusy||stopped)return;masterPollBusy=true;
 try{masterSnapshot=await api('/api/master/state');masterRender();}finally{masterPollBusy=false;}
}
async function masterRequest(fn){if(masterBusy)throw new Error('Please wait for the current display request.');masterBusy=true;masterClearError();masterRender();try{return await fn();}finally{masterBusy=false;masterRender();}}
async function masterEnableSensors(){
 if(masterSnapshot?.demo||!masterSnapshot?.display?.connected)throw new Error('Connect the display before enabling temperatures.');
 await masterRequest(async()=>{await api('/api/master/display',{action:'enable_sensors'});await masterPoll();});
}
async function masterSaveSettings(){
 await masterRequest(async()=>{await api('/api/master/settings',{mode:$('master-mode').value,fps:Number($('master-fps-setting').value),background:$('master-background').checked,startAtLogin:$('master-login').checked});await masterPoll();$('master-settings-feedback').textContent='Preferences saved. TRCC startup is unchanged.';});
}
async function masterSaveCapture(){
 const source=(masterSnapshot?.captureSources||[]).find(v=>String(v.id)===$('master-capture-window').value),values={enabled:$('master-capture-enabled').checked};for(const key of ['x','y','width','height'])values[key]=$('master-capture-'+key).value;
 const patch=masterCapturePatch(masterSnapshot?.settings||{},source,values);
 await masterRequest(async()=>{await api('/api/master/settings',patch);$('master-mode').value='live_mini';await masterPoll();});
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
 const current=masterSnapshot?.settings?.projects||masterSnapshot?.projects||[];
 await masterRequest(async()=>{await api('/api/master/settings',{projects:[...current.map(p=>({id:p.id,label:p.label||p.name||p.id,root:p.root||''})),{id,label,root}]});$('master-project-label').value='';$('master-project-root').value='';await masterPoll();});
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
 $('master-capture-window').addEventListener('change',()=>{const selected=(masterSnapshot?.captureSources||[]).find(v=>String(v.id)===$('master-capture-window').value);if(selected?.bounds)for(const key of ['x','y','width','height'])$('master-capture-'+key).value=String(selected.bounds[key]??0);$('master-capture-enabled').checked=false;});
 $('master-black-pad').addEventListener('click',masterOpenCapturePad);$('master-close-pad').addEventListener('click',masterCloseCapturePad);
 setInterval(()=>{if(masterVisible&&!stopped)masterPoll().catch(error=>{masterError(error);$('master-source-status').textContent='CONNECTION LOST';$('master-source-status').className='master-status danger';});},1500);
 setInterval(()=>masterFetchFrame(),100);
 setInterval(()=>{if(masterVisible)$('master-frame-age').textContent=masterFrameAge(masterSnapshot?.display,masterLastFrame,masterFrameError);},1000);
 if(new URLSearchParams(location.search).get('view')==='master')masterSetView(true);
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
 return {available,observed:String(value?.source_observed_at||value?.observed_at||''),counts:{total:number('total'),prepared:number('prepared'),delivered:number('delivered'),attention:number('attention'),running:number('running'),queued:number('queued')},tasks:(Array.isArray(value?.tasks)?value.tasks:[]).slice(0,100),truncated:value?.truncated===true};
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
  const delivery=document.createElement('span');delivery.className='buddy-report-delivery';delivery.textContent='Project delivery: '+String(task.delivery_status||'not_delivered');
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
 $('buddy-report-limits').textContent=(model.truncated?'Showing up to 100 tasks, with attention items first. ':'')+'Prepared, delivered, and saved to MSI are separate outcomes. Save-back remains explicit.';
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
