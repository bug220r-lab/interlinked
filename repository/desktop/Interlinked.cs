// INTERLINKED Windows host. The reviewed Python service owns every workflow lock.
// This host never terminates that service or any MAGIX process.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace InterlinkedDesktop
{
    internal sealed class Options
    {
        internal bool Demo, Smoke, Background;
        internal string App, Python, DemoState, Receipt, Kit, Identity, MasterWorker, MasterViolet;
        internal static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a == "--demo") o.Demo = true;
                else if (a == "--background") o.Background = true;
                else if (a == "--smoke-test") o.Smoke = true;
                else if (a == "--app" || a == "--python" || a == "--demo-state" || a == "--receipt" || a == "--master-worker" || a == "--master-violet")
                {
                    if (++i >= args.Length) throw new ArgumentException("Missing option value.");
                    if (a == "--app") o.App = Path.GetFullPath(args[i]);
                    if (a == "--python") o.Python = Path.GetFullPath(args[i]);
                    if (a == "--demo-state") o.DemoState = Path.GetFullPath(args[i]);
                    if (a == "--receipt") o.Receipt = Path.GetFullPath(args[i]);
                    if (a == "--master-worker") o.MasterWorker = Path.GetFullPath(args[i]);
                    if (a == "--master-violet") o.MasterViolet = Path.GetFullPath(args[i]);
                }
                else throw new ArgumentException("Unknown desktop option.");
            }
            string exeFolder = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            o.Kit = Directory.GetParent(exeFolder).FullName;
            if (o.Demo)
            {
                if (o.App == null || o.Python == null || o.DemoState == null)
                    throw new ArgumentException("Demo requires --app, --python and a fresh --demo-state.");
                o.Identity = o.DemoState;
            }
            else
            {
                if (o.App != null || o.Python != null || o.DemoState != null || o.Smoke || o.Receipt != null || o.MasterWorker != null || o.MasterViolet != null)
                    throw new ArgumentException("Custom paths and tests are available only with --demo.");
                string canonical = @"C:\Users\Jude\Documents\MSI-Project-Library";
                if (!String.Equals(o.Kit, canonical, StringComparison.OrdinalIgnoreCase) ||
                    !String.Equals(Environment.MachineName, "THIS-MACHINE", StringComparison.OrdinalIgnoreCase) ||
                    !String.Equals(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"C:\Users\Jude", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("Run the installed INTERLINKED application on THIS-MACHINE as Jude.");
                o.App = Path.Combine(o.Kit, @"INTERLINKED-App\interlinked_gui.py");
                o.Python = Path.Combine(o.Kit, @"Project-Library-Client\Runtime\python.exe");
                o.Identity = o.Kit;
            }
            if (o.Smoke && o.Receipt == null) throw new ArgumentException("Smoke test requires --receipt.");
            if (!File.Exists(o.App) || !File.Exists(o.Python)) throw new ArgumentException("The application or reviewed Python runtime is missing.");
            return o;
        }
    }

    internal sealed class WindowPreferences
    {
        internal readonly string PathName;
        private readonly string owner;
        private readonly JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength = 8192, RecursionLimit = 8 };
        private const string Format = "interlinked-window-preferences-v1";
        internal WindowPreferences(string path, string identity) { PathName = Path.GetFullPath(path); owner = identity; }

        private static void Ordinary(string path)
        {
            string current = Path.GetFullPath(path);
            while (current != null)
            {
                try
                {
                    var attrs = File.GetAttributes(current);
                    if (((long)attrs & 0x441400) != 0) throw new IOException("Window preference link/cloud path refused.");
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                current = Path.GetDirectoryName(current);
            }
        }
        private Dictionary<string, object> Read()
        {
            Ordinary(PathName);
            using (var stream = new FileStream(PathName, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length > 8192) throw new IOException("Window preferences are too large.");
                using (var reader = new StreamReader(stream, new UTF8Encoding(false, true)))
                {
                    var value = serializer.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    if (value == null || value.Count != 7 || !value.ContainsKey("format") || Convert.ToString(value["format"]) != Format || !value.ContainsKey("owner") || Convert.ToString(value["owner"]) != owner)
                        throw new IOException("Window preference ownership differs.");
                    foreach (string field in new[] { "x", "y", "width", "height" })
                        if (!value.ContainsKey(field) || !(value[field] is int)) throw new IOException("Invalid window preference bounds.");
                    if (!value.ContainsKey("maximized") || !(value["maximized"] is bool)) throw new IOException("Invalid window preference state.");
                    int x = (int)value["x"], y = (int)value["y"], width = (int)value["width"], height = (int)value["height"];
                    if (x < -200000 || x > 200000 || y < -200000 || y > 200000 || width < 850 || width > 16000 || height < 600 || height > 16000)
                        throw new IOException("Window preference bounds are outside supported limits.");
                    return value;
                }
            }
        }
        internal static Rectangle VisibleBounds(Rectangle bounds)
        {
            var area = Screen.FromRectangle(bounds).WorkingArea;
            int width = Math.Min(Math.Max(bounds.Width, 850), area.Width);
            int height = Math.Min(Math.Max(bounds.Height, 600), area.Height);
            int x = Math.Max(area.Left, Math.Min(bounds.X, area.Right - width));
            int y = Math.Max(area.Top, Math.Min(bounds.Y, area.Bottom - height));
            return new Rectangle(x, y, width, height);
        }
        internal bool TryLoad(out Rectangle bounds, out bool maximized)
        {
            bounds = Rectangle.Empty; maximized = false;
            try
            {
                var value = Read();
                bounds = VisibleBounds(new Rectangle((int)value["x"], (int)value["y"], (int)value["width"], (int)value["height"]));
                maximized = (bool)value["maximized"];
                return bounds.Width >= 850 && bounds.Height >= 600;
            }
            catch (Exception) { return false; } // Preferences never block project work.
        }
        internal bool Save(Rectangle bounds, bool maximized)
        {
            try
            {
                Ordinary(PathName);
                if (File.Exists(PathName)) Read(); // Preserve corrupt or differently owned preference files.
                string parent = Path.GetDirectoryName(PathName); Ordinary(parent); Directory.CreateDirectory(parent); Ordinary(parent);
                bounds = VisibleBounds(bounds);
                if (bounds.Width < 850 || bounds.Height < 600) return false;
                string text = serializer.Serialize(new { format = Format, owner = owner, x = bounds.X, y = bounds.Y, width = bounds.Width, height = bounds.Height, maximized = maximized });
                string temporary = PathName + ".pending-" + Guid.NewGuid().ToString("N");
                byte[] bytes = new UTF8Encoding(false).GetBytes(text);
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                Ordinary(PathName);
                if (File.Exists(PathName)) { Read(); File.Replace(temporary, PathName, null); }
                else File.Move(temporary, PathName);
                Rectangle check; bool state;
                return TryLoad(out check, out state) && check == bounds && state == maximized;
            }
            catch (Exception) { return false; }
        }
    }

    internal static class Program
    {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)] private static extern int SetCurrentProcessExplicitAppUserModelID(string id);
        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                if (new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
                    throw new InvalidOperationException("Run INTERLINKED normally, without administrator mode.");
                var o = Options.Parse(args);
                string key;
                using (var sha = SHA256.Create()) key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(o.Identity.ToLowerInvariant()))).Replace("-", "").Substring(0, 24);
                SetCurrentProcessExplicitAppUserModelID("INTERLINKED.Desktop");
                bool first;
                using (var mutex = new Mutex(true, @"Local\INTERLINKED.Desktop." + key, out first))
                using (var activate = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\INTERLINKED.Activate." + key))
                {
                    if (!first) { activate.Set(); return 0; }
                    var form = new MainWindow(o, key);
                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        while (!form.IsDisposed)
                        {
                            bool signaled;
                            try { signaled = activate.WaitOne(1000); } catch (ObjectDisposedException) { break; }
                            if (signaled && form.IsHandleCreated)
                                try { form.BeginInvoke((Action)delegate { form.ActivateWindow(); }); } catch (InvalidOperationException) { }
                        }
                    });
                    Application.ThreadException += delegate { form.ShowFailure("The desktop interface encountered an error. Its service is still protected. Close MAGIX before using recovery if the interface cannot be restored."); };
                    Application.Run(form);
                    mutex.ReleaseMutex();
                    return form.ExitCode;
                }
            }
            catch (Exception e) { MessageBox.Show(e.Message, "INTERLINKED", MessageBoxButtons.OK, MessageBoxIcon.Warning); return 2; }
        }
    }

    internal sealed class MainWindow : Form
    {
        private readonly Options options;
        private readonly string profileKey;
        private readonly Label status = new Label();
        private readonly Button reopen = new Button();
        private readonly List<Form> audits = new List<Form>();
        private readonly List<WebView2> auditViews = new List<WebView2>();
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly HttpClient http;
        private readonly WindowPreferences windowPreferences;
        private WebView2 view;
        private Process service;
        private string origin, token;
        private bool allowClose, askingQuit, serviceEnded, ready, smokeRunning;
        private string startupError;
        private int blockedNavigation, auditWindows, completedDownloads, focusCount;
        private int activationRequests;
        private string profilePath;
        private MasterTray masterTray;
        private readonly Dictionary<string, object> checks = new Dictionary<string, object>();
        internal int ExitCode = 0;

        internal MainWindow(Options o, string key)
        {
            options = o; profileKey = key;
            Text = "INTERLINKED" + (o.Demo ? " — DEMO" : "");
            BackColor = Color.FromArgb(6, 14, 10); ForeColor = Color.FromArgb(124, 255, 158);
            Width = 1320; Height = 900; MinimumSize = new Size(850, 600); StartPosition = FormStartPosition.CenterScreen;
            windowPreferences = new WindowPreferences(o.Demo ? Path.Combine(o.DemoState, "window.json") : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"INTERLINKED\window.json"), o.Identity);
            Rectangle remembered; bool maximized;
            if (windowPreferences.TryLoad(out remembered, out maximized)) { StartPosition = FormStartPosition.Manual; Bounds = remembered; if (maximized) WindowState = FormWindowState.Maximized; }
            Icon = MakeIcon();
            status.Text = "Starting INTERLINKED…"; status.Dock = DockStyle.Fill; status.TextAlign = ContentAlignment.MiddleCenter;
            status.Font = new Font("Segoe UI", 14); Controls.Add(status);
            reopen.Text = "Reopen interface"; reopen.Dock = DockStyle.Bottom; reopen.Height = 44; reopen.Visible = false;
            reopen.Click += async delegate { if (origin != null && !serviceEnded) { reopen.Enabled = false; try { foreach (var child in audits.ToArray()) child.Close(); if (view != null) { Controls.Remove(view); view.Dispose(); } await CreateView(new Uri(origin + "/#" + token)); reopen.Visible = false; } catch (Exception) { ShowFailure("The interface could not be reopened. Its service remains protected. Try Reopen interface again."); } finally { reopen.Enabled = true; } } };
            Controls.Add(reopen);
            http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false }); http.Timeout = TimeSpan.FromSeconds(6);
            Shown += async delegate { if (options.Background) Hide(); await Start(); };
            Activated += delegate { focusCount++; };
            FormClosing += OnClosing;
            FormClosed += delegate
            {
                if (masterTray != null) masterTray.Dispose();
                if (service != null && (!serviceEnded || service.ExitCode != 0)) return;
                Rectangle normal = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                bool saved = windowPreferences.Save(normal, WindowState == FormWindowState.Maximized);
                if (options.Smoke) { checks["windowPreferencesSavedOnClose"] = saved; if (!saved) { checks["status"] = "FAIL"; ExitCode = 3; } WriteReceipt(); }
            };
        }

        private static Icon MakeIcon()
        {
            using (var bitmap = new Bitmap(64, 64))
            using (var g = Graphics.FromImage(bitmap))
            using (var pen = new Pen(Color.FromArgb(98, 255, 141), 5))
            {
                g.Clear(Color.FromArgb(6, 14, 10));
                g.DrawRectangle(pen, 5, 5, 53, 53); g.DrawLine(pen, 22, 16, 22, 48); g.DrawLine(pen, 40, 16, 40, 48);
                g.DrawLine(pen, 16, 16, 46, 16); g.DrawLine(pen, 16, 48, 46, 48);
                IntPtr handle = bitmap.GetHicon();
                try { return (Icon)Icon.FromHandle(handle).Clone(); } finally { DestroyIcon(handle); }
            }
        }
        [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr handle);
        private static string Quote(string s) { return "\"" + s.Replace("\"", "\\\"") + "\""; }

        private async Task Start()
        {
            try
            {
                // Detect the shared runtime before starting a workflow-owning service.
                CoreWebView2Environment.GetAvailableBrowserVersionString();
                Phase("runtimeAvailable");
                var start = new ProcessStartInfo(options.Python);
                start.Arguments = "-I -B -X utf8 " + Quote(options.App) + " --desktop --no-browser" +
                    (options.Demo ? " --demo --demo-state " + Quote(options.DemoState) : "") +
                    (options.MasterWorker != null ? " --master-worker " + Quote(options.MasterWorker) : "") +
                    (options.MasterViolet != null ? " --master-violet " + Quote(options.MasterViolet) : "");
                start.WorkingDirectory = Path.GetDirectoryName(options.App);
                start.UseShellExecute = false; start.CreateNoWindow = true; start.RedirectStandardOutput = true; start.RedirectStandardError = true;
                start.StandardOutputEncoding = Encoding.UTF8; start.StandardErrorEncoding = Encoding.UTF8;
                service = new Process { StartInfo = start, EnableRaisingEvents = true };
                service.Exited += delegate
                {
                    serviceEnded = true;
                    if (IsHandleCreated) try { BeginInvoke((Action)OnServiceExited); } catch (InvalidOperationException) { }
                };
                service.Start();
                Task stderr = DrainErrors();
                Task<string> first = service.StandardOutput.ReadLineAsync();
                if (await Task.WhenAny(first, Task.Delay(30000)) != first) throw new InvalidOperationException("The local service did not become ready. It has not been terminated.");
                string line = await first;
                if (line == null) { await stderr; throw new InvalidOperationException(startupError ?? "The local service could not start."); }
                var hello = json.Deserialize<Dictionary<string, object>>(line);
                if (Convert.ToString(hello["format"]) != "interlinked-desktop-startup-v1") throw new InvalidOperationException("Unexpected service startup response.");
                var uri = new Uri(Convert.ToString(hello["url"]));
                if (uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.IsDefaultPort || uri.AbsolutePath != "/" || uri.Query != "" || !Regex.IsMatch(uri.Fragment, "^#[A-Za-z0-9_-]{40,100}$"))
                    throw new InvalidOperationException("The service returned an invalid local address.");
                origin = uri.GetLeftPart(UriPartial.Authority); token = uri.Fragment.Substring(1);
                Phase("serviceReady");
                masterTray = new MasterTray(this, options.Demo, options.Background || !options.Demo,
                    Api, ActivateWindow, () => Quit(true));
                masterTray.Start();
                DrainOutput();
                profilePath = options.Demo ? options.DemoState + "-webview2" : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"INTERLINKED\WebView2\" + profileKey);
                await CreateView(uri);
            }
            catch (Exception e)
            {
                ExitCode = 2;
                string message = e is WebView2RuntimeNotFoundException ? "Microsoft WebView2 Runtime is required. Install the Evergreen Runtime from Microsoft's WebView2 download page, then reopen INTERLINKED." : e.Message;
                ShowFailure(message);
                if (options.Smoke) { checks["status"] = "FAIL"; checks["failure"] = message; WriteReceipt(); }
            }
            if (ExitCode != 0 && options.Smoke) { if (origin != null) await Quit(false); else { allowClose = service == null || serviceEnded; if (allowClose) Close(); } }
        }
        internal void ActivateWindow() { activationRequests++; WindowState = FormWindowState.Normal; Show(); Activate(); }
        private async Task CreateView(Uri uri)
        {
            var environment = await CoreWebView2Environment.CreateAsync(null, profilePath, null);
            Phase("environmentReady");
            view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor };
            Controls.Add(view); view.BringToFront();
            var privateOptions = environment.CreateCoreWebView2ControllerOptions(); privateOptions.IsInPrivateModeEnabled = true;
            await view.EnsureCoreWebView2Async(environment, privateOptions);
            Phase("controllerReady"); Configure(view, true);
            view.CoreWebView2.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (e.IsSuccess) { ready = true; if (options.Smoke && !smokeRunning) { smokeRunning = true; await Smoke(); } }
                else if (!ready && options.Smoke) { ExitCode = 3; checks["status"] = "FAIL"; checks["failure"] = "Initial navigation failed: " + e.WebErrorStatus; WriteReceipt(); await Quit(false); }
            };
            view.CoreWebView2.Navigate(uri.AbsoluteUri);
        }

        private async Task DrainErrors()
        {
            try
            {
                char[] buffer = new char[1024]; int count;
                while ((count = await service.StandardError.ReadAsync(buffer, 0, buffer.Length)) > 0)
                {
                    startupError = ((startupError ?? "") + new string(buffer, 0, count));
                    if (startupError.Length > 8000) startupError = startupError.Substring(startupError.Length - 8000);
                }
            }
            catch (IOException) { }
            finally { if (startupError != null && startupError.Length > 8000) startupError = startupError.Substring(startupError.Length - 8000); }
        }
        private async void DrainOutput()
        {
            try { char[] buffer = new char[1024]; while (await service.StandardOutput.ReadAsync(buffer, 0, buffer.Length) > 0) { } } catch (IOException) { }
        }
        private bool Local(string value, bool document)
        {
            Uri uri;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri)) return false;
            if (uri.GetLeftPart(UriPartial.Authority) != origin || uri.UserInfo != "") return false;
            return !document || uri.AbsolutePath == "/" || uri.AbsolutePath == "/audit";
        }
        private void Configure(WebView2 browser, bool main)
        {
            var core = browser.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false; core.Settings.AreHostObjectsAllowed = false; core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false; core.Settings.IsStatusBarEnabled = false;
            core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs e) { e.State = e.PermissionKind == CoreWebView2PermissionKind.MultipleAutomaticDownloads && Local(e.Uri, false) ? CoreWebView2PermissionState.Allow : CoreWebView2PermissionState.Deny; };
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { if (!Local(e.Uri, true)) { e.Cancel = true; blockedNavigation++; } };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += delegate(object sender, CoreWebView2WebResourceRequestedEventArgs e)
            {
                if (!Local(e.Request.Uri, false) && !e.Request.Uri.StartsWith("blob:" + origin + "/", StringComparison.Ordinal))
                    e.Response = core.Environment.CreateWebResourceResponse(new MemoryStream(), 403, "Blocked", "Content-Type: text/plain");
            };
            core.NewWindowRequested += async delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            {
                e.Handled = true;
                if (!Local(e.Uri, true) || new Uri(e.Uri).AbsolutePath != "/audit") { blockedNavigation++; return; }
                var deferral = e.GetDeferral();
                try
                {
                    var child = new Form { Text = "INTERLINKED — Media review", Width = 1200, Height = 850, Icon = Icon, StartPosition = FormStartPosition.CenterParent };
                    var web = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor };
                    var privateOptions = core.Environment.CreateCoreWebView2ControllerOptions(); privateOptions.IsInPrivateModeEnabled = true;
                    child.Controls.Add(web); await web.EnsureCoreWebView2Async(core.Environment, privateOptions); Configure(web, false);
                    e.NewWindow = web.CoreWebView2;
                    audits.Add(child); auditViews.Add(web); auditWindows++;
                    child.FormClosed += delegate { audits.Remove(child); auditViews.Remove(web); web.Dispose(); };
                    child.Show(this);
                }
                catch (Exception) { ShowFailure("Media review could not open. The main interface remains available."); }
                finally { deferral.Complete(); }
            };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e)
            {
                e.Handled = true;
                if (!e.DownloadOperation.Uri.StartsWith("blob:" + origin + "/", StringComparison.Ordinal)) { e.Cancel = true; return; }
                string name = Path.GetFileName(e.ResultFilePath);
                if (!Regex.IsMatch(name, "^INTERLINKED-missing-media-[a-z0-9-]+\\.(json|csv)$")) { e.Cancel = true; return; }
                if (options.Smoke) e.ResultFilePath = Path.Combine(Path.GetDirectoryName(options.Receipt), name);
                else
                {
                    using (var dialog = new SaveFileDialog { FileName = name, Filter = "Report files (*.json;*.csv)|*.json;*.csv", OverwritePrompt = true })
                    {
                        if (dialog.ShowDialog(this) != DialogResult.OK) { e.Cancel = true; return; }
                        e.ResultFilePath = dialog.FileName;
                    }
                }
                var download = e.DownloadOperation;
                download.StateChanged += delegate { if (download.State == CoreWebView2DownloadState.Completed) completedDownloads++; };
            };
            core.ProcessFailed += delegate { ShowFailure("The interface renderer stopped. Its local service remains running. Close MAGIX before using recovery if the interface cannot be restored."); };
        }

        internal void ShowFailure(string text)
        {
            status.Text = text; status.BringToFront();
            if (view != null) view.Visible = false;
            reopen.Visible = view != null && ready && !serviceEnded; reopen.BringToFront();
        }
        private void OnServiceExited()
        {
            if (!ready && !allowClose)
            {
                if (options.Smoke && ExitCode != 0) { allowClose = true; Close(); }
                return;
            }
            if (service.ExitCode != 0)
            {
                ExitCode = 3;
                if (!options.Smoke) { ShowFailure("The local service stopped unexpectedly. Close MAGIX and use the reviewed Recover workflow before continuing. Local work has not been saved or released automatically."); return; }
                checks["status"] = "FAIL";
            }
            if (options.Smoke) { checks["gracefulShutdown"] = service.ExitCode == 0; checks["serviceExited"] = true; WriteReceipt(); }
            allowClose = true; foreach (var child in audits.ToArray()) child.Close(); Close();
        }
        private async void OnClosing(object sender, FormClosingEventArgs e)
        {
            if (allowClose || serviceEnded || service == null) return;
            e.Cancel = true;
            if (masterTray != null && masterTray.BackgroundEnabled && !askingQuit && !options.Smoke) { Hide(); return; }
            if (askingQuit) return;
            if (origin == null) { MessageBox.Show(this, "INTERLINKED is still starting. Wait for startup to complete; the service has not been terminated.", "INTERLINKED"); return; }
            await Quit(true);
        }
        private async Task<HttpResponseMessage> Api(string path, object body)
        {
            var request = new HttpRequestMessage(body == null ? HttpMethod.Get : HttpMethod.Post, origin + path);
            request.Headers.Add("X-INTERLINKED-Token", token); request.Headers.Add("Origin", origin);
            if (body != null) request.Content = new StringContent(json.Serialize(body), Encoding.UTF8, "application/json");
            return await http.SendAsync(request);
        }
        private async Task<bool> Quit(bool show)
        {
            askingQuit = true;
            try
            {
                using (var response = await Api("/api/quit", new Dictionary<string, object>()))
                {
                    if (!response.IsSuccessStatusCode)
                    {
                        if (show) MessageBox.Show(this, "An operation or MAGIX session is still active. Save and close MAGIX, then confirm Done in INTERLINKED. Wait for the operation to finish before exiting.", "INTERLINKED — work protected", MessageBoxButtons.OK, MessageBoxIcon.Information);
                        return false;
                    }
                    allowClose = true;
                    // Closing follows the confirmed service exit, not a forced process stop.
                    if (serviceEnded) Close();
                    return true;
                }
            }
            catch (Exception)
            {
                if (show) MessageBox.Show(this, "INTERLINKED could not confirm a safe shutdown. Its service was left running. Close MAGIX before recovery if the connection cannot be restored.", "INTERLINKED", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            finally { askingQuit = false; }
        }
        private async Task<Dictionary<string, object>> State()
        {
            using (var r = await Api("/api/state", null)) { r.EnsureSuccessStatusCode(); return json.Deserialize<Dictionary<string, object>>(await r.Content.ReadAsStringAsync()); }
        }
        private async Task WaitFor(Func<Task<bool>> condition, string label)
        {
            for (int i = 0; i < 100; i++) { if (await condition()) return; await Task.Delay(100); }
            throw new InvalidOperationException("Timed out: " + label);
        }
        private async Task<bool> JsBool(string script) { return await view.ExecuteScriptAsync(script) == "true"; }
        private async Task Smoke()
        {
            try
            {
                await WaitFor(async delegate { return await JsBool("document.querySelectorAll('.project-item').length===6 && !!selected"); }, "mixed demo catalog");
                checks["catalogLoaded"] = true;
                int activated = activationRequests;
                var secondInfo = new ProcessStartInfo(Application.ExecutablePath, "--demo --app " + Quote(options.App) + " --python " + Quote(options.Python) + " --demo-state " + Quote(options.DemoState)) { UseShellExecute = false, CreateNoWindow = true };
                using (var second = Process.Start(secondInfo)) { await Task.Run(delegate { if (!second.WaitForExit(10000)) throw new InvalidOperationException("Second instance did not exit."); }); if (second.ExitCode != 0) throw new InvalidOperationException("Second instance failed."); }
                await WaitFor(delegate { return Task.FromResult(activationRequests > activated); }, "single-instance activation");
                checks["singleInstanceActivation"] = true;
                checks["localNavigationOnly"] = !Local("https://example.com", true) && !Local(origin + ".evil/", true) && !Local(origin + "/api/state", true);
                int blocked = blockedNavigation;
                view.CoreWebView2.Navigate("https://example.com/");
                await WaitFor(delegate { return Task.FromResult(blockedNavigation > blocked); }, "navigation rejection");
                checks["externalNavigationBlocked"] = true;
                await view.ExecuteScriptAsync("document.getElementById('audit-window').click()");
                await WaitFor(delegate { return Task.FromResult(auditWindows == 1); }, "audit window");
                await WaitFor(async delegate { return auditViews.Count == 1 && await auditViews[0].ExecuteScriptAsync("location.pathname==='/audit' && !!selected && document.getElementById('audit-panel').hidden===false") == "true"; }, "audit interface loaded");
                checks["auditWindow"] = true;
                await view.ExecuteScriptAsync("document.getElementById('missing-path').value='Synthetic missing clip.mp4'; document.getElementById('missing-note').value='Standalone WebView2 fixture'; document.getElementById('missing-report').click()");
                await WaitFor(async delegate { return await JsBool("document.querySelectorAll('.missing-record').length===1 && !notePending"); }, "report append");
                await view.ExecuteScriptAsync("document.getElementById('missing-json').click()");
                await WaitFor(delegate { return Task.FromResult(completedDownloads >= 1); }, "JSON download");
                await WaitFor(async delegate { return await JsBool("!notePending"); }, "export controls enabled");
                await view.ExecuteScriptAsync("document.getElementById('missing-csv').click()");
                await WaitFor(delegate { return Task.FromResult(completedDownloads >= 2); }, "CSV download");
                checks["reportDownloads"] = completedDownloads;
                await view.ExecuteScriptAsync("document.getElementById('open').click()");
                await WaitFor(async delegate { var s = await State(); return Convert.ToBoolean(s["busy"]); }, "active demo");
                checks["busyCloseRefused"] = !await Quit(false) && !service.HasExited;
                checks["busyCloseDoesNotWriteWindowPreferences"] = !File.Exists(windowPreferences.PathName);
                await WaitFor(async delegate { var s = await State(); var job = s["operation"] as Dictionary<string, object>; return job != null && Convert.ToBoolean(job["awaitingDone"]); }, "Done prompt");
                var beforeRecovery = await State();
                string operationId = Convert.ToString(((Dictionary<string, object>)beforeRecovery["operation"])["id"]);
                Phase("MSI jobs during active synthetic editing");
                await view.ExecuteScriptAsync("document.getElementById('jobs-view-tab').click()");
                await WaitFor(async delegate { return await JsBool("document.getElementById('msi-jobs-view').hidden===false && document.getElementById('library-shell').hidden===true && jobsSnapshot?.demo===true && jobsSnapshot.readOnly===true && document.getElementById('msi-jobs-demo').hidden===false && document.querySelectorAll('.msi-job-row').length===3"); }, "read-only synthetic MSI Jobs panel");
                checks["msiJobsPanelLoaded"] = true;
                await WaitFor(async delegate { return await JsBool("document.getElementById('msi-jobs-stream').value==='stdout' && document.getElementById('msi-jobs-log').textContent.includes('DEMO') && !jobsPollBusy"); }, "synthetic MSI stdout log");
                checks["msiJobsLogsRendered"] = true;
                await view.ExecuteScriptAsync("document.getElementById('msi-jobs-stream').value='stderr'; document.getElementById('msi-jobs-stream').dispatchEvent(new Event('change'))");
                await WaitFor(async delegate { return await JsBool("document.getElementById('msi-jobs-log').textContent==='' && document.getElementById('msi-jobs-log-note').textContent.includes('empty at the observation')"); }, "synthetic MSI stderr log");
                await view.ExecuteScriptAsync("document.getElementById('msi-jobs-stream').value='stdout'; document.getElementById('msi-jobs-stream').dispatchEvent(new Event('change'))");
                await WaitFor(async delegate { return await JsBool("document.getElementById('msi-jobs-log').textContent.includes('DEMO')"); }, "synthetic MSI stdout restored");
                checks["msiJobsLogStreamSwitching"] = true;
                await view.ExecuteScriptAsync("window.__interlinkedJobsObserved=jobsSnapshot.observedAt; document.getElementById('msi-jobs-refresh').click()");
                await WaitFor(async delegate { return await JsBool("jobsSnapshot?.demo===true && jobsSnapshot.readOnly===true && jobsSnapshot.observedAt!==window.__interlinkedJobsObserved && !jobsSnapshot.refreshing && !jobsRefreshPending && !jobsPollBusy && document.getElementById('msi-jobs-log').textContent.includes('DEMO')"); }, "explicit synthetic MSI refresh");
                var afterJobsRefresh = await State();
                checks["msiJobsRefreshPreservesSession"] = !service.HasExited && Convert.ToBoolean(afterJobsRefresh["busy"]) && Convert.ToString(((Dictionary<string, object>)afterJobsRefresh["operation"])["id"]) == operationId && Convert.ToBoolean(((Dictionary<string, object>)afterJobsRefresh["operation"])["awaitingDone"]);
                await view.ExecuteScriptAsync("document.getElementById('library-view-tab').click()");
                await WaitFor(async delegate { return await JsBool("document.getElementById('library-shell').hidden===false && document.getElementById('msi-jobs-view').hidden===true && state.busy && state.operation.awaitingDone"); }, "library return with protected synthetic session");
                var afterJobsReturn = await State();
                checks["msiJobsLibraryReturnPreservesSession"] = !service.HasExited && Convert.ToBoolean(afterJobsReturn["busy"]) && Convert.ToString(((Dictionary<string, object>)afterJobsReturn["operation"])["id"]) == operationId;
                int serviceId = service.Id;
                ShowFailure("Synthetic renderer recovery test. The demo session remains active."); reopen.PerformClick();
                await WaitFor(async delegate { return view != null && view.CoreWebView2 != null && !reopen.Enabled == false && await JsBool("!!selected && state.busy && state.operation.awaitingDone"); }, "renderer recovery");
                var afterRecovery = await State();
                checks["rendererRecoveryPreservesSession"] = service.Id == serviceId && !service.HasExited && Convert.ToBoolean(afterRecovery["busy"]) && Convert.ToString(((Dictionary<string, object>)afterRecovery["operation"])["id"]) == operationId;
                await WaitFor(async delegate { return await JsBool("!document.getElementById('done').disabled"); }, "Done enabled");
                await view.ExecuteScriptAsync("document.getElementById('done').click()");
                await WaitFor(async delegate { return !Convert.ToBoolean((await State())["busy"]); }, "Done completion");
                checks["doneFinishesSession"] = true;
                await view.ExecuteScriptAsync("document.getElementById('save').click()");
                await WaitFor(async delegate { var s = await State(); var job = s["operation"] as Dictionary<string, object>; return job != null && Convert.ToString(job["action"]) == "save" && !Convert.ToBoolean(s["busy"]); }, "save completion");
                checks["explicitSave"] = true;
                await WaitFor(async delegate { return await JsBool("!mutation && !state.busy"); }, "MAGIX UI completion");
                await view.ExecuteScriptAsync("document.getElementById('favorite-toggle').click()");
                await WaitFor(async delegate { return await JsBool("preferences.favorites.includes(selected.id)"); }, "favorite recorded");
                await view.ExecuteScriptAsync("loadCatalog()");
                await WaitFor(async delegate { return await JsBool("preferences.favorites.includes(selected.id) && document.getElementById('favorite-toggle').getAttribute('aria-pressed')==='true'"); }, "favorite catalog persistence");
                checks["favoritePersistence"] = true;
                await view.ExecuteScriptAsync("document.querySelector('[data-filter=\"mw5\"]').click()");
                await WaitFor(async delegate { return await JsBool("document.querySelectorAll('.project-item').length===1 && projectType(projects.find(p=>p.type==='mw5'))==='mw5'"); }, "MW5 filter");
                await view.ExecuteScriptAsync("document.querySelector('[data-filter=\"coding_data\"]').click()");
                await WaitFor(async delegate { return await JsBool("document.querySelectorAll('.project-item').length===1"); }, "coding filter");
                checks["mixedProjectFilters"] = true;
                await view.ExecuteScriptAsync("select(projects.find(p=>projectType(p)==='coding_data').id,true)");
                await WaitFor(async delegate { return await JsBool("projectType(selected)==='coding_data' && !document.getElementById('folder').disabled"); }, "coding folder ready");
                await view.ExecuteScriptAsync("document.getElementById('folder').click()");
                await WaitFor(async delegate { return await JsBool("state.activeSession?.kind==='folder' && state.operation.awaitingDone && !document.getElementById('session-done').disabled"); }, "generic folder session");
                checks["genericSessionProtected"] = !await Quit(false) && !service.HasExited;
                await view.ExecuteScriptAsync("document.getElementById('session-done').click()");
                await WaitFor(async delegate { return await JsBool("!state.busy && !mutation"); }, "generic session finished");
                checks["genericSessionFinished"] = true;
                await view.ExecuteScriptAsync("document.querySelector('[data-filter=\"mw5\"]').click(); select(projects.find(p=>projectType(p)==='mw5').id,true); window.confirm=()=>true");
                await WaitFor(async delegate { return await JsBool("projectType(selected)==='mw5' && !document.getElementById('mw5-refresh').disabled"); }, "MW5 check enabled");
                await view.ExecuteScriptAsync("document.getElementById('mw5-refresh').click()");
                await WaitFor(async delegate { return await JsBool("mw5Readiness?.canStage===true && !document.getElementById('mw5-stage').disabled && !mutation"); }, "MW5 editor check");
                checks["mw5Readiness"] = true;
                await view.ExecuteScriptAsync("document.getElementById('mw5-stage').click()");
                await WaitFor(async delegate { return await JsBool("mw5Preview?.direction==='stage' && !document.getElementById('mw5-apply').disabled && !mutation"); }, "MW5 staging preview");
                checks["mw5StagePreview"] = await JsBool("document.getElementById('mw5-preview-changes').children.length>0");
                await view.ExecuteScriptAsync("document.getElementById('mw5-apply').click()");
                await WaitFor(async delegate { return await JsBool("state.activeSession?.kind==='mw5' && mw5Readiness?.session?.canOpen===true && !document.getElementById('open').disabled && !mutation"); }, "MW5 staged session");
                checks["mw5StagingProtected"] = !await Quit(false) && !service.HasExited;
                await view.ExecuteScriptAsync("document.getElementById('open').click()");
                await WaitFor(async delegate { return await JsBool("mw5Readiness?.session?.status==='EDITING' && !state.transferring && !mutation"); }, "synthetic MW5 editing");
                checks["mw5EditorSession"] = true;
                await view.ExecuteScriptAsync("document.getElementById('mw5-collect').click()");
                await WaitFor(async delegate { return await JsBool("mw5Preview?.direction==='collect' && !document.getElementById('mw5-apply').disabled && !mutation"); }, "MW5 collection preview");
                checks["mw5CollectPreview"] = await JsBool("document.getElementById('mw5-preview-summary').textContent.includes('file change')");
                await view.ExecuteScriptAsync("document.getElementById('mw5-apply').click()");
                await WaitFor(async delegate { return await JsBool("!state.busy && !state.activeSession && !mutation && !document.getElementById('save').disabled"); }, "MW5 collection complete");
                checks["mw5CollectionFinishesSession"] = true;
                await view.ExecuteScriptAsync("document.getElementById('save').click()");
                await WaitFor(async delegate { return await JsBool("state.operation?.action==='save' && !state.busy && !mutation"); }, "MW5 explicit save");
                checks["mw5ExplicitSave"] = true;
                checks["hostObjectsDisabled"] = !view.CoreWebView2.Settings.AreHostObjectsAllowed;
                checks["webMessagesDisabled"] = !view.CoreWebView2.Settings.IsWebMessageEnabled;
                checks["devToolsDisabled"] = !view.CoreWebView2.Settings.AreDevToolsEnabled;
                checks["inPrivateProfile"] = view.CoreWebView2.Profile.IsInPrivateModeEnabled;
                TestWindowPreferences();
                foreach (var check in checks) if (check.Value is bool && !(bool)check.Value) throw new InvalidOperationException("Failed assertion: " + check.Key);
                checks["status"] = "PASS"; WriteReceipt();
                if (!await Quit(false)) throw new InvalidOperationException("Idle service did not accept shutdown.");
            }
            catch (Exception e)
            {
                ExitCode = 3; checks["status"] = "FAIL"; checks["failure"] = e.Message; WriteReceipt();
            }
            if (ExitCode == 3 && origin != null && !serviceEnded)
            {
                try
                {
                    var s = await State(); var job = s["operation"] as Dictionary<string, object>;
                    if (job != null && Convert.ToBoolean(job["awaitingDone"]))
                        using (var r = await Api("/api/session", new { response = "DONE", operationId = job["id"], promptGeneration = job["promptGeneration"] })) { }
                    await WaitFor(async delegate { return !Convert.ToBoolean((await State())["busy"]); }, "fixture cleanup");
                    await Quit(false);
                }
                catch (Exception) { ShowFailure("The synthetic test failed. Its demo service was preserved; no production workflow was started."); }
            }
        }
        private void TestWindowPreferences()
        {
            string folder = Path.Combine(Path.GetDirectoryName(options.Receipt), "window-preferences-fixture");
            Directory.CreateDirectory(folder);
            string path = Path.Combine(folder, "window.json");
            var prefs = new WindowPreferences(path, options.Identity);
            Rectangle bounds = WindowPreferences.VisibleBounds(new Rectangle(100000, -100000, 1100, 750));
            Rectangle restored; bool maximized;
            checks["windowPreferenceRoundtrip"] = prefs.Save(bounds, true) && prefs.TryLoad(out restored, out maximized) && restored == bounds && maximized;
            checks["windowPreferenceVisibleBounds"] = Screen.FromRectangle(bounds).WorkingArea.Contains(bounds) && bounds.Width >= 850 && bounds.Height >= 600;
            string original = File.ReadAllText(path);
            var differentOwner = new WindowPreferences(path, options.Identity + "-other");
            checks["windowPreferenceOwnership"] = !differentOwner.TryLoad(out restored, out maximized) && !differentOwner.Save(bounds, false) && File.ReadAllText(path) == original;
            string invalid = json.Serialize(new { format = "interlinked-window-preferences-v1", owner = options.Identity, x = 0, y = 0, width = 1, height = 1, maximized = false });
            File.WriteAllText(path, invalid, new UTF8Encoding(false));
            checks["windowPreferenceInvalidDataPreserved"] = !prefs.TryLoad(out restored, out maximized) && !prefs.Save(bounds, false) && File.ReadAllText(path) == invalid;
        }
        private void WriteReceipt()
        {
            if (!options.Smoke) return;
            checks["format"] = "interlinked-desktop-smoke-v1"; checks["demoOnly"] = true;
            checks["runtime"] = CoreWebView2Environment.GetAvailableBrowserVersionString();
            checks["utc"] = DateTime.UtcNow.ToString("o"); checks["servicePid"] = service == null ? 0 : service.Id;
            File.WriteAllText(options.Receipt, json.Serialize(checks), new UTF8Encoding(false));
        }
        private void Phase(string phase) { if (options.Smoke) { checks["status"] = "RUNNING"; checks["phase"] = phase; WriteReceipt(); } }
    }
}
