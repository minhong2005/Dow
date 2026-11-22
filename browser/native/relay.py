"""Local proxy and link handoff. Only the isolated workspace makes external connections."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

STATE = Path.home() / "Library/Application Support/dow-native"
DOCKER = ("/opt/homebrew/bin/docker", "--context", "colima-dow", "exec")
EXPECTED = (Path.home() / ".config/dow/expected-ip").read_text().strip()
LOCAL = {"localhost", "127.0.0.1", "::1"}
CANARY = b"aiwork-local-firewall-test\n"


class Relay:
    def __init__(self):
        self.valid_until = 0
        self.connections = set()

    async def monitor(self):
        while True:
            proc = await asyncio.create_subprocess_exec(*DOCKER, "aiwork-tools", "exit-ip",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                data, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
                valid = proc.returncode == 0 and data.decode().strip() == EXPECTED
            except (asyncio.TimeoutError, UnicodeError):
                valid = False
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
            if valid:
                self.valid_until = time.monotonic() + 23
            else:
                self.valid_until = 0
                for task in tuple(self.connections):
                    task.cancel()
            await asyncio.sleep(9)

    async def proxy(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        proc = None
        upload = None
        try:
            if time.monotonic() >= self.valid_until:
                raise ValueError("offline")
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
            lines = header[:-4].split(b"\r\n")
            method, target, ver = lines[0].decode("ascii").split(" ")
            connect = method == "CONNECT"
            dest = urlsplit("//" + target if connect else target)
            if not dest.hostname or (not connect and dest.scheme != "http") or dest.username:
                raise ValueError("invalid target")
            port = dest.port or (443 if connect else 80)
            host = dest.hostname.rstrip(".").lower()
            direct = host in LOCAL or host.endswith(".localhost")
            if direct and port in (5961, 6091, 7890, 7892, 9292):
                raise ValueError("reserved")
            peer = "TCP4:127.0.0.1:" + str(port if direct else 7890)
            proc = await asyncio.create_subprocess_exec(*DOCKER, "-i", "aiwork-tools",
                "socat", "STDIO", peer, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            if direct and connect:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                if direct:
                    path = dest.path or "/"
                    if dest.query:
                        path += "?" + dest.query
                    lines[0] = (method + " " + path + " " + ver).encode("ascii")
                    lines = [s for s in lines if not s.lower().startswith(
                        (b"proxy-authorization:", b"proxy-connection:", b"connection:"))]
                    lines.append(b"Connection: close")
                    header = b"\r\n".join(lines) + b"\r\n\r\n"
                proc.stdin.write(header)
                await proc.stdin.drain()
            async def copy_up():
                try:
                    while True:
                        chunk = await reader.read(65536)
                        if not chunk:
                            return
                        proc.stdin.write(chunk)
                        await proc.stdin.drain()
                finally:
                    proc.stdin.close()
            upload = asyncio.create_task(copy_up())
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ValueError, OSError, EOFError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            if proc is None:
                writer.write(b"HTTP/1.1 503 Offline\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
        finally:
            self.connections.discard(task)
            writer.close()
            if upload:
                upload.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError): await upload
            if proc and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError): proc.terminate()
                await proc.wait()

    async def control(self, reader, writer):
        try:
            req = json.loads(await asyncio.wait_for(reader.readline(), timeout=4))
            if req == {"action":"health"}:
                check, client = await asyncio.wait_for(asyncio.open_connection("127.0.0.1",39176),2)
                try:
                    live = await asyncio.wait_for(check.readline(),2) == CANARY
                finally:
                    client.close()
                result = {"ok":live and time.monotonic() < self.valid_until}
            else:
                result = await self.open(req)
        except Exception:
            result = {"ok": False, "error": "浏览器连接未就绪"}
        writer.write(json.dumps(result).encode() + b"\n")
        await writer.drain()
        writer.close()

    async def open(self, req):
        if time.monotonic() >= self.valid_until:
            return {"ok":False,"error":"指定网络出口尚未就绪"}
        r, w = await asyncio.open_unix_connection(str(STATE/"driver.sock"))
        try:
            w.write(json.dumps(req).encode() + b"\n")
            await w.drain()
            return json.loads(await asyncio.wait_for(r.readline(), timeout=30))
        finally:
            w.close()

    async def links(self):
        while True:
            proc = await asyncio.create_subprocess_exec(*DOCKER, "-i", "aiwork-tools",
                "/usr/local/bin/aiwork-native-requests", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line: break
                    try:
                        req = json.loads(line)
                        if req.get("action") == "show":
                            res = await self.open({"action":"show"})
                        else:
                            res = await self.open({"action":"open","url":req["url"]})
                    except Exception:
                        res = {"ok":False, "error":"专用 Chrome 尚未就绪"}
                    proc.stdin.write(json.dumps(res).encode()+b"\n")
                    await proc.stdin.drain()
            except (OSError, ValueError):
                pass
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError): proc.terminate()
                await proc.wait()
            await asyncio.sleep(3)


async def canary(reader, writer):
    writer.write(CANARY)
    await writer.drain()
    writer.close()


async def main():
    os.umask(0o077)
    relay = Relay()
    socket_path = STATE/"relay.sock"
    socket_path.unlink(missing_ok=True)
    proxy = await asyncio.start_server(relay.proxy, "127.0.0.1", 39175)
    test = await asyncio.start_server(canary, "127.0.0.1", 39176)
    control = await asyncio.start_unix_server(relay.control, path=str(socket_path))
    async with proxy, test, control:
        await asyncio.gather(relay.monitor(), relay.links())


if __name__ == "__main__":
    asyncio.run(main())
