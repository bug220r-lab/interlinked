// Desktop lifecycle for MASTER. The Python service retains all workflow locks.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace InterlinkedDesktop
{
    internal sealed class MasterTray : IDisposable
    {
        private readonly NotifyIcon icon;
        private readonly Timer timer;
        private readonly Func<string, object, Task<HttpResponseMessage>> api;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly bool demo;
        private bool polling, disposed;
        internal bool BackgroundEnabled { get; private set; }
        internal string StartupError { get; private set; }
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunName = "INTERLINKED MASTER";

        internal MasterTray(Form window, bool synthetic, bool background,
            Func<string, object, Task<HttpResponseMessage>> call,
            Action activate, Func<Task<bool>> quit)
        {
            demo = synthetic; api = call; BackgroundEnabled = background;
            var menu = new ContextMenuStrip();
            menu.Items.Add("Open INTERLINKED", null, delegate { activate(); });
            menu.Items.Add("Disconnect cooler", null, async delegate
            {
                try { using (var response = await api("/api/master/display", new { connect = false })) { } }
                catch (Exception) { icon.ShowBalloonTip(2500, "INTERLINKED MASTER", "The display service is unavailable. Project work remains protected.", ToolTipIcon.Warning); }
            });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Exit INTERLINKED", null, async delegate { await quit(); });
            icon = new NotifyIcon { Icon = window.Icon, Text = "INTERLINKED MASTER", ContextMenuStrip = menu, Visible = !synthetic || background };
            icon.DoubleClick += delegate { activate(); };
            timer = new Timer { Interval = 3000 };
            timer.Tick += async delegate { await Poll(); };
        }

        internal void Start() { timer.Start(); }
        private async Task Poll()
        {
            if (polling || disposed) return;
            polling = true;
            try
            {
                using (var response = await api("/api/master/state", null))
                {
                    if (!response.IsSuccessStatusCode) return;
                    var state = json.Deserialize<Dictionary<string, object>>(await response.Content.ReadAsStringAsync());
                    object raw;
                    if (state == null || !state.TryGetValue("settings", out raw)) return;
                    var settings = raw as Dictionary<string, object>;
                    if (settings == null) return;
                    if (!demo && settings.TryGetValue("background", out raw) && raw is bool)
                        BackgroundEnabled = (bool)raw;
                    icon.Visible = !demo || BackgroundEnabled;
                    if (!demo && settings.TryGetValue("startAtLogin", out raw) && raw is bool)
                        ApplyStartup((bool)raw);
                }
            }
            catch (Exception) { /* Observation failures never stop a workflow. */ }
            finally { polling = false; }
        }

        private void ApplyStartup(bool enabled)
        {
            string executable = Path.GetFullPath(Application.ExecutablePath);
            string expected = @"C:\Users\Jude\Documents\MSI-Project-Library\INTERLINKED-Desktop\INTERLINKED.exe";
            if (!String.Equals(executable, expected, StringComparison.OrdinalIgnoreCase))
            { StartupError = "Startup is available only in the reviewed installation."; return; }
            string command = "\"" + executable + "\" --background";
            try
            {
                using (var key = Registry.CurrentUser.CreateSubKey(RunKey))
                {
                    object current = key.GetValue(RunName);
                    if (current != null && !String.Equals(Convert.ToString(current), command, StringComparison.Ordinal))
                    { StartupError = "An existing startup entry differs; it was preserved."; return; }
                    if (enabled && current == null) key.SetValue(RunName, command, RegistryValueKind.String);
                    else if (!enabled && current != null) key.DeleteValue(RunName, false);
                    StartupError = null;
                }
            }
            catch (Exception) { StartupError = "Windows did not allow the startup preference to be applied."; }
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true; timer.Stop(); timer.Dispose(); icon.Visible = false;
            icon.ContextMenuStrip.Dispose(); icon.Dispose();
        }
    }
}
