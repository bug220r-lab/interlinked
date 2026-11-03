// Isolated synthetic sensor scheduling and matte-equivalence checks. No hardware.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class MasterOptimizationTests
    {
        private static readonly List<string> checks=new List<string>();
        private static void Check(bool ok,string message){if(!ok)throw new Exception(message);checks.Add(message);}
        private static bool Until(Func<bool> predicate,int timeout=1500){var watch=Stopwatch.StartNew();while(watch.ElapsedMilliseconds<timeout){if(predicate())return true;Thread.Sleep(1);}return predicate();}
        private static Dictionary<string,object> Reading(double value,DateTime when){return new Dictionary<string,object>{{"value",value},{"status","live"},{"observedAt",when.ToString("o")}};}
        private static Dictionary<string,object> Sample(){return new Dictionary<string,object>{{"cpu",Reading(68,DateTime.UtcNow.AddSeconds(-1))},{"gpu",Reading(72,DateTime.UtcNow.AddSeconds(-10))},{"status","partial"},{"observedAt",DateTime.UtcNow.ToString("o")}};}

        private static void SensorChecks()
        {
            int reads=0;
            using(var entered=new ManualResetEvent(false))using(var release=new ManualResetEvent(false))
            {
                var sampler=new SensorSampler(delegate(Func<bool> cancelled){int count=Interlocked.Increment(ref reads);if(count==2){entered.Set();release.WaitOne();}return Sample();},10);
                try{
                    sampler.Start();sampler.Start();
                    Check(Until(delegate{return sampler.Latest()!=null;}),"Initial synthetic sample is published");
                    var initial=sampler.Latest();
                    Check(entered.WaitOne(1500),"A subsequent synthetic sensor acquisition can stall");
                    var watch=Stopwatch.StartNew();for(int i=0;i<10000;i++)sampler.Latest();
                    Check(watch.ElapsedMilliseconds<500,"Latest-sample reads remain independent of a blocked producer");
                    using(var worker=new Worker()){
                        var state=new Dictionary<string,object>{{"utc",DateTime.UtcNow.ToString("o")},{"temperatures",initial}};
                        var config=new Dictionary<string,object>{{"captureEnabled",false}};
                        for(int i=0;i<6;i++)using(var frame=worker.DrawFrame(state,config,i/30.0))Check(frame.Width==480&&frame.Height==480,"Native frame drawing proceeds while sensor acquisition is blocked: "+i);
                    }
                    Check(reads==2,"Repeated starts and frame reads do not queue additional sensor acquisitions");
                    Check(Worker.TemperatureText(Values.Obj(initial["cpu"]))=="68°C","Fresh per-field CPU timestamp is retained");
                    Check(Worker.TemperatureText(Values.Obj(initial["gpu"]))=="—","Old GPU field stays unavailable despite the fresh sample envelope");
                    string observed=Values.Text(Values.Obj(initial["cpu"]),"observedAt");
                    Check(Values.Text(Values.Obj(sampler.Latest()["cpu"]),"observedAt")==observed,"Reading cached samples does not renew their timestamps");
                    watch.Restart();bool stopped=sampler.Stop(50);
                    Check(!stopped&&watch.ElapsedMilliseconds<750,"An uncooperative synthetic read cannot extend the bounded stop wait");
                    release.Set();Check(sampler.Stop(1500),"Producer exits after its blocked acquisition is released");
                    Check(object.ReferenceEquals(initial,sampler.Latest()),"A result completing after cancellation is discarded");
                    sampler.Start();Check(reads==2,"A stopped sampler cannot restart or dispatch another read");
                }finally{release.Set();sampler.Stop(1500);}
            }
            int waitingReads=0;
            var waiting=new SensorSampler(delegate(Func<bool> cancelled){Interlocked.Increment(ref waitingReads);return Sample();},60000);
            try{
                waiting.Start();Check(Until(delegate{return waiting.Latest()!=null;}),"Long-interval fixture publishes once before waiting");
                var watch=Stopwatch.StartNew();Check(waiting.Stop(1000)&&watch.ElapsedMilliseconds<500,"Stop wakes the interval wait without waiting for the next poll");
                Check(waitingReads==1,"Stopping an idle sampler does not invoke an extra read");
            }finally{waiting.Stop(1000);}
            int cancelledReads=0;var neverStarted=new SensorSampler(delegate(Func<bool> cancelled){Interlocked.Increment(ref cancelledReads);return Sample();});
            Check(neverStarted.Stop(1000),"Stopping before start completes immediately");neverStarted.Start();Check(cancelledReads==0,"Cancellation before start prevents producer creation");
            int attempts=0;var recovering=new SensorSampler(delegate(Func<bool> cancelled){if(Interlocked.Increment(ref attempts)==1)throw new IOException("synthetic sensor failure");return Sample();},5);
            try{recovering.Start();Check(Until(delegate{return recovering.Latest()!=null;}),"A failed acquisition does not terminate the sampler");Check(attempts>=2,"Sampler recovers through a later bounded acquisition");}finally{recovering.Stop(1000);}
            using(var entered=new ManualResetEvent(false)){
                var cooperative=new SensorSampler(delegate(Func<bool> cancelled){entered.Set();while(!cancelled())Thread.Sleep(1);return Sample();});
                try{cooperative.Start();Check(entered.WaitOne(1000),"Cooperative cancellation fixture starts");Check(cooperative.Stop(1000),"Producer observes shutdown cancellation");Check(cooperative.Latest()==null,"Cancellation cannot publish an artificially fresh final reading");}finally{cooperative.Stop(1000);}
            }
            using(var entered=new ManualResetEvent(false)){
                var owned=new SensorSampler(delegate(Func<bool> cancelled){entered.Set();while(!cancelled())Thread.Sleep(1);return Sample();});
                using(var worker=new Worker()){
                    typeof(Worker).GetField("sensorSampler",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(worker,owned);
                    owned.Start();Check(entered.WaitOne(1000),"Worker-owned synthetic sampler starts without invoking Worker.Run");
                }
                Check(owned.Stopping()&&owned.Stop(0),"Worker disposal cancels and joins its cooperative sampler");
                Check(owned.Latest()==null,"Worker shutdown discards an acquisition completing during disposal");
            }
        }

        private static Bitmap OriginalMatte(Bitmap source){var matte=new Bitmap(192,208,PixelFormat.Format32bppArgb);for(int y=0;y<208;y++)for(int x=0;x<192;x++){Color c=source.GetPixel(x,y);matte.SetPixel(x,y,c.A>=128?Color.FromArgb(255,0,0,0):Color.Transparent);}return matte;}
        private static void Fill(Bitmap bitmap){for(int y=0;y<208;y++)for(int x=0;x<192;x++)bitmap.SetPixel(x,y,Color.FromArgb((x*17+y*31)%256,(x*5)%256,(y*7)%256,(x+y)%256));}
        private static void EqualMatte(Bitmap source,string label){using(var original=OriginalMatte(source))using(var bulk=VioletAnimationAssets.BuildMatte(source)){
            for(int y=0;y<208;y++)for(int x=0;x<192;x++)if(original.GetPixel(x,y).ToArgb()!=bulk.GetPixel(x,y).ToArgb())throw new Exception("Matte ARGB mismatch: "+label+" at "+x+","+y);
            Check(true,"Every synthetic matte ARGB pixel matches the original loop: "+label);
        }}
        private static Dictionary<string,object> MatteChecks()
        {
            foreach(var format in new[]{PixelFormat.Format32bppArgb,PixelFormat.Format32bppPArgb,PixelFormat.Format24bppRgb})using(var source=new Bitmap(192,208,format)){Fill(source);EqualMatte(source,format.ToString());}
            const int stride=192*4+16;IntPtr memory=Marshal.AllocHGlobal(stride*208);
            try{
                var bytes=new byte[stride*208];for(int y=0;y<208;y++)for(int x=0;x<192;x++){int i=y*stride+x*4;bytes[i]=(byte)(x+y);bytes[i+1]=(byte)(y*7);bytes[i+2]=(byte)(x*5);bytes[i+3]=(byte)((x*17+y*31)%256);}Marshal.Copy(bytes,0,memory,bytes.Length);
                using(var negative=new Bitmap(192,208,-stride,PixelFormat.Format32bppArgb,IntPtr.Add(memory,stride*207)))EqualMatte(negative,"padded negative stride");
            }finally{Marshal.FreeHGlobal(memory);}
            using(var source=new Bitmap(192,208,PixelFormat.Format32bppArgb)){
                source.SetPixel(0,0,Color.FromArgb(127,45,67,89));source.SetPixel(1,0,Color.FromArgb(128,45,67,89));
                using(var matte=VioletAnimationAssets.BuildMatte(source)){Check(matte.GetPixel(0,0).ToArgb()==Color.Transparent.ToArgb(),"Alpha 127 retains transparent-white ARGB semantics");Check(matte.GetPixel(1,0).ToArgb()==Color.Black.ToArgb(),"Alpha 128 becomes opaque black");}
                Check(source.GetPixel(0,0).A==127&&source.GetPixel(1,0).A==128,"Bulk matte construction does not mutate its source");
                // Successful relocking also detects a leaked LockBits lease.
                var locked=source.LockBits(new Rectangle(0,0,192,208),ImageLockMode.ReadOnly,PixelFormat.Format32bppArgb);source.UnlockBits(locked);Check(true,"Source pixels are unlocked after successful construction");
            }
            using(var small=new Bitmap(12,12)){
                bool refused=false;try{using(var matte=VioletAnimationAssets.BuildMatte(small)){}}catch(ArgumentException){refused=true;}
                Check(refused,"An undersized source is rejected");var locked=small.LockBits(new Rectangle(0,0,12,12),ImageLockMode.ReadOnly,PixelFormat.Format32bppArgb);small.UnlockBits(locked);Check(true,"A rejected source remains usable and unlocked");
            }
            using(var source=new Bitmap(192,208,PixelFormat.Format32bppArgb)){
                Fill(source);var original=new double[3];var bulk=new double[3];
                for(int run=0;run<3;run++){
                    var watch=Stopwatch.StartNew();for(int key=0;key<86;key++)using(var matte=OriginalMatte(source)){}original[run]=watch.Elapsed.TotalMilliseconds;
                    watch.Restart();for(int key=0;key<86;key++)using(var matte=VioletAnimationAssets.BuildMatte(source)){}bulk[run]=watch.Elapsed.TotalMilliseconds;
                }
                return new Dictionary<string,object>{{"copiesPerRun",86},{"originalMs",original},{"bulkMs",bulk},{"scope","Synthetic matte stage only; not full asset load or application startup"}};
            }
        }

        [STAThread]private static int Main(string[] args)
        {
            if(args.Length!=1)return 2;string output=Path.GetFullPath(args[0]);if(Directory.Exists(output))throw new IOException("Use absent fixture output");Directory.CreateDirectory(output);
            bool ok=false;string error=null;Dictionary<string,object> timing=null;
            try{SensorChecks();timing=MatteChecks();ok=true;}catch(Exception ex){error=ex.ToString();}
            var report=new Dictionary<string,object>{{"format","interlinked-master-optimization-checks-v1"},{"ok",ok},{"checks",checks},{"checkCount",checks.Count},{"error",error},{"timing",timing},{"hardwareTested",false},{"workerRunInvoked",false},{"realArtworkUsed",false},{"sensorOrCaptureMethodsInvoked",false}};
            string encoded=new JavaScriptSerializer().Serialize(report);File.WriteAllText(Path.Combine(output,"report.json"),encoded,new UTF8Encoding(false));Console.WriteLine(encoded);return ok?0:1;
        }
    }
}
