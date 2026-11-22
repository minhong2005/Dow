"""Cache and verify the Linux ARM64 release archives used by the images."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

CACHE = Path(os.environ.get("DOW_CACHE_DIR", Path.home() / "Library/Caches/Dow"))
ARCHIVES = [
    (
        'codex.tgz',
        'https://registry.npmjs.org/@openai/codex/-/codex-0.171.0-linux-arm64.tgz',
        176542339,
        'f73d7bb786db509f540886f8f4ee0fa00fa48bee39ab7addf2767c3de1fab189e52a406577f5e9a3f392de1dd38bde78f9fa2c857e28ee4e53094f0def3cea40',
    ),
    (
        'claude.tgz',
        'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-arm64/-/claude-code-linux-arm64-2.1.312.tgz',
        133294185,
        'd8162b01872be6af661bfeea0fc34df0c9e1f182ef5b5723e8ef568cb5c78783a8ada03ebe3c006243fb74cc865d0fe7416ff2770e3cde3f06f75766b0c0573c',
    ),
    (
        'agy.tgz',
        'https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.13-5999302598852608/linux-arm/cli_linux_arm64.tar.gz',
        91596782,
        'dccea2eb455099f5175f0c87cc47b38fcefe4cb0cb44dea3927469bb4bbf68ac3c0b30fc04affb24c17e90959386a93c3785e8e4b8b1a3338ca4e4d57b80057b',
    ),
    (
        'singbox.tgz',
        'https://github.com/SagerNet/sing-box/releases/download/v1.14.4/sing-box-1.14.4-linux-arm64.tar.gz',
        29140599,
        '436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26',
    ),
]


def matches(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    checksum = hashlib.sha256() if len(digest) == 64 else hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest() == digest


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, url, size, digest in ARCHIVES:
        target = CACHE / name
        if matches(target, size, digest):
            continue
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            partial = Path(directory) / name
            subprocess.run(
                ["curl", "-fsSL", "--retry", "3", "-o", str(partial), url],
                check=True,
            )
            if not matches(partial, size, digest):
                raise SystemExit("Checksum mismatch for " + name)
            partial.replace(target)


if __name__ == "__main__":
    main()
