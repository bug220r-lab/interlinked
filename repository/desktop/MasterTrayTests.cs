using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;
using InterlinkedDesktop;

internal static class MasterTrayTests
{
    private static readonly List<string> checks = new List<string>();
    private static void Check(bool value, string name) { if (!value) throw new Exception(name); checks.Add(name); }
    private static string StartupValue() { using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", false)) return key == null ? null : Convert.ToString(key.GetValue("INTERLINKED MASTER")); }
    private static Task Poll(MasterTray tray) { return (Task)typeof(MasterTray).GetMethod("Poll", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(tray, null); }
    private static void Pump(Task task) { DateTime deadline = DateTime.UtcNow.AddSeconds(6); while (!task.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(5); } if (!task.IsCompleted) throw new Exception("Synthetic callback did not finish."); task.GetAwaiter().GetResult(); }
    [STAThread]
    public static int Main()
    {
        try
        {
            string before = StartupValue(); int calls = 0, activation = 0, exits = 0, disconnects = 0;
            bool fail = false; HttpStatusCode code = HttpStatusCode.OK;
            Func<string, object, Task<HttpResponseMessage>> api = (route, body) =>
            {
                calls++;
                if (route == "/api/master/display") { disconnects++; return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)); }
                if (fail) { var tcs = new TaskCompletionSource<HttpResponseMessage>(); tcs.SetException(new HttpRequestException("Synthetic unavailable source")); return tcs.Task; }
                return Task.FromResult(new HttpResponseMessage(code) { Content = new StringContent("{\"settings\":{\"background\":true,\"startAtLogin\":true}}") });
            };
            using (var form = new Form())
            {
                var tray = new MasterTray(form, true, false, api, () => activation++, () => { exits++; return Task.FromResult(false); });
                Check(!tray.BackgroundEnabled && calls == 0, "Creating a synthetic tray makes no project or status request");
                Pump(Poll(tray));
                Check(calls == 1 && !tray.BackgroundEnabled, "A demo status snapshot cannot enable production background preferences");
                Check(StartupValue() == before, "Demo startup preferences never modify the real login entry");
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
                fail = false;
                typeof(MasterTray).GetMethod("ApplyStartup", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(tray, new object[] { true });
                Check(tray.StartupError != null && StartupValue() == before, "An unreviewed test executable cannot install a production startup entry");
                int previous = calls; tray.Dispose(); tray.Dispose(); Pump(Poll(tray));
                Check(calls == previous, "Disposed tray is idempotent and stops status requests");
            }
            Check(StartupValue() == before, "All synthetic lifecycle checks preserve the existing login setting");
            Console.WriteLine(new JavaScriptSerializer().Serialize(new { status = "PASS", count = checks.Count, checks = checks, scope = "Synthetic tray callbacks and status responses; no project operations, USB, or production registry writes" }));
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
