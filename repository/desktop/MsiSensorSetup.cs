// Explicit UAC installation of an embedded, MSI-pinned sensor package only.
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Windows.Forms;

internal static class MsiSensorSetup
{
    private const string TaskSddl = "O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)";
    internal sealed class TaskState {
        internal int ActionCount, ActionType, TriggerCount, TriggerType, LogonType, RunLevel, MultipleInstances, RestartCount;
        internal string ActionPath, Arguments, WorkingDirectory, PrincipalSid, TriggerSid, ExecutionTimeLimit, RestartInterval;
        internal bool TriggerEnabled, DisallowBatteries, StopOnBatteries;
    }
    internal static bool TaskMissing(Exception ex) { return ex.HResult == unchecked((int)0x80070002); }
    internal static bool PrincipalMatches(string name) { try { return MsiSensorCommon.AccountSid(name) == MsiSensorPins.UserSid; } catch { return false; } }
    private static TaskState ReadTaskState(dynamic task) {
        var result = new TaskState(); result.ActionCount = (int)task.Actions.Count; result.TriggerCount = (int)task.Triggers.Count;
        if (result.ActionCount == 1) { dynamic action = task.Actions.Item(1); result.ActionType = (int)action.Type; result.ActionPath = (string)action.Path; result.Arguments = (string)action.Arguments; result.WorkingDirectory = (string)action.WorkingDirectory; }
        if (result.TriggerCount == 1) { dynamic trigger = task.Triggers.Item(1); result.TriggerType = (int)trigger.Type; result.TriggerEnabled = (bool)trigger.Enabled; if (result.TriggerType == 9) result.TriggerSid = (string)trigger.UserId; }
        result.PrincipalSid = (string)task.Principal.UserId; result.LogonType = (int)task.Principal.LogonType; result.RunLevel = (int)task.Principal.RunLevel;
        result.MultipleInstances = (int)task.Settings.MultipleInstances; result.RestartCount = (int)task.Settings.RestartCount; result.RestartInterval = (string)task.Settings.RestartInterval; result.ExecutionTimeLimit = (string)task.Settings.ExecutionTimeLimit;
        result.DisallowBatteries = (bool)task.Settings.DisallowStartIfOnBatteries; result.StopOnBatteries = (bool)task.Settings.StopIfGoingOnBatteries; return result;
    }
    internal static void ValidateTaskState(TaskState value) {
        if (value.ActionCount != 1 || value.ActionType != 0 || !String.Equals(value.ActionPath, MsiSensorCommon.PublisherPath, StringComparison.OrdinalIgnoreCase) || !String.IsNullOrEmpty(value.Arguments) || !String.Equals(value.WorkingDirectory, MsiSensorCommon.Folder, StringComparison.OrdinalIgnoreCase) ||
            !PrincipalMatches(value.PrincipalSid) || value.LogonType != 3 || value.RunLevel != 1 || value.TriggerCount != 1 || value.TriggerType != 9 || !value.TriggerEnabled || !PrincipalMatches(value.TriggerSid) ||
            value.MultipleInstances != 2 || value.RestartCount != 3 || value.RestartInterval != "PT1M" || value.ExecutionTimeLimit != "PT0S" || value.DisallowBatteries || value.StopOnBatteries) throw new IOException("MSI sensor task ownership differs; it was preserved.");
    }
    internal static void ValidateTaskSecurity(string sddl) {
        var security = new RawSecurityDescriptor(sddl);
        if (security.Owner == null || (security.Owner.Value != "S-1-5-18" && security.Owner.Value != "S-1-5-32-544") || (security.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0 || security.DiscretionaryAcl == null) throw new IOException("Sensor task ownership differs.");
        bool system = false, administrators = false;
        foreach (GenericAce raw in security.DiscretionaryAcl) {
            var ace = raw as CommonAce; if (ace == null || ace.AceQualifier != AceQualifier.AccessAllowed || ace.AceFlags != AceFlags.None) throw new IOException("Unexpected sensor task permissions.");
            string sid = ace.SecurityIdentifier.Value;
            if ((ace.AccessMask & 0x10000000) == 0 && (ace.AccessMask & 0x001f01ff) != 0x001f01ff) throw new IOException("Sensor task owners lack full control.");
            if (sid == "S-1-5-18") system = true; else if (sid == "S-1-5-32-544") administrators = true; else throw new IOException("Sensor task grants access outside administrators and SYSTEM.");
        }
        if (!system || !administrators) throw new IOException("Sensor task owner permissions incomplete.");
    }
    private static byte[] Payload(string name, string expected) {
        using (var input = typeof(MsiSensorSetup).Assembly.GetManifestResourceStream(name)) {
            if (input == null || input.Length > 4194304) throw new IOException("Embedded sensor payload missing or too large.");
            using (var output = new MemoryStream()) { input.CopyTo(output); byte[] bytes = output.ToArray(); if (MsiSensorCommon.Hash(bytes) != expected) throw new IOException("Embedded sensor payload changed."); return bytes; }
        }
    }
    private static void WriteOnce(string path, byte[] bytes) { using (var f = MsiSensorCommon.NewProtectedFile(path)) { f.Write(bytes, 0, bytes.Length); f.Flush(true); } if (MsiSensorCommon.HashFile(path) != MsiSensorCommon.Hash(bytes)) throw new IOException("Sensor payload readback failed."); }
    private static void NewDirectory(string path) { Directory.CreateDirectory(path, MsiSensorCommon.DirectoryAcl()); MsiSensorCommon.Protected(path, true); }
    private static Dictionary<string, object> Descriptor() { return new Dictionary<string, object> {
        { "format", "interlinked-msi-sensor-descriptor-v1" }, { "machine", "MSI" }, { "userSid", MsiSensorPins.UserSid }, { "publisherSha256", MsiSensorPins.PublisherHash }, { "publisherPath", MsiSensorCommon.PublisherPath }, { "outputPath", MsiSensorCommon.OutputPath },
        { "cpuSensorName", MsiSensorPins.CpuSensorName }, { "cpuGroupSuffix", MsiSensorPins.CpuGroupSuffix }, { "cpuModelSubstring", MsiSensorPins.CpuModelSubstring },
        { "vendorHashes", new Dictionary<string, object> { { "HWINFO.exe", MsiSensorPins.ExeHash }, { "HWiNFO64.dll", MsiSensorPins.DllHash } } } }; }
    private static void ValidateInstalled() {
        foreach (string directory in new[] { MsiSensorCommon.Folder, MsiSensorCommon.Vendor, MsiSensorCommon.DataParent, MsiSensorCommon.DataFolder }) MsiSensorCommon.Protected(directory, true);
        foreach (var pair in new[] { new[] { MsiSensorCommon.PublisherPath, MsiSensorPins.PublisherHash }, new[] { Path.Combine(MsiSensorCommon.Vendor, "HWINFO.exe"), MsiSensorPins.ExeHash }, new[] { Path.Combine(MsiSensorCommon.Vendor, "HWiNFO64.dll"), MsiSensorPins.DllHash } }) { MsiSensorCommon.Protected(pair[0], false); if (MsiSensorCommon.HashFile(pair[0]) != pair[1]) throw new IOException("Installed MSI sensor bytes differ."); }
        MsiSensorCommon.Protected(MsiSensorCommon.DescriptorPath, false); string expected = MsiSensorCommon.Json.Serialize(Descriptor()); if (File.ReadAllText(MsiSensorCommon.DescriptorPath) != expected) throw new IOException("Installed MSI descriptor differs.");
        string setup = Path.Combine(MsiSensorCommon.Folder, "INTERLINKED.MSI.Sensors.Setup.exe"); MsiSensorCommon.Protected(setup, false);
        if (MsiSensorCommon.HashFile(setup) != MsiSensorCommon.HashFile(typeof(MsiSensorSetup).Assembly.Location)) throw new IOException("Retained MSI setup differs.");
    }
    private static Dictionary<string, object> InstallResult(dynamic task) {
        TaskState state = ReadTaskState(task.Definition); ValidateTaskState(state); string sddl = (string)task.GetSecurityDescriptor(7); ValidateTaskSecurity(sddl); ValidateInstalled();
        return new Dictionary<string, object> {
            { "format", "interlinked-msi-sensor-setup-result-v1" }, { "status", "INSTALLED" }, { "observedAt", DateTime.UtcNow.ToString("o") }, { "machine", "MSI" }, { "userSid", MsiSensorPins.UserSid },
            { "setupSha256", MsiSensorCommon.HashFile(typeof(MsiSensorSetup).Assembly.Location) }, { "publisherSha256", MsiSensorPins.PublisherHash }, { "vendorHashes", new Dictionary<string, object> { { "HWINFO.exe", MsiSensorPins.ExeHash }, { "HWiNFO64.dll", MsiSensorPins.DllHash } } },
            { "descriptorSha256", MsiSensorCommon.HashFile(MsiSensorCommon.DescriptorPath) }, { "protectedAclVerified", true }, { "payloadFolderOwner", Directory.GetAccessControl(MsiSensorCommon.Folder).GetOwner(typeof(SecurityIdentifier)).Value },
            { "telemetryFolderOwner", Directory.GetAccessControl(MsiSensorCommon.DataFolder).GetOwner(typeof(SecurityIdentifier)).Value }, { "taskSddl", sddl }, { "taskState", (int)task.State },
            { "taskDefinition", new Dictionary<string, object> { { "actionPath", state.ActionPath }, { "arguments", state.Arguments }, { "workingDirectory", state.WorkingDirectory }, { "principalSid", MsiSensorCommon.AccountSid(state.PrincipalSid) }, { "triggerSid", MsiSensorCommon.AccountSid(state.TriggerSid) }, { "logonType", state.LogonType }, { "runLevel", state.RunLevel }, { "restartCount", state.RestartCount }, { "restartInterval", state.RestartInterval }, { "multipleInstances", state.MultipleInstances } } },
            { "liveCpuMappingVerified", false }, { "unrelatedTasksChanged", false }, { "appOrNodeElevated", false }
        };
    }
    private static void PrintResult(Dictionary<string, object> result) { try { string json = MsiSensorCommon.Json.Serialize(result); if (json.Length <= 16384) Console.WriteLine(json); } catch { } }
    [STAThread] private static int Main(string[] args) {
        bool installedTask = false; dynamic ownedTask = null; bool cliInstall = args.Length == 1 && args[0] == "--install";
        try {
            if (args.Length > 1 || (args.Length == 1 && args[0] != "--disable" && !cliInstall) || !MsiSensorCommon.IdentityMatches(Environment.MachineName, WindowsIdentity.GetCurrent().User.Value) || !MsiSensorCommon.Administrator()) throw new IOException("Run this fixed MSI package as the reviewed MSI user with Windows administrator approval.");
            dynamic scheduler = Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service")); scheduler.Connect(); dynamic root = scheduler.GetFolder("\\"); dynamic existing = null;
            try { existing = root.GetTask(MsiSensorCommon.TaskName); } catch (Exception ex) { if (!TaskMissing(ex)) throw; }
            if (args.Length == 1 && !cliInstall) {
                if (existing == null) throw new IOException("The owned sensor task is absent."); ValidateInstalled(); ValidateTaskState(ReadTaskState(existing.Definition)); ValidateTaskSecurity((string)existing.GetSecurityDescriptor(7));
                existing.Enabled = false; if ((int)existing.State == 4 || (int)existing.State == 2) existing.Stop(0);
                MessageBox.Show("MSI sensor telemetry is disabled. Its reviewed files are retained; TRCC and other tasks were preserved.", "INTERLINKED MSI Sensors"); return 0;
            }
            if (existing != null || Directory.Exists(MsiSensorCommon.Folder) || Directory.Exists(MsiSensorCommon.DataFolder)) throw new IOException("Existing MSI sensor installation was preserved. A separately reviewed migration is required.");
            // Validate all existing ancestors before creating anything. Never repair
            // another application's permissions or adopt an unprotected parent.
            MsiSensorCommon.Ordinary(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), true); MsiSensorCommon.Ordinary(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), true);
            if (Directory.Exists(MsiSensorCommon.DataParent)) MsiSensorCommon.Protected(MsiSensorCommon.DataParent, true);
            byte[] publisher = Payload("publisher", MsiSensorPins.PublisherHash), exe = Payload("vendor-exe", MsiSensorPins.ExeHash), dll = Payload("vendor-dll", MsiSensorPins.DllHash);
            foreach (string name in new[] { "TRCC", "HWINFO", "HWiNFO64", "LibreHardwareMonitor" }) { var list = System.Diagnostics.Process.GetProcessesByName(name); bool conflict = list.Length != 0; foreach (var process in list) process.Dispose(); if (conflict) throw new IOException("An existing sensor publisher is active; it was preserved. Close it explicitly before setup."); }
            NewDirectory(MsiSensorCommon.Folder); NewDirectory(MsiSensorCommon.Vendor);
            if (!Directory.Exists(MsiSensorCommon.DataParent)) NewDirectory(MsiSensorCommon.DataParent); NewDirectory(MsiSensorCommon.DataFolder);
            WriteOnce(MsiSensorCommon.PublisherPath, publisher); WriteOnce(Path.Combine(MsiSensorCommon.Vendor, "HWINFO.exe"), exe); WriteOnce(Path.Combine(MsiSensorCommon.Vendor, "HWiNFO64.dll"), dll);
            string setupSelf = typeof(MsiSensorSetup).Assembly.Location; MsiSensorCommon.Ordinary(setupSelf, false);
            if (new FileInfo(setupSelf).Length > 8388608) throw new IOException("Setup exceeds bounds.");
            WriteOnce(Path.Combine(MsiSensorCommon.Folder, "INTERLINKED.MSI.Sensors.Setup.exe"), File.ReadAllBytes(setupSelf));
            WriteOnce(MsiSensorCommon.DescriptorPath, new UTF8Encoding(false).GetBytes(MsiSensorCommon.Json.Serialize(Descriptor()))); ValidateInstalled();
            dynamic task = scheduler.NewTask(0); task.RegistrationInfo.Description = "Reviewed MSI-only CPU package sensor telemetry. No project, USB or model controls.";
            task.Principal.UserId = MsiSensorPins.UserSid; task.Principal.LogonType = 3; task.Principal.RunLevel = 1;
            dynamic trigger = task.Triggers.Create(9); trigger.UserId = MsiSensorPins.UserSid; trigger.Enabled = true;
            dynamic action = task.Actions.Create(0); action.Path = MsiSensorCommon.PublisherPath; action.Arguments = ""; action.WorkingDirectory = MsiSensorCommon.Folder;
            task.Settings.Enabled = false; task.Settings.MultipleInstances = 2; task.Settings.RestartCount = 3; task.Settings.RestartInterval = "PT1M"; task.Settings.ExecutionTimeLimit = "PT0S"; task.Settings.DisallowStartIfOnBatteries = false; task.Settings.StopIfGoingOnBatteries = false;
            // TASK_CREATE | TASK_DONT_ADD_PRINCIPAL_ACE: never replace a task or
            // let registration append a principal ACE. Remain disabled until the
            // explicit protected descriptor and all fixed fields read back.
            ownedTask = root.RegisterTaskDefinition(MsiSensorCommon.TaskName, task, 0x12, MsiSensorPins.UserSid, null, 3, TaskSddl); installedTask = true;
            ownedTask.SetSecurityDescriptor(TaskSddl, 0x10);
            ValidateTaskState(ReadTaskState(ownedTask.Definition)); ValidateTaskSecurity((string)ownedTask.GetSecurityDescriptor(7)); ownedTask.Enabled = true;
            ownedTask.SetSecurityDescriptor(TaskSddl, 0x10); ValidateTaskSecurity((string)ownedTask.GetSecurityDescriptor(7)); ownedTask.Run(null);
            var result = InstallResult(ownedTask); string resultJson = MsiSensorCommon.Json.Serialize(result); if (resultJson.Length > 16384) throw new IOException("Setup receipt exceeds bounds.");
            WriteOnce(Path.Combine(MsiSensorCommon.DataFolder, "setup-result.json"), new UTF8Encoding(false).GetBytes(resultJson));
            if (cliInstall) PrintResult(result); else MessageBox.Show("MSI sensor telemetry is installed and starting. Fresh verified readings may take up to one minute. Missing or unverified readings remain unavailable.", "INTERLINKED MSI Sensors"); return 0;
        } catch (Exception ex) {
            // Only a task created by this invocation may be disabled on failure.
            if (installedTask && ownedTask != null) try { ownedTask.Enabled = false; ownedTask.Stop(0); } catch { }
            if (cliInstall) PrintResult(new Dictionary<string, object> { { "format", "interlinked-msi-sensor-setup-result-v1" }, { "status", "FAILED" }, { "observedAt", DateTime.UtcNow.ToString("o") }, { "errorType", ex.GetType().Name }, { "message", ex.Message.Length > 512 ? ex.Message.Substring(0, 512) : ex.Message }, { "createdTaskDisabled", installedTask }, { "liveCpuMappingVerified", false } });
            else MessageBox.Show(ex.Message + "\nExisting publishers and unrelated tasks were preserved. Retained installation files require review before retry.", "INTERLINKED MSI Sensors", MessageBoxButtons.OK, MessageBoxIcon.Error); return 2;
        }
    }
}
