"""Keep the workbench socket closed until the proxy's public exit is confirmed."""

import asyncio
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import time


EXPECTED = ipaddress.IPv4Address(os.environ["DOW_EXPECTED_IP"])
CHECKS = (
    ("ipify", "https://api.ipify.org/", False),
    ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace", True),
    ("aws", "https://checkip.amazonaws.com/", False),
    ("ifconfig", "https://ifconfig.me/ip", False),
)
REQUIRED = 2
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890
SOCKET = Path("/link/out.sock")
STATUS_SOCKET = Path("/link/status.sock")
VALID_SECONDS = 15
# Start sooner so a slow detector has more time without extending the
# maximum 12-second window between confirmation and the next decision.
PROBE_SECONDS = 8
ROUND_SECONDS = 9
READY_PAUSE = 3


async def observe(name, url, trace):
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-qfsS", "--proxy", f"http://{PROXY_HOST}:{PROXY_PORT}", "--noproxy", "",
            "--max-time", str(PROBE_SECONDS), "--max-filesize", "4096", "--header", "Cache-Control: no-cache",
            "--write-out", "\n%{http_connect} %{http_code}",
            f"{url}?dow={secrets.token_hex(8)}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return name, None, "worker_failed"
    try:
        data, _ = await proc.communicate()
        body, _, stats = data.rpartition(b"\n")
        connect, _, http = stats.partition(b" ")
        if proc.returncode != 0:
            if proc.returncode == 28:
                reason = "proxy_timeout" if connect == b"000" else "response_timeout"
            elif http.isdigit() and http != b"000":
                reason = "http_" + http.decode("ascii")
            else:
                reason = f"curl_{proc.returncode}"
            return name, None, reason
        value = body.decode("ascii").strip()
        if trace:
            value = next((line[3:] for line in value.splitlines() if line.startswith("ip=")), "")
        observed = ipaddress.ip_address(value)
        return name, observed, "ok" if observed == EXPECTED else "mismatch"
    except (UnicodeError, ValueError):
        return name, None, "invalid_body"
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


async def confirmed():
    checks = [asyncio.create_task(observe(*check)) for check in CHECKS]
    results = {}
    matches = 0
    try:
        for result in asyncio.as_completed(checks, timeout=ROUND_SECONDS):
            name, value, assessment = await result
            results[name] = assessment
            if value is not None:
                if value != EXPECTED:
                    return "mismatch", results
                matches += 1
    except asyncio.TimeoutError:
        for name, _, _ in CHECKS:
            results.setdefault(name, "worker_timeout")
    finally:
        for task in checks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*checks, return_exceptions=True)
    return ("verified" if matches >= REQUIRED else "probe_failed"), results


class Gate:
    def __init__(self, host=PROXY_HOST, port=PROXY_PORT):
        self.host = host
        self.port = port
        self.valid_until = 0.0
        self.state = "checking"
        self.generation = ""
        self.sampled_at = 0
        self.connections = set()
        self.probes = {}

    def available(self):
        return time.monotonic() < self.valid_until

    def block(self, reason):
        if self.state != reason:
            print(f"Dow gateway: {reason}, closing clients", flush=True)
        self.state = reason
        self.valid_until = 0.0
        for connection in tuple(self.connections):
            connection.cancel()

    async def check_exit(self):
        while True:
            result, probes = await confirmed()
            if probes != self.probes and (any(code != "ok" for code in probes.values())
                                         or any(code != "ok" for code in self.probes.values())):
                print("Dow gateway: probes " + " ".join(f"{name}={code}"
                      for name, code in sorted(probes.items())), flush=True)
            self.probes = probes
            if result == "verified":
                if not self.available():
                    print("Dow gateway: fixed exit confirmed by independent probes", flush=True)
                    self.generation = secrets.token_hex(12)
                self.state = result
                self.sampled_at = int(time.time())
                self.valid_until = time.monotonic() + VALID_SECONDS
            else:
                self.block(result)
            await asyncio.sleep(READY_PAUSE if result == "verified" else 2)

    async def expire(self):
        while True:
            if self.valid_until and not self.available():
                self.block("expired")
            await asyncio.sleep(0.2)

    async def report(self, _reader, writer):
        remaining = max(0, int((self.valid_until - time.monotonic()) * 1000))
        ready = remaining > 0
        status = {
            "state": "verified" if ready else "expired" if self.valid_until else self.state,
            "ip": str(EXPECTED) if ready else "",
            "at": self.sampled_at,
            "generation": self.generation if ready else "",
            "remainingMs": remaining,
            "probes": self.probes,
        }
        try:
            writer.write(json.dumps(status).encode() + b"\n")
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    async def forward(self, reader, writer):
        while data := await reader.read(65536):
            if not self.available():
                raise ConnectionError("exit confirmation expired")
            writer.write(data)
            await writer.drain()
        writer.write_eof()

    async def accept(self, client_reader, client_writer):
        if not self.available():
            client_writer.transport.abort()
            return
        connection = asyncio.current_task()
        self.connections.add(connection)
        upstream_writer = None
        transfers = ()
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(self.host, self.port)
            transfers = (
                asyncio.create_task(self.forward(client_reader, upstream_writer)),
                asyncio.create_task(self.forward(upstream_reader, client_writer)),
            )
            await asyncio.gather(*transfers)
        except OSError:
            pass
        finally:
            self.connections.discard(connection)
            for transfer in transfers:
                transfer.cancel()
            if upstream_writer is not None:
                upstream_writer.transport.abort()
            client_writer.transport.abort()


async def main():
    gate = Gate()
    server = await asyncio.start_unix_server(gate.accept, path=SOCKET)
    status = await asyncio.start_unix_server(gate.report, path=STATUS_SOCKET)
    os.chmod(SOCKET, 0o666)
    os.chmod(STATUS_SOCKET, 0o666)
    async with server, status:
        await asyncio.gather(gate.check_exit(), gate.expire())


if __name__ == "__main__":
    asyncio.run(main())
