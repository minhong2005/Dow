"""Forward browser opens under the identity assigned to this socket by the host."""
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
from urllib.parse import urlsplit


class BrowserRequest(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(8)
        try:
            data = json.loads(self.rfile.readline(65536))
            action = data["action"]
            if action not in ("open", "show"):
                raise ValueError("未知浏览器操作")
            profile = self.server.profile
            if data.get("profile") not in (None, "general", profile):
                raise ValueError("当前工作环境无权使用其他浏览器")
            message = {"action": action, "profile": profile}
            if action == "open":
                url = data["url"]
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 30000 or any(ord(c)<32 for c in url):
                    raise ValueError("网址无效")
                message["url"] = url
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(35)
                client.connect("/upstream/native.sock")
                client.sendall(json.dumps(message).encode() + b"\n")
                reply = json.loads(client.makefile("rb").readline(65536))
        except (KeyError, TypeError, ValueError, OSError) as error:
            reply = {"ok": False, "error": str(error)}
        self.wfile.write(json.dumps(reply).encode()+b"\n")


if __name__ == "__main__":
    os.umask(0o077)
    path = Path("/endpoint/native.sock")
    path.unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(str(path), BrowserRequest) as server:
        server.profile = sys.argv[1]
        server.serve_forever()
