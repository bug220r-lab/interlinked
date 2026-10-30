// Interactive Windows acceptance fixture. Captures only windows created here.
// Reflection exercises the built worker without starting Run(), USB or helpers.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class MasterCaptureFixture : Form
{
    private int phase;
    private Color background = Color.Black;
    private static readonly List<Dictionary<string, object>> Checks = new List<Dictionary<string, object>>();
    private static readonly List<string> Failures = new List<string>();
    private static Type workerType;
    private static object worker;
    private static string output;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [StructLayout(LayoutKind.Sequential)] private struct Rect { internal int Left, Top, Right, Bottom; }

    private MasterCaptureFixture()
    {
        Text = "INTERLINKED owned synthetic capture fixture";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        ShowInTaskbar = false;
        TopMost = true;
        DoubleBuffered = true;
        Rectangle work = Screen.PrimaryScreen.WorkingArea;
        Bounds = new Rectangle(work.Left + 35, work.Top + 45, 280, 280);
    }
    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.Clear(background);
        using (var purple = new SolidBrush(Color.FromArgb(155, 48, 238)))
            e.Graphics.FillEllipse(purple, 65 + phase * 35, 90, 90, 90);
        e.Graphics.FillEllipse(Brushes.White, 88 + phase * 35, 117, 12, 16);
        e.Graphics.FillEllipse(Brushes.White, 118 + phase * 35, 117, 12, 16);
    }
    private static void Pump(int milliseconds)
    {
        var watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < milliseconds) { Application.DoEvents(); Thread.Sleep(10); }
    }
    private static void Check(bool passed, string name, string detail = "")
    {
        Checks.Add(new Dictionary<string, object> { { "name", name }, { "passed", passed }, { "detail", detail } });
        if (!passed) Failures.Add(name + (detail.Length == 0 ? "" : ": " + detail));
    }
    private static Dictionary<string, object> BoundsValue(Rect rect)
    {
        return new Dictionary<string, object> { { "x", rect.Left }, { "y", rect.Top }, { "width", rect.Right - rect.Left }, { "height", rect.Bottom - rect.Top } };
    }
    private static Bitmap Capture(Dictionary<string, object> config, out string reason)
    {
        object[] arguments = { config, null };
        var result = (Bitmap)workerType.GetMethod("CaptureMini", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(worker, arguments);
        reason = arguments[1] as string;
        return result;
    }
    private static Bitmap Draw(Dictionary<string, object> config)
    {
        var state = new Dictionary<string, object> { { "projects", new object[] {
            new Dictionary<string, object> { { "id", "fixture" }, { "label", "SYNTHETIC" }, { "activity", new Dictionary<string, object> { { "status", "unknown" } } } }
        } } };
        return (Bitmap)workerType.GetMethod("DrawFrame", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(worker, new object[] { state, config, 1.0 });
    }
    private static string Digest(Bitmap bitmap)
    {
        using (var stream = new MemoryStream()) using (var sha = SHA256.Create())
        {
            bitmap.Save(stream, ImageFormat.Png);
            return BitConverter.ToString(sha.ComputeHash(stream.ToArray())).Replace("-", "").ToLowerInvariant();
        }
    }
    private static int MagentaPixels(Bitmap image)
    {
        int count = 0;
        for (int y = 59; y < 149; y++) for (int x = 195; x < 285; x++)
        { Color pixel = image.GetPixel(x, y); if (pixel.R > 235 && pixel.B > 235 && pixel.G < 30) count++; }
        return count;
    }
    private static Form Cover(Rectangle bounds)
    {
        var cover = new Form { Text = "Owned fixture occluder", FormBorderStyle = FormBorderStyle.None,
            StartPosition = FormStartPosition.Manual, ShowInTaskbar = false, TopMost = true,
            BackColor = Color.Red, Bounds = bounds };
        cover.Show(); cover.BringToFront(); Pump(250); return cover;
    }
    private static void ExpectUnavailable(Dictionary<string, object> config, string expected, string check)
    {
        string reason;
        using (var image = Capture(config, out reason))
            Check(image == null && (reason ?? "").IndexOf(expected, StringComparison.OrdinalIgnoreCase) >= 0, check, reason ?? "No reason");
    }
    private void RunChecks()
    {
        Pump(350);
        Rect rect; if (!GetWindowRect(Handle, out rect)) throw new InvalidOperationException("Fixture bounds unavailable");
        var config = new Dictionary<string, object> { { "mode", "live_mini" }, { "captureEnabled", true },
            { "captureWindowId", Handle.ToInt64().ToString(CultureInfo.InvariantCulture) },
            { "captureWindowBounds", BoundsValue(rect) }, { "captureRect", BoundsValue(rect) } };
        string reason, firstDigest = null;
        using (var first = Capture(config, out reason))
        {
            Check(first != null && reason == null && first.Width == 280 && first.Height == 280, "Visible black fixture produces a real region capture", reason ?? "");
            if (first != null)
            {
                firstDigest = Digest(first); first.Save(Path.Combine(output, "capture-first.png"));
                Check(first.GetPixel(1, 1).ToArgb() == Color.Black.ToArgb() && first.GetPixel(278, 278).ToArgb() == Color.Black.ToArgb(), "Captured margins remain pure black");
            }
        }
        phase = 1; Invalidate(); Pump(300);
        using (var second = Capture(config, out reason))
        {
            Check(second != null && firstDigest != null && Digest(second) != firstDigest, "New source motion changes captured pixels", reason ?? "");
            if (second != null) second.Save(Path.Combine(output, "capture-motion.png"));
        }
        using (var cover = Cover(new Rectangle(rect.Left + 100, rect.Top + 85, 100, 110)))
            ExpectUnavailable(config, "covered", "Centered occlusion invalidates live capture");
        Pump(200);
        using (var cover = Cover(new Rectangle(rect.Left + 18, rect.Top + 18, 45, 45)))
            ExpectUnavailable(config, "covered", "Off-center partial occlusion invalidates live capture");
        Pump(200);
        Point original = Location; Location = new Point(original.X + 18, original.Y + 12); Pump(200);
        ExpectUnavailable(config, "moved", "Moving the selected source invalidates its calibration");
        Location = original; Pump(200);
        WindowState = FormWindowState.Minimized; Pump(200);
        ExpectUnavailable(config, "hidden", "Minimizing the source invalidates live capture");
        WindowState = FormWindowState.Normal; Bounds = new Rectangle(rect.Left, rect.Top, 280, 280); Activate(); Pump(250);
        background = Color.FromArgb(0, 32, 0); Invalidate(); Pump(200);
        ExpectUnavailable(config, "black", "Non-black source margins are refused");
        background = Color.Black; Invalidate(); Pump(200);
        var invalidCrop = new Dictionary<string, object>(config);
        invalidCrop["captureRect"] = new Dictionary<string, object> { { "x", rect.Left - 1 }, { "y", rect.Top }, { "width", 280 }, { "height", 280 } };
        ExpectUnavailable(invalidCrop, "outside", "A crop outside the owned source is refused before capture");
        using (var atlas = new Bitmap(1536, 2288))
        { using (var graphics = Graphics.FromImage(atlas)) graphics.Clear(Color.Magenta); atlas.Save(Path.Combine(output, "synthetic-atlas.png")); }
        config["violetAtlasPath"] = Path.Combine(output, "synthetic-atlas.png");
        var dashboard = new Dictionary<string, object>(config); dashboard["mode"] = "dashboard";
        using (var frame = Draw(dashboard)) Check(MagentaPixels(frame) > 1000, "Synthetic atlas control proves fallback detection is sensitive");
        Hide(); Pump(150);
        ExpectUnavailable(config, "hidden", "Hiding the source invalidates live capture");
        using (var frame = Draw(config))
        {
            frame.Save(Path.Combine(output, "mini-unavailable.png"));
            Check(MagentaPixels(frame) == 0, "Unavailable live mini never substitutes the dashboard idle atlas");
        }
        Show(); BringToFront(); Pump(250);
        using (var restored = Capture(config, out reason)) Check(restored != null && reason == null, "Restoring the same calibrated source resumes real capture", reason ?? "");
        var disabled = new Dictionary<string, object>(config); disabled["captureEnabled"] = false;
        ExpectUnavailable(disabled, "calibrate", "Disabled capture never reads a desktop region");
        var missing = new Dictionary<string, object>(config); missing["captureWindowId"] = "0";
        ExpectUnavailable(missing, "calibrate", "An absent source never reads a desktop region");
    }
    [STAThread] private static int Main(string[] args)
    {
        if (args.Length != 2) return 2;
        SetProcessDPIAware(); Application.EnableVisualStyles();
        output = Path.GetFullPath(args[1]);
        try
        {
            if (!Directory.Exists(output)) throw new InvalidOperationException("Runner must create the isolated output directory");
            workerType = Assembly.LoadFrom(Path.GetFullPath(args[0])).GetType("InterlinkedMaster.Worker", true);
            worker = Activator.CreateInstance(workerType, true);
            using (var fixture = new MasterCaptureFixture())
            {
                fixture.Shown += delegate { fixture.BeginInvoke(new Action(delegate {
                    try { fixture.RunChecks(); } catch (Exception ex) { Check(false, "Fixture completed", ex.ToString()); }
                    finally { fixture.Close(); }
                })); };
                Application.Run(fixture);
            }
        }
        catch (Exception ex) { Check(false, "Fixture initialization", ex.ToString()); }
        finally { if (worker != null) ((IDisposable)worker).Dispose(); }
        var report = new Dictionary<string, object> { { "ok", Failures.Count == 0 }, { "checks", Checks },
            { "errors", Failures }, { "usbTested", false }, { "actualMiniTested", false },
            { "scope", "Real Windows screen capture restricted to this process's synthetic source and occluder windows; worker Run and USB paths never invoked" } };
        string encoded = Json.Serialize(report);
        File.WriteAllText(Path.Combine(output, "capture-report.json"), encoded);
        Console.WriteLine(encoded); return Failures.Count == 0 ? 0 : 1;
    }
}
