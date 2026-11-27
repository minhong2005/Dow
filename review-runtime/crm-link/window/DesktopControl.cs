using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal sealed partial class WindowShare {
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool RegisterHotKey(IntPtr h,int id,uint modifiers,uint key);
    [DllImport("user32.dll")] private static extern bool UnregisterHotKey(IntPtr h,int id);
    [DllImport("user32.dll")] private static extern uint SendInput(uint n, Input[] entries,int size);
    [StructLayout(LayoutKind.Sequential)] struct MouseInput {public int x,y; public uint data,flags,time; public UIntPtr extra;}
    [StructLayout(LayoutKind.Sequential)] struct KeyInput {public ushort code,scan; public uint flags,time; public UIntPtr extra;}
    [StructLayout(LayoutKind.Explicit)] struct Payload {
        [FieldOffset(0)] public MouseInput mouse;
        [FieldOffset(0)] public KeyInput key;
    }
    [StructLayout(LayoutKind.Sequential)] struct Input {public uint type; public Payload payload;}
    private static void RunWorkstation() {
        string runtime=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Dow CRM","Workstation","runtime","node.exe");
        if(!File.Exists(runtime)) {MessageBox.Show("请使用已配对的 Windows 用户打开此组件。","Dow");return;}
        var info=new System.Diagnostics.ProcessStartInfo(runtime,"-"){UseShellExecute=false,RedirectStandardInput=true};
        info.EnvironmentVariables["DOW_WINDOW_PANEL"]=Application.ExecutablePath;
        using(var process=System.Diagnostics.Process.Start(info))
        using(var source=System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("window.cjs")) {
            source.CopyTo(process.StandardInput.BaseStream);
            process.StandardInput.Close();
            process.WaitForExit();
        }
    }
    private static void ReleaseOldDowPanel() {
        using(var self=System.Diagnostics.Process.GetCurrentProcess()) {
            foreach(var p in System.Diagnostics.Process.GetProcessesByName("WindowShare")) {
                using(p) {
                    try {
                        if(p.Id!=self.Id && p.SessionId==self.SessionId && p.MainWindowTitle=="Dow · China Windows") p.CloseMainWindow();
                    } catch(InvalidOperationException) {} catch(System.ComponentModel.Win32Exception) {}
                }
            }
        }
    }
    private const int StopHotkey=902;
    private bool hotkeyReady;
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        hotkeyReady=RegisterHotKey(Handle,StopHotkey,3,(uint)Keys.F8);
    }
    protected override void OnHandleDestroyed(EventArgs e) {
        if(hotkeyReady) UnregisterHotKey(Handle,StopHotkey);
        base.OnHandleDestroyed(e);
    }
    protected override void WndProc(ref Message m) {
        if(m.Msg==0x0312 && m.WParam.ToInt32()==StopHotkey && current!=null) {StopSharing(); return;}
        base.WndProc(ref m);
    }
    private sealed class DesktopFrame {
        public string Token=Guid.NewGuid().ToString("N");
        public Rectangle Bounds;
        public int Width,Height;
    }
    private string Snapshot(Lease lease) {
        Rectangle bounds=SystemInformation.VirtualScreen;
        double scale=Math.Min(1,1600.0/Math.Max(bounds.Width,bounds.Height));
        using(Bitmap source=new Bitmap(bounds.Width,bounds.Height)) {
            using(Graphics g=Graphics.FromImage(source)) g.CopyFromScreen(bounds.Location,Point.Empty,bounds.Size);
            ImageCodecInfo codec=Array.Find(ImageCodecInfo.GetImageEncoders(),e=>e.MimeType=="image/jpeg");
            for(int pass=0;pass<12;pass++,scale*=0.84) {
                var frame=new DesktopFrame{Bounds=bounds,Width=Math.Max(1,(int)(bounds.Width*scale)),Height=Math.Max(1,(int)(bounds.Height*scale))};
                using(Bitmap img=new Bitmap(frame.Width,frame.Height))
                using(Graphics g=Graphics.FromImage(img))
                using(MemoryStream stream=new MemoryStream())
                using(var parameters=new EncoderParameters(1)) {
                    g.InterpolationMode=InterpolationMode.HighQualityBilinear;
                    g.DrawImage(source,new Rectangle(0,0,img.Width,img.Height));
                    parameters.Param[0]=new EncoderParameter(System.Drawing.Imaging.Encoder.Quality,72L);
                    img.Save(stream,codec,parameters);
                    if(stream.Length>42000) continue;
                    lease.Frame=frame;
                    return new JavaScriptSerializer().Serialize(new {frame=frame.Token,width=frame.Width,height=frame.Height,
                        jpeg=Convert.ToBase64String(stream.ToArray())});
                }
            }
        }
        throw new InvalidOperationException("无法生成桌面图像。");
    }
    private static Input Key(ushort code,ushort scan,uint flags) {
        return new Input{type=1,payload=new Payload{key=new KeyInput{code=code,scan=scan,flags=flags}}};
    }
    private static void Chord(List<Input> events,string chord) {
        var keys=new List<ushort>();
        string[] parts=chord.Split('+');
        if(parts.Length>4) throw new ArgumentException("组合键过长。");
        for(int i=0;i<parts.Length;i++) {
            string name=parts[i].Trim().ToUpperInvariant(); ushort vk;
            if(name=="CTRL") vk=17; else if(name=="ALT") vk=18;
            else if(name=="SHIFT") vk=16; else if(name=="WIN") vk=91;
            else {
                if(i!=parts.Length-1) throw new ArgumentException("组合键格式无效。");
                if(name.Length==1 && ((name[0]>='A' && name[0]<='Z') || (name[0]>='0' && name[0]<='9'))) vk=name[0];
                else {
                    Keys parsed;
                    if(name.Length==0 || !Char.IsLetter(name[0]) || !Enum.TryParse<Keys>(name,true,out parsed) ||
                       !Enum.IsDefined(typeof(Keys),parsed) || (int)parsed<8 || (int)parsed>254)
                        throw new ArgumentException("不支持的键名。");
                    vk=(ushort)parsed;
                }
            }
            if(keys.Contains(vk)) throw new ArgumentException("重复的按键。");
            keys.Add(vk);
        }
        foreach(ushort vk in keys) events.Add(Key(vk,0,0));
        keys.Reverse();
        foreach(ushort vk in keys) events.Add(Key(vk,0,2));
    }
    private string DesktopAct(Lease lease,string action,Dictionary<string,object> args,long deadline) {
        if(current!=lease || !lease.Target.Desktop || Expired(deadline)) throw new InvalidOperationException("桌面未共享或操作已过期。");
        if(action=="screen") return Snapshot(lease);
        DesktopFrame frame=lease.Frame;
        if(frame==null || !args.ContainsKey("frame") || (string)args["frame"]!=frame.Token ||
           frame.Bounds!=SystemInformation.VirtualScreen) throw new InvalidOperationException("请重新查看桌面后操作。");
        var events=new List<Input>();
        if(action=="key") Chord(events,(string)args["key"]);
        else if(action=="type") {
            string text=(string)args["text"];
            if(text.Length>1000) throw new ArgumentException("每次输入最多 1000 个字符。");
            foreach(char c in text) {events.Add(Key(0,c,4));events.Add(Key(0,c,6));}
        } else {
            int x=(int)args["x"],y=(int)args["y"];
            if(x<0 || x>=frame.Width || y<0 || y>=frame.Height) throw new ArgumentException("坐标超出桌面。");
            events.Add(new Input{payload=new Payload{mouse=new MouseInput{x=(int)((x+0.5)*65535/frame.Width),y=(int)((y+0.5)*65535/frame.Height),flags=0xC001}}});
            if(action=="wheel_up" || action=="wheel_down") {
                events.Add(new Input{payload=new Payload{mouse=new MouseInput{flags=0x0800,data=unchecked((uint)(action=="wheel_up"?120:-120))}}});
            } else {
                if(action!="point_click" && action!="double_click" && action!="right_click") throw new ArgumentException("不支持的鼠标操作。");
                uint down=action=="right_click"?8u:2u;
                for(int i=0;i<(action=="double_click"?2:1);i++){
                    events.Add(new Input{payload=new Payload{mouse=new MouseInput{flags=down}}});
                    events.Add(new Input{payload=new Payload{mouse=new MouseInput{flags=down<<1}}});
                }
            }
        }
        lease.Frame=null;
        if(events.Count>0 && SendInput((uint)events.Count,events.ToArray(),Marshal.SizeOf(typeof(Input)))!=events.Count)
            throw new InvalidOperationException("Windows 未接受全部输入，请重新查看桌面。");
        return "已发送输入。请用 screen 确认结果。";
    }
}
