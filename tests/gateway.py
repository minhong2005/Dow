"""Exercise the public gateway with simulated exit observations and loopback peers."""
import asyncio
import importlib.util
import ipaddress
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

os.environ["DOW_EXPECTED_IP"] = "198.51.100.7"
spec = importlib.util.spec_from_file_location("guard", Path(__file__).resolve().parents[1] / "gateway/guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


async def exercise():
    good = guard.EXPECTED
    other = ipaddress.IPv4Address("203.0.113.2")
    for answers, expected in [
        ([good, None, None, None], "probe_failed"),
        ([good, good, None, None], "verified"),
        ([good, good, good, other], "mismatch"),
    ]:
        values = iter(answers)

        async def observe(name, url, trace):
            value = next(values)
            return name, value, "test"

        with patch.object(guard, "observe", observe):
            result, _ = await guard.confirmed()
            assert result == expected

    async def echo(reader, writer):
        try:
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    with tempfile.TemporaryDirectory() as tmp:
        async with await asyncio.start_server(echo, "127.0.0.1", 0) as upstream:
            gate = guard.Gate(port=upstream.sockets[0].getsockname()[1])
            path = str(Path(tmp) / "proxy.sock")
            async with await asyncio.start_unix_server(gate.accept, path) as server:
                reader, writer = await asyncio.open_unix_connection(path)
                assert await asyncio.wait_for(reader.read(), 2) == b""
                writer.close()
                gate.valid_until = time.monotonic() + 10
                reader, writer = await asyncio.open_unix_connection(path)
                writer.write(b"open"); await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(4), 2) == b"open"
                gate.block("mismatch")
                assert await asyncio.wait_for(reader.read(), 2) == b""
                writer.close()
                reader, writer = await asyncio.open_unix_connection(path)
                assert await asyncio.wait_for(reader.read(), 2) == b""
                writer.close()


if __name__ == "__main__":
    asyncio.run(exercise())
