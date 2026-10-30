'use strict';
const $=id=>document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('interlinked-token')||'';
if(token){sessionStorage.setItem('interlinked-token',token);history.replaceState(null,'',location.pathname+location.search);}
let projects=[], selected=null, state={busy:false,revision:0}, filter='all', auditLoaded=null, releaseTarget=null, lastCompleted=null, stopped=false;
let mutation=false,pollSequence=0,appliedPoll=0,entrySequence=0,auditSequence=0;
let requestedAction=null;
let missingSequence=0,notePending=false,missingRecords=[];
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
function isLocal(p){return ['READY','PENDING_SAVE','FETCHING','RELEASING'].includes(p.cache.status);}
function needsReview(p){const text=String(p.mediaStatus).toLowerCase();return !text.includes('covered')&&!text.includes('ready')||p.warnings.length>0;}
function renderList(){
 const search=$('search').value.toLowerCase().trim();
 const rows=projects.filter(p=>(!search||(p.name+' '+p.id).toLowerCase().includes(search))&&(filter==='all'||filter==='local'&&isLocal(p)||filter==='review'&&needsReview(p)));
 $('project-count').textContent=projects.length;$('project-list').replaceChildren();
 for(const p of rows){
  const button=document.createElement('button');button.type='button';button.disabled=state.busy||mutation;button.className='project-item'+(selected?.id===p.id?' active':'');button.setAttribute('aria-pressed',String(selected?.id===p.id));
  const name=document.createElement('strong');name.textContent=p.name;
  const sub=document.createElement('span');sub.className='subline';
  const bytes=document.createElement('span');bytes.textContent=size(p.bytes);
  const local=document.createElement('span');local.textContent=p.cache.status==='READY'?'● LOCAL':p.cache.status==='DEMO'?'PREVIEW':human(p.cache.status);
  sub.append(bytes,local);const entries=document.createElement('span');entries.className='entry-count';entries.textContent=p.entryPoints.length+' project / recovery entries';
  button.append(name,sub,entries);button.addEventListener('click',()=>select(p.id));$('project-list').append(button);
 }
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No projects match this view.';$('project-list').append(empty);}
}
function renderDetail(){
 if(!selected)return;
 $('project-code').textContent=selected.id;$('project-name').textContent=selected.name;
 $('project-subtitle').textContent=selected.cache.status==='READY'?'Your local edits are kept. Open this working copy without fetching again.':'Prepare one family at a time. Keep its media local while you work.';
 $('size').textContent=size(selected.bytes);$('file-count').textContent=selected.files.toLocaleString()+' selected files';
 $('cache-state').textContent=selected.cache.status==='ABSENT'?'Not fetched':human(selected.cache.status);
 $('cache-note').textContent=selected.cache.warning||'Metadata status, not verification';
 $('media-health').textContent=human(selected.mediaStatus);
 const count=selected.mediaCounts.mediaReferenceCandidates??selected.mediaCounts.referenceCandidates;
 $('audit-count').textContent=count!=null?count.toLocaleString()+' strings':'';
 const entry=chosenEntries.get(selected.id);$('entry').replaceChildren();
 for(const item of selected.entryPoints){const option=document.createElement('option');option.value=item.path;const name=item.path.split('/').pop();option.textContent=(item.origin==='LOCAL_SAVE_AS'?'[LOCAL SAVE AS] ':item.kind==='recovery'?'[BACKUP] ':'[PROJECT] ')+name;option.title=item.path;$('entry').append(option);}
 if(entry&&selected.entryPoints.some(e=>e.path===entry))$('entry').value=entry;
 else if(entry){const missing=document.createElement('option');missing.value='';missing.textContent=selected.loadingEntries?'Checking your previous selection…':'Previous selection unavailable — choose another entry';missing.disabled=true;missing.selected=true;$('entry').prepend(missing);}
 else if($('entry').value)chosenEntries.set(selected.id,$('entry').value);
 $('warnings').replaceChildren();
 for(const warning of [...selected.warnings,...selected.notes]){const li=document.createElement('li');li.textContent=String(warning);$('warnings').append(li);}
 updateEntry();renderState();
}
function select(id){const previous=selected?.id;selected=projects.find(p=>p.id===id)||projects[0];auditLoaded=null;auditSequence++;if(selected)selected.loadingEntries=true;renderList();renderDetail();if(selected)loadEntries(selected.id).catch(error=>{if(selected?.id===id){selected.loadingEntries=false;renderState();}showError(error);});if(previous!==selected?.id){$('missing-path').value='';$('missing-note').value='';}loadMissing().catch(missingError);if(!$('audit-panel').hidden)loadAudit().catch(showError);}
async function loadEntries(id){const sequence=++entrySequence,result=await api('/api/entries?project='+encodeURIComponent(id));if(selected?.id!==id||sequence!==entrySequence)return;selected.entryPoints=result.entries;selected.loadingEntries=false;renderDetail();if(result.warnings.length)$('entry-note').textContent+=' '+result.warnings.join(' ');}
function updateEntry(){const item=selected?.entryPoints.find(e=>e.path===$('entry').value);$('entry-note').textContent=item?.kind==='recovery'?'Backup selected: MAGIX opens, then choose File > Load backup project using the exact local path shown in Activity. The backup is not renamed.':'The reviewed launcher uses a short local path to the selected cached project. It does not stream media from MSI.';}
async function loadCatalog(){
 const catalog=await api('/api/catalog');projects=catalog.projects;
 $('demo-banner').hidden=!catalog.demo;$('mode').lastChild.textContent=catalog.demo?'DEMO · NO REAL FILES':'LOCAL CONTROL';
 $('snapshot-date').textContent='CATALOG '+String(catalog.indexDate||'snapshot').split('T')[0]+' · AUDIT '+(catalog.auditAvailable?'AVAILABLE':'NOT INSTALLED');
 select(selected?.id||initialProject||projects[0]?.id);
}
async function action(name){
 if(!selected&&name!=='recover')throw new Error('Choose a project first.');
 if(name==='recover'&&!confirm('Close all MAGIX windows first. Recover removes only the recorded local mappings; it does not save or free project files. Continue?'))return;
 await mutate(async()=>{await api('/api/action',{action:name,project:selected?.id,entry:name==='open'?$('entry').value:undefined});await poll();},name);
}
function renderState(){
 const job=state.operation,busy=state.busy,locked=busy||mutation||state.closing;
 for(const id of ['open','save','verify','release','recover'])$(id).disabled=locked||!selected;
 $('quit').disabled=locked||notePending;$('entry').disabled=locked||selected?.loadingEntries;
 for(const button of document.querySelectorAll('.project-item'))button.disabled=locked;
 if(selected&&!locked){$('open').disabled=selected.loadingEntries||!$('entry').value;$('save').disabled=!['READY','PENDING_SAVE','DEMO'].includes(selected.cache.status);$('release').disabled=!['READY','DEMO','RELEASING'].includes(selected.cache.status);}
 document.querySelector('.activity').classList.toggle('busy',busy);
 $('operation-status').textContent=job?human(job.status):'Ready when you are';
 const runningName=projects.find(p=>p.id===job?.projectId)?.name||job?.projectId||'owned mappings';
 $('operation-description').textContent=job?(human(job.action)+' · '+runningName+(job.error?' · '+job.error:'')):'Actions run locally. Nothing is saved or freed without your request.';
 $('session-controls').hidden=!(busy&&job?.action==='open');
 $('done').disabled=mutation||!job?.awaitingDone;$('cancel-startup').disabled=mutation||!job?.awaitingDone;
 $('release-confirm').disabled=mutation||$('release-id').value!==releaseTarget?.id;
 $('done').textContent=job?.awaitingDone?'I saved and closed MAGIX':'Waiting for the launcher…';
 const log=$('operation-log'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<35;log.textContent=state.output||'No operation output yet.';if(atEnd)log.scrollTop=log.scrollHeight;
 $('log-warning').hidden=!job?.reportingWarning;$('log-warning').textContent=job?.reportingWarning||'';
 $('recovery-note').hidden=!state.previousIncomplete?.length;
 $('recovery-note').textContent='A previous service run ended with an unfinished operation. Do not assume it stopped. Close MAGIX and check the prior launcher before using Recover. No files were automatically released.';
 renderOpenFeedback();
}
async function poll(){
 if(stopped)return;
 const sequence=++pollSequence,value=await api('/api/state');if(sequence<appliedPoll)return;appliedPoll=sequence;state=value;renderState();
 if(state.operation&&!state.busy&&state.operation.id!==lastCompleted){const completed=state.operation.id;await loadCatalog();lastCompleted=completed;}
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
for(const name of ['open','save','verify','recover'])$(name).addEventListener('click',safe(()=>action(name)));
$('done').addEventListener('click',safe(()=>sendResponse('DONE')));$('cancel-startup').addEventListener('click',safe(()=>sendResponse('CANCEL STARTUP')));
$('workspace-tab').addEventListener('click',safe(()=>setTab(false)));$('audit-tab').addEventListener('click',safe(()=>setTab(true)));
$('audit-window').addEventListener('click',()=>{if(selected)window.open('/audit?project='+encodeURIComponent(selected.id)+'#'+token,'_blank','noopener');});
$('toggle-log').addEventListener('click',()=>{$('operation-log').hidden=!$('operation-log').hidden;$('toggle-log').textContent=$('operation-log').hidden?'Show details':'Hide details';});
$('open-feedback-link').addEventListener('click',()=>{$('operation-log').hidden=false;$('toggle-log').textContent='Hide details';document.querySelector('.activity').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});});
$('release').addEventListener('click',()=>{releaseTarget={id:selected.id,name:selected.name,revision:state.revision};$('release-description').textContent=selected.name+' · '+size(selected.bytes)+' selected working set estimate.';$('release-expected').textContent=selected.id;$('release-id').value='';$('release-confirm').disabled=true;$('release-dialog').showModal();});
$('release-id').addEventListener('input',()=>{$('release-confirm').disabled=$('release-id').value!==releaseTarget?.id;});
$('release-confirm').addEventListener('click',safe(()=>mutate(async()=>{if(!releaseTarget)throw new Error('Choose a project again.');await api('/api/action',{action:'release',project:releaseTarget.id,confirmation:$('release-id').value,expectedRevision:releaseTarget.revision});$('release-dialog').close();releaseTarget=null;await poll();})));
$('quit').addEventListener('click',safe(()=>mutate(async()=>{await api('/api/quit',{});stopped=true;state.closing=true;$('mode').textContent='SERVICE STOPPED';$('operation-description').textContent='The interface service has stopped. You may close this tab.';document.querySelectorAll('button').forEach(button=>button.disabled=true);})));
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}});
async function initialize(){
 if(!token)throw new Error('Open the local link printed by INTERLINKED.cmd. Its private session token is required.');
 await loadCatalog();await poll();if(location.pathname==='/audit')await setTab(true);
 setInterval(()=>poll().catch(error=>{if(!stopped)showError('Local interface connection lost. Closing a tab does not stop a running launcher. Keep its service window open; use the original launcher Recover only after closing MAGIX. '+error.message);}),1200);
}
initialize().catch(showError);
