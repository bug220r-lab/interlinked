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

    internal sealed class Worker : IDisposable
    {
        private const int Slot=691200, MaxJpeg=691136;
        private readonly object gate=new object(), logGate=new object(), jsonGate=new object();
        private readonly JavaScriptSerializer json=new JavaScriptSerializer {MaxJsonLength=262144};
        private Dictionary<string,object> snapshot=new Dictionary<string,object>(), settings=new Dictionary<string,object>();
        private volatile bool running=true, connectRequested=false, connected=false;
        private byte[] latest;private long sequence, delivered, dropped, acknowledged, encodedBytes;
        private double encodeMs,ackMs;private Image violetAtlas;private string atlasPath="";
        private MemoryMappedFile map;private MemoryMappedViewAccessor view;private Process helper;
        private Thread usbThread;private IntPtr helperJob=IntPtr.Zero;
        private Process ownedSensor;private IntPtr sensorJob=IntPtr.Zero;
        private MemoryMappedFile ownedSensorMap;private DateTime ownedSensorStarted=DateTime.MinValue;
        private readonly object sensorGate=new object();private volatile bool sensorElevationRequested=false,sensorBrokerActive=false;
        private Thread sensorBrokerThread;private NamedPipeServerStream sensorPipe;
        private readonly byte[] sensorFirst=new byte[1048576],sensorSecond=new byte[1048576];
        private string sensorHash="";private DateTime sensorChanged=DateTime.MinValue;
        private Dictionary<string,object> lastSensors=new Dictionary<string,object>();
        private string displayStatus="disconnected", captureStatus="";
        private readonly Stopwatch clock=Stopwatch.StartNew();
        private readonly Stream output=Console.OpenStandardOutput();
        private readonly AutoResetEvent frameReady=new AutoResetEvent(false);
        private readonly ImageCodecInfo jpegCodec=Array.Find(ImageCodecInfo.GetImageEncoders(),x=>x.MimeType=="image/jpeg");
        private static readonly Color Purple=Color.FromArgb(192,58,255), Lilac=Color.FromArgb(206,87,255), White=Color.FromArgb(245,240,255), Dim=Color.FromArgb(162,154,174);
        private static readonly Color Green=Color.FromArgb(112,235,177), Amber=Color.FromArgb(255,191,89), Red=Color.FromArgb(255,100,126);
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
        private void Status(string status,string error=null) {displayStatus=status;Emit("status","connected",connected,"displayStatus",status,"error",error);}
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
                        else if(op=="display"){connectRequested=Values.Bool(command,"connect");if(!connectRequested)Status("disconnecting");}
                        else if(op=="capture_sources")CaptureSources();
                        else if(op=="sensors")RequestSensorPermission(Values.Bool(command,"enable"));
                        else if(op=="quit"){running=false;break;}
                        else Emit("status","error","Unknown worker command");
                    }catch(Exception ex){Emit("status","error","Worker command failed; monitoring remains active.","command",CommandHint(line),"inputLength",line.Length,"exceptionType",ex.GetType().FullName,"methods",ExceptionMethods(ex));}
                }
            } catch(Exception ex){Emit("status","error","Worker input ended unexpectedly","exceptionType",ex.GetType().FullName,"methods",ExceptionMethods(ex));}
            finally{running=false;frameReady.Set();}
        }
        private void WriteFrames()
        {
            long last=0;
            try {
                while(running){frameReady.WaitOne(1000);byte[] frame;long number;lock(gate){frame=latest;number=sequence;}if(frame==null||number==last)continue;
                    byte[] length=BitConverter.GetBytes(frame.Length);output.Write(length,0,4);output.Write(frame,0,frame.Length);output.Flush();last=number;
                }
            }catch(IOException){running=false;}catch(ObjectDisposedException){running=false;}
        }
        internal int Run()
        {
            new Thread(ReadInput){IsBackground=true,Name="MASTER input"}.Start();
            new Thread(WriteFrames){IsBackground=true,Name="MASTER preview pipe"}.Start();
            usbThread=new Thread(UsbLoop){IsBackground=true,Name="MASTER LCD"};usbThread.Start();
            Emit("status","connected",false,"displayStatus","disconnected","protocolVersion",1);
            double nextReport=0;long previousSequence=0;double previousTime=0;
            while(running){
                var watch=Stopwatch.StartNew();var config=Settings();int fps=Values.Int(config,"fps",10);if(fps!=5&&fps!=10&&fps!=15&&!(Values.Bool(config,"diagnostic")&&(fps==20||fps==30)))fps=10;
                Dictionary<string,object> state;lock(gate)state=new Dictionary<string,object>(snapshot);
                if(watch.ElapsedMilliseconds<1000&&clock.Elapsed.TotalSeconds>=nextSensorRead){ReadSensors();nextSensorRead=clock.Elapsed.TotalSeconds+1;}
                if(lastSensors.Count>0)state["temperatures"]=lastSensors;
                try {
                    using(var bitmap=DrawFrame(state,config,clock.Elapsed.TotalSeconds))using(var stream=new MemoryStream())using(var parameters=new EncoderParameters(1)){
                        parameters.Param[0]=new EncoderParameter(System.Drawing.Imaging.Encoder.Quality,85L);bitmap.Save(stream,jpegCodec,parameters);
                        byte[] frame=stream.ToArray();if(frame.Length>MaxJpeg)throw new InvalidDataException("Encoded image exceeds slot");
                        lock(gate){latest=frame;sequence++;encodedBytes=frame.Length;}frameReady.Set();
                    }
                } catch(Exception ex){Emit("status","error","Render failed: "+ex.GetType().Name);}
                encodeMs=watch.Elapsed.TotalMilliseconds;
                double now=clock.Elapsed.TotalSeconds;
                if(now>=nextReport){long current;lock(gate)current=sequence;using(var process=Process.GetCurrentProcess())Emit("stats","renderFps",(current-previousSequence)/Math.Max(.001,now-previousTime),"fps",fps,"encodedBytes",encodedBytes,"encodeMs",encodeMs,"ackMs",ackMs,"acknowledgedFrames",acknowledged,"droppedFrames",dropped,"frameSequence",current,"workingSetBytes",process.WorkingSet64,"cpuTotalMs",process.TotalProcessorTime.TotalMilliseconds);previousSequence=current;previousTime=now;nextReport=now+2;}
                int wait=(int)Math.Max(1,1000.0/fps-watch.Elapsed.TotalMilliseconds);Thread.Sleep(wait);
            }
            connectRequested=false;StopSensorBroker();if(usbThread!=null)usbThread.Join(3500);if(sensorBrokerThread!=null)sensorBrokerThread.Join(1500);return 0;
        }
        private static bool OtherWriter(int ownPid=0)
        {
            foreach(string name in new[]{"TRCC","USBLCDNEW"})foreach(var p in Process.GetProcessesByName(name))using(p){if(p.Id!=ownPid)return true;}return false;
        }
        private bool WaitIdle(int timeout)
        {
            var wait=Stopwatch.StartNew();while(running&&wait.ElapsedMilliseconds<timeout){if(view.ReadByte(2)==0)return true;Thread.Sleep(2);}return false;
        }
        private void ConnectUsb()
        {
            if(OtherWriter())throw new InvalidOperationException("TRCC or another LCD writer is running. Close it, then Connect display.");
            string directory=@"C:\Program Files\TRCCCAP", exe=Path.Combine(directory,"USBLCDNEW.exe"), dll=Path.Combine(directory,"USBLCDNEW.dll");
            string pin=Values.Text(Settings(),"helperSha256");
            ValidateHelperPins(false);
            StartSensors();
            map=MemoryMappedFile.CreateNew("shareMemory_ImageRGB",34560000);view=map.CreateViewAccessor(0,0,MemoryMappedFileAccess.ReadWrite);
            var start=new ProcessStartInfo(exe){WorkingDirectory=directory,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardError=true,RedirectStandardOutput=true};
            helperJob=NewHelperJob();
            helper=Process.Start(start);if(!AssignProcessToJobObject(helperJob,helper.Handle))throw new IOException("Cannot contain LCD helper; connection refused.");helper.OutputDataReceived+=delegate{};helper.ErrorDataReceived+=delegate{};helper.BeginOutputReadLine();helper.BeginErrorReadLine();
            var wait=Stopwatch.StartNew();bool discovered=false;
            while(running&&connectRequested&&wait.ElapsedMilliseconds<12000){if(helper.HasExited)throw new InvalidOperationException("Vendor LCD helper exited.");if(view.ReadByte(6)==0xDC&&view.ReadByte(4)==4&&view.ReadByte(2)==0x48){discovered=true;break;}Thread.Sleep(25);}
            if(!discovered)throw new InvalidOperationException("Expected 480 x 480 Peerless Vision PM4 handshake was not received.");
            view.Write(0L,(byte)0);view.Write(1L,(byte)1);view.Write(2L,(byte)0);connected=true;Status("connected");
        }
        internal static byte[] Packet(byte[] jpeg)
        {
            if(jpeg==null||jpeg.Length<4||jpeg.Length>MaxJpeg||jpeg[0]!=255||jpeg[1]!=216||jpeg[jpeg.Length-2]!=255||jpeg[jpeg.Length-1]!=217)throw new InvalidDataException("Invalid JPEG frame");
            using(var stream=new MemoryStream(jpeg,false))using(var image=Image.FromStream(stream)){if(image.Width!=480||image.Height!=480)throw new InvalidDataException("Frame must be 480 x 480");}
            byte[] result=new byte[jpeg.Length+64];result[0]=0x12;result[1]=0x34;result[2]=0x56;result[3]=0x78;
            foreach(var field in new[]{new[]{4,2},new[]{8,480},new[]{12,480},new[]{56,2},new[]{60,jpeg.Length}})Array.Copy(BitConverter.GetBytes(field[1]),0,result,field[0],4);
            Array.Copy(jpeg,0,result,64,jpeg.Length);return result;
        }
        private void UsbLoop()
        {
            long last=0;double conflictCheck=0,lastSend=0,lastChanged=0;
            while(running){
                if(!connectRequested){if(connected||helper!=null){StopUsb();Status("disconnected");}Thread.Sleep(50);continue;}
                try {
                    if(!connected){Status("connecting");ConnectUsb();last=0;lastChanged=clock.Elapsed.TotalSeconds;}
                    if(helper==null||helper.HasExited)throw new IOException("LCD helper exited; reconnect explicitly.");
                    if(clock.Elapsed.TotalSeconds>=conflictCheck){if(OtherWriter(helper.Id))throw new IOException("Another LCD writer started; custom display stopped.");conflictCheck=clock.Elapsed.TotalSeconds+1;}
                    byte[] jpeg;long number;lock(gate){jpeg=latest;number=sequence;}
                    if(jpeg==null){Thread.Sleep(20);continue;}
                    if(number!=last)lastChanged=clock.Elapsed.TotalSeconds;
                    else {
                        if(clock.Elapsed.TotalSeconds-lastChanged>3)throw new IOException("Renderer stopped producing fresh frames; LCD stream stopped.");
                        if(clock.Elapsed.TotalSeconds-lastSend<1){Thread.Sleep(25);continue;}
                        Emit("status","connected",true,"displayStatus","render_stale","error","Repeating the last picture while awaiting a fresh frame.");
                    }
                    var ack=Stopwatch.StartNew();if(!WaitIdle(1500))throw new IOException("LCD helper acknowledgment timed out.");
                    if(view.ReadByte(0)!=0||view.ReadByte(1)!=1)throw new IOException("LCD device readiness changed.");
                    byte[] packet=Packet(jpeg);view.WriteArray(Slot,packet,0,packet.Length);view.Write(2L,(byte)1);view.Flush();
                    if(!WaitIdle(1500))throw new IOException("LCD picture was not acknowledged.");
                    ackMs=ack.Elapsed.TotalMilliseconds;acknowledged++;if(last>0&&number>last+1)dropped+=number-last-1;last=number;lastSend=clock.Elapsed.TotalSeconds;delivered++;
                }catch(Exception ex){connectRequested=false;StopUsb();Status("unavailable",ex.Message);}
            }
            StopUsb();
        }
        private void StopUsb()
        {
            connected=false;
            var owned=helper;helper=null;
            if(owned!=null){try{if(!owned.HasExited){owned.Kill();owned.WaitForExit(2000);}}catch{}finally{owned.Dispose();}}
            if(helperJob!=IntPtr.Zero){CloseHandle(helperJob);helperJob=IntPtr.Zero;}
            StopSensorBroker();lock(sensorGate){StopOwnedNormalSensor();if(sensorBrokerThread==null||!sensorBrokerThread.IsAlive)CloseSensorMap();}
            if(view!=null){view.Dispose();view=null;}if(map!=null){map.Dispose();map=null;}
        }

        private static bool Waiting(string state){return state=="waiting"||state=="needs_input"||state=="waiting_for_input"||state=="waiting_on_approval";}
        private static Color StateColor(string state){if(state=="failed"||state=="error"||state=="test_failed")return Red;if(Waiting(state))return Amber;if(state=="passed"||state=="verified_success")return Green;if(state=="working"||state=="running")return Lilac;return Dim;}
        private static string Activity(Dictionary<string,object> project){var activity=Values.Get(project,"activity");if(activity is string)return(string)activity;var info=Values.Obj(activity);return Values.Bool(info,"stale")?"stale":Values.Text(info,"status","unknown");}
        private static string Human(string value){return value.Replace('_',' ').Replace('-',' ');}
        private static bool Fresh(string value,double seconds){DateTime when;return DateTime.TryParse(value,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out when)&&(DateTime.UtcNow-when.ToUniversalTime()).TotalSeconds>=0&&(DateTime.UtcNow-when.ToUniversalTime()).TotalSeconds<=seconds;}
        internal static bool SnapshotFresh(Dictionary<string,object> state){return Fresh(Values.Text(state,"utc"),15);}
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
            for(int n=0;n<2;n++){double head=((time*rate+index*.23+n*.5)%1)*length;for(int j=4;j>=0;j--){double d=head-j*9;if(d<0)continue;var p=PathPoint(points,d);Binary(g,p.X,p.Y,j==0?White:color,(1-j/6.0)*.95,n+j+(int)(time*2));}}
            for(int n=0;n<16;n++){
                double life=(time*(status=="waiting"?.55:1.1)+n*.618+index*.17)%1,d=((n*.38197+time*.18)%1)*length;
                var p=PathPoint(points,d);var before=PathPoint(points,Math.Max(0,d-.5));var after=PathPoint(points,Math.Min(length,d+.5));bool horizontal=Math.Abs(after.X-before.X)>Math.Abs(after.Y-before.Y);
                float drift=(float)((3+life*13)*(n%2==1?1:-1));Binary(g,p.X+(horizontal?(float)Math.Sin(n*3+life*4)*2:drift),p.Y+(horizontal?drift:(float)Math.Sin(n*3+life*4)*2),n%6==0?White:color,Math.Sin(life*Math.PI)*.7,n+index+(int)(time*2));
            }
        }
        internal Bitmap DrawFrame(Dictionary<string,object> state,Dictionary<string,object> config,double time)
        {
            var bitmap=new Bitmap(480,480,PixelFormat.Format24bppRgb);
            using(var g=Graphics.FromImage(bitmap)){
                g.Clear(Color.Black);g.SmoothingMode=SmoothingMode.AntiAlias;g.TextRenderingHint=System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                Border(g,new Rectangle(6,6,468,468),Color.FromArgb(73,36,94));bool fresh=SnapshotFresh(state);
                var projects=Projects(state);int focus=FocusIndex(projects,config,time),page=focus/3*3;
                DrawViolet(g,state,config,time,new Rectangle(174,9,132,143));
                string overall="unknown",source="NO LIVE STATUS";
                if(fresh){foreach(string priority in new[]{"test_failed","waiting","working"}){foreach(var project in projects)if(ProjectState(project)==priority){overall=priority;break;}if(overall!="unknown")break;}
                    var violet=Values.Obj(Values.Get(state,"violet"));bool reported=Values.Bool(violet,"connected")&&!Values.Bool(violet,"stale");
                    source=overall=="test_failed"?"CHECK RESULT":overall!="unknown"?"PROJECT ACTIVITY":reported?Values.Text(violet,"source","UNVERIFIED").ToUpperInvariant():"NO LIVE STATUS";
                }
                Text(g,source,detail,Lilac,new RectangleF(310,60,158,26));var attention=Values.Obj(Values.Get(state,"attention"));int pending=Values.Int(attention,"pendingCount");
                string alerts=fresh&&Values.Bool(attention,"available")?(pending==0?"No pending alerts":pending+" alert"+(pending==1?"":"s")+" to review"):"Alerts unavailable";
                Text(g,alerts,detail,pending>0&&fresh?Amber:Color.FromArgb(226,177,255),new RectangleF(310,89,158,25));
                for(int slot=0;slot<3&&page+slot<projects.Count;slot++)DrawWire(g,87+slot*153,fresh?ProjectState(projects[page+slot]):"stale",slot,time);
                for(int slot=0;slot<3&&page+slot<projects.Count;slot++)DrawRoom(g,projects[page+slot],new Rectangle(18+slot*153,177,138,155),page+slot==focus,time,slot,fresh);
                var selected=projects[focus];string status=fresh?ProjectState(selected):"stale";Border(g,new Rectangle(18,349,444,42),StateColor(status));
                string summary=status=="stale"?"Activity update stale":status=="unknown"?"Status unavailable":status=="test_failed"?"Build/test check failed":status=="waiting"?"Waiting for your input":status=="turn_finished"?"Chat turn finished":Human(StatusLabel(status));
                string paging=projects.Count>3?" ["+(page/3+1)+"/"+((projects.Count+2)/3)+"]":"";
                Text(g,Values.Text(selected,"label",Values.Text(selected,"id"))+" · "+summary+paging,roomFont,White,new RectangleF(31,358,418,24));
                var temperatures=Values.Obj(Values.Get(state,"temperatures"));DrawTemperature(g,"CPU",fresh?Values.Obj(Values.Get(temperatures,"cpu")):new Dictionary<string,object>(),new Rectangle(18,410,214,49));DrawTemperature(g,"GPU",fresh?Values.Obj(Values.Get(temperatures,"gpu")):new Dictionary<string,object>(),new Rectangle(248,410,214,49));
                DrawVioletStatus(g,state);
                if(Values.Bool(config,"diagnostic")){int x=20+(int)(time*65)%400;using(var brush=new SolidBrush(Lilac))g.FillRectangle(brush,x,466,32,3);Text(g,"TEST FRAME "+sequence,small,White,new RectangleF(16,16,145,20));}
                else if(Values.Bool(state,"demo"))Text(g,"DEMO",small,Amber,new RectangleF(16,16,70,20));
            }return bitmap;
        }
        private void DrawRoom(Graphics g,Dictionary<string,object> project,Rectangle rect,bool focus,double time,int index,bool fresh)
        {
            string status=fresh?ProjectState(project):"stale";Color color=StateColor(status);bool active=status=="working",motion=active||status=="waiting"||status=="test_failed";
            Border(g,rect,motion?color:Color.FromArgb(105,87,127));Text(g,Values.Text(project,"label",Values.Text(project,"id")),roomFont,White,new RectangleF(rect.Left+10,rect.Top+8,rect.Width-20,24));
            using(var pen=new Pen(Color.FromArgb(80,54,95),1)){g.DrawLine(pen,rect.Left+9,rect.Top+38,rect.Right-9,rect.Top+38);g.DrawLine(pen,rect.Left+9,rect.Bottom-36,rect.Right-9,rect.Bottom-36);}
            if(motion){var saved=g.Save();g.SetClip(new Rectangle(rect.Left+8,rect.Top+44,rect.Width-16,rect.Height-82));int count=active?24:18;
                for(int n=0;n<count;n++){double life=(time*(active?.65:.4)+n*.618+index*.29)%1,angle=n*2.39996+time*(n%2==1?-.4:.4),radius=17+life*34;Binary(g,rect.Left+rect.Width/2+(float)(Math.Cos(angle)*radius),rect.Top+85+(float)(Math.Sin(angle)*radius*.58-life*6),n%8==0?White:color,Math.Sin(life*Math.PI)*.8,n+index+(int)(time*1.5));}
                if(active)for(int n=0;n<6;n++){float yy=rect.Top+45+(float)((time*30+n*19+index*27)%(rect.Height-82));Binary(g,rect.Left+13+n*20,yy,color,.32,n+(int)time);Binary(g,rect.Left+13+n*20,yy-11,color,.16,n+1+(int)time);}g.Restore(saved);
            }
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
            using(var brush=new SolidBrush(White))g.DrawString(label,temperatureLabel,brush,new PointF(left,rect.Top+10),StringFormat.GenericTypographic);
            using(var brush=new SolidBrush(value=="—"?Dim:Lilac))g.DrawString(value,temperatureValue,brush,new PointF(left+a+10,rect.Top+7),StringFormat.GenericTypographic);
        }
        private void DrawViolet(Graphics g,Dictionary<string,object> state,Dictionary<string,object> config,double time,Rectangle target)
        {
            if(Values.Text(config,"mode")=="live_mini"){
                string reason;using(var capture=CaptureMini(config,out reason)){string next=capture==null?"unavailable":"live";if(next!=captureStatus){captureStatus=next;Emit("capture","status",next,"valid",capture!=null,"reason",reason,"message",reason);}if(capture!=null){g.DrawImage(capture,target);return;}}
                Text(g,"MINI",small,Amber,new RectangleF(target.Left,target.Top+20,target.Width,20),StringAlignment.Center);Text(g,"UNAVAILABLE",small,Dim,new RectangleF(target.Left-10,target.Top+40,target.Width+20,20),StringAlignment.Center);return;
            }
            if(captureStatus!="dashboard"){captureStatus="dashboard";Emit("capture","status","dashboard","valid",false,"reason",null,"message",null);}
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
                    ownedSensor=Process.Start(new ProcessStartInfo(@"C:\Program Files\TRCCCAP\HWINFO.exe"){WorkingDirectory=@"C:\Program Files\TRCCCAP",UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardOutput=true,RedirectStandardError=true});
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
        private double nextSensorRead;
        private static Dictionary<string,object> MissingSensor(){return new Dictionary<string,object>{{"value",null},{"unit","C"},{"status","unavailable"}};}
        private void ReadSensors()
        {
            var result=new Dictionary<string,object>{{"cpu",MissingSensor()},{"gpu",MissingSensor()},{"status","unavailable"},{"observedAt",DateTime.UtcNow.ToString("o")}};
            try {
                var publishers=Process.GetProcessesByName("HWINFO");bool exists=publishers.Length>0;foreach(var process in publishers)process.Dispose();if(!exists)throw new IOException("Sensor publisher is not running.");
                ValidateHelperPins(true);
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
                var groups=Values.Obj(Decode(text.Substring(start)));Dictionary<string,object> cpu=null,gpu=null;
                foreach(var group in groups){string name=group.Key.ToLowerInvariant();bool isCpu=name.StartsWith("cpu [#0]:")&&name.Contains("enhanced"),isGpu=name.StartsWith("gpu [#0]:");if(!isCpu&&!isGpu)continue;
                    foreach(var sensor in Values.Rows(group.Value))foreach(var pair in sensor){var reading=Values.Obj(pair.Value);if(Values.Text(reading,"type")!="temperature")continue;if((isCpu&&pair.Key=="CPU (Tctl/Tdie)")||(isGpu&&pair.Key=="GPU Temperature")){
                        double value;if(!double.TryParse(Values.Text(reading,"value"),NumberStyles.Float,CultureInfo.InvariantCulture,out value)||double.IsNaN(value)||double.IsInfinity(value)||value<0||value>=150)continue;
                        var item=new Dictionary<string,object>{{"value",value},{"unit","C"},{"status","live"},{"label",pair.Key},{"source","Vendor sensor publisher"},{"observedAt",DateTime.UtcNow.ToString("o")}};if(isCpu)cpu=item;else gpu=item;
                    }}
                }
                if(cpu!=null)result["cpu"]=cpu;if(gpu!=null)result["gpu"]=gpu;result["status"]=cpu!=null&&gpu!=null?"live":"partial";
            }catch {
                bool normalActive=false;lock(sensorGate){try{normalActive=ownedSensor!=null&&!ownedSensor.HasExited;}catch{}}
                bool initializing=(sensorBrokerActive||normalActive)&&(DateTime.UtcNow-ownedSensorStarted).TotalSeconds<60;
                result["status"]=initializing?"initializing":"unavailable";
                result["reason"]=initializing?"Vendor sensor publisher is initializing (up to 60 seconds).":"Vendor sensor source absent, stale, or unverified.";
            }
            lastSensors=result;Emit("sensors","temperatures",result);
        }
        public void Dispose(){running=false;connectRequested=false;StopSensorBroker();frameReady.Set();if(usbThread!=null&&usbThread.IsAlive)usbThread.Join(3500);if(sensorBrokerThread!=null&&sensorBrokerThread.IsAlive)sensorBrokerThread.Join(1500);if(usbThread==null)StopUsb();if(violetAtlas!=null)violetAtlas.Dispose();title.Dispose();roomFont.Dispose();mono.Dispose();small.Dispose();detail.Dispose();binaryFont.Dispose();temperatureLabel.Dispose();temperatureValue.Dispose();}
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
