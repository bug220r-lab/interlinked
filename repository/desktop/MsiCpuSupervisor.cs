// Fixed MSI CPU-only child supervision. No task, driver or node configuration API.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32.SafeHandles;

internal static class MsiCpuSupervisor
{
    internal const int DeadlineMilliseconds=30000, RetryMilliseconds=60000, Retries=3;
    internal sealed class Auth {
        internal string Pipe,Nonce,Hash;internal int Parent;internal long Created;
        internal string Arguments {get{return Pipe+" "+Nonce+" "+Parent+" "+Created+" "+Hash;}}
        internal static Auth Parse(string[] args,int offset) {
            int pid;long created;
            if(args.Length!=offset+5||!Regex.IsMatch(args[offset],"\\AINTERLINKED-MSI-CPU-[a-f0-9]{32}\\z")||!Hex(args[offset+1])||!Int32.TryParse(args[offset+2],out pid)||pid<=0||!Int64.TryParse(args[offset+3],out created)||created<=0||!Hex(args[offset+4]))throw new IOException("CPU child authentication arguments refused.");
            return new Auth{Pipe=args[offset],Nonce=args[offset+1],Parent=pid,Created=created,Hash=args[offset+4]};
        }
    }
    internal sealed class Settings {
        internal string Executable,Directory,Hash,Sid;internal int Deadline=DeadlineMilliseconds,Delay=RetryMilliseconds,MaxRetries=Retries,ExitWait=5000;
        internal Func<Auth,string> Arguments=null;internal Action<int,OwnedChild> Started=null;internal Action<OwnedChild> BeforeResume=null;internal Func<OwnedChild,int,bool> Stop=null;internal Action<string> Failed=null;
    }
    internal sealed class Result {internal int Code,Attempts,Authenticated,Publications;}
    private static bool Hex(string value){return value!=null&&Regex.IsMatch(value,"\\A[a-f0-9]{64}\\z");}
    internal static bool PeerMatches(int actualPid,int expectedPid,long actualCreated,long expectedCreated,string actualPath,string expectedPath,string actualHash,string expectedHash) {
        return actualPid==expectedPid&&actualCreated==expectedCreated&&String.Equals(actualPath,expectedPath,StringComparison.OrdinalIgnoreCase)&&actualHash==expectedHash;
    }
    private static string Nonce(){var bytes=new byte[32];using(var random=RandomNumberGenerator.Create())random.GetBytes(bytes);return BitConverter.ToString(bytes).Replace("-","").ToLowerInvariant();}
    private static NamedPipeServerStream Server(string name,string sid) {
        var identity=new SecurityIdentifier(sid);var acl=new PipeSecurity();acl.SetOwner(identity);acl.SetAccessRuleProtection(true,false);
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null),PipeAccessRights.FullControl,AccessControlType.Deny));
        acl.AddAccessRule(new PipeAccessRule(identity,PipeAccessRights.FullControl,AccessControlType.Allow));
        return new NamedPipeServerStream(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,acl);
    }
    private static int Remaining(long last,int milliseconds) {
        double elapsed=(Stopwatch.GetTimestamp()-last)*1000.0/Stopwatch.Frequency;
        if(elapsed>=milliseconds)return 0;return Math.Max(1,(int)Math.Ceiling(milliseconds-elapsed));
    }
    private static byte[] ReadExact(PipeStream pipe,int length,int milliseconds) {
        var bytes=new byte[length];int received=0;long start=Stopwatch.GetTimestamp();
        while(received<length) {
            int remaining=Remaining(start,milliseconds);if(remaining==0)throw new IOException("CPU child pipe deadline expired.");
            var pending=pipe.BeginRead(bytes,received,length-received,null,null);
            // Never close an APM event while its native callback is pending.
            // Cancellation closes the pipe; that callback retains its event.
            if(!pending.AsyncWaitHandle.WaitOne(remaining))throw new IOException("CPU child pipe deadline expired.");
            int count;try{count=pipe.EndRead(pending);}finally{pending.AsyncWaitHandle.Close();}
            if(count==0)throw new IOException("CPU child pipe closed.");received+=count;
        }
        return bytes;
    }
    private static void Write(PipeStream pipe,byte[] bytes,int milliseconds) {
        if(milliseconds<=0)throw new IOException("CPU child pipe deadline expired.");
        var pending=pipe.BeginWrite(bytes,0,bytes.Length,null,null);
        if(!pending.AsyncWaitHandle.WaitOne(milliseconds))throw new IOException("CPU child pipe deadline expired.");
        try{pipe.EndWrite(pending);}finally{pending.AsyncWaitHandle.Close();}
    }
    internal static void CompletedPublication(PipeStream pipe,ref long sequence){sequence=checked(sequence+1);var bytes=new byte[9];bytes[0]=0x50;BitConverter.GetBytes(sequence).CopyTo(bytes,1);Write(pipe,bytes,5000);}
    internal static NamedPipeClientStream ConnectWorker(Auth auth,string self,string hash) {
        if(auth.Hash!=hash||!VerifyProcess(auth.Parent,auth.Created,self,hash))throw new IOException("CPU child parent identity differs.");
        var pipe=new NamedPipeClientStream(".",auth.Pipe,PipeDirection.InOut,PipeOptions.Asynchronous,TokenImpersonationLevel.Identification);
        try {
            pipe.Connect(5000);uint server;
            if(!GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out server)||server!=auth.Parent||!VerifyProcess(auth.Parent,auth.Created,self,hash))throw new IOException("CPU child pipe owner differs.");
            byte[] nonce=Encoding.ASCII.GetBytes(auth.Nonce);Write(pipe,nonce,5000);
            if(Encoding.ASCII.GetString(ReadExact(pipe,64,5000))!=auth.Nonce||!VerifyProcess(auth.Parent,auth.Created,self,hash))throw new IOException("CPU child parent handshake differs.");
            return pipe;
        }catch{pipe.Dispose();throw;}
    }
    internal static int RunProduction(string hash) {
        string lease=Path.Combine(MsiSensorCommon.DataFolder,"cpu-supervisor.lock");
        if(!File.Exists(lease))using(var created=MsiSensorCommon.NewProtectedFile(lease)){}
        MsiSensorCommon.Protected(lease,false);
        using(var singleton=File.Open(lease,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
        using(var pinned=File.Open(MsiSensorCommon.PublisherPath,FileMode.Open,FileAccess.Read,FileShare.Read))
        using(var descriptor=File.Open(MsiSensorCommon.DescriptorPath,FileMode.Open,FileAccess.Read,FileShare.Read)) {
            var settings=new Settings{Executable=MsiSensorCommon.PublisherPath,Directory=MsiSensorCommon.Folder,Hash=hash,Sid=WindowsIdentity.GetCurrent().User.Value,Arguments=delegate(Auth auth){return "--cpu-worker "+auth.Arguments;}};
            return Run(settings,null).Code;
        }
    }
    // All variable paths, timings and callbacks are internal fixture injection.
    // Production can enter only through the fixed RunProduction wrapper above.
    internal static Result Run(Settings settings,WaitHandle cancel) {
        if(settings.Deadline<=0||settings.Delay<0||settings.MaxRetries<0||settings.MaxRetries>Retries||settings.ExitWait<=0||settings.Arguments==null||!Hex(settings.Hash))throw new ArgumentException("CPU supervisor settings differ.");
        int authenticated=0,publications=0;
        for(int attempt=0;attempt<=settings.MaxRetries;attempt++) {
            bool stopped=false,confirmed=true;OwnedChild child=null;NamedPipeServerStream pipe=null;
            try {
                long started=Stopwatch.GetTimestamp();Auth auth;
                using(var self=Process.GetCurrentProcess())auth=new Auth{Pipe="INTERLINKED-MSI-CPU-"+Guid.NewGuid().ToString("N"),Nonce=Nonce(),Parent=self.Id,Created=self.StartTime.ToUniversalTime().Ticks,Hash=settings.Hash};
                pipe=Server(auth.Pipe,settings.Sid);child=OwnedChild.Start(settings.Executable,settings.Directory,settings.Arguments(auth),settings.ExitWait,settings.BeforeResume);
                if(settings.Started!=null)settings.Started(attempt,child);
                var connecting=pipe.BeginWaitForConnection(null,null);
                if(!connecting.AsyncWaitHandle.WaitOne(Remaining(started,settings.Deadline)))throw new IOException("CPU child connection expired.");
                try{pipe.EndWaitForConnection(connecting);}finally{connecting.AsyncWaitHandle.Close();}uint client;
                if(!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out client)||client!=child.Id||!child.Matches(settings.Executable,settings.Hash))throw new IOException("CPU child identity differs.");
                if(Encoding.ASCII.GetString(ReadExact(pipe,64,Math.Min(5000,Remaining(started,settings.Deadline))))!=auth.Nonce)throw new IOException("CPU child nonce differs.");
                Write(pipe,Encoding.ASCII.GetBytes(auth.Nonce),Math.Min(5000,Remaining(started,settings.Deadline)));
                authenticated++;
                long completed=started,sequence=0;var bytes=new byte[9];int received=0;
                while(true) {
                    int remaining=Remaining(completed,settings.Deadline);if(remaining==0)throw new IOException("CPU child publication progress expired.");
                    var pending=pipe.BeginRead(bytes,received,bytes.Length-received,null,null);
                    {
                        var wait=pending.AsyncWaitHandle;
                        var handles=cancel==null?new WaitHandle[]{child.Exit,wait}:new WaitHandle[]{child.Exit,wait,cancel};
                        int signaled=WaitHandle.WaitAny(handles,remaining);
                        if(signaled==2){stopped=true;break;}
                        if(signaled!=1)throw new IOException("CPU child stopped or publication progress expired.");
                    }
                    int count;try{count=pipe.EndRead(pending);}finally{pending.AsyncWaitHandle.Close();}
                    if(count==0)throw new IOException("CPU child progress pipe closed.");received+=count;
                    if(received==bytes.Length) {
                        long next=BitConverter.ToInt64(bytes,1);
                        if(bytes[0]!=0x50||next<=sequence)throw new IOException("CPU child progress sequence differs.");
                        sequence=next;received=0;completed=Stopwatch.GetTimestamp();publications++;
                    }
                }
            }catch(UnconfirmedExit){confirmed=false;}
            catch(Exception e){if(settings.Failed!=null)settings.Failed(e.GetType().Name+": "+e.Message);}
            finally {
                if(pipe!=null)pipe.Dispose();
                if(child!=null){
                    bool exited=settings.Stop==null?child.StopAndConfirm(settings.ExitWait):settings.Stop(child,settings.ExitWait);
                    confirmed=exited&&confirmed;
                    // Failed cleanup never releases ownership to a replacement.
                    // Hold the job, process handle and outer supervisor lease
                    // until the exact owned process is actually signaled.
                    if(!exited)while(!child.Exit.WaitOne(1000)){}
                    child.Dispose();
                }
            }
            if(!confirmed)return new Result{Code=5,Attempts=attempt+1,Authenticated=authenticated,Publications=publications};
            if(stopped)return new Result{Code=0,Attempts=attempt+1,Authenticated=authenticated,Publications=publications};
            if(attempt==settings.MaxRetries)return new Result{Code=4,Attempts=attempt+1,Authenticated=authenticated,Publications=publications};
            if(cancel==null)Thread.Sleep(settings.Delay);else if(cancel.WaitOne(settings.Delay))return new Result{Code=0,Attempts=attempt+1,Authenticated=authenticated,Publications=publications};
        }
        throw new InvalidOperationException();
    }
    private sealed class UnconfirmedExit:IOException{}
    private sealed class ProcessWait:WaitHandle {internal ProcessWait(IntPtr process){SafeWaitHandle=new SafeWaitHandle(process,false);}}
    internal static bool ConfirmStopped(WaitHandle exited,int milliseconds,Func<bool> requestTermination) {
        if(milliseconds<0)throw new ArgumentOutOfRangeException("milliseconds");
        if(exited.WaitOne(0))return true;
        // TerminateProcess can fail while an independently terminated child is
        // still completing exit. Its return value never replaces the bounded
        // wait for the exact retained process handle to become signaled.
        requestTermination();return exited.WaitOne(milliseconds);
    }
    internal sealed class OwnedChild:IDisposable {
        private IntPtr process,job;internal uint Id;internal long Created;internal WaitHandle Exit;
        internal static OwnedChild Start(string executable,string directory,string arguments,int exitWait,Action<OwnedChild> beforeResume) {
            var owned=new OwnedChild();owned.job=NewJob();var startup=new STARTUPINFOEX();startup.Info.Size=Marshal.SizeOf(typeof(STARTUPINFOEX));startup.Info.Flags=1;startup.Info.ShowWindow=0;PROCESS_INFORMATION child;
            IntPtr size=IntPtr.Zero,jobList=IntPtr.Zero;bool initialized=false;
            InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
            try {
                if(size.ToInt64()<=0)throw new Win32Exception();
                startup.Attributes=Marshal.AllocHGlobal(size);jobList=Marshal.AllocHGlobal(IntPtr.Size);Marshal.WriteIntPtr(jobList,owned.job);
                if(!InitializeProcThreadAttributeList(startup.Attributes,1,0,ref size))throw new Win32Exception();initialized=true;
                if(!UpdateProcThreadAttribute(startup.Attributes,0,new IntPtr(0x0002000D),jobList,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero))throw new Win32Exception();
                if(!CreateProcess(executable,new StringBuilder("\""+executable+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,false,0x08080004,IntPtr.Zero,directory,ref startup,out child))throw new Win32Exception();
            }catch{owned.Dispose();throw;}
            finally{if(startup.Attributes!=IntPtr.Zero){if(initialized)DeleteProcThreadAttributeList(startup.Attributes);Marshal.FreeHGlobal(startup.Attributes);}if(jobList!=IntPtr.Zero)Marshal.FreeHGlobal(jobList);}
            owned.process=child.Process;owned.Id=child.ProcessId;owned.Exit=new ProcessWait(child.Process);
            try {
                owned.Created=Creation(child.Process);
                if(beforeResume!=null)beforeResume(owned);
                if(ResumeThread(child.Thread)==UInt32.MaxValue)throw new Win32Exception();return owned;
            }catch {
                bool confirmed=owned.StopAndConfirm(exitWait);if(!confirmed)while(!owned.Exit.WaitOne(1000)){};owned.Dispose();if(!confirmed)throw new UnconfirmedExit();throw;
            }finally{CloseHandle(child.Thread);}
        }
        internal bool Matches(string path,string hash){return !Exit.WaitOne(0)&&ProcessMatches(process,(int)Id,Created,path,hash);}
        internal bool StopAndConfirm(int milliseconds) {
            if(process==IntPtr.Zero)return true;
            return ConfirmStopped(Exit,milliseconds,delegate{return TerminateProcess(process,4);});
        }
        public void Dispose(){if(job!=IntPtr.Zero){CloseHandle(job);job=IntPtr.Zero;}if(Exit!=null){Exit.Dispose();Exit=null;}if(process!=IntPtr.Zero){CloseHandle(process);process=IntPtr.Zero;}}
    }
    private static bool VerifyProcess(int pid,long created,string path,string hash) {
        IntPtr process=OpenProcess(0x00101000,false,(uint)pid);if(process==IntPtr.Zero)return false;
        try{return WaitForSingleObject(process,0)==258&&ProcessMatches(process,pid,created,path,hash);}finally{CloseHandle(process);}
    }
    private static bool ProcessMatches(IntPtr process,int pid,long created,string path,string hash) {
        int size=32768;var image=new StringBuilder(size);
        return QueryFullProcessImageName(process,0,image,ref size)&&PeerMatches(pid,pid,Creation(process),created,image.ToString(),path,MsiSensorCommon.HashFile(path),hash);
    }
    private static long Creation(IntPtr process){long created,exit,kernel,user;if(!GetProcessTimes(process,out created,out exit,out kernel,out user))throw new Win32Exception();return DateTime.FromFileTimeUtc(created).Ticks;}
    [StructLayout(LayoutKind.Sequential)] private struct JOB_BASIC {internal long ProcessTime,JobTime;internal uint LimitFlags;internal UIntPtr MinWorkingSet,MaxWorkingSet;internal uint ActiveProcesses;internal UIntPtr Affinity;internal uint PriorityClass,SchedulingClass;}
    [StructLayout(LayoutKind.Sequential)] private struct IO_COUNTERS {internal ulong ReadOperations,WriteOperations,OtherOperations,ReadBytes,WriteBytes,OtherBytes;}
    [StructLayout(LayoutKind.Sequential)] private struct JOB_EXTENDED {internal JOB_BASIC Basic;internal IO_COUNTERS Io;internal UIntPtr ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory;}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] private struct STARTUPINFO {internal int Size;internal string Reserved,Desktop,Title;internal int X,Y,Width,Height,CharsX,CharsY,Fill,Flags;internal short ShowWindow,ReservedSize;internal IntPtr ReservedPointer,StdInput,StdOutput,StdError;}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] private struct STARTUPINFOEX {internal STARTUPINFO Info;internal IntPtr Attributes;}
    [StructLayout(LayoutKind.Sequential)] private struct PROCESS_INFORMATION {internal IntPtr Process,Thread;internal uint ProcessId,ThreadId;}
    private static IntPtr NewJob() {
        IntPtr job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)throw new Win32Exception();var limits=new JOB_EXTENDED();limits.Basic.LimitFlags=0x2000;
        int size=Marshal.SizeOf(typeof(JOB_EXTENDED));IntPtr data=Marshal.AllocHGlobal(size);
        try{Marshal.StructureToPtr(limits,data,false);if(!SetInformationJobObject(job,9,data,(uint)size))throw new Win32Exception();return job;}catch{CloseHandle(job);throw;}finally{Marshal.FreeHGlobal(data);}
    }
    [DllImport("kernel32.dll",SetLastError=true)] private static extern IntPtr CreateJobObject(IntPtr security,string name);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool SetInformationJobObject(IntPtr job,int info,IntPtr data,uint length);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
    [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern bool CreateProcess(string application,StringBuilder command,IntPtr processSecurity,IntPtr threadSecurity,bool inherit,uint flags,IntPtr environment,string directory,ref STARTUPINFOEX startup,out PROCESS_INFORMATION information);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process,out long created,out long exit,out long kernel,out long user);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr process,int flags,StringBuilder path,ref int size);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
}
