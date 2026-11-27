"""Operations on the paired Windows workstation. No local shell interpolation."""
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
from pathlib import PureWindowsPath

CONFIG = Path.home() / "Library/Application Support/Dow/devices/windows-crm/workstation.json"


def run(args):
    config = json.loads(CONFIG.read_text())
    peer = config["peer"]
    options = ["-F", "/dev/null", "-i", config["identity"], "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
               "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=4"]
    action = args["action"]
    if action == "powershell":
        script = args["script"]
        if not isinstance(script, str) or not 0 < len(script) <= 6000:
            raise ValueError("PowerShell 脚本长度须为 1 到 6000 字符")
        script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$ErrorActionPreference='Stop';\n" + script
        encoded = base64.b64encode(script.encode("utf-8")).decode()
        command = ["/usr/bin/ssh", *options, peer, "& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))))"]
    else:
        root = Path(config["exchange"]).resolve(strict=True)
        name = args["filename"]
        if not isinstance(name,str) or not re.fullmatch(r"[\w .()-]{1,180}", name) or name.startswith("."):
            raise ValueError("交接区仅接受单个文件名")
        local = root / name
        path = PureWindowsPath(args["remote"])
        if path.drive.upper() != "D:" or not path.is_absolute() or any(
            not re.fullmatch(r"[\w .()-]+", part) or part in (".","..") for part in path.parts[1:]):
            raise ValueError("请输入 D 盘文件的完整路径")
        remote = peer + ":" + path.as_posix()
        if action == "upload" and local.is_file() and not local.is_symlink():
            command = ["/usr/bin/scp", *options, "--", str(local), remote]
        elif action == "download" and not local.exists() and not local.is_symlink():
            import os
            with tempfile.TemporaryDirectory(dir=root) as temp:
                staged = Path(temp)/"download"
                subprocess.run(["/usr/bin/scp", *options, "--", remote, str(staged)],check=True,timeout=18)
                os.link(staged,local)
            return "已下载 " + name
        else:
            raise ValueError("检查上传源文件，或为下载选择未使用的文件名")
    with tempfile.TemporaryFile() as out:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=18)
        out.seek(0)
        output = out.read(32000).decode(errors="replace")
        if result.returncode:
            raise ValueError(output or f"工作机返回 {result.returncode}")
        return output or "完成"
