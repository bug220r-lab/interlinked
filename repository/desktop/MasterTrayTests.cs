using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using InterlinkedDesktop;

internal static class MasterTrayTests
{
    private static readonly List<string> checks = new List<string>();
    private static void Check(bool value, string name) { if (!value) throw new Exception(name); checks.Add(name); }
    private static Task Poll(MasterTray tray) { return (Task)typeof(MasterTray).GetMethod("Poll", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(tray, null); }
    private static void Pump(Task task) { DateTime deadline = DateTime.UtcNow.AddSeconds(6); while (!task.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(5); } if (!task.IsCompleted) throw new Exception("Synthetic callback did not finish."); task.GetAwaiter().GetResult(); }
    [STAThread]
    public static int Main()
    {
        try
        {
            int calls = 0, activation = 0, exits = 0, disconnects = 0, startupCalls = 0;
            bool simulatedStartup = false;
            string snapshot = "{\"settingsValid\":true,\"lifecyclePreferencesKnown\":true,\"settings\":{\"background\":true,\"startAtLogin\":true}}";
            bool fail = false; HttpStatusCode code = HttpStatusCode.OK;
            Func<string, object, Task<HttpResponseMessage>> api = (route, body) =>
            {
                calls++;
                if (route == "/api/master/display") { disconnects++; return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)); }
                if (fail) { var tcs = new TaskCompletionSource<HttpResponseMessage>(); tcs.SetException(new HttpRequestException("Synthetic unavailable source")); return tcs.Task; }
                return Task.FromResult(new HttpResponseMessage(code) { Content = new StringContent(snapshot) });
            };
            using (var form = new Form())
            {
                Action<bool> startup = enabled => { startupCalls++; simulatedStartup = enabled; };
                var tray = new MasterTray(form, true, false, api, () => activation++, () => { exits++; return Task.FromResult(false); }, startup);
                Check(!tray.BackgroundEnabled && calls == 0, "Creating a synthetic tray makes no project or status request");
                Pump(Poll(tray));
                Check(calls == 1 && !tray.BackgroundEnabled, "A demo status snapshot cannot enable production background preferences");
                Check(startupCalls == 0, "Demo startup preferences never reach the startup writer");
                var icon = (NotifyIcon)typeof(MasterTray).GetField("icon", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(tray);
                Check(!icon.Visible, "A non-background synthetic host leaves no visible tray icon");
                icon.ContextMenuStrip.Items[0].PerformClick();
                Check(activation == 1, "Open INTERLINKED dispatches only the supplied activation callback");
                icon.ContextMenuStrip.Items[1].PerformClick();
                Check(disconnects == 1 && exits == 0, "Disconnect cooler does not request application shutdown");
                icon.ContextMenuStrip.Items[3].PerformClick();
                Check(exits == 1 && !form.IsDisposed, "Refused guarded exit does not close the owner form");
                code = HttpStatusCode.ServiceUnavailable; Pump(Poll(tray));
                Check(!tray.BackgroundEnabled, "Unavailable status response preserves the current lifecycle preference");
                fail = true; Pump(Poll(tray));
                Check(!form.IsDisposed && !tray.BackgroundEnabled, "Status transport failure cannot close the host or change its background state");
                fail = false; code = HttpStatusCode.OK;
                int previous = calls; tray.Dispose(); tray.Dispose(); Pump(Poll(tray));
                Check(calls == previous, "Disposed tray is idempotent and stops status requests");
                using (var production = new MasterTray(form, false, false, api, () => activation++, () => Task.FromResult(false), startup))
                {
                    Pump(Poll(production));
                    Check(startupCalls == 1 && simulatedStartup && production.BackgroundEnabled, "Valid known preferences reach only the simulated lifecycle writer");
                    snapshot = "{\"settingsValid\":false,\"lifecyclePreferencesKnown\":false,\"settings\":{\"background\":false,\"startAtLogin\":false}}";
                    Pump(Poll(production));
                    Check(startupCalls == 1 && simulatedStartup && production.BackgroundEnabled, "Corrupt configuration cannot apply false fallback preferences");
                    snapshot = "{\"settingsValid\":true,\"lifecyclePreferencesKnown\":false,\"settings\":{\"background\":false,\"startAtLogin\":false}}";
                    Pump(Poll(production));
                    Check(startupCalls == 1 && production.BackgroundEnabled, "Recovery with unknown lifecycle preferences preserves the current tray state");
                    snapshot = "{\"settings\":{\"background\":false,\"startAtLogin\":false}}";
                    Pump(Poll(production));
                    Check(startupCalls == 1 && production.BackgroundEnabled, "Missing validity metadata is not actionable startup authority");
                    snapshot = "{\"settingsValid\":true,\"lifecyclePreferencesKnown\":true,\"settings\":{\"background\":false,\"startAtLogin\":false}}";
                    Pump(Poll(production));
                    Check(startupCalls == 2 && !simulatedStartup && !production.BackgroundEnabled, "Explicit valid preferences can disable simulated startup and background mode");
                }
            }
            Check(startupCalls == 2, "All lifecycle checks use the injected writer without opening the registry");
            Console.WriteLine(new JavaScriptSerializer().Serialize(new { status = "PASS", count = checks.Count, checks = checks, scope = "Synthetic tray callbacks and status responses; no project operations, USB, or production registry writes" }));
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
