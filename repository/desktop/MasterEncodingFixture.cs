// Owned, transparent test window: its caption exercises metadata encoding only.
// No other application's UI, window contents, or desktop pixels are accessed.
using System;
using System.Drawing;
using System.Globalization;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class MasterEncodingFixture
{
    internal const string Caption="Violet \u2014 \u03A9 \u65E5\u672C\u8A9E";

    [STAThread] private static int Main()
    {
        Console.InputEncoding=new UTF8Encoding(false);
        Console.OutputEncoding=new UTF8Encoding(false);
        Application.EnableVisualStyles();
        using(var form=new Form {
            Text=Caption,FormBorderStyle=FormBorderStyle.FixedToolWindow,
            StartPosition=FormStartPosition.Manual,Location=new Point(-30000,-30000),
            Size=new Size(180,100),ShowInTaskbar=false,Opacity=0,TopMost=false
        })using(var safety=new System.Windows.Forms.Timer {Interval=30000}){
            safety.Tick+=delegate{form.Close();};
            form.Shown+=delegate {
                safety.Start();
                Console.WriteLine("{\"windowId\":\""+form.Handle.ToInt64().ToString(CultureInfo.InvariantCulture)+"\"}");
                Console.Out.Flush();
                new Thread(delegate(){
                    try{Console.ReadLine();}catch{}
                    try{if(!form.IsDisposed)form.BeginInvoke(new Action(form.Close));}catch{}
                }){IsBackground=true,Name="Encoding fixture lifetime"}.Start();
            };
            Application.Run(form);
        }
        return 0;
    }
}
