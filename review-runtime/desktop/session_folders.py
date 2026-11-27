"""User-visible project directories and stable paths used by existing Docker mounts."""
import ctypes
import os
from pathlib import Path
import re
import secrets
import unicodedata

from project_copy import FOLDERS, workspace_path


def safe_name(title):
    title = unicodedata.normalize("NFC", title).strip()
    title = title.replace("/", "／").replace(":", "：").lstrip(".")
    title = "".join(c for c in title if unicodedata.category(c) not in ("Cc", "Cf"))
    return title.encode("utf8")[:220].decode("utf8", errors="ignore").strip() or "会话"


def candidates(parent, title):
    yield parent / title
    n = 2
    while True:
        yield parent / f"{title} ({n})"
        n += 1


def matches(path, title):
    return re.fullmatch(re.escape(title) + r"(?: \([0-9]+\))?", unicodedata.normalize("NFC", path.name)) is not None


def swap(a, b):
    # APFS exchanges both entries atomically, so active bind mounts keep their inode
    # and callers using the old path can still reach it during a rename.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renamex_np(os.fsencode(a), os.fsencode(b), 2):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def point_at(link, dest):
    tmp = link.with_name(".folder-link-" + secrets.token_hex(10))
    tmp.symlink_to(os.path.relpath(dest, link.parent))
    try:
        os.replace(tmp, link)
    finally:
        tmp.unlink(missing_ok=True)


def named_directory(store, hub, title):
    anchor = store / "workspace"
    old = workspace_path(store)
    if anchor.is_symlink():
        # Repair a rename interrupted after the exchange but before re-pointing the mount.
        intermediate = hub / Path(os.readlink(anchor)).name
        if intermediate.is_symlink():
            point_at(anchor, old)
            intermediate.unlink()
    if old.parent == hub and matches(old, title):
        return old
    for dest in candidates(hub, title):
        target = os.path.relpath(dest, old.parent)
        try:
            dest.symlink_to(target)
        except FileExistsError:
            # APFS may resolve a case-only change to the existing real directory.
            if old.parent == hub and not dest.is_symlink() and dest.exists() and old.samefile(dest):
                old.rename(dest)
                point_at(anchor, dest)
                return dest
            continue
        try:
            swap(old, dest)
        except OSError:
            dest.unlink()
            raise
        if old != anchor:
            point_at(anchor, dest)
            old.unlink()
        return dest


def legacy_entry(root, hub, job, title, folder):
    # Several old terminals can own the same data; keep one identifiable entry per job.
    if not re.fullmatch(r"job-[a-f0-9]{12}", job):
        raise ValueError("会话编号无效")
    refs = root.parent / ".dow-project-copies" / ".folder-links"
    refs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if refs.is_symlink():
        raise ValueError("会话目录索引异常")
    anchor = refs / job
    if not anchor.is_symlink():
        anchor.symlink_to(os.path.relpath(folder, refs))
    if anchor.resolve(strict=True) != folder.resolve(strict=True):
        raise ValueError("旧会话挂载目录发生变化")
    target = os.path.relpath(anchor, hub)
    existing = [p for p in hub.iterdir() if p.is_symlink() and os.readlink(p) == target]
    dest = next((p for p in existing if matches(p, title)), None)
    if dest is None:
        for dest in candidates(hub, title):
            try:
                dest.symlink_to(target)
                break
            except FileExistsError:
                if any(p.name.casefold()==dest.name.casefold() and dest.is_symlink()
                       and os.readlink(dest)==target for p in existing):
                    p = next(p for p in existing if p.name.casefold()==dest.name.casefold())
                    p.rename(dest)
                    existing.remove(p)
                    break
    for p in existing:
        if p != dest:
            p.unlink()
    return dest


def assign(root, store, row, original=None):
    hub = root.parent / FOLDERS
    hub.mkdir(mode=0o700, exist_ok=True)
    if hub.is_symlink() or not hub.is_dir():
        raise ValueError("会话文件夹总目录异常")
    title = safe_name(row["title"])
    if original is not None:
        return legacy_entry(root, hub, row["id"], title, original)
    return named_directory(store, hub, title)
