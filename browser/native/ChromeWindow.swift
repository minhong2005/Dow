import AppKit

// Deliver a standard macOS open-URL event to the verified process only.
// No browser scripting, tab enumeration or debugging access is required.
func fail(_ text: String) -> Never {
    FileHandle.standardError.write(Data((text + "\n").utf8))
    exit(1)
}
guard CommandLine.arguments.count == 3, let pid = Int32(CommandLine.arguments[2]),
      let chrome = NSRunningApplication(processIdentifier: pid),
      chrome.bundleIdentifier == "com.google.Chrome",
      chrome.executableURL?.resolvingSymlinksInPath().standardizedFileURL ==
        URL(fileURLWithPath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").resolvingSymlinksInPath().standardizedFileURL,
      !chrome.isTerminated else { fail("目标 Chrome 未运行") }
switch CommandLine.arguments[1] {
case "ready":
    if !chrome.isFinishedLaunching { exit(1) }
case "open":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count <= 120000, let text = String(data: data, encoding: .utf8),
          let url = URL(string: text), ["https","http"].contains(url.scheme?.lowercased() ?? ""),
          url.host != nil else { fail("网页链接无效") }
    let event = NSAppleEventDescriptor(eventClass: 0x4755524c, eventID: 0x4755524c,
        targetDescriptor: NSAppleEventDescriptor(processIdentifier: pid), returnID: -1, transactionID: 0)
    event.setParam(NSAppleEventDescriptor(string: text), forKeyword: 0x2d2d2d2d)
    do {
        let reply = try event.sendEvent(options: [.waitForReply, .neverInteract], timeout: 8)
        if let error = reply.paramDescriptor(forKeyword: 0x6572726e), error.int32Value != 0 {
            fail("Chrome 拒绝打开网页（\(error.int32Value)）")
        }
    } catch { fail("macOS 未能将链接交给指定的 Chrome（\((error as NSError).code)）") }
    _ = chrome.activate(options: [.activateAllWindows])
case "show":
    // Same reopen event as a Dock click. Chrome can be running with no open window.
    let reopen = NSAppleEventDescriptor(eventClass: 0x61657674, eventID: 0x72617070,
        targetDescriptor: NSAppleEventDescriptor(processIdentifier: pid), returnID: -1, transactionID: 0)
    _ = try? reopen.sendEvent(options: [.noReply,.neverInteract], timeout: 3)
    _ = chrome.activate(options: [.activateAllWindows])
case "quit":
    if !chrome.terminate() { fail("Chrome 尚未接受正常退出请求") }
default: fail("不支持的窗口操作")
}
