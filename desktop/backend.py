"""Private stdio bridge between the Mac window and the isolated workbench."""
import base64
import ipaddress
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

DOCKER = ["/opt/homebrew/bin/docker","--context","colima-dow","exec"]
CONTAINER = "aiwork-tools"
LAUNCHER = str(Path(__file__).resolve().with_name("aiwork"))
EXPECTED = (Path.home() / ".config/dow/expected-ip").read_text().strip()
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

def api(action,*args):
    return json.loads(run([*DOCKER,CONTAINER,"aiwork-sessions","api",action,*args]))

def refresh():
    global catalog
    rows=api("list")
    catalog={r["id"]:r for r in rows}
    emit("sessions",rows=rows)

def address(raw):
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""

def check(boot):
    global online
    try:
        value=run([LAUNCHER,"--ready"] if boot else [*DOCKER,CONTAINER,"exit-ip"],timeout=150 if boot else 35)
        ip=address(value.splitlines()[-1]) if value else ""
    except (OSError,RuntimeError,subprocess.TimeoutExpired):
        ip=""
    online=ip==EXPECTED
    emit("network",online=online,ip=ip,state="verified" if online else "mismatch" if ip else "unknown",at=int(time.time()))
    if boot and not online:emit("notice",message="固定出口未就绪，请检查工作台代理后点「终端出口」。")

def chrome(action, **fields):
    path = Path.home() / "Library/Application Support/dow-native/driver.sock"
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(30 if action == "open" else 7)
        conn.connect(str(path))
        conn.sendall(json.dumps(dict(action=action, **fields)).encode() + b"\n")
        with conn.makefile("rb") as response:
            result = json.loads(response.readline(8192))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Chrome 网络保护未就绪")
    return result

def probe_line(proc, timeout):
    if not select.select([proc.stdout], [], [], timeout)[0]:
        raise RuntimeError("Chrome 检测超时")
    line = proc.stdout.readline(512)
    if not line.endswith(b"\n"):
        raise RuntimeError("Chrome 检测未完成")
    return line

def sample_chrome():
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
            chrome("open", url=f"http://dow-exit-check.localhost:{port}/{token}")
            pid = chrome("guard").get("pid")
            if not pid:
                raise RuntimeError("未找到受保护的 Chrome")
            values = json.loads(probe_line(proc, 22))
            if chrome("guard").get("pid") != pid:
                raise RuntimeError("Chrome 已重新启动")
        finally:
            if proc.poll() is None:
                proc.terminate()
    if not isinstance(values, list) or len(values) != 3 or not all(isinstance(v, str) for v in values):
        raise ValueError("出口回执无效")
    ips = [ip for v in values if (ip := address(v))]
    unexpected = next((ip for ip in ips if ip != EXPECTED), None)
    if unexpected:
        state, ip = "mismatch", unexpected
    elif len(ips) >= 2:
        state, ip = "verified", ips[0]
    elif ips:
        state, ip = "partial", ips[0]
    else:
        state, ip = "unknown", ""
    return pid, dict(state=state, ip=ip, at=int(time.time()))

def watch_chrome():
    sample = sampled_pid = None
    while not closing.is_set():
        manual = browser_requested.is_set()
        if manual:
            browser_requested.clear()
            emit("browserNetwork", state="checking")
        pid = None
        try:
            pid = chrome("guard").get("pid")
            if manual:
                sampled_pid, sample = sample_chrome()
                pid = sampled_pid
            if not pid or sampled_pid != pid:
                sample = None
            current = sample or {"state": "untested" if pid else "closed"}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            current = {"state":"unknown", "detail":str(error)[:180]}
            sampled_pid, sample = pid, current if pid else None
            if manual:
                emit("notice", message="未能验证浏览器出口，请点击重试。")
        emit("browserNetwork", **current)
        browser_requested.wait(8)

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
    proc=subprocess.Popen([*DOCKER,"-i",CONTAINER,"aiwork-gui-pty",job,str(int(msg["cols"])),str(int(msg["rows"]))],
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

def work():
    global online
    while not closing.is_set():
        msg=ops.get();action=msg["op"];request=msg.get("request")
        try:
            if action=="startup":
                check(True)
                if online:refresh()
            elif action=="poll":
                try:
                    if msg.get("network"):check(False)
                    refresh()
                finally:poll_pending.clear()
            elif action=="preset":
                if not online:raise RuntimeError("请先重新连接工作台")
                ids=api("preset",msg["project"])
                refresh();emit("result",request=request,ok=True,ids=ids)
            elif action in ("create","restart","rename","end"):
                if not online:raise RuntimeError("请先重新连接工作台")
                params={"create":["tool","project","title"],"restart":["id"],"rename":["id","title"],"end":["id"]}[action]
                ident=api(action,*[msg[k] for k in params])
                refresh();emit("result",request=request,ok=True,id=ident)
            elif action=="attach":attach(msg)
            elif action=="detach":detach(msg["view"])
            elif action=="browser":
                args=["browser-open",msg.get("url") or "--show"]
                run([*DOCKER,CONTAINER,*args],timeout=45)
                emit("result",request=request,ok=True)
        except (ValueError,IndexError,KeyError,RuntimeError,OSError,subprocess.TimeoutExpired) as e:
            if action=="poll":
                if online:
                    online=False;emit("network",online=False,ip="",state="unknown")
            elif action=="attach":emit("disconnected",view=msg.get("view",""))
            elif request:emit("result",request=request,ok=False,message=str(e))
            else:emit("notice",message=str(e))

def poll():
    count=0
    while not closing.wait(6):
        count+=1
        if not poll_pending.is_set():
            poll_pending.set();ops.put(dict(op="poll",network=(count%8==0 or not online)))

if __name__=="__main__":
    mac_process=subprocess.Popen([sys.executable,"-I",str(Path(__file__).with_name("mac-commands.py"))],
                                 stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    threading.Thread(target=work,daemon=True).start()
    threading.Thread(target=poll,daemon=True).start()
    threading.Thread(target=watch_chrome,daemon=True).start()
    try:
        for line in sys.stdin.buffer:
            try:
                msg=json.loads(line);action=msg["op"]
                if action in ("input","resize"):
                    proc=views.get(msg["view"])
                    if proc:send(proc,msg)
                elif action=="checkBrowser":browser_requested.set()
                elif action in ("startup","create","restart","rename","end","preset","attach","detach","browser"):
                    ops.put(msg)
            except (OSError,ValueError,KeyError):pass
    finally:
        closing.set()
        browser_requested.set()
        mac_process.stdin.close()
        for view in list(views):detach(view)
