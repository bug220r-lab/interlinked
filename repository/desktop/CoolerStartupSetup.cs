// One-time administrative setup. Payload and hashes are embedded at build time.
using System;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Windows.Forms;
using System.Xml;
using System.Runtime.InteropServices;

internal static partial class CoolerStartupSetup
{
    private const string Folder=@"C:\Program Files\INTERLINKED Cooler Startup";
    private const string TaskName="INTERLINKED Cooler Temperatures";
    private const string TaskSddl="O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)";
    internal static bool TaskMissing(Exception ex){return ex.HResult==unchecked((int)0x80070002);}
    internal static bool PrincipalMatches(string name){try{return AccountSid(name)==UserSid;}catch{return false;}}
    internal sealed class RepairState {
        internal bool TaskExists,FolderExists,LiveOptInExists,FolderProtected,SensorProtected,SetupProtected;
        internal string SensorDigest,SetupDigest,ExpectedSensorDigest,ExpectedSetupDigest,BeforeTrccDigest,ReviewedTrccDigest;
    }
    internal static void ValidateRepairState(RepairState state) {
        if(!state.TaskExists || !state.FolderExists || state.LiveOptInExists || !state.FolderProtected || !state.SensorProtected || !state.SetupProtected ||
            String.IsNullOrEmpty(state.ExpectedSetupDigest) || state.SensorDigest!=state.ExpectedSensorDigest || state.SetupDigest!=state.ExpectedSetupDigest ||
            (state.BeforeTrccDigest!=null && state.BeforeTrccDigest!=state.ReviewedTrccDigest))
            throw new Exception("The reviewed incomplete setup or its TRCC phase differs; existing records were preserved.");
    }
    internal static bool ExistingCopyMatches(bool protectedOrdinary,string observed,string expected){return protectedOrdinary && observed==expected;}
    [StructLayout(LayoutKind.Sequential)] private struct FileIdentity {internal uint Attributes,CreationLow,CreationHigh,AccessLow,AccessHigh,WriteLow,WriteHigh,Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;}
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetFileInformationByHandle(Microsoft.Win32.SafeHandles.SafeFileHandle handle,out FileIdentity identity);
    private static void Ordinary(string path,bool directory) {
        string walk=Path.GetFullPath(path);
        while(walk!=null){if(((int)File.GetAttributes(walk)&0x441400)!=0)throw new Exception("Linked, offline or cloud startup paths are not supported.");walk=Path.GetDirectoryName(walk);}
        if(!directory)using(var stream=File.Open(path,FileMode.Open,FileAccess.Read,FileShare.Read)){
            FileIdentity identity;if(!GetFileInformationByHandle(stream.SafeFileHandle,out identity) || identity.Links!=1)throw new Exception("Startup files must have one ordinary local link.");
        }
    }
    private static bool ProtectedOwned(string path,bool directory) {
        try{
            Ordinary(path,directory);
            FileSystemSecurity security=directory?(FileSystemSecurity)Directory.GetAccessControl(path):File.GetAccessControl(path);
            string owner=security.GetOwner(typeof(SecurityIdentifier)).Value;
            if(!security.AreAccessRulesProtected || (owner!="S-1-5-18" && owner!="S-1-5-32-544"))return false;
            const FileSystemRights writes=FileSystemRights.Write|FileSystemRights.Delete|FileSystemRights.DeleteSubdirectoriesAndFiles|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership;
            foreach(FileSystemAccessRule rule in security.GetAccessRules(true,true,typeof(SecurityIdentifier))){string sid=rule.IdentityReference.Value;if(rule.AccessControlType==AccessControlType.Allow && sid!="S-1-5-18" && sid!="S-1-5-32-544" && (rule.FileSystemRights&writes)!=0)return false;}
            return true;
        }catch{return false;}
    }
    internal sealed class TaskState {
        internal int ActionCount,ActionType,TriggerCount,TriggerType,LogonType,RunLevel,MultipleInstances,RestartCount;
        internal string ActionPath,Arguments,WorkingDirectory,PrincipalSid,TriggerSid,ExecutionTimeLimit,RestartInterval;
        internal bool TriggerEnabled,DisallowBatteries,StopOnBatteries;
    }
    private static TaskState ReadTaskState(dynamic task) {
        var result=new TaskState();
        result.ActionCount=(int)task.Actions.Count;result.TriggerCount=(int)task.Triggers.Count;
        if(result.ActionCount==1){dynamic action=task.Actions.Item(1);result.ActionType=(int)action.Type;result.ActionPath=(string)action.Path;result.Arguments=(string)action.Arguments;result.WorkingDirectory=(string)action.WorkingDirectory;}
        if(result.TriggerCount==1){dynamic trigger=task.Triggers.Item(1);result.TriggerType=(int)trigger.Type;result.TriggerEnabled=(bool)trigger.Enabled;if(result.TriggerType==9)result.TriggerSid=(string)trigger.UserId;}
        result.PrincipalSid=(string)task.Principal.UserId;result.LogonType=(int)task.Principal.LogonType;result.RunLevel=(int)task.Principal.RunLevel;
        result.MultipleInstances=(int)task.Settings.MultipleInstances;result.RestartCount=(int)task.Settings.RestartCount;
        result.RestartInterval=(string)task.Settings.RestartInterval;result.ExecutionTimeLimit=(string)task.Settings.ExecutionTimeLimit;
        result.DisallowBatteries=(bool)task.Settings.DisallowStartIfOnBatteries;result.StopOnBatteries=(bool)task.Settings.StopIfGoingOnBatteries;
        return result;
    }
    internal static void ValidateTaskState(TaskState value) {
        if(value.ActionCount!=1 || value.ActionType!=0 || !String.Equals(value.ActionPath,Path.Combine(Folder,"INTERLINKED.Sensor.Startup.exe"),StringComparison.OrdinalIgnoreCase) ||
            !String.IsNullOrEmpty(value.Arguments) || !String.Equals(value.WorkingDirectory,Folder,StringComparison.OrdinalIgnoreCase) ||
            !PrincipalMatches(value.PrincipalSid) || value.LogonType!=3 || value.RunLevel!=1 || value.TriggerCount!=1 || value.TriggerType!=9 || !value.TriggerEnabled || !PrincipalMatches(value.TriggerSid) ||
            value.MultipleInstances!=2 || value.RestartCount!=3 || value.RestartInterval!="PT1M" || value.ExecutionTimeLimit!="PT0S" || value.DisallowBatteries || value.StopOnBatteries)
            throw new Exception("Startup task ownership or fixed settings differ; existing data was preserved.");
    }
    internal static void ValidateTaskSecurity(string sddl) {
        var security=new RawSecurityDescriptor(sddl);
        if(security.Owner==null || (security.Owner.Value!="S-1-5-18" && security.Owner.Value!="S-1-5-32-544") ||
            (security.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0 || security.DiscretionaryAcl==null)
            throw new Exception("Startup task ownership or protected permissions differ.");
        bool system=false,administrators=false,user=false;
        foreach(GenericAce raw in security.DiscretionaryAcl){
            var ace=raw as CommonAce;
            if(ace==null || ace.AceQualifier!=AceQualifier.AccessAllowed || ace.AceFlags!=AceFlags.None)throw new Exception("Unexpected startup task permissions.");
            string sid=ace.SecurityIdentifier.Value;
            if(sid=="S-1-5-18")system=true;
            else if(sid=="S-1-5-32-544")administrators=true;
            else if(sid==UserSid && (ace.AccessMask&~unchecked((int)0xa01200a9))==0)user=true;
            else throw new Exception("Startup task permits access outside its fixed owners and read-only user.");
        }
        if(!system || !administrators || !user)throw new Exception("Startup task permissions are incomplete.");
    }
    internal static void ValidateFolderSecurity(DirectorySecurity security) {
        string owner=security.GetOwner(typeof(SecurityIdentifier)).Value;
        if(!security.AreAccessRulesProtected || (owner!="S-1-5-18" && owner!="S-1-5-32-544"))throw new Exception("Startup folder ownership or protected permissions differ.");
        const FileSystemRights writes=FileSystemRights.Write|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership|FileSystemRights.Delete|FileSystemRights.DeleteSubdirectoriesAndFiles;
        foreach(FileSystemAccessRule rule in security.GetAccessRules(true,true,typeof(SecurityIdentifier))){
            string sid=((SecurityIdentifier)rule.IdentityReference).Value;
            if(rule.AccessControlType==AccessControlType.Allow && sid!="S-1-5-18" && sid!="S-1-5-32-544" && (rule.FileSystemRights&writes)!=0)
                throw new Exception("Startup folder grants write access outside administrators or SYSTEM.");
        }
    }
    private static void ValidateInstalledFolder() {
        var directory=new DirectoryInfo(Folder);
        if((directory.Attributes&FileAttributes.ReparsePoint)!=0)throw new Exception("Linked startup folders are not supported.");
        ValidateFolderSecurity(directory.GetAccessControl());
    }
    private static string Hash(string path){using(var sha=SHA256.Create())using(var f=File.OpenRead(path))return BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();}
    private static string TextHash(string text){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-","").ToLowerInvariant();}
    private static void ProtectedText(string name,string text){using(var output=ProtectedFile(Path.Combine(Folder,name))){byte[] bytes=new UTF8Encoding(false).GetBytes(text);output.Write(bytes,0,bytes.Length);output.Flush(true);}}
    private static void ProtectedTextOnce(string name,string text) {
        string path=Path.Combine(Folder,name);
        if(File.Exists(path)){if(!ProtectedOwned(path,false) || !ExistingCopyMatches(true,Hash(path),TextHash(text)))throw new Exception("Existing startup receipt differs; it was preserved.");return;}
        ProtectedText(name,text);
    }
    private static void CopySetupOnce(string name) {
        string source=typeof(CoolerStartupSetup).Assembly.Location,path=Path.Combine(Folder,name),expected=Hash(source);
        if(File.Exists(path)){if(!ProtectedOwned(path,false) || !ExistingCopyMatches(true,Hash(path),expected))throw new Exception("Existing startup setup copy differs; it was preserved.");return;}
        using(var input=File.OpenRead(source))using(var output=ProtectedFile(path)){input.CopyTo(output);output.Flush(true);}
        if(Hash(path)!=expected)throw new Exception("Startup setup copy readback failed.");
    }
    private static void ArchiveOptIn() {
        string record=Path.Combine(Folder,"startup.json");
        if(File.Exists(record))File.Move(record,Path.Combine(Folder,"startup.disabled-"+DateTime.UtcNow.ToString("yyyyMMddTHHmmssfffZ")+"-"+Guid.NewGuid().ToString("N")+".json"));
    }
    private static void CommitOptIn() {
        string pending="startup.pending-"+Guid.NewGuid().ToString("N")+".json";
        ProtectedText(pending,StartupJson);
        if(Hash(Path.Combine(Folder,pending))!=TextHash(StartupJson))throw new Exception("Startup opt-in readback failed.");
        File.Move(Path.Combine(Folder,pending),Path.Combine(Folder,"startup.json"));
    }
    private static string AccountSid(string value){return value.StartsWith("S-1-",StringComparison.Ordinal)?new SecurityIdentifier(value).Value:((SecurityIdentifier)new NTAccount(value).Translate(typeof(SecurityIdentifier))).Value;}
    internal static XmlDocument TrccDocument(string xml) {
        var settings=new XmlReaderSettings();settings.DtdProcessing=DtdProcessing.Prohibit;settings.XmlResolver=null;
        var doc=new XmlDocument();doc.XmlResolver=null;
        using(var source=new StringReader(xml))using(var reader=XmlReader.Create(source,settings))doc.Load(reader);
        var ns=new XmlNamespaceManager(doc.NameTable);ns.AddNamespace("t","http://schemas.microsoft.com/windows/2004/02/mit/task");
        var actions=doc.SelectNodes("/t:Task/t:Actions/*",ns);var principals=doc.SelectNodes("/t:Task/t:Principals/t:Principal",ns);var triggers=doc.SelectNodes("/t:Task/t:Triggers/*",ns);
        if(actions.Count!=1 || actions[0].LocalName!="Exec" || principals.Count!=1 || triggers.Count!=1 || triggers[0].LocalName!="LogonTrigger")throw new Exception("Unknown TRCC task structure.");
        Func<XmlNode,string,string> value=(node,name)=>{var values=node.SelectNodes("t:"+name,ns);if(values.Count>1)throw new Exception("Repeated TRCC task field.");return values.Count==0?"":values[0].InnerText;};
        if(!String.Equals(value(actions[0],"Command").Trim('"'),@"C:\Program Files\TRCCCAP\TRCC.exe",StringComparison.OrdinalIgnoreCase) ||
           value(actions[0],"Arguments")!="" || value(actions[0],"WorkingDirectory")!="" || AccountSid(value(principals[0],"UserId"))!=UserSid ||
           value(principals[0],"LogonType")!="InteractiveToken" || value(principals[0],"RunLevel")!="HighestAvailable" || value(triggers[0],"UserId")!="")
            throw new Exception("Unknown TRCC startup identity.");
        return doc;
    }
    private static bool TrccEnabled(XmlDocument doc) {
        var ns=new XmlNamespaceManager(doc.NameTable);ns.AddNamespace("t",doc.DocumentElement.NamespaceURI);
        var values=doc.SelectNodes("/t:Task/t:Settings/t:Enabled",ns);
        if(values.Count>1)throw new Exception("Repeated TRCC enabled setting.");
        return values.Count==0 || XmlConvert.ToBoolean(values[0].InnerText);
    }
    private static string TrccBody(XmlDocument doc) {
        var ns=new XmlNamespaceManager(doc.NameTable);ns.AddNamespace("t",doc.DocumentElement.NamespaceURI);
        var enabled=doc.SelectSingleNode("/t:Task/t:Settings/t:Enabled",ns);if(enabled!=null)enabled.ParentNode.RemoveChild(enabled);
        return doc.OuterXml;
    }
    internal static bool TrccRestoreAllowed(string before,string current,string reviewedHash) {
        try{var original=TrccDocument(before);var observed=TrccDocument(current);return TextHash(before)==reviewedHash && TrccEnabled(original) && !TrccEnabled(observed) && TrccBody(original)==TrccBody(observed);}catch{return false;}
    }
    private static void RestoreTrcc(dynamic root,string before) {
        dynamic prior=root.GetTask("TRCCAppStartup");
        if(TextHash((string)prior.Xml)==TrccTaskHash && TrccEnabled(TrccDocument((string)prior.Xml)))return;
        if(!TrccRestoreAllowed(before,(string)prior.Xml,TrccTaskHash))throw new Exception("TRCC startup changed; its current task was preserved.");
        prior.Enabled=true;
    }
    internal static void DisableStartup(Action removeOptIn,Action disableTask,Action stopTask,Action restoreTrcc) {
        removeOptIn();
        var failures=new System.Collections.Generic.List<Exception>();
        foreach(Action action in new[]{disableTask,stopTask,restoreTrcc})try{action();}catch(Exception ex){failures.Add(ex);}
        if(failures.Count!=0)throw new AggregateException("Automatic startup was opted out; one or more shutdown steps need review.",failures);
    }
    internal static void CompleteStartup(Action install,Action removeOptIn,Action disableTask,Action restoreTrcc) {
        try{install();}
        catch(Exception original){
            var failures=new System.Collections.Generic.List<Exception>();failures.Add(original);
            foreach(Action action in new[]{removeOptIn,disableTask,restoreTrcc})try{action();}catch(Exception ex){failures.Add(ex);}
            throw new AggregateException("Startup setup did not complete; retained files and any rollback failure need review.",failures);
        }
    }
    private static FileStream ProtectedFile(string path) {
        var acl=new FileSecurity();acl.SetAccessRuleProtection(true,false);acl.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
        foreach(string sid in new[]{"S-1-5-18","S-1-5-32-544"})acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),FileSystemRights.FullControl,AccessControlType.Allow));
        acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"),FileSystemRights.ReadAndExecute,AccessControlType.Allow));
        return new FileStream(path,FileMode.CreateNew,FileSystemRights.Write,FileShare.None,4096,FileOptions.None,acl);
    }
    [STAThread] private static int Main(string[] args) {
        try {
            if(args.Length>1 || (args.Length==1 && args[0]!="--disable") || Environment.MachineName!="THIS-MACHINE" || WindowsIdentity.GetCurrent().User.Value!=UserSid ||
                !new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))throw new Exception("Run this setup on THIS-MACHINE as Jude with administrator approval.");
            if(args.Length==1) {
                ValidateInstalledFolder();
                dynamic service=Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service"));service.Connect();dynamic owned=service.GetFolder("\\").GetTask(TaskName);
                ValidateTaskState(ReadTaskState(owned.Definition));
                ValidateTaskSecurity((string)owned.GetSecurityDescriptor(7));
                if(Hash(Path.Combine(Folder,"INTERLINKED.Sensor.Startup.exe"))!=SensorHash)
                    throw new Exception("Startup ownership differs; nothing was disabled.");
                DisableStartup(
                    ArchiveOptIn,
                    delegate {owned.Enabled=false;},
                    delegate {if((int)owned.State==4 || (int)owned.State==2)owned.Stop(0);},
                    delegate {
                        string saved=Path.Combine(Folder,"trcc-startup-before.xml");
                        if(File.Exists(saved)){
                            string before=File.ReadAllText(saved);var original=TrccDocument(before);
                            if(TextHash(before)!=TrccTaskHash)throw new Exception("TRCC recovery record differs; current startup was preserved.");
                            if(TrccEnabled(original))RestoreTrcc(service.GetFolder("\\"),before);
                        }
                    });
                MessageBox.Show("Automatic cooler startup is disabled. Installed files were retained.","INTERLINKED cooler startup");return 0;
            }
            if(Hash(WorkerPath)!=WorkerHash || Hash(AppPath)!=AppHash || Hash(@"C:\Program Files\TRCCCAP\HWINFO.exe")!=ExeHash || Hash(@"C:\Program Files\TRCCCAP\HWiNFO64.dll")!=DllHash)
                throw new Exception("The reviewed installed package changed. Rebuild the setup against the current approved installation.");
            dynamic scheduler=Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service"));scheduler.Connect();dynamic root=scheduler.GetFolder("\\");
            dynamic trcc=null;try{trcc=root.GetTask("TRCCAppStartup");}catch(Exception ex){if(!TaskMissing(ex))throw;}
            string trccBefore=trcc==null?null:(string)trcc.Xml;
            if((trcc==null && TrccTaskHash!="") || (trcc!=null && TextHash(trccBefore)!=TrccTaskHash))throw new Exception("The reviewed TRCC startup task changed; it was preserved.");
            bool trccWasEnabled=trcc!=null && TrccEnabled(TrccDocument(trccBefore));
            bool exists=false;try{root.GetTask(TaskName);exists=true;}catch(Exception ex){if(!TaskMissing(ex))throw;}
            bool resume=PreviousSetupHash!="";
            if(resume){
                string sensor=Path.Combine(Folder,"INTERLINKED.Sensor.Startup.exe"),previous=Path.Combine(Folder,"INTERLINKED.Cooler.Startup.Setup.exe"),before=Path.Combine(Folder,"trcc-startup-before.xml");
                bool folderProtected=Directory.Exists(Folder) && ProtectedOwned(Folder,true),sensorProtected=File.Exists(sensor) && ProtectedOwned(sensor,false),setupProtected=File.Exists(previous) && ProtectedOwned(previous,false);
                if(File.Exists(before) && !ProtectedOwned(before,false))throw new Exception("Partial TRCC receipt ownership differs.");
                ValidateRepairState(new RepairState{TaskExists=exists,FolderExists=Directory.Exists(Folder),LiveOptInExists=File.Exists(Path.Combine(Folder,"startup.json")),FolderProtected=folderProtected,SensorProtected=sensorProtected,SetupProtected=setupProtected,
                    SensorDigest=sensorProtected?Hash(sensor):null,SetupDigest=setupProtected?Hash(previous):null,ExpectedSensorDigest=SensorHash,ExpectedSetupDigest=PreviousSetupHash,
                    BeforeTrccDigest=File.Exists(before)?Hash(before):null,ReviewedTrccDigest=TrccTaskHash});
                dynamic prior=root.GetTask(TaskName);ValidateTaskState(ReadTaskState(prior.Definition));ValidateTaskSecurity((string)prior.GetSecurityDescriptor(7));
            } else if(exists || Directory.Exists(Folder))throw new Exception("Startup installation already exists; it was preserved.");
            var acl=new DirectorySecurity();acl.SetAccessRuleProtection(true,false);acl.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
            foreach(string sid in new[]{"S-1-5-18","S-1-5-32-544"})acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),FileSystemRights.FullControl,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
            acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"),FileSystemRights.ReadAndExecute,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
            if(!resume)Directory.CreateDirectory(Folder,acl);
            ValidateInstalledFolder();
            string executable=Path.Combine(Folder,"INTERLINKED.Sensor.Startup.exe");
            if(!resume)using(var payload=typeof(CoolerStartupSetup).Assembly.GetManifestResourceStream("sensor"))using(var output=ProtectedFile(executable)){payload.CopyTo(output);output.Flush(true);}
            if(Hash(executable)!=SensorHash)throw new Exception("Sensor installation readback failed.");
            CopySetupOnce(resume?"INTERLINKED.Cooler.Startup.Setup.Repaired.exe":"INTERLINKED.Cooler.Startup.Setup.exe");
            dynamic task=scheduler.NewTask(0);task.RegistrationInfo.Author="INTERLINKED";task.RegistrationInfo.Description="Approved fixed vendor temperature publisher for Jude. No project or display privileges.";
            task.Principal.UserId=UserSid;task.Principal.LogonType=3;task.Principal.RunLevel=1;
            task.Settings.DisallowStartIfOnBatteries=false;task.Settings.StopIfGoingOnBatteries=false;task.Settings.ExecutionTimeLimit="PT0S";task.Settings.MultipleInstances=2;
            task.Settings.RestartCount=3;task.Settings.RestartInterval="PT1M";
            task.Settings.Enabled=false;
            dynamic trigger=task.Triggers.Create(9);trigger.UserId=UserSid;
            dynamic action=task.Actions.Create(0);action.Path=executable;action.WorkingDirectory=Folder;
            string sddl=TaskSddl+"(A;;GRGX;;;"+UserSid+")";
            if(!resume)root.RegisterTaskDefinition(TaskName,task,2,UserSid,null,3,sddl);
            dynamic installed=root.GetTask(TaskName);
            ValidateTaskState(ReadTaskState(installed.Definition));
            ValidateTaskSecurity((string)installed.GetSecurityDescriptor(7));
            if(!resume && installed.Enabled)throw new Exception("New startup task was unexpectedly enabled.");
            CompleteStartup(delegate {
                installed.Enabled=false;if(installed.Enabled)throw new Exception("Owned startup task could not be paused for repair.");
                if(trcc!=null){
                    if(TextHash((string)trcc.Xml)!=TrccTaskHash)throw new Exception("TRCC startup changed during setup; it was preserved.");
                    ProtectedTextOnce("trcc-startup-before.xml",trccBefore);
                    if(trccWasEnabled){
                        trcc.Enabled=false;
                        if(!TrccRestoreAllowed(trccBefore,(string)trcc.Xml,TrccTaskHash))throw new Exception("TRCC disabled-state readback differs.");
                        ProtectedTextOnce("trcc-startup-disabled.xml",(string)trcc.Xml);
                    }
                }
                installed.Enabled=true;
                if(!installed.Enabled)throw new Exception("Startup task could not be enabled.");
                CommitOptIn();
            },ArchiveOptIn,
            delegate {ValidateTaskState(ReadTaskState(installed.Definition));ValidateTaskSecurity((string)installed.GetSecurityDescriptor(7));installed.Enabled=false;},
            delegate {if(trccWasEnabled)RestoreTrcc(root,trccBefore);});
            // Live acceptance starts the exact installed task explicitly, separately
            // from setup. No existing manual publisher is stopped here.
            MessageBox.Show("Automatic cooler startup is installed. The temperature reader will start at your next sign-in.","INTERLINKED cooler startup");return 0;
        } catch(Exception ex){MessageBox.Show(ex.Message,"INTERLINKED startup setup",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
}
