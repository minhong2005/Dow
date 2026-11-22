import AppKit
import WebKit

final class Workbench: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, NSToolbarDelegate {
    private var window: NSWindow!
    private var web: WKWebView!
    private var child: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private let reads = DispatchQueue(label: "aiwork.desktop.read")
    private let writes = DispatchQueue(label: "aiwork.desktop.write")
    private var pending = Data()
    private let resources = Bundle.main.resourceURL!
    private let searchItem = NSToolbarItem.Identifier("DowSessionSearch")

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "workbench")
        config.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.allowsMagnification = false
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1190, height: 760),
                          styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named:.darkAqua)
        window.title = "Dow"
        window.titleVisibility = .hidden
        window.toolbarStyle = .unifiedCompact
        let toolbar = NSToolbar(identifier: "DowWorkspace")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.centeredItemIdentifier = searchItem
        window.toolbar = toolbar
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(srgbRed: 0.078, green: 0.078, blue: 0.078, alpha: 1)
        window.minSize = NSSize(width: 850,height: 560)
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("AIWorkDesktopWindow")
        startBridge()
        web.loadFileURL(resources.appendingPathComponent("index.html"),allowingReadAccessTo: resources)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps:true)
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, searchItem]
    }
    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, searchItem, .flexibleSpace]
    }
    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        guard id == searchItem else { return nil }
        let item = NSToolbarItem(itemIdentifier: id)
        let button = NSButton(title: "Dow  ·  查找会话     ⌘P", target: self, action: #selector(quickOpen))
        button.image = NSImage(systemSymbolName: "magnifyingglass", accessibilityDescription: nil)
        button.imagePosition = .imageLeading
        button.bezelStyle = .accessoryBarAction
        button.font = .systemFont(ofSize: 12)
        button.setAccessibilityLabel("查找会话")
        button.widthAnchor.constraint(equalToConstant: 252).isActive = true
        item.label = "查找会话"
        item.view = button
        return item
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ note: Notification) {
        output?.readabilityHandler = nil
        try? input?.close()
    }

    private func startBridge() {
        let p = Process(), i = Pipe(), o = Pipe()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-I","-u",resources.appendingPathComponent("backend.py").path]
        p.environment = ProcessInfo.processInfo.environment.merging(["PATH":"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin","LANG":"en_US.UTF-8"]) { _, new in new }
        p.standardInput = i; p.standardOutput = o; p.standardError = FileHandle.nullDevice
        input = i.fileHandleForWriting; output = o.fileHandleForReading
        o.fileHandleForReading.readabilityHandler = { [weak self] h in
            let next = h.availableData
            if next.isEmpty { h.readabilityHandler = nil; return }
            self?.reads.async { [weak self] in self?.parse(next) }
        }
        p.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let s = self, s.child === process else { return }
                s.child = nil
                s.deliver([["type":"network","online":false,"ip":"","state":"unknown"],
                           ["type":"browserNetwork","state":"unknown"],
                           ["type":"notice","message":"工作台连接已关闭。点「终端出口」重新连接。"]])
            }
        }
        do { try p.run(); child = p } catch {
            showError("无法启动工作台连接",error.localizedDescription)
        }
    }

    private func parse(_ data: Data) {
        pending.append(data)
        var batch = [[String:Any]]()
        while let end = pending.firstIndex(of: 10) {
            if let object = try? JSONSerialization.jsonObject(with: pending[..<end]) as? [String:Any] {
                batch.append(object)
            }
            pending.removeSubrange(...end)
        }
        if !batch.isEmpty {
            let events = batch
            DispatchQueue.main.async { [weak self] in self?.deliver(events) }
        }
    }

    private func deliver(_ events: [[String:Any]]) {
        web.callAsyncJavaScript("window.acceptEvents(events)", arguments:["events":events], in:nil, in:.page, completionHandler:nil)
    }

    func userContentController(_ controller: WKUserContentController, didReceive msg: WKScriptMessage) {
        guard msg.frameInfo.isMainFrame, msg.frameInfo.request.url?.isFileURL == true,
              let value = msg.body as? [String:Any], let op = value["op"] as? String else { return }
        if op == "copy", let text = value["text"] as? String {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text,forType:.string)
            return
        }
        if (op == "startup" || op == "checkBrowser") && child == nil { startBridge() }
        guard let pipe = input, var data = try? JSONSerialization.data(withJSONObject:value) else { return }
        data.append(10)
        let bytes = data
        writes.async { try? pipe.write(contentsOf: bytes) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy)->Void) {
        let target = action.request.url?.standardizedFileURL
        decisionHandler(target == resources.appendingPathComponent("index.html").standardizedFileURL ? .allow : .cancel)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        child?.terminate(); child=nil
        startBridge()
        web.reload()
    }

    private func showError(_ title:String,_ detail:String) {
        let alert = NSAlert(); alert.messageText=title; alert.informativeText=detail; alert.runModal()
    }
    private func makeMenu() {
        let bar = NSMenu()
        func addMenu(_ title:String)->NSMenu {
            let item = NSMenuItem(); bar.addItem(item)
            let menu = NSMenu(title:title); item.submenu=menu; return menu
        }
        let app = addMenu("Dow")
        app.addItem(withTitle:"退出 Dow",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        let session = addMenu("会话")
        session.addItem(withTitle:"新建会话",action:#selector(newSession),keyEquivalent:"n").target=self
        session.addItem(withTitle:"会话总览",action:#selector(overview),keyEquivalent:"1").target=self
        session.addItem(withTitle:"查找会话",action:#selector(quickOpen),keyEquivalent:"p").target=self
        session.addItem(withTitle:"显示或隐藏侧栏",action:#selector(toggleSidebar),keyEquivalent:"b").target=self
        let edit = addMenu("编辑")
        for (title,selector,key) in [("撤销","undo:","z"),("剪切","cut:","x"),("复制","copy:","c"),("粘贴","paste:","v"),("全选","selectAll:","a")] {
            edit.addItem(withTitle:title,action:NSSelectorFromString(selector),keyEquivalent:key)
        }
        let win=addMenu("窗口")
        win.addItem(withTitle:"最小化",action:#selector(NSWindow.performMiniaturize(_:)),keyEquivalent:"m")
        win.addItem(withTitle:"关闭窗口",action:#selector(NSWindow.performClose(_:)),keyEquivalent:"w")
        NSApp.mainMenu=bar; NSApp.windowsMenu=win
    }
    @objc private func newSession(){ web.evaluateJavaScript("window.newSession()") }
    @objc private func overview(){ web.evaluateJavaScript("window.overview()") }
    @objc private func quickOpen(){ web.evaluateJavaScript("window.quickOpen()") }
    @objc private func toggleSidebar(){ web.evaluateJavaScript("window.toggleSidebar()") }
}
let application = NSApplication.shared
if CommandLine.arguments.dropFirst() == ["--confirm-mac-command"] {
    guard let value = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String:String],
          let cwd = value["cwd"], let command = value["command"] else { exit(1) }
    application.setActivationPolicy(.accessory)
    let alert = NSAlert()
    alert.messageText = "允许这次 Mac 本机操作？"
    alert.informativeText = "工作目录  " + cwd + "\n命令将使用你的 Mac 文件和应用权限。"
    alert.addButton(withTitle: "取消")
    alert.addButton(withTitle: "运行一次")
    let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 510, height: 160))
    scroll.hasVerticalScroller = true
    scroll.borderType = .bezelBorder
    let view = NSTextView(frame: scroll.contentView.bounds)
    view.isEditable = false
    view.isVerticallyResizable = true
    view.autoresizingMask = [.width]
    view.textContainer?.widthTracksTextView = true
    view.font = .monospacedSystemFont(ofSize: 13, weight: .regular)
    view.string = command
    scroll.documentView = view
    alert.accessoryView = scroll
    application.activate(ignoringOtherApps: true)
    exit(alert.runModal() == .alertSecondButtonReturn ? 0 : 1)
}
let delegate = Workbench()
signal(SIGPIPE, SIG_IGN)
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
