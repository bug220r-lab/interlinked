// Synthetic status/render checks only. Never starts the worker, USB or sensors.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class VioletStatusFixture
    {
        private static readonly List<string> checks=new List<string>();
        private static Dictionary<string,object> Obj(params object[] fields){var result=new Dictionary<string,object>();for(int i=0;i<fields.Length;i+=2)result[(string)fields[i]]=fields[i+1];return result;}
        private static string When(double seconds=0){return DateTime.UtcNow.AddSeconds(seconds).ToString("o",CultureInfo.InvariantCulture);}
        private static Dictionary<string,object> State(string status="working",string reporter="local_task")
        {
            var projects=new List<Dictionary<string,object>>();foreach(string name in new[]{"ED-IT Buddy","MW5 MOD","INTERLINKED"})projects.Add(Obj("id",name,"label",name,"activity",Obj("status","working"),"health",Obj("status","unknown")));
            return Obj("utc",When(),"demo",false,"projects",projects,"attention",Obj("available",true,"pendingCount",0),"temperatures",Obj(),
                "violet",Obj("status",status,"reporter",reporter,"connected",true,"stale",false,"observedAt",When(),"message","PRIVATE MESSAGE MUST NOT BE RENDERED"));
        }
        private static void Check(bool value,string label){if(!value)throw new Exception(label);checks.Add(label);}
        private static void Expect(Dictionary<string,object> state,string text,string label){Check(Worker.VioletStatusLine(state)==text,label);}
        private static bool Same(Bitmap first,Bitmap second,Rectangle area){for(int y=area.Top;y<area.Bottom;y++)for(int x=area.Left;x<area.Right;x++)if(first.GetPixel(x,y)!=second.GetPixel(x,y))return false;return true;}
        private static int Main(string[] args)
        {
            try{
                string output=Path.GetFullPath(args[0]);if(Directory.Exists(output))throw new Exception("Use a fresh output directory");Directory.CreateDirectory(output);
                foreach(var pair in new[]{new[]{"working","WORKING"},new[]{"waiting","WAITING"},new[]{"turn_finished","FINISHED"},new[]{"interrupted","INTERRUPTED"}}){
                    Expect(State(pair[0]),"VIOLET: "+pair[1]+" · LOCAL TASK","Local task "+pair[0]+" has its own visible label");
                    Expect(State(pair[0],"dot"),"VIOLET: "+pair[1]+" · DOT REPORT","Direct Dot "+pair[0]+" retains report provenance");
                }
                Expect(State("unknown"),"VIOLET: NO UPDATE · LOCAL TASK","Unknown is never presented as working");
                Expect(State("passed"),"VIOLET: NO UPDATE · LOCAL TASK","Build success is not a Violet activity state");
                Expect(State("working","unverified"),"VIOLET: NO UPDATE · UNVERIFIED","Unverified reporter cannot establish a live Violet state");
                var state=State();state.Remove("violet");Expect(state,"VIOLET: NO UPDATE · UNVERIFIED","Missing report is unavailable");
                state=State();Values.Obj(state["violet"])["connected"]=false;Expect(state,"VIOLET: NO UPDATE · DISCONNECTED","Disconnected report is unavailable");
                state=State();Values.Obj(state["violet"])["stale"]=true;Expect(state,"VIOLET: NO UPDATE · STALE","Explicit stale flag wins over working");
                state=State();Values.Obj(state["violet"]).Remove("stale");Expect(state,"VIOLET: NO UPDATE · STALE","Missing freshness flag fails closed");
                state=State();Values.Obj(state["violet"])["observedAt"]=When(-121);Expect(state,"VIOLET: NO UPDATE · STALE","Report older than two minutes is not live");
                state=State();state["utc"]=When(-16);Expect(state,"VIOLET: NO UPDATE · STALE FEED","Old snapshot cannot keep a report live");
                state=State();state["utc"]="invalid";Expect(state,"VIOLET: NO UPDATE · STALE FEED","Invalid snapshot timestamp is unavailable");
                state=State();Values.Obj(state["violet"])["observedAt"]=When(60);Expect(state,"VIOLET: NO UPDATE · UNKNOWN TIME","Future report timestamp is not accepted");
                state=State();Values.Obj(state["violet"])["observedAt"]="invalid";Expect(state,"VIOLET: NO UPDATE · UNKNOWN TIME","Invalid report timestamp is not accepted");
                state=State();Values.Obj(state["violet"]).Remove("observedAt");Expect(state,"VIOLET: NO UPDATE · UNKNOWN TIME","Missing observation time cannot establish freshness");
                state=State();Values.Obj(state["violet"]).Remove("observedAt");Values.Obj(state["violet"])["reportedAt"]=When();Expect(state,"VIOLET: WORKING · LOCAL TASK","Explicit reportedAt supports the additive timestamp shape");
                state=State();state["demo"]=true;Expect(state,"VIOLET: DEMO · NOT LIVE","Demo snapshot is always labeled as non-live");
                Expect(State("working","demo"),"VIOLET: DEMO · NOT LIVE","Demo reporter never claims actual Violet work");
                state=State();Values.Obj(state["violet"])["source"]="SECRET SOURCE";Check(!Worker.VioletStatusLine(state).Contains("SECRET")&&!Worker.VioletStatusLine(state).Contains("PRIVATE"),"Display uses fixed source labels, never raw message or source contents");
                var config=Obj();if(args.Length>1)config["violetAtlasPath"]=Path.GetFullPath(args[1]);
                using(var worker=new Worker())using(var working=worker.DrawFrame(State("working"),config,1.25))using(var finished=worker.DrawFrame(State("turn_finished"),config,1.25)){
                    Check(!Same(working,finished,new Rectangle(28,392,424,17)),"Violet working-to-finished changes actual status-line pixels while all projects work");
                    Check(Same(working,finished,new Rectangle(18,177,444,155)),"Violet report changes do not rewrite project-room evidence");
                    Check(Same(working,finished,new Rectangle(18,410,444,49)),"Status changes do not alter temperature evidence");
                    Check(working.GetPixel(0,0).ToArgb()==Color.Black.ToArgb(),"Background remains black");
                    working.Save(Path.Combine(output,"violet-working.png"),ImageFormat.Png);finished.Save(Path.Combine(output,"violet-finished.png"),ImageFormat.Png);
                    using(var waiting=worker.DrawFrame(State("waiting"),config,1.25))waiting.Save(Path.Combine(output,"violet-waiting.png"),ImageFormat.Png);
                    var stale=State();Values.Obj(stale["violet"])["stale"]=true;using(var image=worker.DrawFrame(stale,config,1.25))image.Save(Path.Combine(output,"violet-stale.png"),ImageFormat.Png);
                    using(var g=Graphics.FromImage(working))using(var font=new Font("Consolas",12,FontStyle.Regular,GraphicsUnit.Pixel)){
                        foreach(string line in new[]{"VIOLET: INTERRUPTED · LOCAL TASK","VIOLET: NO UPDATE · DISCONNECTED","VIOLET: NO UPDATE · UNKNOWN TIME","VIOLET: FINISHED · DOT REPORT"}){
                            var size=g.MeasureString(line,font,new PointF(0,0),StringFormat.GenericTypographic);Check(size.Width<=424&&size.Height<=17,"Native-size status line fits without ellipsis: "+line);
                        }
                    }
                }
                var report=Obj("ok",true,"synthetic",true,"count",checks.Count,"checks",checks,"usbAccessed",false,"violetStatusPublished",false);
                string json=new JavaScriptSerializer().Serialize(report);File.WriteAllText(Path.Combine(output,"report.json"),json);Console.WriteLine(json);return 0;
            }catch(Exception ex){Console.Error.WriteLine(ex.Message);return 2;}
        }
    }
}
