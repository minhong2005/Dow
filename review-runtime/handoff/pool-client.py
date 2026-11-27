"""Route a job id to a stable lane, even for concurrent submissions."""
import fcntl
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import client
from files import publish, read_json, task_id

def perform(action,args):
    pool=client.ROOT/"handoff/.dow-dispatch"
    lanes=[pool/"tasks",pool/"a",pool/"b"]
    def invoke(lane,action,args):
        client.QUEUE=lane
        return original(action,args)
    if action=="status" and not args.get("id"):
        return {lane.name:invoke(lane,action,{}) for lane in [*lanes,pool/"old"]}
    ident=task_id(args["id"])
    if ident.startswith("legacy-"):
        return dict(invoke(pool/"old",action,args),lane="old")
    with (pool/"pool.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        lane=next((p for p in lanes if (p/(ident+".request.json")).exists()),None)
        if lane is None:
            if action!="submit":raise ValueError("没有这个任务")
            lane=pool/"tasks"
            if not invoke(lane,"status",{}).get("online"):
                raise ValueError("按单派发服务离线")
        result = dict(invoke(lane,action,args),lane=lane.name)
        if action in ("status", "collect") and result.get("state") in ("queued", "sending", "running"):
            if not invoke(lane, "status", {}).get("online"):
                result["lastKnownState"] = result["state"]
                result.update(state="unavailable", message="执行通道离线，当前进度未知")
        return result
original=client.perform
client.perform=perform
if sys.argv[1:]==["mcp"]:client.serve()
else:
    import argparse
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="action",required=True)
    q=sub.add_parser("submit");q.add_argument("id");q.add_argument("task");q.add_argument("include",nargs="*")
    for name in ["status","collect"]:sub.add_parser(name).add_argument("id",nargs="?")
    args=vars(p.parse_args());print(json.dumps(perform(args.pop("action"),args),ensure_ascii=False))
