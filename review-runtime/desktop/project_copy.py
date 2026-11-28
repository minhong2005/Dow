"""Create a filtered project copy without running anything from the source tree."""
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SKIP = {
    ".git", ".hg", ".svn", ".git-credentials", ".netrc", ".npmrc", ".pypirc",
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".config",
    ".claude", ".claude.json", ".codex", ".gemini", ".gemini.json", ".codebuddy",
    ".workbuddy", ".mcp.json", ".vault-token", ".password-store", ".bash_history",
    ".zsh_history", ".python_history",
    ".dow-dispatch", ".cache", ".venv", "venv", "node_modules", "__pycache__", ".ds_store",
    "credentials.json", "credentials.yml", "credentials.yaml", "credentials.toml",
    "secrets.json", "secrets.yml", "secrets.yaml", "secrets.toml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
PATTERNS = (".dow-handoff-*", ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
            "*service-account*.json", "*service_account*.json")
OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
GIT = "/usr/bin/git"
SCAN_REPORT = "content-filter.json"
CHUNK = 1024 * 1024
SECRET = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    rb"|(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}"
    rb"|github_pat_[A-Za-z0-9_]{40,}|sk-(?:ant|proj|svcacct)-[A-Za-z0-9_-]{28,}"
    rb"|xox[baprs]-[A-Za-z0-9-]{18,}|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35})"
    rb"|[\x22']?private_key[\x22']?\s*[:=]\s*[\x22']-----BEGIN "
)


def skip(name):
    folded = name.casefold()
    return folded in SKIP or any(fnmatch.fnmatchcase(folded, pattern) for pattern in PATTERNS)


def inspect_file(reader, writer=None):
    tail = b""
    while chunk := reader.read(CHUNK):
        if SECRET.search(tail + chunk):
            return False
        if writer is not None:
            writer.write(chunk)
        tail = chunk[-256:]
    return True


def copy_directory(source_fd, target, blocked, prefix=Path()):
    directory_before = os.fstat(source_fd)
    with os.scandir(source_fd) as children:
        for item in children:
            if skip(item.name):
                continue
            relative = prefix / item.name
            dest = target / relative if target is not None else None
            if item.is_dir(follow_symlinks=False):
                fd = os.open(item.name, OPEN_DIR, dir_fd=source_fd)
                try:
                    if dest is not None:
                        dest.mkdir(mode=0o700)
                    copy_directory(fd, target, blocked, relative)
                finally:
                    os.close(fd)
            elif item.is_file(follow_symlinks=False):
                fd = os.open(item.name, OPEN_FILE, dir_fd=source_fd)
                with os.fdopen(fd, "rb") as reader:
                    metadata = os.fstat(reader.fileno())
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        continue
                    if dest is None:
                        clean = inspect_file(reader)
                    else:
                        with dest.open("xb") as writer:
                            clean = inspect_file(reader, writer)
                        if clean:
                            dest.chmod(0o700 if metadata.st_mode & stat.S_IXUSR else 0o600)
                        else:
                            dest.unlink()
                    after = os.fstat(fd)
                    if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError("文件正在修改，请稍后重试复制：" + str(relative))
                    if not clean:
                        blocked.add(relative)
    if directory_before.st_mtime_ns != os.fstat(source_fd).st_mtime_ns:
        raise ValueError("项目目录在复制期间发生变化，请重试")


def inspect_tree(source, target=None):
    blocked = set()
    fd = os.open(source, OPEN_DIR)
    try:
        copy_directory(fd, target, blocked)
    finally:
        os.close(fd)
    return blocked


def save_report(store, files):
    path = store / SCAN_REPORT
    fd, tmp = tempfile.mkstemp(dir=store)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as report:
            json.dump(sorted(str(name) for name in files), report, ensure_ascii=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


FOLDERS = "Dow 会话"


def workspace_path(store):
    """Only Dow's relative mount anchor may point outside the private copy store."""
    anchor = store / "workspace"
    if store.is_symlink() or not store.is_dir():
        raise ValueError("会话存储目录异常")
    if not anchor.is_symlink():
        if not anchor.is_dir():
            raise ValueError("会话工作目录不存在")
        return anchor
    link = Path(os.readlink(anchor))
    hub = store.parent.parent / FOLDERS
    if len(link.parts)==5 and link.parts[:2]==("..","..") and link.parts[3]==".dow-shared":
        base = store.parent.parent / link.parts[2]
        hub = base / ".dow-shared"
        if link.parts[2] in (".","..") or base.is_symlink() or hub.is_symlink() or link.name.startswith("."):
            raise ValueError("共享会话目录异常")
    elif (len(link.parts) != 4 or link.parts[:3] != ("..", "..", FOLDERS)
            or link.name.startswith(".") or hub.is_symlink()):
        raise ValueError("会话工作目录链接异常")
    resolved = anchor.resolve(strict=True)
    if resolved.parent != hub or not resolved.is_dir():
        raise ValueError("会话工作目录不在指定位置")
    return resolved


def prepare(source, store):
    """Publish the baseline and writable copy together, outside the shared project tree."""
    if store.exists():
        for folder in (store, store / "baseline", workspace_path(store)):
            if not folder.is_dir() or folder.is_symlink():
                raise RuntimeError("保护副本目录异常")
        if not (store / SCAN_REPORT).is_file():
            found = inspect_tree(store / "baseline") | inspect_tree(workspace_path(store))
            if found:
                raise RuntimeError("旧保护副本中发现疑似凭据，暂停启动。副本和改动已保留。")
            save_report(store, set())
        return store / "workspace"
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".copy-", dir=store.parent))
    try:
        for name in ("baseline", "workspace"):
            (stage / name).mkdir(mode=0o700)
        blocked = inspect_tree(source, stage / "baseline")
        inspect_tree(stage / "baseline", stage / "workspace")
        save_report(stage, blocked)
        stage.rename(store)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return store / "workspace"


def excluded_paths(store):
    with (store / SCAN_REPORT).open(encoding="utf-8") as report:
        paths = {Path(name) for name in json.load(report)}
    if any(path.is_absolute() or path == Path() or ".." in path.parts for path in paths):
        raise RuntimeError("保护副本的过滤报告无效")
    return paths


def excluded_count(store):
    return len(excluded_paths(store))


def write_patch(source, destination):
    fd, filename = tempfile.mkstemp(prefix=".dow-patch-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as patch:
            shutil.copyfileobj(source, patch)
        os.link(filename, destination)
    finally:
        os.unlink(filename)


def export_patch(store, destination):
    """Compare filtered trees without executing Git hooks or filters from either tree."""
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"}
    with tempfile.TemporaryDirectory(prefix="dow-patch-") as tmp:
        root = Path(tmp)
        blocked = excluded_paths(store) if (store / SCAN_REPORT).is_file() else set()
        for name in ("baseline", "workspace"):
            (root / name).mkdir(mode=0o700)
            source = workspace_path(store) if name == "workspace" else store / name
            blocked.update(inspect_tree(source, root / name))
        for path in blocked:
            for name in ("baseline", "workspace"):
                candidate = root / name / path
                if candidate.is_file():
                    candidate.unlink()
                elif candidate.is_dir():
                    shutil.rmtree(candidate)
        env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [GIT, "--no-pager", "diff", "--no-index", "--binary", "--no-ext-diff",
                 "--no-textconv", "--no-renames", "--", "baseline", "workspace"],
                cwd=root, env=env, stdout=output, stderr=subprocess.PIPE, timeout=45)
            if result.returncode == 0:
                raise ValueError("含疑似凭据的改动已排除，其余文件没有可导出的改动"
                                 if blocked else "保护副本还没有可导出的改动")
            if result.returncode != 1 or result.stderr:
                raise RuntimeError("无法安全生成项目补丁")
            output.seek(0)
            write_patch(output, destination)
    return destination


def main():
    action, root_name, project, name, *origin = sys.argv[1:]
    root = Path("/dow")
    store = root / ".dow-project-copies" / name
    if action == "prepare":
        source = root / root_name / project
        if origin:
            if len(origin) != 1 or not re.fullmatch(r"dow-(?:session-|project-(?:protected-)?)[a-f0-9]{24}", origin[0]):
                raise ValueError("无效的源副本")
            source = workspace_path(root / ".dow-project-copies" / origin[0])
            if not source.is_dir():
                raise ValueError("源会话文件夹不存在")
        try:
            if not origin:
                source.mkdir(mode=0o700, exist_ok=True)
        except FileExistsError:
            raise ValueError("项目路径已经存在，且不是项目目录") from None
        if source.is_symlink() or source.resolve(strict=True) != source:
            raise ValueError("项目目录不能是符号链接")
        prepare(source, store)
        print(excluded_count(store))
    elif action == "export":
        if not store.is_dir():
            raise ValueError("此项目还没有保护副本")
        with tempfile.TemporaryDirectory(prefix="dow-export-") as tmp:
            file = export_patch(store, Path(tmp) / "project.patch")
            with file.open("rb") as patch:
                shutil.copyfileobj(patch, sys.stdout.buffer)
    else:
        raise ValueError("无效的项目存储操作")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
