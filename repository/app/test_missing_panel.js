'use strict';
// Synthetic DOM and mocked authenticated API only; no sockets or project files.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
const block=source.split('// BEGIN MISSING MEDIA PANEL\n')[1].split('// END MISSING MEDIA PANEL')[0];
const elements={},buttons=[],calls=[],checks=[];
function element(tag='div'){return {tag,children:[],value:'',textContent:'',dataset:{},handlers:{},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;},addEventListener(k,fn){this.handlers[k]=fn;},click(){this.clicked=true;},remove(){}};}
const document={createElement:element,body:element('body'),querySelectorAll(){return buttons;}};
let fail=false,records=[];
const context={$:id=>elements[id]??=element(),document,selected:{id:'project-a',name:'Synthetic A'},state:{busy:true,closing:false},mutation:false,notePending:false,missingSequence:0,missingRecords:[],human:String,crypto:{randomUUID:()=>crypto.randomUUID()},prompt:()=>'',Blob,URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL(){}},setTimeout:fn=>fn(),api:async(route,body)=>{calls.push({route,body});if(fail)throw new Error('Synthetic durable write refused');if(route.includes('/export?'))return {filename:'fixed.json',mime:'application/json',text:'{}'};if(route.startsWith('/api/missing?'))return {records};return {status:'RECORDED'};}};
vm.createContext(context);vm.runInContext(block,context);
function check(value,label){assert.ok(value,label);checks.push(label);}
(async()=>{
 context.missingControls();check(elements['missing-report'].disabled===false,'Report remains enabled during an active MAGIX job');
 elements['missing-path']={...element(),value:'<img src=x onerror=alert(1)>.mp4'};elements['missing-note']={...element(),value:'Observed\nΩ'};elements['missing-source']={...element(),value:'user_dialog'};
 await context.reportManual();let sent=calls.find(c=>c.body?.source==='user_dialog');
 check(sent.body.project==='project-a'&&sent.body.path==='<img src=x onerror=alert(1)>.mp4'&&sent.body.note==='Observed\nΩ','Manual text is sent literally and scoped to captured selected ID');
 check(!calls.some(c=>c.route==='/api/action'||c.route==='/api/session'),'Reporting never dispatches an application or session action');
 check(elements['missing-path'].value===''&&context.notePending===false,'Successful report clears form and independent note gate');
 fail=true;elements['missing-path'].value='keep.mp4';await assert.rejects(context.reportManual());fail=false;
 check(elements['missing-path'].value==='keep.mp4'&&context.notePending===false,'Failed durable write retains user input and releases only note gate');
 await context.flagAudit('project-a','ref-1');sent=calls.find(c=>c.body?.source==='audit_flag');
 check(sent.body.auditReferenceId==='ref-1'&&!Object.hasOwn(sent.body,'path'),'Audit flag sends reference ID without accepting a spoofable path');
 records=[{projectId:'project-a',recordId:'a'.repeat(64),path:'<script>bad()</script>',status:'reported_missing',firstSource:'user_dialog',firstNote:'original',note:'latest'}];
 await context.loadMissing();const rendered=elements['missing-records'].children[0];
 check(rendered.children[0].textContent==='<script>bad()</script>','Reported filename is rendered with textContent, never HTML');
 check(rendered.children[2].textContent.includes('original')&&rendered.children[2].textContent.includes('latest'),'Original evidence note remains visible after later status notes');
 const resolved=rendered.children[3].children[0];await resolved.handlers.click();sent=calls.find(c=>c.route==='/api/missing/status');
 check(sent.body.project==='project-a'&&sent.body.recordId==='a'.repeat(64)&&sent.body.status==='resolved','Status update binds record and project, with explicit user-marked status');
 context.notePending=true;context.missingControls();check(elements['missing-path'].disabled&&elements['missing-note'].disabled,'In-flight report prevents editing text that a successful response would clear');await assert.rejects(context.reportManual());context.notePending=false;
 check(!block.includes('innerHTML')&&!block.includes('eval('),'Panel has no HTML parsing or executable report text');
 await context.exportMissing('json');check(document.body.children.at(-1).download==='fixed.json','Export uses backend fixed filename and browser Blob');
 context.state.closing=true;context.missingControls();check(elements['missing-report'].disabled,'Closing interface refuses further user report submission');
 const result={status:'PASS',count:checks.length,checks,appJsSha256:crypto.createHash('sha256').update(source).digest('hex'),syntheticDomOnly:true,noNetwork:true};
 if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
