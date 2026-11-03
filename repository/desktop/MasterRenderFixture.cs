// Synthetic rendering and telemetry-contract checks only. Never calls Worker.Run,
// USB acquisition, live capture, ReadSensors, or sensor-permission methods.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Reflection;
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
            Check(Worker.SubagentText(Subagents(3,"partial"))=="3","Partial subagent counts use the requested numeric label");
            Check(Worker.SubagentText(Subagents(0,"partial"))=="0","Partial zero uses the requested numeric label");
            var partialSummary=Subagents(2,"partial");Worker.SubagentText(partialSummary);Check(Values.Text(partialSummary,"status")=="partial","Numeric count presentation preserves partial-coverage metadata");
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
        private static Dictionary<string,object> AiState()
        {
            var state=State(Project("ED-IT Buddy","unknown"),Project("MW5 MOD","working"),Project("INTERLINKED","working"),Project("The Stats","unknown"));
            var worker=Obj("host","local","modelId","gemma4:12b-it-q4_K_M","digest","worker-digest");
            var reviewer=Obj("host","msi","modelId","gemma4:e2b-it-q4_K_M","digest","reviewer-digest");
            var flow=Obj("id","violet_bridge:fixture","origin","violet_bridge","workerId","task-fixture","projectId","INTERLINKED","executionHost","msi","worker",worker,"reviewer",reviewer,"phase","reviewing","phaseAt",When(),"resultState","pending");
            state["ai"]=Obj("available",true,"observedAt",When(),"bootId","fixture","sequence",3,"partial",false,"selectedFlow",flow,"flows",new[]{flow},"otherActiveCount",1,
                "activeCalls",new[]{Obj("id","call-1","flowId","violet_bridge:fixture","origin","violet_bridge","role","reviewer","host","msi","modelId","gemma4:e2b-it-q4_K_M","digest","reviewer-digest","phase","request_pending","phaseAt",When())},
                "hosts",new[]{Obj("id","local","available",true,"observedAt",When(),"stale",false,"loadedModels",new[]{Obj("id","gemma4:12b-it-q4_K_M","digest","worker-digest")},"queuedCount",1,"runningCount",0),Obj("id","msi","available",true,"observedAt",When(),"stale",false,"loadedModels",new[]{Obj("id","gemma4:e2b-it-q4_K_M","digest","reviewer-digest")},"queuedCount",0,"runningCount",1)},
                "pulses",new[]{Obj("sequence",3,"flowId","violet_bridge:fixture","kind","request_sent","origin","violet_bridge","host","msi","role","reviewer","modelId","gemma4:e2b-it-q4_K_M","digest","reviewer-digest","occurredAt",When(-1))});
            state["lifecycleGraph"]=Obj("available",true,"observedAt",When(),"partial",true,"nodes",new[]{
                Obj("nodeId","main","kind","codex_main","projectId","INTERLINKED","status","working","observedAt",When(),"confidence","observed"),
                Obj("nodeId","child","kind","codex_child","parentNodeId","main","projectId","INTERLINKED","status","working","observedAt",When(),"confidence","observed"),
                Obj("nodeId","orphan","kind","codex_child","projectId","MW5 MOD","status","waiting","observedAt",When(),"confidence","partial")});
            return state;
        }
        private static void AiRenderChecks(string directory,string atlas)
        {
            var state=AiState();var config=Obj("layout","violet_web","diagnostic",false,"focusProject","INTERLINKED");if(atlas!=null)config["violetAtlasPath"]=atlas;
            Check(Worker.AiFresh(state),"Current bounded AI summary is fresh");
            var scene=Worker.BuildWebScene(state,config,1);Check(scene.PageCount==1,"Web activity stays on one overview without pages");Check(scene.Groups.Count==2,"Only active project groups enter the overview");
            int nodeCount=scene.TotalNodes;bool orphan=false;var allIds=new HashSet<string>();bool parentEdge=false;
            for(int page=0;page<scene.PageCount;page++){
                var current=Worker.BuildWebScene(state,config,page*8+1);Check(current.PageCount==1,"Web never rotates active work into another page");
                foreach(var node in current.Nodes){allIds.Add(node.Id);if(node.Id=="codex:orphan")orphan=node.Detail=="PARENT ?";}
                foreach(var edge in current.Edges){Check(!(edge.From=="violet"&&edge.To.StartsWith("codex:",StringComparison.Ordinal)),"No fabricated Violet to Codex control edge: "+page);Check(!(edge.Kind=="inference"&&(edge.From.StartsWith("codex:",StringComparison.Ordinal)||edge.To.StartsWith("codex:",StringComparison.Ordinal))),"Codex does not inherit a local model: "+page);if(edge.Kind=="parent")parentEdge=true;}
            }
            Check(allIds.Count==nodeCount,"Overview retains every bounded observed relationship");Check(orphan,"Missing confirmed parent remains visibly unknown");
            var minimal=State(Project("INTERLINKED","working"));minimal["lifecycleGraph"]=state["lifecycleGraph"];var parentScene=Worker.BuildWebScene(minimal,config,1);foreach(var edge in parentScene.Edges)if(edge.Kind=="parent")parentEdge=true;
            Check(parentEdge,"Confirmed parent and child identity produces a parent edge");
            int models=0;foreach(var id in allIds)if(id.StartsWith("model:",StringComparison.Ordinal))models++;Check(models==2,"Loaded inventory and worker records deduplicate exact host and digest");
            var assistant=State(Project("INTERLINKED","unknown"));assistant["ai"]=Obj("available",true,"observedAt",When(),"hosts",new object[0],"activeCalls",new[]{Obj("id","assistant","flowId","assistant:fixture","origin","assistant","role","analysis","host","local","modelId","LongInstalledModelNameThatMustBeClippedInsideTheNode:12b-q4","digest","assistant-digest","phase","request_pending","phaseAt",When(),"observedAt",When())});
            var assistantScene=Worker.BuildWebScene(assistant,config,1);Check(!assistantScene.Edges.Exists(delegate(Worker.WebEdge edge){return edge.From=="violet"||edge.To=="violet";}),"Assistant inference never claims a Violet route");
            var mutable=AiState();Values.Obj(mutable["ai"])["hosts"]=new[]{Obj("id","local","available",true,"observedAt",When(),"stale",false,"loadedModels",new[]{Obj("id","gemma4:12b-it-q4_K_M","digest","new-worker-digest")})};var mutableIds=new HashSet<string>();var mutableFirst=Worker.BuildWebScene(mutable,config,1);foreach(var node in mutableFirst.Nodes)if(node.Kind=="model")mutableIds.Add(node.Id);Check(mutableIds.Contains("model:local:worker-digest")&&!mutableIds.Contains("model:local:new-worker-digest"),"Loaded-only mutable tags stay off the web and cannot replace an observed digest");
            var response=State(Project("INTERLINKED","unknown"));response["ai"]=Obj("available",true,"observedAt",When(),"hosts",new object[0],"pulses",new[]{Obj("sequence",5,"flowId","violet_bridge:analysis","origin","violet_bridge","kind","response_received","host","local","role","analysis","modelId","gemma4:12b-it-q4_K_M","digest","response-digest","occurredAt",When(-1))});
            var responseScene=Worker.BuildWebScene(response,config,1);Check(responseScene.Nodes.Exists(delegate(Worker.WebNode node){return node.Kind=="model";}),"Validated response pulse keeps its exact model visible after active call ends");Check(responseScene.Edges.Exists(delegate(Worker.WebEdge edge){return edge.Kind=="inference"&&edge.From=="violet";}),"Observed bridge response retains its return route");
            var resultRead=State(Project("INTERLINKED","unknown"));var readFlow=Obj("id","violet_bridge:old-analysis","origin","violet_bridge","phase","result_ready","resultState","ready","role","analysis","model",Obj("host","local","modelId","gemma4:12b-it-q4_K_M","digest","retained-digest"));resultRead["ai"]=Obj("available",true,"observedAt",When(),"selectedFlow",readFlow,"flows",new[]{readFlow},"hosts",new object[0],"activeCalls",new object[0],"pulses",new[]{Obj("sequence",6,"flowId","violet_bridge:old-analysis","origin","violet_bridge","kind","result_read","occurredAt",When(-1))});var readScene=Worker.BuildWebScene(resultRead,config,1);Check(readScene.Nodes.Exists(delegate(Worker.WebNode node){return node.Id=="model:local:retained-digest";}),"Later result retrieval retains actual analysis model identity");Check(readScene.Edges.Exists(delegate(Worker.WebEdge edge){return edge.From=="violet"&&edge.To=="model:local:retained-digest"&&edge.Role=="analysis";}),"Later result retrieval uses its evidenced bridge route");Check(Worker.AiFlowCaption(Values.Obj(resultRead["ai"]))=="RESULT READ","Result read has its own caption");var readyAi=new Dictionary<string,object>(Values.Obj(resultRead["ai"]));readyAi["pulses"]=new object[0];Check(Worker.AiFlowCaption(readyAi)=="RESULT READY","Durable ready result does not claim retrieval");readFlow["worker"]=Obj("host","local","modelId","gemma4","digest","retained-digest");Check(Worker.AiFlowCaption(readyAi)=="REVIEW REQUIRED","Ready worker output remains a review request, not project success");readFlow.Remove("worker");
            using(var worker=new Worker()){
                using(var image=worker.DrawFrame(state,config,1.1))image.Save(Path.Combine(directory,"ai-violet-web.png"),ImageFormat.Png);
                using(var image=worker.DrawFrame(state,config,9.1))image.Save(Path.Combine(directory,"ai-violet-web-page2.png"),ImageFormat.Png);
                using(var image=worker.DrawFrame(assistant,config,1.1))image.Save(Path.Combine(directory,"ai-web-assistant-long-label.png"),ImageFormat.Png);
                using(var image=worker.DrawFrame(response,config,1.1))image.Save(Path.Combine(directory,"ai-web-response.png"),ImageFormat.Png);
                using(var image=worker.DrawFrame(resultRead,config,1.1))image.Save(Path.Combine(directory,"ai-web-result-read.png"),ImageFormat.Png);
                var deck=new Dictionary<string,object>(config);deck["layout"]="command_deck";using(var image=worker.DrawFrame(state,deck,1.1))image.Save(Path.Combine(directory,"ai-command-deck.png"),ImageFormat.Png);
                var legacy=new Dictionary<string,object>(deck);legacy.Remove("layout");var noPulses=new Dictionary<string,object>(state);var stableAi=new Dictionary<string,object>(Values.Obj(state["ai"]));stableAi["pulses"]=new object[0];noPulses["ai"]=stableAi;using(var a=worker.DrawFrame(noPulses,legacy,1.1))using(var b=worker.DrawFrame(noPulses,deck,1.1))Check(SamePixels(a,b,new Rectangle(0,0,480,480)),"Missing layout defaults to command deck");
                foreach(string layout in new[]{"command_deck","violet_web"}){var mini=new Dictionary<string,object>(config);mini["layout"]=layout;mini["mode"]="live_mini";mini["captureEnabled"]=false;using(var image=worker.DrawFrame(state,mini,1.1)){Check(image.Width==480&&image.Height==480,"Disabled mini safely renders unavailable in "+layout);image.Save(Path.Combine(directory,"ai-"+layout+"-mini-unavailable.png"),ImageFormat.Png);}}
                state["utc"]=When(-30);Check(!Worker.AiFresh(state),"Stale whole snapshot suppresses fresh AI summary");var stale=Worker.BuildWebScene(state,config,1);Check(!stale.Nodes.Exists(delegate(Worker.WebNode node){return node.Fresh;}),"Every stale web node is neutralized");
                using(var a=worker.DrawFrame(state,config,.25))using(var b=worker.DrawFrame(state,config,1.4)){Check(SamePixels(a,b,new Rectangle(18,38,120,280)),"Stale model nodes and links stop motion");Check(SamePixels(a,b,new Rectangle(342,38,120,280)),"Stale project nodes and links stop motion");a.Save(Path.Combine(directory,"ai-web-stale.png"),ImageFormat.Png);}
            }
        }
        private static Dictionary<string,object> PolishState(int count)
        {
            string[] names={"INTERLINKED","ED-IT Buddy","MW5 MOD","The Stats","Violet Worker Test","Project 06","Project 07","Project 08","Project 09","Project 10","Project 11","Project 12"};
            var projects=new List<Dictionary<string,object>>();var graph=new List<Dictionary<string,object>>();var flows=new List<Dictionary<string,object>>();var titles=Obj();
            for(int i=0;i<count;i++){
                var project=Project(names[i],i==3?"waiting":i==4?"unknown":"working");projects.Add(project);
                if(i==1||i==4){var flow=Obj("id","flow-"+i,"origin","violet_bridge","workerId","worker-"+i,"projectId",names[i],"executionHost","local","worker",Obj("host","local","modelId","gemma4:12b-it-q4_K_M","digest","worker-"+i),"phase",i==4?"queued":"reviewing","phaseAt",When());if(i==1)flow["reviewer"]=Obj("host","msi","modelId","gemma4:e2b-it-q4_K_M","digest","reviewer");flows.Add(flow);}
                else {string id="main-"+i;graph.Add(Obj("nodeId",id,"kind","codex_main","projectId",names[i],"status",i==3?"waiting":"working","observedAt",When(),"confidence","observed"));titles[id]=i==0?"Fix cooler display reconnection":i==2?"Companion update":"Active project chat "+(i+1);if(i==0||i==2)for(int n=0;n<(i==0?3:2);n++)graph.Add(Obj("nodeId","child-"+i+"-"+n,"kind","codex_child","parentNodeId",id,"projectId",names[i],"status","working","observedAt",When(),"confidence","observed"));}
            }
            var state=State(projects.ToArray());state["lifecycleGraph"]=Obj("available",true,"observedAt",When(),"partial",false,"nodes",graph.ToArray());state["ai"]=Obj("available",true,"observedAt",When(),"bootId","polish","flows",flows.ToArray(),"activeCalls",new object[0],"pulses",new object[0],"hosts",new object[0]);
            state["coolerDisplay"]=Obj("observedAt",When(),"activityAvailable",true,"activityPartial",false,"titles",titles,"resources",new[]{
                Obj("id","local","cpuTemperature",Sensor(57),"gpuTemperature",Sensor(49),"gpuLoad",Sensor(42),"vramUsed",Sensor(7.2*1073741824),"vramTotal",Sensor(16.0*1073741824),"gpuId","local-gpu"),
                Obj("id","msi","cpuTemperature",Sensor(61),"gpuTemperature",Sensor(54),"gpuLoad",Sensor(67),"vramUsed",Sensor(4.8*1073741824),"vramTotal",Sensor(8.0*1073741824),"gpuId","msi-gpu")});return state;
        }
        private static void AvatarAnchorChecks(string atlas){
            if(atlas==null)return;
            using(var image=new Bitmap(atlas))foreach(int row in new[]{0,5,6,7})for(int column=0;column<(row==5?8:6);column++){
                bool opaque=true;for(int y=-6;y<=6;y++)for(int x=-6;x<=6;x++){
                    int sx=(int)Math.Floor((240+x-166)*192.0/148),sy=(int)Math.Floor((288+y-187)*208.0/160);
                    if(image.GetPixel(column*192+sx,row*208+sy).A!=255)opaque=false;
                }
                Check(opaque,"Original Violet pose fully hides the attachment/particle endpoint: row "+row+" frame "+column);
            }
        }
        private static void ProjectPortChecks()
        {
            var route=typeof(Worker).GetMethod("ProjectHubRouteAvoiding",BindingFlags.NonPublic|BindingFlags.Static);
            var anchor=new PointF(240,288);
            foreach(int count in new[]{1,2,6,12})using(var worker=new Worker()){
                var state=PolishState(count);var scene=Worker.BuildWebScene(state,Obj("layout","violet_web"),1);var cards=worker.LayoutWeb(scene,1);var ports=new List<PointF>();int projects=0;
                foreach(var card in cards){if(card.Role!="PROJECT"||card.Badge)continue;projects++;
                    var path=(PointF[])route.Invoke(null,new object[]{card,cards});Check(path.Length==2,"Each project has one direct visible hub spoke at density "+count);
                    var start=path[0];Check(Math.Abs(start.X-card.Bounds.Left)<.01||Math.Abs(start.X-card.Bounds.Right)<.01||Math.Abs(start.Y-card.Bounds.Bottom)<.01,"Spoke starts on its actual card border at density "+count);
                    Check(path[path.Length-1]==anchor,"Spoke continues behind opaque Violet at density "+count);
                    bool distinct=true;foreach(var prior in ports)if(Math.Abs(prior.X-start.X)<4&&Math.Abs(prior.Y-start.Y)<4)distinct=false;ports.Add(start);Check(distinct,"Project spokes retain distinct card-border attachments at density "+count);
                    bool outside=true;for(int n=1;n<100;n++){float t=n/100f;var point=new PointF(start.X+(path[1].X-start.X)*t,start.Y+(path[1].Y-start.Y)*t);foreach(var other in cards)if(point.X>other.Bounds.Left+.01&&point.X<other.Bounds.Right-.01&&point.Y>other.Bounds.Top+.01&&point.Y<other.Bounds.Bottom-.01)outside=false;}
                    Check(outside,"Project spoke never crosses another card interior at density "+count);
                }
                Check(projects==count,"Every project keeps exactly one visible attachment at density "+count);
            }
            foreach(int count in new[]{1,6,12})using(var worker=new Worker()){
                var state=PolishState(count);var ai=Values.Obj(state["ai"]);var flows=Values.Rows(ai["flows"]);
                foreach(string origin in new[]{"violet_bridge","assistant","buddy","calibration","local_ai"})flows.Add(Obj("id","source-"+origin,"origin",origin,"phase","working","observedAt",When(),"phaseAt",When(),"model",Obj("host","local","modelId","source-model-"+origin,"digest","source-"+origin)));
                ai["flows"]=flows.ToArray();var graph=Values.Obj(state["lifecycleGraph"]);var nodes=Values.Rows(graph["nodes"]);nodes.Add(Obj("nodeId","source-parent","kind","codex_main","status","working","observedAt",When(),"confidence","observed"));graph["nodes"]=nodes.ToArray();
                var scene=Worker.BuildWebScene(state,Obj("layout","violet_web"),1);var cards=worker.LayoutWeb(scene,1);Check(cards.FindAll(delegate(Worker.WebCard card){return card.Badge;}).Count==6,"All six central source summaries coexist at project count "+count);
                foreach(var card in cards){if(card.Role!="PROJECT"||card.Badge)continue;var path=(PointF[])route.Invoke(null,new object[]{card,cards});bool outside=true;
                    for(int segment=1;segment<path.Length;segment++)for(int n=1;n<100;n++){float t=n/100f;var point=new PointF(path[segment-1].X+(path[segment].X-path[segment-1].X)*t,path[segment-1].Y+(path[segment].Y-path[segment-1].Y)*t);foreach(var other in cards)if(point.X>other.Bounds.Left+.01&&point.X<other.Bounds.Right-.01&&point.Y>other.Bounds.Top+.01&&point.Y<other.Bounds.Bottom-.01)outside=false;}
                    Check(outside,"Project attachment avoids every central source summary and project card with "+count+" projects");
                    Check(path[path.Length-1]==anchor,"Obstacle detour retains its endpoint behind Violet with "+count+" projects");
                }
                foreach(var badge in cards){if(!badge.Badge)continue;var target=cards.Find(delegate(Worker.WebCard card){return !card.Badge&&card.Role=="PROJECT";});var path=Worker.WebRoute(badge,target);bool outside=true;
                    for(int segment=1;segment<path.Length;segment++)for(int n=1;n<100;n++){float t=n/100f;var point=new PointF(path[segment-1].X+(path[segment].X-path[segment-1].X)*t,path[segment-1].Y+(path[segment].Y-path[segment-1].Y)*t);foreach(var other in cards)if(point.X>other.Bounds.Left+.01&&point.X<other.Bounds.Right-.01&&point.Y>other.Bounds.Top+.01&&point.Y<other.Bounds.Bottom-.01)outside=false;}
                    Check(outside,"Source-to-project route uses the exterior gutter without crossing other cards at project count "+count);
                }
            }
        }
        private static void PolishChecks(string directory,string atlas)
        {
            var config=Obj("layout","violet_web","diagnostic",false);if(atlas!=null)config["violetAtlasPath"]=atlas;
            foreach(int count in new[]{0,1,2,3,6,7,12})using(var worker=new Worker()){
                var state=PolishState(count);var scene=Worker.BuildWebScene(state,config,1);Check(scene.Groups.Count==count,"Every active project remains named at count "+count);Check(scene.PageCount==1,"No pages at count "+count);var cards=worker.LayoutWeb(scene,1);int expected=count<=2?count:count;var ids=new HashSet<string>();foreach(var card in cards){ids.Add(card.GroupId);Check(card.Bounds.Top>=44&&card.Bounds.Bottom<=344,"Web card remains above fixed resource footer "+count);Check(card.Bounds.Left>=18&&card.Bounds.Right<=462,"Web card remains inside native margins "+count);}Check(ids.Count==expected,"Native layout includes every project at count "+count);
                File.WriteAllText(Path.Combine(directory,"violet-web-"+count+"-state.json"),new JavaScriptSerializer().Serialize(Obj("format","interlinked-violet-web-synthetic-v1","synthetic",true,"snapshot",state,"settings",config)),new UTF8Encoding(false));
                using(var first=worker.DrawFrame(state,config,.2))using(var second=worker.DrawFrame(state,config,.7)){first.Save(Path.Combine(directory,"violet-web-"+count+".png"),ImageFormat.Png);if(count>0){second.Save(Path.Combine(directory,"violet-web-"+count+"-later.png"),ImageFormat.Png);Check(!SamePixels(first,second,new Rectangle(13,40,454,305)),"Observed work animates web at count "+count);}Check(SamePixels(first,second,new Rectangle(18,350,444,114)),"Machine cards never acquire particle or flash animation "+count);Check(SamePixels(first,second,new Rectangle(0,0,480,10)),"Outer frame does not acquire particle animation "+count);}
            }
            foreach(int count in new[]{1,3,7})using(var worker=new Worker()){
                var partialState=PolishState(count);Values.Obj(partialState["coolerDisplay"])["activityPartial"]=true;
                var partialScene=Worker.BuildWebScene(partialState,config,1);var partialCards=worker.LayoutWeb(partialScene,1);
                Check(partialScene.Partial&&partialScene.Groups.TrueForAll(delegate(Worker.WebGroup group){return group.Partial;}),"Numeric web labels preserve incomplete coverage at project count "+count);
                Check(partialCards.TrueForAll(delegate(Worker.WebCard card){return !(card.Label+card.Body+card.Summary).Contains("?");}),"Expanded/compact/dense subagent counts omit the uncertainty suffix at project count "+count);
                Check(partialCards.Exists(delegate(Worker.WebCard card){return card.Label=="3 active"||(card.Body??"").Contains("3 subs")||(card.Summary??"").Contains("3 subs");}),"The confirmed three-subagent count stays visible at project count "+count);
            }
            using(var worker=new Worker()){
                var reviewState=PolishState(2);var reviewScene=Worker.BuildWebScene(reviewState,config,1);var roles=worker.LayoutWeb(reviewScene,1);var localModel=roles.Find(delegate(Worker.WebCard card){return card.Role=="LOCAL MODEL";});var reviewingModel=roles.Find(delegate(Worker.WebCard card){return card.Role=="REVIEW MODEL";});
                Check(localModel!=null&&localModel.Status=="idle"&&localModel.Summary=="WORK COMPLETE","Review-only work leaves the worker model stationary and truthfully complete");Check(reviewingModel!=null&&reviewingModel.Status=="reviewing","Only the evidenced reviewer receives the reviewing model state");
                using(var a=worker.DrawFrame(reviewState,config,.2))using(var b=worker.DrawFrame(reviewState,config,.7))Check(SamePixels(a,b,localModel.Bounds),"Review-only telemetry does not animate the local model card");
            }
            using(var worker=new Worker()){
                var state=PolishState(6);var first=worker.LayoutWeb(Worker.BuildWebScene(state,config,1),1);var smaller=PolishState(3);var next=worker.LayoutWeb(Worker.BuildWebScene(smaller,config,2),2);foreach(var card in next){var previous=first.Find(delegate(Worker.WebCard row){return row.GroupId==card.GroupId;});Check(previous!=null&&previous.Bounds==card.Bounds,"Surviving projects retain slots when peers disappear");}
                var dense=worker.LayoutWeb(Worker.BuildWebScene(PolishState(7),config,3),3);Check(dense.TrueForAll(delegate(Worker.WebCard card){return card.Dense;}),"Density expands immediately when needed");var hold=worker.LayoutWeb(Worker.BuildWebScene(smaller,config,4),4);Check(hold.TrueForAll(delegate(Worker.WebCard card){return card.Dense;}),"Downshift waits to avoid flapping");var down=worker.LayoutWeb(Worker.BuildWebScene(smaller,config,12.1),12.1);Check(down.TrueForAll(delegate(Worker.WebCard card){return !card.Dense;}),"Density downshifts after eight stable seconds");
            }
            var completed=PolishState(1);var done=Values.Rows(Values.Get(Values.Obj(completed["lifecycleGraph"]),"nodes"));foreach(var row in done){row["status"]="turn_finished";row["observedAt"]=When(-1);}Values.Obj(Values.Rows(completed["projects"])[0]["activity"])["status"]="turn_finished";Values.Obj(Values.Rows(completed["projects"])[0]["activity"])["observedAt"]=When(-1);
            Check(Worker.BuildWebScene(completed,config,1).Groups[0].Status=="finished","Fresh terminal transition produces green finish cue");foreach(var row in done)row["observedAt"]=When(-4);Values.Obj(Values.Rows(completed["projects"])[0]["activity"])["observedAt"]=When(-4);Check(Worker.BuildWebScene(completed,config,2).Groups.Count==0,"Completion expires from source timestamp after three seconds");
            completed["utc"]=When();Check(Worker.BuildWebScene(completed,config,3).Groups.Count==0,"Repeated fresh snapshots do not replay completed work");
            var idle=PolishState(0);Check(Worker.BuildWebScene(idle,config,1).ActivityAvailable,"Explicit complete coverage establishes known idle");Values.Obj(idle["coolerDisplay"])["activityPartial"]=true;Check(Worker.BuildWebScene(idle,config,1).Partial,"Partial activity remains distinguishable from idle");
            var stale=PolishState(3);stale["utc"]=When(-16);Check(Worker.BuildWebScene(stale,config,1).Groups.Count==0,"Stale observations disappear rather than paging");
            var oldProject=State(Project("Old source","working"));Values.Obj(Values.Rows(oldProject["projects"])[0]["activity"])["observedAt"]=When(-181);Check(Worker.BuildWebScene(oldProject,config,1).Groups.Count==0,"Fresh snapshot cannot renew an expired project activity source");
            var queued=PolishState(0);var queueFlow=Obj("id","queue-only","origin","assistant","phase","queued","phaseAt",When(),"observedAt",When(),"model",Obj("host","local","modelId","gemma4:12b","digest","queued"));Values.Obj(queued["ai"])["flows"]=new[]{queueFlow};using(var worker=new Worker()){var queueScene=Worker.BuildWebScene(queued,config,1);Check(queueScene.Groups[0].Status=="queued","Queue waiting is distinct from needs input and work");var queueCards=worker.LayoutWeb(queueScene,1);using(var a=worker.DrawFrame(queued,config,.2))using(var b=worker.DrawFrame(queued,config,.7))foreach(var card in queueCards)Check(SamePixels(a,b,card.Bounds),"Queued card remains stationary without running particles");}
            var metric=Sensor(73);Check(Worker.WebMetric(metric,100)==73,"Fresh load metric is measured");metric["observedAt"]=When(-6);Check(!Worker.WebMetric(metric,100).HasValue,"Resource TTL is five seconds independently of enrichment time");
            foreach(object invalid in new object[]{true,-1,101,double.NaN,double.PositiveInfinity})Check(!Worker.WebMetric(Sensor(invalid),100).HasValue,"Invalid GPU percentage is unavailable: "+invalid);
            var staleFlow=PolishState(0);Values.Obj(staleFlow["ai"])["flows"]=new[]{Obj("id","stale-flow","origin","assistant","phase","working","phaseAt",When(),"observedAt",When(-181),"model",Obj("host","local","modelId","gemma4:12b","digest","stale"))};Check(Worker.BuildWebScene(staleFlow,config,1).Groups.Count==0,"Fresh summary cannot renew an expired source flow");var retainedFlow=Values.Rows(Values.Obj(staleFlow["ai"])["flows"])[0];retainedFlow["phase"]="failed";retainedFlow["phaseAt"]=When(-900);retainedFlow["observedAt"]=When();Check(Worker.BuildWebScene(staleFlow,config,1).Groups[0].Status=="test_failed","A freshly observed unresolved failure survives an old phase timestamp");
            foreach(int projectCount in new[]{0,1,12})using(var worker=new Worker()){
                var sourceState=PolishState(projectCount);var ai=Values.Obj(sourceState["ai"]);var sourceFlows=Values.Rows(ai["flows"]);foreach(string id in new[]{"assistant-a","assistant-b","calibration-a"})sourceFlows.Add(Obj("id",id,"origin",id.StartsWith("assistant",StringComparison.Ordinal)?"assistant":"calibration","phase","working","phaseAt",When(),"observedAt",When(),"model",Obj("host","local","modelId","gemma4:12b","digest","shared-model")));ai["flows"]=sourceFlows.ToArray();ai["activeCalls"]=new[]{Obj("id","duplicate-call","flowId","assistant-a","origin","assistant","role","analysis","host","local","modelId","gemma4:12b","digest","shared-model","phase","request_pending","phaseAt",When(),"observedAt",When())};
                var sourceScene=Worker.BuildWebScene(sourceState,config,1);var sourceCards=worker.LayoutWeb(sourceScene,1);Check(sourceCards.FindAll(delegate(Worker.WebCard card){return card.Badge;}).Count==2,"Source groups always use separate badges with "+projectCount+" projects");var badge=sourceCards.Find(delegate(Worker.WebCard card){return card.GroupId=="source:assistant";});Check(badge!=null&&badge.Label.IndexOf("2 tasks",StringComparison.Ordinal)>=0,"Source badge deduplicates underlying models and calls into two logical tasks");if(projectCount==1)Check(sourceCards.Exists(delegate(Worker.WebCard card){return card.Role=="PROJECT"&&card.Bounds.Height==66;}),"Source badges cannot push one project into compact mode");if(projectCount==12)Check(sourceCards.FindAll(delegate(Worker.WebCard card){return !card.Badge;}).Count==12,"All twelve project cards remain visible beside source badges");Check(!sourceScene.Edges.Exists(delegate(Worker.WebEdge edge){return edge.From=="violet"&&(edge.FlowId=="assistant-a"||edge.FlowId=="assistant-b"||edge.FlowId=="calibration-a");}),"Assistant and calibration badges never acquire a Violet route");using(var image=worker.DrawFrame(sourceState,config,1))image.Save(Path.Combine(directory,"violet-web-"+projectCount+"-with-sources.png"),ImageFormat.Png);
            }
            foreach(string ownPhase in new[]{"queued","failed"})using(var worker=new Worker()){
                var shared=State(Project("A","unknown"),Project("B","unknown"));var model=Obj("host","local","modelId","gemma4:12b","digest","same-physical-model");shared["ai"]=Obj("available",true,"observedAt",When(),"flows",new[]{Obj("id","own-A","origin","assistant","projectId","A","phase",ownPhase,"phaseAt",When(),"observedAt",When(),"model",model),Obj("id","other-B","origin","assistant","projectId","B","phase","working","phaseAt",When(),"observedAt",When(),"model",model)},"activeCalls",new object[0],"pulses",new object[0]);var ownScene=Worker.BuildWebScene(shared,config,1);Check(ownScene.Nodes.FindAll(delegate(Worker.WebNode node){return node.Kind=="model";}).Count==1,"Shared physical model identity remains deduplicated");var ownGroup=ownScene.Groups.Find(delegate(Worker.WebGroup group){return group.Id=="project:A";});Check(ownGroup.Status==(ownPhase=="failed"?"test_failed":"queued")&&ownGroup.Activities.Count==1,"Project A cannot inherit B activity through a shared model: "+ownPhase);var ownCards=worker.LayoutWeb(ownScene,1);var ownCard=ownCards.Find(delegate(Worker.WebCard card){return card.GroupId=="project:A"&&card.Role=="LOCAL MODEL";});Check(ownCard!=null&&ownCard.Status==ownGroup.Status,"Per-flow model role stays scoped to its own project: "+ownPhase);Check(ownCards.Find(delegate(Worker.WebCard card){return card.GroupId=="project:A"&&card.Role=="PROJECT";}).Summary.IndexOf("working",StringComparison.OrdinalIgnoreCase)<0,"Failure/queue summary cannot invent working counts from B");using(var image=worker.DrawFrame(shared,config,1))image.Save(Path.Combine(directory,"violet-web-shared-model-"+ownPhase+".png"),ImageFormat.Png);
            }
            using(var worker=new Worker()){var parentState=PolishState(3);var graph=Values.Obj(parentState["lifecycleGraph"]);var parentNodes=Values.Rows(graph["nodes"]);parentNodes.Add(Obj("nodeId","unassigned-parent","kind","codex_main","status","working","observedAt",When(),"confidence","observed"));parentNodes.Add(Obj("nodeId","known-child-in-project","kind","codex_child","parentNodeId","unassigned-parent","projectId","INTERLINKED","status","working","observedAt",When(),"confidence","observed"));graph["nodes"]=parentNodes.ToArray();var parentScene=Worker.BuildWebScene(parentState,config,1);var parentCards=worker.LayoutWeb(parentScene,1);var sourceCard=parentCards.Find(delegate(Worker.WebCard card){return card.Nodes.Contains("codex:unassigned-parent");});var childCard=parentCards.Find(delegate(Worker.WebCard card){return card.Nodes.Contains("codex:known-child-in-project");});Check(sourceCard!=null&&sourceCard.Badge&&childCard!=null,"Confirmed unassigned parent remains in its own source badge");var path=Worker.WebRoute(sourceCard,childCard);Check(path[0].Y==sourceCard.Bounds.Top+sourceCard.Bounds.Height/2&&path[path.Length-1].Y==childCard.Bounds.Top+childCard.Bounds.Height/2,"Source-to-project parent route joins its actual two cards");Check(!Array.Exists(path,delegate(PointF point){return point==new PointF(174,216)||point==new PointF(306,216);}),"Confirmed source parent route cannot acquire an inferred Violet anchor");}
            var attention=PolishState(5);var nodes=Values.Rows(Values.Obj(attention["lifecycleGraph"])["nodes"]);var failure=nodes.Find(delegate(Dictionary<string,object> row){return Values.Text(row,"nodeId")=="main-2";});failure["status"]="test_failed";nodes.Find(delegate(Dictionary<string,object> row){return Values.Text(row,"nodeId")=="main-0";})["status"]="waiting";nodes.Find(delegate(Dictionary<string,object> row){return Values.Text(row,"nodeId")=="main-3";})["status"]="turn_finished";var attentionProjects=Values.Rows(attention["projects"]);Values.Obj(attentionProjects[0]["activity"])["status"]="waiting";Values.Obj(attentionProjects[3]["activity"])["status"]="turn_finished";
            using(var worker=new Worker())using(var image=worker.DrawFrame(attention,config,.25))image.Save(Path.Combine(directory,"violet-web-attention.png"),ImageFormat.Png);
            string animation=Path.Combine(directory,"animation");Directory.CreateDirectory(animation);using(var worker=new Worker())for(int frame=0;frame<48;frame++)using(var image=worker.DrawFrame(attention,config,.2+frame/24.0))image.Save(Path.Combine(animation,"frame-"+frame.ToString("000",CultureInfo.InvariantCulture)+".png"),ImageFormat.Png);Check(Directory.GetFiles(animation,"frame-*.png").Length==48,"Native two-second animation preview contains48 actual renderer frames");
            var missing=PolishState(1);var resources=Values.Rows(Values.Obj(missing["coolerDisplay"])["resources"]);resources[1].Clear();resources[1]["id"]="msi";Values.Obj(Values.Obj(missing["coolerDisplay"])["titles"])["main-0"]="Refine cooler display and AI activity across every active project and machine";using(var worker=new Worker())using(var image=worker.DrawFrame(missing,config,1))image.Save(Path.Combine(directory,"violet-web-long-title-missing-host.png"),ImageFormat.Png);
            var unavailable=PolishState(0);Values.Obj(unavailable["coolerDisplay"])["activityAvailable"]=false;Values.Obj(unavailable["coolerDisplay"])["activityPartial"]=true;using(var worker=new Worker())using(var image=worker.DrawFrame(unavailable,config,1))image.Save(Path.Combine(directory,"violet-web-activity-unavailable.png"),ImageFormat.Png);
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
                    Check(g.MeasureString("SUBAGENTS 256",font).Width<=158,"Maximum numeric count fits beside Violet without truncation");
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
                ContractChecks();RenderChecks(directory,atlas);AiRenderChecks(directory,atlas);PolishChecks(directory,atlas);ProjectPortChecks();AvatarAnchorChecks(atlas);
                report["ok"]=true;report["checks"]=Checks;report["hardwareTested"]=false;report["sensorPublisherStarted"]=false;report["usbRequested"]=false;
                string json=new JavaScriptSerializer().Serialize(report);
                File.WriteAllText(Path.Combine(directory,"render-report.json"),json,new UTF8Encoding(false));Console.WriteLine(json);return 0;
            }
            catch(Exception ex){Console.Error.WriteLine(ex.ToString());return 1;}
        }
    }
}
