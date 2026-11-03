// Fixed-function elevated publisher. No command, project or model execution API.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;

internal static partial class MsiSensorPublisher
{
    private static bool ConflictingPublisher(int ownedPid) {
        foreach (string name in new[] { "TRCC", "HWINFO", "HWiNFO64", "LibreHardwareMonitor" }) {
            var list = Process.GetProcessesByName(name); bool found = false; foreach (var p in list) { if (p.Id != ownedPid) found = true; p.Dispose(); } if (found) return true;
        }
        return false;
    }
    private static FileStream Pin(string name, string hash) {
        string path = Path.Combine(MsiSensorCommon.Vendor, name); MsiSensorCommon.Protected(path, false);
        var f = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        try { using (var sha = SHA256.Create()) if (BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant() != hash) throw new IOException("Reviewed sensor payload changed."); return f; } catch { f.Dispose(); throw; }
    }
    private static void Publish(string self, Dictionary<string, object> cpu) {
        MsiSensorCommon.Protected(MsiSensorCommon.DataParent, true); MsiSensorCommon.Protected(MsiSensorCommon.DataFolder, true);
        var value = new Dictionary<string, object> { { "format", "interlinked-sensor-telemetry-v1" }, { "machine", "MSI" }, { "publisherSha256", self }, { "observedAt", DateTime.UtcNow.ToString("o") }, { "cpuTemperature", cpu }, { "gpuTemperature", MsiSensorCommon.Missing() } };
        byte[] bytes = new UTF8Encoding(false).GetBytes(MsiSensorCommon.Json.Serialize(value)); if (bytes.Length > 16384) throw new IOException("Sensor result exceeds bounds.");
        string temp = Path.Combine(MsiSensorCommon.DataFolder, "temperatures.pending-" + Guid.NewGuid().ToString("N") + ".json");
        using (var f = MsiSensorCommon.NewProtectedFile(temp)) { f.Write(bytes, 0, bytes.Length); f.Flush(true); }
        try { if (File.Exists(MsiSensorCommon.OutputPath)) { MsiSensorCommon.Protected(MsiSensorCommon.OutputPath, false); File.Replace(temp, MsiSensorCommon.OutputPath, null); } else File.Move(temp, MsiSensorCommon.OutputPath); }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    [STAThread] private static int Main(string[] args) {
        if (args.Length != 0) return 2;
        if (!MsiSensorCommon.IdentityMatches(Environment.MachineName, WindowsIdentity.GetCurrent().User.Value) || !MsiSensorCommon.Administrator()) return 3;
        try {
            string self = typeof(MsiSensorPublisher).Assembly.Location;
            if (!String.Equals(self, MsiSensorCommon.PublisherPath, StringComparison.OrdinalIgnoreCase)) return 3;
            MsiSensorCommon.Protected(MsiSensorCommon.Folder, true); MsiSensorCommon.Protected(self, false); MsiSensorCommon.Protected(MsiSensorCommon.Vendor, true);
            string digest = MsiSensorCommon.HashFile(self);
            MsiSensorCommon.Protected(MsiSensorCommon.DescriptorPath, false);
            var descriptor = MsiSensorCommon.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(MsiSensorCommon.DescriptorPath));
            if ((string)descriptor["publisherSha256"] != digest || (string)descriptor["machine"] != "MSI" || (string)descriptor["userSid"] != MsiSensorPins.UserSid || (string)descriptor["outputPath"] != MsiSensorCommon.OutputPath || (string)descriptor["publisherPath"] != MsiSensorCommon.PublisherPath) throw new IOException("Sensor descriptor differs.");
            if (ConflictingPublisher(0)) return 4;
            using (var exe = Pin("HWINFO.exe", MsiSensorPins.ExeHash)) using (var dll = Pin("HWiNFO64.dll", MsiSensorPins.DllHash)) {
                var security = new MemoryMappedFileSecurity(); security.SetSecurityDescriptorSddlForm("D:P(A;;GA;;;SY)(A;;GA;;;BA)");
                using (var map = MemoryMappedFile.CreateNew("shareMemory_SysInfo", 2097152, MemoryMappedFileAccess.ReadWrite, MemoryMappedFileOptions.None, security, HandleInheritability.None)) {
                    IntPtr job = NewHelperJob(); Process child = null;
                    try {
                        child = StartContainedSensor(job); byte[] first = new byte[1048576], second = new byte[1048576]; string previous = null; DateTime changed = DateTime.MinValue;
                        using (var view = map.CreateViewAccessor(0, first.Length, MemoryMappedFileAccess.Read)) {
                            while (!child.WaitForExit(1000)) {
                                if (ConflictingPublisher(child.Id)) return 4;
                                var cpu = MsiSensorCommon.Missing();
                                try {
                                    view.ReadArray(0, first, 0, first.Length); Thread.Sleep(2); view.ReadArray(0, second, 0, second.Length);
                                    int length = Array.IndexOf(first, (byte)0); if (length < 0 || length != Array.IndexOf(second, (byte)0)) throw new InvalidDataException();
                                    for (int i = 0; i < length; i++) if (first[i] != second[i]) throw new InvalidDataException();
                                    string hash; using (var sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(first, 0, length));
                                    if (previous != hash) { previous = hash; changed = DateTime.UtcNow; }
                                    if (!MsiSensorCommon.SampleFresh(changed, DateTime.UtcNow)) throw new IOException("Sensor sample stopped changing.");
                                    cpu = MsiSensorCommon.ParseCpu(Encoding.ASCII.GetString(first, 0, length), DateTime.UtcNow);
                                } catch (Exception ex) { if (!(ex is InvalidDataException || ex is ArgumentException || ex is IOException)) throw; }
                                Publish(digest, cpu);
                            }
                        }
                        return 5;
                    } finally { CloseHandle(job); if (child != null) { try { child.WaitForExit(1500); } catch { } child.Dispose(); } }
                }
            }
        } catch { return 5; }
    }
}
