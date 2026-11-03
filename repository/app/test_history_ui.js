'use strict';
// Synthetic UI fixtures only. No archive, cloud, clipboard or model access.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8'),html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8');
const models=source.split('// BEGIN AI MODELS\n')[1].split('// END AI MODELS')[0];
const ui=source.split('// BEGIN AI INTERFACE\n')[1].split('// END AI INTERFACE')[0];
const bindings=source.split('// BEGIN AI BINDINGS\n')[1].split('// END AI BINDINGS')[0];
const history=source.split('// BEGIN AI HISTORY\n')[1].split('// END AI HISTORY')[0];
const elements={},requests=[],copies=[],checks=[];
function element(){return {hidden:false,disabled:false,textContent:'',value:'',checked:false,files:[],selectedOptions:[],children:[],attributes:{},listeners:{},classList:{toggle(){}},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;this.textContent='';},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,fn){this.listeners[k]=fn;},focus(){},select(){}};}
for(const match of html.matchAll(/id="([^"]+)"/g))elements[match[1]]=element();
const safe={id:'a'.repeat(64),kind:'conversation',title:'<script>source title</script>',text:'Private fixture <img onerror=alert(1)>',excerpt:'<b>literal passage</b>',timestamp:'2026-11-01T00:00:00Z',memory_allowed:true};
const restricted={...safe,id:'b'.repeat(64),title:'Restricted fixture',memory_allowed:false};
let state={available:true,role:'replica',generation:{generationId:'c'.repeat(64),createdAt:'2026-11-02T11:00:00Z'},lastSync:'2026-11-02T12:00:00Z',offline:false,coverage:{records:2},jobs:[],preparedGenerations:[],memories:[],exclusions:[],projects:['Fixture'],gateway:{available:true,endpoint:'http://127.0.0.1:47444/v1',clients:[]}};
let sourceReply=safe,searchReply=[safe,restricted],pendingSource=null,pendingHandoff=null;
let conversation={id:'fixture-chat',title:'Fixture conversation',status:'completed',messages:[{role:'assistant',content:'Visible previous answer',sources:[safe]}]};
const ctx={document:{createElement:()=>element(),documentElement:{dataset:{}},body:{classList:{toggle(){}}},hidden:false,addEventListener(){}},$:id=>elements[id]??=element(),stopped:false,confirm:()=>true,masterSetView(){},jobsHide(){},setTimeout,clearTimeout,setInterval(){},addEventListener(){},ArrayBuffer,DataView,Float32Array,Uint8Array,console,navigator:{clipboard:{async writeText(text){copies.push(text);}}},api:async(route,body)=>{requests.push({route,body});if(route==='/api/ai/history/state')return state;if(route==='/api/ai/state')return {available:true,hosts:[],models:[],projects:[],settings:{},conversations:[],tasks:[]};if(route==='/api/ai/bridge/status')return {connectionAvailable:true};if(route.startsWith('/api/ai/conversation?'))return conversation;if(route==='/api/ai/chat')return conversation;if(route==='/api/ai/history/search')return {results:searchReply};if(route==='/api/ai/history/source')return pendingSource?await pendingSource:sourceReply;if(route==='/api/ai/history/handoff')return pendingHandoff?await pendingHandoff:{text:'Exact preview\n'+body.text+'\n'+body.sourceIds.join(',')};if(route==='/api/ai/history/gateway-create')return {key:'synthetic-client-key'};return {};}};
vm.createContext(ctx);vm.runInContext(models+'\n'+ui+'\n'+bindings,ctx);
function check(ok,label){assert.ok(ok,label);checks.push(label);}
function last(route){return requests.filter(r=>r.route===route).at(-1);}
function renderedText(node){return [node.textContent,...node.children.map(renderedText)].join('\n');}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await ctx.aiHistoryPoll();
 check(requests.every(r=>r.body===undefined),'History polling is read-only and never starts inference or imports');
 check(elements['ai-history-enabled'].checked&&!elements['ai-history-enabled'].disabled,'Verified active archive enables automatic local history by default');
 check(elements['ai-history-banner'].textContent.includes(state.lastSync),'Snapshot last-sync time remains visible');
 state={...state,lastPeerObservedAt:'2026-11-02T12:01:00Z',peerStateStale:true,peerRefreshInFlight:true,jobs:[
  {id:'normalizing',kind:'import',status:'running',phase:'normalizing',records:14300,total:1405,completed:1405,bytes:1034148082,privatePayload:'UNLISTED_PRIVATE_COUNTER_PAYLOAD'},
  {id:'embedding',kind:'import',status:'running',phase:'embedding',records:400,total:1200,completed:1405},
  {id:'intake',kind:'transfer-import',status:'running',phase:'intake',completed:0,total:1405,bytes:0},
  {id:'transfer',kind:'transfer-import',status:'running',phase:'transferring',completed:100,total:1405},
  {id:'sync',kind:'sync',status:'running',phase:'syncing',bytes:1024,total:4096},
  {id:'resume',kind:'import',status:'running',phase:'resuming',records:10,total:700},
  {id:'nested',kind:'import',status:'running',phase:'embedding',progress:{records:2,total:6,privatePayload:'UNLISTED_NESTED_PAYLOAD'}},
  {id:'invalid',kind:'import',status:'failed',phase:'normalizing',records:-1,total:'100',bytes:1.5,errorCode:'FixtureError'}
 ]};const statusRequests=requests.length;await ctx.aiHistoryPoll();
 const jobCards=elements['ai-history-jobs'].children.slice(1),jobText=jobCards.map(renderedText);
 check(jobText[0].includes('Phase: Normalizing')&&jobText[0].includes('14,300 records')&&jobText[0].includes('1,034,148,082 bytes')&&!jobText[0].includes('1,405'),'Flat normalizing counters are visible without mixing leftover file totals into record progress');
 check(jobText[1].includes('400 / 1,200 chunks')&&!jobText[1].includes('1,405')&&jobText[5].includes('10 / 700 chunks'),'Embedding and resumed vectors display chunk progress rather than file or source record units');
 check(jobText[2].includes('0 / 1,405 files')&&jobText[2].includes('0 bytes')&&jobText[3].includes('100 / 1,405 files'),'Intake and transfer display completed files including valid zero progress');
 check(jobText[4].includes('1,024 / 4,096 bytes')&&!jobText[4].includes('files'),'Sync byte totals are labeled as bytes');
 check(jobText[6].includes('2 / 6 chunks')&&!jobText.join('\n').includes('UNLISTED_'),'Only whitelisted counters render for both flat and legacy nested progress');
 check(jobText[7].includes('Phase: Normalizing')&&jobText[7].includes('FixtureError')&&!jobText[7].includes('records')&&!jobText[7].includes('bytes'),'Malformed counters are omitted while failed job phase and error remain visible');
 check(elements['ai-history-jobs'].children[0].textContent.includes('MSI job status: stale')&&elements['ai-history-jobs'].children[0].textContent.includes(state.lastPeerObservedAt)&&elements['ai-history-sync-status'].textContent.includes('Refreshing'),'History and Sync distinguish stale peer metadata and an asynchronous refresh from archive synchronization');
 check(requests.slice(statusRequests).every(row=>row.route==='/api/ai/history/state'&&row.body===undefined),'Rendering peer refresh state adds no blocking refresh or mutation request');
 state={...state,lastPeerObservedAt:'2026-11-02T12:01:04Z',peerStateStale:false,peerRefreshInFlight:false,jobs:[{...state.jobs[0],records:15100}]};await ctx.aiHistoryPoll();
 check(renderedText(elements['ai-history-jobs']).includes('15,100 records')&&elements['ai-history-jobs'].children[0].textContent.includes('MSI job status: observed')&&!elements['ai-history-sync-status'].textContent.includes('Refreshing'),'A new peer observation updates counters and clears stale refresh labels');
 await elements['ai-history-jobs'].children[1].children.at(-1).listeners.click();check(last('/api/ai/history/cancel').body.id==='normalizing','Progress rendering preserves explicit cancellation bound to the displayed job');
 state={...state,jobs:[]};await ctx.aiHistoryPoll();
 state={...state,offline:true};await ctx.aiHistoryPoll();
 check(elements['ai-history-banner'].textContent.includes('Offline')&&!elements['ai-history-search'].disabled&&elements['ai-history-import'].disabled,'Offline replica remains searchable while primary mutations are unavailable');
 check(!elements['ai-gateway-create'].disabled,'Local client keys can be created while MSI is offline');
 state={...state,offline:false};await ctx.aiHistoryPoll();
 elements['ai-history-query'].value='fixture';await ctx.aiHistorySearch();
 check(!('dateFrom' in last('/api/ai/history/search').body)&&!('dateTo' in last('/api/ai/history/search').body),'Unselected date filters are omitted from history requests');
 elements['ai-history-date-from'].value='2026-10-01';elements['ai-history-date-to'].value='2026-11-02';await ctx.aiHistorySearch();check(last('/api/ai/history/search').body.dateFrom==='2026-10-01'&&last('/api/ai/history/search').body.dateTo==='2026-11-02','History search sends the selected optional From and Through dates');
 elements['ai-history-date-to'].value='';await ctx.aiHistorySearch();check(last('/api/ai/history/search').body.dateFrom==='2026-10-01'&&!('dateTo' in last('/api/ai/history/search').body),'A one-sided date filter preserves the unset opposite boundary');
 elements['ai-history-date-to'].value='2026-09-01';const datedRequests=requests.filter(r=>r.route==='/api/ai/history/search').length;await assert.rejects(ctx.aiHistorySearch(),/From date/);check(requests.filter(r=>r.route==='/api/ai/history/search').length===datedRequests,'A reversed date range is rejected before dispatch');elements['ai-history-date-from'].value='';elements['ai-history-date-to'].value='';
 const first=elements['ai-history-results'].children[0],second=elements['ai-history-results'].children[1];
 check(first.children[0].children[1].textContent.includes('<script>source title</script>')&&first.children[1].textContent==='<b>literal passage</b>','Archive titles and excerpts render as literal text');
 check(second.children.some(c=>String(c.textContent).includes('excluded from standing memory')),'Restricted sources are visibly searchable without promising standing memory');
 first.children[0].children[0].checked=true;first.children[0].children[0].listeners.change();
 check(!elements['ai-handoff-prepare'].disabled&&!elements['ai-memory-propose'].disabled,'Eligible selected sources support reviewed memory and handoff');
 second.children[0].children[0].checked=true;second.children[0].children[0].listeners.change();
 check(elements['ai-memory-propose'].disabled&&!elements['ai-handoff-prepare'].disabled,'Do-not-remember source blocks memory proposals but permits explicit handoff');
 await assert.rejects(ctx.aiHistoryProposeMemory(),/eligible/);check(!last('/api/ai/history/memory-propose'),'Restricted selection never dispatches a memory proposal');
 ctx.aiHistorySelect(restricted,false);elements['ai-memory-text'].value='Reviewed fixture preference';elements['ai-memory-category'].value='preference';await ctx.aiHistoryProposeMemory();
 check(last('/api/ai/history/memory-propose').body.sourceIds[0]===safe.id,'Memory proposal includes the exact selected supporting source');
 await ctx.aiHistoryOpenSource(safe.id);
 check(elements['ai-history-source-text'].textContent===safe.text&&elements['ai-history-source-title'].textContent===safe.title,'Source viewer preserves exact text without executable markup');
 const revisionSource={...safe,revision:'d'.repeat(64),text:'A😀B\n<fixture> end',metadata:{page:7}},citation={...safe,kind:'document',revision:revisionSource.revision,start:1,end:13};sourceReply=revisionSource;searchReply=[citation];await ctx.aiHistorySearch();await elements['ai-history-results'].children[0].children.at(-1).listeners.click();
 check(last('/api/ai/history/source').body.revision===citation.revision&&last('/api/ai/history/source').body.start===1&&last('/api/ai/history/source').body.end===13,'Search citations preserve their exact revision and offsets in source requests');
 check(elements['ai-history-source-text'].textContent==='😀B\n<fixture>'&&elements['ai-history-source-meta'].textContent.includes('Unicode offsets [1, 13)')&&elements['ai-history-source-meta'].textContent.includes('Page 7'),'Cited Unicode passages render the exact character range and document location as text');
 await elements['ai-history-source-actions'].children.at(-1).listeners.click();check(elements['ai-history-source-text'].textContent===revisionSource.text&&last('/api/ai/history/source').body.revision===citation.revision&&last('/api/ai/history/source').body.start===undefined,'Showing the full source retains the cited revision');
 ctx.aiRenderConversation({...conversation,messages:[{role:'assistant',content:'Cited answer',sources:[citation]}]});await elements['ai-messages'].children[0].children.at(-1).listeners.click();check(last('/api/ai/history/source').body.revision===citation.revision&&elements['ai-history-source-text'].textContent==='😀B\n<fixture>','Answer citations open their exact historical passage rather than the latest source');
 sourceReply={...revisionSource,revision:'e'.repeat(64)};await assert.rejects(ctx.aiHistoryOpenSource(citation),/revision is unavailable/);check(elements['ai-history-source-view'].hidden,'A mismatched source revision cannot be presented as the cited evidence');
 sourceReply=revisionSource;await assert.rejects(ctx.aiHistoryOpenSource({...citation,end:999}),/outside/);await assert.rejects(ctx.aiHistoryOpenSource({...citation,start:-1}),/invalid passage/);check(elements['ai-history-source-view'].hidden,'Out-of-range or malformed citation offsets do not silently show different evidence');
 sourceReply=safe;searchReply=[safe,restricted];
 let release;pendingSource=new Promise(resolve=>{release=resolve;});const old=ctx.aiHistoryOpenSource(safe.id);pendingSource=null;sourceReply=restricted;await ctx.aiHistoryOpenSource(restricted.id);release(safe);await old;
 check(elements['ai-history-source-title'].textContent===restricted.title,'Late source response cannot replace a newer source selection');
 sourceReply=safe;
 elements['ai-handoff-note'].value='My selected note';await ctx.aiHistoryPrepareHandoff();
 check(copies.length===0&&elements['ai-handoff-preview'].value.includes('My selected note')&&!elements['ai-handoff-copy'].disabled,'Preparing a handoff displays exact text without copying or sharing');
 await ctx.aiHistoryCopyHandoff();check(copies.length===1&&copies[0]===elements['ai-handoff-preview'].value,'Copy uses exactly the displayed handoff text');
 elements['ai-handoff-note'].value='Changed note';elements['ai-handoff-note'].listeners.input();
 check(elements['ai-handoff-copy'].disabled&&elements['ai-handoff-preview'].value==='','Editing the handoff invalidates the previously reviewed preview');
 let finish;pendingHandoff=new Promise(resolve=>{finish=resolve;});const handoff=ctx.aiHistoryPrepareHandoff();elements['ai-handoff-note'].listeners.input();finish({text:'Stale private preview'});await handoff;pendingHandoff=null;
 check(elements['ai-handoff-preview'].value===''&&elements['ai-handoff-copy'].disabled,'An outdated asynchronous handoff cannot restore copy eligibility');
 ctx.aiRenderConversation(conversation);const transcript=elements['ai-messages'].children[0];elements['ai-history-enabled'].checked=false;elements['ai-history-enabled'].listeners.change();
 check(elements['ai-messages'].children[0]===transcript&&ctx.aiHistoryChatOptions().historyContextReset&&!ctx.aiHistoryChatOptions().historyEnabled,'Switching history off preserves visible transcript and requests fresh inference context');
 elements['ai-prompt'].value='New independent question';await ctx.aiSend();
 check(last('/api/ai/chat').body.historyEnabled===false&&last('/api/ai/chat').body.historyContextReset===true,'Chat dispatch explicitly carries history-off and context-reset policy');
 check(!ctx.aiHistoryChatOptions().historyContextReset,'Successful chat submission consumes the pending context-reset marker');
 elements['ai-history-project'].value='Changed project';elements['ai-history-project'].listeners.change();check(ctx.aiHistoryChatOptions().historyContextReset&&ctx.aiHistoryChatOptions().historyProject==='Changed project','Changing project also requests a fresh inference context');
 state={...state,memories:[{id:'memory-one',text:'Proposed fixture',status:'proposed',category:'preference',sourceIds:[safe.id]}]};await ctx.aiHistoryPoll();
 const memoryCard=elements['ai-memory-list'].children[0],edit=memoryCard.children[1],buttons=memoryCard.children.at(-1);edit.value='Edited fixture';edit.listeners.input();await ctx.aiHistoryPoll();
 check(elements['ai-memory-list'].children[0]===memoryCard&&edit.value==='Edited fixture'&&buttons.children[1].disabled,'Periodic polling preserves an edited proposal and approval requires saving the edit');
 await buttons.children[0].listeners.click();check(last('/api/ai/history/memory-review').body.action==='edit'&&last('/api/ai/history/memory-review').body.text==='Edited fixture','Saving a memory edit dispatches the literal edited proposal');
 const activeGeneration=state.generation;state={...state,generation:null,ready:false,exclusions:[{sourceId:safe.id,enabled:true}],preparedGenerations:[{id:'next-generation',status:'ready',coverage:{records:9}}]};await ctx.aiHistoryPoll();
 check(elements['ai-history-exclusions'].children[0].children[1].disabled,'Restoring a prepared exclusion requires an active archive or an explicit prepared-review scope');
 elements['ai-history-ready'].children[0].children[2].listeners.click();
 check(elements['ai-history-review-status'].textContent.includes('Reviewing prepared snapshot')&&!elements['ai-history-search'].disabled&&elements['ai-history-enabled'].disabled,'Prepared passages can be reviewed before the first active generation');
 check(elements['ai-history-coverage'].textContent.includes('9'),'Prepared review displays coverage for the selected generation');
 await elements['ai-history-exclusions'].children[0].children[1].listeners.click();check(last('/api/ai/history/exclude').body.enabled===false&&last('/api/ai/history/exclude').body.generation==='next-generation','Restoring a prepared exclusion uses the selected generation before first activation');
 elements['ai-history-date-from'].value='2026-10-01';elements['ai-history-date-to'].value='2026-11-02';await ctx.aiHistorySearch();check(last('/api/ai/history/search').body.generation==='next-generation'&&last('/api/ai/history/search').body.dateFrom==='2026-10-01'&&last('/api/ai/history/search').body.dateTo==='2026-11-02'&&elements['ai-history-results'].children[0].children[0].children[0].disabled,'Prepared search preserves generation and date filters without enabling handoff or memory selection');elements['ai-history-date-from'].value='';elements['ai-history-date-to'].value='';
 sourceReply=revisionSource;await ctx.aiHistoryOpenSource(citation);check(last('/api/ai/history/source').body.generation==='next-generation'&&last('/api/ai/history/source').body.revision===citation.revision&&elements['ai-history-source-text'].textContent==='😀B\n<fixture>'&&elements['ai-history-source-actions'].children[0].disabled,'Prepared source viewer retains generation, revision and exact passage while disabling downstream selection');sourceReply=safe;
 await assert.rejects(ctx.aiHistoryPrepareHandoff(),/Activate/);await assert.rejects(ctx.aiHistoryProposeMemory(),/Activate/);
 await elements['ai-history-source-actions'].children[1].listeners.click();check(last('/api/ai/history/exclude').body.generation==='next-generation'&&last('/api/ai/history/exclude').body.id===safe.id,'Pre-activation exclusion carries the reviewed generation and source identity');
 state={...state,generation:activeGeneration,ready:true,exclusions:[]};await ctx.aiHistoryPoll();await ctx.aiHistoryOpenActiveSource(safe.id);check(!last('/api/ai/history/source').body.generation&&elements['ai-history-review-return'].hidden,'Existing answer citations open active local sources even after prepared review');
 elements['ai-history-ready'].children[0].children[2].listeners.click();elements['ai-history-review-return'].listeners.click();check(elements['ai-history-review-return'].hidden&&elements['ai-history-source-view'].hidden,'Returning to the active archive invalidates the prepared source viewer');
 await elements['ai-history-ready'].children[0].children.at(-1).listeners.click();check(last('/api/ai/history/activate').body.generation==='next-generation','Activation is an explicit action bound to the displayed prepared generation');
 await ctx.aiHistoryOpenSource(safe.id);await elements['ai-history-source-actions'].children[1].listeners.click();
 check(last('/api/ai/history/exclude').body.id===safe.id&&last('/api/ai/history/exclude').body.enabled===true&&elements['ai-history-source-view'].hidden&&elements['ai-handoff-copy'].disabled,'Excluding a source clears its viewer and invalidates any prepared handoff');
 elements['ai-history-source-path'].value='C:\\Fixture\\export';elements['ai-history-manifest'].value='D'.repeat(64);await ctx.aiHistoryImport();check(last('/api/ai/history/import').body.expectedManifestSha256==='d'.repeat(64),'Import sends the selected path and normalized explicit manifest pin');
 elements['ai-history-manifest'].value='invalid';await assert.rejects(ctx.aiHistoryImport(),/64 hexadecimal/);check(requests.filter(r=>r.route==='/api/ai/history/import').length===1,'Invalid manifest identity is rejected before import dispatch');
 elements['ai-gateway-label'].value='Fixture app';elements['ai-gateway-history'].checked=true;await ctx.aiHistoryCreateGateway();check(elements['ai-gateway-key'].textContent==='synthetic-client-key'&&copies.length===1,'Client key is displayed locally without copying or cloud sharing');
 elements['ai-history-calibrate-model'].value=JSON.stringify(['local','fixture']);await ctx.aiHistoryCalibrate();check(last('/api/ai/history/calibrate').body.host==='local'&&last('/api/ai/history/calibrate').body.model==='fixture','History calibration dispatches only the explicitly selected local model');
 elements['ai-memory-generate-project'].value='Fixture';await ctx.aiHistoryGenerateMemories();check(last('/api/ai/history/memory-generate').body.project==='Fixture','Automatic memory suggestions are explicitly requested with project scope');
 await ctx.aiHistoryOpenSource(safe.id);elements['ai-history-source-actions'].children[2].children[0].value='Assigned';await elements['ai-history-source-actions'].children[3].listeners.click();check(last('/api/ai/history/project').body.id===safe.id&&last('/api/ai/history/project').body.project==='Assigned','Project assignment binds the edited tag to the displayed source');
 ctx.aiHistorySelect(safe,true);await ctx.aiHistoryPrepareHandoff();let policyRelease;pendingSource=new Promise(resolve=>{policyRelease=resolve;});const policySource=ctx.aiHistoryOpenSource(safe.id);pendingSource=null;state={...state,exclusions:[{sourceId:safe.id,enabled:true}]};await ctx.aiHistoryPoll();policyRelease(safe);await policySource;check(elements['ai-handoff-copy'].disabled&&elements['ai-history-source-view'].hidden&&elements['ai-history-results'].children.length===0,'Synchronized exclusion changes invalidate stale selection, handoff preview, and pending source responses');
 check(!history.includes('innerHTML')&&!history.includes('window.open')&&!history.includes('fetch('),'History content does not become HTML, navigate arbitrary source URLs, or bypass the authenticated API');
 const ids=[...history.matchAll(/\$\('([^']+)'\)/g)].map(m=>m[1]);check(ids.every(id=>html.includes('id="'+id+'"')),'Every history control referenced by the implementation exists in HTML');
 check(['history','memories','sync','handoff'].every(name=>html.includes('id="ai-panel-'+name+'"')&&html.includes('id="ai-tab-'+name+'"')),'All four history workflow panels and navigation controls exist');
 await tick();console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Synthetic DOM/API policy checks; native clipboard and installed UI acceptance remain separate'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
