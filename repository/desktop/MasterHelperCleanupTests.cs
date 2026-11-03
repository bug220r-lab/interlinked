// Synthetic child-process/map cleanup checks. No vendor, USB, sensor or UI calls.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Reflection;
using System.Threading;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class MasterHelperCleanupTests
    {
        private static readonly List<string> Checks=new List<string>();
        private const BindingFlags Hidden=BindingFlags.Instance|BindingFlags.NonPublic;
        private static void Check(bool value,string message){if(!value)throw new Exception(message);Checks.Add(message);}
        private static object Get(Worker worker,string field){return typeof(Worker).GetField(field,Hidden).GetValue(worker);}
        private static void Set(Worker worker,string field,object value){typeof(Worker).GetField(field,Hidden).SetValue(worker,value);}
        private static object Call(Worker worker,string method,params object[] args){return typeof(Worker).GetMethod(method,Hidden).Invoke(worker,args);}
        private sealed class Child:IDisposable
        {
            internal readonly string Name="INTERLINKED-CLEANUP-FIXTURE-"+Guid.NewGuid().ToString("N");
            internal MemoryMappedFile Map;internal MemoryMappedViewAccessor View;internal Process Process;
            internal Child(string mode){
                Map=MemoryMappedFile.CreateNew(Name,64);View=Map.CreateViewAccessor();
                Process=System.Diagnostics.Process.Start(new ProcessStartInfo(Assembly.GetExecutingAssembly().Location,"--child "+mode+" "+Name){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden});
                var ready=Stopwatch.StartNew();while(View.ReadByte(8)!=1&&ready.ElapsedMilliseconds<3000&&!Process.HasExited)Thread.Sleep(5);
                Check(View.ReadByte(8)==1,"Synthetic child reached its private-map ready barrier: "+mode);
            }
            public void Dispose(){if(Process!=null){try{if(!Process.HasExited){Process.Kill();Process.WaitForExit(2000);}}finally{Process.Dispose();}}if(View!=null)View.Dispose();if(Map!=null)Map.Dispose();}
        }
        private static int ChildMain(string mode,string name){
            if(!name.StartsWith("INTERLINKED-CLEANUP-FIXTURE-",StringComparison.Ordinal)||name.Length!="INTERLINKED-CLEANUP-FIXTURE-".Length+32)return 2;
            if(mode!="grace"&&mode!="slow"&&mode!="clobber"&&mode!="ignore"&&mode!="immediate")return 2;
            using(var map=MemoryMappedFile.OpenExisting(name))using(var view=map.CreateViewAccessor()){
                view.Write(8L,(byte)1);view.Flush();if(mode=="immediate")return 0;
                bool clobbered=false;var lifetime=Stopwatch.StartNew();
                while(lifetime.ElapsedMilliseconds<12000){
                    if(mode!="ignore"&&view.ReadUInt32(0)==0xDDCCBBAAu){
                        if(mode=="clobber"&&!clobbered){view.Write(2L,(byte)0);view.Write(12L,1);view.Flush();clobbered=true;Thread.Sleep(35);continue;}
                        if(mode=="slow")Thread.Sleep(150);
                        view.Write(16L,1);view.Flush();return 0;
                    }
                    Thread.Sleep(2);
                }
            }return 3;
        }
        private static void RealLifetimeChecks(){
            var none=Worker.StopOwnedHelper(null,null);
            Check(none.Clean&&none.ExitConfirmed&&!none.Forced&&!none.Graceful,"Absent owned helper needs no signal or termination");
            using(var child=new Child("immediate")){
                Check(child.Process.WaitForExit(2000),"Already-exited fixture terminates independently");
                var result=Worker.StopOwnedHelper(child.Process,child.View);
                Check(result.Clean&&result.ExitConfirmed&&!result.Forced&&!result.Graceful,"Already-exited owned process is confirmed without signalling");
                Check(child.View.ReadUInt32(0)==0,"No exit marker is written after the helper has already exited");
            }
            foreach(string mode in new[]{"grace","slow","clobber"})using(var child=new Child(mode)){
                var result=Worker.StopOwnedHelper(child.Process,child.View);
                Check(result.Clean&&result.ExitConfirmed&&result.Graceful&&!result.Forced,"Owned child uses the graceful vendor marker and exits: "+mode);
                Check(child.Process.HasExited&&child.Process.ExitCode==0&&child.View.ReadInt32(16)==1,"Cleanup returns only after child finalization and actual exit: "+mode);
                Check(result.ElapsedMs<Worker.HelperGraceMilliseconds+500,"Responsive helper cleanup stays in the graceful budget: "+mode);
                if(mode=="clobber")Check(child.View.ReadInt32(12)==1,"A one-time in-flight acknowledgment cannot erase all repeated exit signals");
            }
            using(var owned=new Child("ignore"))using(var borrowed=new Child("ignore")){
                var result=Worker.StopOwnedHelper(owned.Process,owned.View);
                Check(result.Clean&&result.ExitConfirmed&&result.Forced&&!result.Graceful,"Unresponsive owned helper uses bounded forced-exit fallback");
                Check(result.ElapsedMs>=Worker.HelperGraceMilliseconds-30&&result.ElapsedMs<Worker.HelperGraceMilliseconds+Worker.HelperForcedExitMilliseconds+500,"Unresponsive fallback observes graceful and forced-exit budgets");
                Check(owned.Process.HasExited&&!borrowed.Process.HasExited,"Cleanup acts only on its retained owned child, preserving a borrowed process");
                Check(borrowed.View.ReadUInt32(0)==0,"The unrelated child's map receives no termination marker");
            }
            using(var child=new Child("ignore")){
                var result=Worker.StopOwnedHelper(child.Process,null);
                Check(result.Clean&&result.ExitConfirmed&&result.Forced&&result.ElapsedMs<Worker.HelperForcedExitMilliseconds+500,"Partially initialized helper without a map still has bounded owned cleanup");
            }
            using(var child=new Child("ignore")){
                child.View.Dispose();child.View=null;
                using(var disposed=child.Map.CreateViewAccessor()){
                    disposed.Dispose();var result=Worker.StopOwnedHelper(child.Process,disposed);
                    Check(result.Clean&&result.ExitConfirmed&&result.Forced&&result.SignalError=="ObjectDisposedException","Broken signal map is reported while owned termination still completes");
                }
            }
        }
        private static void FailureChecks(){
            int waitTimeout=0,kills=0;
            var failure=Worker.StopHelperLifetime(delegate{return false;},delegate(int timeout){waitTimeout=timeout;return false;},delegate{kills++;},null);
            Check(!failure.Clean&&!failure.ExitConfirmed&&failure.Forced&&kills==1,"A non-exiting predecessor is never reported as clean");
            Check(waitTimeout==Worker.HelperForcedExitMilliseconds,"Failed forced-exit confirmation is bounded");
            var denied=Worker.StopHelperLifetime(delegate{return false;},delegate(int timeout){return false;},delegate{throw new UnauthorizedAccessException();},null);
            Check(!denied.Clean&&!denied.ExitConfirmed&&denied.StopError=="UnauthorizedAccessException","Owned termination permission failure is explicit and not treated as exit");
            using(var worker=new Worker())using(var map=MemoryMappedFile.CreateNew("INTERLINKED-CLEANUP-FIXTURE-"+Guid.NewGuid().ToString("N"),64))using(var view=map.CreateViewAccessor())using(var unqueryable=new Process()){
                Set(worker,"helper",unqueryable);Set(worker,"map",map);Set(worker,"view",view);
                Check(!(bool)Call(worker,"StopUsb"),"Unknown retained process exit state fails cleanup closed");
                Check(Object.ReferenceEquals(Get(worker,"helper"),unqueryable)&&Object.ReferenceEquals(Get(worker,"view"),view)&&Object.ReferenceEquals(Get(worker,"map"),map),"Failed cleanup preserves exact process/map handles for later inspection");
                Set(worker,"requestId",1L);Set(worker,"connectRequested",true);
                bool refused=false;try{Call(worker,"ConnectUsb",1L);}catch(TargetInvocationException ex){var field=ex.InnerException.GetType().GetField("Code",BindingFlags.Instance|BindingFlags.NonPublic);refused=field!=null&&(string)field.GetValue(ex.InnerException)=="cleanup_failed";}
                Check(refused&&Object.ReferenceEquals(Get(worker,"helper"),unqueryable),"A new Connect refuses an unconfirmed predecessor before touching vendor transport");
                Set(worker,"helper",null);Set(worker,"connectRequested",false);
                Check((bool)Call(worker,"StopUsb"),"Once predecessor exit is independently resolved, retained map ownership can be released");
                Check(Get(worker,"map")==null&&Get(worker,"view")==null,"Successful retry leaves no stale owned map handles");
            }
        }
        private static void SerializedCleanupChecks(){
            using(var worker=new Worker())using(var child=new Child("slow")){
                Set(worker,"helper",child.Process);Set(worker,"map",child.Map);Set(worker,"view",child.View);
                bool first=false,second=false;Exception error=null;
                var a=new Thread(delegate(){try{first=(bool)Call(worker,"StopUsb");}catch(Exception ex){error=ex;}});
                var b=new Thread(delegate(){try{second=(bool)Call(worker,"StopUsb");}catch(Exception ex){error=ex;}});
                a.Start();b.Start();Check(a.Join(3000)&&b.Join(3000)&&error==null,"Concurrent cleanup requests serialize and finish without disposing live resources");
                Check(first&&second&&Get(worker,"helper")==null&&Get(worker,"map")==null&&Get(worker,"view")==null,"Both cancellation cleanup callers observe the same completed owned shutdown");
                // Production cleanup disposed these retained handles; avoid a
                // second Process.HasExited call on its disposed wrapper.
                child.Process=null;child.View=null;child.Map=null;
            }
        }
        [STAThread]private static int Main(string[] args){
            if(args.Length==3&&args[0]=="--child")return ChildMain(args[1],args[2]);
            if(args.Length!=1)return 2;string output=Path.GetFullPath(args[0]);Directory.CreateDirectory(output);string error=null;
            try{RealLifetimeChecks();FailureChecks();SerializedCleanupChecks();}catch(Exception ex){error=ex.ToString();}
            var record=new Dictionary<string,object>{{"format","interlinked-master-helper-cleanup-checks-v1"},{"ok",error==null},{"checkCount",Checks.Count},{"checks",Checks},{"error",error},{"hardwareTested",false},{"vendorHelperStarted",false},{"usbRequested",false},{"sensorPublisherStarted",false},{"scope","Actual private synthetic child processes and unique 64-byte maps plus controlled failure operations; no vendor/USB/sensor processes or elevation."}};
            string json=new JavaScriptSerializer().Serialize(record);File.WriteAllText(Path.Combine(output,"report.json"),json);Console.WriteLine(json);return error==null?0:1;
        }
    }
}
