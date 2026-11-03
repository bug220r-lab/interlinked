// MSI-specific sensor telemetry. These paths never select a project or USB device.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;
using System.Runtime.InteropServices;

internal static class MsiSensorCommon
{
    internal const string Folder = @"C:\Program Files\INTERLINKED MSI Sensors";
    internal const string DataParent = @"C:\ProgramData\INTERLINKED";
    internal const string DataFolder = @"C:\ProgramData\INTERLINKED\MSI-Sensors";
    internal const string PublisherPath = Folder + @"\INTERLINKED.MSI.Sensor.exe";
    internal const string OutputPath = DataFolder + @"\temperatures.json";
    internal const string DescriptorPath = DataFolder + @"\descriptor.json";
    internal const string TaskName = "INTERLINKED MSI Sensor Telemetry";
    internal const string Vendor = Folder + @"\Vendor";
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 1048576, RecursionLimit = 16 };
    internal static bool IdentityMatches(string machine, string sid) { return machine == "MSI" && sid == MsiSensorPins.UserSid; }
    internal static bool Administrator() { return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); }
    internal static string Hash(byte[] bytes) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    internal static string HashFile(string path) { using (var sha = SHA256.Create()) using (var f = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read)) return BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant(); }
    [StructLayout(LayoutKind.Sequential)] private struct FileIdentity { internal uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(Microsoft.Win32.SafeHandles.SafeFileHandle handle, out FileIdentity identity);
    internal static void Ordinary(string path, bool directory) {
        string walk = Path.GetFullPath(path);
        while (walk != null) { if (((int)File.GetAttributes(walk) & 0x441400) != 0) throw new IOException("Linked, cloud or offline sensor path refused."); walk = Path.GetDirectoryName(walk); }
        if (directory) { if (!Directory.Exists(path)) throw new IOException("Sensor directory missing."); }
        else using (var f = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read)) { FileIdentity identity; if (!GetFileInformationByHandle(f.SafeFileHandle, out identity) || identity.Links != 1) throw new IOException("Sensor payload must have one local link."); }
    }
    internal static void ValidateSecurity(FileSystemSecurity security) {
        string owner = security.GetOwner(typeof(SecurityIdentifier)).Value;
        if (!security.AreAccessRulesProtected || (owner != "S-1-5-18" && owner != "S-1-5-32-544")) throw new IOException("Sensor ownership or protected permissions differ.");
        const FileSystemRights writes = FileSystemRights.Write | FileSystemRights.Delete | FileSystemRights.DeleteSubdirectoriesAndFiles | FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;
        bool system = false, admins = false;
        foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier))) {
            string sid = rule.IdentityReference.Value;
            if (rule.AccessControlType != AccessControlType.Allow) continue;
            if (sid == "S-1-5-18") system = true;
            else if (sid == "S-1-5-32-544") admins = true;
            else if ((rule.FileSystemRights & writes) != 0) throw new IOException("Sensor path grants unprivileged write access.");
        }
        if (!system || !admins) throw new IOException("Sensor owners have incomplete access.");
    }
    internal static void Protected(string path, bool directory) {
        Ordinary(path, directory); ValidateSecurity(directory ? (FileSystemSecurity)Directory.GetAccessControl(path) : File.GetAccessControl(path));
    }
    internal static DirectorySecurity DirectoryAcl() {
        var acl = new DirectorySecurity(); acl.SetAccessRuleProtection(true, false); acl.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
        foreach (string sid in new[] { "S-1-5-18", "S-1-5-32-544" }) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid), FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"), FileSystemRights.ReadAndExecute, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow)); return acl;
    }
    internal static FileSecurity FileAcl() {
        var acl = new FileSecurity(); acl.SetAccessRuleProtection(true, false); acl.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
        foreach (string sid in new[] { "S-1-5-18", "S-1-5-32-544" }) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid), FileSystemRights.FullControl, AccessControlType.Allow));
        acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"), FileSystemRights.ReadAndExecute, AccessControlType.Allow)); return acl;
    }
    internal static FileStream NewProtectedFile(string path) { return new FileStream(path, FileMode.CreateNew, FileSystemRights.Write, FileShare.None, 4096, FileOptions.None, FileAcl()); }
    internal static Dictionary<string, object> Missing() { return new Dictionary<string, object> { { "value", null }, { "status", "unavailable" }, { "source", "reviewed-sensor-publisher" }, { "observedAt", null } }; }
    internal static Dictionary<string, object> ParseCpu(string text, DateTime observed) {
        if (text == null || text.Length > 1048576 || !text.StartsWith("HWi32_GetNumberOfDetectedSensors:\n", StringComparison.Ordinal)) throw new InvalidDataException("Unrecognized sensor format.");
        int start = text.IndexOf('{'); if (start < 0) throw new InvalidDataException("Sensor object missing.");
        var groups = Json.DeserializeObject(text.Substring(start)) as Dictionary<string, object>; if (groups == null) throw new InvalidDataException("Sensor groups missing.");
        Dictionary<string, object> found = null;
        foreach (var group in groups) {
            // This target is the reviewed Intel i7-10750H. AMD/ACPI/core estimates
            // and other CPU packages are deliberately not aliases for its package.
            if (!group.Key.StartsWith("CPU [#0]:", StringComparison.OrdinalIgnoreCase) || group.Key.IndexOf(MsiSensorPins.CpuModelSubstring, StringComparison.OrdinalIgnoreCase) < 0 || !group.Key.EndsWith(MsiSensorPins.CpuGroupSuffix, StringComparison.OrdinalIgnoreCase)) continue;
            var rows = group.Value as object[]; if (rows == null) continue;
            foreach (object row in rows) {
                var pair = row as Dictionary<string, object>; object raw; if (pair == null || !pair.TryGetValue(MsiSensorPins.CpuSensorName, out raw)) continue;
                var reading = raw as Dictionary<string, object>; object type, value;
                if (reading == null || !reading.TryGetValue("type", out type) || !Object.Equals(type, "temperature") || !reading.TryGetValue("value", out value) || value is bool || value == null) continue;
                double number; if (!Double.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out number) || Double.IsNaN(number) || Double.IsInfinity(number) || number < 0 || number >= 150) continue;
                if (found != null) throw new InvalidDataException("Ambiguous CPU package sensor.");
                found = new Dictionary<string, object> { { "value", number }, { "status", "verified" }, { "source", "reviewed-sensor-publisher" }, { "observedAt", observed.ToUniversalTime().ToString("o") } };
            }
        }
        return found ?? Missing();
    }
    internal static bool SampleFresh(DateTime changed, DateTime now) { return now >= changed && (now - changed).TotalSeconds <= 5; }
    internal static string AccountSid(string value) { return value.StartsWith("S-1-", StringComparison.Ordinal) ? new SecurityIdentifier(value).Value : ((SecurityIdentifier)new NTAccount(value).Translate(typeof(SecurityIdentifier))).Value; }
}
