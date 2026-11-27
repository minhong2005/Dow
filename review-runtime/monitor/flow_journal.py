"""Append connection lifecycle metadata. Never persist HTTP headers, paths or payloads."""
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Chicago")


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.run = secrets.token_hex(8)
        self.lost = 0
        self.emit("recorder_started")

    def emit(self, event, **fields):
        now = dt.datetime.now(ZONE)
        row = dict(time=now.isoformat(timespec="milliseconds"), run=self.run, event=event, **fields)
        if self.lost:
            row["previous_write_failures"] = self.lost
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.directory / (now.date().isoformat() + "-" + self.run + ".jsonl")
            fd = os.open(path, os.O_APPEND | os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                data = (json.dumps(row, separators=(",", ":")) + "\n").encode()
                if os.write(fd, data) != len(data):
                    raise OSError("short journal write")
            finally:
                os.close(fd)
            self.lost = 0
        except OSError:
            self.lost += 1

    def start(self, kind):
        return Flow(self, kind)


class Flow:
    def __init__(self, journal, kind):
        self.journal = journal
        self.id = secrets.token_hex(10)
        self.start_time = time.monotonic()
        self.kind = kind
        self.up = self.down = 0
        self.host = None
        self.port = None
        self.status = None
        self.buffers = [bytearray(), bytearray()]
        journal.emit("start", id=self.id, kind=kind)

    def destination(self, host, port):
        if isinstance(host, str) and len(host) <= 253:
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
                    return
            self.host = host.lower()
            self.port = port

    def observe(self, chunk, downstream=False):
        index = int(downstream)
        if downstream:
            self.down += len(chunk)
        else:
            self.up += len(chunk)
        buf = self.buffers[index]
        if buf is None:
            return
        buf.extend(chunk[:max(0, 2048-len(buf))])
        if b"\n" not in buf and len(buf) < 2048:
            return
        self.buffers[index] = None
        first = bytes(buf).partition(b"\n")[0]
        if downstream:
            m = re.match(rb"HTTP/\d(?:\.\d)? ([1-5]\d\d)\b", first)
            if m:
                self.status = int(m[1])
        else:
            parts = first.split()
            if len(parts) == 3 and parts[2].startswith(b"HTTP/"):
                try:
                    target = parts[1].decode("ascii")
                    dest = urlsplit("//"+target if parts[0]==b"CONNECT" else target)
                    self.destination(dest.hostname, dest.port or (443 if parts[0]==b"CONNECT" else 80))
                except (ValueError, UnicodeError):
                    pass

    def end(self, result):
        self.journal.emit("end", id=self.id, result=result, host=self.host, port=self.port,
                          proxy_status=self.status, up_bytes=None if self.kind == "exit_probe" else self.up,
                          down_bytes=None if self.kind == "exit_probe" else self.down,
                          duration_ms=round(1000*(time.monotonic()-self.start_time)))
