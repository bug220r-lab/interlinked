// Synthetic rendering and telemetry-contract checks only. Never calls Worker.Run,
// USB acquisition, capture, ReadSensors, or sensor-permission methods.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace InterlinkedMaster
{
    internal static class MasterRenderFixture
    {
        private static readonly List<string> Checks = new List<string>();
        private static Dictionary<string,object> Obj(params object[] pairs)
        {
            var value=new Dictionary<string,object>();
            for(int i=0;i<pairs.Length;i+=2)value[(string)pairs[i]]=pairs[i+1];
            return value;
        }
        private static string When(double seconds=0) { return DateTime.UtcNow.AddSeconds(seconds).ToString("o",CultureInfo.InvariantCulture); }
        private static void Check(bool value,string label)
        {
            if(!value)throw new InvalidOperationException(label);
            Checks.Add(label);
        }
        private static Dictionary<string,object> Project(string id,string activity,string health="unknown")
        {
            return Obj("id",id,"label",id,"activity",Obj("status",activity,"source","Synthetic fixture","observedAt",When(),"stale",false),
                "health",Obj("status",health,"observedAt",When(),"source","Synthetic test result"),"workspace",Obj("status","unknown"));
        }
        private static Dictionary<string,object> Sensor(object value,string status="live",double age=0)
        {
            return Obj("value",value,"status",status,"observedAt",When(age),"stale",false);
        }
        private static Dictionary<string,object> Subagents(object count,string status="observed",double age=0)
        {
            return Obj("workingCount",count,"status",status,"observedAt",When(age),"source","Synthetic observed lifecycle");
        }
        private static Dictionary<string,object> State(params Dictionary<string,object>[] projects)
        {
            return Obj("utc",When(),"projects",projects,"violet",Obj("connected",false,"stale",false),
                "attention",Obj("available",true,"pendingCount",2),
                "temperatures",Obj("cpu",Sensor(57),"gpu",Sensor(49)));
        }
        private static void ContractChecks()
        {
            Check(Worker.ProjectState(Project("A","working"))=="working","Working lifecycle is active");
            Check(Worker.ProjectState(Project("A","waiting"))=="waiting","Input request is waiting");
            Check(Worker.ProjectState(Project("A","waiting","failed"))=="test_failed","Evidenced test failure takes priority over input");
            Check(Worker.ProjectState(Project("A","turn_finished"))=="turn_finished","Turn completion remains distinct from test success");
            Check(Worker.ProjectState(Project("A","interrupted"))=="interrupted","Interrupted activity is preserved");
            var old=Project("A","working");Values.Obj(old["activity"])["stale"]=true;
            Check(Worker.ProjectState(old)=="stale","Stale activity cannot remain working");
            var priorFailure=Project("A","unknown");Values.Obj(priorFailure["health"])["lastResult"]="failed";
            Check(Worker.ProjectState(priorFailure)!="test_failed","Previous noncurrent result is not a current failure");
            var busy=Project("A","unknown");busy["workspace"]=Obj("status","running","observedAt",When(),"source","Synthetic coordinator");
            Check(Worker.ProjectState(busy)=="working","Fresh coordinator work remains visible without lifecycle data");
            busy["workspace"]=Obj("status","running","observedAt",When(-30),"source","Synthetic coordinator");
            Check(Worker.ProjectState(busy)!="working","Stale coordinator work cannot claim activity");
            busy["workspace"]=Obj("status","idle","observedAt",When());
            Check(Worker.ProjectState(busy)=="idle","Fresh idle workspace is not working");
            Check(Worker.StatusLabel("test_failed").IndexOf("CHECK",StringComparison.OrdinalIgnoreCase)>=0,"Failure label identifies build or test check evidence");
            Check(Worker.StatusLabel("turn_finished")!=Worker.StatusLabel("passed"),"Finished-turn and passed-test labels differ");

            Check(Worker.TemperatureText(Sensor(55))=="55\u00b0C","Fresh native live reading is displayed");
            Check(Worker.TemperatureText(Sensor(42,"available"))=="42\u00b0C","Fresh normalized backend reading is displayed");
            Check(Worker.TemperatureText(Sensor(61,"verified"))=="61\u00b0C","Fresh verified reading is displayed");
            foreach(object invalid in new object[]{double.NaN,double.PositiveInfinity,double.NegativeInfinity,true,-1,150,900})
                Check(Worker.TemperatureText(Sensor(invalid))=="\u2014","Invalid temperature rejected: "+Convert.ToString(invalid,CultureInfo.InvariantCulture));
            Check(Worker.TemperatureText(Sensor(55,"unavailable"))=="\u2014","Unverified reading is unavailable");
            Check(Worker.TemperatureText(Sensor(55,"live",-6))=="\u2014","Expired sensor timestamp is unavailable");
            Check(Worker.TemperatureText(Sensor(55,"live",30))=="\u2014","Future sensor timestamp is unavailable");
            var missingTime=Sensor(55);missingTime.Remove("observedAt");
            Check(Worker.TemperatureText(missingTime)=="\u2014","Missing sensor timestamp is unavailable");
            var stale=Sensor(55);stale["stale"]=true;
            Check(Worker.TemperatureText(stale)=="\u2014","Explicit stale sensor is unavailable");
            Check(Worker.TemperatureText(Obj())=="\u2014","Missing sensor is unavailable");
            Check(Worker.SnapshotFresh(Obj("utc",When())),"Current snapshot is fresh");
            Check(!Worker.SnapshotFresh(Obj("utc",When(-16))),"Expired snapshot is stale");
            Check(!Worker.SnapshotFresh(Obj("utc",When(30))),"Future snapshot is not fresh");
            Check(!Worker.SnapshotFresh(Obj()),"Missing snapshot time is not fresh");

            foreach(int count in new[]{0,1,3,100,256})
                Check(Worker.SubagentText(Subagents(count))==count.ToString(CultureInfo.InvariantCulture),"Observed subagent count formats: "+count);
            Check(Worker.SubagentText(Subagents(3,"partial"))=="3?","Incomplete subagent tracking has a question mark");
            Check(Worker.SubagentText(Subagents(0,"partial"))=="0?","Partial zero remains explicitly uncertain");
            foreach(object invalid in new object[]{null,true,-1,257,3.0,"3",double.NaN,double.PositiveInfinity})
                Check(Worker.SubagentText(Subagents(invalid))=="\u2014","Invalid subagent count rejected: "+Convert.ToString(invalid,CultureInfo.InvariantCulture));
            Check(Worker.SubagentText(Subagents(3,"unavailable"))=="\u2014","Unavailable source cannot claim a count");
            Check(Worker.SubagentText(Subagents(3,"observed",-181))=="\u2014","Expired child evidence cannot claim a count");
            Check(Worker.SubagentText(Subagents(3,"observed",30))=="\u2014","Future child evidence cannot claim a count");
            Check(Worker.SubagentText(Subagents(3),false)=="\u2014","Whole snapshot staleness hides a fresh child count");
            Check(Worker.SubagentText(Obj())=="\u2014","Legacy snapshot without counter displays unavailable");
            var noChildTime=Subagents(3);noChildTime.Remove("observedAt");
            Check(Worker.SubagentText(noChildTime)=="\u2014","Missing child observation time is unavailable");

            var projects=new List<Dictionary<string,object>>{Project("A","waiting"),Project("B","working"),Project("C","unknown"),Project("D","unknown","failed")};
            Check(Worker.FocusIndex(projects,Obj(),0)==3,"Failure outside first page takes priority over waiting");
            projects[3]=Project("D","unknown");
            Check(Worker.FocusIndex(projects,Obj("focusProject","B"),0)==0,"Input request overrides requested focus");
            projects[0]=Project("A","unknown");
            Check(Worker.FocusIndex(projects,Obj("focusProject","D"),0)==3,"Explicit focus selects a later project");
            Check(Worker.FocusIndex(projects,Obj("rotationSeconds",8),0)==0 && Worker.FocusIndex(projects,Obj("rotationSeconds",8),8)==1,"Quiet focus rotates across registered projects");
        }
        private static bool SamePixels(Bitmap a,Bitmap b,Rectangle region)
        {
            for(int y=region.Top;y<region.Bottom;y++)for(int x=region.Left;x<region.Right;x++)
                if(a.GetPixel(x,y).ToArgb()!=b.GetPixel(x,y).ToArgb())return false;
            return true;
        }
        private static void RenderChecks(string directory,string atlas)
        {
            var config=Obj("diagnostic",false,"rotationSeconds",8);
            if(atlas!=null)config["violetAtlasPath"]=atlas;
            using(var worker=new Worker())
            {
                var countedRows=new[]{Project("ED-IT Buddy","unknown"),Project("MW5 MOD","working"),Project("INTERLINKED","working")};
                countedRows[0]["subagents"]=Subagents(0);countedRows[1]["subagents"]=Subagents(1);countedRows[2]["subagents"]=Subagents(255);
                var counted=State(countedRows);counted["subagents"]=Subagents(256);
                using(var image=worker.DrawFrame(counted,config,1.1))image.Save(Path.Combine(directory,"subagent-counts.png"),ImageFormat.Png);
                counted["subagents"]=Subagents(256,"partial");countedRows[1]["subagents"]=Subagents(1,"partial");
                countedRows[2]["subagents"]=Subagents(null,"unavailable");
                using(var image=worker.DrawFrame(counted,config,1.1))image.Save(Path.Combine(directory,"subagent-partial.png"),ImageFormat.Png);
                using(var image=new Bitmap(480,480))using(var g=Graphics.FromImage(image))using(var font=new Font("Segoe UI",16,FontStyle.Bold,GraphicsUnit.Pixel))
                    Check(g.MeasureString("SUBAGENTS 256?",font).Width<=158,"Maximum partial count fits beside Violet without truncation");
                var hiddenCount=State(countedRows);hiddenCount["utc"]=When(-30);hiddenCount["subagents"]=Subagents(256);
                var hiddenMissing=State(countedRows);hiddenMissing["utc"]=hiddenCount["utc"];
                foreach(var row in countedRows)row["subagents"]=Subagents(null,"unavailable");
                using(var first=worker.DrawFrame(hiddenCount,config,1.1))using(var second=worker.DrawFrame(hiddenMissing,config,1.1))
                {
                    Check(SamePixels(first,second,new Rectangle(310,118,158,25)),"Stale frame total matches unavailable presentation");
                    first.Save(Path.Combine(directory,"subagent-stale.png"),ImageFormat.Png);
                }
                var active=State(Project("ED-IT Buddy","stale"),Project("MW5 MOD","working"),Project("INTERLINKED","working"));
                using(var first=worker.DrawFrame(active,config,.25))using(var second=worker.DrawFrame(active,config,1.4))
                {
                    Check(first.Width==480&&first.Height==480,"Render dimensions remain exactly 480 by 480");
                    Check(!SamePixels(first,second,new Rectangle(18,177,444,155)),"Real activity changes station pixels with diagnostics disabled");
                    first.Save(Path.Combine(directory,"working.png"),ImageFormat.Png);
                    second.Save(Path.Combine(directory,"working-later.png"),ImageFormat.Png);
                }
                var inactive=State(Project("ED-IT Buddy","stale"),Project("MW5 MOD","unknown"),Project("INTERLINKED","stale"));
                using(var first=worker.DrawFrame(inactive,config,.25))using(var second=worker.DrawFrame(inactive,config,1.4))
                {
                    Check(SamePixels(first,second,new Rectangle(18,177,444,155)),"Stale and unknown stations do not animate active work");
                    first.Save(Path.Combine(directory,"stale-unavailable.png"),ImageFormat.Png);
                }
                var manyAlerts=State(Project("ED-IT Buddy","working"),Project("MW5 MOD","working"),Project("INTERLINKED","working"));
                manyAlerts["attention"]=Obj("available",true,"pendingCount",16);
                using(var image=worker.DrawFrame(manyAlerts,config,1.1))image.Save(Path.Combine(directory,"many-alerts.png"),ImageFormat.Png);
                var missingSensors=State(Project("ED-IT Buddy","unknown"),Project("MW5 MOD","unknown"),Project("INTERLINKED","unknown"));
                missingSensors["temperatures"]=Obj();
                using(var image=worker.DrawFrame(missingSensors,config,1.1))image.Save(Path.Combine(directory,"missing-sensors.png"),ImageFormat.Png);
                var waiting=State(Project("ED-IT Buddy","working"),Project("MW5 MOD","waiting"),Project("INTERLINKED","working"));
                using(var image=worker.DrawFrame(waiting,config,1.1))image.Save(Path.Combine(directory,"waiting.png"),ImageFormat.Png);
                var failure=State(Project("ED-IT Buddy","working"),Project("MW5 MOD","working","failed"),Project("INTERLINKED","working"));
                using(var image=worker.DrawFrame(failure,config,1.1))image.Save(Path.Combine(directory,"test-failed.png"),ImageFormat.Png);
                active["utc"]=When(-30);
                using(var first=worker.DrawFrame(active,config,.25))using(var second=worker.DrawFrame(active,config,1.4))
                {
                    Check(SamePixels(first,second,new Rectangle(18,177,444,155)),"Stale whole snapshot cannot keep working stations animated");
                    first.Save(Path.Combine(directory,"stale-snapshot.png"),ImageFormat.Png);
                }
                foreach(int count in new[]{0,1,4,12})
                {
                    var rows=new List<Dictionary<string,object>>();
                    for(int i=0;i<count;i++){var row=Project("Project "+(i+1),"unknown",i==count-1?"failed":"unknown");row["subagents"]=Subagents(i);rows.Add(row);}
                    var pagedState=State(rows.ToArray());pagedState["subagents"]=Subagents(count*(count-1)/2);
                    using(var image=worker.DrawFrame(pagedState,config,1))
                    {
                        Check(image.Width==480&&image.Height==480,"Project count "+count+" renders safely");
                        if(count==4)image.Save(Path.Combine(directory,"later-project-focus.png"),ImageFormat.Png);
                    }
                }
            }
        }
        [STAThread]
        private static int Main(string[] args)
        {
            Console.OutputEncoding=new UTF8Encoding(false);
            var report=new Dictionary<string,object>();
            try
            {
                if(args.Length<1||args.Length>2)throw new ArgumentException("Pass a fresh output directory and optional approved atlas path.");
                string directory=Path.GetFullPath(args[0]);
                if(Directory.Exists(directory)||File.Exists(directory))throw new IOException("Use an absent fixture output directory.");
                Directory.CreateDirectory(directory);
                string atlas=args.Length==2?Path.GetFullPath(args[1]):null;
                if(atlas!=null)
                {
                    using(var image=Image.FromFile(atlas))if(image.Width!=1536||image.Height!=2288)throw new InvalidDataException("Expected approved v2 atlas dimensions.");
                    using(var file=File.OpenRead(atlas))using(var sha=SHA256.Create())report["atlasSha256"]=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant();
                }
                ContractChecks();RenderChecks(directory,atlas);
                report["ok"]=true;report["checks"]=Checks;report["hardwareTested"]=false;report["sensorPublisherStarted"]=false;report["usbRequested"]=false;
                string json=new JavaScriptSerializer().Serialize(report);
                File.WriteAllText(Path.Combine(directory,"render-report.json"),json,new UTF8Encoding(false));Console.WriteLine(json);return 0;
            }
            catch(Exception ex){Console.Error.WriteLine(ex.ToString());return 1;}
        }
    }
}
