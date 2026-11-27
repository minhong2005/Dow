"""Host side of the Dow command bridge. Only a native Dow confirmation authorizes execution."""
import contextlib
import json
import os
from pathlib import Path
import select
import selectors
import secrets
import signal
import socket
import subprocess
import sys
import time
import unicodedata

RES = Path(__file__).resolve().parent
HOME = Path.home()
DOCKER = ["/opt/homebrew/bin/docker","--context","colima-dow","exec","-i","aiwork-tools"]
LIMIT=98304

def healthy():
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(4);s.connect(str(HOME/"Library/Application Support/aiwork-native/relay.sock"))
            s.sendall(b'{"action":"health"}\n')
            return json.loads(s.makefile("rb").readline(8192)).get("ok") is True
    except (ValueError,OSError):return False

def captured(command,cwd):
    proxy="http://127.0.0.1:39175"
    env={"HOME":str(HOME),"USER":HOME.name,"LOGNAME":HOME.name,"LANG":"en_US.UTF-8",
         "PATH":":".join([str(HOME/".local/bin"),str(HOME/"node-local/bin"),"/opt/homebrew/bin","/usr/local/bin","/usr/bin","/bin","/usr/sbin","/sbin"])}
    env.update({key:proxy for key in ("http_proxy","https_proxy","all_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY")})
    args=["/usr/bin/sandbox-exec","-f",str(RES/"mac-command.sb"),"/bin/zsh","-f","-c",command]
    p=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    data=bytearray();truncated=False;end=time.monotonic()+100
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout,selectors.EVENT_READ)
            while time.monotonic()<end:
                if not sel.select(max(0,end-time.monotonic())):break
                chunk=os.read(p.stdout.fileno(),16384)
                if not chunk:
                    code=p.wait(timeout=max(.1,end-time.monotonic()))
                    return {"code":code,"output":data.decode(errors="replace")+("\n[后续输出已截断]" if truncated else "")}
                room=LIMIT-len(data);data.extend(chunk[:room]);truncated|=len(chunk)>room
        return {"code":124,"output":data.decode(errors="replace")+"\n[本机命令超时]"}
    finally:
        with contextlib.suppress(ProcessLookupError):os.killpg(p.pid,signal.SIGKILL)
        p.stdout.close();p.wait()

def still_requested(child):
    if select.select([sys.stdin], [], [], 0)[0]:
        return False
    child.stdin.write(b'{"stage":"connected"}\n')
    child.stdin.flush()
    ready = select.select([sys.stdin, child.stdout], [], [], 3)[0]
    if sys.stdin in ready or child.stdout not in ready:
        raise BrokenPipeError("本机命令连接已关闭")
    return child.stdout.readline(80) == b'{"connected":true}\n'

def windows_request(request):
    action = request["windowsAction"]
    if action == "window" and set(request) == {"windowsAction","args"}:
        result = subprocess.run([sys.executable,"-I",str(RES/"windows_device.py"),"window"],
                                input=json.dumps(request["args"]),capture_output=True,text=True,timeout=24)
        return {"code":result.returncode,"output":(result.stdout or result.stderr).strip()}
    if action in ("status", "result") and set(request) == {"windowsAction"}:
        args = [action]
    elif action == "lookup" and set(request) == {"windowsAction", "company"} and isinstance(request["company"], str):
        args = [action, "--", request["company"]]
    else:
        raise ValueError("Windows 工具请求无效。")
    # The device CLI enforces the same lookup scope and online check as the Mac entry point.
    result = subprocess.run([sys.executable, "-I", str(RES/"windows_device.py"), *args],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    return {"code": result.returncode, "output": (result.stdout or result.stderr).strip()}

def review_command(review, child, clicks):
    # Only the Mac UI receives this ticket. Opening the review cannot approve it.
    ticket = secrets.token_hex(16)
    deadline = time.monotonic() + 140
    if not still_requested(child):
        return False
    print(ticket, flush=True)
    try:
        while time.monotonic() < deadline:
            ready = select.select([sys.stdin, clicks], [], [], min(2, max(0, deadline-time.monotonic())))[0]
            if sys.stdin in ready or not still_requested(child):
                return False
            if clicks in ready and clicks.recv(128) == ticket.encode():
                break
        else:
            return False
    finally:
        print("-", flush=True)
    if time.monotonic() >= deadline:
        return False
    with subprocess.Popen([str(RES.parent/"MacOS/Dow"), "--confirm-mac-command"],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as prompt:
        try:
            prompt.stdin.write(review)
            prompt.stdin.close()
            while time.monotonic() < deadline:
                try:
                    return prompt.wait(timeout=min(2, max(0, deadline-time.monotonic()))) == 0
                except subprocess.TimeoutExpired:
                    if not still_requested(child):
                        break
            return False
        finally:
            if prompt.poll() is None:
                prompt.kill()


def execute(request, child, clicks):
    if "windowsAction" in request:
        return windows_request(request)
    command=request.get("command");directory=request.get("cwd")
    if not isinstance(command,str) or not isinstance(directory,str) or not command.strip():
        raise ValueError("命令或目录无效")
    if any(unicodedata.category(c) in ("Cc","Cf") and c not in "\n\t" for c in command) or any(unicodedata.category(c) in ("Cc","Cf") for c in directory):
        raise ValueError("命令或目录包含不可见的控制字符")
    cwd=Path(directory).expanduser()
    if not cwd.is_absolute():raise ValueError("请输入 Mac 上的绝对路径")
    cwd=cwd.resolve(strict=True)
    if not cwd.is_dir():raise ValueError("目录不存在")
    if not healthy():raise RuntimeError("固定网络出口未就绪")
    review=json.dumps({"cwd":str(cwd),"command":command},ensure_ascii=False).encode()
    if not review_command(review, child, clicks):
        return {"code":1,"output":"Mac 命令未执行。请在 Dow 底部查看待确认的命令，取消或超时后需重新发起。"}
    if not healthy():raise RuntimeError("固定网络出口未就绪")
    if not still_requested(child):
        return {"code":1,"output":"原会话已断开，本机命令已取消。"}
    return captured(command,cwd)

def main(clicks):
    linux=(RES/"mac-command-socket.py").read_text()
    # Dow holds stdin open. An app restart releases the singleton guest socket.
    with selectors.DefaultSelector() as sel:
        sel.register(sys.stdin,selectors.EVENT_READ)
        while not sel.select(0):
            child=subprocess.Popen([*DOCKER,"python3","-I","-u","-c",linux],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            try:
                sel.register(child.stdout,selectors.EVENT_READ)
                while True:
                    ready=sel.select()
                    if any(k.fileobj is sys.stdin for k,_ in ready):return
                    line=child.stdout.readline(32769)
                    if not line:break
                    try:r=execute(json.loads(line),child,clicks)
                    except BrokenPipeError:break
                    except (ValueError,KeyError,TypeError,OSError,RuntimeError,subprocess.SubprocessError) as e:r={"code":1,"output":str(e)}
                    child.stdin.write(json.dumps(r).encode()+b"\n");child.stdin.flush()
            except (BrokenPipeError,OSError): pass
            finally:
                with contextlib.suppress(Exception):sel.unregister(child.stdout)
                with contextlib.suppress(OSError):child.stdin.close()
                child.stdout.close()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:child.terminate();child.wait()
            if sel.select(2):break

if __name__=="__main__":
    with socket.socket(fileno=int(sys.argv[1])) as clicks:
        main(clicks)
