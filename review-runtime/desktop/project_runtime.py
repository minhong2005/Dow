"""Provision project containers from the host. The Docker API stays on the Mac."""
from concurrent.futures import ThreadPoolExecutor
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_copy
import session_folders
import account_profiles

DOCKER = ("/opt/homebrew/bin/docker", "--context", "colima-dow")
CONTROLLER = "aiwork-tools"
PROJECT_LABEL = "local.dow.project"
IMAGE = "dow-project:1"
HOME = "/home/node"
BRIDGE = HOME + "/.local/state/aiwork"
TOOLS = ("claude", "codex", "agy")
SHARED_WORK = "/work/.dow-shared"
SESSION_CONTAINER = re.compile(r"dow-session-[a-f0-9]{24}")
PROJECT_CONTAINER = re.compile(r"dow-(?:session-|project-(?:protected-)?)[a-f0-9]{24}")
STATE = Path.home() / "Library/Application Support/aiwork-desktop"


def docker(*args, timeout=25):
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(detail[-1][:250] if detail else "项目环境连接失败")
    return result.stdout.strip()


def inspect(name):
    return json.loads(docker("container", "inspect", name))[0]


def mount(*fields):
    result = io.StringIO()
    csv.writer(result).writerow(fields)
    return result.getvalue().rstrip("\r\n")


def project_id(project, protected=True):
    if (not isinstance(project, str) or not project or len(project) > 100
            or project in {".", "..", ".dow-shared"} or "/" in project
            or any(ord(c) < 32 or ord(c) == 127 for c in project)):
        raise ValueError("请输入有效的项目名称")
    prefix = "dow-project-protected-" if protected else "dow-project-"
    return prefix + hashlib.sha256(project.encode()).hexdigest()[:24]


def project_root(controller):
    return Path(next(m["Source"] for m in controller["Mounts"]
                     if m["Type"] == "bind" and m["Destination"] == "/work"))


def copy_store(root, name):
    return root.parent / ".dow-project-copies" / name


def storage(root, action, project, name, output=subprocess.PIPE, source_store=None):
    # Access workspaces through the VM mount Dow already uses. The helper gets
    # no network interface apart from loopback and no access to the CLI homes.
    source = Path(project_copy.__file__).read_bytes()
    args = ["run", "--rm", "-i", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
            "--mount", mount("type=bind", "source=" + str(root.parent), "target=/dow",
                             "bind-recursive=disabled"),
            "--entrypoint", "/usr/bin/python3", IMAGE, "-I", "-", action, root.name, project, name]
    if source_store is not None:
        args.append(source_store)
    result = subprocess.run([*DOCKER, *args], input=source, stdout=output,
                            stderr=subprocess.PIPE, timeout=90)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(detail[-1][:250] if detail else "项目文件操作失败")
    return result.stdout


def export(project, name=None):
    name = name or project_id(project)
    root = project_root(inspect(CONTROLLER))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = Path.home() / "Desktop" / ("Dow-" + stamp + "-" + secrets.token_hex(3) + ".patch")
    with tempfile.TemporaryFile() as patch:
        storage(root, "export", project, name, patch)
        patch.seek(0)
        project_copy.write_patch(patch, destination)
    return destination


def available(running=True):
    command = ["ps", "--filter", f"label={PROJECT_LABEL}", "--format", "{{.Names}}"]
    if not running:
        command.append("-a")
    return sorted(name for name in docker(*command).splitlines() if PROJECT_CONTAINER.fullmatch(name))


def ensure(project, name=None, source_store=None, tool=None):
    project_name = project_id(project)
    name = name or project_name
    if name != project_name and not SESSION_CONTAINER.fullmatch(name):
        raise ValueError("会话目录编号无效")
    if source_store and not PROJECT_CONTAINER.fullmatch(source_store):
        raise ValueError("源会话目录编号无效")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "projects.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        controller = inspect(CONTROLLER)
        if controller["HostConfig"]["NetworkMode"] != "none" or not controller["State"]["Running"]:
            raise RuntimeError("固定出口工作区尚未就绪，已停止启动项目")
        root = project_root(controller)
        filtered = int(storage(root, "prepare", project, name, source_store=source_store).strip())
        workspace = copy_store(root, name) / "workspace"
        workdir = "/work/" + project
        net = "container:" + controller["Id"]
        endpoint = "aiwork-home"
        volume = project_name + "-home"
        extra = []
        bridge_fields = ["volume-subpath=.local/state/aiwork"]
        if tool:
            launcher = Path(__file__).resolve().with_name("shared") / "workbench"
            if not launcher.exists():
                launcher = Path(__file__).resolve().parent.parent / "shared/workbench"
            subprocess.run([sys.executable, str(launcher), tool, project, "--prepare"], check=True,
                           env=dict(os.environ, PATH=str(Path(DOCKER[0]).parent)+":"+os.environ.get("PATH","")))
            endpoint = "dow-shared-" + tool + "-v1-endpoint"
            identity = account_profiles.profiles(STATE, tool, "dow-account-"+tool)
            volume = identity["home"]
            bridge_fields = []
            extra = ["--label", "org.dow.shared.tool="+tool]
            for directory, account_volume in identity["directories"].items():
                extra += ["--mount", mount("type=volume","source="+account_volume,
                          "volume-subpath="+directory,"target="+HOME+"/"+directory,"volume-nocopy")]
            pilot = inspect("dow-shared-"+tool+"-v1")
            if any(m["Destination"]=="/opt/dow-pillow" for m in pilot["Mounts"]):
                extra += ["--mount",mount("type=volume","source=aiwork-home","target=/opt/dow-pillow",
                          "volume-subpath=.local/state/aiwork/python-libs/pillow-13.0.0","readonly"),
                          "--env","PYTHONPATH=/opt/dow-pillow"]
        if name not in available(running=False):
            shared = root / ".dow-shared"
            shared.mkdir(mode=0o700, exist_ok=True)
            if shared.is_symlink() or not shared.is_dir():
                raise RuntimeError("共享工作目录无效")
            codex = inspect("aiwork-codex")
            policy = next((opt[len("seccomp="):] for opt in codex["HostConfig"]["SecurityOpt"]
                           if opt.startswith("seccomp={")), "")
            if not policy:
                raise RuntimeError("Codex 的文件沙箱配置尚未就绪")
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as profile:
                profile.write(policy)
                profile.flush()
                docker("create", *extra, "--name", name, "--label", f"{PROJECT_LABEL}={project}",
                       "--init", "--network", net, "--cap-drop", "ALL", "--read-only",
                       "--security-opt", "no-new-privileges", "--security-opt", "apparmor=dow-codex",
                       "--security-opt", "seccomp=" + profile.name,
                       "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                       "--tmpfs", "/run:rw,nosuid,nodev,mode=755",
                       "--mount", mount("type=bind", "source=" + str(workspace), "target=" + workdir,
                                        "bind-recursive=disabled"),
                       "--mount", mount("type=bind", "source=" + str(shared), "target=" + SHARED_WORK,
                                        "bind-recursive=disabled"),
                       "--mount", mount("type=volume", "source=" + volume, "target=" + HOME),
                       "--mount", mount("type=volume", "source="+endpoint, "target=" + BRIDGE,
                                        "volume-nocopy", *bridge_fields, "readonly"),
                       "--env", "TZ=America/Chicago", "--env", "DOW_PROJECT=" + project, "--env", "DOW_SINGLE_SESSION=" + str(int(bool(SESSION_CONTAINER.fullmatch(name)))), "--workdir", workdir, IMAGE)
        info = inspect(name)
        host = info["HostConfig"]
        paths = {m["Destination"]: m for m in info["Mounts"]}
        allowed_paths = {workdir, HOME, BRIDGE, SHARED_WORK}
        if tool:
            for directory, account_volume in identity["directories"].items():
                target = HOME + "/" + directory
                allowed_paths.add(target)
                if paths.get(target, {}).get("Name") != account_volume:
                    raise RuntimeError("账号目录未正确挂载")
        if "/opt/dow-pillow" in paths and tool:
            allowed_paths.add("/opt/dow-pillow")
        if (info["Config"]["Labels"].get(PROJECT_LABEL) != project or host["NetworkMode"] != net
                or not host["ReadonlyRootfs"] or host["Privileged"]
                or set(paths) not in ((allowed_paths,) if tool else ({workdir, HOME, BRIDGE}, allowed_paths))
                or (SHARED_WORK in paths and (paths[SHARED_WORK]["Source"] != str(root / ".dow-shared")
                    or paths[SHARED_WORK]["Type"] != "bind" or not paths[SHARED_WORK]["RW"]))
                or paths[workdir]["Source"] != str(workspace) or paths[HOME].get("Name") != volume
                or paths[BRIDGE].get("Name") != endpoint or paths[BRIDGE]["RW"]):
            raise RuntimeError("项目环境的挂载或网络与保存的配置不符")
        if not info["State"]["Running"]:
            docker("start", name)
        # Docker reports a running process before its app-server accepts clients.
        # Check inside the container so both entry points see the same socket.
        docker("exec", name, "python3", "-I", "-c", """
import errno
import os
import socket
import time

deadline = time.monotonic() + 10
while True:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        try:
            client.connect(os.environ["DOW_CODEX_SOCKET"])
            break
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                raise
    if time.monotonic() >= deadline:
        raise SystemExit("项目里的 Codex 连接入口未能就绪，请重试。")
    time.sleep(0.05)
""")
        return name, filtered


# Existing project containers have a read-only root. Use the deployed copy on
# their read-only bridge mount so that session updates preserve running jobs.
SESSION_CLI = ("sh", "-c", 'if [ -x "$HOME/.local/state/aiwork/aiwork-sessions" ]; then exec "$HOME/.local/state/aiwork/aiwork-sessions" "$@"; fi; exec aiwork-sessions "$@"', "dow-sessions")


def sessions(name, *args):
    return json.loads(docker("exec", name, *SESSION_CLI, "api", *args,
                             timeout=6 if args == ("list",) else 25))


def session_folder(container, row):
    project_id(row["project"])
    root = project_root(inspect(CONTROLLER))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "projects.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if SESSION_CONTAINER.fullmatch(container):
            if inspect(container)["Config"]["Labels"].get("org.dow.shared.tool"):
                return session_folders.named_directory(copy_store(root,container), root / ".dow-shared", session_folders.safe_name(row["title"]))
            return session_folders.assign(root, copy_store(root, container), row)
        target = "/work/" + row["project"]
        mounts = inspect(container)["Mounts"]
        original = next((Path(m["Source"]) for m in mounts if m["Type"] == "bind"
                         and m["Destination"] == target), None)
        if original is None:
            original = project_root({"Mounts": mounts}) / row["project"]
        return session_folders.assign(root, None, row, original)


def rename_session(container, ident, title):
    rows = {r["id"]:r for r in sessions(container, "list")}
    old = rows[ident]["title"]
    sessions(container, "rename", ident, title)
    try:
        row = next(r for r in sessions(container, "list") if r["id"] == ident)
        session_folder(container, row)
    except Exception:
        sessions(container, "rename", ident, old)
        raise
    return ident


def new_session(project, tool, title="", source=None):
    project_id(project)
    if tool not in TOOLS:
        raise ValueError("未知的会话工具")
    name = "dow-session-" + secrets.token_hex(12)
    source_store = None
    if source:
        container, row = source
        if row["project"] != project:
            raise ValueError("源会话项目不匹配")
        if row["scope"] in ("protected", "isolated"):
            source_store = container
    try:
        _, filtered = ensure(project, name, source_store, tool=tool)
        title = sessions(name, "allocate-title", tool, project, title)
        session_folder(name, {"project": project, "title": title})
        ident = sessions(name, "create", tool, project, title)
    except Exception:
        # Only this attempt can own its randomly named container. Leave project files intact.
        if name in available(running=False):
            docker("rm", "-f", name)
        raise
    return {"id": ident, "filteredFiles": filtered}


def session_preset(project):
    lock_path = STATE / (project_id(project) + "-preset.lock")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = {row["tool"]: row["id"] for _, row in catalog().values()
                   if row["project"] == project and row["scope"] == "isolated"
                   and not row["archived"] and not row["exited"]}
        ids = []
        filtered = 0
        for tool in TOOLS:
            if tool in running:
                ids.append(running[tool])
            else:
                result = new_session(project, tool)
                ids.append(result["id"])
                filtered += result["filteredFiles"]
        return {"ids": ids, "filteredFiles": filtered}


def catalog():
    names = [CONTROLLER, *available()]
    result = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {name: pool.submit(sessions, name, "list") for name in names}
        for name, future in pending.items():
            try:
                rows = future.result()
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                if name == CONTROLLER:
                    raise
                continue
            for row in rows:
                if not re.fullmatch(r"job-[a-f0-9]{12}", row.get("id", "")):
                    continue
                scope = "shared"
                if name != CONTROLLER:
                    try:
                        if SESSION_CONTAINER.fullmatch(name):
                            scope = "isolated"
                        elif project_id(row["project"]) == name:
                            scope = "protected"
                        elif project_id(row["project"], protected=False) == name:
                            scope = "direct"
                        else:
                            continue
                    except ValueError:
                        continue
                result.setdefault(row["id"], (name, dict(row, scope=scope)))
    return result


def main():
    mode, *params = sys.argv[1:]
    if mode == "list":
        status = {"shared": "旧会话 · 共享目录", "direct": "旧会话 · 原项目", "protected": "项目共享副本", "isolated":"会话独立目录"}
        for _, row in catalog().values():
            print(f'{row["project"]}\t{row["title"]}\t{status[row["scope"]]}\t{row["id"]}')
        return
    project, = params
    if mode == "export":
        rows = catalog()
        if project in rows:
            container, row = rows[project]
            if row["scope"] not in ("isolated", "protected"):
                raise ValueError("所选会话没有保护副本")
            print(export(row["project"], container))
        else:
            matches = {name for name, row in rows.values()
                       if row["project"] == project and row["scope"] in ("isolated", "protected")}
            if len(matches) > 1:
                raise ValueError("此项目有多个副本。请从 aiwork --list 选择会话编号，或在 Dow 内导出。")
            print(export(project, next(iter(matches), None)))
        return
    if mode in ("init", "preset"):
        from terminal_sessions import open_terminal
        open_terminal(mode, project)
        return
    if mode != "shell":
        raise ValueError("不支持的项目操作")
    # The standalone shell can be reattached independently of all agent workspaces.
    shell_name = "dow-session-" + hashlib.sha256(("shell\0" + project).encode()).hexdigest()[:24]
    previous = project_id(project)
    if previous in available() and subprocess.run(
            [*DOCKER, "exec", previous, "tmux", "has-session", "-t", "=project-shell"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        name = previous
    else:
        name, _ = ensure(project, shell_name)
    args = ("tmux", "new-session", "-A", "-s", "project-shell")
    os.execv(DOCKER[0], [*DOCKER, "exec", "-it", "-e", "LANG=C.UTF-8", name, *args])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
