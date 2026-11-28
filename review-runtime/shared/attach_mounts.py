"""Colima VM helper: attach read-only runtime resources and the shared work tree."""
import ctypes
import os
from pathlib import Path
import stat
import sys

libc = ctypes.CDLL(None, use_errno=True)
def checked(result):
    if result == -1:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return result

def opened(root, relative):
    fd = os.dup(root)
    for part in Path(relative).parts:
        current = os.open(part, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        if stat.S_ISLNK(os.fstat(current).st_mode):
            raise ValueError("Unexpected symlink in runtime path")
        fd = current
    return fd

def same(a, b):
    return os.path.samestat(os.fstat(a), os.fstat(b))

def run():
    source_pid, target_pid = map(int, sys.argv[1:])
    if min(source_pid, target_pid) <= 1 or source_pid == target_pid:
        raise ValueError("Expected two distinct container processes")
    source, target = [os.open(f"/proc/{p}/root", os.O_PATH) for p in (source_pid, target_pid)]
    ns = os.open(f"/proc/{target_pid}/ns/mnt", os.O_RDONLY)
    if same(ns, os.open("/proc/self/ns/mnt", os.O_RDONLY)):
        raise ValueError("Refusing host mount namespace")
    if not same(opened(source, "work/main"), opened(target, "work/main")):
        raise ValueError("Workspace identity mismatch")
    pairs = [("work","work"), ("opt/runtime-endpoint/native.sock","home/node/.local/state/aiwork/native.sock"),
             ("opt/runtime-pillow","usr/local/lib/python3.11/dist-packages")]
    checked(libc.setns(os.open(f"/proc/{source_pid}/ns/mnt", os.O_RDONLY),0))
    changes = []
    for src, dst in pairs:
        a,b = opened(source,src),opened(target,dst)
        if same(a,b):
            continue
        if dst=="work":
            try:
                existing=opened(target,"work/.dow-shared")
            except FileNotFoundError:
                pass
            else:
                if same(existing,opened(source,"work/.dow-shared")):
                    continue
                raise ValueError("Shared work path already occupied")
        if dst.endswith("dist-packages") and os.listdir(os.open(".",os.O_RDONLY|os.O_DIRECTORY,dir_fd=b)):
            raise ValueError("Refusing to hide existing Python packages")
        if dst.endswith("native.sock") and not all(stat.S_ISSOCK(os.fstat(f).st_mode) for f in (a,b)):
            raise ValueError("Invalid browser endpoint")
        # Linux open_tree / move_mount. Hold both endpoints by fd through the namespace switch.
        tree=checked(libc.syscall(428,a,b"",1 | os.O_CLOEXEC | 0x1000 | (0x8000 if src=="work" else 0)))
        changes.append((tree,b))
    if changes:
        checked(libc.setns(ns,0))
        for tree,b in changes:
            checked(libc.syscall(429,tree,b"",b,b"",0x4|0x40))
    print("updated" if changes else "current")

if __name__=="__main__":
    run()
