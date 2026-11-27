"""Incrementally mirror append-only gateway journals. A file is unique to one recorder run."""
import os
from pathlib import Path
import re
import subprocess

DOCKER = ['/opt/homebrew/bin/docker','--context','colima-dow','exec','aiwork-gateway']
SPOOL = '/link/dow-flow-journal'
FILENAME = re.compile(r'\d{4}-\d\d-\d\d-[a-f0-9]{16}\.jsonl')


def inventory():
    data = subprocess.check_output([*DOCKER,'find',SPOOL,'-maxdepth','1','-type','f','-printf','%f %s\n'],
                                   timeout=6, stderr=subprocess.DEVNULL, text=True)
    sizes = {}
    for line in data.splitlines():
        name, _, size = line.rpartition(' ')
        if FILENAME.fullmatch(name) and size.isdecimal():
            sizes[name] = int(size)
    return sizes


def mirror(root):
    dest = root/'连接/网关'
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    sizes = inventory()
    if not sizes:
        raise OSError('gateway journal missing')
    lag = 0
    for name, size in sorted(sizes.items()):
        path = dest/name
        offset = path.stat().st_size if path.exists() else 0
        if offset > size:
            raise OSError('source journal shorter than mirror')
        if offset == size:
            continue
        count = min(size-offset, 4*1024**2)
        # Finish reading a fixed prefix before appending; failed transfers do not advance the offset.
        data = subprocess.check_output([*DOCKER,'dd','if='+SPOOL+'/'+name,'iflag=skip_bytes,count_bytes',
                                        f'skip={offset}',f'count={count}','bs=64K','status=none'],
                                       timeout=15, stderr=subprocess.DEVNULL)
        if len(data) != count:
            raise OSError('incomplete journal transfer')
        with path.open('ab') as out:
            out.write(data)
        lag += size-offset-count
    return '正在补齐积压' if lag else '源端逐连接记录；Mac 副本已追上本轮读取位置'


def cleanup(root, oldest):
    # Only unlink generated, expired files. A gateway file is removed only after its full length is mirrored.
    sizes = inventory()
    for name,size in sizes.items():
        if name[:10] < oldest:
            local = root/'连接/网关'/name
            if not local.is_file() or local.stat().st_size != size:
                raise OSError('expired gateway day not fully mirrored')
            subprocess.run([*DOCKER,'rm','--',SPOOL+'/'+name],check=True,timeout=5)
    for component in ('网关','Chrome'):
        for f in (root/'连接'/component).glob('*.jsonl'):
            if FILENAME.fullmatch(f.name) and f.name[:10] < oldest:
                f.unlink()
