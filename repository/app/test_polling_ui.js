'use strict';
// Current production functions with deterministic synthetic APIs and timers.
// No browser, network, native app or production project/cache access.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
function section(start,end){const first=source.indexOf(start),last=source.indexOf(end,first);assert.ok(first>=0&&last>first,'Current polling function boundaries are present');return source.slice(first,last);}
const functions=section('function select(id,record=false)','function updateEntry()')+'\n'+section('async function loadCatalog(','async function action(')+'\n'+section('async function pollOnce()','async function sendResponse(');
const visibility=source.split('\n').find(line=>line.startsWith("document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(pollTimer)"));
assert.ok(visibility,'Base poll visibility handler is present');
const checks=[];
function check(value,label){assert.ok(value,label);checks.push(label);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const entry={path:'Projects/Original.MVP',kind:'project'};
const localEntry={path:'Projects/My Save.MVP',kind:'project',origin:'LOCAL_SAVE_AS'};
function project(overrides={}){return {id:'magix-one',name:'Fixture family',type:'magix',cache:{status:'READY'},entryPoints:[{...entry}],...overrides};}
function catalog(overrides={}){return {projects:[project()],hub:{},preferences:{favorites:[],recent:[]},demo:true,indexDate:'2026-11-02T00:00:00Z',auditAvailable:false,...overrides};}
function harness(){
 let now=1000,respond=async route=>route==='/api/state'?{busy:false,revision:1}:route==='/api/catalog'?catalog():route.startsWith('/api/entries?')?{entries:[{...entry},{...localEntry}],warnings:[]}:{};
 const requests=[],errors=[],timers=new Map(),listeners={},elements={},renders={list:0,detail:0,state:0,hub:0},selectedProject=project();
 selectedProject.catalogEntryKey=JSON.stringify(selectedProject.entryPoints);selectedProject.entryPoints.push({...localEntry});selectedProject.loadingEntries=false;
 let timerSequence=0;
 const context={
  projects:[selectedProject],selected:selectedProject,state:{busy:false,revision:0},stopped:false,mutation:false,
  pollPending:null,pollAgain:false,pollTimer:null,catalogPending:null,catalogAgain:false,pollSequence:0,appliedPoll:0,
  entrySequence:0,auditSequence:0,auditLoaded:null,mw5Readiness:null,mw5Preview:null,mw5Sequence:0,
  lastCatalogRefresh:1000,lastCompleted:null,entryRefreshTimes:new Map([['magix-one',1000]]),chosenEntries:new Map([['magix-one',localEntry.path]]),
  hub:{},preferences:{favorites:[],recent:[]},initialProject:null,buddyReportRefreshed:1000,
  Date:{now:()=>now},document:{hidden:false,addEventListener:(event,callback)=>{listeners[event]=callback;}},
  $:id=>id==='buddy-report-panel'?null:elements[id]??=(id==='audit-panel'?{hidden:true}:{hidden:false,textContent:'',value:'',lastChild:{textContent:''}}),
  isMagix:value=>value?.type==='magix',projectType:value=>value?.type,
  renderState(){renders.state++;},renderHub(){renders.hub++;},renderList(){renders.list++;},renderDetail(){renders.detail++;},
  showError:error=>errors.push(String(error.message||error)),loadMissing:async()=>{},missingError:error=>errors.push(String(error)),
  loadAudit:async()=>{},loadStats:async()=>{},loadMw5:async()=>{},recordRecent:async()=>{},buddyLoadReport:async()=>{},
  setTimeout:(callback,delay)=>{const id=++timerSequence;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id),
  api:async(route,body)=>{requests.push({route,body});return respond(route,body);},
 };
 vm.createContext(context);vm.runInContext(functions+'\n'+visibility,context);
 return {context,requests,errors,timers,listeners,renders,setNow:value=>{now=value;},respond:handler=>{respond=handler;},
  count:prefix=>requests.filter(row=>row.route.startsWith(prefix)).length,
  async runTimer(){const first=timers.entries().next().value;assert.ok(first,'A scheduled callback exists');timers.delete(first[0]);await first[1].callback();},
 };
}

(async()=>{
 {
  const h=harness(),pending=deferred();h.respond(route=>route==='/api/state'?pending.promise:Promise.reject(new Error('Unexpected '+route)));
  const first=h.context.poll({background:true}),second=h.context.poll({background:true}),third=h.context.poll({background:true});
  check(h.count('/api/state')===1,'Delayed background polls coalesce into one current state request');
  pending.resolve({busy:false,revision:2});await Promise.all([first,second,third]);
  check(h.context.state.revision===2&&h.context.pollPending===null&&h.count('/api/state')===1,'Coalesced state is applied once and releases its pending gate');
 }
 {
  const h=harness(),old=deferred(),fresh=deferred();let reads=0;
  h.respond(route=>{assert.equal(route,'/api/state');return ++reads===1?old.promise:fresh.promise;});
  const before=h.context.poll({background:true}),mutationRefresh=h.context.poll();
  check(reads===1,'Explicit mutation refresh waits for the older state read without overlap');
  old.resolve({busy:false,revision:2});await flush();
  check(reads===2,'An explicit refresh queued during a read performs one newer read afterward');
  let completed=false;mutationRefresh.then(()=>{completed=true;});await flush();check(!completed,'Explicit refresh completion waits for the newer state response');
  fresh.resolve({busy:false,revision:3});await Promise.all([before,mutationRefresh]);
  check(h.context.state.revision===3&&h.context.pollPending===null,'The queued mutation refresh publishes the fresh revision and clears its gate');
 }
 {
  const h=harness(),pending=deferred();let reads=0;h.respond(route=>{assert.equal(route,'/api/state');return ++reads===1?pending.promise:{busy:false,revision:4};});
  const failed=h.context.poll({background:true});pending.reject(new Error('fixture connection failure'));await assert.rejects(failed,/fixture connection failure/);
  check(h.context.pollPending===null,'A rejected read releases the state polling gate');
  await h.context.poll({background:true});check(reads===2&&h.context.state.revision===4,'A subsequent poll recovers after rejection without a stuck pending promise');
 }
 {
  const h=harness(),old=deferred();let reads=0;h.respond(route=>{assert.equal(route,'/api/state');return ++reads===1?old.promise:{busy:false,revision:6};});
  const background=h.context.poll({background:true}),explicit=h.context.poll();old.reject(new Error('stale read rejected'));
  await Promise.all([background,explicit]);check(reads===2&&h.context.state.revision===6,'An explicit refresh queued behind a failed background read still obtains fresh state');
 }
 {
  const h=harness(),pending=deferred();h.respond(route=>{assert.equal(route,'/api/state');return pending.promise;});
  h.context.schedulePoll();check(h.timers.size===1&&[...h.timers.values()][0].delay===1200,'Visible background polling schedules one 1200 ms timeout');
  const running=h.runTimer();check(h.count('/api/state')===1&&h.timers.size===0,'A slow scheduled request has no overlapping timer queued');
  pending.resolve({busy:false,revision:1});await running;check(h.timers.size===1,'The next timeout starts only after the current scheduled poll settles');
  h.context.document.hidden=true;h.listeners.visibilitychange();check(h.timers.size===0&&h.context.pollTimer===null,'Hiding the document cancels its pending background timeout');
  const before=h.count('/api/state');await h.context.poll({background:true});check(h.count('/api/state')===before,'Hidden background calls do not request state');
  h.respond(route=>{assert.equal(route,'/api/state');return {busy:false,revision:5};});h.context.document.hidden=false;h.listeners.visibilitychange();await flush();
  check(h.count('/api/state')===before+1&&h.context.state.revision===5&&h.timers.size===1,'Visibility resume immediately refreshes state and restores one scheduler');
  h.context.stopped=true;h.context.schedulePoll();check(h.timers.size===0,'A stopped interface cannot schedule more background work');
 }
 {
  const h=harness();h.respond(()=>Promise.reject(new Error('fixture scheduler failure')));h.context.schedulePoll();await h.runTimer();
  check(h.errors.length===1&&h.context.pollPending===null&&h.timers.size===1,'A rejected scheduled read reports connection loss and schedules one recovery attempt');
 }
 {
  const h=harness(),before=h.context.selected.entryPoints,choice=h.context.chosenEntries.get('magix-one');await h.context.loadCatalog();
  check(h.count('/api/catalog')===1&&h.count('/api/entries')===0,'An unchanged catalog performs no local entry scan');
  check(h.context.selected.entryPoints===before&&h.context.selected.entryPoints.some(row=>row.origin==='LOCAL_SAVE_AS'),'An unchanged catalog preserves discovered local Save As entries');
  check(h.context.chosenEntries.get('magix-one')===choice&&h.context.selected.id==='magix-one','An unchanged catalog preserves selected project and entry choice');
 }
 {
  const h=harness();h.respond(route=>route==='/api/catalog'?catalog({projects:[project({entryPoints:[...project().entryPoints,{path:'Projects/New.MVP',kind:'project'}]})]}):route.startsWith('/api/entries?')?{entries:[entry,localEntry],warnings:[]}:Promise.reject(new Error(route)));
  await h.context.loadCatalog();await flush();check(h.count('/api/entries')===1,'Changed catalog entry identity triggers a selected-family scan');
 }
 {
  const h=harness();h.respond(route=>route==='/api/catalog'?catalog({projects:[project({cache:{status:'ABSENT'}})]}):route.startsWith('/api/entries?')?{entries:[entry],warnings:[]}:Promise.reject(new Error(route)));
  await h.context.loadCatalog();await flush();check(h.count('/api/entries')===1,'A changed cache status triggers a selected-family scan');
 }
 {
  const h=harness();await h.context.loadCatalog({refreshSelection:true});await flush();check(h.count('/api/entries')===1,'An explicit selected-family refresh scans even when catalog metadata is unchanged');
 }
 {
  const h=harness(),first=deferred();let catalogs=0;h.respond(route=>route==='/api/catalog'?(++catalogs===1?first.promise:catalog()):route.startsWith('/api/entries?')?{entries:[entry,localEntry],warnings:[]}:Promise.reject(new Error(route)));
  const background=h.context.loadCatalog(),explicit=h.context.loadCatalog({refreshSelection:true});check(catalogs===1,'Concurrent catalog refresh requests do not overlap');first.resolve(catalog());await Promise.all([background,explicit]);await flush();
  check(catalogs===2&&h.count('/api/entries')===1,'Explicit refresh arriving during catalog fetch queues one refreshed selection scan');
 }
 {
  const h=harness(),old=deferred();let catalogs=0;h.respond(route=>route==='/api/catalog'?(++catalogs===1?old.promise:catalog()):route.startsWith('/api/entries?')?{entries:[entry,localEntry],warnings:[]}:Promise.reject(new Error(route)));
  const background=h.context.loadCatalog(),explicit=h.context.loadCatalog({refreshSelection:true});old.reject(new Error('stale catalog failed'));await Promise.all([background,explicit]);await flush();
  check(catalogs===2&&h.count('/api/entries')===1&&h.context.catalogPending===null,'An explicit catalog refresh queued behind rejection still fetches and scans the current selection');
 }
 {
  const h=harness();h.respond(route=>route==='/api/state'?{busy:false,revision:2,operation:{id:'completed-one',status:'COMPLETED'}}:route==='/api/catalog'?catalog():route.startsWith('/api/entries?')?{entries:[entry,localEntry],warnings:[]}:Promise.reject(new Error(route)));
  await h.context.poll();await flush();check(h.count('/api/entries')===1&&h.context.lastCompleted==='completed-one','An observed completed operation refreshes local entries before marking completion consumed');
  await h.context.poll();check(h.count('/api/entries')===1,'Repeated observation of the same completed operation does not rescan');
 }
 {
  const h=harness();h.setNow(31000);h.context.lastCatalogRefresh=31000;await h.context.poll();check(h.count('/api/entries')===0,'Save As discovery is not repeated before its 30-second refresh interval expires');
  h.setNow(31001);await h.context.poll();check(h.count('/api/entries')===1,'Visible selected MAGIX entries refresh after 30 seconds to discover new Save As files');
  await h.context.poll();check(h.count('/api/entries')===1,'A fresh Save As discovery suppresses repeated scans on following state polls');
  h.setNow(62002);h.context.lastCatalogRefresh=62002;h.context.document.hidden=true;await h.context.poll();check(h.count('/api/entries')===1,'A hidden document does not scan local Save As entries during an explicit state read');
 }
 {
  const h=harness(),old=deferred();h.setNow(32000);h.context.lastCatalogRefresh=32000;h.respond(route=>route==='/api/state'?{busy:false,revision:2}:route.startsWith('/api/entries?')?old.promise:Promise.reject(new Error(route)));
  const pending=h.context.poll();await flush();check(h.count('/api/entries')===1,'Periodic discovery can remain pending independently of a later selection change');
  h.context.selected=project({id:'different-family',loadingEntries:true});old.reject(new Error('old family read failed'));await assert.rejects(pending,/old family read failed/);
  check(h.context.selected.loadingEntries===true,'A delayed discovery failure cannot clear the loading flag of a newer selected family');
 }
 {
  const h=harness(),old=deferred(),initial=h.context.state;h.respond(route=>{assert.equal(route,'/api/state');return old.promise;});
  const background=h.context.poll({background:true}),explicit=h.context.poll();h.context.stopped=true;h.context.state.closing=true;old.resolve({busy:false,revision:99,operation:{id:'late-completion',status:'COMPLETED'}});await Promise.all([background,explicit]);
  check(h.context.state===initial&&h.context.state.closing===true&&h.renders.state===0,'A state response arriving after guarded shutdown cannot replace or rerender stopped UI state');
  check(h.count('/api/state')===1&&h.count('/api/catalog')===0&&h.context.pollPending===null,'Shutdown discards late state completion without consuming queued refreshes or starting catalog reads');
 }
 {
  const h=harness(),old=deferred(),initialProject=h.context.selected,initialProjects=h.context.projects;h.respond(route=>{assert.equal(route,'/api/catalog');return old.promise;});
  const background=h.context.loadCatalog(),explicit=h.context.loadCatalog({refreshSelection:true});h.context.stopped=true;old.resolve(catalog({projects:[project({id:'late-family',cache:{status:'ABSENT'}})]}));await Promise.all([background,explicit]);await flush();
  check(h.context.selected===initialProject&&h.context.projects===initialProjects&&h.renders.hub===0&&h.renders.list===0&&h.renders.detail===0,'A catalog response arriving after guarded shutdown cannot replace projects, rerender or reselect a family');
  check(h.count('/api/catalog')===1&&h.count('/api/entries')===0&&h.context.catalogPending===null,'Shutdown discards late catalog completion without queued catalog or local-entry reads');
 }
 console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Extracted current production functions; deterministic synthetic API, clock, timers and view stubs; no network or app/project operations.'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
