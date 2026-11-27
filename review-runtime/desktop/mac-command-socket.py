"""Linux side of Dow's private docker-exec pipe. This process cannot grant approval."""
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import sys

os.umask(0o077)
root=Path.home()/".local/state/aiwork"
root.mkdir(parents=True,exist_ok=True)
with (root/"mac-command.lock").open("a") as lock, socket.socket(socket.AF_UNIX) as listener:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    path=root/"mac-command.sock"
    path.unlink(missing_ok=True)
    listener.bind(str(path));listener.listen(4)
    while True:
        ready,_,_=select.select([sys.stdin.buffer,listener],[],[])
        if sys.stdin.buffer in ready:break
        conn,_=listener.accept()
        with conn:
            conn.settimeout(6)
            try:
                data=conn.makefile("rb").readline(32769)
                if len(data)>32768 or not data.endswith(b"\n"):continue
                req=json.loads(data)
                if not isinstance(req,dict) or set(req) not in (
                    {"cwd","command"}, {"windowsAction"}, {"windowsAction","company"}, {"windowsAction","args"}
                ):continue
                print(json.dumps(req),flush=True)
                response=sys.stdin.buffer.readline(1048576)
                while response == b'{"stage":"connected"}\n':
                    # Our client sends one request, then waits without closing its socket.
                    disconnected = bool(select.select([conn], [], [], 0)[0])
                    print('{"connected":false}' if disconnected else '{"connected":true}', flush=True)
                    response=sys.stdin.buffer.readline(1048576)
                if not response:break
                conn.sendall(response)
            except (ValueError,OSError):pass
