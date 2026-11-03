// Separate fixture entry point. Children use fake reads/publication and local
// fixture leases only; no production path, task, driver or sensor is invoked.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Security.Principal;
using System.Text;
using System.Threading;

internal static class MsiCpuSupervisorFixture
{
    private static int checks;
    private static void Check(bool value){if(!value)throw new Exception("CPU supervisor fixture failed: "+checks);checks++;}
    private static string Self {get{return Assembly.GetExecutingAssembly().Location;}}
    private static MsiCpuSupervisor.Settings Settings(string mode,int retries) {
        return new MsiCpuSupervisor.Settings{Executable=Self,Directory=Path.GetDirectoryName(Self),Hash=MsiSensorCommon.HashFile(Self),Sid=WindowsIdentity.GetCurrent().User.Value,Deadline=1500,Delay=30,MaxRetries=retries,ExitWait=1500,Arguments=delegate(MsiCpuSupervisor.Auth auth){return "--fixture-worker "+mode+" "+auth.Arguments;}};
    }
    private static MsiCpuSupervisor.Result Run(string mode,int retries,WaitHandle cancel,Func<MsiCpuSupervisor.OwnedChild,int,bool> stop) {
        var previous=new List<Process>();var settings=Settings(mode,retries);long lastExit=0;
        settings.Stop=delegate(MsiCpuSupervisor.OwnedChild child,int wait){bool confirmed=stop==null?child.StopAndConfirm(wait):stop(child,wait);lastExit=Stopwatch.GetTimestamp();return confirmed;};
        settings.Failed=delegate(string message){File.AppendAllText(Path.Combine(settings.Directory,"failure.txt"),mode+" "+message+Environment.NewLine);};
        settings.Started=delegate(int attempt,MsiCpuSupervisor.OwnedChild child){foreach(var p in previous)Check(p.HasExited);if(attempt>0)Check((Stopwatch.GetTimestamp()-lastExit)*1000.0/Stopwatch.Frequency>=settings.Delay-1);var owned=Process.GetProcessById((int)child.Id);IntPtr retained=owned.Handle;previous.Add(owned);};
        try {var result=MsiCpuSupervisor.Run(settings,cancel);foreach(var p in previous)Check(p.HasExited);Check(result.Attempts==previous.Count);return result;}
        finally{foreach(var p in previous)p.Dispose();}
    }
    private static int Worker(string[] args) {
        string mode=args[1];var auth=MsiCpuSupervisor.Auth.Parse(args,2);
        if(mode=="initialization-hang"){Thread.Sleep(Timeout.Infinite);return 9;}
        if(mode=="partial-auth")using(var pipe=new NamedPipeClientStream(".",auth.Pipe,PipeDirection.InOut,PipeOptions.Asynchronous)){pipe.Connect(1000);byte[] half=Encoding.ASCII.GetBytes(auth.Nonce.Substring(0,32));pipe.Write(half,0,half.Length);Thread.Sleep(Timeout.Infinite);return 9;}
        if(mode=="wrong-parent")auth.Created--;
        if(mode=="wrong-nonce")auth.Nonce=(auth.Nonce[0]=='0'?"1":"0")+auth.Nonce.Substring(1);
        using(var pipe=MsiCpuSupervisor.ConnectWorker(auth,Self,MsiSensorCommon.HashFile(Self)))
        using(var lease=File.Open(Path.Combine(Path.GetDirectoryName(Self),"fake-reader.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)) {
            long sequence=0;
            if(mode=="read-hang"){Thread.Sleep(Timeout.Infinite);return 9;}
            if(mode=="exit-four")return 4;
            if(mode=="partial")while(true){pipe.Write(new byte[]{0x50},0,1);Thread.Sleep(500);}
            if(mode=="forged"){var frame=new byte[9];frame[0]=0x51;BitConverter.GetBytes(1L).CopyTo(frame,1);pipe.Write(frame,0,frame.Length);Thread.Sleep(Timeout.Infinite);}
            MsiCpuSupervisor.CompletedPublication(pipe,ref sequence); // Fake completed constant 49 C sample.
            if(mode=="duplicate"||mode=="stale"){var frame=new byte[9];frame[0]=0x50;BitConverter.GetBytes(mode=="duplicate"?1L:0L).CopyTo(frame,1);pipe.Write(frame,0,frame.Length);Thread.Sleep(Timeout.Infinite);}
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(Self),"published-"+Process.GetCurrentProcess().Id+".txt"),"published");
            if(mode=="publication-hang"){Thread.Sleep(Timeout.Infinite);return 9;}
            while(true){Thread.Sleep(20);MsiCpuSupervisor.CompletedPublication(pipe,ref sequence);}
        }
    }
    private static int Main(string[] args) {
        try {
            if(args.Length>0&&args[0]=="--fixture-worker")return Worker(args);
            if(args.Length==3&&args[0]=="--contain-parent") {
                var settings=Settings(args[1],0);settings.Deadline=10000;
                if(args[1]=="suspended")settings.BeforeResume=delegate(MsiCpuSupervisor.OwnedChild child){File.WriteAllText(args[2],child.Id+" "+child.Created);Thread.Sleep(Timeout.Infinite);};
                else settings.Started=delegate(int attempt,MsiCpuSupervisor.OwnedChild child){File.WriteAllText(args[2],child.Id+" "+child.Created);};
                return MsiCpuSupervisor.Run(settings,null).Code;
            }
            if(args.Length!=0)return 2;
            Check(MsiCpuSupervisor.DeadlineMilliseconds==30000);Check(MsiCpuSupervisor.RetryMilliseconds==60000);Check(MsiCpuSupervisor.Retries==3);
            using(var delayedExit=new ManualResetEvent(false)) {
                int requests=0;Thread finish=null;var elapsed=Stopwatch.StartNew();
                bool confirmed=MsiCpuSupervisor.ConfirmStopped(delayedExit,1000,delegate{
                    requests++;finish=new Thread(new ThreadStart(delegate{Thread.Sleep(50);delayedExit.Set();}));finish.IsBackground=true;finish.Start();return false;
                });
                Check(confirmed);Check(requests==1);Check(elapsed.ElapsedMilliseconds>=40);Check(finish.Join(1000));
            }
            using(var noExit=new ManualResetEvent(false)) {
                int requests=0;var elapsed=Stopwatch.StartNew();
                Check(!MsiCpuSupervisor.ConfirmStopped(noExit,30,delegate{requests++;return false;}));Check(requests==1);Check(elapsed.ElapsedMilliseconds>=20);
            }
            using(var alreadyExited=new ManualResetEvent(true)) {
                int requests=0;Check(MsiCpuSupervisor.ConfirmStopped(alreadyExited,1000,delegate{requests++;return false;}));Check(requests==0);
            }
            string readerLease=Path.Combine(Path.GetDirectoryName(Self),"reader-lease-contract.lock");File.WriteAllText(readerLease,"");
            using(var held=MsiCpuOnly.OpenReaderLease(readerLease)){bool refused=false;try{using(var duplicate=MsiCpuOnly.OpenReaderLease(readerLease)){};}catch(IOException){refused=true;}Check(refused);}
            using(var released=MsiCpuOnly.OpenReaderLease(readerLease))Check(released.CanWrite);
            string hash=new string('a',64),path=@"C:\fixed.exe";
            Check(MsiCpuSupervisor.PeerMatches(1,1,2,2,path,path,hash,hash));
            Check(!MsiCpuSupervisor.PeerMatches(9,1,2,2,path,path,hash,hash));Check(!MsiCpuSupervisor.PeerMatches(1,1,9,2,path,path,hash,hash));
            Check(!MsiCpuSupervisor.PeerMatches(1,1,2,2,@"C:\foreign.exe",path,hash,hash));Check(!MsiCpuSupervisor.PeerMatches(1,1,2,2,path,path,new string('b',64),hash));
            string[] valid={"INTERLINKED-MSI-CPU-"+new string('a',32),hash,"1","2",hash};Check(MsiCpuSupervisor.Auth.Parse(valid,0).Parent==1);
            foreach(int changed in new[]{0,1,2,3,4}){var invalid=(string[])valid.Clone();invalid[changed]="bad";bool refused=false;try{MsiCpuSupervisor.Auth.Parse(invalid,0);}catch(IOException){refused=true;}Check(refused);}
            var surplus=new string[6];Array.Copy(valid,surplus,5);bool tooMany=false;try{MsiCpuSupervisor.Auth.Parse(surplus,0);}catch(IOException){tooMany=true;}Check(tooMany);
            foreach(string mode in new[]{"initialization-hang","read-hang","publication-hang"}) {
                var result=Run(mode,1,null,null);Check(result.Code==4);Check(result.Attempts==2);
                Check(result.Authenticated==(mode=="initialization-hang"?0:2));Check(result.Publications==(mode=="publication-hang"?2:0));
            }
            foreach(string mode in new[]{"wrong-parent","wrong-nonce","partial-auth"}){var result=Run(mode,0,null,null);Check(result.Code==4);Check(result.Authenticated==0);Check(result.Publications==0);}
            foreach(string mode in new[]{"forged","partial","duplicate","stale"}){var result=Run(mode,0,null,null);Check(result.Code==4);Check(result.Authenticated==1);Check(result.Publications==((mode=="duplicate"||mode=="stale")?1:0));}
            var budget=Run("exit-four",3,null,null);Check(budget.Code==4);Check(budget.Attempts==4);Check(budget.Authenticated==4);
            var transient=Run("publication-hang",3,null,null);Check(transient.Code==4);Check(transient.Attempts==4);Check(transient.Publications==4);
            var unconfirmed=Run("read-hang",3,null,delegate(MsiCpuSupervisor.OwnedChild child,int wait){child.StopAndConfirm(wait);return false;});Check(unconfirmed.Code==5);Check(unconfirmed.Attempts==1);
            using(var cancel=new ManualResetEvent(false)) {
                var timer=new Thread(new ThreadStart(delegate{Thread.Sleep(800);cancel.Set();}));timer.IsBackground=true;timer.Start();
                var healthy=Run("healthy",0,cancel,null);timer.Join(1500);Check(healthy.Code==0);Check(healthy.Attempts==1);Check(healthy.Authenticated==1);Check(healthy.Publications>2);
            }
            Console.WriteLine("{\"status\":\"PASS\",\"checks\":"+checks+",\"hardwareStarted\":false,\"driverStarted\":false}");return 0;
        }catch(Exception e){if(args.Length>0&&args[0]=="--fixture-worker")File.AppendAllText(Path.Combine(Path.GetDirectoryName(Self),"child-error.txt"),e.ToString()+Environment.NewLine);Console.Error.WriteLine(e.GetType().Name+": "+e.Message);return 3;}
    }
}
