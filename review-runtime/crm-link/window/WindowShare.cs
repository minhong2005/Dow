using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal sealed partial class WindowShare : Form {
    private delegate bool EnumCallback(IntPtr hwnd, IntPtr context);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumCallback callback, IntPtr context);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool ShowWindowAsync(IntPtr hwnd, int command);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder title, int size);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    private static readonly HashSet<string> Apps = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
        "msedge","chrome","notepad","excel","winword"
    };
    private sealed class Target {
        public IntPtr Handle;
        public uint Pid;
        public bool Desktop;
        public string App, Title;
        public override string ToString() { return Desktop ? Title : App + "  ·  " + Title; }
    }
    private sealed class Lease {
        public Target Target;
        public DesktopFrame Frame;
        public string Token = Guid.NewGuid().ToString("N");
        public Dictionary<string,AutomationElement> Elements = new Dictionary<string,AutomationElement>();
        public volatile bool Choosing;
    }
    private volatile Lease current;
    private readonly object output = new object();
    private readonly ComboBox choices = new ComboBox();
    private readonly Button share = new Button(), pause = new Button(), refresh = new Button();
    private readonly Label status = new Label();

    [STAThread] public static void Main(string[] args) {
        SetProcessDPIAware();
        if(args.Length==0 && System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceInfo("window.cjs")!=null) {
            RunWorkstation();
            return;
        }
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        Application.EnableVisualStyles();
        Application.Run(new WindowShare());
    }
    private void Send(object data) {
        lock(output) { Console.WriteLine(new JavaScriptSerializer().Serialize(data)); Console.Out.Flush(); }
    }
    public WindowShare() {
        Text="Dow · China Windows"; ClientSize=new Size(570,125); AutoScaleMode=AutoScaleMode.Dpi;
        FormBorderStyle=FormBorderStyle.FixedDialog; MaximizeBox=false;
        choices.DropDownStyle=ComboBoxStyle.DropDownList; choices.SetBounds(12,12,452,27);
        refresh.Text="刷新窗口"; refresh.SetBounds(472,11,84,29); refresh.Click+=(s,e)=>RefreshTargets();
        share.Text="共享给 Dow"; share.SetBounds(12,53,126,30);
        pause.Text="我来接手"; pause.SetBounds(147,53,126,30); pause.Enabled=false;
        status.AutoEllipsis=true; status.SetBounds(12,93,545,25);
        Controls.AddRange(new Control[]{choices,refresh,share,pause,status});
        share.Click+=(s,e)=>{
            Target target=choices.SelectedItem as Target;
            if(target==null) return;
            if(target.Desktop && !hotkeyReady) {status.Text="Ctrl+Alt+F8 被占用，无法共享桌面。";return;}
            Lease next=new Lease{Target=target};
            string state=GetSharedWindowState(next);
            if(state=="gone" || state=="hidden") {
                RefreshTargets();
                status.Text="所选窗口已失效。请从列表重新选择。";
                return;
            }
            if(target.Desktop) ReleaseOldDowPanel();
            current=next;
            choices.Enabled=refresh.Enabled=share.Enabled=false; pause.Enabled=true;
            status.Text="共享中 · "+target.Title+"（点「我来接手」可停止后续操作）";
            Send(new {kind="share",session=next.Token});
        };
        pause.Click+=(s,e)=>StopSharing();
        var monitor=new System.Windows.Forms.Timer{Interval=700};
        monitor.Tick+=(s,e)=>{
            Lease lease=current;
            if(lease!=null) Send(new {kind="windowState",session=lease.Token,state=GetSharedWindowState(lease)});
        };
        Shown+=(s,e)=>{ RefreshTargets(); monitor.Start(); Thread io=new Thread(ReadCommands); io.IsBackground=true; io.SetApartmentState(ApartmentState.MTA); io.Start(); };
        FormClosing+=(s,e)=>{ current=null; monitor.Dispose(); };
    }
    private void StopSharing() {
        Lease old=current; current=null;
        if(old!=null) Send(new {kind="stop",session=old.Token});
        choices.Enabled=refresh.Enabled=true; pause.Enabled=false; RefreshTargets();
    }
    private void RefreshTargets() {
        choices.Items.Clear();
        choices.Items.Add(new Target{Desktop=true,Title="整个当前桌面（Ctrl+Alt+F8 随时接手）"});
        EnumWindows(delegate(IntPtr hwnd, IntPtr unused) {
            if(!IsWindowVisible(hwnd)) return true;
            uint pid; GetWindowThreadProcessId(hwnd,out pid);
            try {
                using(Process p=Process.GetProcessById((int)pid)) {
                    if(!Apps.Contains(p.ProcessName)) return true;
                    StringBuilder title=new StringBuilder(260); GetWindowText(hwnd,title,title.Capacity);
                    if(title.Length!=0) choices.Items.Add(new Target{Handle=hwnd,Pid=pid,App=p.ProcessName,Title=title.ToString()});
                }
            } catch(ArgumentException) {} catch(System.ComponentModel.Win32Exception) {} catch(InvalidOperationException) {}
            return true;
        },IntPtr.Zero);
        share.Enabled=choices.Items.Count>0;
        if(share.Enabled) choices.SelectedIndex=0;
        status.Text=share.Enabled?"请选择要共享的业务窗口。":"请先打开 Edge、Chrome、记事本、Excel 或 Word，再点刷新窗口。";
    }
    private static string GetSharedWindowState(Lease lease) {
        if(lease.Choosing) return "selecting";
        if(lease.Target.Desktop) return "desktop";
        uint pid;
        IntPtr handle=lease.Target.Handle;
        if(GetWindowThreadProcessId(handle,out pid)==0 || pid!=lease.Target.Pid) return "gone";
        if(!IsWindowVisible(handle)) return "hidden";
        return IsIconic(handle)?"minimized":"ready";
    }
    private string RequestSelection(Lease lease) {
        lease.Choosing=true;
        lease.Elements.Clear();
        BeginInvoke(new Action(()=>{
            if(current!=lease) return;
            choices.Enabled=refresh.Enabled=true;
            RefreshTargets();
            status.Text="Dow 请求重新选择窗口。选择后点「共享给 Dow」。";
            if(WindowState==FormWindowState.Minimized) WindowState=FormWindowState.Normal;
            Show();
            Activate();
        }));
        return "已在 Windows 上打开窗口选择面板。等待用户选定新窗口。";
    }
    private static void RequireVisible(string state) {
        if(state=="ready") return;
        if(state=="minimized")
            throw new InvalidOperationException("共享窗口仍存在，但已最小化。调用 restore 后再查看。");
        if(state=="selecting")
            throw new InvalidOperationException("Windows 正在等待选择要共享的新窗口。");
        throw new InvalidOperationException(state=="gone"
            ?"原共享窗口已失效。调用 reselect，可在 Windows 上打开窗口选择面板。"
            :"原共享窗口当前隐藏。调用 reselect，可在 Windows 上打开窗口选择面板。");
    }
    private AutomationElement WorkArea(Lease lease) {
        string app=lease.Target.App.ToLowerInvariant();
        AutomationElement root=AutomationElement.FromHandle(lease.Target.Handle);
        List<Condition> types=new List<Condition>();
        types.Add(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Document));
        if(app=="notepad") types.Add(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Edit));
        if(app=="excel") {
            types.Add(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.DataGrid));
            types.Add(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Table));
            types.Add(new PropertyCondition(AutomationElement.ClassNameProperty,"EXCEL7"));
        }
        Condition type=types.Count==1?types[0]:new OrCondition(types.ToArray());
        AutomationElement area=root.FindFirst(TreeScope.Descendants,new AndCondition(type,
            new PropertyCondition(AutomationElement.IsOffscreenProperty,false)));
        if(area==null && Browser(lease.Target)) return null;
        if(area==null) throw new InvalidOperationException("应用尚未提供可操作的文档区域，请在所选窗口打开网页或文档。");
        if(app=="chrome" || app=="msedge") {
            ValuePattern location=Pattern<ValuePattern>(area,ValuePattern.Pattern);
            Uri uri;
            if(location!=null && Uri.TryCreate(location.Current.Value,UriKind.Absolute,out uri) &&
               uri.Scheme!="https" && uri.Scheme!="http")
                return null;
        }
        return area;
    }
    private static T Pattern<T>(AutomationElement el, AutomationPattern id) where T:class {
        object p; return el.TryGetCurrentPattern(id,out p)?p as T:null;
    }
    private static List<string> Actions(AutomationElement el) {
        List<string> names=new List<string>();
        if(!el.Current.IsEnabled || el.Current.IsPassword) return names;
        if(Pattern<InvokePattern>(el,InvokePattern.Pattern)!=null ||
           Pattern<TogglePattern>(el,TogglePattern.Pattern)!=null ||
           Pattern<SelectionItemPattern>(el,SelectionItemPattern.Pattern)!=null ||
           Pattern<ExpandCollapsePattern>(el,ExpandCollapsePattern.Pattern)!=null) names.Add("click");
        ValuePattern v=Pattern<ValuePattern>(el,ValuePattern.Pattern);
        if(v!=null && !v.Current.IsReadOnly) names.Add("fill");
        ScrollPattern sc=Pattern<ScrollPattern>(el,ScrollPattern.Pattern);
        if(sc!=null && sc.Current.VerticallyScrollable) {names.Add("scroll_up"); names.Add("scroll_down");}
        return names;
    }
    private static string Clip(string s,int max) {return s==null?"":s.Substring(0,Math.Min(max,s.Length));}
    private static bool Browser(Target target) {
        return target.App.Equals("msedge",StringComparison.OrdinalIgnoreCase) ||
               target.App.Equals("chrome",StringComparison.OrdinalIgnoreCase);
    }

    // Browser chrome and page content are siblings. Stop at document boundaries so
    // page-created ARIA tabs cannot impersonate tabs in the browser's tab strip.
    private static bool BrowserTab(AutomationElement el, AutomationElement window) {
        if(el.Current.ControlType!=ControlType.TabItem) return false;
        for(int i=0; i<100 && el!=null; i++,el=TreeWalker.RawViewWalker.GetParent(el)) {
            if(el.Current.ControlType==ControlType.Document) return false;
            if(Automation.Compare(el,window)) return true;
        }
        return false;
    }
    private void AddBrowserTabs(Lease lease,List<object> rows) {
        AutomationElement root=AutomationElement.FromHandle(lease.Target.Handle);
        TreeWalker walker=TreeWalker.ControlViewWalker;
        Stack<AutomationElement> pending=new Stack<AutomationElement>();
        pending.Push(root);
        int visited=0;
        while(pending.Count>0 && rows.Count<150 && visited++<1200) {
            if(current!=lease) throw new InvalidOperationException("已由 Windows 用户接手。");
            AutomationElement el=pending.Pop();
            try {
                if(el.Current.ControlType==ControlType.Document) continue;
                if(el.Current.ControlType==ControlType.TabItem && BrowserTab(el,root)) {
                    if(!el.Current.IsEnabled) continue;
                    SelectionItemPattern selection=Pattern<SelectionItemPattern>(el,SelectionItemPattern.Pattern);
                    if(selection==null && Pattern<InvokePattern>(el,InvokePattern.Pattern)==null) continue;
                    string id=Guid.NewGuid().ToString("N");
                    lease.Elements.Add(id,el);
                    rows.Add(new {id=id,role="BrowserTab",name=Clip(el.Current.Name,110),
                        value=selection!=null && selection.Current.IsSelected?"selected":"",actions=new[]{"click"}});
                    continue;
                }
                for(AutomationElement child=walker.GetLastChild(el);child!=null && pending.Count<1600;
                    child=walker.GetPreviousSibling(child)) pending.Push(child);
            } catch(ElementNotAvailableException) {}
        }
    }
    private string Inspect(Lease lease,AutomationElement area) {
        lease.Elements.Clear();
        TreeWalker walker=TreeWalker.ControlViewWalker;
        Stack<AutomationElement> stack=new Stack<AutomationElement>(); if(area!=null) stack.Push(area);
        List<object> rows=new List<object>(); int visited=0;
        while(stack.Count>0 && rows.Count<110 && visited++<650) {
            if(current!=lease) throw new InvalidOperationException("已由 Windows 用户接手。");
            AutomationElement el=stack.Pop();
            try {
                if(el.Current.IsPassword) continue;
                if(!el.Current.IsOffscreen) {
                    List<string> actions=Actions(el);
                    string name=Clip(el.Current.Name,110), value="";
                    ValuePattern v=Pattern<ValuePattern>(el,ValuePattern.Pattern);
                    if(v!=null) value=Clip(v.Current.Value,160);
                    else if(el==area) {
                        TextPattern t=Pattern<TextPattern>(el,TextPattern.Pattern);
                        if(t!=null) value=Clip(t.DocumentRange.GetText(360),360);
                    }
                    if(name!="" || value!="" || actions.Count>0) {
                        string id=actions.Count==0?"":Guid.NewGuid().ToString("N");
                        if(id!="") lease.Elements.Add(id,el);
                        rows.Add(new {id=id,role=el.Current.ControlType.ProgrammaticName,name=name,value=value,actions=actions});
                    }
                }
                List<AutomationElement> children=new List<AutomationElement>();
                for(AutomationElement c=walker.GetFirstChild(el);c!=null && children.Count<180;c=walker.GetNextSibling(c)) children.Add(c);
                for(int i=children.Count-1;i>=0;i--) stack.Push(children[i]);
            } catch(ElementNotAvailableException) {}
        }
        List<object> tabs=new List<object>();
        if(Browser(lease.Target)) AddBrowserTabs(lease,tabs);
        StringBuilder title=new StringBuilder(260);
        GetWindowText(lease.Target.Handle,title,title.Capacity);
        return new JavaScriptSerializer().Serialize(new {window=title.ToString(),tabs=tabs,controls=rows,more=stack.Count>0});
    }
    private static bool InArea(AutomationElement el,AutomationElement area) {
        if(area==null) return false;
        for(int n=0;n<100 && el!=null;n++,el=TreeWalker.RawViewWalker.GetParent(el))
            if(Automation.Compare(el,area)) return true;
        return false;
    }
    private static bool Expired(long deadline) {
        return (DateTime.UtcNow - new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalMilliseconds>=deadline;
    }
    private string Act(Lease lease, string action,Dictionary<string,object> args,long deadline) {
        if(Expired(deadline)) throw new InvalidOperationException("窗口请求已过期。");
        if(lease.Target.Desktop) {
            if(action=="inspect") return "已共享整个桌面，请调用 windows_desktop 的 screen 操作。";
            return (string)Invoke(new Func<string>(()=>DesktopAct(lease,action,args,deadline)));
        }
        if(action=="screen" || args.ContainsKey("frame")) throw new InvalidOperationException("当前只共享一个窗口。桌面控制需要在 Windows 面板选择整个当前桌面。");
        string state=GetSharedWindowState(lease);
        if(action=="reselect") {
            if(state=="selecting") return "Windows 已在等待选择窗口。";
            return RequestSelection(lease);
        }
        if(action=="restore" && state=="minimized") {
            if(current!=lease || Expired(deadline)) throw new InvalidOperationException("窗口请求已取消。");
            const int restore=9;
            if(!ShowWindowAsync(lease.Target.Handle,restore))
                throw new InvalidOperationException("Windows 未接受恢复请求，请更新窗口状态。");
            lease.Elements.Clear();
            for(int i=0;i<20 && current==lease && !Expired(deadline) && state=="minimized";i++) {
                Thread.Sleep(100);
                state=GetSharedWindowState(lease);
            }
            RequireVisible(state);
            return "已恢复原共享窗口，现在可以重新查看。";
        }
        RequireVisible(state);
        if(action=="restore") return "原共享窗口已可见，现在可以重新查看。";
        AutomationElement area=WorkArea(lease);
        if(action=="inspect") return Inspect(lease,area);
        AutomationElement el;
        if(lease.Elements.TryGetValue((string)args["element"],out el) && action=="click" && Browser(lease.Target) &&
           BrowserTab(el,AutomationElement.FromHandle(lease.Target.Handle))) {
            if(current!=lease || Expired(deadline) || !el.Current.IsEnabled) throw new InvalidOperationException("请重新查看标签页。");
            lease.Elements.Clear();
            SelectionItemPattern selection=Pattern<SelectionItemPattern>(el,SelectionItemPattern.Pattern);
            if(selection!=null) selection.Select();
            else ((InvokePattern)el.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
            return "已选择标签页。请重新查看共享窗口。";
        }
        if(!lease.Elements.TryGetValue((string)args["element"],out el) || !InArea(el,area) ||
            el.Current.IsOffscreen || !Actions(el).Contains(action)) throw new InvalidOperationException("控件已变化，请重新查看。");
        if(current!=lease || Expired(deadline)) throw new InvalidOperationException("窗口请求已取消或过期。");
        lease.Elements.Clear();
        if(action=="fill") {
            string text=(string)args["text"];
            if(text.Length>1000) throw new ArgumentException("输入过长。");
            if(lease.Target.App.Equals("excel",StringComparison.OrdinalIgnoreCase) && text.TrimStart().IndexOfAny(new char[]{'=','+','-','@'})==0)
                throw new ArgumentException("此输入操作仅写入表格文本。");
            ((ValuePattern)el.GetCurrentPattern(ValuePattern.Pattern)).SetValue(text);
        } else if(action=="click") {
            InvokePattern inv=Pattern<InvokePattern>(el,InvokePattern.Pattern);
            TogglePattern tog=Pattern<TogglePattern>(el,TogglePattern.Pattern);
            SelectionItemPattern sel=Pattern<SelectionItemPattern>(el,SelectionItemPattern.Pattern);
            if(inv!=null) inv.Invoke();
            else if(tog!=null) tog.Toggle();
            else if(sel!=null) sel.Select();
            else {
                ExpandCollapsePattern exp=(ExpandCollapsePattern)el.GetCurrentPattern(ExpandCollapsePattern.Pattern);
                if(exp.Current.ExpandCollapseState==ExpandCollapseState.Expanded)exp.Collapse();else exp.Expand();
            }
        } else if(action=="scroll_down" || action=="scroll_up") {
            ((ScrollPattern)el.GetCurrentPattern(ScrollPattern.Pattern)).Scroll(ScrollAmount.NoAmount,
                action=="scroll_down"?ScrollAmount.LargeIncrement:ScrollAmount.LargeDecrement);
        } else {
            throw new InvalidOperationException("不支持的窗口动作。");
        }
        return "操作已执行。请查看当前窗口确认结果。";
    }
    private void ReadCommands() {
        string line;
        try {
            while((line=Console.ReadLine())!=null) {
                var req=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(line);
                string id=(string)req["id"], session=(string)req["session"];
                var args=(Dictionary<string,object>)req["args"];
                Lease lease=current;
                try {
                    if(lease==null || lease.Token!=session) throw new InvalidOperationException("Windows 用户已接手。");
                    string text=Act(lease,(string)args["action"],args,Convert.ToInt64(req["deadline"]));
                    if(current!=lease) throw new InvalidOperationException("Windows 用户已接手。请重新核对结果。");
                    Send(new {kind="reply",id=id,session=session,ok=true,text=text});
                } catch(Exception ex) {Send(new {kind="reply",id=id,session=session,ok=false,text=Clip(ex.Message,240)});}
            }
        } finally {if(!IsDisposed && IsHandleCreated) BeginInvoke(new Action(Close));}
    }
}
