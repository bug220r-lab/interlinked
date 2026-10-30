'use strict';
// Isolated pure-model + synthetic DOM checks. No browser, sockets or processes.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'web','app.js'),'utf8');
const block=source.split('// BEGIN PURE OPEN FEEDBACK\n')[1]?.split('// END PURE OPEN FEEDBACK')[0];
assert.ok(block,'Isolated reviewed feedback block is present');
let writes=0;
const elements={};
for(const id of ['open-feedback','open-feedback-title','open-feedback-text','open-feedback-progress','open-feedback-link']){
 const element={};for(const prop of ['textContent','className','hidden']){let value;Object.defineProperty(element,prop,{get(){return value;},set(next){writes++;value=next;}});}elements[id]=element;
}
const context={state:{},selected:{id:'synthetic-project'},requestedAction:null,$:id=>elements[id]};
vm.createContext(context);vm.runInContext(block,context);
const checks=[];function check(value,label){assert.ok(value,label);checks.push(label);}
const failure={status:'BLOCKED_OR_INTERRUPTED',error:"[WinError32] The process cannot access the file because it is being used by another process: 'C:\\fixture\\.workflow.lock'",automaticRelease:false};
const blocked={busy:false,operation:{id:'operation-one',projectId:'synthetic-project',action:'open',status:'BLOCKED',exitCode:2},output:JSON.stringify(failure)};
check(context.terminalCliError(blocked.output)===failure.error,'Single-line known terminal JSON error is extracted as data');
check(context.terminalCliError('earlier progress\n'+JSON.stringify(failure,null,2))===failure.error,'Indented terminal JSON is extracted after progress');
check(context.terminalCliError('{"status":"COMPLETED","error":"not a failure"}')==='','Success output is not converted to failure');
check(context.terminalCliError('not JSON { malicious fragment')==='','Malformed output stays non-executable data');
let model=context.openFeedbackModel(blocked,context.selected,null);
check(model.tone==='blocked'&&model.text.includes('type DONE')&&model.text.includes('retry Prepare & open'),'Confirmed held-lock result gets explicit original-console completion guidance');
const denied={...blocked,output:JSON.stringify({...failure,error:'Access denied: C:\\fixture\\.workflow.lock'})};
check(context.openFeedbackModel(denied,context.selected,null).text.startsWith('Access denied'),'Access denied alone is not mislabeled as an active console lock');
const active={busy:true,operation:{...blocked.operation,status:'RUNNING',exitCode:null},output:blocked.output+'\nFetching: clip.mp4 | 15/100 bytes | 2 files processed'};
model=context.openFeedbackModel(active,context.selected,null);
check(model.tone==='pending'&&!model.text.includes('did not complete')&&model.progress.includes('15/100 bytes'),'Active fetch shows per-file progress without terminal-failure inference');
check(context.openFeedbackModel(active,context.selected,'open').title==='Starting your request…','Click immediately displays pending request feedback');
model=context.openFeedbackModel({...active,operation:{...active.operation,awaitingDone:true}},context.selected,null);
check(model.text.includes('After MAGIX opens')&&model.title.includes('protected'),'Prompt feedback does not assert MAGIX actually opened');
check(context.openFeedbackModel(blocked,{id:'different-project'},null)===null,'A different selected project cannot inherit this failure notice');
model=context.openFeedbackModel({busy:false,operation:{...blocked.operation,status:'COMPLETED',exitCode:0}},context.selected,null);
check(model.tone==='complete'&&model.text.includes('not automatically saved'),'Successful child exit does not claim save-back or release');
check(context.openFeedbackModel({busy:false,operation:{...blocked.operation,status:'COMPLETED',exitCode:null}},context.selected,null)===null,'Success requires confirmed zero child exit code');
context.state=blocked;context.renderOpenFeedback();
check(elements['open-feedback'].hidden===false&&elements['open-feedback-text'].textContent.includes('type DONE'),'Failure renders visibly beside Prepare using textContent');
const firstWrites=writes;context.renderOpenFeedback();
check(writes===firstWrites,'Repeated identical polling does not rewrite the live status notice');
context.state={...blocked,operation:{...blocked.operation,id:'operation-two'}};context.renderOpenFeedback();
check(writes>firstWrites,'A new owned operation receives its own completion notice');
context.state={...blocked,output:JSON.stringify({...failure,error:'<img src=x onerror=alert(1)>'})};context.renderOpenFeedback();
check(elements['open-feedback-text'].textContent==='<img src=x onerror=alert(1)>','CLI error text is rendered literally, never as HTML');
check(!block.includes('scrollIntoView')&&!block.includes('fetch('),'Polling feedback cannot scroll, retry or dispatch a command');
const event=source.split("$('open-feedback-link').addEventListener('click',")[1]?.split('\n')[0];
check(event&&event.includes('scrollIntoView')&&event.includes("$('operation-log').hidden=false"),'Only explicit Activity-link click expands details and scrolls');
const result={status:'PASS',count:checks.length,checks,appJsSha256:crypto.createHash('sha256').update(source).digest('hex'),syntheticDomOnly:true,noNetwork:true,noFilesystemMutationExceptRequestedReceipt:true};
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
