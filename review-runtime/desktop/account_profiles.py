"""Keep provider data locations independent of the installed workbench image."""
import json
import os
from pathlib import Path
import re
import tempfile

DIRECTORIES = {"claude": (".claude",), "codex": (".codex",),
               "agy": (".config/antigravity", ".gemini")}

def profiles(state, tool, default_home):
    # Called inside projects.lock. Persist the selection before creating containers.
    path = state / "account-profiles.json"
    try:
        bindings = json.loads(path.read_text())
    except FileNotFoundError:
        bindings = {}
    if tool not in bindings:
        bindings[tool] = {"home": default_home, "directories": {}}
        save(path, bindings)
    profile = bindings[tool]
    if (set(profile) != {"home", "directories"} or
            not set(profile["directories"]).issubset(DIRECTORIES[tool]) or
            any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", name)
                for name in [profile["home"], *profile["directories"].values()])):
        raise ValueError("工具账号目录映射异常，已停止启动")
    return profile

def save(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
