"""Bring the protected, native Google Chrome window to the front."""
import json
from pathlib import Path
import socket
import sys
import time

path = Path.home()/"Library/Application Support/dow-native/relay.sock"
for attempt in range(5):
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(30)
            conn.connect(str(path))
            conn.sendall(b'{"action":"show"}\n')
            reply = json.loads(conn.makefile("rb").readline())
    except (ValueError,OSError):
        reply = {"ok": False, "error": "工作台 Chrome 的网络保护尚未就绪"}
    if reply.get("ok"):
        sys.exit(0)
    if attempt<4: time.sleep(2)
sys.exit(reply.get("error","工作台 Chrome 未能打开"))
