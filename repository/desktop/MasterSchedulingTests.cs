// Synthetic scheduling/publication fixture. Never calls Worker.Run, USB,
// capture, sensor readers, or permission/broker setup.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class MasterSchedulingTests
    {
        private static readonly List<string> Checks=new List<string>();
        private const BindingFlags Hidden=BindingFlags.Instance|BindingFlags.NonPublic;
        private static void Check(bool value,string message)
        {
            if(!value)throw new InvalidOperationException(message);
            Checks.Add(message);
        }
        private static object Field(Worker worker,string name)
        {
            var field=typeof(Worker).GetField(name,Hidden);
            if(field==null)throw new MissingFieldException(typeof(Worker).FullName,name);
            return field.GetValue(worker);
        }
        private static object Call(Worker worker,string name,params object[] args)
        {
            var method=typeof(Worker).GetMethod(name,Hidden);
            if(method==null)throw new MissingMethodException(typeof(Worker).FullName,name);
            return method.Invoke(worker,args);
        }
        private static Dictionary<string,object> Config(int fps,bool diagnostic=false)
        {
            return new Dictionary<string,object>{{"fps",fps},{"diagnostic",diagnostic}};
        }
        private static long Sequence(Worker worker)
        {
            lock(Field(worker,"gate"))return (long)Field(worker,"sequence");
        }
        private static double Now(Worker worker) {return ((Stopwatch)Field(worker,"clock")).Elapsed.TotalSeconds;}

        private static void DeadlineChecks()
        {
            var pacer=new FramePacer(1000);
            Check(pacer.Next(1000,1003,10)==1100,"First deadline uses frame start rather than completed work");
            Check(pacer.Next(1108,1111,10)==1200,"Late wake does not accumulate into the next deadline");
            Check(pacer.Next(1211,1214,5)==1411,"FPS change resets to the new frame period");
            long resumed=pacer.Next(2000,2010,5);
            Check(resumed>2010&&resumed<=2210,"Long stall selects a future slot within one period");
            long next=pacer.Next(resumed,resumed+3,5);
            Check(next>resumed+3,"A skipped interval cannot produce historical catch-up deadlines");

            var fractional=new FramePacer(1000);
            long deadline=0;
            for(int frame=0;frame<300;frame++)
            {
                long start=(long)Math.Floor(frame*1000.0/30);
                deadline=fractional.Next(start,start+1,30);
            }
            Check(Math.Abs(deadline-10000)<=1,"Thirty FPS retains its fractional period over three hundred frames");
            var overrun=new FramePacer(10000000);
            overrun.Next(0,10,30);
            long future=overrun.Next(90000000,90000000,30);
            Check(future>90000000&&future<=90333334,"Multiple missed slots are skipped without a backlog");
            var changed=new FramePacer(10000000);
            changed.Next(0,100,30);
            long slow=changed.Next(400000,400010,5);
            Check(slow==2400000,"Switching from thirty to five FPS abandons the old fast deadline");

            Check(Worker.FrameRate(new Dictionary<string,object>())==30,"Worker default is the reviewed thirty FPS target");
            foreach(int fps in new[]{5,10,15,30})
                Check(Worker.FrameRate(Config(fps))==fps,"Normal supported frame rate: "+fps);
            Check(Worker.FrameRate(Config(20))==30,"Diagnostic twenty FPS is not a normal setting");
            Check(Worker.FrameRate(Config(20,true))==20,"Reviewed diagnostic twenty FPS remains available");
            foreach(int fps in new[]{0,-1,45,60,100})
                Check(Worker.FrameRate(Config(fps,true))==30,"Unsupported diagnostic frame rate falls back: "+fps);
        }

        private static void HealthChecks()
        {
            Check(!Worker.FrameStale(13,10),"Exactly three seconds is still the bounded grace interval");
            Check(Worker.FrameStale(13.001,10),"Renderer inactivity beyond three seconds stops freshness");
            Check(!Worker.RepeatDue(10.999,10),"A cached picture is not repeated faster than once per second");
            Check(Worker.RepeatDue(11,10),"A cached picture becomes eligible at the keepalive deadline");
            Check(Worker.UsbFrameWait(10,10,10,11,true)==250,"Quiet sender uses a bounded wait rather than polling");
            Check(Worker.UsbFrameWait(10.99,10,10,11,true)<=11,"Wait respects an imminent cached-picture repeat");
            Check(Worker.UsbFrameWait(12.99,12.5,10,14,true)<=11,"Wait respects renderer-health expiry");
            Check(Worker.UsbFrameWait(10,10,10,10.007,true)<=8,"Wait respects the conflict-check deadline");
            Check(Worker.UsbFrameWait(13,12.5,10,14,true)>=1,"Deadline boundary does not request a zero-time spin");
            Check(Worker.UsbFrameWait(10,0,10,11,false)==250,"Never-produced frame wait is also bounded");
        }

        private static void PublicationChecks()
        {
            using(var worker=new Worker())
            {
                var preview=(AutoResetEvent)Field(worker,"frameReady");
                var usb=(AutoResetEvent)Field(worker,"usbFrameReady");
                Check(!object.ReferenceEquals(preview,usb),"Preview and USB have independent notifications");
                byte[] original={255,216,1,2,255,217};
                byte[] newest=null;
                for(int i=0;i<400;i++)
                {
                    newest=new byte[]{255,216,(byte)i,(byte)(i>>8),255,217};
                    Call(worker,"PublishFrame",newest);
                }
                lock(Field(worker,"gate"))
                {
                    Check(Sequence(worker)==400,"Every successful publication advances the producer sequence");
                    Check(object.ReferenceEquals(Field(worker,"latest"),newest),"Latest-frame publication retains only the newest owned buffer");
                }
                Check(usb.WaitOne(0),"USB observes coalesced producer notification");
                Check(!usb.WaitOne(0),"Four hundred publications do not queue four hundred USB wakeups");
                Check(preview.WaitOne(0),"Consuming USB notification does not consume preview notification");
                Check(!preview.WaitOne(0),"Preview notification also coalesces");

                Call(worker,"PublishFrame",original);usb.Reset();preview.Reset();
                long observed=Sequence(worker);
                // Reproduce the predicate-before-wait race: publication occurs
                // after a consumer saw its old sequence but before entering wait.
                byte[] afterPredicate={255,216,9,9,255,217};
                Call(worker,"PublishFrame",afterPredicate);
                double now=Now(worker);
                var watch=Stopwatch.StartNew();
                Call(worker,"WaitForUsbFrame",now,now,now+1,true);
                Check(watch.ElapsedMilliseconds<200,"Publication before wait is retained and wakes without a polling timeout");
                Check(Sequence(worker)==observed+1,"Predicate-to-wait boundary observes the newly published sequence");
                Check(!usb.WaitOne(0),"Actual sender wait consumed its stored USB notification");
                Check(original[2]==1&&original[3]==2,"A newer publication leaves an older selected frame unchanged");

                usb.Reset();preview.Reset();
                Exception waiterError=null;
                using(var started=new ManualResetEvent(false))
                {
                    var waiter=new Thread(delegate(){try{started.Set();double waitNow=Now(worker);Call(worker,"WaitForUsbFrame",waitNow,waitNow,waitNow+1,true);}catch(Exception ex){waiterError=ex;}}){IsBackground=true};
                    waiter.Start();Check(started.WaitOne(1000),"USB waiter started");
                    Thread.Sleep(20);Call(worker,"PublishFrame",new byte[]{255,216,7,7,255,217});
                    Check(waiter.Join(1000),"Publication releases an already waiting sender within a bounded interval");
                    Check(waiterError==null,"Concurrent sender notification completes without an exception");
                }

                long before=Sequence(worker);
                for(int i=0;i<3;i++)Call(worker,"PublishFrame",new byte[]{255,216,5,5,255,217});
                Check(Sequence(worker)==before+3,"Healthy static pixels still publish fresh sequences");
                Check(usb.WaitOne(0),"Healthy unchanged content still notifies the sender");
            }
        }

        private static void WakeChecks(string input,string label)
        {
            using(var worker=new Worker())
            using(var ready=new ManualResetEvent(false))
            {
                var usb=(AutoResetEvent)Field(worker,"usbFrameReady");usb.Reset();
                bool signalled=false;Exception waiterError=null;
                var waiter=new Thread(delegate(){try{ready.Set();signalled=usb.WaitOne(2000);}catch(Exception ex){waiterError=ex;}}){IsBackground=true};
                waiter.Start();Check(ready.WaitOne(1000),label+" waiter started");
                TextReader original=Console.In;
                try{Console.SetIn(new StringReader(input));Call(worker,"ReadInput");}
                finally{Console.SetIn(original);}
                Check(waiter.Join(1000),label+" wakes the dedicated sender within a bounded interval");
                Check(signalled&&waiterError==null,label+" signals without opening USB");
            }
        }

        private sealed class HeldInput : TextReader
        {
            private readonly string command;
            private readonly ManualResetEvent finish=new ManualResetEvent(false);
            internal readonly ManualResetEvent Waiting=new ManualResetEvent(false);
            private int reads;
            internal HeldInput(string command){this.command=command;}
            internal int Reads {get{return Interlocked.CompareExchange(ref reads,0,0);}}
            public override string ReadLine()
            {
                if(Interlocked.Increment(ref reads)==1)return command;
                Waiting.Set();finish.WaitOne(3000);return null;
            }
            internal void Release(){finish.Set();}
            protected override void Dispose(bool disposing)
            {
                if(disposing){finish.Set();Waiting.Dispose();finish.Dispose();}
                base.Dispose(disposing);
            }
        }

        private static void HeldCommandChecks(string command,bool exits,string label)
        {
            using(var worker=new Worker())
            using(var input=new HeldInput(command))
            {
                var usb=(AutoResetEvent)Field(worker,"usbFrameReady");usb.Reset();
                Exception inputError=null;
                var reader=new Thread(delegate(){try{Call(worker,"ReadInput");}catch(Exception ex){inputError=ex;}}){IsBackground=true};
                TextReader original=Console.In;
                try
                {
                    Console.SetIn(input);reader.Start();
                    if(exits)
                    {
                        Check(reader.Join(1000),label+" exits while its input remains open");
                        Check(input.Reads==1,label+" does not require EOF or a second input read");
                        Check(usb.WaitOne(0),label+" wakes the sender independently of EOF");
                    }
                    else
                    {
                        Check(input.Waiting.WaitOne(1000),label+" keeps the private input open for further commands");
                        Check(!reader.Join(0)&&(bool)Field(worker,"running"),label+" preserves worker monitoring before EOF");
                        Check(usb.WaitOne(500),label+" wakes the sender before input closes");
                    }
                    Check(inputError==null,label+" input processing has no exception");
                }
                finally{input.Release();reader.Join(1000);Console.SetIn(original);}
            }
        }

        [STAThread]private static int Main(string[] args)
        {
            if(args.Length!=1)return 2;
            string output=Path.GetFullPath(args[0]);Directory.CreateDirectory(output);
            bool ok=false;string error=null;
            try
            {
                DeadlineChecks();HealthChecks();PublicationChecks();
                WakeChecks("","EOF");
                HeldCommandChecks("{\"op\":\"display\",\"connect\":false}",false,"Explicit disconnect");
                HeldCommandChecks("{\"op\":\"quit\"}",true,"Explicit quit");
                ok=true;
            }
            catch(Exception ex){error=ex.GetType().Name+": "+ex.Message;}
            var report=new Dictionary<string,object>{{"format","interlinked-master-scheduling-checks-v1"},{"ok",ok},
                {"checks",Checks},{"checkCount",Checks.Count},{"error",error},{"hardwareTested",false},
                {"workerRunInvoked",false},{"sensorOrCaptureMethodsInvoked",false},
                {"scope","Synthetic production pacing/policy/publication methods and input EOF only; no helper, map or elevated process is created."}};
            string encoded=new JavaScriptSerializer().Serialize(report);
            File.WriteAllText(Path.Combine(output,"report.json"),encoded,new UTF8Encoding(false));
            Console.WriteLine(encoded);return ok?0:1;
        }
    }
}
