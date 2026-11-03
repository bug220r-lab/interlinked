// Pure/refusal fixtures only. Never invoke the privileged setup entry point.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class MsiSensorFixture
{
    private static int checks;
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); checks++; }
    private static void Refused(Action action, string message) { bool refused = false; try { action(); } catch { refused = true; } Check(refused, message); }
    private static string Sample(string group, string key, object value, string type) {
        return "HWi32_GetNumberOfDetectedSensors:\n1\n" + MsiSensorCommon.Json.Serialize(new Dictionary<string, object> { { group, new object[] { new Dictionary<string, object> { { key, new Dictionary<string, object> { { "value", value }, { "type", type } } } } } } });
    }
    private static MsiSensorSetup.TaskState ValidTask() { return new MsiSensorSetup.TaskState {
        ActionCount = 1, ActionType = 0, ActionPath = MsiSensorCommon.PublisherPath, Arguments = "", WorkingDirectory = MsiSensorCommon.Folder, PrincipalSid = MsiSensorPins.UserSid,
        LogonType = 3, RunLevel = 1, TriggerCount = 1, TriggerType = 9, TriggerEnabled = true, TriggerSid = MsiSensorPins.UserSid,
        MultipleInstances = 2, RestartCount = 3, RestartInterval = "PT1M", ExecutionTimeLimit = "PT0S", DisallowBatteries = false, StopOnBatteries = false }; }
    [STAThread] private static int Main() {
        Check(MsiSensorCommon.IdentityMatches("MSI", MsiSensorPins.UserSid), "MSI identity rejected");
        Check(!MsiSensorCommon.IdentityMatches("THIS-MACHINE", MsiSensorPins.UserSid), "Foreign computer accepted");
        Check(!MsiSensorCommon.IdentityMatches("MSI", "S-1-5-18"), "Foreign account accepted");
        string cpu = "CPU [#0]: Intel Core i7-10750H: DTS"; DateTime now = DateTime.UtcNow;
        var sample = MsiSensorCommon.ParseCpu(Sample(cpu, "CPU Package", "67.25", "temperature"), now);
        Check((double)sample["value"] == 67.25 && (string)sample["status"] == "verified" && (string)sample["source"] == "reviewed-sensor-publisher", "Verified CPU package did not parse");
        Check((double)MsiSensorCommon.ParseCpu(Sample("cpu [#0]: intel core i7-10750h: dts", "CPU Package", "66.5", "temperature"), now)["value"] == 66.5, "Observed MSI lowercase vendor group rejected");
        foreach (object invalid in new object[] { -1, 150, "NaN", "Infinity", true, "bad" }) Check(MsiSensorCommon.ParseCpu(Sample(cpu, "CPU Package", invalid, "temperature"), now)["value"] == null, "Invalid temperature accepted");
        foreach (string group in new[] { "CPU [#0]: AMD Ryzen: Enhanced", "CPU [#1]: Intel Core i7-10750H: DTS", "ACPI", "CPU [#0]: Intel Core i9-14900H: DTS", "CPU [#0]: Intel Core i7-10750H: Enhanced" }) Check(MsiSensorCommon.ParseCpu(Sample(group, "CPU Package", 65, "temperature"), now)["value"] == null, "Wrong CPU group accepted");
        Check(MsiSensorCommon.ParseCpu(Sample(cpu, "Core 0", 65, "temperature"), now)["value"] == null, "Core estimate substituted");
        Check(MsiSensorCommon.ParseCpu(Sample(cpu, "CPU Package", 65, "power"), now)["value"] == null, "Power substituted as temperature");
        Refused(delegate { MsiSensorCommon.ParseCpu("{}", now); }, "Unrecognized map accepted");
        Check(MsiSensorCommon.SampleFresh(now, now.AddSeconds(5)), "Five-second boundary rejected");
        Check(!MsiSensorCommon.SampleFresh(now, now.AddSeconds(5.001)), "Frozen sample accepted");
        Check(!MsiSensorCommon.SampleFresh(now, now.AddSeconds(-1)), "Future sample accepted");
        MsiSensorCommon.ValidateSecurity(MsiSensorCommon.DirectoryAcl()); checks++;
        var unsafeAcl = MsiSensorCommon.DirectoryAcl(); unsafeAcl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"), FileSystemRights.Modify, AccessControlType.Allow));
        Refused(delegate { MsiSensorCommon.ValidateSecurity(unsafeAcl); }, "User-writable sensor folder accepted");
        var inherited = MsiSensorCommon.DirectoryAcl(); inherited.SetAccessRuleProtection(false, true); Refused(delegate { MsiSensorCommon.ValidateSecurity(inherited); }, "Inherited sensor security accepted");
        MsiSensorSetup.ValidateTaskState(ValidTask()); checks++;
        var cases = new Dictionary<string, object> { { "ActionCount", 2 }, { "ActionPath", @"C:\foreign.exe" }, { "Arguments", "--foreign" }, { "WorkingDirectory", @"C:\" }, { "PrincipalSid", "S-1-5-18" }, { "TriggerSid", "S-1-5-18" }, { "LogonType", 1 }, { "RunLevel", 0 }, { "TriggerCount", 2 }, { "TriggerType", 1 }, { "TriggerEnabled", false }, { "MultipleInstances", 0 }, { "RestartCount", 99 }, { "RestartInterval", "PT1S" }, { "ExecutionTimeLimit", "PT1H" }, { "DisallowBatteries", true }, { "StopOnBatteries", true } };
        foreach (var pair in cases) { var changed = ValidTask(); typeof(MsiSensorSetup.TaskState).GetField(pair.Key, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(changed, pair.Value); Refused(delegate { MsiSensorSetup.ValidateTaskState(changed); }, "Changed task field accepted: " + pair.Key); }
        string sddl = "O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)"; MsiSensorSetup.ValidateTaskSecurity(sddl); checks++;
        foreach (string bad in new[] { sddl.Replace("D:P", "D:"), sddl.Replace("O:BA", "O:" + MsiSensorPins.UserSid), sddl + "(A;;GA;;;" + MsiSensorPins.UserSid + ")", sddl.Replace("GA;;;BA", "GR;;;BA") }) Refused(delegate { MsiSensorSetup.ValidateTaskSecurity(bad); }, "Unsafe task ACL accepted");
        Console.WriteLine("{\"ok\":true,\"checks\":" + checks + ",\"privilegedSetupRun\":false,\"sensorsStarted\":false,\"hardwareMappingVerified\":false}"); return 0;
    }
}
