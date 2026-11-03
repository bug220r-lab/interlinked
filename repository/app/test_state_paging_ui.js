'use strict';
// Current UI functions with synthetic DOM and local API fixtures only.
// No browser, network, archive, worker, model, clipboard or hardware access.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8'),html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8');
function block(name){const value=source.split('// BEGIN '+name+'\n')[1]?.split('// END '+name)[0];assert.ok(value,'Current '+name+' block exists');return value;}
const checks=[];function check(value,label){assert.ok(value,label);checks.push(label);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const flush=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
function element(){return {hidden:false,disabled:false,textContent:'',value:'',checked:false,files:[],selectedOptions:[],children:[],attributes:{},listeners:{},classList:{toggle(){}},append(...values){this.children.push(...values);},replaceChildren(...values){this.children=values;this.textContent='';},setAttribute(key,value){this.attributes[key]=value;},addEventListener(key,fn){this.listeners[key]=fn;},focus(){},select(){}};}
function task(id='task-one',overrides={}){return {id,projectId:'fixture-project',status:'READY_FOR_REVIEW',prompt:'Review '+id,revision:'revision-one',result:{sha256:'a'.repeat(64),changes:[]},...overrides};}
function memory(id='memory-one'){return {id,text:'Fixture '+id,status:'proposed',category:'fact',sources:[]};}
function qualifyRecovery(h,ids=['a'.repeat(32)]){h.fixture.state.models=[{host:'local',id:'fixture-model',digest:'d'.repeat(64),eligible:true,stale:false,capabilities:['completion']}];h.fixture.history.profiles=[{host:'local',digest:'d'.repeat(64),passed:true}];h.fixture.history.memoryRecovery={uncertainRequestIds:ids,total:ids.length};}
function harness(){
 const elements={},requests=[];for(const match of html.matchAll(/id="([^"]+)"/g))elements[match[1]]=element();
 const fixture={state:{available:true,hosts:[],models:[],projects:[],settings:{},conversations:[],tasks:Array.from({length:50},(_,i)=>({id:'task-'+i,prompt:'Task '+i,status:'COMPLETED',revision:'revision-one'})),tasksPage:{nextCursor:'task-cursor&one',total:120}},
  history:{available:true,role:'replica',generation:{generationId:'g'.repeat(64)},offline:false,memories:Array.from({length:100},(_,i)=>memory('memory-'+i)),memoryPage:{nextCursor:{afterId:'memory-99',generationId:'g'.repeat(64)},total:230},exclusionSync:{localPolicyRevision:1},jobs:[],projects:[],gateway:{available:false}},
  detail:task(),taskPage:{tasks:[{id:'older-task',prompt:'Older task',status:'COMPLETED',revision:'older-one'}],nextCursor:'task-cursor-two',total:120},memoryPage:{memories:[memory('older-memory')],nextCursor:{afterId:'older-memory'},total:230}};
 const reply=(route,body)=>{if(route==='/api/ai/state')return fixture.state;if(route==='/api/ai/history/state')return fixture.history;if(route==='/api/ai/bridge/status')return {connectionAvailable:true,pendingUnloads:[]};if(route.startsWith('/api/ai/task?'))return fixture.detail;if(route.startsWith('/api/ai/tasks?'))return fixture.taskPage;if(route==='/api/ai/history/memories')return fixture.memoryPage;if(route==='/api/ai/tasks/reconcile'||route==='/api/ai/tasks/cancel')return {accepted:true};throw new Error('Unexpected synthetic request '+route);};
 let responder=reply;
 const ctx={document:{createElement:()=>element(),documentElement:{dataset:{}},body:{classList:{toggle(){}}},hidden:false,addEventListener(){}},$:id=>elements[id]??=element(),stopped:false,confirm:()=>true,masterSetView(){},jobsHide(){},setTimeout,clearTimeout,setInterval(){},addEventListener(){},ArrayBuffer,DataView,Float32Array,Uint8Array,console,navigator:{},api:async(route,body)=>{requests.push({route,body});return responder(route,body);}};
 vm.createContext(ctx);vm.runInContext(block('AI MODELS')+'\n'+block('AI INTERFACE')+'\n'+block('AI BINDINGS'),ctx);
 return {ctx,elements,requests,fixture,reply,respond:fn=>{responder=fn;},set(name,value){ctx.fixtureValue=value;vm.runInContext(name+'=fixtureValue',ctx);},get:expression=>vm.runInContext(expression,ctx),count:prefix=>requests.filter(row=>row.route.startsWith(prefix)).length};
}

(async()=>{
 for(const id of ['ai-tasks-count','ai-tasks-first','ai-tasks-next','ai-task-reconcile','ai-memories-count','ai-memories-first','ai-memories-next','ai-history-policy-status'])check(html.includes('id="'+id+'"'),'HTML provides '+id+' control/status');
 {
  const h=harness();await h.ctx.aiPoll();
  check(h.elements['ai-task-list'].children.length===50&&h.elements['ai-tasks-count'].textContent.includes('50 shown of 120'),'Compact initial task state renders its bounded first 50 rows and total');
  check(h.elements['ai-tasks-first'].disabled&&!h.elements['ai-tasks-next'].disabled&&h.count('/api/ai/tasks?')===0,'Initial task view offers older access without automatically fetching more pages');
  await h.elements['ai-tasks-next'].listeners.click();const request=h.requests.find(row=>row.route.startsWith('/api/ai/tasks?'));
  check(request.route==='/api/ai/tasks?after=task-cursor%26one&limit=50'&&request.body===undefined,'Older tasks use an encoded cursor and bounded read-only GET with limit 50');
  check(h.elements['ai-task-list'].children.length===1&&h.elements['ai-tasks-count'].textContent.includes('Older tasks')&&!h.elements['ai-tasks-first'].disabled,'Older tasks replace the visible page and enable return to latest');
  const before=h.requests.length;h.elements['ai-tasks-first'].listeners.click();check(h.get('aiTaskPage')===null&&h.elements['ai-task-list'].children.length===50&&h.requests.length===before,'Latest tasks resets locally to compact state without a task action');
 }
 {
  const h=harness();h.set('aiSnapshot',h.fixture.state);const old=deferred();h.respond(route=>route.startsWith('/api/ai/tasks?')?old.promise:h.reply(route));
  const pending=h.ctx.aiLoadTaskPage();await h.ctx.aiLoadTaskPage();check(h.count('/api/ai/tasks?')===1&&h.elements['ai-tasks-next'].disabled,'Task paging suppresses duplicate requests and disables paging while busy');
  h.ctx.aiLatestTasks();old.resolve(h.fixture.taskPage);await pending;check(h.get('aiTaskPage')===null&&!h.get('aiTaskPageBusy'),'A late older-task response cannot undo a Latest tasks reset');
  const failed=deferred();h.respond(()=>failed.promise);const stale=h.ctx.aiLoadTaskPage();h.ctx.aiLatestTasks();failed.reject(new Error('obsolete task page'));await stale;check(h.get('aiTaskPage')===null&&!h.get('aiTaskPageBusy'),'An obsolete task-page rejection is fenced after reset');
  h.respond(()=>Promise.reject(new Error('current task page failed')));await assert.rejects(h.ctx.aiLoadTaskPage(),/current task page failed/);check(!h.get('aiTaskPageBusy')&&!h.elements['ai-tasks-next'].disabled,'A current task-page error clears busy state and retains a retryable cursor');
 }
 {
  const h=harness();h.fixture.detail=task('task-0');h.set('aiTask','task-0');await h.ctx.aiPoll();await h.ctx.aiPoll();
  check(h.count('/api/ai/task?')===1&&h.elements['ai-task-detail'].hidden===false,'Selected full task is fetched once and reused across unchanged summary revisions');
  h.fixture.state={...h.fixture.state,tasks:h.fixture.state.tasks.map((row,index)=>index?row:{...row,revision:'revision-two'})};h.fixture.detail={...h.fixture.detail,summary:'Updated review'};await h.ctx.aiPoll();
  check(h.count('/api/ai/task?')===2&&h.elements['ai-task-summary'].textContent.includes('Updated review'),'A changed task summary revision refreshes selected full details');
  h.fixture.state={...h.fixture.state,tasks:[]};await h.ctx.aiPoll();await h.ctx.aiPoll();check(h.count('/api/ai/task?')===4,'Missing selected summary conservatively refreshes details rather than assuming freshness');
  h.fixture.state={...h.fixture.state,tasks:[{id:'task-0',updatedAt:'updated-fallback'}]};await h.ctx.aiPoll();await h.ctx.aiPoll();check(h.count('/api/ai/task?')===5,'Older summary contracts can use updatedAt to avoid unchanged detail reads');
 }
 {
  const h=harness(),old=deferred();h.set('aiTask','task-old');h.respond(route=>route.includes('task-old')?old.promise:h.reply(route));const pending=h.ctx.aiReadTask();h.set('aiTask','task-new');h.fixture.detail=task('task-new');await h.ctx.aiReadTask();old.resolve(task('task-old'));await pending;
  check(h.get('aiTaskValue.id')==='task-new'&&h.elements['ai-task-title'].textContent.includes('task-new'),'A late task-detail response cannot replace a newer selected identity');
 }
 {
  const h=harness();h.fixture.detail=task('recover-me',{status:'RECOVERY_REQUIRED',result:null});h.set('aiTask','recover-me');await h.ctx.aiReadTask();
  check(!h.elements['ai-task-reconcile'].hidden&&!h.elements['ai-task-reconcile'].disabled&&!h.elements['ai-task-cancel'].disabled&&h.elements['ai-task-apply'].disabled,'Recovery state visibly enables reconcile/cancel while result application stays disabled');
  const before=h.requests.length;await h.elements['ai-task-reconcile'].listeners.click();const actions=h.requests.slice(before).filter(row=>row.body!==undefined);
  check(actions.length===1&&actions[0].route==='/api/ai/tasks/reconcile'&&JSON.stringify(actions[0].body)==='{"id":"recover-me"}','Reconcile sends exactly the displayed task identity to the safe reconciliation route');
  check(!h.requests.some(row=>/tasks\/(start|apply)|\/chat|\/calibrate|\/model/.test(row.route)),'Recovery rendering and reconcile never start inference, workers, application or model operations');
  await h.elements['ai-task-cancel'].listeners.click();check(h.requests.some(row=>row.route==='/api/ai/tasks/cancel'&&row.body.id==='recover-me'),'Recovery cancellation remains explicitly bound to the selected task');
  h.fixture.detail=task('finished',{status:'COMPLETED'});h.set('aiTask','finished');await h.ctx.aiReadTask();check(h.elements['ai-task-reconcile'].hidden&&h.elements['ai-task-cancel'].disabled,'A completed non-recovery task hides reconcile and disables cancellation');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();check(h.elements['ai-memory-list'].children.length===100&&h.elements['ai-memories-count'].textContent==='100 shown of 230','Compact history state renders its bounded first 100 memories and total');
  check(!h.elements['ai-memories-next'].disabled&&h.elements['ai-memories-first'].disabled&&h.count('/api/ai/history/memories')===0,'Initial memory view exposes continuation without fetching all proposals');
  await h.elements['ai-memories-next'].listeners.click();const request=h.requests.find(row=>row.route==='/api/ai/history/memories');check(JSON.stringify(request.body)===JSON.stringify({after:h.fixture.history.memoryPage.nextCursor,limit:100}),'Memory continuation uses the exact structured cursor and bounded limit 100');
  check(h.elements['ai-memory-list'].children.length===1&&!h.elements['ai-memories-first'].disabled,'Older memory page replaces the first page and enables return');
  h.fixture.memoryPage={memories:[],nextCursor:{afterId:'empty-page-cursor'},total:230};await h.ctx.aiHistoryLoadMemoryPage();check(!h.elements['ai-memories-next'].disabled&&h.elements['ai-memory-list'].children[0].textContent.includes('Continue'),'An empty filtered memory page preserves its next cursor and visible continuation');
  h.fixture.memoryPage={memories:[memory('after-empty')],nextCursor:null,total:230};await h.ctx.aiHistoryLoadMemoryPage();check(h.requests.at(-1).body.after.afterId==='empty-page-cursor'&&h.elements['ai-memories-next'].disabled,'Continuation advances through an empty page and stops only when the cursor is exhausted');
  h.ctx.aiLatestMemories();check(h.get('aiMemoryPage')===null&&h.get('aiMemoryAfter')===null&&h.elements['ai-memory-list'].children.length===100,'First memory page clears older-page identity and restores compact state');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();const old=deferred();h.respond((route,body)=>route==='/api/ai/history/memories'?old.promise:h.reply(route,body));const pending=h.ctx.aiHistoryLoadMemoryPage();await h.ctx.aiHistoryLoadMemoryPage();check(h.count('/api/ai/history/memories')===1&&h.elements['ai-memories-next'].disabled,'Memory paging suppresses duplicates and disables controls while busy');
  h.ctx.aiLatestMemories();old.resolve(h.fixture.memoryPage);await pending;check(h.get('aiMemoryPage')===null&&!h.get('aiMemoryPageBusy'),'A late memory-page success cannot undo First page reset');
  h.respond(()=>Promise.reject(new Error('current memory page failed')));await assert.rejects(h.ctx.aiHistoryLoadMemoryPage(),/current memory page failed/);check(!h.get('aiMemoryPageBusy')&&!h.elements['ai-memories-next'].disabled,'Current memory-page errors clear busy state and retain continuation');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();await h.ctx.aiHistoryLoadMemoryPage();const cursor=h.get('aiMemoryAfter');h.set('aiView','memories');h.fixture.memoryPage={memories:[memory('refreshed-older')],nextCursor:null,total:230};await h.ctx.aiHistoryPoll();
  check(JSON.stringify(h.requests.filter(row=>row.route==='/api/ai/history/memories').at(-1).body.after)===JSON.stringify(cursor)&&h.get('aiMemoryPage.memories[0].id')==='refreshed-older','Polling the visible older memory page refreshes its current cursor rather than advancing');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();await h.ctx.aiHistoryLoadMemoryPage();h.set('aiView','memories');const old=deferred();let pages=0;h.respond((route,body)=>route==='/api/ai/history/memories'?(++pages===1?old.promise:{memories:[memory('newer-page')],nextCursor:null,total:230}):h.reply(route,body));
  const pending=h.ctx.aiHistoryPoll();await flush();h.ctx.aiLatestMemories();await h.ctx.aiHistoryLoadMemoryPage({afterId:'new-cursor'});old.reject(new Error('obsolete visible page'));await pending;
  check(h.get('aiMemoryPage.memories[0].id')==='newer-page'&&!h.elements['ai-error'].textContent,'A late rejected page refresh cannot reset a newer memory page or surface an obsolete error');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();await h.ctx.aiHistoryLoadMemoryPage();h.fixture.history={...h.fixture.history,exclusionSync:{localPolicyRevision:2,pendingCount:2}};await h.ctx.aiHistoryPoll();
  check(h.get('aiMemoryPage')===null&&h.elements['ai-memory-list'].children.length===100,'A changed local privacy policy resets older memory pages before rendering');
  check(h.elements['ai-history-policy-status'].textContent.includes('2 privacy changes')&&h.elements['ai-history-policy-status'].textContent.includes('Local restrictions remain active'),'Pending exclusion intents visibly retain local restrictions');
  h.fixture.history={...h.fixture.history,exclusionSync:{localPolicyRevision:2,pendingCount:2,unresolvedCount:1}};await h.ctx.aiHistoryPoll();check(h.elements['ai-history-policy-status'].textContent.includes('1 unresolved; 2 pending'),'Unresolved privacy changes disclose review needs and pending count');
  h.fixture.history={...h.fixture.history,exclusionSync:{localPolicyRevision:2,capabilityRequired:true,pendingCount:2}};await h.ctx.aiHistoryPoll();check(h.elements['ai-history-policy-status'].textContent.includes('Update the paired history service')&&h.elements['ai-history-policy-status'].textContent.includes('2 pending'),'Missing paired privacy capability visibly requires a service update while retaining pending count');
  h.fixture.history={...h.fixture.history,exclusionSync:{localPolicyRevision:2,pendingCount:0,unresolvedCount:0}};await h.ctx.aiHistoryPoll();check(h.elements['ai-history-policy-status'].textContent==='No pending privacy changes.','An empty observed exclusion queue describes its scope without claiming global synchronization');
  h.fixture.history={...h.fixture.history,exclusionSync:undefined};await h.ctx.aiHistoryPoll();check(h.elements['ai-history-policy-status'].textContent==='Privacy confirmation unavailable.','Missing exclusion confirmation cannot become a synchronized claim');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();const old=deferred();h.respond((route,body)=>route==='/api/ai/history/memories'?old.promise:h.reply(route,body));const pending=h.ctx.aiHistoryLoadMemoryPage();
  h.fixture.history={...h.fixture.history,exclusionSync:{localPolicyRevision:2}};await h.ctx.aiHistoryPoll();old.resolve(h.fixture.memoryPage);await pending;
  check(h.get('aiMemoryPage')===null&&!h.get('aiMemoryPageBusy'),'A changed privacy scope fences an in-flight old memory-page response');
 }
 {
  const h=harness(),valid=Array.from({length:24},(_,index)=>index.toString(16).padStart(32,'0'));qualifyRecovery(h,[...valid,valid[0],null,24,'invalid','A'.repeat(32),'b'.repeat(31)]);h.fixture.history.memoryRecovery.total=50;await h.ctx.aiPoll();
  const panel=h.elements['ai-memory-recovery'],rows=panel.children.slice(2),ids=rows.map(row=>row.children[0].textContent);
  check(html.includes('id="ai-memory-recovery"')&&!panel.hidden&&rows.length===20&&new Set(ids).size===20&&ids.every(id=>/^[a-f0-9]{32}$/.test(id)),'Interrupted-memory recovery panel validates, deduplicates and bounds exact request IDs to 20 rows');
  check(panel.children[1].textContent.includes('20 shown of 50')&&panel.children[1].textContent.includes('may have run')&&!rows[0].children[1].disabled,'Recovery discloses uncertainty and total while enabling explicit retry only with current qualification');
  check(h.requests.every(row=>row.body===undefined),'Polling and rendering uncertain memory requests never automatically retry or dispatch a write');
 }
 {
  const h=harness();qualifyRecovery(h);await h.ctx.aiPoll();const disabled=()=>h.elements['ai-memory-recovery'].children.slice(2).every(row=>row.children[1].disabled);
  h.set('aiSnapshot',{...h.fixture.state,models:[]});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry is disabled without a current installed qualified model');
  h.set('aiSnapshot',{...h.fixture.state,models:h.fixture.state.models.map(model=>({...model,stale:true}))});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry is disabled when the matching model observation is stale');
  h.set('aiSnapshot',h.fixture.state);h.set('aiHistoryState',{...h.fixture.history,generation:null});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry is disabled without an active archive generation');
  h.set('aiHistoryState',{...h.fixture.history,offline:true});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry is disabled while the history primary is offline');
  h.set('aiHistoryState',{...h.fixture.history,jobs:[{id:'active-job',status:'running'}]});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry is disabled while a history job is running');
  h.set('aiHistoryState',{...h.fixture.history,profiles:[{host:'local',digest:'e'.repeat(64),passed:true}]});h.ctx.aiHistoryRenderProfiles();check(disabled(),'Memory retry cannot inherit a passing profile from a different model digest');
 }
 {
  const h=harness(),id='a'.repeat(32);qualifyRecovery(h,[id]);await h.ctx.aiPoll();h.elements['ai-memory-generate-project'].value=' Fixture Project ';h.ctx.confirm=()=>false;const before=h.requests.length;
  await h.elements['ai-memory-recovery'].children[2].children[1].listeners.click();check(h.requests.length===before&&!h.get('aiMemoryRecoveryBusy'),'Declining the uncertainty confirmation never dispatches a retry or holds the pending gate');
  h.ctx.confirm=()=>true;h.respond((route,body)=>route==='/api/ai/history/memory-generate'?{accepted:true}:h.reply(route,body));await h.elements['ai-memory-recovery'].children[2].children[1].listeners.click();
  const actions=h.requests.slice(before).filter(row=>row.body!==undefined);check(actions.length===1&&actions[0].route==='/api/ai/history/memory-generate'&&JSON.stringify(actions[0].body)===JSON.stringify({project:'Fixture Project',retryUncertainRequestIds:[id]}),'Accepted explicit retry sends only the exact chosen request ID and trimmed project scope');
  check(!h.requests.some(row=>/memory-review|tasks\/start|tasks\/apply|\/chat|\/model/.test(row.route)),'Explicit uncertain-memory retry does not approve proposals or start unrelated workers, chat or model actions');
  const prior=h.requests.length;await assert.rejects(h.ctx.aiHistoryRetryMemory('b'.repeat(32)),/no longer listed/);await assert.rejects(h.ctx.aiHistoryRetryMemory('<invalid>'),/no longer listed/);check(h.requests.length===prior,'Stale or invalid uncertain-memory identity is rejected before any API request');
 }
 {
  const h=harness(),id='a'.repeat(32),pending=deferred();qualifyRecovery(h,[id]);await h.ctx.aiPoll();h.respond((route,body)=>route==='/api/ai/history/memory-generate'?pending.promise:h.reply(route,body));
  const active=h.ctx.aiHistoryRetryMemory(id);await assert.rejects(h.ctx.aiHistoryRetryMemory(id),/Wait for the current work/);check(h.count('/api/ai/history/memory-generate')===1&&h.get('aiMemoryRecoveryBusy')&&h.elements['ai-memory-recovery'].children[2].children[1].disabled,'Pending uncertain-memory retry disables controls and suppresses duplicate dispatch');
  pending.reject(new Error('fixture retry failure'));await assert.rejects(active,/fixture retry failure/);check(!h.get('aiMemoryRecoveryBusy')&&!h.elements['ai-memory-recovery'].children[2].children[1].disabled,'A failed uncertain-memory retry releases its gate and restores explicit retry controls');
 }
 {
  const h=harness(),id='a'.repeat(32),old=deferred(),fresh=deferred();qualifyRecovery(h,[id]);await h.ctx.aiPoll();const oldHistory=h.fixture.history;let states=0;
  h.respond((route,body)=>route==='/api/ai/history/state'?(++states===1?old.promise:fresh.promise):route==='/api/ai/history/memory-generate'?{accepted:true}:h.reply(route,body));
  const background=h.ctx.aiHistoryPoll({background:true}),retry=h.ctx.aiHistoryRetryMemory(id);await flush();
  check(states===1&&h.get('aiMemoryRecoveryBusy')&&h.elements['ai-memory-recovery'].children[2].children[1].disabled,'Accepted retry retains its pending gate while an older history-state read is outstanding');
  old.resolve(oldHistory);await flush();check(states===2&&h.get('aiMemoryRecoveryBusy'),'Post-action history refresh queues one fresh state read after the older read settles');
  fresh.resolve({...oldHistory,memoryRecovery:{uncertainRequestIds:[],total:0},jobs:[{id:'retry-job',status:'running'}]});await Promise.all([background,retry]);
  check(!h.get('aiMemoryRecoveryBusy')&&h.elements['ai-memory-recovery'].hidden&&h.elements['ai-memory-generate'].disabled,'Retry gate releases only after fresh state removes the uncertain request and reports the active job');
 }
 {
  const h=harness(),pending=deferred();let reads=0;h.respond(route=>{assert.equal(route,'/api/ai/history/state');reads++;return pending.promise;});
  const first=h.ctx.aiHistoryPoll({background:true}),second=h.ctx.aiHistoryPoll({background:true}),third=h.ctx.aiHistoryPoll({background:true});check(reads===1&&h.get('aiHistoryPollBusy'),'Concurrent background history polls share one current request without overlap');
  pending.resolve(h.fixture.history);await Promise.all([first,second,third]);check(reads===1&&!h.get('aiHistoryPollBusy')&&h.get('aiHistoryPollPending')===null,'Background history coalescing does not queue redundant reads and releases its shared gate');
 }
 {
  const h=harness(),id='a'.repeat(32),old=deferred(),fresh=deferred();qualifyRecovery(h,[id]);await h.ctx.aiPoll();let reads=0;
  h.respond((route,body)=>route==='/api/ai/history/state'?(++reads===1?old.promise:fresh.promise):route==='/api/ai/history/memory-generate'?{accepted:true}:h.reply(route,body));
  const background=h.ctx.aiHistoryPoll({background:true}),retry=h.ctx.aiHistoryRetryMemory(id);await flush();old.reject(new Error('older state unavailable'));await flush();
  check(reads===2&&h.get('aiMemoryRecoveryBusy'),'A failed older history read still runs the queued post-action refresh with the retry gate held');
  fresh.reject(new Error('fresh state unavailable'));await Promise.all([background,retry]);check(!h.get('aiMemoryRecoveryBusy')&&h.elements['ai-memory-recovery'].children[2].children[1].disabled&&h.get('aiHistoryState.available')===false,'Failure to confirm post-action history state keeps retry controls disabled after releasing the request gate');
 }
 {
  const h=harness();await h.ctx.aiHistoryPoll();const previous=h.get('aiHistoryState'),banner=h.elements['ai-history-banner'].textContent,pending=deferred();let reads=0;h.respond(route=>{assert.equal(route,'/api/ai/history/state');reads++;return pending.promise;});
  const background=h.ctx.aiHistoryPoll({background:true}),explicit=h.ctx.aiHistoryPoll();h.ctx.stopped=true;pending.resolve({...h.fixture.history,generation:{generationId:'different'}});await Promise.all([background,explicit]);
  check(h.get('aiHistoryState')===previous&&h.elements['ai-history-banner'].textContent===banner&&reads===1,'A history-state response after shutdown cannot replace its state/render or run a queued explicit reread');
 }
 console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Current AI/history blocks and bindings with synthetic DOM and bounded API fixtures; no external operations.'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
