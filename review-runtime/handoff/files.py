"""Bounded task bundles. Only ordinary project files cross a session boundary."""
import base64
import json
import os
from pathlib import Path
import re
import stat
import tempfile

import project_copy

MAX_BYTES = 12 * 1024 * 1024
MAX_JSON = 18 * 1024 * 1024
MAX_FILES = 1200
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")


def task_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("任务编号只接受字母、数字、横线和下划线，以字母开头")
    return value


def parts(name):
    if not isinstance(name, str) or len(name) > 512 or any(ord(c) < 32 for c in name):
        raise ValueError("项目文件路径无效")
    segments = name.split("/")
    if any(s in ("", ".", "..", ".dow-dispatch") or project_copy.skip(s) for s in segments):
        raise ValueError("仅能交接普通项目相对路径")
    return segments


def read_at(parent, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            raise ValueError("文件不是独立普通文件或超过交接大小限制")
        data = f.read(limit+1)
        if len(data) != st.st_size or os.fstat(fd).st_mtime_ns != st.st_mtime_ns:
            raise ValueError("文件正在变化，请完成写入后再交接")
        return data


def read_json(directory, name):
    fd = os.open(directory, DIR)
    try:
        return json.loads(read_at(fd, name, MAX_JSON))
    finally:
        os.close(fd)


def publish(directory, filename, value, exclusive=False):
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, mode="w", encoding="utf8") as f:
        tmp = Path(f.name)
        try:
            json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
            if exclusive:
                os.link(tmp, directory/filename)
            else:
                os.replace(tmp, directory/filename)
        finally:
            tmp.unlink(missing_ok=True)


def pack(root, names):
    result = {}
    size = 0
    def visit(parent, name, prefix):
        nonlocal size
        if prefix in result:
            return
        st = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            fd = os.open(name, DIR, dir_fd=parent)
            try:
                for child in sorted(os.listdir(fd)):
                    if not project_copy.skip(child) and child != ".dow-dispatch":
                        visit(fd, child, prefix+"/"+child)
            finally:
                os.close(fd)
        elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
            parts(prefix)
            data = read_at(parent, name, MAX_BYTES-size)
            if project_copy.SECRET.search(data):
                raise ValueError("交接文件中包含疑似凭据："+prefix)
            if len(result) >= MAX_FILES:
                raise ValueError("交接文件过多")
            size += len(data)
            result[prefix] = base64.b64encode(data).decode("ascii")
    for name in names:
        components = parts(name)
        fd = os.dup(root) if isinstance(root, int) else os.open(root, DIR)
        try:
            for part in components[:-1]:
                nxt = os.open(part, DIR, dir_fd=fd)
                os.close(fd)
                fd = nxt
            visit(fd, components[-1], name)
        finally:
            os.close(fd)
    return result


def unpack(files, dest):
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError("文件清单无效")
    size = 0
    for name, encoded in files.items():
        components = parts(name)
        data = base64.b64decode(encoded, validate=True)
        size += len(data)
        if size > MAX_BYTES or project_copy.SECRET.search(data):
            raise ValueError("交接大小超限或含疑似凭据")
        fd = os.dup(dest) if isinstance(dest, int) else os.open(dest, DIR)
        try:
            for part in components[:-1]:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                nxt = os.open(part, DIR, dir_fd=fd)
                os.close(fd)
                fd = nxt
            out = os.open(components[-1],os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(out, "wb") as f:
                f.write(data)
        finally:
            os.close(fd)
