// Separate synthetic entry point. No driver, sensor, task or installed-file API.
using System;
using System.Diagnostics;
using System.Threading;

internal static class MsiCpuWatchdogFixture
{
    private static int checks;
    private static void Check(bool value){if(!value)throw new Exception("CPU watchdog fixture failed: "+checks);checks++;}
    private sealed class ConstantCpu:IMsiCpuMsr {
        public bool Read(uint register,out ulong value){value=register==MsiCpuOnly.Target?100UL<<16:0x80000000UL|(51UL<<16);return true;}
    }
    private static int Main(string[] args) {
        try {
            if(args.Length==1&&(args[0]=="--terminate-initialization"||args[0]=="--terminate-body")) {
                using(var watchdog=new MsiCpuWatchdog(200,10,Stopwatch.GetTimestamp,Stopwatch.Frequency,MsiCpuWatchdog.TerminateSelf)) {
                    if(args[0]=="--terminate-body"){watchdog.Heartbeat();Thread.Sleep(80);watchdog.Heartbeat();}
                    Thread.Sleep(Timeout.Infinite); // Owned fixture child, not a real kernel call.
                }
                return 9;
            }
            if(args.Length!=0)return 2;
            Check(MsiCpuWatchdog.DeadlineMilliseconds==30000);Check(MsiCpuWatchdog.ExitCode==4);
            Check(!MsiCpuWatchdog.Expired(100,129,30));Check(MsiCpuWatchdog.Expired(100,130,30));
            Check(MsiCpuWatchdog.Expired(100,131,30));Check(!MsiCpuWatchdog.Expired(100,99,30));
            Check(!MsiCpuWatchdog.Expired(129,130,30));

            int healthyTimeouts=0;
            var healthy=new MsiCpuWatchdog(150,5,Stopwatch.GetTimestamp,Stopwatch.Frequency,delegate{Interlocked.Increment(ref healthyTimeouts);});
            try {
                var reader=new ConstantCpu();
                for(int n=0;n<6;n++){Check(MsiCpuOnly.Sample(reader)==49);healthy.Heartbeat();Thread.Sleep(40);}
                Check(Interlocked.CompareExchange(ref healthyTimeouts,0,0)==0);
            }finally{healthy.Dispose();}
            Check(!healthy.IsAlive);

            long stamp=1000;int boundaryTimeouts=0;
            using(var fired=new ManualResetEvent(false)) {
                var boundary=new MsiCpuWatchdog(100,5,delegate{return Interlocked.Read(ref stamp);},1000,delegate{Interlocked.Increment(ref boundaryTimeouts);fired.Set();});
                try {
                    Interlocked.Exchange(ref stamp,1099);boundary.Heartbeat();Interlocked.Exchange(ref stamp,1100);
                    Check(!fired.WaitOne(30)); // Heartbeat just before the old deadline wins.
                    Interlocked.Exchange(ref stamp,1198);Check(!fired.WaitOne(30));
                    Interlocked.Exchange(ref stamp,1199);Check(fired.WaitOne(1000));
                    Check(Interlocked.CompareExchange(ref boundaryTimeouts,0,0)==1);
                }finally{boundary.Dispose();}
                Check(!boundary.IsAlive);
            }
            // Force an old completedAt snapshot, then finish a heartbeat before
            // the watcher claims expiry. It must reread the newer progress.
            long raceStamp=1000;int hold=1,raceTimeouts=0,mainThread=Thread.CurrentThread.ManagedThreadId;
            using(var entered=new ManualResetEvent(false))using(var release=new ManualResetEvent(false))using(var fired=new ManualResetEvent(false)) {
                var race=new MsiCpuWatchdog(100,5,delegate{
                    if(Thread.CurrentThread.ManagedThreadId!=mainThread&&Interlocked.Exchange(ref hold,0)==1){entered.Set();release.WaitOne(1000);}
                    return Interlocked.Read(ref raceStamp);
                },1000,delegate{Interlocked.Increment(ref raceTimeouts);fired.Set();});
                try {
                    Check(entered.WaitOne(1000));Interlocked.Exchange(ref raceStamp,1099);race.Heartbeat();Interlocked.Exchange(ref raceStamp,1100);release.Set();
                    Check(!fired.WaitOne(30));Interlocked.Exchange(ref raceStamp,1199);Check(fired.WaitOne(1000));
                    Check(Interlocked.CompareExchange(ref raceTimeouts,0,0)==1);
                }finally{release.Set();race.Dispose();}
                Check(!race.IsAlive);
            }
            // A heartbeat whose caller stops while obtaining its timestamp
            // cannot hold a lock/state that suppresses the independent watcher.
            long hungStamp=0;int heartbeatThread=0,hungTimeouts=0;
            using(var entered=new ManualResetEvent(false))using(var release=new ManualResetEvent(false))using(var fired=new ManualResetEvent(false)) {
                var hung=new MsiCpuWatchdog(100,5,delegate{
                    if(Thread.CurrentThread.ManagedThreadId==Interlocked.CompareExchange(ref heartbeatThread,0,0)){entered.Set();release.WaitOne();}
                    return Interlocked.Read(ref hungStamp);
                },1000,delegate{Interlocked.Increment(ref hungTimeouts);fired.Set();});
                var pulse=new Thread(new ThreadStart(delegate{Interlocked.Exchange(ref heartbeatThread,Thread.CurrentThread.ManagedThreadId);hung.Heartbeat();}));pulse.IsBackground=true;
                try {
                    pulse.Start();Check(entered.WaitOne(1000));Interlocked.Exchange(ref hungStamp,100);
                    Check(fired.WaitOne(1000)); // Main heartbeat remains blocked.
                    release.Set();Check(pulse.Join(1000));hung.Heartbeat();
                    Check(Interlocked.CompareExchange(ref hungTimeouts,0,0)==1);
                }finally{release.Set();if(pulse.IsAlive)pulse.Join(1000);hung.Dispose();}
                Check(!hung.IsAlive);
            }
            long disposeStamp=0;int disposedTimeouts=0;
            var disposed=new MsiCpuWatchdog(100,5,delegate{return Interlocked.Read(ref disposeStamp);},1000,delegate{Interlocked.Increment(ref disposedTimeouts);});
            disposed.Dispose();disposed.Dispose();Interlocked.Exchange(ref disposeStamp,1000);Thread.Sleep(30);
            Check(!disposed.IsAlive);Check(Interlocked.CompareExchange(ref disposedTimeouts,0,0)==0);
            Console.WriteLine("{\"status\":\"PASS\",\"checks\":"+checks+",\"hardwareStarted\":false}");return 0;
        }catch(Exception e){Console.Error.WriteLine(e.GetType().Name+": "+e.Message);return 3;}
    }
}
