"""Forward only legacy-tagged requests to the existing single owner of the old thread."""
import sys,time
from pathlib import Path
sys.path.insert(0,'/opt/dow')
from files import ID,read_json,publish
source=Path('/sender');target=Path('/legacy')
def copy(src,dst,name,once=False):
    value=read_json(src,name)
    try:
        if read_json(dst,name)==value:return
        if once:raise ValueError('交接编号冲突')
    except FileNotFoundError:pass
    publish(dst,name,value,exclusive=once)
while True:
    try:
        copy(target,source,'channel.json')
        for p in source.glob('legacy-*.request.json'):
            ident=p.name[:-13]
            if not ID.fullmatch(ident):continue
            copy(source,target,p.name,True)
            for suffix in ['.result.json','.status.json']:
                if (target/(ident+suffix)).exists():copy(target,source,ident+suffix)
    except (ValueError,OSError) as e:print(str(e),flush=True)
    time.sleep(2)
