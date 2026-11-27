"""Test the real Chrome firewall identity against temporary local listeners."""
from contextlib import ExitStack
import select
import socket

TARGETS = (
    ("tcp4", socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1"),
    ("udp4", socket.AF_INET, socket.SOCK_DGRAM, "127.0.0.1"),
    ("tcp6", socket.AF_INET6, socket.SOCK_STREAM, "::1"),
    ("udp6", socket.AF_INET6, socket.SOCK_DGRAM, "::1"),
)


def check(driver):
    with ExitStack() as stack:
        listeners = {}
        for name, family, kind, host in TARGETS:
            listener = stack.enter_context(socket.socket(family, kind))
            listener.settimeout(1)
            listener.bind((host, 0))
            target = (host, listener.getsockname()[1])
            with socket.socket(family, kind) as client:
                client.settimeout(1)
                if kind == socket.SOCK_STREAM:
                    listener.listen()
                    client.connect(target)
                    connection, _ = listener.accept()
                    connection.close()
                else:
                    client.sendto(b"dow-host-control", target)
                    if listener.recvfrom(64)[0] != b"dow-host-control":
                        raise RuntimeError("本机 UDP 对照检测失败")
            listeners[listener] = name
        ports = {name:sock.getsockname()[1] for sock, name in listeners.items()}
        reply = driver("verifySockets", ports=ports)
        if set(reply.get("attempted", ())) != set(ports) or not reply.get("browserPid"):
            raise RuntimeError("Chrome 防火墙检测未完成")
        ready, _, _ = select.select(listeners, [], [], 0.4)
        if ready:
            paths = "、".join(listeners[item].upper() for item in ready)
            raise RuntimeError("Chrome 防火墙未拦住测试流量 · " + paths)
        return reply["browserPid"]
