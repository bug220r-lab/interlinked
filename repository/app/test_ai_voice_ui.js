'use strict';
// Isolated lifecycle mocks only: no real microphone, speaker, model or network.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
const ui=source.split('// BEGIN AI INTERFACE\n')[1].split('// END AI INTERFACE')[0];
const checks=[];const check=(value,label)=>{assert.ok(value,label);checks.push(label)};
let wall=Date.UTC(2026,10,2,12),mono=0,serial=0;
const intervals=new Map(),timeouts=new Map(),requests=[],messages=[],elements={},contexts=[];
function element(){return {hidden:false,checked:false,value:'',textContent:'',classList:{toggle(){}},setAttribute(){},replaceChildren(){},append(){}}}
class ClockDate extends Date{constructor(...args){super(...(args.length?args:[wall]))}static now(){return wall}}
class Track{constructor(){this.kind='audio';this.readyState='live';this.muted=false;this.listeners={};this.stops=0}addEventListener(k,f){this.listeners[k]=f}removeEventListener(k,f){if(this.listeners[k]===f)delete this.listeners[k]}stop(){this.readyState='ended';this.stops++}emit(k){this.listeners[k]?.()}}
class AudioContextMock{constructor(){this.state='suspended';this.sampleRate=16000;this.listeners={};contexts.push(this)}async resume(){if(this.blockResume)await this.blockResume;this.state='running'}async close(){this.state='closed';this.listeners.statechange?.()}addEventListener(k,f){this.listeners[k]=f}removeEventListener(k){delete this.listeners[k]}createMediaStreamSource(){return {connect(){},disconnect(){}}}createScriptProcessor(){this.processor={connect(){},disconnect(){},onaudioprocess:null};return this.processor}createGain(){return {gain:{value:1},connect(){},disconnect(){}}}emit(state){this.state=state;this.listeners.statechange?.()}}
const voice={voiceURI:'fixture-voice',localService:true};
const synth={speaking:false,paused:false,utterances:[],getVoices:()=>[voice],cancel(){this.speaking=false;this.paused=false},speak(value){this.utterances.push(value)}};
const context={console,document:{hidden:false,documentElement:{dataset:{}},createElement:element},$:id=>elements[id]??=element(),
 stopped:false,masterSetView(){},jobsHide(){},Date:ClockDate,performance:{now:()=>mono},Number,Math,
 crypto:{randomUUID:()=> '12345678-1234-4234-8234-123456789abc'},navigator:{},AudioContext:AudioContextMock,
 Float32Array,ArrayBuffer,DataView,Uint8Array,SpeechSynthesisUtterance:function(text){this.text=text},speechSynthesis:synth,
 chrome:{webview:{postMessage:value=>messages.push(value)}},
 setInterval:(fn,ms)=>{const id=++serial;intervals.set(id,{fn,ms});return id},clearInterval:id=>intervals.delete(id),
 setTimeout:(fn,ms)=>{const id=++serial;timeouts.set(id,{fn,ms});return id},clearTimeout:id=>timeouts.delete(id),
 api:async(route,body)=>{requests.push({route,body});return {}},aiHistoryContextReset:false};
vm.createContext(context);vm.runInContext(ui,context);
const run=text=>vm.runInContext(text,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const snapshots=()=>requests.filter(row=>row.route==='/api/master/voice-activity').map(row=>row.body);
const latest=()=>snapshots().at(-1);
function advance(ms){mono+=ms;wall+=ms;for(const timer of [...intervals.values()])timer.fn()}
function sample(){contexts.at(-1).processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array([.1,-.1])}})}
function activeView(){context.document.hidden=false;run("aiVisible=true;aiView='assistant'");context.$('ai-speak').checked=true}
(async()=>{
 activeView();await context.aiPoll?.().catch(()=>{});check(!snapshots().length,'Opening UI without observed voice creates no voice write or heartbeat');
 let currentTrack=new Track();context.navigator.mediaDevices={getUserMedia:async()=>({getTracks:()=>[currentTrack]})};
 await context.aiStartRecording();check(!snapshots().some(s=>s.listening)&&intervals.size===0,'Permission and live stream alone are not Listening before capture samples');
 sample();await settle();check(latest().listening&&latest().speaking==='none'&&intervals.size===1,'Live audio callback starts Listening and one active heartbeat');
 advance(1000);await settle();check(latest().listening,'Fresh observed capture renews the one-second lease');
 advance(1000);await settle();check(!latest().listening&&intervals.size===0,'Stopped audio callbacks expire at two seconds without renewed capture');
 sample();await settle();currentTrack.muted=true;currentTrack.emit('mute');await settle();check(!latest().listening,'Track mute clears capture immediately');
 currentTrack.muted=false;sample();await settle();check(latest().listening,'A fresh sample after unmute may resume Listening');
 contexts.at(-1).emit('suspended');await settle();check(!latest().listening,'Suspended context clears Listening');
 contexts.at(-1).emit('running');await settle();check(!latest().listening,'Context resume alone does not invent new capture');
 sample();await settle();wall-=100;context.aiVoiceObserve(true);await settle();check(!latest().listening,'Capture fails closed when wall clock goes backwards');wall+=100;
 currentTrack.readyState='ended';currentTrack.emit('ended');await settle();check(!latest().listening&&run('aiRecording===null'),'Ended track stops the owned capture and clears its lease');
 let resolvePermission;context.navigator.mediaDevices={getUserMedia:()=>new Promise(resolve=>resolvePermission=resolve)};const pending=context.aiStartRecording();context.aiHide();currentTrack=new Track();resolvePermission({getTracks:()=>[currentTrack]});await pending;check(currentTrack.stops===1&&!latest().listening,'Leaving during permission stops the delayed stream without Listening');
 activeView();context.$('ai-voice').value='fixture-voice';context.aiSpeak('SECRET_REPLY_TEXT');await settle();check(latest().speaking==='none','Queued browser speech is not Speaking');
 let utterance=synth.utterances.at(-1);synth.speaking=true;utterance.onstart();await settle();check(latest().speaking==='browser_tts','Browser onstart with active engine starts Speaking');
 synth.paused=true;utterance.onpause();await settle();check(latest().speaking==='none','Browser pause clears Speaking');
 synth.paused=false;utterance.onresume();await settle();check(latest().speaking==='browser_tts','Browser resume can restart observed Speaking');
 context.aiSpeak('NEW_REPLY');let newest=synth.utterances.at(-1);synth.speaking=true;newest.onstart();await settle();const count=snapshots().length;utterance.onend();utterance.onerror();await settle();check(snapshots().length===count&&latest().speaking==='browser_tts','Late callbacks from the older utterance cannot clear newer playback');
 context.aiStopSpeech();await settle();utterance.onresume();await settle();check(latest().speaking==='none'&&intervals.size===0,'Explicit stop cancels playback and fences old callbacks');
 const spoken=synth.utterances.length;context.document.hidden=true;context.aiSpeak('HIDDEN_REPLY');check(synth.utterances.length===spoken,'A late reply while document is hidden cannot start speech');context.document.hidden=false;
 context.$('ai-voice').value='native:fixture-token';context.aiSpeak('NATIVE_SECRET_TEXT');await settle();const request=messages.filter(m=>m.action==='speak').at(-1);check(latest().speaking==='none','Native Speak request does not itself mark Speaking');
 const status=(speaking,overrides={})=>({type:'interlinked-ai-speech-status',sessionId:request.sessionId,generation:request.generation,streamId:7,speaking,observedAt:new ClockDate().toISOString(),...overrides});
 context.aiNativeSpeechStatus(status(true));await settle();check(latest().speaking==='windows_sapi','Matching observed native stream starts Windows Speaking');
 let before=snapshots().length;context.aiNativeSpeechStatus(status(false,{generation:request.generation-1}));context.aiNativeSpeechStatus(status(false,{sessionId:'f'.repeat(32)}));context.aiNativeSpeechStatus(status(false,{streamId:9}));await settle();check(snapshots().length===before&&latest().speaking==='windows_sapi','Old generation, document session and mismatched stream statuses are ignored');
 context.aiNativeSpeechStatus(status(false));await settle();check(latest().speaking==='none','Matching native completion clears Speaking');
 context.aiSpeak('NATIVE_2');const request2=messages.filter(m=>m.action==='speak').at(-1);const delayed={type:'interlinked-ai-speech-status',sessionId:request2.sessionId,generation:request2.generation,streamId:8,speaking:true,observedAt:new ClockDate(wall-1500).toISOString()};context.aiNativeSpeechStatus(delayed);await settle();check(latest().speaking==='windows_sapi','A delayed native status may use only its remaining freshness');advance(600);await settle();check(latest().speaking==='none','Delayed native status expires after remaining budget, not a fresh two seconds');
 context.aiSpeak('NATIVE_3');const request3=messages.filter(m=>m.action==='speak').at(-1);context.aiNativeSpeechStatus({type:'interlinked-ai-speech-status',sessionId:request3.sessionId,generation:request3.generation,streamId:10,speaking:true,observedAt:new ClockDate().toISOString()});await settle();wall-=1;context.aiVoiceObserve(true);await settle();check(latest().speaking==='none','Native evidence fails closed on wall-clock rollback');wall+=1;
 context.aiStopSpeech();await settle();activeView();context.$('ai-voice').value='fixture-voice';context.aiSpeak('NEWEST');newest=synth.utterances.at(-1);synth.speaking=true;context.document.hidden=true;newest.onstart();await settle();check(latest().speaking==='none','Hidden document cannot activate a delayed browser callback');context.aiHide();await settle();
 const bodies=snapshots();check(bodies.every(body=>Object.keys(body).sort().join('|')==='listening|observedAt|sequence|sessionId|speaking'),'Every write has the exact five-field telemetry allowlist');
 check(bodies.every(body=>/^[a-f0-9]{32}$/.test(body.sessionId)&&Number.isSafeInteger(body.sequence)&&body.sequence>0&&Number.isFinite(Date.parse(body.observedAt))),'Session, sequence and UTC observation identities are bounded');
 check(bodies.every((body,index)=>!index||body.sequence>bodies[index-1].sequence),'Full-state snapshots have strictly increasing sequence numbers');
 check(!JSON.stringify(bodies).includes('SECRET')&&!JSON.stringify(bodies).includes('fixture-token')&&!JSON.stringify(bodies).includes('fixture-voice'),'No audio, text, voice names or token selection enters telemetry');
 check(intervals.size===0,'No heartbeat remains once actual capture/playback is inactive');
 console.log(JSON.stringify({status:'PASS',count:checks.length,checks,scope:'Mocked capture, browser/native speech callbacks and metadata writes only; no microphone, voice, inference or network invoked'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
