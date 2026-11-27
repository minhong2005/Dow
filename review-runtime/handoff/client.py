#!/usr/bin/env python3
"""Antigravity's shell and stdio MCP entry points for its paired Codex chat."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from files import pack, parts, publish, read_json, task_id, unpack

ROOT = Path("/work") / os.environ.get("DOW_PROJECT", "main")
QUEUE = ROOT/"handoff/.dow-dispatch"
TOOLS = {
    "codex_handoff_submit": ("Send a task file and explicitly selected project inputs to the paired existing Codex chat, exactly once per ID. Use when you decide to dispatch. Returns queued immediately.", {
        "id":{"type":"string"}, "task":{"type":"string"}, "include":{"type":"array","items":{"type":"string"}}}, ["id","task"]),
    "codex_handoff_status": ("Read channel readiness, or check a submitted task by ID. Never dispatches.", {"id":{"type":"string"}}, []),
    "codex_handoff_collect": ("Retrieve a finished task's project files and receipt into handoff/results/ID for review.", {"id":{"type":"string"}}, ["id"]),
}


def perform(action, args):
    if not QUEUE.is_dir():
        raise ValueError("此工作区未与旧 Codex 配对")
    if action == "status" and not args.get("id"):
        channel = read_json(QUEUE,"channel.json")
        channel["online"] = time.time()-channel["updated"] < 25 and channel["state"] not in ("unavailable","notLoaded")
        return channel
    ident = task_id(args["id"])
    request = ident+".request.json"
    if action == "submit":
        task = args["task"]
        parts(task)
        if not task.endswith(".md"):
            raise ValueError("任务单须为 Markdown 文件")
        includes = args.get("include",[])
        if not isinstance(includes,list) or len(includes)>100:
            raise ValueError("附件清单无效")
        data = pack(ROOT,[task,*includes])
        if task not in data:
            raise ValueError("没有找到可交接的任务单")
        payload = {"task":task,"files":data}
        try:
            publish(QUEUE,request,payload,exclusive=True)
        except FileExistsError:
            if read_json(QUEUE,request) != payload:
                raise ValueError("此编号已用于另一份内容，请使用新编号")
    try:
        state = read_json(QUEUE,ident+".status.json")
    except FileNotFoundError:
        if not (QUEUE/request).is_file():
            raise ValueError("没有这个任务")
        state = {"state":"queued","id":ident}
    if action == "collect":
        if not state.get("result"):
            return state
        dest = ROOT/"handoff/results"/ident
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
                stage = Path(tmp)/"result"
                stage.mkdir()
                unpack(read_json(QUEUE,ident+".result.json"),stage)
                try:
                    stage.rename(dest)
                except OSError:
                    if not dest.is_dir(): raise
        state["directory"] = str(dest)
    return state


def serve():
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if "id" not in req: continue
            method=req["method"]
            if method=="initialize":
                result={"protocolVersion":req.get("params",{}).get("protocolVersion","2024-11-05"),
                        "capabilities":{"tools":{}},"serverInfo":{"name":"dow-codex","version":"1.0"}}
            elif method=="tools/list":
                result={"tools":[{"name":name,"description":desc,"inputSchema":{"type":"object","properties":schema,"required":required,"additionalProperties":False}}
                                 for name,(desc,schema,required) in TOOLS.items()] if QUEUE.is_dir() else []}
            elif method=="tools/call":
                params=req["params"];name=params["name"]
                try:
                    if name not in TOOLS:raise ValueError("未知交接操作")
                    data=perform(name.split("_")[-1],params.get("arguments",{}))
                    result={"content":[{"type":"text","text":json.dumps(data,ensure_ascii=False)}]}
                except (ValueError,KeyError,TypeError,OSError) as error:
                    result={"isError":True,"content":[{"type":"text","text":str(error)}]}
            elif method=="ping":result={}
            else:
                print(json.dumps({"jsonrpc":"2.0","id":req["id"],"error":{"code":-32601,"message":"Unknown method"}}),flush=True)
                continue
            print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result}),flush=True)
        except (ValueError,KeyError,TypeError):
            continue


if __name__=="__main__":
    if sys.argv[1:]==["mcp"]:
        serve()
    else:
        parser=argparse.ArgumentParser(description=__doc__)
        commands=parser.add_subparsers(dest="action",required=True)
        submit=commands.add_parser("submit");submit.add_argument("id");submit.add_argument("task");submit.add_argument("include",nargs="*")
        commands.add_parser("status").add_argument("id",nargs="?")
        commands.add_parser("collect").add_argument("id")
        args=vars(parser.parse_args())
        try: print(json.dumps(perform(args.pop("action"),args),ensure_ascii=False))
        except (OSError,TypeError,ValueError,KeyError) as error:sys.exit(str(error))
