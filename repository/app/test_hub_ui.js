'use strict';
// Pure models and synthetic DOM/API responses only. No browser, network or apps.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8');
const models=source.split('// BEGIN HUB MODELS\n')[1].split('// END HUB MODELS')[0];
const ui=source.split('// BEGIN HUB INTERFACE\n')[1].split('// END HUB INTERFACE')[0];
const elements={},requests=[],checks=[];
function element(){return {hidden:false,disabled:false,textContent:'',children:[],classList:{toggle(){}},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;}};}
let responder=async()=>({favorites:[],recent:[]});
const magix={id:'magix-one',name:'A MAGIX family',type:'magix',entryPoints:[],cache:{status:'READY'},warnings:[],mediaStatus:'REFERENCES_COVERED'};
const mw5={id:'library-opaque',originalId:'mw5-companion',name:'MW5 mod',adapter:'library',type:'mw5',cache:{status:'READY'},capabilities:{openApplication:true,fetch:true,save:false,release:false},warnings:[]};
const coding={id:'library-code',originalId:'Code_Original',name:'Code project',adapter:'library',projectType:'coding_data',cache:{status:'ABSENT'},warnings:[]};
const prefs={favorites:[mw5.id],recent:[{id:coding.id,utc:'2026-01-01T00:00:00Z'}]};
const context={
 document:{createElement:()=>element()},$:id=>elements[id]??=element(),
 state:{busy:false,closing:false},mutation:false,selected:mw5,projects:[magix,mw5,coding],preferences:prefs,
 hub:{libraryStatus:'OFFLINE',returnsStatus:'UNKNOWN',catalogStale:true,catalogRefreshedUtc:'2026-01-01T00:00:00Z',storage:{localFreeBytes:50,msiFreeBytes:null}},
 mw5Readiness:null,mw5Preview:null,mw5Sequence:0,
 human:value=>String(value).replaceAll('_',' '),size:value=>String(value)+' bytes',asText:value=>typeof value==='string'?value:JSON.stringify(value),
 api:async(route,body)=>{requests.push({route,body});return responder(route,body);},
 renderList(){},renderDetail(){},loadCatalog:async()=>{},poll:async()=>{},mutate:async fn=>fn(),confirm:()=>true,
};
vm.createContext(context);vm.runInContext(models+'\n'+ui,context);
function check(ok,label){assert.ok(ok,label);checks.push(label);}
(async()=>{
 check(context.projectMatches(magix,'magix','',prefs)&&!context.projectMatches(mw5,'magix','',prefs),'MAGIX view includes only the matching type');
 check(context.projectMatches(mw5,'mw5','mw5-companion',prefs),'MW5 can be searched by its original catalog ID');
 check(context.projectMatches(coding,'coding_data','Code_Original',prefs),'Coding/Data accepts the projectType alias and original ID');
 check(context.projectMatches(coding,'recent','',prefs)&&!context.projectMatches(mw5,'recent','',prefs),'Recent accepts timestamped preference records');
 check(context.projectMatches(mw5,'favorites','',prefs)&&!context.projectMatches(coding,'favorites','',prefs),'Favorites uses backend-owned project identities');
 check(context.projectMatches(mw5,'local','',prefs)&&!context.projectMatches(coding,'local','',prefs),'Local is distinct from an absent cache');
 check(context.hasCapability(mw5,'fetch')&&!context.hasCapability(mw5,'save')&&!context.hasCapability(coding,'openApplication'),'Unavailable or unspecified generic capabilities fail closed');
 check(context.hasCapability(magix,'openApplication'),'Legacy MAGIX catalog compatibility retains its launcher capability');
 check(context.canSelectProject(mw5,{busy:true,activeSession:{kind:'mw5-recovery',projectId:null}},false)&&!context.canSelectProject(coding,{busy:true,activeSession:{kind:'mw5-recovery',projectId:null}},false),'Interrupted MW5 recovery permits selecting its adapter while other projects remain blocked');
 check(context.releaseConfirmation(mw5)==='mw5-companion'&&context.releaseConfirmation(magix)==='magix-one','Release confirmation displays original ID without replacing opaque action identity');
 check(context.evidenceText(null,'verification').includes('No verification'),'Absent evidence cannot become a verified badge');
 check(context.evidenceText({utc:'2026-01-01',currentBaseline:false,scope:'saved files'},'verification').includes('current files are not certified'),'Old verification evidence explicitly does not certify current files');
 context.renderHub();
 check(elements['hub-connection'].textContent.includes('OFFLINE')&&elements['hub-refresh-time'].textContent.includes('cached snapshot'),'Offline library visibly marks cached catalog provenance');
 check(elements['hub-storage'].textContent.includes('MSI unknown'),'Unknown remote capacity never renders as zero or available');
 context.state={busy:true,operation:{projectId:mw5.id,action:'open',awaitingDone:true}};context.renderSessionBanner();
 check(!elements['session-done'].hidden&&elements['session-next'].textContent.includes('close the application'),'Sticky session controls guide generic editor completion');
 context.state={busy:false,closing:false};context.mw5Readiness={canStage:true,canCollect:false,session:{status:'STAGED',canOpen:false},editor:{installed:true,path:'C:\\Editor'}};context.renderMw5Controls();
 check(!elements['mw5-stage'].disabled&&elements['mw5-collect'].disabled&&elements.open.disabled,'MW5 readiness gates staging, collection and editor launch independently');
 context.state={busy:true,transferring:false,activeSession:{kind:'mw5',projectId:mw5.id,nextAction:'Close editor and collect changes.'}};context.mw5Readiness={canStage:false,canCollect:true,session:{status:'STAGED',canOpen:true}};context.renderMw5Controls();context.renderSessionBanner();
 check(!elements['mw5-collect'].disabled&&!elements.open.disabled&&elements['mw5-stage'].disabled,'Owned MW5 session permits opening and collecting while retaining its session protection');
 check(elements['session-next'].textContent==='Close editor and collect changes.'&&elements['session-done'].hidden,'MW5 next action comes from the session and cannot bypass collection with Done');
 context.state.transferring=true;context.renderMw5Controls();check(elements.open.disabled&&elements['mw5-collect'].disabled,'An active transfer blocks competing MW5 commands');context.state={busy:false,closing:false};
 context.state={busy:true,transferring:false,activeSession:{kind:'mw5-recovery',projectId:null}};context.mw5Readiness={session:{status:'NOT_CHECKED'}};context.renderMw5Controls();check(!elements['mw5-refresh'].disabled,'Recovering MW5 can explicitly check editor readiness while protected');
 context.state={busy:true,transferring:false,activeSession:{kind:'mw5',projectId:mw5.id}};context.mw5Readiness={session:{status:'COLLECTED',canFinish:true}};context.renderMw5Controls();check(!elements['mw5-finish'].hidden&&!elements['mw5-finish'].disabled,'Finish recovered session is offered only for completed collection');
 responder=async()=>({status:'STARTING'});await context.finishMw5();check(requests.some(r=>r.route==='/api/mw5/finish'&&r.body?.project===mw5.id),'Recovered finish requests the guarded MW5 finish route without save-back');context.state={busy:false,closing:false};
 const preview={id:'preview-one',direction:'collect',applicable:true,changes:[{path:'<script>asset.uasset</script>',action:'delete',bytes:10}],conflicts:[],warnings:['Review deletion']};
 context.mw5Preview=preview;context.renderMw5Preview();
 check(elements['mw5-preview-changes'].children[0].textContent.includes('<script>')&&elements['mw5-preview-summary'].textContent.includes('1 file change'),'Transfer paths and file operations render literally, not as markup');
 context.mw5Preview={...preview,conflicts:[{path:'asset.uasset',reason:'Both copies changed'}]};context.renderMw5Preview();
 check(elements['mw5-apply'].disabled&&elements['mw5-preview-conflicts'].children[0].textContent.includes('Both copies changed'),'Conflicts remain visible and cannot be applied');
 check(!context.transferCanApply({...preview,applicable:undefined}),'Missing applicability proof disables apply');
 context.mw5Preview=preview;responder=async()=>({status:'STARTING'});await context.applyMw5();
 const apply=requests.find(r=>r.route==='/api/mw5/apply');
 check(apply.body.project===mw5.id&&apply.body.previewId==='preview-one'&&apply.body.confirmation==='COLLECT'&&Object.keys(apply.body).length===3,'Collection submits captured preview identity and confirmation, never filesystem paths');
 check(!requests.some(r=>r.route==='/api/action'&&r.body?.action==='save'),'Collect does not automatically save to MSI');
 context.selected=mw5;responder=async()=>({status:'STARTING'});await context.previewMw5('stage');
 const staging=requests.find(r=>r.route==='/api/mw5/preview');
 check(staging.body.direction==='stage'&&Object.keys(staging.body).length===2,'Staging preview request accepts only project and direction');
 await context.checkMw5();check(requests.some(r=>r.route==='/api/mw5/check'&&r.body?.project===mw5.id),'Check editor explicitly starts the asynchronous readiness operation');
 let complete;responder=()=>new Promise(resolve=>{complete=resolve;});context.selected=mw5;const pending=context.loadMw5();context.selected=coding;complete({ready:true,preview});await pending;
 check(context.mw5Readiness?.ready!==true,'Late MW5 readiness cannot be applied to a different selected project');
 context.selected=mw5;responder=async()=>({favorites:[mw5.id],recent:[]});await context.toggleFavorite();
 check(requests.some(r=>r.route==='/api/preferences'&&r.body?.favorite===mw5.id&&r.body.enabled===false),'Favorite changes use the authenticated preferences API');
 for(const kind of ['all','magix','mw5','coding_data','local','recent','favorites'])check(html.includes('data-filter="'+kind+'"'),'Required '+kind+' view has an explicit control');
 check(html.indexOf('id="session-banner"')<html.indexOf('id="project-name"'),'Active session guidance appears before project detail');
 check(!ui.includes('innerHTML')&&!ui.includes('eval('),'Hub and transfer rendering never executes or parses supplied text');
 const stats={id:'stats-ui',originalId:'stats-active',type:'stats',name:'The Stats',cache:{status:'READY'}};
 check(context.canSelectProject(stats,{busy:true,activeSession:{kind:'mw5',projectId:mw5.id}},false),'Stats remains selectable alongside an MW5 session');
 check(context.projectMatches(stats,'coding_data','',prefs),'Stats remains in the Coding/Data filter');
 const running=context.statsControls({status:'RUNNING',running:true,ready:true},{busy:true,transferring:false});
 check(running.stop&&running['open-browser']&&!running.save&&!running.release,'Running Stats can be stopped alongside another project and cannot be saved or released');
 const idle=context.statsControls({status:'IDLE',running:false,ready:true},{busy:true,transferring:false});
 check(idle.start&&idle.save,'An unrelated editor session does not disable idle Stats actions');
 check(!context.statsControls({status:'IDLE',ready:true},{transferring:true}).save,'Actual transfer contention disables Stats save');
 check(context.statsControls({status:'RECOVERY_REQUIRED'},{}).acknowledge&&!context.statsControls({status:'RECOVERY_REQUIRED'},{}).start,'Interrupted Stats requires recovery acknowledgement');
 check(html.includes('Checkout code requires Git commit/push')&&html.includes('Closing a browser tab does not stop Stats'),'Stats explains save scope and browser lifetime');
 console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Synthetic UI models, DOM and mocked local APIs; no production operations'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
