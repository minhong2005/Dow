"""Open links normally in the user's Chrome, under the workbench firewall identity."""
import contextlib
import grp
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import time
from urllib.parse import urlsplit

STATE = Path.home() / "Library/Application Support/dow-native"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WINDOW = str(STATE / "chrome-window")
PROXY = "--proxy-server=http://127.0.0.1:39175"
NET_FLAGS = [PROXY, "--proxy-bypass-list=<-loopback>",
             "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
             "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"]


def guard_ok():
    gid = grp.getgrnam("_dowchrome").gr_gid
    if os.getuid() == 0 or (os.getgid(), os.getegid()) != (gid, gid):
        raise RuntimeError("Chrome 启动身份未通过核验")
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(5)
        conn.connect(str(STATE/"relay.sock"))
        conn.sendall(b'{"action":"health"}\n')
        if not json.loads(conn.makefile("rb").readline(65536)).get("ok"):
            raise RuntimeError("固定出口尚未就绪")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1",39176)) == 0:
            raise RuntimeError("防火墙未拦住测试连接，已拒绝打开 Chrome")


def shared_pid():
    # Refuse to forward to an existing regular instance, including a Dock launch
    # that won the race for the default profile. An open-URL event targets one PID.
    table = subprocess.check_output(["/bin/ps","-axo","pid=,uid=,gid=,rgid=,comm="],text=True)
    matches = []
    executables = {CHROME, os.path.realpath(CHROME)}
    for line in table.splitlines():
        fields = line.strip().split(None,4)
        if len(fields)!=5 or int(fields[1])!=os.getuid():continue
        if fields[4] not in executables:continue
        pid, _, group, real_group, _ = fields
        if int(group)!=os.getegid() or int(real_group)!=os.getgid():
            raise RuntimeError("当前 Chrome 是从普通入口启动的。请用 ⌘Q 退出 Chrome，再从 Dow 打开，以启用固定出口。")
        args = subprocess.run(["/bin/ps","-ww","-p",pid,"-o","args="],text=True,capture_output=True)
        if args.returncode:continue
        if PROXY not in args.stdout.split() or "--user-data-dir=" in args.stdout or "--remote-debugging" in args.stdout:
            raise RuntimeError("另一个 Chrome 档案正在使用工作台网络，请先正常退出该实例。")
        matches.append(int(pid))
    if len(matches)>1:
        raise RuntimeError("等待 Chrome 完成启动后再重试。")
    return matches[0] if matches else None


class Chrome:
    def __init__(self):
        self.process = None

    def start(self):
        guard_ok()
        if self.process is not None and self.process.poll() is not None:
            self.process = None
        pid = shared_pid()
        if pid:
            return pid
        restore = STATE/"restore-once"
        args = [CHROME,*NET_FLAGS]
        if restore.exists():args.append("--restore-last-session")
        # No custom user-data-dir or profile selector: Chrome uses its real
        # profile and its own normal macOS Keychain access and sync settings.
        self.process = subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(80):
            if self.process.poll() is not None:break
            pid = shared_pid()
            if pid==self.process.pid:
                probe = subprocess.run([WINDOW,"ready",str(pid)],capture_output=True)
                if probe.returncode==0:
                    restore.unlink(missing_ok=True)
                    return pid
            time.sleep(.15)
        raise RuntimeError("共享 Chrome 尚未完成启动，请稍后重试。")

    def open(self,url):
        if url is not None:
            parts = urlsplit(url)
            if parts.scheme not in ("http","https") or not parts.hostname or len(url)>30000 or any(ord(c)<32 for c in url):
                raise ValueError("链接无效")
        pid=self.start()
        proc=subprocess.run([WINDOW,"show" if url is None else "open",str(pid)],input=url or "",
                            capture_output=True,text=True,timeout=12)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "Chrome 未能打开网页")
        return {"ok":True}

chrome = Chrome()


class Driver(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            req=json.loads(self.rfile.readline(65536))
            action=req.get("action")
            if action in ("open","show"):
                out=chrome.open(req["url"] if action=="open" else None)
            elif action=="guard":
                guard_ok()
                out={"ok":True,"uid":os.getuid(),"gid":os.getegid(),"pid":shared_pid()}
            else:raise ValueError()
        except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
            out={"ok":False,"error":str(error) if isinstance(error,RuntimeError) else "共享 Chrome 或网络保护尚未就绪"}
        with contextlib.suppress(OSError):self.wfile.write(json.dumps(out).encode()+b"\n")

if __name__=="__main__":
    os.umask(0o077)
    sock=STATE/"driver.sock"
    sock.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(sock),Driver) as server:
        server.serve_forever()
