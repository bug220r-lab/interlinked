// MASTER's isolated renderer and picture-only vendor transport. No project operations.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class Values
    {
        internal static Dictionary<string,object> Obj(object value) { return value as Dictionary<string,object> ?? new Dictionary<string,object>(); }
        internal static object Get(Dictionary<string,object> d,string key) { object v;return d.TryGetValue(key,out v)?v:null; }
        internal static string Text(Dictionary<string,object> d,string key,string fallback="") { var v=Get(d,key);return v==null?fallback:Convert.ToString(v,CultureInfo.InvariantCulture); }
        internal static bool Bool(Dictionary<string,object> d,string key,bool fallback=false) { var v=Get(d,key);return v is bool?(bool)v:fallback; }
        internal static int Int(Dictionary<string,object> d,string key,int fallback=0) { try {var v=Get(d,key);return v==null?fallback:Convert.ToInt32(v,CultureInfo.InvariantCulture);}catch{return fallback;} }
        internal static double? Number(Dictionary<string,object> d,string key) { try {var v=Get(d,key);return v==null?(double?)null:Convert.ToDouble(v,CultureInfo.InvariantCulture);}catch{return null;} }
        internal static List<Dictionary<string,object>> Rows(object source) { var result=new List<Dictionary<string,object>>();var list=source as IEnumerable;if(list!=null)foreach(var v in list){var row=v as Dictionary<string,object>;if(row!=null)result.Add(row);}return result; }
    }

    internal sealed class FramePacer
    {
        private readonly long frequency;
        private double deadline;
        private int previousFps;
        internal FramePacer(long frequency){if(frequency<=0)throw new ArgumentOutOfRangeException("frequency");this.frequency=frequency;}
        internal long Next(long frameStart,long now,int fps)
        {
            if(fps<=0)throw new ArgumentOutOfRangeException("fps");
            double period=frequency/(double)fps;
            if(previousFps!=fps||deadline==0){deadline=frameStart+period;previousFps=fps;}else deadline+=period;
            if(now>=deadline)deadline+=(Math.Floor((now-deadline)/period)+1)*period;
            return (long)Math.Ceiling(deadline);
        }
    }

    // One producer and one latest immutable result. A slow sensor read never
    // holds the publication lock, queues another read, or blocks frame drawing.
    internal sealed class SensorSampler
    {
        private readonly object gate=new object();
        private readonly Func<Func<bool>,Dictionary<string,object>> sample;
        private readonly int interval;
        private Thread thread;
        private bool stopping,started;
        private Dictionary<string,object> latest;
        internal SensorSampler(Func<Func<bool>,Dictionary<string,object>> sample,int interval=1000)
        {
            if(sample==null)throw new ArgumentNullException("sample");
            if(interval<1)throw new ArgumentOutOfRangeException("interval");
            this.sample=sample;this.interval=interval;
        }
        internal bool Stopping(){lock(gate)return stopping;}
        internal void Start()
        {
            lock(gate){if(started||stopping)return;started=true;thread=new Thread(Loop){IsBackground=true,Name="MASTER sensor samples"};thread.Start();}
        }
        // The producer relinquishes ownership of a completed dictionary;
        // consumers only read it, including its original per-field timestamps.
        internal Dictionary<string,object> Latest(){lock(gate)return latest;}
        internal bool Stop(int timeout)
        {
            Thread current;lock(gate){stopping=true;Monitor.PulseAll(gate);current=thread;}
            return current==null||current==Thread.CurrentThread||current.Join(timeout);
        }
        private void Loop()
        {
            while(!Stopping()){
                Dictionary<string,object> next=null;
                try{next=sample(Stopping);}catch{ /* Existing readings retain their expiry if a producer fails. */ }
                lock(gate){
                    if(stopping)return;
                    if(next!=null)latest=next;
                    Monitor.Wait(gate,interval);
                }
            }
        }
    }

    internal sealed class Worker : IDisposable
    {
        private const int Slot=691200, MaxJpeg=691136;
        private readonly object gate=new object(), logGate=new object(), jsonGate=new object(), displayGate=new object(), usbCleanupGate=new object();
        private readonly JavaScriptSerializer json=new JavaScriptSerializer {MaxJsonLength=262144};
        private Dictionary<string,object> snapshot=new Dictionary<string,object>(), settings=new Dictionary<string,object>();
        private volatile bool running=true, connectRequested=false, connected=false;
        private long requestId, activeRequestId=-1, attemptAcknowledged;private double lastTransportHeartbeat;
        private sealed class TransportFailure : IOException
        {
            internal readonly string Code;internal readonly bool Retryable;
            internal TransportFailure(string code,string message,bool retryable=false):base(message){Code=code;Retryable=retryable;}
        }
        private byte[] latest;private long sequence, delivered, dropped, acknowledged, encodedBytes;
        private double encodeMs,ackMs;private Image violetAtlas;private string atlasPath="";
        private readonly VioletAnimationPlayer violetAnimations=new VioletAnimationPlayer();
        private bool animationFallbackReported;
        private MemoryMappedFile map;private MemoryMappedViewAccessor view;private Process helper;
        private Thread usbThread;private IntPtr helperJob=IntPtr.Zero;
        private Process ownedSensor;private IntPtr sensorJob=IntPtr.Zero;
        private MemoryMappedFile ownedSensorMap;private DateTime ownedSensorStarted=DateTime.MinValue;
        private readonly object sensorGate=new object();private volatile bool sensorElevationRequested=false,sensorBrokerActive=false;
        private Thread sensorBrokerThread;private NamedPipeServerStream sensorPipe;
        private readonly byte[] sensorFirst=new byte[1048576],sensorSecond=new byte[1048576];
        private string sensorHash="";private DateTime sensorChanged=DateTime.MinValue;
        private SensorSampler sensorSampler;
        private readonly JavaScriptSerializer sensorJson=new JavaScriptSerializer {MaxJsonLength=262144};
        private string displayStatus="disconnected", captureStatus="";
        private readonly Stopwatch clock=Stopwatch.StartNew();
        private readonly Stream output=Console.OpenStandardOutput();
        private readonly AutoResetEvent frameReady=new AutoResetEvent(false);
        private readonly AutoResetEvent usbFrameReady=new AutoResetEvent(false);
        private readonly ImageCodecInfo jpegCodec=Array.Find(ImageCodecInfo.GetImageEncoders(),x=>x.MimeType=="image/jpeg");
        private static readonly Color Purple=Color.FromArgb(192,58,255), Lilac=Color.FromArgb(206,87,255), White=Color.FromArgb(245,240,255), Dim=Color.FromArgb(162,154,174);
        private static readonly Color Green=Color.FromArgb(82,255,183), Amber=Color.FromArgb(255,193,75), Red=Color.FromArgb(255,70,111);
        private static readonly Color ActivityHeading=Color.FromArgb(227,115,255), Alert=Color.FromArgb(255,204,99), RoomText=Color.FromArgb(250,245,255), SummaryText=Color.FromArgb(233,222,239), TemperatureTextColor=Color.FromArgb(240,232,250);
        private static readonly Color BinaryHead=Color.FromArgb(255,244,255), WireParticle=Color.FromArgb(255,243,255), AgentParticle=Color.FromArgb(252,236,255);
        private readonly Font title=new Font("Consolas",17,FontStyle.Bold,GraphicsUnit.Pixel), roomFont=new Font("Segoe UI",14,FontStyle.Bold,GraphicsUnit.Pixel), mono=new Font("Consolas",12,FontStyle.Regular,GraphicsUnit.Pixel), small=new Font("Segoe UI",11,FontStyle.Regular,GraphicsUnit.Pixel), detail=new Font("Segoe UI",16,FontStyle.Bold,GraphicsUnit.Pixel);
        private readonly Font binaryFont=new Font("Consolas",10,FontStyle.Regular,GraphicsUnit.Pixel), temperatureLabel=new Font("Segoe UI",22,FontStyle.Bold,GraphicsUnit.Pixel), temperatureValue=new Font("Segoe UI",25,FontStyle.Bold,GraphicsUnit.Pixel);

        private void Emit(string type,params object[] fields)
        {
            var item=new Dictionary<string,object>();item["type"]=type;item["utc"]=DateTime.UtcNow.ToString("o");
            for(int i=0;i+1<fields.Length;i+=2)item[Convert.ToString(fields[i])]=fields[i+1];
            string encoded;lock(jsonGate)encoded=json.Serialize(item);lock(logGate){Console.Error.WriteLine(encoded);Console.Error.Flush();}
        }
        private object Decode(string value){lock(jsonGate)return json.DeserializeObject(value);}
        private static string[] ExceptionMethods(Exception ex){var result=new List<string>();var frames=new StackTrace(ex,false).GetFrames();if(frames!=null)foreach(var frame in frames){var method=frame.GetMethod();if(method!=null)result.Add((method.DeclaringType==null?"":method.DeclaringType.FullName+".")+method.Name);if(result.Count==8)break;}return result.ToArray();}
        private static string CommandHint(string line){int key=line.IndexOf("\"op\"",StringComparison.Ordinal);if(key<0)return "unknown";int colon=line.IndexOf(':',key+4);if(colon<0)return "unknown";int start=colon+1;while(start<line.Length&&char.IsWhiteSpace(line[start]))start++;if(start>=line.Length||line[start]!='\"')return "unknown";int end=line.IndexOf('\"',start+1);if(end<0||end-start>18)return "unknown";string hint=line.Substring(start+1,end-start-1);foreach(string op in new[]{"snapshot","settings","display","sensors","capture_sources","quit"})if(hint==op)return op;return "unknown";}
        private void Status(string status,string error=null,long forRequest=-1,string errorCode=null,bool retryable=false,bool cleanupComplete=true)
        {
            displayStatus=status;Emit("status","requestId",forRequest<0?Interlocked.Read(ref requestId):forRequest,
                "connected",connected&&(status=="connected"||status=="render_stale"),"displayStatus",status,
                "error",error,"errorCode",errorCode,"retryable",retryable,"cleanupComplete",cleanupComplete);
        }
        private bool CurrentRequest(long id){lock(displayGate)return running&&connectRequested&&requestId==id;}
        private bool StopRequest(long id){lock(displayGate){if(requestId!=id)return false;connectRequested=false;return true;}}
        private void CheckRequest(long id){if(!CurrentRequest(id))throw new OperationCanceledException("Display request was cancelled.");}
        private void TransportHeartbeat(long id,string phase)
        {
            double now=clock.Elapsed.TotalSeconds;if(now-lastTransportHeartbeat<.5)return;lastTransportHeartbeat=now;
            Emit("transport","requestId",id,"phase",phase);
        }
        private Dictionary<string,object> Settings() {lock(gate)return new Dictionary<string,object>(settings);}
        private void ReadInput()
        {
            try {
                string line;
                while(running&&(line=Console.ReadLine())!=null){
                    if(line.Length>262144){Emit("status","error","Input exceeds bounds");continue;}
                    try {
                        var command=Values.Obj(Decode(line));string op=Values.Text(command,"op");
                        if(op=="snapshot"){lock(gate)snapshot=Values.Obj(Values.Get(command,"snapshot"));}
                        else if(op=="settings"){lock(gate)settings=Values.Obj(Values.Get(command,"settings"));}
                        else if(op=="display"){
                            long id;object raw=Values.Get(command,"requestId");
                            if(raw!=null&&(!(raw is int||raw is long)||Convert.ToInt64(raw,CultureInfo.InvariantCulture)<1))throw new InvalidDataException("Invalid display request identity.");
                            bool requested=Values.Bool(command,"connect");
                            lock(displayGate){id=raw==null?requestId+1:Convert.ToInt64(raw,CultureInfo.InvariantCulture);if(id<=requestId)continue;requestId=id;connectRequested=requested;}
                            if(!requested)Status("disconnecting",null,id);usbFrameReady.Set();
                        }
                        else if(op=="capture_sources")CaptureSources();
                        else if(op=="sensors")RequestSensorPermission(Values.Bool(command,"enable"));
                        else if(op=="quit"){running=false;break;}
                        else Emit("status","error","Unknown worker command");
                    }catch(Exception ex){Emit("status","error","Worker command failed; monitoring remains active.","command",CommandHint(line),"inputLength",line.Length,"exceptionType",ex.GetType().FullName,"methods",ExceptionMethods(ex));}
                }
            } catch(Exception ex){Emit("status","error","Worker input ended unexpectedly","exceptionType",ex.GetType().FullName,"methods",ExceptionMethods(ex));}
            finally{running=false;frameReady.Set();usbFrameReady.Set();}
        }
        private void WriteFrames()
        {
            long last=0;
            try {
                while(running){frameReady.WaitOne(1000);byte[] frame;long number;lock(gate){frame=latest;number=sequence;}if(frame==null||number==last)continue;
                    byte[] length=BitConverter.GetBytes(frame.Length);output.Write(length,0,4);output.Write(frame,0,frame.Length);output.Flush();last=number;
                }
            }catch(IOException){running=false;usbFrameReady.Set();}catch(ObjectDisposedException){running=false;usbFrameReady.Set();}
        }
        private void PublishFrame(byte[] frame)
        {
            lock(gate){latest=frame;sequence++;encodedBytes=frame.Length;}
            frameReady.Set();usbFrameReady.Set();
        }
        internal static int FrameRate(Dictionary<string,object> config)
        {
            int fps=Values.Int(config,"fps",30);
            return fps==5||fps==10||fps==15||fps==30||(Values.Bool(config,"diagnostic")&&fps==20)?fps:30;
        }
        internal int Run()
        {
            new Thread(ReadInput){IsBackground=true,Name="MASTER input"}.Start();
            new Thread(WriteFrames){IsBackground=true,Name="MASTER preview pipe"}.Start();
            usbThread=new Thread(UsbLoop){IsBackground=true,Name="MASTER LCD"};usbThread.Start();
            sensorSampler=new SensorSampler(ReadSensors);sensorSampler.Start();
            Emit("status","requestId",0,"connected",false,"displayStatus","disconnected","protocolVersion",2,"cleanupComplete",true);
            double nextReport=0;long previousSequence=0;double previousTime=0;var pacer=new FramePacer(Stopwatch.Frequency);
            Dictionary<string,object> reportedSensors=null;
            while(running){
                long frameStart=Stopwatch.GetTimestamp();var watch=Stopwatch.StartNew();var config=Settings();int fps=FrameRate(config);
                Dictionary<string,object> state;lock(gate)state=new Dictionary<string,object>(snapshot);
                var sensors=sensorSampler.Latest();
                if(sensors!=null){state["temperatures"]=sensors;if(!object.ReferenceEquals(sensors,reportedSensors)){reportedSensors=sensors;Emit("sensors","temperatures",sensors);}}
                try {
                    using(var bitmap=DrawFrame(state,config,clock.Elapsed.TotalSeconds))using(var stream=new MemoryStream())using(var parameters=new EncoderParameters(1)){
                        parameters.Param[0]=new EncoderParameter(System.Drawing.Imaging.Encoder.Quality,100L);bitmap.Save(stream,jpegCodec,parameters);
                        byte[] frame=stream.ToArray();if(frame.Length>MaxJpeg)throw new InvalidDataException("Encoded image exceeds slot");
                        PublishFrame(frame);
                    }
                } catch(Exception ex){Emit("status","error","Render failed: "+ex.GetType().Name);}
                encodeMs=watch.Elapsed.TotalMilliseconds;
                double now=clock.Elapsed.TotalSeconds;
                if(now>=nextReport){long current;lock(gate)current=sequence;using(var process=Process.GetCurrentProcess())Emit("stats","requestId",Interlocked.Read(ref requestId),"attemptAcknowledgedFrames",Interlocked.Read(ref attemptAcknowledged),"renderFps",(current-previousSequence)/Math.Max(.001,now-previousTime),"fps",fps,"encodedBytes",encodedBytes,"encodeMs",encodeMs,"ackMs",ackMs,"acknowledgedFrames",acknowledged,"droppedFrames",dropped,"frameSequence",current,"workingSetBytes",process.WorkingSet64,"cpuTotalMs",process.TotalProcessorTime.TotalMilliseconds);previousSequence=current;previousTime=now;nextReport=now+2;}
                long deadline=pacer.Next(frameStart,Stopwatch.GetTimestamp(),fps);
                while(running){long current=Stopwatch.GetTimestamp();if(current>=deadline)break;int wait=(int)Math.Max(1,Math.Ceiling((deadline-current)*1000.0/Stopwatch.Frequency));Thread.Sleep(wait);}
            }
            connectRequested=false;usbFrameReady.Set();sensorSampler.Stop(1500);StopSensorBroker();if(usbThread!=null)usbThread.Join(5000);if(sensorBrokerThread!=null)sensorBrokerThread.Join(1500);return 0;
        }
        private static bool OtherWriter(int ownPid=0)
        {
            foreach(string name in new[]{"TRCC","USBLCDNEW"})foreach(var p in Process.GetProcessesByName(name))using(p){if(p.Id!=ownPid)return true;}return false;
        }
        private bool WaitIdle(int timeout,long id)
        {
            var wait=Stopwatch.StartNew();while(CurrentRequest(id)&&wait.ElapsedMilliseconds<timeout){TransportHeartbeat(id,"streaming");if(view.ReadByte(2)==0)return true;Thread.Sleep(2);}CheckRequest(id);return false;
        }
        private void ConnectUsb(long id)
        {
            Interlocked.Exchange(ref attemptAcknowledged,0);
            CheckRequest(id);
            // A failed cleanup retains the actual owned process, job and map.
            // Never overwrite those handles or start a successor beside it.
            if(helper!=null||view!=null||map!=null||helperJob!=IntPtr.Zero){
                if(!StopUsbFor(id))throw new TransportFailure("cleanup_failed","The previous owned LCD helper has not finished cleanup; its ownership was preserved.");
                CheckRequest(id);
            }
            if(OtherWriter())throw new TransportFailure("writer_conflict","TRCC or another LCD writer is running. Close it, then Connect display.");
            string directory=@"C:\Program Files\TRCCCAP", exe=Path.Combine(directory,"USBLCDNEW.exe"), dll=Path.Combine(directory,"USBLCDNEW.dll");
            string pin=Values.Text(Settings(),"helperSha256");
            try{ValidateHelperPins(false);}catch(Exception ex){throw new TransportFailure("pin_validation_failed",ex.Message);}
            CheckRequest(id);StartSensors();CheckRequest(id);
            try{map=MemoryMappedFile.CreateNew("shareMemory_ImageRGB",34560000);view=map.CreateViewAccessor(0,0,MemoryMappedFileAccess.ReadWrite);}
            catch(Exception){throw new TransportFailure("map_ownership_conflict","The LCD image map is unavailable or already owned; existing writers were preserved.");}
            var start=new ProcessStartInfo(exe){WorkingDirectory=directory,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardError=true,RedirectStandardOutput=true,RedirectStandardInput=true};
            helperJob=NewHelperJob();
            CheckRequest(id);
            try{helper=Process.Start(start);}catch(Exception){throw new TransportFailure("helper_start_failed","The reviewed LCD helper could not start.");}
            if(!AssignProcessToJobObject(helperJob,helper.Handle))throw new TransportFailure("containment_failed","Cannot contain LCD helper; connection refused.");helper.OutputDataReceived+=delegate{};helper.ErrorDataReceived+=delegate{};helper.BeginOutputReadLine();helper.BeginErrorReadLine();
            var wait=Stopwatch.StartNew();bool discovered=false;
            while(CurrentRequest(id)&&wait.ElapsedMilliseconds<12000){TransportHeartbeat(id,"handshake");if(helper.HasExited)throw new TransportFailure("helper_exited","Vendor LCD helper exited.",true);if(view.ReadByte(6)==0xDC&&view.ReadByte(4)==4&&view.ReadByte(2)==0x48){discovered=true;break;}Thread.Sleep(25);}
            CheckRequest(id);if(!discovered)throw new TransportFailure("handshake_timeout","Expected 480 x 480 Peerless Vision PM4 handshake was not received.",true);
            lock(displayGate){CheckRequest(id);view.Write(0L,(byte)0);view.Write(1L,(byte)1);view.Write(2L,(byte)0);connected=true;}
            Status("connected",null,id);
        }
        internal static byte[] Packet(byte[] jpeg)
        {
            if(jpeg==null||jpeg.Length<4||jpeg.Length>MaxJpeg||jpeg[0]!=255||jpeg[1]!=216||jpeg[jpeg.Length-2]!=255||jpeg[jpeg.Length-1]!=217)throw new InvalidDataException("Invalid JPEG frame");
            using(var stream=new MemoryStream(jpeg,false))using(var image=Image.FromStream(stream)){if(image.Width!=480||image.Height!=480)throw new InvalidDataException("Frame must be 480 x 480");}
            byte[] result=new byte[jpeg.Length+64];result[0]=0x12;result[1]=0x34;result[2]=0x56;result[3]=0x78;
            foreach(var field in new[]{new[]{4,2},new[]{8,480},new[]{12,480},new[]{56,2},new[]{60,jpeg.Length}})Array.Copy(BitConverter.GetBytes(field[1]),0,result,field[0],4);
            Array.Copy(jpeg,0,result,64,jpeg.Length);return result;
        }
        internal static bool FrameStale(double now,double lastChanged){return now-lastChanged>3;}
        internal static bool RepeatDue(double now,double lastSend){return now-lastSend>=1;}
        internal static int UsbFrameWait(double now,double lastSend,double lastChanged,double conflictCheck,bool haveFrame)
        {
            double next=Math.Min(now+.250,Math.Min(lastChanged+3,conflictCheck));
            if(haveFrame)next=Math.Min(next,lastSend+1);
            return (int)Math.Max(1,Math.Ceiling((next-now)*1000.0));
        }
        private void WaitForUsbFrame(double lastSend,double lastChanged,double conflictCheck,bool haveFrame)
        {
            usbFrameReady.WaitOne(UsbFrameWait(clock.Elapsed.TotalSeconds,lastSend,lastChanged,conflictCheck,haveFrame));
        }
        private void UsbLoop()
        {
            long last=0;double conflictCheck=0,lastSend=0,lastChanged=0;
            while(running){
                long id=Interlocked.Read(ref requestId);
                if(!connectRequested){
                    if(activeRequestId!=id||connected||helper!=null||map!=null){bool clean=StopUsbFor(id);activeRequestId=id;Status(clean?"disconnected":"unavailable",clean?null:"Owned display cleanup failed; the previous helper and ownership were preserved.",id,clean?null:"cleanup_failed",false,clean);}
                    TransportHeartbeat(id,"idle");usbFrameReady.WaitOne(50);continue;
                }
                try {
                    CheckRequest(id);TransportHeartbeat(id,"streaming");
                    if(!connected){activeRequestId=id;Status("connecting",null,id);ConnectUsb(id);last=0;lastChanged=clock.Elapsed.TotalSeconds;}
                    if(helper==null||helper.HasExited)throw new TransportFailure("helper_exited","LCD helper exited.",true);
                    if(clock.Elapsed.TotalSeconds>=conflictCheck){if(OtherWriter(helper.Id))throw new TransportFailure("writer_conflict","Another LCD writer started; custom display stopped.");conflictCheck=clock.Elapsed.TotalSeconds+1;}
                    byte[] jpeg;long number;lock(gate){jpeg=latest;number=sequence;}
                    if(jpeg==null){if(FrameStale(clock.Elapsed.TotalSeconds,lastChanged))throw new TransportFailure("renderer_stale","Renderer stopped producing fresh frames; LCD stream stopped.");WaitForUsbFrame(lastSend,lastChanged,conflictCheck,false);continue;}
                    if(number!=last)lastChanged=clock.Elapsed.TotalSeconds;
                    else {
                        if(FrameStale(clock.Elapsed.TotalSeconds,lastChanged))throw new TransportFailure("renderer_stale","Renderer stopped producing fresh frames; LCD stream stopped.");
                        if(!RepeatDue(clock.Elapsed.TotalSeconds,lastSend)){WaitForUsbFrame(lastSend,lastChanged,conflictCheck,true);continue;}
                        Status("render_stale","Repeating the last picture while awaiting a fresh frame.",id);
                    }
                    var ack=Stopwatch.StartNew();if(!WaitIdle(1500,id))throw new TransportFailure("ack_timeout","LCD helper acknowledgment timed out.",true);
                    if(view.ReadByte(0)!=0||view.ReadByte(1)!=1)throw new TransportFailure("device_not_ready","LCD device readiness changed.",true);
                    CheckRequest(id);
                    byte[] packet=Packet(jpeg);view.WriteArray(Slot,packet,0,packet.Length);view.Write(2L,(byte)1);view.Flush();
                    if(!WaitIdle(1500,id))throw new TransportFailure("ack_timeout","LCD picture was not acknowledged.",true);
                    ackMs=ack.Elapsed.TotalMilliseconds;acknowledged++;
                    if(Interlocked.Increment(ref attemptAcknowledged)==1)Emit("delivery","requestId",id,"attemptAcknowledgedFrames",1);
                    if(last>0&&number>last+1)dropped+=number-last-1;last=number;lastSend=clock.Elapsed.TotalSeconds;delivered++;
                }catch(OperationCanceledException){
                    bool clean=StopUsbFor(id);long current;bool requested;lock(displayGate){current=requestId;requested=connectRequested;}activeRequestId=current;
                    Status(clean?"disconnected":"unavailable",clean?null:"Owned display cleanup failed.",requested?id:current,clean?null:"cleanup_failed",false,clean);
                }catch(Exception ex){
                    StopRequest(id);
                    bool clean=StopUsbFor(id);var failure=ex as TransportFailure;
                    Status("unavailable",clean?ex.Message:"Owned display cleanup failed.",id,
                        clean?(failure==null?"transport_failed":failure.Code):"cleanup_failed",clean&&failure!=null&&failure.Retryable,clean);
                }
            }
            StopUsb();
        }
        internal sealed class HelperStopResult
        {
            internal bool Clean, Graceful, Forced, ExitConfirmed;
            internal string SignalError, StopError;
            internal double ElapsedMs;
        }
        internal const int HelperGraceMilliseconds=2000, HelperForcedExitMilliseconds=1000;
        // The caller supplies only its retained Process and its exclusively
        // created map. TRCC's own close/reset path sends this exact vendor IPC
        // marker; the helper then closes its USB transfers and device itself.
        internal static HelperStopResult StopOwnedHelper(Process owned,MemoryMappedViewAccessor ownedView)
        {
            if(owned==null)return new HelperStopResult{Clean=true,ExitConfirmed=true};
            return StopHelperLifetime(delegate{return owned.HasExited;},delegate(int timeout){return owned.WaitForExit(timeout);},
                delegate{owned.Kill();},ownedView==null?(Action)null:delegate{ownedView.Write(0L,0xDDCCBBAAu);Thread.MemoryBarrier();ownedView.Flush();});
        }
        // The injectable operations are used only by the synthetic failure
        // fixture. Production supplies its retained Process/map above, never
        // a PID lookup or a process selected by name.
        internal static HelperStopResult StopHelperLifetime(Func<bool> hasExited,Func<int,bool> waitForExit,Action forceStop,Action signal)
        {
            var result=new HelperStopResult();var wait=Stopwatch.StartNew();bool signalled=false;
            try
            {
                if(!hasExited())
                {
                    if(signal!=null)
                    {
                        while(!hasExited()&&wait.ElapsedMilliseconds<HelperGraceMilliseconds)
                        {
                            try
                            {
                                // Aligned little-endian AA BB CC DD. Repeat it
                                // because an in-flight vendor acknowledgement
                                // can overwrite byte 2 before the next loop.
                                signal();signalled=true;
                            }
                            catch(Exception ex){result.SignalError=ex.GetType().Name;break;}
                            if(waitForExit(25))break;
                        }
                    }
                    if(!hasExited()){result.Forced=true;forceStop();waitForExit(HelperForcedExitMilliseconds);}
                    else result.Graceful=signalled;
                }
                result.ExitConfirmed=hasExited();result.Clean=result.ExitConfirmed;
            }
            catch(Exception ex){result.StopError=ex.GetType().Name;result.Clean=false;}
            result.ElapsedMs=wait.Elapsed.TotalMilliseconds;return result;
        }
        private bool StopUsb(){return StopUsbFor(-1);}
        private bool StopUsbFor(long forRequest)
        {
            lock(usbCleanupGate)
            {
                connected=false;bool clean=true;
                var owned=helper;HelperStopResult stopped=null;
                if(owned!=null)
                {
                    stopped=StopOwnedHelper(owned,view);clean=stopped.Clean;
                    if(stopped.ExitConfirmed)try{owned.Dispose();helper=null;}catch{clean=false;}
                }
                // A failed exit confirmation must retain containment/map
                // ownership, so neither this worker nor a retry can replace it.
                if(helper==null&&helperJob!=IntPtr.Zero){if(CloseHandle(helperJob))helperJob=IntPtr.Zero;else clean=false;}
                try{StopSensorBroker();lock(sensorGate){StopOwnedNormalSensor();if(sensorBrokerThread==null||!sensorBrokerThread.IsAlive)CloseSensorMap();}}catch{clean=false;}
                if(helper==null&&helperJob==IntPtr.Zero){
                    try{if(view!=null){view.Dispose();view=null;}}catch{clean=false;}
                    try{if(view==null&&map!=null){map.Dispose();map=null;}}catch{clean=false;}
                }
                if(stopped!=null)try{Emit("cleanup","requestId",forRequest<0?Interlocked.Read(ref requestId):forRequest,"ownedHelperGraceful",stopped.Graceful,
                    "ownedHelperForced",stopped.Forced,"ownedHelperExitConfirmed",stopped.ExitConfirmed,"elapsedMs",stopped.ElapsedMs,"signalError",stopped.SignalError,"stopError",stopped.StopError);}catch{}
                return clean&&helper==null&&helperJob==IntPtr.Zero&&view==null&&map==null;
            }
        }

        private static bool Waiting(string state){return state=="waiting"||state=="needs_input"||state=="waiting_for_input"||state=="waiting_on_approval";}
        private static Color StateColor(string state){if(state=="failed"||state=="error"||state=="test_failed")return Red;if(Waiting(state))return Amber;if(state=="passed"||state=="verified_success")return Green;if(state=="working"||state=="running")return Purple;return Dim;}
        private static string Activity(Dictionary<string,object> project){var activity=Values.Get(project,"activity");if(activity is string)return(string)activity;var info=Values.Obj(activity);return Values.Bool(info,"stale")?"stale":Values.Text(info,"status","unknown");}
        private static string Human(string value){return value.Replace('_',' ').Replace('-',' ');}
        private static bool Fresh(string value,double seconds){DateTime when;return DateTime.TryParse(value,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out when)&&(DateTime.UtcNow-when.ToUniversalTime()).TotalSeconds>=0&&(DateTime.UtcNow-when.ToUniversalTime()).TotalSeconds<=seconds;}
        internal static bool SnapshotFresh(Dictionary<string,object> state){return Fresh(Values.Text(state,"utc"),15);}
        internal static string SubagentText(Dictionary<string,object> summary,bool snapshotFresh=true)
        {
            string status=Values.Text(summary,"status");object count=Values.Get(summary,"workingCount");
            bool integer=count is byte||count is sbyte||count is short||count is ushort||count is int||count is uint||count is long||count is ulong;
            if(!snapshotFresh||!integer||(status!="observed"&&status!="partial")||!Fresh(Values.Text(summary,"observedAt"),180))return "\u2014";
            decimal value=Convert.ToDecimal(count,CultureInfo.InvariantCulture);if(value<0||value>256)return "\u2014";
            return value.ToString(CultureInfo.InvariantCulture);
        }
        private static Color SubagentColor(Dictionary<string,object> summary,bool fresh)
        {
            return SubagentText(summary,fresh)=="\u2014"?Dim:Values.Text(summary,"status")=="partial"?Amber:Lilac;
        }
        internal static string ProjectState(Dictionary<string,object> project)
        {
            var health=Values.Obj(Values.Get(project,"health"));if(Values.Text(health,"status")=="failed")return "test_failed";
            string activity=Activity(project);var workspace=Values.Obj(Values.Get(project,"workspace"));string work=Fresh(Values.Text(workspace,"observedAt"),15)?Values.Text(workspace,"status"):"unknown";
            if(Waiting(activity)||Waiting(work))return "waiting";
            if(activity=="working"||activity=="running"||work=="running"||work=="working"||work=="busy"||work=="editing")return "working";
            if(activity=="turn_finished"||activity=="completed")return "turn_finished";
            if(activity=="interrupted")return "interrupted";
            if(Values.Text(health,"status")=="passed")return "passed";
            if(work=="idle"||work=="ready")return "idle";
            return activity=="stale"?"stale":"unknown";
        }
        internal static string StatusLabel(string status){switch(status){case "test_failed":return "CHECK FAILED";case "waiting":return "NEEDS INPUT";case "turn_finished":return "TURN FINISHED";case "passed":return "CHECK PASSED";default:return Human(status).ToUpperInvariant();}}
        internal static string TemperatureText(Dictionary<string,object> sensor)
        {
            object raw=Values.Get(sensor,"value");double? value=Values.Number(sensor,"value");string status=Values.Text(sensor,"status");
            bool valid=!(raw is bool)&&value.HasValue&&!double.IsNaN(value.Value)&&!double.IsInfinity(value.Value)&&value.Value>=0&&value.Value<150&&!Values.Bool(sensor,"stale")&&(status=="live"||status=="available"||status=="verified")&&Fresh(Values.Text(sensor,"observedAt"),5);
            return valid?value.Value.ToString("0",CultureInfo.InvariantCulture)+"°C":"—";
        }
        internal static int FocusIndex(List<Dictionary<string,object>> projects,Dictionary<string,object> config,double time)
        {
            if(projects.Count==0)return 0;
            foreach(string priority in new[]{"test_failed","waiting"})for(int i=0;i<projects.Count;i++)if(ProjectState(projects[i])==priority)return i;
            string requested=Values.Text(config,"focusProject");for(int i=0;i<projects.Count;i++)if(Values.Text(projects[i],"id")==requested)return i;
            return (int)(Math.Max(0,time)/Math.Max(1,Values.Int(config,"rotationSeconds",8)))%projects.Count;
        }
        private void Text(Graphics g,string text,Font font,Color color,RectangleF rect,StringAlignment align=StringAlignment.Near){using(var brush=new SolidBrush(color))using(var format=new StringFormat{Alignment=align,LineAlignment=StringAlignment.Center,Trimming=StringTrimming.EllipsisCharacter,FormatFlags=StringFormatFlags.NoWrap})g.DrawString(text,font,brush,rect,format);}
        private static double VioletObservationAge(string value)
        {
            DateTime when;return DateTime.TryParse(value,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out when)?(DateTime.UtcNow-when.ToUniversalTime()).TotalSeconds:double.NaN;
        }
        internal static string VioletStatusLine(Dictionary<string,object> state)
        {
            var report=Values.Obj(Values.Get(state,"violet"));string reporter=Values.Text(report,"reporter");
            if(Values.Bool(state,"demo")||reporter=="demo")return "VIOLET: DEMO · NOT LIVE";
            double snapshotAge=VioletObservationAge(Values.Text(state,"utc"));
            if(double.IsNaN(snapshotAge)||snapshotAge<0||snapshotAge>15)return "VIOLET: NO UPDATE · STALE FEED";
            if(reporter!="dot"&&reporter!="local_task")return "VIOLET: NO UPDATE · UNVERIFIED";
            double age=VioletObservationAge(Values.Text(report,"observedAt",Values.Text(report,"reportedAt")));
            if(double.IsNaN(age)||age<0)return "VIOLET: NO UPDATE · UNKNOWN TIME";
            if(Values.Bool(report,"stale",true)||age>120)return "VIOLET: NO UPDATE · STALE";
            if(!Values.Bool(report,"connected"))return "VIOLET: NO UPDATE · DISCONNECTED";
            string label;switch(Values.Text(report,"status")){
                case "working":label="WORKING";break;case "waiting":label="WAITING";break;
                case "turn_finished":label="FINISHED";break;case "interrupted":label="INTERRUPTED";break;
                default:label="NO UPDATE";break;
            }
            return "VIOLET: "+label+" · "+(reporter=="local_task"?"LOCAL TASK":"DOT REPORT");
        }
        private void DrawVioletStatus(Graphics g,Dictionary<string,object> state)
        {
            string line=VioletStatusLine(state);Color color=line.StartsWith("VIOLET: WORKING",StringComparison.Ordinal)?Lilac:line.StartsWith("VIOLET: FINISHED",StringComparison.Ordinal)?White:line.StartsWith("VIOLET: WAITING",StringComparison.Ordinal)||line.StartsWith("VIOLET: INTERRUPTED",StringComparison.Ordinal)||line.StartsWith("VIOLET: DEMO",StringComparison.Ordinal)?Amber:Dim;
            Text(g,line,mono,color,new RectangleF(28,392,424,17));
        }
        private void Border(Graphics g,Rectangle rect,Color color){using(var pen=new Pen(color,1))g.DrawPolygon(pen,new[]{new Point(rect.Left+8,rect.Top),new Point(rect.Right,rect.Top),new Point(rect.Right,rect.Bottom-8),new Point(rect.Right-8,rect.Bottom),new Point(rect.Left,rect.Bottom),new Point(rect.Left,rect.Top+8)});}
        private List<Dictionary<string,object>> Projects(Dictionary<string,object> state)
        {
            var rows=Values.Rows(Values.Get(state,"projects"));if(rows.Count==0)foreach(string name in new[]{"ED-IT Buddy","MW5 MOD","INTERLINKED"})rows.Add(new Dictionary<string,object>{{"id",name},{"label",name},{"activity",new Dictionary<string,object>{{"status","unknown"}}}});return rows;
        }
        private void Binary(Graphics g,float x,float y,Color color,double alpha,int digit)
        {
            using(var brush=new SolidBrush(Color.FromArgb((int)(Math.Max(0,Math.Min(1,alpha))*255),color)))g.DrawString((digit&1).ToString(CultureInfo.InvariantCulture),binaryFont,brush,x-3,y-6,StringFormat.GenericTypographic);
        }
        private static PointF PathPoint(PointF[] points,double distance)
        {
            for(int i=1;i<points.Length;i++){double dx=points[i].X-points[i-1].X,dy=points[i].Y-points[i-1].Y,length=Math.Sqrt(dx*dx+dy*dy);if(distance<=length&&length>0)return new PointF(points[i-1].X+(float)(dx*distance/length),points[i-1].Y+(float)(dy*distance/length));distance-=length;}return points[points.Length-1];
        }
        private void DrawWire(Graphics g,int x,string status,int index,double time)
        {
            bool active=status=="working"||status=="waiting"||status=="test_failed";Color color=active?StateColor(status):Color.FromArgb(101,91,117);
            PointF[] points=x==240?new[]{new PointF(240,147),new PointF(240,177)}:new[]{new PointF(240,147),new PointF(240,158),new PointF(x,158),new PointF(x,177)};
            using(var pen=new Pen(color,1))g.DrawLines(pen,points);if(!active)return;
            double length=0;for(int i=1;i<points.Length;i++)length+=Math.Abs(points[i].X-points[i-1].X)+Math.Abs(points[i].Y-points[i-1].Y);
            double rate=status=="waiting"?.38:status=="test_failed"?.9:.7;
            for(int n=0;n<2;n++){double head=((time*rate+index*.23+n*.5)%1)*length;for(int j=4;j>=0;j--){double d=head-j*9;if(d<0)continue;var p=PathPoint(points,d);Binary(g,p.X,p.Y,j==0?BinaryHead:color,(1-j/6.0)*.95,n+j+(int)(time*2));}}
            for(int n=0;n<16;n++){
                double life=(time*(status=="waiting"?.55:1.1)+n*.618+index*.17)%1,d=((n*.38197+time*.18)%1)*length;
                var p=PathPoint(points,d);var before=PathPoint(points,Math.Max(0,d-.5));var after=PathPoint(points,Math.Min(length,d+.5));bool horizontal=Math.Abs(after.X-before.X)>Math.Abs(after.Y-before.Y);
                float drift=(float)((3+life*13)*(n%2==1?1:-1));Binary(g,p.X+(horizontal?(float)Math.Sin(n*3+life*4)*2:drift),p.Y+(horizontal?drift:(float)Math.Sin(n*3+life*4)*2),n%6==0?WireParticle:color,Math.Sin(life*Math.PI)*.7,n+index+(int)(time*2));
            }
        }
        // These scene records contain display metadata only. In particular a
        // Codex project association is never treated as a model/control edge.
        internal sealed class WebNode
        {
            internal string Id,Kind,Label,Detail,Status,ProjectId,ParentId,FlowId,Host,Role,Digest;
            internal string Origin,ObservedAt;
            internal readonly HashSet<string> Groups=new HashSet<string>();
            internal bool Fresh,Pinned;
        }
        internal sealed class WebEdge
        {
            internal string From,To,Kind,FlowId,Host,Role,Status;
        }
        internal sealed class WebScene
        {
            internal readonly List<WebNode> Nodes=new List<WebNode>();
            internal readonly List<WebEdge> Edges=new List<WebEdge>();
            internal int Page=0,PageCount=1,TotalNodes,HiddenEdges=0,Omitted;
            internal bool Partial;
            internal bool ActivityAvailable;
            internal readonly List<WebGroup> Groups=new List<WebGroup>();
        }
        private static string AiPhase(Dictionary<string,object> row){return Values.Text(row,"phase",Values.Text(row,"status")).ToUpperInvariant();}
        private static bool AiActive(string phase){return phase=="QUEUED"||phase=="PREPARING"||phase=="RUNNING"||phase=="WORKING"||phase=="REVIEWING"||phase=="APPLYING"||phase=="REQUEST_PENDING";}
        private static bool AiWaiting(string phase){return phase=="READY_FOR_REVIEW"||phase=="REVIEW_PENDING"||phase=="RESULT_READY"||phase=="WAITING";}
        private static string AiStatus(string phase){return phase=="FAILED"||phase=="RECOVERY_REQUIRED"?"test_failed":AiWaiting(phase)?"waiting":AiActive(phase)?"working":phase=="CANCELLED"||phase=="INTERRUPTED"?"interrupted":"idle";}
        internal static bool AiFresh(Dictionary<string,object> state)
        {
            var ai=Values.Obj(Values.Get(state,"ai"));return SnapshotFresh(state)&&Values.Bool(ai,"available")&&Fresh(Values.Text(ai,"observedAt"),15);
        }
        private static string AiOrigin(string origin)
        {
            switch(origin){case "violet_bridge":return "VIOLET BRIDGE";case "assistant":return "ASSISTANT";case "buddy":return "BUDDY";case "calibration":return "CALIBRATION";default:return "LOCAL AI";}
        }
        internal static string AiFlowCaption(Dictionary<string,object> ai)
        {
            var flow=Values.Obj(Values.Get(ai,"selectedFlow"));string phase=AiPhase(flow);
            bool read=Values.Rows(Values.Get(ai,"pulses")).Exists(delegate(Dictionary<string,object> pulse){double age=VioletObservationAge(Values.Text(pulse,"occurredAt"));return Values.Text(pulse,"flowId")==Values.Text(flow,"id")&&Values.Text(pulse,"kind")=="result_read"&&!double.IsNaN(age)&&age>=0&&age<3;});
            string line=read?"RESULT READ":Values.Obj(Values.Get(flow,"worker")).Count>0?(phase=="REVIEWING"?"WORKER > REVIEW":AiWaiting(phase)?"REVIEW REQUIRED":"WORKER > "+phase.Replace('_',' ')):phase=="RESULT_READY"?"RESULT READY":Values.Text(flow,"origin").Length>0?AiOrigin(Values.Text(flow,"origin")):"No active AI request";
            int other=Values.Int(ai,"otherActiveCount");return line+(other>0?" +"+other:"");
        }
        private static string HostLabel(string host){return host=="local"?"THIS-PC":host=="msi"?"MSI":"HOST ?";}
        private static string ModelLabel(string name)
        {
            int slash=name.LastIndexOf('/');if(slash>=0&&slash+1<name.Length)name=name.Substring(slash+1);return name.Length>100?name.Substring(0,100):name;
        }
        private static string TaskSuffix(string id){int start=Math.Max(0,id.Length-4);return id.Substring(start).ToUpperInvariant();}
        private static void AddWebEdge(List<WebEdge> edges,string from,string to,string kind,string flow="",string host="",string role="")
        {
            if(string.IsNullOrEmpty(from)||string.IsNullOrEmpty(to)||from==to)return;
            foreach(var edge in edges)if(edge.From==from&&edge.To==to&&edge.Kind==kind&&edge.FlowId==flow)return;
            edges.Add(new WebEdge{From=from,To=to,Kind=kind,FlowId=flow,Host=host,Role=role});
        }
        private static WebNode AddModelNode(Dictionary<string,WebNode> nodes,Dictionary<string,WebNode> aliases,string host,string model,string digest,bool fresh,bool active)
        {
            if((host!="local"&&host!="msi")||string.IsNullOrEmpty(model))return null;
            string alias=host+":"+model;WebNode node;
            if(string.IsNullOrEmpty(digest)&&aliases.TryGetValue(alias,out node)){node.Fresh|=fresh;if(active)node.Status="working";return node;}
            string key="model:"+host+":"+(string.IsNullOrEmpty(digest)?model:digest);
            if(nodes.TryGetValue(key,out node)){aliases[alias]=node;node.Fresh|=fresh;if(active)node.Status="working";return node;}
            node=new WebNode{Id=key,Kind="model",Label=ModelLabel(model),Detail=HostLabel(host)+(active?" · REQUEST":" · LOADED"),Status=active?"working":"idle",Fresh=fresh,Host=host,Digest=digest};nodes[key]=node;aliases[alias]=node;return node;
        }
        private static int WebPriority(WebNode node)
        {
            if(node.Pinned)return 0;if(node.Status=="test_failed")return 1;if(node.Status=="waiting")return 2;if(node.Status=="working")return 3;return node.Kind=="project"?4:5;
        }
        private void DrawAiLink(Graphics g,PointF from,PointF to,Dictionary<string,object> ai,string flow,string host,string role,double time,bool live,bool inference)
        {
            using(var pen=new Pen(live?Color.FromArgb(98,53,124):Color.FromArgb(53,43,63),1))g.DrawLine(pen,from,to);
            if(!live)return;
            foreach(var pulse in Values.Rows(Values.Get(ai,"pulses"))){
                if(Values.Text(pulse,"flowId")!=flow)continue;string kind=Values.Text(pulse,"kind");bool sent=kind=="request_sent",received=kind=="response_received",read=kind=="result_read";
                bool directRead=inference&&read&&(role=="analysis"||role=="calibration");
                if(inference?(!sent&&!received&&!directRead)||!directRead&&(Values.Text(pulse,"host")!=host||Values.Text(pulse,"role")!=role):!read)continue;
                double age=VioletObservationAge(Values.Text(pulse,"occurredAt"));if(double.IsNaN(age)||age<0||age>=3)continue;
                double progress=age/3;if(received||read)progress=1-progress;
                for(int tail=0;tail<4;tail++){double p=progress+(received||read?1:-1)*tail*.035;if(p<0||p>1)continue;Binary(g,from.X+(float)((to.X-from.X)*p),from.Y+(float)((to.Y-from.Y)*p),tail==0?BinaryHead:Lilac,1-tail*.2,tail+(int)Values.Int(pulse,"sequence"));}
            }
        }
        private void DrawAiDeck(Graphics g,Dictionary<string,object> state,double time)
        {
            var ai=Values.Obj(Values.Get(state,"ai"));bool live=AiFresh(state);Border(g,new Rectangle(18,14,146,130),live?Color.FromArgb(115,49,147):Color.FromArgb(68,50,81));
            Text(g,"LOCAL AI",small,live?ActivityHeading:Dim,new RectangleF(26,17,130,16));int index=0;
            foreach(string id in new[]{"local","msi"}){
                var host=Values.Rows(Values.Get(ai,"hosts")).Find(delegate(Dictionary<string,object> h){return Values.Text(h,"id")==id;})??new Dictionary<string,object>();
                int top=36+index*41;bool hostFresh=live&&!Values.Bool(host,"stale",true)&&Fresh(Values.Text(host,"observedAt"),60);var call=Values.Rows(Values.Get(ai,"activeCalls")).Find(delegate(Dictionary<string,object> c){return Values.Text(c,"host")==id;});
                var model=call!=null?call:Values.Obj(Values.Get(host,"activeModel"));var loaded=Values.Rows(Values.Get(host,"loadedModels"));string modelName=Values.Text(model,"modelId",Values.Text(model,"id"));if(modelName.Length==0&&loaded.Count>0)modelName=Values.Text(loaded[0],"id");
                bool busy=live&&call!=null;string heading=HostLabel(id)+"  "+(busy?"R"+Values.Int(host,"runningCount",1)+" Q"+Values.Int(host,"queuedCount"):!hostFresh?"STALE":Values.Bool(host,"available")?"READY":"OFFLINE");
                Text(g,heading,small,busy?Lilac:hostFresh?RoomText:Dim,new RectangleF(26,top,130,14));Text(g,modelName.Length>0?ModelLabel(modelName):"No model observation",small,busy?White:Dim,new RectangleF(26,top+14,130,15));
                if(busy){float x=29+(float)((Math.Sin(time*4)+1)*.5*116);using(var brush=new SolidBrush(Lilac))g.FillEllipse(brush,x,top+30,3,3);}
                var recent=Values.Rows(Values.Get(ai,"pulses")).FindLast(delegate(Dictionary<string,object> p){double age=VioletObservationAge(Values.Text(p,"occurredAt"));return Values.Text(p,"host")==id&&!double.IsNaN(age)&&age>=0&&age<3;});
                if(live&&(busy||recent!=null)){var metadata=call??recent;bool violet=Values.Text(metadata,"origin")=="violet_bridge";PointF from=new PointF(violet?171:27,top+31),to=new PointF(148,top+31);DrawAiLink(g,from,to,ai,Values.Text(metadata,"flowId"),id,Values.Text(metadata,"role"),time,true,true);}
                index++;
            }
            string line=live?AiFlowCaption(ai):"AI observations unavailable";
            Text(g,line,small,live?Lilac:Dim,new RectangleF(26,120,130,18));
        }
        internal sealed class WebGroup
        {
            internal string Id,Label,Status,Kind;
            internal readonly List<WebNode> Items=new List<WebNode>();
            internal readonly Dictionary<string,string> Activities=new Dictionary<string,string>();
            internal bool Partial;
        }
        private static void WebActivity(WebGroup group,string id,string status){if(string.IsNullOrEmpty(id))return;string previous;if(!group.Activities.TryGetValue(id,out previous)||WebRank(status)<WebRank(previous))group.Activities[id]=status;}
        private readonly Dictionary<string,int> webSlots=new Dictionary<string,int>();
        private readonly Dictionary<string,string> webSelected=new Dictionary<string,string>();
        private int webDensity=-1,webPendingDensity=-1;
        private double webDensitySince;
        private readonly Font webName=new Font("Segoe UI",14,FontStyle.Bold,GraphicsUnit.Pixel),webDenseName=new Font("Segoe UI",12,FontStyle.Bold,GraphicsUnit.Pixel),webMeta=new Font("Segoe UI",11,FontStyle.Regular,GraphicsUnit.Pixel),webTitle=new Font("Segoe UI",14f,FontStyle.Bold,GraphicsUnit.Pixel),webTemperature=new Font("Segoe UI",32,FontStyle.Bold,GraphicsUnit.Pixel),webBinary=new Font("Consolas",10,FontStyle.Regular,GraphicsUnit.Pixel);
        private static readonly Color WebPurple=Color.FromArgb(248,30,235),WebLilac=Color.FromArgb(255,85,244);
        private static string WebHost(string host){return host=="local"?"THIS-MACHINE":host=="msi"?"MSI":"HOST ?";}
        private static string WebModelName(string name){name=ModelLabel(name);int colon=name.IndexOf(':');if(colon<0)return name;string stem=name.Substring(0,colon),tag=name.Substring(colon+1).Split('-')[0];if(stem.StartsWith("gemma",StringComparison.OrdinalIgnoreCase))stem="Gemma "+stem.Substring(5);else if(stem.Length>0)stem=char.ToUpperInvariant(stem[0])+stem.Substring(1);return stem+" · "+tag.ToUpperInvariant();}
        private static int WebRank(string status){switch(status){case "test_failed":return 0;case "waiting":return 1;case "reviewing":return 2;case "working":return 3;case "queued":return 4;case "finished":return 5;default:return 9;}}
        private static bool WebMoving(string status){return status=="working"||status=="reviewing";}
        private static bool WebShown(string status){return WebRank(status)<9;}
        private static string WebLabel(string status){switch(status){case "test_failed":return "FAILED";case "waiting":return "NEEDS INPUT";case "reviewing":return "REVIEWING";case "queued":return "QUEUED";case "finished":return "FINISHED";default:return "WORKING";}}
        private static Color WebColor(string status){return status=="test_failed"?Red:status=="waiting"?Amber:status=="finished"?Green:status=="queued"||status=="idle"?Dim:WebPurple;}
        private static string WebPhase(Dictionary<string,object> flow)
        {
            string phase=AiPhase(flow);
            if(phase=="FAILED"||phase=="RECOVERY_REQUIRED")return "test_failed";
            if(phase=="QUEUED")return "queued";
            if(phase=="REVIEWING")return "reviewing";
            if(phase=="REVIEW_PENDING"||phase=="READY_FOR_REVIEW"||phase=="WAITING"||phase=="RESULT_READY"&&Values.Text(flow,"resultState")=="review_required")return "waiting";
            if(AiActive(phase))return "working";
            if(phase=="APPLIED"||phase=="COMPLETED"||phase=="RESPONSE_RECEIVED"||phase=="RESULT_READY")return Fresh(Values.Text(flow,"terminalAt",Values.Text(flow,"phaseAt")),3)?"finished":"idle";
            return "idle";
        }
        private static string WebProjectState(Dictionary<string,object> row)
        {
            string status=ProjectState(row);var activity=Values.Obj(Values.Get(row,"activity"));var health=Values.Obj(Values.Get(row,"health"));var workspace=Values.Obj(Values.Get(row,"workspace"));
            if(status=="test_failed")return Fresh(Values.Text(health,"observedAt"),180)?status:"idle";
            if(status=="turn_finished")return Fresh(Values.Text(activity,"observedAt"),3)?"finished":"idle";
            if(status=="waiting"||status=="working"){bool observed=!Values.Bool(activity,"stale")&&Fresh(Values.Text(activity,"observedAt"),180);string work=Values.Text(workspace,"status");bool workspaceLive=Fresh(Values.Text(workspace,"observedAt"),15)&&(Waiting(work)||work=="working"||work=="running"||work=="busy"||work=="editing");return observed||workspaceLive?status:"idle";}return "idle";
        }
        private static void WebGroupNode(WebNode node,string group){if(!string.IsNullOrEmpty(group))node.Groups.Add(group);}
        internal static WebScene BuildWebScene(Dictionary<string,object> state,Dictionary<string,object> config,double time)
        {
            var scene=new WebScene();var nodes=new Dictionary<string,WebNode>();var aliases=new Dictionary<string,WebNode>();var groups=new Dictionary<string,WebGroup>();
            var ai=Values.Obj(Values.Get(state,"ai"));var graph=Values.Obj(Values.Get(state,"lifecycleGraph"));var display=Values.Obj(Values.Get(state,"coolerDisplay"));
            bool fresh=SnapshotFresh(state),aiFresh=AiFresh(state);var titles=Values.Obj(Values.Get(display,"titles"));
            scene.Partial=Values.Bool(graph,"partial")||Values.Bool(ai,"partial")||Values.Bool(display,"activityPartial");scene.Omitted=Values.Int(graph,"omittedCount");
            scene.ActivityAvailable=fresh&&(display.ContainsKey("activityAvailable")?Values.Bool(display,"activityAvailable"):aiFresh||Values.Bool(graph,"available"));
            foreach(var row in Values.Rows(Values.Get(state,"projects"))){
                string id=Values.Text(row,"id");if(id.Length==0)continue;string key="project:"+id;
                groups[key]=new WebGroup{Id=key,Label=Values.Text(row,"label",id),Kind="project",Status=fresh?WebProjectState(row):"idle",Partial=scene.Partial};
                nodes[key]=new WebNode{Id=key,Kind="project",Label=Values.Text(row,"label",id),ProjectId=id,Status=fresh?WebProjectState(row):"idle",Fresh=fresh};
            }
            Func<string,string,string> groupFor=delegate(string project,string origin){
                string key="project:"+project;if(project.Length>0&&groups.ContainsKey(key))return key;
                key="source:"+(origin.Length>0?origin:"local_ai");if(!groups.ContainsKey(key))groups[key]=new WebGroup{Id=key,Kind="source",Label=origin=="codex"?"CODEX":AiOrigin(origin),Status="idle",Partial=origin=="codex"?Values.Bool(graph,"partial"):Values.Bool(ai,"partial")};return key;
            };
            var flows=Values.Rows(Values.Get(ai,"flows"));var selected=Values.Obj(Values.Get(ai,"selectedFlow"));
            if(Values.Text(selected,"id").Length>0&&!flows.Exists(delegate(Dictionary<string,object> row){return Values.Text(row,"id")==Values.Text(selected,"id");}))flows.Add(selected);
            if(aiFresh)foreach(var flow in flows){
                string id=Values.Text(flow,"id"),status=WebPhase(flow),origin=Values.Text(flow,"origin"),project=Values.Text(flow,"projectId");
                if(id.Length==0||!WebShown(status)||Values.Bool(flow,"stale")||!Fresh(Values.Text(flow,"observedAt",Values.Text(flow,"phaseAt")),180))continue;
                string group=groupFor(project,origin),workerId=Values.Text(flow,"workerId"),actor="";WebActivity(groups[group],id,status);var worker=Values.Obj(Values.Get(flow,"worker"));var reviewer=Values.Obj(Values.Get(flow,"reviewer"));
                if(workerId.Length>0||worker.Count>0){
                    actor="worker:"+(workerId.Length>0?workerId:id);var task=new WebNode{Id=actor,Kind="worker",Label="Local worker",Detail=WebLabel(status),Status=status,ProjectId=project,FlowId=id,Origin=origin,Host=Values.Text(flow,"executionHost"),Role="worker",ObservedAt=Values.Text(flow,"phaseAt"),Fresh=true,Pinned=id==Values.Text(selected,"id")};WebGroupNode(task,group);nodes[actor]=task;
                    if(project.Length>0&&nodes.ContainsKey("project:"+project))AddWebEdge(scene.Edges,actor,"project:"+project,"assignment",id);
                    if(origin=="violet_bridge")AddWebEdge(scene.Edges,"violet",actor,"bridge",id);
                }
                bool direct=workerId.Length==0&&worker.Count==0&&Values.Obj(Values.Get(flow,"model")).Count>0;
                if(direct){actor="analysis:"+id;var analysis=new WebNode{Id=actor,Kind="analysis",Label=AiOrigin(origin),Detail=WebLabel(status),Status=status,ProjectId=project,FlowId=id,Origin=origin,Host=Values.Text(flow,"executionHost"),Role=Values.Text(flow,"role","analysis"),ObservedAt=Values.Text(flow,"observedAt",Values.Text(flow,"phaseAt")),Fresh=true};WebGroupNode(analysis,group);nodes[actor]=analysis;if(project.Length>0&&nodes.ContainsKey("project:"+project))AddWebEdge(scene.Edges,actor,"project:"+project,"association",id);if(origin=="violet_bridge")AddWebEdge(scene.Edges,"violet",actor,"bridge",id);}
                foreach(string role in new[]{"worker","reviewer","analysis"}){
                    var model=role=="worker"?worker:role=="reviewer"?reviewer:Values.Obj(Values.Get(flow,"model"));if(model.Count==0||role=="analysis"&&!direct)continue;string inferenceRole=role=="analysis"?Values.Text(flow,"role","analysis"):role;
                    string host=Values.Text(model,"host"),name=Values.Text(model,"modelId",Values.Text(model,"id"));bool active=role=="reviewer"?status=="reviewing":status=="working";
                    var modelNode=AddModelNode(nodes,aliases,host,name,Values.Text(model,"digest"),true,active);if(modelNode==null)continue;WebGroupNode(modelNode,group);modelNode.Origin=origin;modelNode.FlowId=id;modelNode.Role=inferenceRole;modelNode.ObservedAt=Values.Text(flow,"phaseAt");
                    if((role=="analysis"||active)&&WebRank(status)<WebRank(modelNode.Status))modelNode.Status=status;modelNode.Detail=WebHost(host);string from=actor;
                    if(role=="reviewer"){
                        from="reviewer:"+(workerId.Length>0?workerId:id);var review=new WebNode{Id=from,Kind="reviewer",Label="Independent review",Detail=status=="reviewing"?"REVIEWING":"Review stage",Status=status=="reviewing"?"reviewing":"idle",ProjectId=project,FlowId=id,Origin=origin,Role="reviewer",Fresh=true};WebGroupNode(review,group);nodes[from]=review;if(actor.Length>0)AddWebEdge(scene.Edges,actor,from,"review",id);
                    }
                    if(from.Length>0)AddWebEdge(scene.Edges,from,modelNode.Id,"inference",id,host,inferenceRole);
                    else {if(project.Length>0&&nodes.ContainsKey("project:"+project))AddWebEdge(scene.Edges,modelNode.Id,"project:"+project,"association",id);if(origin=="violet_bridge")AddWebEdge(scene.Edges,"violet",modelNode.Id,"inference",id,host,inferenceRole);}
                    foreach(var edge in scene.Edges)if(edge.Kind=="inference"&&edge.FlowId==id&&edge.Role==inferenceRole&&edge.To==modelNode.Id)edge.Status=role=="reviewer"?(status=="reviewing"?"reviewing":"idle"):role=="worker"?(status=="reviewing"||status=="waiting"?"idle":status):status;
                }
            }
            if(aiFresh)foreach(var call in Values.Rows(Values.Get(ai,"activeCalls"))){
                if(Values.Bool(call,"stale")||!Fresh(Values.Text(call,"observedAt",Values.Text(call,"phaseAt")),180))continue;
                string origin=Values.Text(call,"origin"),flow=Values.Text(call,"flowId"),status=AiPhase(call)=="QUEUED"?"queued":"working";var parent=flows.Find(delegate(Dictionary<string,object> row){return Values.Text(row,"id")==flow;});
                string project=parent==null?Values.Text(call,"projectId"):Values.Text(parent,"projectId"),group=groupFor(project,origin),host=Values.Text(call,"host"),role=Values.Text(call,"role");WebActivity(groups[group],flow.Length>0?flow:Values.Text(call,"id"),status);
                var node=AddModelNode(nodes,aliases,host,Values.Text(call,"modelId"),Values.Text(call,"digest"),true,status=="working");if(node==null)continue;WebGroupNode(node,group);node.FlowId=flow;node.Origin=origin;node.Role=role;string callStatus=role=="reviewer"&&status=="working"?"reviewing":status;if(WebRank(callStatus)<WebRank(node.Status))node.Status=callStatus;node.Detail=WebHost(host);node.ObservedAt=Values.Text(call,"phaseAt");
                var route=scene.Edges.Find(delegate(WebEdge edge){return edge.Kind=="inference"&&edge.FlowId==flow&&edge.Role==role&&edge.To==node.Id;});
                if(route==null){string actor="analysis:"+(flow.Length>0?flow:Values.Text(call,"id"))+":"+role;var analysis=new WebNode{Id=actor,Kind="analysis",Label=AiOrigin(origin),Detail=WebLabel(callStatus),Status=callStatus,ProjectId=project,FlowId=flow,Origin=origin,Role=role,ObservedAt=Values.Text(call,"observedAt",Values.Text(call,"phaseAt")),Fresh=true};WebGroupNode(analysis,group);nodes[actor]=analysis;if(project.Length>0&&nodes.ContainsKey("project:"+project))AddWebEdge(scene.Edges,actor,"project:"+project,"association",flow);if(origin=="violet_bridge")AddWebEdge(scene.Edges,"violet",actor,"bridge",flow);AddWebEdge(scene.Edges,actor,node.Id,"inference",flow,host,role);}
                else {WebNode actor;if(nodes.TryGetValue(route.From,out actor)&&WebRank(callStatus)<WebRank(actor.Status))actor.Status=callStatus;}
                foreach(var edge in scene.Edges)if(edge.Kind=="inference"&&edge.FlowId==flow&&edge.Role==role&&edge.To==node.Id)edge.Status=callStatus;
            }
            // Short, validated return/read observations retain their true model identity.
            if(aiFresh)foreach(var pulse in Values.Rows(Values.Get(ai,"pulses"))){
                string kind=Values.Text(pulse,"kind");if((kind!="response_received"&&kind!="request_sent"&&kind!="result_read")||!Fresh(Values.Text(pulse,"occurredAt"),3))continue;
                string flow=Values.Text(pulse,"flowId"),origin=Values.Text(pulse,"origin"),host=Values.Text(pulse,"host"),role=Values.Text(pulse,"role");var parent=flows.Find(delegate(Dictionary<string,object> row){return Values.Text(row,"id")==flow;});
                var model=parent==null?new Dictionary<string,object>():Values.Obj(Values.Get(parent,"model"));if(host.Length==0)host=Values.Text(model,"host");if(role.Length==0)role=parent==null?"analysis":Values.Text(parent,"role","analysis");
                string project=parent==null?Values.Text(pulse,"projectId"):Values.Text(parent,"projectId"),group=groupFor(project,origin);WebActivity(groups[group],flow,kind=="request_sent"?"working":kind=="result_read"?"idle":"finished");var node=AddModelNode(nodes,aliases,host,Values.Text(pulse,"modelId",Values.Text(model,"modelId")),Values.Text(pulse,"digest",Values.Text(model,"digest")),true,false);if(node==null)continue;
                WebGroupNode(node,group);node.FlowId=flow;node.Origin=origin;node.Role=role;if(!WebShown(node.Status))node.Status=kind=="result_read"?"idle":kind=="request_sent"?"working":"finished";node.Detail=kind=="result_read"?"RESULT READ":WebHost(host);node.ObservedAt=Values.Text(pulse,"occurredAt");
                if(origin=="violet_bridge"&&!scene.Edges.Exists(delegate(WebEdge edge){return edge.To==node.Id&&edge.Kind=="inference"&&edge.FlowId==flow;}))AddWebEdge(scene.Edges,"violet",node.Id,"inference",flow,host,role);
            }
            if(fresh)foreach(var row in Values.Rows(Values.Get(graph,"nodes"))){
                string id=Values.Text(row,"nodeId"),status=Values.Text(row,"status"),kind=Values.Text(row,"kind"),project=Values.Text(row,"projectId");if(id.Length==0||Values.Text(row,"confidence")=="stale"||!Fresh(Values.Text(row,"observedAt"),180))continue;
                if(status=="turn_finished")status=Fresh(Values.Text(row,"observedAt"),3)?"finished":"idle";if(!WebShown(status))continue;
                string group=groupFor(project,"codex"),title=Values.Text(titles,id,kind=="codex_child"?"Subagent":"Codex chat");WebActivity(groups[group],"codex:"+id,status);
                var node=new WebNode{Id="codex:"+id,Kind=kind=="codex_child"?"codex_child":"codex_main",Label=title,Detail=kind=="codex_child"&&Values.Text(row,"parentNodeId").Length==0?"PARENT ?":WebLabel(status),Status=status,ProjectId=project,ParentId=Values.Text(row,"parentNodeId"),Origin="codex",ObservedAt=Values.Text(row,"observedAt"),Fresh=true};WebGroupNode(node,group);nodes[node.Id]=node;
                if(project.Length>0&&nodes.ContainsKey("project:"+project))AddWebEdge(scene.Edges,node.Id,"project:"+project,"association");
            }
            foreach(var node in nodes.Values)if(node.Kind=="codex_child"&&node.ParentId.Length>0&&nodes.ContainsKey("codex:"+node.ParentId))AddWebEdge(scene.Edges,"codex:"+node.ParentId,node.Id,"parent");
            foreach(var group in groups.Values){
                foreach(var node in nodes.Values)if(node.Groups.Contains(group.Id))group.Items.Add(node);
                foreach(string status in group.Activities.Values)if(WebRank(status)<WebRank(group.Status))group.Status=status;
                if(!WebShown(group.Status)&&group.Items.Exists(delegate(WebNode node){return node.Detail=="RESULT READ";}))group.Status="idle";
                if(!WebShown(group.Status)&&!group.Items.Exists(delegate(WebNode node){return node.Detail=="RESULT READ";}))continue;
                group.Items.Sort(delegate(WebNode a,WebNode b){int n=WebRank(a.Status).CompareTo(WebRank(b.Status));return n!=0?n:string.CompareOrdinal(a.Id,b.Id);});scene.Groups.Add(group);
            }
            // Registration order is meaningful and stable; source groups follow it.
            scene.Groups.Sort(delegate(WebGroup a,WebGroup b){if(a.Kind!=b.Kind)return a.Kind=="project"?-1:1;return string.CompareOrdinal(a.Id,b.Id);});
            var retained=new HashSet<string>();foreach(var group in scene.Groups){retained.Add(group.Id);foreach(var node in group.Items)retained.Add(node.Id);}
            foreach(var node in nodes.Values)if(retained.Contains(node.Id)){scene.Nodes.Add(node);}
            scene.Edges.RemoveAll(delegate(WebEdge edge){return edge.From!="violet"&&!retained.Contains(edge.From)||edge.To!="violet"&&!retained.Contains(edge.To);});
            scene.TotalNodes=scene.Nodes.Count;return scene;
        }
        internal sealed class WebCard
        {
            internal Rectangle Bounds;
            internal string GroupId,Role,Label,Body,Status,Summary;
            internal bool Dense,Badge;
            internal readonly HashSet<string> Nodes=new HashSet<string>();
        }
        private static string WebActivityKey(WebNode node){return string.IsNullOrEmpty(node.FlowId)?node.Id:node.FlowId;}
        private WebNode SelectWebItem(WebGroup group)
        {
            var candidates=group.Items.FindAll(delegate(WebNode node){return node.Kind=="codex_main"||node.Kind=="worker"||node.Kind=="analysis";});
            if(candidates.Count==0)candidates=group.Items.FindAll(delegate(WebNode node){return WebShown(node.Status);});
            string previous;if(webSelected.TryGetValue(group.Id,out previous)){var retained=candidates.Find(delegate(WebNode node){return WebActivityKey(node)==previous;});if(retained!=null)return retained;}
            candidates.Sort(delegate(WebNode a,WebNode b){int n=WebRank(a.Status).CompareTo(WebRank(b.Status));if(n==0)n=(a.Kind=="model"?1:0).CompareTo(b.Kind=="model"?1:0);return n!=0?n:string.CompareOrdinal(a.Id,b.Id);});
            if(candidates.Count==0)return null;webSelected[group.Id]=WebActivityKey(candidates[0]);return candidates[0];
        }
        private static int WebCount(WebGroup group,string kind){var ids=new HashSet<string>();foreach(var node in group.Items)if(node.Kind==kind&&WebShown(node.Status))ids.Add(kind=="model"?node.Id:WebActivityKey(node));return ids.Count;}
        private static int WebStateCount(WebGroup group,string status){int count=0;foreach(string value in group.Activities.Values)if(value==status)count++;return count;}
        private static string WebCounts(WebGroup group)
        {
            var parts=new List<string>();foreach(string kind in new[]{"codex_main","codex_child","worker","reviewer","analysis"}){int count=WebCount(group,kind);if(count==0)continue;string label=kind=="codex_main"?"chat":kind=="codex_child"?"sub":kind=="worker"?"worker":kind=="analysis"?"request":"review";parts.Add(count+" "+label+(count==1?"":"s"));}
            if(parts.Count==0){int count=WebCount(group,"model");if(count>0)parts.Add(count+" model"+(count==1?"":"s"));}
            return string.Join(" · ",parts.ToArray());
        }
        private static string WebSummary(WebGroup group,bool dense)
        {
            if(group.Status=="idle")return "RESULT READ";
            if(group.Status=="test_failed"){int failed=WebStateCount(group,"test_failed"),working=WebStateCount(group,"working")+WebStateCount(group,"reviewing");return working>0?Math.Max(1,failed)+" failed · "+working+" working":"FAILED";}
            if(group.Status=="waiting"||group.Status=="queued"||group.Status=="finished")return WebLabel(group.Status);
            string counts=WebCounts(group);return dense&&counts.Length>0?counts:WebLabel(group.Status);
        }
        private static WebNode WebModel(WebScene scene,WebNode actor,string role)
        {
            if(actor==null)return null;var edge=scene.Edges.Find(delegate(WebEdge row){return row.Kind=="inference"&&row.FlowId==actor.FlowId&&row.Role==role;});return edge==null?null:scene.Nodes.Find(delegate(WebNode node){return node.Id==edge.To&&node.Kind=="model";});
        }
        private static string WebRoleStatus(WebScene scene,WebNode actor,string role){var edge=scene.Edges.Find(delegate(WebEdge row){return row.Kind=="inference"&&row.FlowId==actor.FlowId&&row.Role==role;});return edge==null||string.IsNullOrEmpty(edge.Status)?actor.Status:edge.Status;}
        internal List<WebCard> LayoutWeb(WebScene scene,double time)
        {
            var projectGroups=scene.Groups.FindAll(delegate(WebGroup group){return group.Kind=="project";});int count=projectGroups.Count,target=count<=2?0:count<=6?1:2;
            if(webDensity<0||target>webDensity){webDensity=target;webPendingDensity=-1;webSlots.Clear();}
            else if(target<webDensity){if(webPendingDensity!=target){webPendingDensity=target;webDensitySince=time;}else if(time-webDensitySince>=8){webDensity=target;webPendingDensity=-1;webSlots.Clear();}}
            else webPendingDensity=-1;
            var shown=projectGroups.GetRange(0,Math.Min(projectGroups.Count,webDensity==0?2:webDensity==1?6:12));var live=new HashSet<string>();foreach(var group in shown)live.Add(group.Id);
            foreach(string id in new List<string>(webSlots.Keys))if(!live.Contains(id))webSlots.Remove(id);
            foreach(string id in new List<string>(webSelected.Keys))if(!scene.Groups.Exists(delegate(WebGroup group){return group.Id==id;}))webSelected.Remove(id);
            var occupied=new HashSet<int>(webSlots.Values);foreach(var group in shown)if(!webSlots.ContainsKey(group.Id))for(int slot=0;slot<(webDensity==0?2:webDensity==1?6:12);slot++)if(occupied.Add(slot)){webSlots[group.Id]=slot;break;}
            var cards=new List<WebCard>();foreach(var group in shown){
                int slot=webSlots[group.Id],side=webDensity==0?slot:slot%2,row=webDensity==0?0:slot/2,x=side==0?18:318;var selected=SelectWebItem(group);
                if(webDensity>0){
                    var card=new WebCard{Bounds=new Rectangle(x,54+row*(webDensity==1?96:48),144,webDensity==1?90:43),GroupId=group.Id,Role=group.Kind=="project"?"PROJECT":group.Label,Label=group.Label,Status=group.Status,Summary=WebSummary(group,webDensity==2),Dense=webDensity==2};card.Nodes.Add(group.Id);foreach(var node in group.Items)card.Nodes.Add(node.Id);
                    if(selected!=null){if(selected.Kind=="codex_main")card.Body="CODEX CHAT\n"+selected.Label;else {var model=selected.Kind=="model"?selected:WebModel(scene,selected,selected.Kind=="analysis"?selected.Role:"worker");var review=WebModel(scene,selected,"reviewer");card.Body=model==null?(selected.Kind=="codex_child"?"Subagent · parent unknown":"Worker: "+WebHost(selected.Host)):WebModelName(model.Label)+"\n"+WebHost(model.Host);if(review!=null&&group.Status=="reviewing")card.Body+="\n"+WebModelName(review.Label)+" · "+WebHost(review.Host);}}
                    if(string.IsNullOrEmpty(card.Body))card.Body=WebCounts(group);if(!card.Dense&&group.Status!="test_failed"&&group.Status!="waiting"&&group.Status!="queued"&&group.Status!="finished"){int subs=WebCount(group,"codex_child");if(subs>0)card.Summary=subs+" subs · "+WebLabel(group.Status);else if(group.Activities.Count>1)card.Summary=WebLabel(group.Status)+" · +"+(group.Activities.Count-1);}cards.Add(card);continue;
                }
                var header=new WebCard{Bounds=new Rectangle(x,58,144,66),GroupId=group.Id,Role=group.Kind=="project"?"PROJECT":group.Label,Label=group.Label,Status=group.Status,Summary=WebSummary(group,false)};header.Nodes.Add(group.Id);cards.Add(header);if(selected==null)continue;
                bool codex=selected.Kind=="codex_main"||selected.Kind=="codex_child";var workerModel=codex?null:selected.Kind=="model"?selected:WebModel(scene,selected,selected.Kind=="analysis"?selected.Role:"worker");
                string primaryStatus=workerModel==null?selected.Status:WebRoleStatus(scene,selected,selected.Kind=="model"||selected.Kind=="analysis"?selected.Role:"worker");bool separateHost=!codex&&workerModel!=null&&selected.Kind=="worker"&&selected.Host.Length>0&&selected.Host!=workerModel.Host;
                var primary=new WebCard{Bounds=new Rectangle(x,156,144,104),GroupId=group.Id,Role=codex?(selected.Kind=="codex_main"?"CODEX CHAT":"SUBAGENT"):workerModel==null?AiOrigin(selected.Origin):separateHost?"WORKER · "+WebHost(selected.Host):"LOCAL MODEL",Label=codex?selected.Label:workerModel==null?selected.Label:WebModelName(workerModel.Label),Body=codex?(selected.Kind=="codex_child"&&selected.ParentId.Length==0?"PARENT UNKNOWN":""):(separateHost?"Model: ":"")+WebHost(workerModel==null?selected.Host:workerModel.Host),Status=primaryStatus,Summary=selected.Detail=="RESULT READ"?"RESULT READ":primaryStatus=="idle"&&selected.Kind=="worker"?"WORK COMPLETE":WebLabel(primaryStatus)};primary.Nodes.Add(selected.Id);if(workerModel!=null)primary.Nodes.Add(workerModel.Id);cards.Add(primary);
                if(codex){var children=group.Items.FindAll(delegate(WebNode node){return node.Kind=="codex_child"&&(node.ParentId.Length==0||"codex:"+node.ParentId==selected.Id);});if(children.Count>0){var sub=new WebCard{Bounds=new Rectangle(x,277,144,65),GroupId=group.Id,Role="SUBAGENTS",Label=children.Count+" active",Status="working",Summary=""};foreach(var child in children){sub.Nodes.Add(child.Id);if(WebRank(child.Status)<WebRank(sub.Status))sub.Status=child.Status;}sub.Summary=WebLabel(sub.Status);cards.Add(sub);}}
                else {var review=WebModel(scene,selected,"reviewer");if(review!=null&&group.Status=="reviewing"){var card=new WebCard{Bounds=new Rectangle(x,277,144,65),GroupId=group.Id,Role="REVIEW MODEL",Label=WebModelName(review.Label),Status="reviewing",Summary=WebHost(review.Host)+" · REVIEWING"};card.Nodes.Add(review.Id);foreach(var node in group.Items)if(node.Kind=="reviewer"&&node.FlowId==selected.FlowId)card.Nodes.Add(node.Id);cards.Add(card);}}
            }
            string[] sources={"source:violet_bridge","source:assistant","source:buddy","source:calibration","source:codex","source:local_ai"};for(int i=0;i<sources.Length;i++){
                var group=scene.Groups.Find(delegate(WebGroup row){return row.Id==sources[i];});if(group==null)continue;int tasks=group.Activities.Count;string countText=tasks.ToString(CultureInfo.InvariantCulture)+(group.Partial?"?":"");string cue=group.Status=="test_failed"?"FAILED":group.Status=="waiting"?"INPUT":group.Status=="reviewing"?"REVIEW":group.Status=="queued"?"QUEUED":group.Status=="finished"?"FINISHED":group.Status=="idle"?"READ":tasks==1?"task":"tasks";
                var badge=new WebCard{Bounds=new Rectangle(166,54+i*17,148,16),GroupId=group.Id,Label=group.Label+" · "+countText+" "+cue,Status=group.Status,Summary=WebSummary(group,true),Badge=true};badge.Nodes.Add(group.Id);foreach(var node in group.Items)badge.Nodes.Add(node.Id);cards.Add(badge);
            }
            if(webDensity==0&&shown.Count==1&&!cards.Exists(delegate(WebCard card){return card.Badge;})){
                foreach(var card in cards){
                    if(card.Role=="PROJECT")card.Bounds=new Rectangle(144,58,192,66);
                    else if(card.Role=="SUBAGENTS"||card.Role=="REVIEW MODEL")card.Bounds=new Rectangle(316,156,144,88);
                    else card.Bounds=new Rectangle(20,156,144,104);
                }
            }
            return cards;
        }
        private static PointF[] WebBorderPath(Rectangle rect){return new[]{new PointF(rect.Left+6,rect.Top),new PointF(rect.Right,rect.Top),new PointF(rect.Right,rect.Bottom-6),new PointF(rect.Right-6,rect.Bottom),new PointF(rect.Left,rect.Bottom),new PointF(rect.Left,rect.Top+6),new PointF(rect.Left+6,rect.Top)};}
        private static double WebPathLength(PointF[] path){double length=0;for(int i=1;i<path.Length;i++){double dx=path[i].X-path[i-1].X,dy=path[i].Y-path[i-1].Y;length+=Math.Sqrt(dx*dx+dy*dy);}return length;}
        private void WebParticles(Graphics g,PointF[] path,double time,Color color,int seed)
        {
            double length=WebPathLength(path);int count=Math.Max(3,Math.Min(18,(int)(length/28)));
            for(int n=0;n<count;n++){
                double d=(time*55+n*length/count+seed*13)%length;var point=PathPoint(path,d);
                for(int tail=1;tail<=3;tail++){
                    var trail=PathPoint(path,(d-tail*5+length)%length);float radius=1.7f-tail*.3f;
                    using(var brush=new SolidBrush(Color.FromArgb(155-tail*35,color)))g.FillEllipse(brush,trail.X-radius,trail.Y-radius,radius*2,radius*2);
                }
                using(var glow=new SolidBrush(Color.FromArgb(50,color)))g.FillEllipse(glow,point.X-5,point.Y-5,10,10);
                using(var glow=new SolidBrush(Color.FromArgb(95,color)))g.FillEllipse(glow,point.X-2.5f,point.Y-2.5f,5,5);
                using(var core=new SolidBrush(Color.FromArgb(235,White)))g.FillEllipse(core,point.X-1,point.Y-1,2,2);
                using(var brush=new SolidBrush(Color.FromArgb(255,n%3==0?White:color)))g.DrawString(((n+seed+(int)(time*1.2))&1).ToString(CultureInfo.InvariantCulture),webBinary,brush,point.X-3,point.Y-6,StringFormat.GenericTypographic);
            }
        }
        private static Color WebFlash(string status,double time){Color color=WebColor(status);if(status!="test_failed"&&status!="waiting"&&status!="finished")return color;double amount=.48+.52*(.5+.5*Math.Sin(time*Math.PI*2));return Color.FromArgb((int)(color.R*amount),(int)(color.G*amount),(int)(color.B*amount));}
        private void WebText(Graphics g,string text,Font font,Color color,RectangleF bounds,bool wrap=false)
        {
            using(var brush=new SolidBrush(color))using(var format=new StringFormat{Trimming=StringTrimming.EllipsisCharacter,FormatFlags=wrap?StringFormatFlags.LineLimit:StringFormatFlags.NoWrap})g.DrawString(text??"",font,brush,bounds,format);
        }
        private void DrawWebCard(Graphics g,WebCard card,double time)
        {
            Rectangle rect=card.Bounds;Color color=WebFlash(card.Status,time);using(var brush=new SolidBrush(Color.Black))g.FillRectangle(brush,rect);if(WebMoving(card.Status)){WebBorder(g,rect,Color.FromArgb(24,color),8);WebBorder(g,rect,Color.FromArgb(45,color),5);}WebBorder(g,rect,color,2);
            if(WebMoving(card.Status))WebParticles(g,WebBorderPath(rect),time,color,rect.Top+rect.Left);
            if(card.Badge){Text(g,card.Label,webMeta,color,new RectangleF(rect.Left+5,rect.Top+1,rect.Width-10,14),StringAlignment.Center);return;}
            int pad=7;if(card.Dense){WebText(g,card.Label,webDenseName,White,new RectangleF(rect.Left+pad,rect.Top+3,rect.Width-2*pad,16));WebText(g,card.Summary,webMeta,color,new RectangleF(rect.Left+pad,rect.Top+22,rect.Width-2*pad,15));return;}
            bool compact=rect.Height==90&&card.Role=="PROJECT";
            if(compact){Font labelFont=g.MeasureString(card.Label,webName).Width>rect.Width-2*pad?webDenseName:webName;WebText(g,card.Label,labelFont,White,new RectangleF(rect.Left+pad,rect.Top+4,rect.Width-2*pad,19));if((card.Body??"").StartsWith("CODEX CHAT\n",StringComparison.Ordinal)){WebText(g,"CODEX CHAT",webMeta,WebLilac,new RectangleF(rect.Left+pad,rect.Top+25,rect.Width-2*pad,14));WebText(g,card.Body.Substring(11),webDenseName,White,new RectangleF(rect.Left+pad,rect.Top+38,rect.Width-2*pad,33),true);}else {string[] lines=(card.Body??"").Split('\n');for(int i=0;i<Math.Min(3,lines.Length);i++)WebText(g,lines[i],webMeta,WebLilac,new RectangleF(rect.Left+pad,rect.Top+25+i*14,rect.Width-2*pad,14));}WebText(g,card.Summary,webMeta,color,new RectangleF(rect.Left+pad,rect.Bottom-17,rect.Width-2*pad,14));return;}
            WebText(g,card.Role,webMeta,color,new RectangleF(rect.Left+pad,rect.Top+4,rect.Width-2*pad,14));bool chat=card.Role=="CODEX CHAT";
            WebText(g,card.Label,chat?webTitle:webName,White,new RectangleF(rect.Left+pad,rect.Top+22,rect.Width-2*pad,chat?40:20),chat);
            if(!string.IsNullOrEmpty(card.Body))WebText(g,card.Body,webMeta,WebLilac,new RectangleF(rect.Left+pad,rect.Top+44,rect.Width-2*pad,15));
            if(!string.IsNullOrEmpty(card.Summary))WebText(g,card.Summary,webMeta,color,new RectangleF(rect.Left+pad,rect.Bottom-18,rect.Width-2*pad,15));
        }
        internal static PointF[] WebRoute(WebCard from,WebCard to)
        {
            if(from!=null&&from.Badge||to!=null&&to.Badge){
                var badge=from!=null&&from.Badge?from:to;var other=badge==from?to:from;
                float y=badge.Bounds.Top+badge.Bounds.Height/2;PointF[] path;
                if(other!=null){
                    bool left=other.Bounds.Right<=240;float side=left?Math.Max(badge.Bounds.Left,other.Bounds.Right+4):Math.Min(badge.Bounds.Right,other.Bounds.Left-4);
                    float edge=left?other.Bounds.Right:other.Bounds.Left,otherY=other.Bounds.Top+other.Bounds.Height/2;
                    path=new[]{new PointF(left?badge.Bounds.Left:badge.Bounds.Right,y),new PointF(side,y),new PointF(side,otherY),new PointF(edge,otherY)};
                } else {
                    var outside=new PointF(164,178);
                    path=new[]{new PointF(badge.Bounds.Left,y),new PointF(164,y),outside,VioletBackAnchor};
                }
                if(badge!=from)Array.Reverse(path);return path;
            }
            if(from==null||to==null){var card=from??to;var path=ProjectHubRoute(card);if(from==null)Array.Reverse(path);return path;}
            if(from.Role=="PROJECT"&&from.Bounds.Left==144||to.Role=="PROJECT"&&to.Bounds.Left==144){
                var project=from.Role=="PROJECT"?from:to;var branch=project==from?to:from;
                float x=branch.Bounds.Left+branch.Bounds.Width/2,y=project.Bounds.Top+project.Bounds.Height/2;
                var route=new[]{new PointF(x,branch.Bounds.Top),new PointF(x,y),new PointF(x<240?project.Bounds.Left:project.Bounds.Right,y)};
                if(project==from)Array.Reverse(route);return route;
            }
            if(from.Bounds.Left==20&&to.Bounds.Left==316||to.Bounds.Left==20&&from.Bounds.Left==316){
                bool forward=from.Bounds.Left<to.Bounds.Left;var left=forward?from:to;var right=forward?to:from;
                var route=new[]{new PointF(left.Bounds.Right,left.Bounds.Top+16),new PointF(196,left.Bounds.Top+16),new PointF(196,140),new PointF(right.Bounds.Left+right.Bounds.Width/2,140),new PointF(right.Bounds.Left+right.Bounds.Width/2,right.Bounds.Top)};
                if(!forward)Array.Reverse(route);return route;
            }
            if(from.Bounds.Left==to.Bounds.Left){WebCard upper=from.Bounds.Top<to.Bounds.Top?from:to,lower=upper==from?to:from;float x=upper.Bounds.Left+upper.Bounds.Width/2;return new[]{new PointF(x,upper.Bounds.Bottom),new PointF(x,lower.Bounds.Top)};}
            return new[]{new PointF(from.Bounds.Left+from.Bounds.Width/2,from.Bounds.Top),new PointF(from.Bounds.Left+from.Bounds.Width/2,132),new PointF(to.Bounds.Left+to.Bounds.Width/2,132),new PointF(to.Bounds.Left+to.Bounds.Width/2,to.Bounds.Top)};
        }
        private void DrawWebLinks(Graphics g,List<WebCard> cards,WebScene scene,Dictionary<string,object> state,double time)
        {
            var drawn=new HashSet<string>();foreach(var edge in scene.Edges){
                var froms=edge.From=="violet"?new List<WebCard>{null}:cards.FindAll(delegate(WebCard card){return card.Nodes.Contains(edge.From);});var tos=edge.To=="violet"?new List<WebCard>{null}:cards.FindAll(delegate(WebCard card){return card.Nodes.Contains(edge.To);});
                foreach(var from in froms)foreach(var to in tos){if(from==to||from!=null&&to!=null&&from.GroupId!=to.GroupId&&edge.Kind!="parent")continue;
                    string key=(from==null?"violet":from.GroupId+":"+from.Bounds.Top)+">"+(to==null?"violet":to.GroupId+":"+to.Bounds.Top);if(!drawn.Add(key))continue;
                    var target=to??from;
                    if((from==null||to==null)&&target.Role=="PROJECT"){
                        if(edge.Kind=="bridge"||edge.Kind=="inference"){
                            var sharedPath=ProjectHubRouteAvoiding(target,cards);if(from==null)Array.Reverse(sharedPath);
                            DrawWebObservedPulse(g,sharedPath,Values.Obj(Values.Get(state,"ai")),edge);
                        }
                        continue;
                    }
                    string status=target.Status;if(from!=null&&WebRank(from.Status)<WebRank(status))status=from.Status;if(edge.Kind=="inference"&&!string.IsNullOrEmpty(edge.Status))status=edge.Status;Color color=WebFlash(status,time);PointF[] path=WebRoute(from,to);
                    using(var pen=new Pen(Color.FromArgb(WebMoving(status)?32:18,color),3))g.DrawLines(pen,path);using(var pen=new Pen(color,2))g.DrawLines(pen,path);if(WebMoving(status))WebParticles(g,path,time,color,key.GetHashCode()&255);
                    if(edge.Kind=="inference"||edge.Kind=="bridge")DrawWebObservedPulse(g,path,Values.Obj(Values.Get(state,"ai")),edge);
                }
            }
        }
        private void DrawWebObservedPulse(Graphics g,PointF[] path,Dictionary<string,object> ai,WebEdge edge)
        {
            bool inference=edge.Kind=="inference";double length=WebPathLength(path);foreach(var pulse in Values.Rows(Values.Get(ai,"pulses"))){
                if(Values.Text(pulse,"flowId")!=edge.FlowId)continue;string kind=Values.Text(pulse,"kind");bool sent=kind=="request_sent",received=kind=="response_received",read=kind=="result_read",directRead=inference&&read&&(edge.Role=="analysis"||edge.Role=="calibration");
                if(inference?(!sent&&!received&&!directRead)||!directRead&&(Values.Text(pulse,"host")!=edge.Host||Values.Text(pulse,"role")!=edge.Role):!read)continue;
                double age=VioletObservationAge(Values.Text(pulse,"occurredAt"));if(double.IsNaN(age)||age<0||age>=3)continue;double progress=age/3;if(received||read)progress=1-progress;
                for(int tail=0;tail<4;tail++){double point=progress+(received||read?1:-1)*tail*.035;if(point<0||point>1)continue;var p=PathPoint(path,length*point);Binary(g,p.X,p.Y,tail==0?BinaryHead:Lilac,1-tail*.2,tail+Values.Int(pulse,"sequence"));}
            }
        }
        internal static double? WebMetric(Dictionary<string,object> metric,double maximum)
        {
            object raw=Values.Get(metric,"value");double? value=Values.Number(metric,"value");string status=Values.Text(metric,"status");return raw is bool||!value.HasValue||double.IsNaN(value.Value)||double.IsInfinity(value.Value)||value.Value<0||value.Value>maximum||Values.Bool(metric,"stale")||!Fresh(Values.Text(metric,"observedAt"),5)||(status!="live"&&status!="available"&&status!="verified")?null:value;
        }
        private void DrawWebResources(Graphics g,Dictionary<string,object> state)
        {
            var display=Values.Obj(Values.Get(state,"coolerDisplay"));bool fresh=SnapshotFresh(state);var resources=Values.Rows(Values.Get(display,"resources"));int index=0;
            foreach(string host in new[]{"local","msi"}){
                var row=resources.Find(delegate(Dictionary<string,object> item){return Values.Text(item,"id")==host;})??new Dictionary<string,object>();var local=host=="local"?Values.Obj(Values.Get(state,"temperatures")):new Dictionary<string,object>();
                var cpu=Values.Obj(Values.Get(row,"cpuTemperature"));var gpu=Values.Obj(Values.Get(row,"gpuTemperature"));if(host=="local"){if(TemperatureText(cpu)=="—")cpu=Values.Obj(Values.Get(local,"cpu"));if(TemperatureText(gpu)=="—")gpu=Values.Obj(Values.Get(local,"gpu"));}
                string cpuText=fresh?TemperatureText(cpu):"—",gpuText=fresh?TemperatureText(gpu):"—";double? load=fresh?WebMetric(Values.Obj(Values.Get(row,"gpuLoad")),100):null,used=fresh?WebMetric(Values.Obj(Values.Get(row,"vramUsed")),1125899906842624):null,total=fresh?WebMetric(Values.Obj(Values.Get(row,"vramTotal")),1125899906842624):null;
                var rect=new Rectangle(index==0?18:248,350,214,114);WebBorder(g,rect,WebPurple,2);
                WebText(g,WebHost(host),webName,WebLilac,new RectangleF(rect.Left+8,rect.Top+3,198,18));
                WebText(g,"CPU",webDenseName,WebLilac,new RectangleF(rect.Left+8,rect.Top+23,90,16));WebText(g,"GPU",webDenseName,WebLilac,new RectangleF(rect.Left+115,rect.Top+23,90,16));
                DrawWebTemperature(g,cpuText,new RectangleF(rect.Left+5,rect.Top+35,101,48));DrawWebTemperature(g,gpuText,new RectangleF(rect.Left+112,rect.Top+35,101,48));
                using(var pen=new Pen(WebPurple,1.5f))g.DrawLine(pen,rect.Left+107,rect.Top+23,rect.Left+107,rect.Top+81);
                WebText(g,load.HasValue?"GPU LOAD":"GPU LOAD —",webMeta,WebLilac,new RectangleF(rect.Left+8,rect.Top+84,72,14));
                if(load.HasValue){var bar=new Rectangle(rect.Left+78,rect.Top+88,126,6);using(var pen=new Pen(WebPurple,1.5f))g.DrawRectangle(pen,bar);using(var brush=new SolidBrush(WebLilac))g.FillRectangle(brush,bar.Left+1,bar.Top+1,(float)((bar.Width-2)*load.Value/100),bar.Height-1);}
                string vram=used.HasValue&&total.HasValue&&total.Value>0&&used.Value<=total.Value?"VRAM "+(used.Value/1073741824).ToString("0.#",CultureInfo.InvariantCulture)+" / "+(total.Value/1073741824).ToString("0.#",CultureInfo.InvariantCulture)+" GB":"VRAM —";WebText(g,vram,webMeta,White,new RectangleF(rect.Left+8,rect.Top+98,198,13));index++;
            }
        }
        private void DrawWebTemperature(Graphics g,string value,RectangleF bounds){
            using(var large=new Font("Segoe UI",42,FontStyle.Bold,GraphicsUnit.Pixel))using(var fit=new Font("Segoe UI",34,FontStyle.Bold,GraphicsUnit.Pixel))using(var unit=new Font("Segoe UI",22,FontStyle.Bold,GraphicsUnit.Pixel))using(var brush=new SolidBrush(White)){
                if(!value.EndsWith("°C",StringComparison.Ordinal)){g.DrawString(value,large,brush,bounds.Left,bounds.Top,StringFormat.GenericTypographic);return;}
                string number=value.Substring(0,value.Length-2);float suffix=g.MeasureString("°C",unit,1000,StringFormat.GenericTypographic).Width;
                Font font=g.MeasureString(number,large,1000,StringFormat.GenericTypographic).Width+suffix+1<=bounds.Width?large:fit;
                float width=g.MeasureString(number,font,1000,StringFormat.GenericTypographic).Width;
                g.DrawString(number,font,brush,bounds.Left,bounds.Top,StringFormat.GenericTypographic);g.DrawString("°C",unit,brush,bounds.Left+width+1,bounds.Top+(font.Size-unit.Size)*.8f,StringFormat.GenericTypographic);
            }
        }
        private void WebBorder(Graphics g,Rectangle rect,Color color,float width){using(var pen=new Pen(color,width)){pen.LineJoin=LineJoin.Round;g.DrawLines(pen,WebBorderPath(rect));}}
        // All attachment lines continue behind the original opaque avatar.
        private static readonly PointF VioletBackAnchor=new PointF(240,288);
        private static PointF[] ProjectHubRoute(WebCard card){
            PointF port;
            if(card.Bounds.Left<240&&card.Bounds.Right>240){port=new PointF(240,card.Bounds.Bottom);}
            else {
                bool left=card.Bounds.Right<=240;
                float y=Math.Max(card.Bounds.Top+12,Math.Min(card.Bounds.Bottom-12,288));
                port=new PointF(left?card.Bounds.Right:card.Bounds.Left,y);
            }
            return new[]{port,VioletBackAnchor};
        }
        private static bool WebSegmentEntersCard(PointF a,PointF b,Rectangle rect){
            double enter=0,exit=1;double[] origin={a.X,a.Y},delta={b.X-a.X,b.Y-a.Y},minimum={rect.Left+.05,rect.Top+.05},maximum={rect.Right-.05,rect.Bottom-.05};
            for(int axis=0;axis<2;axis++){
                if(Math.Abs(delta[axis])<.0001){if(origin[axis]<=minimum[axis]||origin[axis]>=maximum[axis])return false;continue;}
                double first=(minimum[axis]-origin[axis])/delta[axis],last=(maximum[axis]-origin[axis])/delta[axis];if(first>last){double swap=first;first=last;last=swap;}
                enter=Math.Max(enter,first);exit=Math.Min(exit,last);if(enter>exit)return false;
            }
            return enter<=exit;
        }
        private static PointF[] ProjectHubRouteAvoiding(WebCard card,List<WebCard> cards){
            var direct=ProjectHubRoute(card);bool blocked=false;int lowest=0;
            foreach(var badge in cards){if(!badge.Badge)continue;lowest=Math.Max(lowest,badge.Bounds.Bottom);if(WebSegmentEntersCard(direct[0],direct[1],badge.Bounds))blocked=true;}
            if(!blocked)return direct;
            // The four-pixel gutters separate all side cards from the central
            // source summaries. Bend below the last summary, then join the
            // same visible hub port; no layout or relationship is changed.
            float gutter=card.Bounds.Left<240?164:316,elbow=Math.Max(lowest+8,178);
            return new[]{direct[0],new PointF(gutter,direct[0].Y),new PointF(gutter,elbow),direct[1]};
        }
        private void DrawWebProjectAssociations(Graphics g,List<WebCard> cards,double time){
            // One visible membership spoke per project; never a control/traffic assertion.
            var drawn=new HashSet<string>();foreach(var card in cards){
                if(card.Role!="PROJECT"||card.Badge||!drawn.Add(card.GroupId))continue;
                PointF[] path=ProjectHubRouteAvoiding(card,cards);Color color=WebFlash(card.Status,time);
                using(var halo=new Pen(Color.FromArgb(35,color),6))g.DrawLines(halo,path);
                using(var pen=new Pen(color,2))g.DrawLines(pen,path);
                if(WebMoving(card.Status))WebParticles(g,path,time,color,17+card.Bounds.Top);
            }
        }
        private void DrawVioletWeb(Graphics g,Dictionary<string,object> state,Dictionary<string,object> config,double time)
        {
            WebBorder(g,new Rectangle(8,8,464,464),WebPurple,3);
            using(var heading=new Font("Segoe UI",23,FontStyle.Bold,GraphicsUnit.Pixel))WebText(g,"THIS-MACHINE RUNS VIOLET",heading,WebLilac,new RectangleF(20,16,443,32));
            var scene=BuildWebScene(state,config,time);var cards=LayoutWeb(scene,time);
            var saved=g.Save();g.SetClip(new Rectangle(12,49,456,301));DrawWebProjectAssociations(g,cards,time);DrawWebLinks(g,cards,scene,state,time);DrawViolet(g,state,config,time,new Rectangle(166,187,148,160));foreach(var card in cards)DrawWebCard(g,card,time);g.Restore(saved);
            if(scene.Groups.Count==0){string label=scene.ActivityAvailable&&!scene.Partial?"NO ACTIVE WORK":"ACTIVITY UNAVAILABLE";Text(g,label,webMeta,Dim,new RectangleF(144,161,192,18),StringAlignment.Center);}
            DrawWebResources(g,state);
        }
        internal Bitmap DrawFrame(Dictionary<string,object> state,Dictionary<string,object> config,double time)
        {
            var bitmap=new Bitmap(480,480,PixelFormat.Format24bppRgb);
            using(var g=Graphics.FromImage(bitmap)){
                g.Clear(Color.Black);g.SmoothingMode=SmoothingMode.AntiAlias;g.TextRenderingHint=System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                bool fresh=SnapshotFresh(state);
                var projects=Projects(state);int focus=FocusIndex(projects,config,time),page=focus/3*3;
                bool web=Values.Text(config,"layout","command_deck")=="violet_web";
                if(web)DrawVioletWeb(g,state,config,time);
                else {
                Border(g,new Rectangle(6,6,468,468),Color.FromArgb(73,36,94));
                DrawAiDeck(g,state,time);
                DrawViolet(g,state,config,time,new Rectangle(174,9,132,143));
                string overall="unknown",source="NO LIVE STATUS";
                if(fresh){foreach(string priority in new[]{"test_failed","waiting","working"}){foreach(var project in projects)if(ProjectState(project)==priority){overall=priority;break;}if(overall!="unknown")break;}
                    var violet=Values.Obj(Values.Get(state,"violet"));bool reported=Values.Bool(violet,"connected")&&!Values.Bool(violet,"stale");
                    source=overall=="test_failed"?"CHECK RESULT":overall!="unknown"?"PROJECT ACTIVITY":reported?Values.Text(violet,"source","UNVERIFIED").ToUpperInvariant():"NO LIVE STATUS";
                }
                Text(g,source,detail,ActivityHeading,new RectangleF(310,60,158,26));var attention=Values.Obj(Values.Get(state,"attention"));int pending=Values.Int(attention,"pendingCount");
                string alerts=fresh&&Values.Bool(attention,"available")?(pending==0?"No pending alerts":pending+" alert"+(pending==1?"":"s")+" to review"):"Alerts unavailable";
                Text(g,alerts,detail,pending>0&&fresh?Alert:Color.FromArgb(226,177,255),new RectangleF(310,89,158,25));
                var subagents=Values.Obj(Values.Get(state,"subagents"));Text(g,"SUBAGENTS "+SubagentText(subagents,fresh),detail,SubagentColor(subagents,fresh),new RectangleF(310,118,158,25));
                for(int slot=0;slot<3&&page+slot<projects.Count;slot++)DrawWire(g,87+slot*153,fresh?ProjectState(projects[page+slot]):"stale",slot,time);
                for(int slot=0;slot<3&&page+slot<projects.Count;slot++)DrawRoom(g,projects[page+slot],new Rectangle(18+slot*153,177,138,155),page+slot==focus,time,slot,fresh);
                }
                if(!web){var selected=projects[focus];string status=fresh?ProjectState(selected):"stale";Border(g,new Rectangle(18,349,444,42),StateColor(status));
                string summary=status=="stale"?"Activity update stale":status=="unknown"?"Status unavailable":status=="test_failed"?"Build/test check failed":status=="waiting"?"Waiting for your input":status=="turn_finished"?"Chat turn finished":Human(StatusLabel(status));
                string paging=!web&&projects.Count>3?" ["+(page/3+1)+"/"+((projects.Count+2)/3)+"]":"";
                Text(g,Values.Text(selected,"label",Values.Text(selected,"id"))+" · "+summary+paging,roomFont,SummaryText,new RectangleF(31,358,418,24));
                var temperatures=Values.Obj(Values.Get(state,"temperatures"));DrawTemperature(g,"CPU",fresh?Values.Obj(Values.Get(temperatures,"cpu")):new Dictionary<string,object>(),new Rectangle(18,410,214,49));DrawTemperature(g,"GPU",fresh?Values.Obj(Values.Get(temperatures,"gpu")):new Dictionary<string,object>(),new Rectangle(248,410,214,49));
                DrawVioletStatus(g,state);}
                if(Values.Bool(config,"diagnostic")){int x=20+(int)(time*65)%400;using(var brush=new SolidBrush(Lilac))g.FillRectangle(brush,x,466,32,3);Text(g,"TEST FRAME "+sequence,small,White,new RectangleF(16,16,145,20));}
                else if(Values.Bool(state,"demo"))Text(g,"DEMO",small,Amber,new RectangleF(16,16,70,20));
            }return bitmap;
        }
        private void DrawRoom(Graphics g,Dictionary<string,object> project,Rectangle rect,bool focus,double time,int index,bool fresh)
        {
            string status=fresh?ProjectState(project):"stale";Color color=StateColor(status);bool active=status=="working",motion=active||status=="waiting"||status=="test_failed";
            Border(g,rect,motion?color:Color.FromArgb(105,87,127));Text(g,Values.Text(project,"label",Values.Text(project,"id")),roomFont,RoomText,new RectangleF(rect.Left+10,rect.Top+8,rect.Width-20,24));
            using(var pen=new Pen(Color.FromArgb(80,54,95),1)){g.DrawLine(pen,rect.Left+9,rect.Top+38,rect.Right-9,rect.Top+38);g.DrawLine(pen,rect.Left+9,rect.Bottom-36,rect.Right-9,rect.Bottom-36);}
            if(motion){var saved=g.Save();g.SetClip(new Rectangle(rect.Left+8,rect.Top+44,rect.Width-16,rect.Height-82));int count=active?24:18;
                for(int n=0;n<count;n++){double life=(time*(active?.65:.4)+n*.618+index*.29)%1,angle=n*2.39996+time*(n%2==1?-.4:.4),radius=17+life*34;Binary(g,rect.Left+rect.Width/2+(float)(Math.Cos(angle)*radius),rect.Top+85+(float)(Math.Sin(angle)*radius*.58-life*6),n%8==0?AgentParticle:color,Math.Sin(life*Math.PI)*.8,n+index+(int)(time*1.5));}
                if(active)for(int n=0;n<6;n++){float yy=rect.Top+45+(float)((time*30+n*19+index*27)%(rect.Height-82));Binary(g,rect.Left+13+n*20,yy,color,.32,n+(int)time);Binary(g,rect.Left+13+n*20,yy-11,color,.16,n+1+(int)time);}g.Restore(saved);
            }
            var subagents=Values.Obj(Values.Get(project,"subagents"));
            using(var brush=new SolidBrush(Color.Black))g.FillRectangle(brush,rect.Left+9,rect.Top+40,rect.Width-18,17);
            Text(g,"SUBS "+SubagentText(subagents,fresh),small,SubagentColor(subagents,fresh),new RectangleF(rect.Left+9,rect.Top+40,rect.Width-18,17),StringAlignment.Center);
            // A single aggregate activity figure, independent of the number of observed agents.
            int cx=rect.Left+rect.Width/2,y=rect.Top+59;float bob=active?(float)Math.Sin(time*7+index)*1.2f:0;
            using(var brush=new SolidBrush(Color.FromArgb(39,26,55)))g.FillRectangle(brush,cx-25,y+25,50,6);
            using(var brush=new SolidBrush(active?Color.FromArgb(113,51,156):Color.FromArgb(81,71,94)))g.FillRectangle(brush,cx-16,y,32,19);
            using(var brush=new SolidBrush(Color.FromArgb(16,11,24)))g.FillRectangle(brush,cx-13,y+3,26,12);
            using(var brush=new SolidBrush(color)){for(int k=0;k<3;k++)g.FillRectangle(brush,cx-10,y+5+k*3,active?6+((int)(time*6+k*5)%15):10,1);g.FillEllipse(brush,cx-5,y+31+bob,10,10);g.FillRectangle(brush,cx-6,y+43+bob,12,13);if(active){g.FillRectangle(brush,cx-10,y+47+(int)(time*6)%2,4,3);g.FillRectangle(brush,cx+6,y+48-(int)(time*6)%2,4,3);}}
            Text(g,StatusLabel(status),mono,color,new RectangleF(rect.Left+5,rect.Bottom-30,rect.Width-10,23),StringAlignment.Center);
            if(active){float x=rect.Left+10+(float)((time*.42+index*.3)%1)*(rect.Width-36);using(var pen=new Pen(Color.FromArgb(239,199,255),1.5f))g.DrawLine(pen,x,rect.Bottom,x+15,rect.Bottom);}
            if(focus)using(var brush=new SolidBrush(color))g.FillRectangle(brush,rect.Left+8,rect.Top+5,3,2);
        }
        private void DrawTemperature(Graphics g,string label,Dictionary<string,object> sensor,Rectangle rect)
        {
            Border(g,rect,Color.FromArgb(163,65,223));string value=TemperatureText(sensor);float a=g.MeasureString(label,temperatureLabel,1000,StringFormat.GenericTypographic).Width,b=g.MeasureString(value,temperatureValue,1000,StringFormat.GenericTypographic).Width;
            float left=rect.Left+(rect.Width-a-b-10)/2;
            using(var brush=new SolidBrush(TemperatureTextColor))g.DrawString(label,temperatureLabel,brush,new PointF(left,rect.Top+10),StringFormat.GenericTypographic);
            using(var brush=new SolidBrush(value=="—"?Dim:Lilac))g.DrawString(value,temperatureValue,brush,new PointF(left+a+10,rect.Top+7),StringFormat.GenericTypographic);
        }
        private void DrawViolet(Graphics g,Dictionary<string,object> state,Dictionary<string,object> config,double time,Rectangle target)
        {
            if(Values.Text(config,"mode")=="live_mini"){
                string reason;using(var capture=CaptureMini(config,out reason)){string next=capture==null?"unavailable":"live";if(next!=captureStatus){captureStatus=next;Emit("capture","status",next,"valid",capture!=null,"reason",reason,"message",reason);}if(capture!=null){g.DrawImage(capture,target);return;}}
                Text(g,"MINI",small,Amber,new RectangleF(target.Left,target.Top+20,target.Width,20),StringAlignment.Center);Text(g,"UNAVAILABLE",small,Dim,new RectangleF(target.Left-10,target.Top+40,target.Width+20,20),StringAlignment.Center);return;
            }
            if(captureStatus!="dashboard"){captureStatus="dashboard";Emit("capture","status","dashboard","valid",false,"reason",null,"message",null);}
            if(violetAnimations.Draw(g,state,time,target,connected&&Interlocked.Read(ref acknowledged)>0))return;
            if(!animationFallbackReported){animationFallbackReported=true;Emit("animation","status","fallback","message","Embedded animation assets unavailable; using original Violet artwork.");}
            string path=Values.Text(config,"violetAtlasPath");if(path!=atlasPath){if(violetAtlas!=null){violetAtlas.Dispose();violetAtlas=null;}atlasPath=path;try {if(File.Exists(path)){using(var file=Image.FromFile(path)){if(file.Width==1536&&file.Height==2288)violetAtlas=new Bitmap(file);}}}catch{}}
            if(violetAtlas==null){Border(g,new Rectangle(target.Left+15,target.Top+9,target.Width-30,target.Height-18),Purple);Text(g,"VIOLET",small,Lilac,new RectangleF(target.Left,target.Top+32,target.Width,25),StringAlignment.Center);return;}
            var dot=Values.Obj(Values.Get(state,"violet"));bool dotReported=Values.Bool(dot,"connected")&&!Values.Bool(dot,"stale");string dotActivity=Values.Text(dot,"activity",Values.Text(dot,"status"));
            string overall=dotReported&&Waiting(dotActivity)?"waiting":dotReported&&(dotActivity=="working"||dotActivity=="running")?"working":"unknown";
            foreach(var project in Projects(state)){string status=ProjectState(project);if(status=="test_failed"){overall="failed";break;}if(status=="waiting"&&overall!="failed")overall="waiting";else if(status=="working"&&overall=="unknown")overall="working";}
            if(!SnapshotFresh(state))overall="unknown";
            int row=overall=="failed"?5:overall=="waiting"?6:overall=="working"?7:0,count=row==5?8:6,column=(int)(time*5)%count;
            g.DrawImage(violetAtlas,target,new Rectangle(column*192,row*208,192,208),GraphicsUnit.Pixel);
        }

        [StructLayout(LayoutKind.Sequential)]private struct RECT{internal int Left,Top,Right,Bottom;}
        [StructLayout(LayoutKind.Sequential)]private struct POINT{internal int X,Y;internal POINT(int x,int y){X=x;Y=y;}}
        private delegate bool EnumProc(IntPtr hwnd,IntPtr parameter);
        [DllImport("user32.dll")]private static extern bool EnumWindows(EnumProc callback,IntPtr p);
        [DllImport("user32.dll")]private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")]private static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")]private static extern bool GetWindowRect(IntPtr hwnd,out RECT r);
        [DllImport("user32.dll",CharSet=CharSet.Unicode)]private static extern int GetWindowText(IntPtr hwnd,StringBuilder text,int count);
        [DllImport("user32.dll")]private static extern IntPtr WindowFromPoint(POINT point);
        [DllImport("user32.dll")]private static extern bool IsChild(IntPtr parent,IntPtr child);
        [DllImport("user32.dll")]private static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint id);
        [DllImport("user32.dll")]private static extern bool SetProcessDPIAware();
        internal static void Dpi(){SetProcessDPIAware();}
        private void CaptureSources()
        {
            var sources=new List<Dictionary<string,object>>();EnumWindows(delegate(IntPtr hwnd,IntPtr unused){if(!IsWindowVisible(hwnd)||IsIconic(hwnd))return true;RECT r;if(!GetWindowRect(hwnd,out r)||r.Right-r.Left<30||r.Bottom-r.Top<30)return true;var title=new StringBuilder(512);GetWindowText(hwnd,title,512);if(title.Length==0)return true;uint pid;GetWindowThreadProcessId(hwnd,out pid);string process="";try{using(var p=Process.GetProcessById((int)pid))process=p.ProcessName;}catch{}sources.Add(new Dictionary<string,object>{{"id",hwnd.ToInt64().ToString(CultureInfo.InvariantCulture)},{"title",title.ToString()},{"process",process},{"bounds",new Dictionary<string,object>{{"x",r.Left},{"y",r.Top},{"width",r.Right-r.Left},{"height",r.Bottom-r.Top}}}});return sources.Count<100;},IntPtr.Zero);Emit("capture_sources","sources",sources);
        }
        private Bitmap CaptureMini(Dictionary<string,object> config,out string reason)
        {
            reason="Select and calibrate the visible mini window.";long id;
            if(!Values.Bool(config,"captureEnabled")||!long.TryParse(Values.Text(config,"captureWindowId"),out id)||id==0)return null;
            IntPtr hwnd=new IntPtr(id);RECT actual;if(!IsWindowVisible(hwnd)||IsIconic(hwnd)||!GetWindowRect(hwnd,out actual)){reason="Mini window is hidden or unavailable.";return null;}
            var expected=Values.Obj(Values.Get(config,"captureWindowBounds"));if(actual.Left!=Values.Int(expected,"x",int.MinValue)||actual.Top!=Values.Int(expected,"y",int.MinValue)||actual.Right-actual.Left!=Values.Int(expected,"width")||actual.Bottom-actual.Top!=Values.Int(expected,"height")){reason="Mini window moved. Recalibrate it.";return null;}
            var crop=Values.Obj(Values.Get(config,"captureRect"));int x=Values.Int(crop,"x"),y=Values.Int(crop,"y"),w=Values.Int(crop,"width"),h=Values.Int(crop,"height");if(w<16||h<16||w>1000||h>1000||x<actual.Left||y<actual.Top||x+w>actual.Right||y+h>actual.Bottom){reason="Capture crop is outside the selected mini window.";return null;}
            bool covered=false,found=false;EnumWindows(delegate(IntPtr above,IntPtr unused){if(above==hwnd){found=true;return false;}if(!IsWindowVisible(above)||IsIconic(above))return true;RECT bounds;if(GetWindowRect(above,out bounds)&&bounds.Left<x+w&&bounds.Right>x&&bounds.Top<y+h&&bounds.Bottom>y){covered=true;return false;}return true;},IntPtr.Zero);
            if(covered||!found){reason="Mini capture area is covered. Restore it before mirroring.";return null;}
            try {var bitmap=new Bitmap(w,h,PixelFormat.Format24bppRgb);using(var graphics=Graphics.FromImage(bitmap)){graphics.Clear(Color.Black);graphics.CopyFromScreen(x,y,0,0,new Size(w,h),CopyPixelOperation.SourceCopy);}
                foreach(var point in new[]{new Point(1,1),new Point(w-2,1),new Point(1,h-2),new Point(w-2,h-2)}){Color color=bitmap.GetPixel(point.X,point.Y);if(color.R>8||color.G>8||color.B>8){bitmap.Dispose();reason="Place the mini over the black capture area and leave black margins around the crop.";return null;}}
                reason=null;return bitmap;}catch{reason="Desktop capture is unavailable.";return null;}
        }
        [StructLayout(LayoutKind.Sequential)]private struct JOB_BASIC {internal long ProcessTime,JobTime;internal uint LimitFlags;internal UIntPtr MinWorkingSet,MaxWorkingSet;internal uint ActiveProcesses;internal UIntPtr Affinity;internal uint PriorityClass,SchedulingClass;}
        [StructLayout(LayoutKind.Sequential)]private struct IO_COUNTERS {internal ulong ReadOperations,WriteOperations,OtherOperations,ReadBytes,WriteBytes,OtherBytes;}
        [StructLayout(LayoutKind.Sequential)]private struct JOB_EXTENDED {internal JOB_BASIC Basic;internal IO_COUNTERS Io;internal UIntPtr ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr CreateJobObject(IntPtr security,string name);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool SetInformationJobObject(IntPtr job,int info,IntPtr data,uint length);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
        [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct STARTUPINFO {internal int Size;internal string Reserved,Desktop,Title;internal int X,Y,Width,Height,CharsX,CharsY,Fill,Flags;internal short ShowWindow,ReservedSize;internal IntPtr ReservedPointer,StdInput,StdOutput,StdError;}
        [StructLayout(LayoutKind.Sequential)]private struct PROCESS_INFORMATION {internal IntPtr Process,Thread;internal uint ProcessId,ThreadId;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcess(string application,StringBuilder command,IntPtr processSecurity,IntPtr threadSecurity,bool inherit,uint flags,IntPtr environment,string directory,ref STARTUPINFO startup,out PROCESS_INFORMATION information);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool TerminateProcess(IntPtr process,uint code);
        private static IntPtr NewHelperJob()
        {
            IntPtr job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)throw new IOException("Cannot create helper containment.");
            var limits=new JOB_EXTENDED();limits.Basic.LimitFlags=0x2000;int size=Marshal.SizeOf(typeof(JOB_EXTENDED));IntPtr memory=Marshal.AllocHGlobal(size);
            try{Marshal.StructureToPtr(limits,memory,false);if(!SetInformationJobObject(job,9,memory,(uint)size))throw new IOException("Cannot set helper containment.");return job;}catch{CloseHandle(job);throw;}finally{Marshal.FreeHGlobal(memory);}
        }
        private void ValidateHelperPins(bool sensors)
        {
            var config=Settings();var pins=Values.Obj(Values.Get(config,"helperPins"));bool strict=Values.Bool(config,"requireHelperPins");
            string[] files=sensors?new[]{"HWINFO.exe","HWiNFO64.dll"}:new[]{"USBLCDNEW.exe","USBLCDNEW.dll","USBLCDNEW.runtimeconfig.json","LibUsbDotNet.LibUsbDotNet.dll"};
            if(pins.Count==0&&!strict){if(sensors)return;files=new[]{"USBLCDNEW.dll"};pins["USBLCDNEW.dll"]=Values.Text(config,"helperSha256");}
            foreach(string name in files){string expected=Values.Text(pins,name);if(expected.Length!=64)throw new InvalidOperationException("Vendor helper files need a reviewed setup pin.");using(var sha=SHA256.Create())using(var file=File.OpenRead(Path.Combine(@"C:\Program Files\TRCCCAP",name))){string actual=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","");if(!string.Equals(actual,expected,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Installed vendor helper differs from its reviewed pin.");}}
        }
        private void StartSensors()
        {
            lock(sensorGate){
                if(sensorElevationRequested||sensorBrokerActive||ProcessExists("HWINFO"))return;
                try {
                    ValidateHelperPins(true);if(ownedSensorMap==null)ownedSensorMap=MemoryMappedFile.CreateNew("shareMemory_SysInfo",2097152);sensorJob=NewHelperJob();
                    ownedSensor=Process.Start(new ProcessStartInfo(@"C:\Program Files\TRCCCAP\HWINFO.exe"){WorkingDirectory=@"C:\Program Files\TRCCCAP",UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardOutput=true,RedirectStandardError=true,RedirectStandardInput=true});
                    ownedSensorStarted=DateTime.UtcNow;ownedSensor.OutputDataReceived+=delegate{};ownedSensor.ErrorDataReceived+=delegate{};ownedSensor.BeginOutputReadLine();ownedSensor.BeginErrorReadLine();
                    if(!AssignProcessToJobObject(sensorJob,ownedSensor.Handle)){if(!ownedSensor.HasExited)ownedSensor.Kill();throw new IOException("Cannot contain sensor helper.");}
                }catch {StopOwnedNormalSensor();Emit("sensors","temperatures",new Dictionary<string,object>{{"cpu",MissingSensor()},{"gpu",MissingSensor()},{"status","unavailable"},{"reason","The reviewed sensor helper could not start without changing system privileges."}});}
            }
        }
        private static bool ProcessExists(string name){var found=Process.GetProcessesByName(name);bool exists=found.Length>0;foreach(var p in found)p.Dispose();return exists;}
        private void StopOwnedNormalSensor()
        {
            var sensor=ownedSensor;ownedSensor=null;if(sensor!=null){try{if(!sensor.HasExited){sensor.Kill();sensor.WaitForExit(1500);}}catch{}finally{sensor.Dispose();}}
            if(sensorJob!=IntPtr.Zero){CloseHandle(sensorJob);sensorJob=IntPtr.Zero;}
        }
        private void CloseSensorMap(){if(ownedSensorMap!=null){ownedSensorMap.Dispose();ownedSensorMap=null;}}
        private void StopSensorBroker()
        {
            sensorElevationRequested=false;NamedPipeServerStream pipe;lock(sensorGate)pipe=sensorPipe;if(pipe!=null)try{pipe.Dispose();}catch{}
        }
        private void RequestSensorPermission(bool enable)
        {
            if(!enable){StopSensorBroker();Emit("sensor_permission","status","stopped","message","Sensor permission helper stopped.");return;}
            if(!connected){Emit("sensor_permission","status","unavailable","message","Connect the cooler before enabling live temperatures.");return;}
            lock(sensorGate){if(sensorBrokerThread!=null&&sensorBrokerThread.IsAlive)return;sensorElevationRequested=true;sensorBrokerThread=new Thread(SensorBrokerLoop){IsBackground=true,Name="MASTER sensor permission"};sensorBrokerThread.Start();}
        }
        private static bool Hex(string value,int length){if(value==null||value.Length!=length)return false;foreach(char c in value)if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F')))return false;return true;}
        private static string FileHash(Stream file){using(var sha=SHA256.Create()){file.Position=0;return BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant();}}
        private static FileStream PinnedSensor(string name,string pin)
        {
            if((name!="HWINFO.exe"&&name!="HWiNFO64.dll")||!Hex(pin,64))throw new InvalidOperationException("Live temperatures need reviewed sensor helper pins.");
            var file=File.Open(Path.Combine(@"C:\Program Files\TRCCCAP",name),FileMode.Open,FileAccess.Read,FileShare.Read);try{if(!string.Equals(FileHash(file),pin,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Installed sensor helper differs from its reviewed pin.");return file;}catch{file.Dispose();throw;}
        }
        private static string SelfPath(){return Path.GetFullPath(typeof(Worker).Assembly.Location);}
        private static FileStream WorkerConfiguration(string path)
        {
            const string expected="<?xml version=\"1.0\" encoding=\"utf-8\"?><configuration><startup><supportedRuntime version=\"v4.0\" sku=\".NETFramework,Version=v4.8\"/></startup></configuration>";
            var file=File.Open(path+".config",FileMode.Open,FileAccess.Read,FileShare.Read);try{byte[] actual=new byte[expected.Length+1];int count=file.Read(actual,0,actual.Length);if(count!=expected.Length||Encoding.ASCII.GetString(actual,0,count)!=expected)throw new IOException("Sensor permission requires the sealed worker configuration.");return file;}catch{file.Dispose();throw;}
        }
        private static void PipeWrite(Stream pipe,string line){byte[] data=Encoding.ASCII.GetBytes(line+"\n");if(data.Length>512)throw new IOException("Sensor lease exceeds bounds.");pipe.Write(data,0,data.Length);pipe.Flush();}
        private static string PipeRead(Stream pipe,int timeout)
        {
            var result=new StringBuilder();var watch=Stopwatch.StartNew();byte[] one=new byte[1];while(result.Length<512){int remaining=timeout-(int)watch.ElapsedMilliseconds;if(remaining<=0)throw new IOException("Sensor lease expired.");IAsyncResult read=pipe.BeginRead(one,0,1,null,null);int count;using(var ready=read.AsyncWaitHandle){if(!ready.WaitOne(remaining))throw new IOException("Sensor lease expired.");count=pipe.EndRead(read);}if(count!=1)throw new EndOfStreamException("Sensor lease closed.");if(one[0]==10)return result.ToString();if(one[0]<32||one[0]>126)throw new IOException("Invalid sensor lease.");result.Append((char)one[0]);}throw new IOException("Sensor lease exceeds bounds.");
        }
        private void SensorBrokerLoop()
        {
            NamedPipeServerStream pipe=null;Process broker=null;var locks=new List<FileStream>();bool requested=false,failed=false;
            try {
                var pins=Values.Obj(Values.Get(Settings(),"helperPins"));string exePin=Values.Text(pins,"HWINFO.exe"),dllPin=Values.Text(pins,"HWiNFO64.dll");
                locks.Add(PinnedSensor("HWINFO.exe",exePin));locks.Add(PinnedSensor("HWiNFO64.dll",dllPin));string self=SelfPath();var selfLock=File.Open(self,FileMode.Open,FileAccess.Read,FileShare.Read);locks.Add(selfLock);locks.Add(WorkerConfiguration(self));string selfPin=FileHash(selfLock);
                lock(sensorGate){StopOwnedNormalSensor();if(ProcessExists("TRCC")||ProcessExists("HWINFO"))throw new InvalidOperationException("Close TRCC and other sensor publishers before enabling live temperatures.");if(ownedSensorMap==null)ownedSensorMap=MemoryMappedFile.CreateNew("shareMemory_SysInfo",2097152);}
                string name="INTERLINKED-MASTER-SENSOR-"+Guid.NewGuid().ToString("N"),nonce=Guid.NewGuid().ToString("N")+Guid.NewGuid().ToString("N");
                var security=new PipeSecurity();var sid=WindowsIdentity.GetCurrent().User;security.SetOwner(sid);security.SetAccessRuleProtection(true,false);security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null),PipeAccessRights.FullControl,AccessControlType.Deny));security.AddAccessRule(new PipeAccessRule(sid,PipeAccessRights.FullControl,AccessControlType.Allow));
                pipe=new NamedPipeServerStream(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,security);lock(sensorGate)sensorPipe=pipe;
                if(!running||!connected||!sensorElevationRequested)return;
                string arguments;using(var parent=Process.GetCurrentProcess())arguments="--sensor-host "+name+" "+nonce+" "+exePin+" "+dllPin+" "+selfPin+" "+parent.Id.ToString(CultureInfo.InvariantCulture)+" "+parent.StartTime.ToUniversalTime().Ticks.ToString(CultureInfo.InvariantCulture);
                Emit("sensor_permission","status","requesting","message","Windows will ask permission to start only the reviewed temperature helper. MASTER stays unelevated.");requested=true;
                broker=Process.Start(new ProcessStartInfo(self,arguments){UseShellExecute=true,Verb="runas",WorkingDirectory=Path.GetDirectoryName(self),WindowStyle=ProcessWindowStyle.Hidden});
                if(broker==null)throw new IOException("Sensor permission helper did not start.");if(!running||!connected||!sensorElevationRequested)return;
                var accepted=pipe.BeginWaitForConnection(null,null);using(var ready=accepted.AsyncWaitHandle){var wait=Stopwatch.StartNew();while(!ready.WaitOne(100)){if(!running||!connected||!sensorElevationRequested)return;if(wait.ElapsedMilliseconds>10000||broker.HasExited)throw new IOException("Sensor permission helper did not connect.");}pipe.EndWaitForConnection(accepted);}
                uint client;if(!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out client)||client!=broker.Id)throw new IOException("Sensor permission helper identity was not verified.");
                PipeWrite(pipe,"HELLO 1 "+nonce);if(PipeRead(pipe,5000)!="READY 1 "+nonce)throw new IOException("Sensor permission handshake failed.");sensorBrokerActive=true;ownedSensorStarted=DateTime.UtcNow;Emit("sensor_permission","status","running","message","Reviewed temperature helper is running with permission; waiting for fresh readings.");
                while(running&&connected&&sensorElevationRequested){if(broker.HasExited)throw new IOException("Sensor permission helper exited.");PipeWrite(pipe,"PING "+nonce);if(PipeRead(pipe,5000)!="PONG "+nonce)throw new IOException("Sensor permission lease failed.");for(int i=0;i<10&&running&&connected&&sensorElevationRequested;i++)Thread.Sleep(100);}
            }catch(System.ComponentModel.Win32Exception ex){failed=true;Emit("sensor_permission","status",ex.NativeErrorCode==1223?"canceled":"unavailable","message",ex.NativeErrorCode==1223?"Windows permission was canceled. Temperatures remain unavailable.":"Windows could not start the reviewed temperature helper.");}
            catch(Exception ex){failed=true;if(sensorElevationRequested&&running)Emit("sensor_permission","status","unavailable","message",ex.Message);}
            finally {
                sensorBrokerActive=false;sensorElevationRequested=false;if(pipe!=null)try{pipe.Dispose();}catch{}lock(sensorGate)sensorPipe=null;
                if(broker!=null){try{broker.WaitForExit(5500);}catch{}broker.Dispose();}lock(sensorGate)CloseSensorMap();foreach(var file in locks)file.Dispose();if(requested&&!failed)Emit("sensor_permission","status","stopped","message","Sensor permission lease ended.");
            }
        }
        internal static bool SensorArguments(string[] args,out int parentPid,out long parentTicks)
        {
            parentPid=0;parentTicks=0;return args.Length==8&&args[0]=="--sensor-host"&&args[1].StartsWith("INTERLINKED-MASTER-SENSOR-",StringComparison.Ordinal)&&Hex(args[1].Substring("INTERLINKED-MASTER-SENSOR-".Length),32)&&Hex(args[2],64)&&Hex(args[3],64)&&Hex(args[4],64)&&Hex(args[5],64)&&int.TryParse(args[6],NumberStyles.None,CultureInfo.InvariantCulture,out parentPid)&&parentPid>0&&long.TryParse(args[7],NumberStyles.None,CultureInfo.InvariantCulture,out parentTicks)&&parentTicks>0&&parentTicks<=DateTime.UtcNow.AddMinutes(1).Ticks;
        }
        private static bool SensorParentAlive(int pid,long ticks,string path)
        {
            try{using(var process=Process.GetProcessById(pid))return !process.HasExited&&process.StartTime.ToUniversalTime().Ticks==ticks&&string.Equals(Path.GetFullPath(process.MainModule.FileName),path,StringComparison.OrdinalIgnoreCase);}catch{return false;}
        }
        private static Process StartContainedSensor(IntPtr job)
        {
            var startup=new STARTUPINFO();startup.Size=Marshal.SizeOf(typeof(STARTUPINFO));startup.Flags=1;startup.ShowWindow=0;PROCESS_INFORMATION child;
            if(!CreateProcess(@"C:\Program Files\TRCCCAP\HWINFO.exe",null,IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,@"C:\Program Files\TRCCCAP",ref startup,out child))throw new IOException("Reviewed sensor helper could not start.");
            try{if(!AssignProcessToJobObject(job,child.Process))throw new IOException("Cannot contain sensor helper.");if(ResumeThread(child.Thread)==uint.MaxValue)throw new IOException("Cannot resume contained sensor helper.");return Process.GetProcessById((int)child.ProcessId);}catch{TerminateProcess(child.Process,1);throw;}finally{CloseHandle(child.Thread);CloseHandle(child.Process);}
        }
        // Elevated mode has exactly one capability: a pinned, fixed sensor publisher.
        // It never constructs Worker, renders frames, accesses projects, or accepts commands.
        private static int SensorHost(string[] args)
        {
            int parentPid;long ticks;if(!SensorArguments(args,out parentPid,out ticks))throw new ArgumentException("Invalid sensor host arguments.");
            if(!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))throw new InvalidOperationException("Sensor host requires explicit Windows permission.");
            string self=SelfPath();using(var selfLock=File.Open(self,FileMode.Open,FileAccess.Read,FileShare.Read))using(var configuration=WorkerConfiguration(self)){
                if(!string.Equals(FileHash(selfLock),args[5],StringComparison.OrdinalIgnoreCase)||!SensorParentAlive(parentPid,ticks,self))throw new IOException("Sensor parent identity was not verified.");
                using(var exe=PinnedSensor("HWINFO.exe",args[3]))using(var dll=PinnedSensor("HWiNFO64.dll",args[4]))using(var pipe=new NamedPipeClientStream(".",args[1],PipeDirection.InOut,PipeOptions.Asynchronous,TokenImpersonationLevel.Anonymous)){
                    pipe.Connect(5000);uint server;if(!GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out server)||server!=parentPid)throw new IOException("Sensor lease owner was not verified.");
                    if(PipeRead(pipe,5000)!="HELLO 1 "+args[2])throw new IOException("Sensor lease handshake failed.");
                    if(!SensorParentAlive(parentPid,ticks,self)||ProcessExists("TRCC")||ProcessExists("HWINFO"))throw new IOException("Another sensor publisher or TRCC is active.");
                    IntPtr job=NewHelperJob();Process helper=null;
                    try {
                        helper=StartContainedSensor(job);PipeWrite(pipe,"READY 1 "+args[2]);
                        while(SensorParentAlive(parentPid,ticks,self)&&!helper.HasExited){string lease=PipeRead(pipe,5000);if(lease!="PING "+args[2]||ProcessExists("TRCC"))break;PipeWrite(pipe,"PONG "+args[2]);}
                    }finally{CloseHandle(job);if(helper!=null){try{helper.WaitForExit(1500);}catch{}helper.Dispose();}}
                }
            }return 0;
        }
        private static Dictionary<string,object> MissingSensor(){return new Dictionary<string,object>{{"value",null},{"unit","C"},{"status","unavailable"}};}
        private Dictionary<string,object> ReadSensors(Func<bool> cancelled)
        {
            var result=new Dictionary<string,object>{{"cpu",MissingSensor()},{"gpu",MissingSensor()},{"status","unavailable"},{"observedAt",DateTime.UtcNow.ToString("o")}};
            try {
                if(cancelled()||!running)return null;
                var publishers=Process.GetProcessesByName("HWINFO");bool exists=publishers.Length>0;foreach(var process in publishers)process.Dispose();if(!exists)throw new IOException("Sensor publisher is not running.");
                ValidateHelperPins(true);
                if(cancelled()||!running)return null;
                byte[] first=sensorFirst,second=sensorSecond;
                using(var sensorMap=MemoryMappedFile.OpenExisting("shareMemory_SysInfo",MemoryMappedFileRights.Read))using(var sensorView=sensorMap.CreateViewAccessor(0,1048576,MemoryMappedFileAccess.Read)){sensorView.ReadArray(0,first,0,first.Length);Thread.Sleep(2);sensorView.ReadArray(0,second,0,second.Length);}
                int length=Array.IndexOf(first,(byte)0);if(length<0)throw new InvalidDataException("Sensor text is not terminated.");
                int secondLength=Array.IndexOf(second,(byte)0);if(length!=secondLength)throw new InvalidDataException("Sensor sample changed during read.");
                for(int i=0;i<length;i++)if(first[i]!=second[i])throw new InvalidDataException("Sensor sample changed during read.");
                string text=Encoding.ASCII.GetString(first,0,length);if(!text.StartsWith("HWi32_GetNumberOfDetectedSensors:\n",StringComparison.Ordinal))throw new InvalidDataException("Unrecognized vendor sensor format.");
                string hash;using(var sha=SHA256.Create())hash=BitConverter.ToString(sha.ComputeHash(first,0,length));
                if(hash!=sensorHash){sensorHash=hash;sensorChanged=DateTime.UtcNow;}
                if((DateTime.UtcNow-sensorChanged).TotalSeconds>5)throw new IOException("Sensor sample has stopped changing.");
                int start=text.IndexOf('{');if(start<0)throw new InvalidDataException("Vendor sensor JSON missing.");
                var groups=Values.Obj(sensorJson.DeserializeObject(text.Substring(start)));Dictionary<string,object> cpu=null,gpu=null;
                foreach(var group in groups){string name=group.Key.ToLowerInvariant();bool isCpu=name.StartsWith("cpu [#0]:")&&name.Contains("enhanced"),isGpu=name.StartsWith("gpu [#0]:");if(!isCpu&&!isGpu)continue;
                    foreach(var sensor in Values.Rows(group.Value))foreach(var pair in sensor){var reading=Values.Obj(pair.Value);if(Values.Text(reading,"type")!="temperature")continue;if((isCpu&&pair.Key=="CPU (Tctl/Tdie)")||(isGpu&&pair.Key=="GPU Temperature")){
                        double value;if(!double.TryParse(Values.Text(reading,"value"),NumberStyles.Float,CultureInfo.InvariantCulture,out value)||double.IsNaN(value)||double.IsInfinity(value)||value<0||value>=150)continue;
                        var item=new Dictionary<string,object>{{"value",value},{"unit","C"},{"status","live"},{"label",pair.Key},{"source","Vendor sensor publisher"},{"observedAt",DateTime.UtcNow.ToString("o")}};if(isCpu)cpu=item;else gpu=item;
                    }}
                }
                if(cpu!=null)result["cpu"]=cpu;if(gpu!=null)result["gpu"]=gpu;result["status"]=cpu!=null&&gpu!=null?"live":"partial";
            }catch {
                if(cancelled()||!running)return null;
                bool normalActive=false;lock(sensorGate){try{normalActive=ownedSensor!=null&&!ownedSensor.HasExited;}catch{}}
                bool initializing=(sensorBrokerActive||normalActive)&&(DateTime.UtcNow-ownedSensorStarted).TotalSeconds<60;
                result["status"]=initializing?"initializing":"unavailable";
                result["reason"]=initializing?"Vendor sensor publisher is initializing (up to 60 seconds).":"Vendor sensor source absent, stale, or unverified.";
            }
            return result;
        }
        public void Dispose(){running=false;connectRequested=false;if(sensorSampler!=null)sensorSampler.Stop(1500);StopSensorBroker();frameReady.Set();usbFrameReady.Set();if(usbThread!=null&&usbThread.IsAlive)usbThread.Join(5000);if(sensorBrokerThread!=null&&sensorBrokerThread.IsAlive)sensorBrokerThread.Join(1500);if(usbThread==null)StopUsb();if(violetAtlas!=null)violetAtlas.Dispose();violetAnimations.Dispose();title.Dispose();roomFont.Dispose();mono.Dispose();small.Dispose();detail.Dispose();binaryFont.Dispose();temperatureLabel.Dispose();temperatureValue.Dispose();webName.Dispose();webDenseName.Dispose();webMeta.Dispose();webTitle.Dispose();webTemperature.Dispose();webBinary.Dispose();}
        internal static int SelfTest(string directory)
        {
            Directory.CreateDirectory(directory);using(var worker=new Worker()){
                var state=new Dictionary<string,object>{{"projects",new object[]{new Dictionary<string,object>{{"id","edit"},{"label","ED-IT Buddy"},{"activity",new Dictionary<string,object>{{"status","working"},{"source","SYNTHETIC"}}}},new Dictionary<string,object>{{"id","mw5"},{"label","MW5 MOD"},{"activity",new Dictionary<string,object>{{"status","waiting"}}}},new Dictionary<string,object>{{"id","interlinked"},{"label","INTERLINKED"},{"activity",new Dictionary<string,object>{{"status","unknown"}}}}}}};
                var config=new Dictionary<string,object>{{"diagnostic",true}};string asset=Environment.GetEnvironmentVariable("INTERLINKED_MASTER_VIOLET_ATLAS");if(!string.IsNullOrEmpty(asset))config["violetAtlasPath"]=asset;
                using(var image=worker.DrawFrame(state,config,1.0)){image.Save(Path.Combine(directory,"master-preview.png"));using(var stream=new MemoryStream()){image.Save(stream,ImageFormat.Jpeg);byte[] jpeg=stream.ToArray(),packet=Packet(jpeg);if(packet.Length!=jpeg.Length+64||BitConverter.ToInt32(packet,56)!=2||BitConverter.ToInt32(packet,8)!=480||BitConverter.ToInt32(packet,12)!=480)throw new Exception("Packet contract failed");File.WriteAllBytes(Path.Combine(directory,"test-packet.bin"),packet);}}
                bool refused=false;try{Packet(new byte[]{0,0,0,0});}catch(InvalidDataException){refused=true;}if(!refused)throw new Exception("Malformed JPEG accepted");
                int parent;long ticks;var brokerArgs=new[]{"--sensor-host","INTERLINKED-MASTER-SENSOR-"+new string('a',32),new string('b',64),new string('c',64),new string('d',64),new string('e',64),"1",DateTime.UtcNow.Ticks.ToString(CultureInfo.InvariantCulture)};
                if(!SensorArguments(brokerArgs,out parent,out ticks))throw new Exception("Valid fixed broker arguments rejected");brokerArgs[1]="../arbitrary";if(SensorArguments(brokerArgs,out parent,out ticks))throw new Exception("Broker accepted arbitrary pipe path");if(SensorArguments(new[]{"--sensor-host"},out parent,out ticks))throw new Exception("Broker accepted incomplete arguments");
                // Simulate LCD-owned input dispatch, but omit helper pins so the broker
                // fails before opening vendor files, a map, or a Windows permission prompt.
                var originalInput=Console.In;try{worker.connected=true;Console.SetIn(new StringReader("{\"op\":\"sensors\",\"enable\":true}\n{\"op\":\"settings\",\"settings\":{\"fps\":5}}\n"));worker.ReadInput();if(worker.sensorBrokerThread==null)throw new Exception("Connected sensor command did not dispatch");worker.sensorBrokerThread.Join(2000);if(worker.sensorBrokerThread.IsAlive)throw new Exception("Missing-pin sensor command did not fail closed");}finally{Console.SetIn(originalInput);worker.connected=false;}
                File.WriteAllText(Path.Combine(directory,"native-selftest.json"),"{\"ok\":true,\"dimensions\":[480,480],\"packetPictureOnly\":true,\"malformedJpegRefused\":true,\"sensorArgumentsBounded\":true,\"connectedSensorDispatchWithoutPins\":true,\"hardwareTested\":false}");
            }return 0;
        }
        [STAThread]private static int Main(string[] args){try{Console.InputEncoding=new UTF8Encoding(false);Console.OutputEncoding=new UTF8Encoding(false);Console.SetError(new StreamWriter(Console.OpenStandardError(),new UTF8Encoding(false)){AutoFlush=true});if(args.Length>0&&args[0]=="--sensor-host")return SensorHost(args);Dpi();if(args.Length==2&&args[0]=="--self-test")return SelfTest(Path.GetFullPath(args[1]));if(args.Length!=0)throw new ArgumentException("Unknown worker arguments");using(var worker=new Worker())return worker.Run();}catch(Exception ex){Console.Error.WriteLine("{\"type\":\"status\",\"displayStatus\":\"unavailable\",\"error\":\""+ex.GetType().Name+"\"}");return 2;}}
    }
}

// Embedded artwork is private build input. This runtime has no artwork-path,
// preview-control, synthetic-clock, transport or sensor interfaces.
namespace InterlinkedMaster {
 internal sealed class VioletAnimationAssets : IDisposable {
  internal const string ResourceName="Interlinked.VioletAnimations.v1";
  internal const string ApprovedManifest="e7267f338b6ce53794f6c420451bb9be733f48e538cd429cc465c43205661c02";
  internal sealed class Key {internal Bitmap Image,Matte;internal double X,Y,Angle,Width;}
  internal sealed class Sequence {internal string Id;internal Key[] Keys;internal int Count,Interval;internal bool Once;}
  private readonly Dictionary<string,Sequence> sequences=new Dictionary<string,Sequence>();
  internal static readonly Dictionary<string,int[]> Specifications=new Dictionary<string,int[]>{
   {"new_thinking",new[]{48,8,0}},{"new_reviewing",new[]{54,9,0}},{"new_success",new[]{48,8,1}},{"new_finished",new[]{54,9,1}},
   {"new_listening",new[]{42,7,0}},{"new_speaking",new[]{30,5,0}},{"new_reconnecting",new[]{60,10,0}},{"new_resting",new[]{78,13,0}},
   {"new_greeting",new[]{48,8,1}},{"new_concerned",new[]{60,10,0}},{"legacy_neutral",new[]{36,6,0}},{"legacy_failure",new[]{48,6,0}},
   {"legacy_waiting",new[]{36,6,0}},{"legacy_working",new[]{36,6,0}}};
  private static void Need(bool ok){if(!ok)throw new InvalidDataException("Embedded Violet animation resource differs from the reviewed format.");}
  private static byte[] Read(BinaryReader reader,int count){Need(count>=0&&count<=33554432);var data=reader.ReadBytes(count);Need(data.Length==count);return data;}
  private static string Hash(byte[] value){using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(value)).Replace("-","").ToLowerInvariant();}
  private static bool Hex(string value){if(value.Length!=64)return false;foreach(char c in value)if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;return true;}
  private static int Big(byte[] d,int p){return d[p]<<24|d[p+1]<<16|d[p+2]<<8|d[p+3];}
  private static void Exact(Dictionary<string,object> value,params string[] keys){Need(value.Count==keys.Length);foreach(string key in keys)Need(value.ContainsKey(key));}
  internal static VioletAnimationAssets Load(Stream stream){
   var result=new VioletAnimationAssets();try{
    Need(stream!=null&&stream.CanRead);using(var reader=new BinaryReader(stream,new UTF8Encoding(false,true),true)){
     Need(Encoding.ASCII.GetString(Read(reader,5))=="ILVA1");int length=reader.ReadInt32();Need(length>0&&length<=32768);
     var descriptor=Values.Obj(new JavaScriptSerializer{MaxJsonLength=32768}.DeserializeObject(new UTF8Encoding(false,true).GetString(Read(reader,length))));
     Exact(descriptor,"format","manifestSha256","fps","animations");Need(Values.Text(descriptor,"format")=="interlinked-violet-embedded-v1"&&Values.Text(descriptor,"manifestSha256")==ApprovedManifest&&Values.Int(descriptor,"fps")==30);
     var animations=Values.Rows(descriptor["animations"]);Need(animations.Count==14);int total=0,keys=0;
     foreach(var row in animations){
      Exact(row,"id","frameCount","interval","once","keys");string id=Values.Text(row,"id");int[] spec;Need(Specifications.TryGetValue(id,out spec)&&!result.sequences.ContainsKey(id));
      int count=Values.Int(row,"frameCount"),interval=Values.Int(row,"interval");bool once=Values.Bool(row,"once");
      Need(row["once"] is bool&&count==spec[0]&&interval==spec[1]&&once==(spec[2]==1)&&count%interval==0);
      var pins=row["keys"] as IEnumerable;Need(pins!=null);var hashes=new List<string>();foreach(object pin in pins){Need(pin is string&&Hex((string)pin));hashes.Add((string)pin);}Need(hashes.Count==count/interval);
      var sequence=new Sequence{Id=id,Count=count,Interval=interval,Once=once,Keys=new Key[hashes.Count]};result.sequences.Add(id,sequence);
      for(int i=0;i<hashes.Count;i++){
       int bytes=reader.ReadInt32();Need(bytes>=33&&bytes<=524288);total+=bytes;Need(total<=33554432);var png=Read(reader,bytes);
       Need(BitConverter.ToString(png,0,8)=="89-50-4E-47-0D-0A-1A-0A"&&Big(png,16)==192&&Big(png,20)==208&&png[24]==8&&png[25]==6&&Hash(png)==hashes[i]);
       using(var memory=new MemoryStream(png,false))using(var image=new Bitmap(memory)){Need(image.Width==192&&image.Height==208);var key=new Key{Image=new Bitmap(image)};sequence.Keys[i]=key;FindTape(key);key.Matte=BuildMatte(key.Image);}keys++;
      }
     }
     Need(keys==86&&reader.BaseStream.ReadByte()==-1);
    }return result;
   }catch{result.Dispose();throw;}
  }
  internal static VioletAnimationAssets FromAssembly(){using(var stream=typeof(VioletAnimationAssets).Assembly.GetManifestResourceStream(ResourceName))return stream==null?null:Load(stream);}
  internal int Count {get{return sequences.Count;}}
  internal int FrameCount(string id){return sequences[id].Count;}
  internal int FrameIndex(string id,int tick){var seq=sequences[id];tick=Math.Max(0,tick);return seq.Once?Math.Min(tick,seq.Count-1):tick%seq.Count;}
  private static void FindTape(Key key){
   int w=192,h=208;bool[] good=new bool[w*h],seen=new bool[w*h];for(int y=126;y<198;y++)for(int x=8;x<184;x++){var c=key.Image.GetPixel(x,y);good[y*w+x]=c.A>=180&&c.R<72&&c.G<72&&c.B<72;}
   List<int> best=new List<int>();var queue=new Queue<int>();for(int y=126;y<198;y++)for(int x=8;x<184;x++){int start=y*w+x;if(!good[start]||seen[start])continue;var component=new List<int>();seen[start]=true;queue.Enqueue(start);
    while(queue.Count>0){int q=queue.Dequeue();component.Add(q);int qx=q%w,qy=q/w;for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++){int nx=qx+dx,ny=qy+dy;if(nx<8||nx>=184||ny<126||ny>=198)continue;int n=ny*w+nx;if(good[n]&&!seen[n]){seen[n]=true;queue.Enqueue(n);}}}if(component.Count>best.Count)best=component;}
   Need(best.Count>=300);double cx=0,cy=0;foreach(int p in best){cx+=p%w;cy+=p/w;}cx/=best.Count;cy/=best.Count;double xx=0,xy=0,yy=0;foreach(int p in best){double dx=p%w-cx,dy=p/w-cy;xx+=dx*dx;xy+=dx*dy;yy+=dy*dy;}
   double angle=.5*Math.Atan2(2*xy,xx-yy),cos=Math.Cos(angle),sin=Math.Sin(angle),lo=1000,hi=-1000;foreach(int p in best){double along=(p%w-cx)*cos+(p/w-cy)*sin;lo=Math.Min(lo,along);hi=Math.Max(hi,along);}
   key.X=cx;key.Y=cy;key.Angle=angle;key.Width=hi-lo;Need(key.Width>=50&&key.Width<=170&&Math.Abs(angle)<=.55);
  }
  internal static Bitmap BuildMatte(Bitmap source){
   var matte=new Bitmap(192,208,PixelFormat.Format32bppArgb);var rect=new Rectangle(0,0,192,208);
   try{
    var input=source.LockBits(rect,ImageLockMode.ReadOnly,PixelFormat.Format32bppArgb);
    try{
     var output=matte.LockBits(rect,ImageLockMode.WriteOnly,PixelFormat.Format32bppArgb);
     try{
      var row=new byte[192*4];
      for(int y=0;y<208;y++){
       Marshal.Copy(IntPtr.Add(input.Scan0,y*input.Stride),row,0,row.Length);
       for(int x=0;x<192;x++){int i=x*4;bool solid=row[i+3]>=128;row[i]=row[i+1]=row[i+2]=(byte)(solid?0:255);row[i+3]=(byte)(solid?255:0);}
       Marshal.Copy(row,0,IntPtr.Add(output.Scan0,y*output.Stride),row.Length);
      }
     }finally{matte.UnlockBits(output);}
    }finally{source.UnlockBits(input);}
    return matte;
   }catch{matte.Dispose();throw;}
  }
  private sealed class Pose {internal Key Key;internal double X,Y,Angle,Scale;}
  private static Pose At(Sequence seq,int frame){int first=frame/seq.Interval,sub=frame%seq.Interval,second=(first+1)%seq.Keys.Length;double t=(double)sub/seq.Interval;if(seq.Once&&first==seq.Keys.Length-1){second=first;t=0;}var a=seq.Keys[first];var b=seq.Keys[second];var key=seq.Keys[t<.5?first:second];return new Pose{Key=key,X=a.X+(b.X-a.X)*t,Y=a.Y+(b.Y-a.Y)*t,Angle=a.Angle+(b.Angle-a.Angle)*t,Scale=(a.Width+(b.Width-a.Width)*t)/key.Width};}
  private static PointF Transform(Pose p,double x,double y,Rectangle target){double dx=x-p.Key.X,dy=y-p.Key.Y,angle=p.Angle-p.Key.Angle,cos=Math.Cos(angle),sin=Math.Sin(angle);double sx=p.X+p.Scale*(dx*cos-dy*sin),sy=p.Y+p.Scale*(dx*sin+dy*cos);return new PointF((float)(target.Left+sx*target.Width/192.0),(float)(target.Top+sy*target.Height/208.0));}
  private static void DrawPose(Graphics g,Bitmap image,Pose pose,Rectangle target){var points=new[]{Transform(pose,0,0,target),Transform(pose,192,0,target),Transform(pose,0,208,target)};var saved=g.Save();g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.PixelOffsetMode=PixelOffsetMode.Half;using(var attributes=new ImageAttributes()){attributes.SetWrapMode(WrapMode.TileFlipXY);g.DrawImage(image,points,new RectangleF(0,0,192,208),GraphicsUnit.Pixel,attributes);}g.Restore(saved);}
  internal void Draw(Graphics g,string id,int tick,Rectangle target){var seq=sequences[id];var pose=At(seq,FrameIndex(id,tick));DrawPose(g,pose.Key.Matte,pose,target);DrawPose(g,pose.Key.Image,pose,target);}
  public void Dispose(){foreach(var sequence in sequences.Values)foreach(var key in sequence.Keys)if(key!=null){if(key.Image!=null)key.Image.Dispose();if(key.Matte!=null)key.Matte.Dispose();}sequences.Clear();}
 }

 internal sealed class VioletAnimationSelection {internal string Animation,Source;internal double Started;internal int Tick(double time){return Math.Max(0,(int)Math.Floor(Math.Max(0,time-Started)*30+0.000001));}}
 internal sealed class VioletAnimationSelector {
  private sealed class Terminal {internal string Identity,Animation;internal DateTime When;internal int Rank;}
  private readonly Dictionary<string,DateTime> consumed=new Dictionary<string,DateTime>();private readonly Queue<string> order=new Queue<string>();private DateTime consumedThrough=DateTime.MinValue;
  private bool initialized,greeted,dotWasWorking;private Terminal active;private string loop="",source="";private double loopStarted;
  private DateTime aiSince=DateTime.MinValue,graphSince=DateTime.MinValue;private readonly HashSet<string> projectBaselines=new HashSet<string>();
  private static bool Fresh(string value,DateTime now,double seconds){DateTime when;return DateTime.TryParse(value,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out when)&&(now-when.ToUniversalTime()).TotalSeconds>=0&&(now-when.ToUniversalTime()).TotalSeconds<seconds;}
  private static string Text(Dictionary<string,object> row,string name,string fallback=""){string v=Values.Text(row,name,fallback);return v.Length<=256?v:"";}
  private void Consume(Terminal item){if(!consumed.ContainsKey(item.Identity)){consumed[item.Identity]=item.When;order.Enqueue(item.Identity);while(order.Count>256){string old=order.Dequeue();DateTime when=consumed[old];if(when>consumedThrough)consumedThrough=when;consumed.Remove(old);}}}
  private static bool Wait(string status){return status=="waiting"||status=="needs_input"||status=="waiting_for_input"||status=="review_pending"||status=="ready_for_review";}
  private static bool Work(string status){return status=="working"||status=="running"||status=="preparing"||status=="applying"||status=="busy"||status=="editing";}
  private static bool RequestId(object value,out long id){id=-1;if(!(value is byte||value is sbyte||value is short||value is ushort||value is int||value is uint||value is long||value is ulong))return false;try{id=Convert.ToInt64(value,CultureInfo.InvariantCulture);return id>=0;}catch(OverflowException){return false;}}
  private static bool RecoveryBlocked(string code){switch(code){case "writer_conflict":case "map_ownership_conflict":case "ownership_conflict":case "cleanup_failed":case "configuration_changed":case "recovery_exhausted":case "invalid_pins":case "invalid_configuration":case "permission_denied":return true;default:return false;}}
  private static void Candidate(List<Terminal> items,string identity,string animation,string timestamp,int rank,DateTime now){DateTime when;if(identity.Length>0&&identity.Length<=768&&DateTime.TryParse(timestamp,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out when)){when=when.ToUniversalTime();double age=(now-when).TotalSeconds;if(age>=0&&age<3)items.Add(new Terminal{Identity=identity+"|"+timestamp,Animation=animation,When=when,Rank=rank});}}
  internal VioletAnimationSelection Select(Dictionary<string,object> state,DateTime now,double time,bool delivered){
   var terminals=new List<Terminal>();string chosen="legacy_neutral",chosenSource="unknown";int rank=99;
   Action<int,string,string> choose=delegate(int r,string animation,string origin){if(r<rank){rank=r;chosen=animation;chosenSource=origin;}};
   bool sourceInvalid=false;Action<string> invalidate=delegate(string owner){if(active!=null&&active.Identity.StartsWith(owner+"|",StringComparison.Ordinal))sourceInvalid=true;};
   bool fresh=Fresh(Text(state,"utc"),now,15);var display=Values.Obj(Values.Get(state,"coolerDisplay"));bool complete=fresh&&Values.Bool(display,"activityAvailable")&&!Values.Bool(display,"activityPartial");
   if(fresh){
    // These private observations describe actual capture, playback and established recovery.
    // Neither inference requests nor a manual Connect intent are evidence for these states.
    var voice=Values.Obj(Values.Get(display,"voiceActivity"));if(Values.Bool(voice,"available")&&Fresh(Text(voice,"observedAt"),now,5))foreach(var row in Values.Rows(Values.Get(voice,"sources"))){if(Text(row,"origin")!="assistant_ui"||!Fresh(Text(row,"observedAt"),now,5))continue;string channel=Text(row,"source"),phase=Text(row,"phase");if(channel=="microphone_capture"&&phase=="listening")choose(4,"new_listening","voice:assistant_ui:"+channel);else if((channel=="browser_tts"||channel=="windows_sapi")&&phase=="speaking")choose(5,"new_speaking","voice:assistant_ui:"+channel);}
    var connection=Values.Obj(Values.Get(display,"connectionActivity"));var transport=Values.Obj(Values.Get(state,"display"));long observedRequest,currentRequest;if(Text(connection,"source")=="cooler_transport"&&Text(connection,"phase")=="reconnecting"&&Fresh(Text(connection,"observedAt"),now,5)&&Values.Bool(transport,"desiredConnected")&&RequestId(Values.Get(connection,"requestId"),out observedRequest)&&RequestId(Values.Get(transport,"requestId"),out currentRequest)&&observedRequest==currentRequest&&!RecoveryBlocked(Text(transport,"errorCode")))choose(3,"new_reconnecting","connection:cooler_transport:"+observedRequest.ToString(CultureInfo.InvariantCulture));
    foreach(var project in Values.Rows(Values.Get(state,"projects"))){string id=Text(project,"id");bool newProject=!projectBaselines.Contains(id);if(newProject&&projectBaselines.Count<128)projectBaselines.Add(id);var health=Values.Obj(Values.Get(project,"health"));var baseline=Values.Obj(Values.Get(health,"baseline"));var activity=Values.Obj(Values.Get(project,"activity"));var workspace=Values.Obj(Values.Get(project,"workspace"));
     bool current=Values.Bool(health,"current")&&Values.Bool(baseline,"clean")&&Text(baseline,"head").Length>=40;
     if(active!=null&&active.Identity.StartsWith("check:"+id+":",StringComparison.Ordinal)&&(!current||Values.Text(health,"status")!="passed"||!Fresh(Text(health,"observedAt"),now,180)))sourceInvalid=true;
     if(Fresh(Text(health,"observedAt"),now,180)&&Values.Text(health,"status")=="failed")choose(0,"legacy_failure","project:"+id);
     if(current&&Values.Text(health,"status")=="passed"){int before=terminals.Count;Candidate(terminals,"check:"+id+":"+Text(health,"source")+":"+Text(baseline,"head"),"new_success",Text(health,"observedAt"),10,now);if(newProject&&terminals.Count>before){Consume(terminals[before]);terminals.RemoveAt(before);}}
     if(!Values.Bool(activity,"stale")&&Fresh(Text(activity,"observedAt"),now,180)){string status=Text(activity,"status");if(Wait(status))choose(1,"legacy_waiting","project:"+id);else if(Work(status))choose(8,"legacy_working","project:"+id);}
     if(Fresh(Text(workspace,"observedAt"),now,15)&&Text(workspace,"source")!="INTERLINKED AI coordinator"){string status=Text(workspace,"status");if(Wait(status))choose(1,"legacy_waiting","workspace:"+id);else if(Work(status))choose(8,"legacy_working","workspace:"+id);}
    }
    var graph=Values.Obj(Values.Get(state,"lifecycleGraph"));bool graphFresh=Values.Bool(graph,"available")&&Fresh(Text(graph,"observedAt"),now,180);if(graphFresh&&graphSince==DateTime.MinValue)graphSince=now;if(!graphFresh&&active!=null&&active.Identity.StartsWith("codex:",StringComparison.Ordinal))sourceInvalid=true;if(graphFresh)foreach(var node in Values.Rows(Values.Get(graph,"nodes"))){string id=Text(node,"nodeId"),owner="codex:"+id;if(Text(node,"confidence")=="stale"||Values.Bool(node,"stale")||!Fresh(Text(node,"observedAt"),now,180)){invalidate(owner);continue;}string status=Text(node,"status");if(Wait(status))choose(1,"legacy_waiting",owner);else if(Work(status))choose(8,"legacy_working",owner);else if(status=="turn_finished")Candidate(terminals,owner,"new_finished",Text(node,"observedAt"),11,now);else invalidate(owner);}
    var ai=Values.Obj(Values.Get(state,"ai"));bool aiFresh=Values.Bool(ai,"available")&&Fresh(Text(ai,"observedAt"),now,15);if(aiFresh&&aiSince==DateTime.MinValue)aiSince=now;if(!aiFresh&&active!=null&&active.Identity.StartsWith("ai:",StringComparison.Ordinal))sourceInvalid=true;if(aiFresh){
     var flows=Values.Rows(Values.Get(ai,"flows"));foreach(var flow in flows){string phase=Text(flow,"phase").ToLowerInvariant(),id=Text(flow,"id"),origin=Text(flow,"origin"),owner="ai:"+origin+":"+id;if(Values.Bool(flow,"stale")||!Fresh(Text(flow,"observedAt",Text(flow,"phaseAt")),now,180)){invalidate(owner);continue;}
      if(phase=="failed"||phase=="recovery_required")choose(0,"legacy_failure",origin+":"+id);else if(Wait(phase)||phase=="result_ready"&&Text(flow,"resultState")=="review_required")choose(1,"legacy_waiting",origin+":"+id);else if(phase=="reviewing")choose(6,"new_reviewing",origin+":"+id);else if(phase=="request_pending")choose(Text(flow,"role")=="reviewer"?6:7,Text(flow,"role")=="reviewer"?"new_reviewing":"new_thinking",origin+":"+id);else if(Work(phase))choose(8,"legacy_working",origin+":"+id);else if(phase=="queued")choose(9,"legacy_neutral",origin+":"+id);else if(phase=="completed"||phase=="applied")Candidate(terminals,owner,"new_finished",Text(flow,"terminalAt",Text(flow,"phaseAt")),11,now);else invalidate(owner);
     }
     foreach(var call in Values.Rows(Values.Get(ai,"activeCalls"))){if(Values.Bool(call,"stale"))continue;string flowId=Text(call,"flowId");bool parentFresh=flows.Exists(delegate(Dictionary<string,object> flow){return Text(flow,"id")==flowId&&!Values.Bool(flow,"stale")&&Fresh(Text(flow,"observedAt",Text(flow,"phaseAt")),now,180);});if(!parentFresh&&!Fresh(Text(call,"observedAt",Text(call,"phaseAt")),now,180))continue;string phase=Text(call,"phase").ToLowerInvariant();if(phase=="request_pending"||phase=="working"||phase=="running")choose(Text(call,"role")=="reviewer"?6:7,Text(call,"role")=="reviewer"?"new_reviewing":"new_thinking",Text(call,"origin")+":"+Text(call,"id"));}
    }
    var dot=Values.Obj(Values.Get(state,"violet"));bool dotFresh=Values.Bool(dot,"connected")&&!Values.Bool(dot,"stale")&&Fresh(Text(dot,"observedAt",Text(dot,"reportedAt")),now,180);string dotState=Text(dot,"activity",Text(dot,"status"));
    if(dotFresh){if(Text(dot,"activity")=="interrupted"&&!Values.Bool(state,"demo")&&!Values.Bool(dot,"stale",true)&&(Text(dot,"reporter")=="dot"||Text(dot,"reporter")=="local_task"))choose(2,"new_concerned","violet:"+Text(dot,"reporter"));if(Wait(dotState))choose(1,"legacy_waiting","violet");else if(Work(dotState))choose(8,"legacy_working","violet");if(dotState=="turn_finished"&&dotWasWorking)Candidate(terminals,"violet-transition:"+Text(dot,"reporter")+":"+Text(dot,"projectId"),"new_finished",Text(dot,"observedAt",Text(dot,"reportedAt")),11,now);if(dotState!="turn_finished"&&active!=null&&active.Identity.StartsWith("violet-transition:",StringComparison.Ordinal))sourceInvalid=true;dotWasWorking=Work(dotState);}else {dotWasWorking=false;if(active!=null&&active.Identity.StartsWith("violet-transition:",StringComparison.Ordinal))sourceInvalid=true;}
   }else dotWasWorking=false;
   terminals.RemoveAll(delegate(Terminal item){bool bootstrap=item.Identity.StartsWith("codex:",StringComparison.Ordinal)&&item.When<=graphSince||item.Identity.StartsWith("ai:",StringComparison.Ordinal)&&item.When<=aiSince;if(bootstrap)Consume(item);return bootstrap;});
   if(sourceInvalid)active=null;
   if(delivered&&!greeted){greeted=true;if(initialized)terminals.Add(new Terminal{Identity="greeting:acknowledged-stream",Animation="new_greeting",When=now,Rank=12});}
   if(!initialized&&fresh){foreach(var item in terminals)Consume(item);initialized=true;}
   bool blocked=!fresh||rank<10;if(blocked){foreach(var item in terminals)Consume(item);active=null;}
   else {
    foreach(var item in terminals){if((consumed.ContainsKey(item.Identity)||item.When<=consumedThrough))continue;Consume(item);if(active==null||item.Rank<active.Rank||item.Rank==active.Rank&&item.When>active.When)active=item;}
    if(active!=null&&((now-active.When).TotalSeconds<0||(now-active.When).TotalSeconds>=3))active=null;
    if(active!=null)return new VioletAnimationSelection{Animation=active.Animation,Source=active.Identity,Started=time-(now-active.When).TotalSeconds};
   }
   if(rank==99){chosen=complete?"new_resting":"legacy_neutral";chosenSource=complete?"verified-idle":"unknown";}
   if(chosen!=loop||chosenSource!=source){loop=chosen;source=chosenSource;loopStarted=time;}return new VioletAnimationSelection{Animation=chosen,Source=chosenSource,Started=loopStarted};
  }
  internal int ConsumedCount {get{return consumed.Count;}}
 }
 internal sealed class VioletAnimationPlayer : IDisposable {
  private VioletAnimationAssets assets;private bool attempted;private readonly VioletAnimationSelector selector=new VioletAnimationSelector();
  private bool Ready(){
   if(!attempted){attempted=true;try{assets=VioletAnimationAssets.FromAssembly();}catch{assets=null;}}
   return assets!=null;
  }
  internal bool Draw(Graphics g,Dictionary<string,object> state,double time,Rectangle target,bool delivered){
   if(!Ready())return false;var selected=selector.Select(state,DateTime.UtcNow,time,delivered);assets.Draw(g,selected.Animation,selected.Tick(time),target);return true;
  }
  internal bool DrawById(Graphics g,string id,int tick,Rectangle target){
   if(!Ready()||!VioletAnimationAssets.Specifications.ContainsKey(id))return false;assets.Draw(g,id,tick,target);return true;
  }
  public void Dispose(){if(assets!=null){assets.Dispose();assets=null;}}
 }
}
