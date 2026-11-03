// Pure fake-status/request-fence tests. Never instantiates SAPI or WebView2.
using System;
using System.Collections.Generic;
using System.Threading.Tasks;
namespace InterlinkedDesktop {
internal static class AiSpeechActivityTests {
 static readonly List<string> checks=new List<string>();
 static void Check(bool value,string label){if(!value)throw new Exception(label);checks.Add(label);}
 static async Task<bool> Deferred(AiSpeechRequestFence fence,long ticket,object requested,Func<object> actual,string session,long generation,Task<bool> visible){return await visible&&fence.Current(ticket,requested,actual(),session,generation);}
 static async Task<bool> DeferredFailure(AiSpeechRequestFence fence,long ticket,object requested,Func<object> actual,string session,long generation,Task<bool> visible){try{await visible;return false;}catch(Exception){return fence.Current(ticket,requested,actual(),session,generation);}}
 static int Main(){try{
  var now=new DateTime(2026,11,2,12,0,0,DateTimeKind.Utc);string session=new string('a',32);var status=new AiSpeechRuntimeStatus();
  status.Begin(session,1,7,now);Check(status.Pending&&!status.Speaking,"Speak invocation alone is not observed Speaking");
  Check(!status.Observe(0,0,now.AddMilliseconds(200)),"Queued SAPI state stays inactive");
  Check(!status.Observe(1,0,now.AddMilliseconds(250))&&status.Pending,"Initial asynchronous Done sample retains bounded startup grace");
  Check(!status.Observe(2,8,now.AddMilliseconds(300)),"A different stream cannot mark this request Speaking");
  Check(status.Observe(2,7,now.AddMilliseconds(400)),"Matching SAPI engine/stream establishes Speaking");
  Check(status.PublishDue(now.AddMilliseconds(400)),"Observed start publishes once");
  Check(!status.PublishDue(now.AddMilliseconds(1399)),"Active status does not publish faster than its heartbeat");
  Check(status.PublishDue(now.AddMilliseconds(1400)),"Active engine heartbeat is due at one second");
  Check(!status.Observe(0,0,now.AddSeconds(2))&&status.PublishDue(now.AddSeconds(2)),"Paused/waiting engine clears Speaking immediately");
  Check(status.Observe(2,7,now.AddSeconds(3)),"Matching engine may resume before bounded inactivity timeout");
  Check(!status.Observe(1,0,now.AddSeconds(4))&&!status.Pending,"SAPI done state ends polling");
  status.Begin(session,2,9,now);Check(!status.Observe(0,0,now.AddSeconds(20))&&!status.Pending,"An engine that never begins is bounded at twenty seconds");
  status.Begin(session,3,10,now);Check(!status.Observe(2,10,now.AddMinutes(30))&&!status.Pending,"A status observer is bounded at thirty minutes without stopping audio itself");
  status.Begin(session,4,11,now);Check(!status.Observe(2,11,now.AddTicks(-1))&&!status.Pending,"Clock rollback invalidates native evidence");
  status.Begin(session,5,12,now);status.Observe(2,12,now);status.Cancel();Check(!status.Speaking&&!status.Pending,"Cancel clears observed native activity");
  bool invalid=false;try{status.Begin(session,0,1,now);}catch(ArgumentException){invalid=true;}Check(invalid,"Invalid native generation is refused");
  var fence=new AiSpeechRequestFence();object core=new object(),actualCore=core;long ticket=fence.Begin(core,session,1);
  Check(fence.Current(ticket,core,actualCore,session,1),"Current core/document/request ticket is accepted");
  var visible=new TaskCompletionSource<bool>();var pending=Deferred(fence,ticket,core,()=>actualCore,session,1,visible.Task);fence.Cancel();visible.SetResult(true);Check(!pending.GetAwaiter().GetResult(),"Cancel during asynchronous UI check prevents speech continuation");
  ticket=fence.Begin(core,session,2);visible=new TaskCompletionSource<bool>();pending=Deferred(fence,ticket,core,()=>actualCore,session,2,visible.Task);fence.Cancel();actualCore=new object();visible.SetResult(true);Check(!pending.GetAwaiter().GetResult(),"Navigation/reload during UI check fences the old document");
  ticket=fence.Begin(core,session,3);Check(!fence.Current(ticket,core,new object(),session,3),"Core identity mismatch is rejected even without an epoch change");
  ticket=fence.Begin(core,session,4);fence.Begin(core,new string('b',32),1);Check(!fence.Current(ticket,core,core,session,4),"A new document namespace supersedes its old request");
  actualCore=core;ticket=fence.Begin(core,session,5);visible=new TaskCompletionSource<bool>();pending=DeferredFailure(fence,ticket,core,()=>actualCore,session,5,visible.Task);fence.Begin(core,session,6);visible.SetException(new InvalidOperationException("old UI check failed"));Check(!pending.GetAwaiter().GetResult(),"Exception from superseded asynchronous UI check cannot stop the new request");
  ticket=fence.Begin(core,session,7);visible=new TaskCompletionSource<bool>();pending=DeferredFailure(fence,ticket,core,()=>actualCore,session,7,visible.Task);visible.SetException(new InvalidOperationException("current UI check failed"));Check(pending.GetAwaiter().GetResult(),"Only the current failed UI check may cancel its own request");
  Console.WriteLine("{\"status\":\"PASS\",\"count\":"+checks.Count+",\"voiceInvoked\":false,\"hardwareInvoked\":false}");foreach(string item in checks)Console.WriteLine(item);return 0;
 }catch(Exception ex){Console.Error.WriteLine(ex);return 1;}}
}}
