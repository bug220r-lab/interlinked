// Fixed-function logon temperature publisher. Installed under Program Files.
// No arguments, IPC commands, project access, or configurable executable paths.
using System;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Threading;

internal static partial class SensorStartup
{
    private const string Vendor = @"C:\Program Files\TRCCCAP";
    private static bool Exists(string name) {
        var list=Process.GetProcessesByName(name); bool found=list.Length!=0;
        foreach(var process in list)process.Dispose(); return found;
    }
    private static FileStream Pin(string name,string hash) {
        var file=File.Open(Path.Combine(Vendor,name),FileMode.Open,FileAccess.Read,FileShare.Read);
        try {using(var sha=SHA256.Create())if(BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant()!=hash)
            throw new IOException("Reviewed vendor bytes changed."); return file;}
        catch{file.Dispose();throw;}
    }
    internal static MemoryMappedFileSecurity SensorMapSecurity() {
        // SECTION_ALL_ACCESS includes SECTION_EXTEND_SIZE, omitted by .NET FullControl.
        var security=new MemoryMappedFileSecurity();
        security.SetSecurityDescriptorSddlForm("D:P(A;;GA;;;"+UserSid+")");
        return security;
    }
    [STAThread] private static int Main(string[] args) {
        if(args.Length!=0)return 2;
        if(Environment.MachineName!="THIS-MACHINE" || WindowsIdentity.GetCurrent().User.Value!=UserSid ||
            !new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))return 3;
        // Never replace or terminate another publisher. A currently running manual
        // lease may finish during setup; otherwise the task waits for next logon.
        for(int i=0;i<60 && (Exists("TRCC")||Exists("HWINFO"));i++)Thread.Sleep(1000);
        if(Exists("TRCC")||Exists("HWINFO"))return 4;
        try {
            using(var exe=Pin("HWINFO.exe",ExeHash))using(var dll=Pin("HWiNFO64.dll",DllHash)) {
                var security=SensorMapSecurity();
                using(var map=MemoryMappedFile.CreateNew("shareMemory_SysInfo",2097152,MemoryMappedFileAccess.ReadWrite,MemoryMappedFileOptions.None,security,HandleInheritability.None)) {
                    IntPtr job=NewHelperJob(); Process child=null;
                    try {
                        child=StartContainedSensor(job);
                        while(!child.WaitForExit(1000))if(Exists("TRCC"))return 4;
                        return 5; // An early publisher exit is a retryable task failure.
                    } finally {CloseHandle(job);if(child!=null){try{child.WaitForExit(1500);}catch{}child.Dispose();}}
                }
            }
        } catch {return 5;}
    }
}
