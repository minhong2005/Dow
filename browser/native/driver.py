"""Open links normally in the user's Chrome, under the workbench firewall identity."""
import contextlib
import grp
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
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


def isolated_profile():
    try:
        mode = (STATE / "profile-mode").read_text().strip()
    except FileNotFoundError:
        mode = "shared"
    if mode not in ("shared", "isolated"):
        raise RuntimeError("Chrome 档案模式无效")
    return mode == "isolated"


PROFILE = STATE / "Dow Chrome"
PROFILE_ARG = "--user-data-dir=" + str(PROFILE)


def browser_pid():
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
        args = subprocess.run(["/bin/ps","-ww","-p",pid,"-o","args="],text=True,capture_output=True)
        if args.returncode:continue
        command = " " + args.stdout.strip() + " "
        if isolated_profile():
            if " " + PROFILE_ARG + " " not in command:
                continue
            if command.count("--user-data-dir=") != 1:
                raise RuntimeError("Chrome 档案参数不一致")
        elif "--user-data-dir=" in command:
            continue
        if int(group)!=os.getegid() or int(real_group)!=os.getgid():
            raise RuntimeError("所选 Chrome 档案已在普通入口打开，请先完全退出该实例。")
        if any(" " + flag + " " not in command for flag in NET_FLAGS) or "--remote-debugging" in command:
            raise RuntimeError("Chrome 网络参数未通过核验")
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
        pid = browser_pid()
        if pid:
            return pid
        restore = STATE/"restore-once"
        args = [CHROME,*NET_FLAGS]
        if isolated_profile():
            PROFILE.mkdir(mode=0o700,exist_ok=True)
            info = PROFILE.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError("Dow 专用 Chrome 资料目录权限无效")
            args.extend([PROFILE_ARG, "--disable-sync", "--no-first-run", "--no-default-browser-check"])
        elif restore.exists():
            args.append("--restore-last-session")
        self.process = subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(80):
            if self.process.poll() is not None:break
            pid = browser_pid()
            if pid==self.process.pid:
                probe = subprocess.run([WINDOW,"ready",str(pid)],capture_output=True)
                if probe.returncode==0:
                    if not isolated_profile():restore.unlink(missing_ok=True)
                    return pid
            time.sleep(.15)
        raise RuntimeError("Chrome 尚未完成启动，请稍后重试。")

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
                out={"ok":True,"uid":os.getuid(),"gid":os.getegid(),"pid":browser_pid()}
            else:raise ValueError()
        except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
            out={"ok":False,"error":str(error) if isinstance(error,RuntimeError) else "Chrome 或网络保护尚未就绪"}
        with contextlib.suppress(OSError):self.wfile.write(json.dumps(out).encode()+b"\n")

if __name__=="__main__":
    os.umask(0o077)
    sock=STATE/"driver.sock"
    sock.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(sock),Driver) as server:
        server.serve_forever()
