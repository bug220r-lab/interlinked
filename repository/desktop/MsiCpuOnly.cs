// Fixed MSI CPU-package reader. No vendor process, EC, battery, fan or USB API.
// PawnIO wire protocol and Intel DTS decoding follow LibreHardwareMonitor v0.9.6
// (MPL-2.0): PawnIo/PawnIo.cs and Hardware/Cpu/IntelCpu.cs. See MSI-SENSORS.md.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

internal interface IMsiCpuMsr { bool Read(uint register, out ulong value); }

internal sealed class MsiCpuPawn : IMsiCpuMsr, IDisposable
{
    private SafeFileHandle handle;
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFile(string path,uint access,uint share,IntPtr security,uint mode,uint flags,IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool DeviceIoControl(SafeFileHandle file,uint code,byte[] input,int inputSize,byte[] output,int outputSize,out uint returned,IntPtr overlapped);
    internal MsiCpuPawn() {
        byte[] module;
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("IntelMSR"))
        using(var memory=new MemoryStream()) { if(stream==null)throw new IOException("Intel module missing.");stream.CopyTo(memory);module=memory.ToArray(); }
        if(MsiSensorCommon.Hash(module)!=MsiCpuOnlyPins.ModuleHash)throw new IOException("Intel module hash differs.");
        handle=CreateFile(@"\\?\GLOBALROOT\Device\PawnIO",0xc0000000,3,IntPtr.Zero,3,0,IntPtr.Zero);
        if(handle.IsInvalid) { handle.Dispose();throw new IOException("Signed PawnIO driver unavailable."); }
        uint returned;
        if(!DeviceIoControl(handle,0xa1b22084,module,module.Length,null,0,out returned,IntPtr.Zero)) { handle.Dispose();throw new IOException("Signed Intel module refused."); }
    }
    public bool Read(uint register,out ulong value) {
        value=0;
        // Neither callers nor command-line arguments can select another MSR.
        if(register!=MsiCpuOnly.Target && register!=MsiCpuOnly.Package)return false;
        byte[] input=new byte[40],output=new byte[8];
        Encoding.ASCII.GetBytes("ioctl_read_msr").CopyTo(input,0);
        BitConverter.GetBytes((long)register).CopyTo(input,32);uint returned;
        if(!DeviceIoControl(handle,0xa1b22104,input,input.Length,output,output.Length,out returned,IntPtr.Zero)||returned!=8)return false;
        value=BitConverter.ToUInt64(output,0);return true;
    }
    public void Dispose(){if(handle!=null)handle.Dispose();}
}

internal static class MsiCpuOnly
{
    internal const uint Target=0x1a2,Package=0x1b1;
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentThread();
    [DllImport("kernel32.dll",SetLastError=true)] private static extern UIntPtr SetThreadAffinityMask(IntPtr thread,UIntPtr mask);
    internal static double? Sample(IMsiCpuMsr reader) {
        ulong target,package;
        if(!reader.Read(Target,out target)||!reader.Read(Package,out package))return null;
        if((package&0x80000000UL)==0)return null;
        uint maximum=(uint)((target>>16)&0xff),delta=(uint)((package>>16)&0x7f);
        if(maximum<80||maximum>125||delta>maximum)return null;
        return maximum-delta;
    }
    private static double? ReadCpu(IMsiCpuMsr reader) {
        UIntPtr previous=SetThreadAffinityMask(GetCurrentThread(),new UIntPtr(1));
        if(previous==UIntPtr.Zero)throw new IOException("Cannot select reviewed CPU zero.");
        try{return Sample(reader);}finally{if(SetThreadAffinityMask(GetCurrentThread(),previous)==UIntPtr.Zero)throw new IOException("Cannot restore CPU affinity.");}
    }
    private static void Identity() {
        if(!MsiSensorCommon.IdentityMatches(Environment.MachineName,WindowsIdentity.GetCurrent().User.Value)||!MsiSensorCommon.Administrator())throw new IOException("Reviewed MSI administrator identity required.");
        using(var query=new ManagementObjectSearcher("SELECT Name,Manufacturer,ProcessorId FROM Win32_Processor"))
        using(var rows=query.Get()) {
            if(rows.Count!=1)throw new IOException("One reviewed Intel CPU required.");
            foreach(ManagementObject row in rows)using(row)if((string)row["Manufacturer"]!="GenuineIntel"||((string)row["Name"]).IndexOf("i7-10750H",StringComparison.OrdinalIgnoreCase)<0||(string)row["ProcessorId"]!="BFEBFBFF000A0652")throw new IOException("Reviewed MSI CPU differs.");
        }
    }
    private static Dictionary<string,object> Installed() {
        if(!String.Equals(Assembly.GetExecutingAssembly().Location,MsiSensorCommon.PublisherPath,StringComparison.OrdinalIgnoreCase))throw new IOException("Production location required.");
        foreach(string p in new[]{MsiSensorCommon.Folder,MsiSensorCommon.DataParent,MsiSensorCommon.DataFolder})MsiSensorCommon.Protected(p,true);
        MsiSensorCommon.Protected(MsiSensorCommon.PublisherPath,false);MsiSensorCommon.Protected(MsiSensorCommon.DescriptorPath,false);
        if(new FileInfo(MsiSensorCommon.DescriptorPath).Length>8192)throw new IOException("Descriptor exceeds bounds.");
        var d=MsiSensorCommon.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(MsiSensorCommon.DescriptorPath));
        if((string)d["format"]!="interlinked-msi-sensor-descriptor-v1"||(string)d["machine"]!="MSI"||(string)d["userSid"]!=MsiCpuOnlyPins.UserSid||(string)d["publisherPath"]!=MsiSensorCommon.PublisherPath||(string)d["outputPath"]!=MsiSensorCommon.OutputPath||(string)d["backend"]!="pawnio-intel-cpu-only-v1"||(string)d["moduleSha256"]!=MsiCpuOnlyPins.ModuleHash||(string)d["publisherSha256"]!=MsiSensorCommon.HashFile(MsiSensorCommon.PublisherPath))throw new IOException("CPU-only descriptor differs.");
        return d;
    }
    private static void Publish(string self,double? sample) {
        var cpu=MsiSensorCommon.Missing();
        if(sample.HasValue)cpu=new Dictionary<string,object>{{"value",sample.Value},{"status","verified"},{"source","reviewed-sensor-publisher"},{"observedAt",DateTime.UtcNow.ToString("o")}};
        var result=new Dictionary<string,object>{{"format","interlinked-sensor-telemetry-v1"},{"machine","MSI"},{"publisherSha256",self},{"observedAt",DateTime.UtcNow.ToString("o")},{"cpuTemperature",cpu},{"gpuTemperature",MsiSensorCommon.Missing()}};
        byte[] data=new UTF8Encoding(false).GetBytes(MsiSensorCommon.Json.Serialize(result));if(data.Length>8192)throw new IOException("Telemetry exceeds bounds.");
        string pending=Path.Combine(MsiSensorCommon.DataFolder,"temperatures.pending-"+Guid.NewGuid().ToString("N")+".json");
        try {
            using(var f=MsiSensorCommon.NewProtectedFile(pending)){f.Write(data,0,data.Length);f.Flush(true);}
            if(File.Exists(MsiSensorCommon.OutputPath)){MsiSensorCommon.Protected(MsiSensorCommon.OutputPath,false);File.Replace(pending,MsiSensorCommon.OutputPath,null);}else File.Move(pending,MsiSensorCommon.OutputPath);
        }finally{if(File.Exists(pending))File.Delete(pending);}
    }
    private static void Conflict() {
        foreach(string name in new[]{"HWINFO","HWiNFO64","LibreHardwareMonitor","OpenHardwareMonitor"})foreach(var p in Process.GetProcessesByName(name))using(p)throw new IOException("Existing sensor reader preserved.");
    }
    internal static bool TaskActionMatches(int count,string path,string arguments,string sid) {
        return count==1&&String.Equals(path,MsiSensorCommon.PublisherPath,StringComparison.OrdinalIgnoreCase)&&String.IsNullOrEmpty(arguments)&&sid==MsiCpuOnlyPins.UserSid;
    }
    private static void Disable() {
        dynamic service=Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service"));service.Connect();dynamic task=service.GetFolder("\\").GetTask(MsiSensorCommon.TaskName);
        if(!TaskActionMatches((int)task.Definition.Actions.Count,(string)task.Definition.Actions.Item(1).Path,(string)task.Definition.Actions.Item(1).Arguments,MsiSensorCommon.AccountSid((string)task.Definition.Principal.UserId))||!String.Equals((string)task.Definition.Actions.Item(1).WorkingDirectory,MsiSensorCommon.Folder,StringComparison.OrdinalIgnoreCase)||(int)task.Definition.Principal.LogonType!=3||(int)task.Definition.Principal.RunLevel!=1||(int)task.Definition.Triggers.Count!=1||(int)task.Definition.Triggers.Item(1).Type!=9||MsiSensorCommon.AccountSid((string)task.Definition.Triggers.Item(1).UserId)!=MsiCpuOnlyPins.UserSid)throw new IOException("Owned task differs.");
        string sddl=(string)task.GetSecurityDescriptor(7);var security=new System.Security.AccessControl.RawSecurityDescriptor(sddl);if(!security.ControlFlags.HasFlag(System.Security.AccessControl.ControlFlags.DiscretionaryAclProtected)||security.Owner==null||(security.Owner.Value!="S-1-5-18"&&security.Owner.Value!="S-1-5-32-544")||security.DiscretionaryAcl==null)throw new IOException("Task security differs.");
        bool system=false,admins=false;
        foreach(System.Security.AccessControl.GenericAce ace in security.DiscretionaryAcl){var access=ace as System.Security.AccessControl.CommonAce;if(access==null||access.AceFlags!=System.Security.AccessControl.AceFlags.None||access.AceQualifier!=System.Security.AccessControl.AceQualifier.AccessAllowed||(access.SecurityIdentifier.Value!="S-1-5-18"&&access.SecurityIdentifier.Value!="S-1-5-32-544")||((access.AccessMask&0x10000000)==0&&(access.AccessMask&0x001f01ff)!=0x001f01ff))throw new IOException("Task permissions differ.");if(access.SecurityIdentifier.Value=="S-1-5-18")system=true;else admins=true;}
        if(!system||!admins)throw new IOException("Task owner permissions incomplete.");
        task.Enabled=false;if((int)task.State==4||(int)task.State==2)task.Stop(0);
    }
    private sealed class Fake:IMsiCpuMsr {
        internal ulong target,package;internal bool ok=true;internal List<uint> reads=new List<uint>();
        public bool Read(uint r,out ulong v){reads.Add(r);v=r==Target?target:package;return ok;}
    }
    private static void SelfTest() {
        int count=0;Action<bool> check=delegate(bool value){if(!value)throw new Exception("CPU decode fixture failed: "+count);count++;};
        var f=new Fake{target=100UL<<16,package=0x80000000UL|(51UL<<16)};check(Sample(f)==49);check(f.reads.Count==2&&f.reads[0]==Target&&f.reads[1]==Package);
        f.package=0;check(Sample(f)==null);f.package=0x80000000UL|(101UL<<16);check(Sample(f)==null);
        f.package=0x80000000UL;check(Sample(f)==100);f.target=0;check(Sample(f)==null);f.target=79UL<<16;check(Sample(f)==null);f.target=126UL<<16;check(Sample(f)==null);
        f.target=100UL<<16;f.ok=false;check(Sample(f)==null);f.ok=true;f.package=0x80000000UL|(100UL<<16);check(Sample(f)==0);
        check(Sample(f)==0); // Constant valid readings remain fresh; no change detector.
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("IntelMSR"))using(var memory=new MemoryStream()){stream.CopyTo(memory);check(MsiSensorCommon.Hash(memory.ToArray())==MsiCpuOnlyPins.ModuleHash);}
        check(TaskActionMatches(1,MsiSensorCommon.PublisherPath,null,MsiCpuOnlyPins.UserSid));check(TaskActionMatches(1,MsiSensorCommon.PublisherPath,"",MsiCpuOnlyPins.UserSid));check(!TaskActionMatches(1,MsiSensorCommon.PublisherPath,"--probe",MsiCpuOnlyPins.UserSid));check(!TaskActionMatches(2,MsiSensorCommon.PublisherPath,"",MsiCpuOnlyPins.UserSid));
        Console.WriteLine("{\"status\":\"PASS\",\"checks\":"+count+",\"hardwareStarted\":false}");
    }
    [STAThread] private static int Main(string[] args) {
        try {
            if(args.Length==1&&args[0]=="--self-test"){SelfTest();return 0;}
            bool probe=args.Length==1&&args[0]=="--probe";
            if(args.Length>1||(args.Length==1&&!probe&&args[0]!="--disable"))return 2;
            Identity();Conflict();
            if(probe){using(var reader=new MsiCpuPawn()){var samples=new List<object>();for(int n=0;n<15;n++){double? sample=ReadCpu(reader);samples.Add(new{utc=DateTime.UtcNow.ToString("o"),cpuPackageC=sample});Thread.Sleep(1000);}Console.WriteLine(MsiSensorCommon.Json.Serialize(new{format="interlinked-cpu-only-probe-v1",moduleSha256=MsiCpuOnlyPins.ModuleHash,samples=samples}));}return 0;}
            var descriptor=Installed();if(args.Length==1){Disable();Console.WriteLine("{\"status\":\"DISABLED\"}");return 0;}
            string lockPath=Path.Combine(MsiSensorCommon.DataFolder,"cpu-only.lock");
            if(!File.Exists(lockPath))using(var created=MsiSensorCommon.NewProtectedFile(lockPath)){}
            MsiSensorCommon.Protected(lockPath,false);
            using(var singleton=File.Open(lockPath,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
            using(var reader=new MsiCpuPawn())while(true){Conflict();Publish((string)descriptor["publisherSha256"],ReadCpu(reader));Thread.Sleep(1000);}
        }catch(Exception e){Console.Error.WriteLine(e.GetType().Name+": "+e.Message);return 3;}
    }
}
