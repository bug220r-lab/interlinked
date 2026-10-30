'use strict';
// Synthetic DOM, two-client conflicts and mocked local metadata APIs only.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
const models=source.split('// BEGIN MASTER MODELS\n')[1].split('// END MASTER MODELS')[0];
const ui=source.split('// BEGIN MASTER INTERFACE\n')[1].split('// END MASTER INTERFACE')[0];
const hooks=source.slice(source.indexOf("if($('master-view')){"),source.indexOf("if($('msi-jobs-view')){"));
const elements={},requests=[],checks=[];
function element(){return {hidden:false,disabled:false,textContent:'',value:'',checked:false,indeterminate:false,open:false,children:[],listeners:{},attributes:{},className:'',classList:{toggle(){}},
 append(...rows){this.children.push(...rows);},replaceChildren(...rows){this.children=rows;},setAttribute(key,value){this.attributes[key]=value;},addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);},
 async emit(type){for(const fn of this.listeners[type]||[])await fn({preventDefault(){}});},show(){this.open=true;},showModal(){this.open=true;},close(){this.open=false;},querySelector(){return element();},getContext(){return {drawImage(){}};},scrollIntoView(){},focus(){}};}
const get=id=>elements[id]??=element();
const time='2026-10-30T18:00:00Z';
let snapshot={settingsValid:true,lifecyclePreferencesKnown:true,settingsRevision:'r1',settings:{mode:'dashboard',fps:10,background:true,startAtLogin:false,projects:[{id:'one',label:'One',root:'C:\\one',activityRoot:'C:\\activity'}]},projects:[{id:'one',label:'One'}],display:{connected:false},captureSources:[],violet:{},temperatures:{}};
let queue={available:true,items:[]},failSave=false,failRestore=false,saves=0,restores=0,previewDeferred=null;
let preferences={favorites:['dormant'],recent:[{id:'dormant',utc:'2025-01-01T00:00:00Z'}]},adopted=null,rendered=0;
const api=async(route,body)=>{requests.push({route,body});
 if(route==='/api/master/state')return snapshot;
 if(route==='/api/master/settings'){
  saves++;if(failSave||body.expectedRevision!==snapshot.settingsRevision){const error=new Error('Stale settings');error.status=409;error.payload={code:'CONFIGURATION_CONFLICT',current:JSON.parse(JSON.stringify(snapshot))};throw error;}
  const {expectedRevision,...patch}=body;snapshot={...snapshot,settingsRevision:'r'+(saves+2),settings:{...snapshot.settings,...patch}};return snapshot;
 }
 if(route==='/api/master/attention')return queue;
 if(route==='/api/master/attention/acknowledge'){queue={...queue,items:queue.items.map(item=>body.ids.includes(item.id)?{...item,acknowledged:true}:item)};return {acknowledged:body.ids,resolvesProjectFailure:false,queue};}
 if(route.startsWith('/api/configuration/history?'))return {available:true,items:[{id:'saved-1',revision:'old',observedAt:time,reason:'Favorite changed'}]};
 if(route==='/api/configuration/preview'){if(previewDeferred)return previewDeferred;return {previewId:'preview-1',scope:body.scope,current:{value:{favorites:[]}},proposed:body.scope==='library'?preferences:{settings:{fps:5,captureEnabled:false}}};}
 if(route==='/api/configuration/restore'){restores++;if(failRestore){const error=new Error('Preview is stale');error.status=409;throw error;}return {restartRequired:body.scope==='master'};}
 if(route==='/api/catalog')return {preferences};
 throw new Error('Unexpected API '+route);
};
const context={document:{createElement:()=>element(),body:{classList:{toggle(){}}},hidden:false},$:get,api,stopped:false,token:'fixture',location:{search:''},setInterval(){},URLSearchParams,adoptPreferences:value=>{adopted=value;},renderList:()=>{rendered++;}};
vm.createContext(context);vm.runInContext(models+'\n'+ui+'\n'+hooks,context);
const read=code=>vm.runInContext(code,context);
function check(value,message){assert.ok(value,message);checks.push(message);}
function text(node){return [node.textContent,...node.children.flatMap(text)].join(' ');}
(async()=>{
 await context.masterPoll();
 check(read('masterDrafts.display.revision')==='r1','Forms capture the revision that supplied their values');
 get('master-fps-setting').value='5';await get('master-fps-setting').emit('input');
 snapshot={...snapshot,settingsRevision:'r2',settings:{...snapshot.settings,fps:15}};await context.masterPoll();
 check(get('master-fps-setting').value==='5'&&read('masterDrafts.display.revision')==='r1','Background polling preserves an unsaved value and its original revision');
 snapshot={...snapshot,restartRequired:true,configurationNotice:'External settings changed. Restart is required.'};await context.masterPoll();
 check(!get('master-configuration-notice').hidden&&get('master-configuration-notice').textContent.includes('External settings')&&get('master-fps-setting').value==='5','External configuration notices remain visible without replacing unsaved drafts');
 snapshot={...snapshot,restartRequired:false,configurationNotice:''};await context.masterPoll();
 check(get('master-configuration-notice').hidden,'Configuration notice clears when the backend no longer reports one');
 let rejected=false;try{await context.masterSaveSettings();}catch(_){rejected=true;}
 check(rejected&&saves===1&&requests.filter(row=>row.route==='/api/master/settings')[0].body.expectedRevision==='r1','A stale save is attempted once using the captured revision');
 check(!get('master-settings-conflict').hidden&&get('master-conflict-current').textContent.includes('15')&&get('master-conflict-draft').textContent.includes('5'),'Conflict presents both current values and the attempted edit');
 check(get('master-fps-setting').value==='5','Conflict handling retains the editable draft');
 context.masterReloadConflict();check(get('master-fps-setting').value==='15'&&read('masterDrafts.display.revision')==='r2','Explicit reload adopts current values and their revision');
 get('master-fps-setting').value='5';await get('master-fps-setting').emit('input');await context.masterSaveSettings();
 check(saves===2&&snapshot.settings.fps===5&&!read('masterDrafts.display.dirty'),'A reviewed resubmission saves successfully without retrying the stale request');
 get('master-capture-x').value='777';await get('master-capture-x').emit('input');get('master-fps-setting').value='10';await get('master-fps-setting').emit('input');await context.masterSaveSettings();
 check(get('master-capture-x').value==='777'&&read('masterDrafts.capture.dirty'),'Saving one section preserves an unsaved capture draft');
 get('master-project-label').value='Second';get('master-project-root').value='C:\\two';await get('master-project-label').emit('input');await context.masterAddProject({preventDefault(){}});
 const added=requests.filter(row=>row.route==='/api/master/settings').at(-1).body;
 check(added.projects[0].activityRoot==='C:\\activity'&&added.projects[1].id==='second','Project additions preserve optional registration fields');
 await context.masterRemoveProject('second',snapshot.settings.projects,snapshot.settingsRevision);
 check(requests.filter(row=>row.route==='/api/master/settings').at(-1).body.projects[0].activityRoot==='C:\\activity','Project removal preserves remaining activity roots');
 snapshot={...snapshot,lifecyclePreferencesKnown:false};await context.masterPoll();
 check(get('master-background').indeterminate&&get('master-login').indeterminate&&!get('master-lifecycle-notice').hidden,'Unknown startup/background choices are displayed explicitly');
 const beforeUnknown=saves;rejected=false;try{await context.masterSaveSettings();}catch(_){rejected=true;}
 check(rejected&&saves===beforeUnknown,'Unknown lifecycle preferences cannot be silently replaced by checkbox defaults');
 await get('master-background').emit('input');await get('master-login').emit('input');await context.masterSaveSettings();
 check(saves===beforeUnknown+1,'Explicitly choosing both lifecycle preferences enables a normal save');
 snapshot={...snapshot,settingsValid:false};await context.masterPoll();
 check(get('master-save-settings').disabled&&get('master-save-capture').disabled,'Unavailable MASTER settings disable ordinary saves without hiding recovery');
 snapshot={...snapshot,settingsValid:true,lifecyclePreferencesKnown:true};
 const items=[{id:'input',projectId:'gone',kind:'input_needed',summary:'Input requested',source:'Codex',observedAt:'2026-10-30T19:00:00Z',acknowledged:false},{id:'older',projectId:'one',kind:'failure',summary:'Build/test failed',source:'Tests',observedAt:'2026-10-30T17:00:00Z',acknowledged:false},{id:'newer',projectId:'one',kind:'failure',summary:'<img onerror=bad>',source:'<script>bad</script>',observedAt:time,acknowledged:false},{id:'done',projectId:'gone',kind:'failure',summary:'Old failure',source:'Tests',observedAt:time,acknowledged:true}];
 queue={available:true,items};await context.masterPollAttention();
 check(read("masterInboxModel(masterAttentionQueue,{review:'unreviewed'}).rows.map(row=>row.id).join(',')")==='newer,older,input','Inbox prioritizes failures, then newest observations, without inventing current status');
 check(text(get('master-attention-list')).includes('<img onerror=bad>')&&text(get('master-attention-list')).includes('<script>bad</script>'),'Untrusted historical summaries and source names render as literal text');
 check(get('master-attention-project').children.some(option=>option.value==='gone'),'Filters retain IDs for projects no longer registered');
 const beforeReads=requests.filter(row=>row.route.endsWith('/acknowledge')).length;get('master-attention-project').value='gone';await get('master-attention-project').emit('change');get('master-attention-review').value='all';await get('master-attention-review').emit('change');await context.masterPollAttention();
 check(requests.filter(row=>row.route.endsWith('/acknowledge')).length===beforeReads,'Reading and filtering never acknowledge observations');
 check(get('master-attention-project').value==='gone'&&get('master-attention-review').value==='all','Inbox filters survive polling');
 read("masterAttentionSelected.add('older');masterAttentionSelected.add('input')");context.masterRenderAttention();await context.masterAcknowledge();
 check(requests.filter(row=>row.route.endsWith('/acknowledge')).at(-1).body.ids.join(',')==='older,input','Batch review sends only exact selected IDs, including hidden selections');
 check(queue.items.find(item=>item.id==='older').acknowledged&&!queue.items.find(item=>item.id==='newer').acknowledged&&read('masterAttentionSelected.size')===0,'Successful acknowledgment changes only selected review states');
 read("masterAttentionSelected.add('newer')");queue={available:true,items:queue.items.filter(item=>item.id!=='newer')};await context.masterPollAttention();
 check(get('master-attention-ack').disabled&&get('master-attention-notice').textContent.includes('no longer retained')&&read('masterAttentionSelected.size')===1,'An evicted selection is retained with a notice instead of silently changing the batch');
 await get('master-attention-clear').emit('click');check(read('masterAttentionSelected.size')===0,'Only explicit clear removes an expired selection');
 queue={available:false};await context.masterPollAttention();check(get('master-attention-count').textContent==='Unavailable'&&get('master-attention-notice').textContent.includes('preserved'),'Unreadable queue does not appear as a healthy empty inbox');
 await context.configurationOpen('library');
 check(get('configuration-dialog').open&&get('configuration-list').children.length===1&&text(get('configuration-list')).includes(time),'History is accessible from the shared dialog with source timestamps');
 check(restores===0&&get('configuration-restore').disabled,'Listing history cannot restore or activate settings');
 await context.configurationShowPreview('library','saved-1');
 check(!get('configuration-preview').hidden&&get('configuration-proposed').textContent.includes('2025-01-01')&&!get('configuration-restore').disabled,'Preview shows exact recorded recent dates before enabling restore');
 await context.configurationRestore();
 check(restores===1&&adopted===preferences&&rendered===1&&get('configuration-feedback').textContent.includes('selected project'),'Library restore refreshes only preference/list data and leaves selected work intact');
 await context.configurationOpen('master');await context.configurationShowPreview('master','saved-1');await context.configurationRestore();
 check(get('configuration-feedback').textContent.includes('Restart INTERLINKED'),'Unavailable MASTER recovery clearly requires restart rather than activating hardware');
 await context.configurationShowPreview('master','saved-1');failRestore=true;rejected=false;try{await context.configurationRestore();}catch(_){rejected=true;}failRestore=false;
 check(rejected&&get('configuration-restore').disabled&&read('configurationPreview')===null&&get('configuration-feedback').textContent.includes('not confirmed'),'A stale/failed restore requires a fresh preview and never retries automatically');
 let release;previewDeferred=new Promise(resolve=>{release=resolve;});const pending=context.configurationShowPreview('master','old-request');get('configuration-scope').value='library';await context.configurationHistory();release({previewId:'late',current:{},proposed:{fps:5}});await pending;previewDeferred=null;
 check(read('configurationPreview')===null&&get('configuration-restore').disabled,'A delayed preview cannot attach to another scope or newer history view');
 check(requests.every(row=>!['/api/action','/api/session','/api/master/display','/api/mw5/apply'].includes(row.route)),'Settings, review and recovery interfaces never dispatch project or device actions');
 check(!ui.includes('innerHTML')&&!ui.includes('eval('),'All review/recovery details use text nodes rather than executable markup');
 console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Synthetic two-window editing, inbox and recovery DOM/API checks only'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
