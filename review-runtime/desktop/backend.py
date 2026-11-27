"""Private stdio bridge between the Mac window and the isolated workbench."""
import base64
import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_firewall
import project_runtime

DOCKER = ["/opt/homebrew/bin/docker","--context","colima-dow","exec"]
CONTAINER = "aiwork-tools"
LAUNCHER = str(Path(__file__).resolve().with_name("aiwork"))
EXPECTED = (Path.home()/".config/dow/expected-ip").read_text().strip()
BROWSER_STATIONS = ("ipify", "Cloudflare", "AWS")
LOCK = threading.Lock()
ops = queue.Queue()
views = {}
catalog = {}
online = False
poll_pending = threading.Event()
closing = threading.Event()
browser_requested = threading.Event()

def emit(kind, **values):
    line = json.dumps(dict(type=kind,**values),ensure_ascii=True,separators=(",",":"))
    with LOCK:
        try:
            sys.stdout.write(line+"\n");sys.stdout.flush()
        except BrokenPipeError:
            os._exit(0)

def run(args, timeout=35):
    result = subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        detail=result.stderr.strip().splitlines()
        raise RuntimeError(detail[-1][:220] if detail else "工作台连接失败")
    return result.stdout.strip()

def refresh():
    global catalog
    catalog = project_runtime.catalog()
    rows = sorted((row for _, row in catalog.values()),
                  key=lambda row: (row["project"], row["scope"] != "isolated", row["title"], row["id"]))
    emit("sessions",rows=rows)

def address(raw):
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""

def gateway_status():
    started = time.time()
    try:
        result = json.loads(run([*DOCKER, CONTAINER, "socat", "-T2", "-",
                                 "UNIX-CONNECT:/link/status.sock"], timeout=3))
        state = result["state"]
        if state in ("checking", "probe_failed", "expired", "mismatch"):
            probes = result.get("probes", {})
            detail = " · ".join(f"{name} {probes[name]}" for name in ("ipify", "cloudflare", "aws", "ifconfig")
                                if isinstance(probes.get(name), str)
                                and re.fullmatch(r"[a-z0-9_]{2,32}", probes[name]))
            return {"state":state, "detail":detail}
        remaining = result["remainingMs"]
        generation = result["generation"]
        if (state == "verified" and result["ip"] == EXPECTED and type(remaining) is int
                and 0 < remaining <= 15000 and re.fullmatch(r"[a-f0-9]{24}", generation)):
            valid_until = started + remaining / 1000
            if valid_until > time.time():
                return {"state":state, "ip":EXPECTED, "at":int(result["at"]),
                        "validUntil":valid_until, "generation":generation}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        pass
    return {"state":"unknown"}

def check(boot):
    global online
    if boot:
        try:
            run([LAUNCHER,"--ready"], timeout=150)
        except (OSError,RuntimeError,subprocess.SubprocessError):
            pass
    sample = gateway_status()
    online = sample["state"] == "verified"
    emit("network", online=online, **sample)
    if boot and not online:emit("notice",message="固定出口未就绪，请检查工作台代理后点「终端出口」。")

def chrome_control(service, action, **fields):
    path = Path.home() / "Library/Application Support/aiwork-native" / (service + ".sock")
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(35 if action in ("open", "startCheck") else 7)
        conn.connect(str(path))
        conn.sendall(json.dumps(dict(action=action, **fields)).encode() + b"\n")
        with conn.makefile("rb") as response:
            result = json.loads(response.readline(8192))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Chrome 网络保护未就绪")
    return result

def chrome(action, **fields):
    return chrome_control("driver", action, **fields)

def browser_relay(action, **fields):
    return chrome_control("relay", action, **fields)

def probe_line(proc, timeout):
    if not select.select([proc.stdout], [], [], timeout)[0]:
        raise RuntimeError("Chrome 检测超时")
    line = proc.stdout.readline(512)
    if not line.endswith(b"\n"):
        raise RuntimeError("Chrome 检测未完成")
    return line

def assess_browser_ips(values):
    if not isinstance(values, list) or len(values) != len(BROWSER_STATIONS) or not all(
        isinstance(value, str) and len(value) <= 64 for value in values
    ):
        raise ValueError("出口回执无效")
    ips = [address(value) for value in values]
    matches = ips.count(EXPECTED)
    unexpected = next((ip for ip in ips if ip and ip != EXPECTED), None)
    if unexpected:
        state, ip = "mismatch", unexpected
    elif matches >= 2:
        state, ip = "verified", EXPECTED
    elif matches == 1:
        state, ip = "partial", EXPECTED
    else:
        state, ip = "unknown", ""
    detail = " · ".join(label + " " + ("一致" if value == EXPECTED else "IP 不符" if value else "未取得")
                        for label, value in zip(BROWSER_STATIONS, ips))
    return state, ip, detail

def sample_chrome():
    start = gateway_status()
    if start["state"] != "verified":
        raise RuntimeError("网关尚未确认固定出口")
    token = secrets.token_hex(24)
    helper = Path(__file__).with_name("browser-exit-page.py")
    args = [*DOCKER, "-i", CONTAINER, "python3", "-Iu", "-", token]
    with helper.open("rb") as source, subprocess.Popen(
        args, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
    ) as proc:
        try:
            port = int(probe_line(proc, 8))
            if not 1024 <= port <= 65535:
                raise ValueError("检测页地址无效")
            browser_relay("startCheck", token=token, port=port, generation=start["generation"])
            pid = chrome("guard").get("pid")
            if not pid:
                raise RuntimeError("未找到受保护的 Chrome")
            if browser_firewall.check(chrome) != pid:
                raise RuntimeError("Chrome 防火墙身份在检测时变化")
            reply = json.loads(probe_line(proc, 22))
            languages = reply["acceptLanguage"]
            state, ip, station_detail = assess_browser_ips(reply["ips"])
            if state == "mismatch":
                browser_relay("finishCheck", token=token, result=state, ip=ip)
            if chrome("guard").get("pid") != pid:
                raise RuntimeError("Chrome 已重新启动")
            end = gateway_status()
            if end.get("generation") != start["generation"]:
                raise RuntimeError("检测过程中网关的出口确认已中断")
            if state != "mismatch":
                browser_relay("finishCheck", token=token, result=state, ip=ip)
        finally:
            try:browser_relay("endCheck", token=token)
            except (OSError, ValueError, RuntimeError):pass
            if proc.poll() is None:
                proc.terminate()
    safe_language = languages if isinstance(languages, str) and re.fullmatch(r"[0-9A-Za-z.,;= -]{1,160}", languages) else ""
    return pid, dict(state=state, ip=ip, at=int(time.time()),
                     detail=station_detail + " · TCP 和 UDP 的 IPv4、IPv6 直连检测均被拦截" +
                     (" · Accept-Language " + safe_language if safe_language else ""),
                     generation=end["generation"], validUntil=end["validUntil"])

def watch_chrome():
    sample = sampled_pid = None
    while not closing.is_set():
        manual = browser_requested.is_set()
        if manual:
            browser_requested.clear()
            emit("browserNetwork", state="checking")
        pid = None
        gateway = gateway_status()
        if gateway["state"] != "verified":
            sample = sampled_pid = None
            emit("browserNetwork", **gateway)
            browser_requested.wait(3)
            continue
        try:
            pid = chrome("guard").get("pid")
            if manual:
                sampled_pid, sample = sample_chrome()
                pid = sampled_pid
                gateway = gateway_status()
            if (not pid or sampled_pid != pid or
                    (sample and sample.get("generation") != gateway.get("generation"))):
                sample = None
            if gateway["state"] != "verified":
                current = gateway
            elif sample:
                current = dict(sample, validUntil=gateway["validUntil"])
            else:
                current = {"state": "untested" if pid else "closed"}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            current = {"state":"unknown", "detail":str(error)[:180]}
            sampled_pid, sample = pid, current if pid else None
            if manual:
                emit("notice", message="未能验证浏览器出口，请点击重试。")
        try:
            block = browser_relay("blockState").get("block")
            if block is not None:
                detail = current.get("detail", "") if manual else ""
                current = {"state":"quarantined", **block,
                           "detail":detail + (" · " if detail else "") + "普通浏览已停网，重新核验通过后恢复"}
                sampled_pid, sample = None, None
        except (OSError, ValueError, RuntimeError):
            current = {"state":"unknown", "detail":"未能确认 Chrome 中转状态"}
        emit("browserNetwork", **current)
        browser_requested.wait(3)

def send(proc, msg):
    if proc.poll() is None:
        proc.stdin.write((json.dumps(msg,separators=(",",":"))+"\n").encode());proc.stdin.flush()

def detach(view):
    proc=views.pop(view,None)
    if not proc:return
    try:send(proc,{"op":"detach"})
    except (OSError,ValueError):pass
    try:proc.stdin.close()
    except OSError:pass

def attach(msg):
    key=msg["view"]
    job=msg["id"]
    if not online or job not in catalog or not re.fullmatch(r"job-[0-9a-f]{12}",job):
        emit("disconnected",view=key);return
    detach(key)
    container, _ = catalog[job]
    proc=subprocess.Popen([*DOCKER,"-i",container,"aiwork-gui-pty",job,str(int(msg["cols"])),str(int(msg["rows"]))],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
    views[key]=proc
    def read():
        try:
            while True:
                data=os.read(proc.stdout.fileno(),32768)
                if not data:break
                emit("output",view=key,data=base64.b64encode(data).decode("ascii"))
        finally:
            proc.stdout.close();proc.wait()
            emit("disconnected",view=key)
    threading.Thread(target=read,daemon=True).start()

rooms_service = None

def work():
    global online, rooms_service
    while not closing.is_set():
        msg=ops.get();action=msg["op"];request=msg.get("request")
        try:
            if action=="startup":
                check(True)
                if online:refresh()
            elif action=="room":
                import brainstorm
                if rooms_service is None: rooms_service = brainstorm.Rooms()
                result = rooms_service.command(msg)
                emit("result", request=request, ok=True, **result)
            elif action=="poll":
                try:
                    check(False)
                    refresh()
                finally:poll_pending.clear()
            elif action in ("preset", "create", "restart", "rename", "end", "archive", "restore", "duplicate", "folder", "copyOutput"):
                if action in ("create", "preset", "restart", "duplicate") and not online:
                    raise RuntimeError("请先重新连接工作台")
                if action in ("preset", "create"):
                    project = msg["project"].strip() or "main"
                    if action == "preset":
                        result = project_runtime.session_preset(project)
                    else:
                        result = project_runtime.new_session(project, msg["tool"], msg["title"])
                else:
                    container, row = catalog[msg["id"]]
                    if action == "duplicate":
                        result = project_runtime.new_session(row["project"], row["tool"], source=(container,row))
                    elif action == "folder":
                        folder = project_runtime.session_folder(container, row)
                        run(["/usr/bin/open", "-a", "Finder", str(folder)])
                        result = {}
                    elif action == "copyOutput":
                        output = subprocess.check_output([*DOCKER, container, "tmux", "-L", "default", "capture-pane",
                                                          "-p", "-J", "-S", "-", "-t", "="+msg["id"]+":"], timeout=10)
                        subprocess.run(["/usr/bin/pbcopy"], input=output, check=True, timeout=10)
                        result = {}
                    else:
                        if action == "rename":
                            ident = project_runtime.rename_session(container, msg["id"], msg["title"])
                        else:
                            ident = project_runtime.sessions(container, action, msg["id"])
                        result = {"id":ident}
                        if action=="end" and row["scope"]=="isolated":
                            project_runtime.docker("rm","-f",container)
                refresh();emit("result",request=request,ok=True,**result)
            elif action == "exportPatch":
                container, row = catalog[msg["id"]]
                if row["scope"] not in ("isolated","protected"):
                    raise ValueError("仅能导出保护副本中的改动")
                path = project_runtime.export(row["project"],container)
                emit("result",request=request,ok=True,name=path.name)
            elif action=="attach":attach(msg)
            elif action=="detach":detach(msg["view"])
            elif action=="android":
                service = "gui/" + str(os.getuid()) + "/local.dow.android.viewer"
                pid = run(["/bin/launchctl", "kickstart", "-p", service]).strip()
                if pid.isdecimal():
                    run([str(Path.home() / "Library/Application Support/Dow Android/focus-viewer"), pid], timeout=15)
                emit("result",request=request,ok=True)
            elif action=="browser":
                tool = msg.get("tool","claude")
                args=["browser-open","--profile",tool if tool in ("agy","claude","codex") else "general",msg.get("url") or "--show"]
                run([*DOCKER,CONTAINER,*args],timeout=45)
                emit("result",request=request,ok=True)
        except (ValueError,IndexError,KeyError,RuntimeError,OSError,subprocess.SubprocessError,StopIteration) as e:
            if action=="poll":
                if online:
                    online=False;emit("network",online=False,ip="",state="unknown")
            elif action=="attach":emit("disconnected",view=msg.get("view",""))
            elif request:emit("result",request=request,ok=False,message=str(e))
            else:emit("notice",message=str(e))

def poll():
    while not closing.wait(4):
        if not poll_pending.is_set():
            poll_pending.set();ops.put(dict(op="poll"))

if __name__=="__main__":
    clicks, mac_clicks = socket.socketpair(type=socket.SOCK_DGRAM)
    mac_process=subprocess.Popen([sys.executable,"-I",str(Path(__file__).with_name("mac-commands.py")),str(mac_clicks.fileno())],
                                 pass_fds=(mac_clicks.fileno(),), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    mac_clicks.close()
    review_lock = threading.Lock()
    pending_review = ""
    def watch_mac_reviews():
        global pending_review
        try:
            for line in mac_process.stdout:
                value = line.strip().decode("ascii")
                with review_lock:
                    pending_review = value if re.fullmatch(r"[0-9a-f]{32}", value) else ""
                    emit("macReview", ticket=pending_review)
        finally:
            with review_lock:
                pending_review = ""
                emit("macReview", ticket="")
    threading.Thread(target=watch_mac_reviews,daemon=True).start()
    def watch_windows_device():
        spec=importlib.util.spec_from_file_location("windows_device", Path(__file__).with_name("windows_device.py"))
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.serve_device(closing, emit)
    threading.Thread(target=watch_windows_device,daemon=True).start()
    threading.Thread(target=work,daemon=True).start()
    threading.Thread(target=poll,daemon=True).start()
    threading.Thread(target=watch_chrome,daemon=True).start()
    try:
        for line in sys.stdin.buffer:
            try:
                msg=json.loads(line);action=msg["op"]
                if action == "startup":
                    with review_lock:
                        emit("macReview",ticket=pending_review)
                if action == "reviewMac":
                    with review_lock:
                        if pending_review and msg.get("ticket") == pending_review:
                            clicks.send(pending_review.encode())
                            pending_review = ""
                            emit("macReview",ticket="")
                elif action in ("input","resize"):
                    proc=views.get(msg["view"])
                    if proc:send(proc,msg)
                elif action=="checkBrowser":browser_requested.set()
                elif action in ("room","startup","create","restart","rename","end","archive","restore","duplicate","folder","copyOutput","preset","attach","detach","browser","android","exportPatch"):
                    ops.put(msg)
            except (OSError,ValueError,KeyError):pass
    finally:
        closing.set()
        browser_requested.set()
        mac_process.stdin.close()
        for view in list(views):detach(view)
