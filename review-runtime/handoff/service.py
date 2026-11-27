"""A paired local delivery worker. It can start a turn only on the configured thread."""
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid
sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,"/opt/dow/websocket.whl")
from websocket import create_connection, WebSocketException
from files import DIR, ID, MAX_BYTES, MAX_FILES, pack, parts, publish, read_at, read_json, unpack
import project_copy

EXCHANGE=Path("/exchange")
DELIVERY=Path("/delivery")
STATE=Path("/state")
SOCKET="/control/client.sock"
THREAD=os.environ.get("DOW_THREAD","")
TARGET=os.environ.get("DOW_TARGET_DIR","")
FINAL={"done","blocked","needs_review","uncertain"}


class Rpc:
    def __enter__(self):
        sock=socket.socket(socket.AF_UNIX)
        try:
            sock.settimeout(12)
            sock.connect(SOCKET)
            self.ws=create_connection("ws://localhost/",socket=sock,timeout=12)
            self.seq=0
            self.call("initialize",{"clientInfo":{"name":"dow-handoff","version":"1"},"capabilities":{"experimentalApi":True}})
            self.ws.send(json.dumps({"jsonrpc":"2.0","method":"initialized"}))
            return self
        except BaseException:
            sock.close();raise

    def __exit__(self,*exc):
        self.ws.close(timeout=1)

    def call(self,method,params):
        self.seq+=1
        self.ws.send(json.dumps({"jsonrpc":"2.0","id":self.seq,"method":method,"params":params}))
        while True:
            reply=json.loads(self.ws.recv())
            if reply.get("id")==self.seq and "method" not in reply:
                if "error" in reply:
                    raise RuntimeError(method+": "+str(reply["error"].get("message","请求失败"))[:180])
                return reply["result"]


def record(ident,state,**values):
    row={"id":ident,"state":state,**values}
    publish(STATE,ident+".json",row)
    publish(EXCHANGE,ident+".status.json",row)
    return row


def stage(ident):
    payload=read_json(EXCHANGE,ident+".request.json")
    task=payload["task"]
    parts(task)
    if not task.endswith(".md") or task not in payload["files"]:
        raise ValueError("任务单不存在")
    folder=DELIVERY/ident
    folder.mkdir(mode=0o700)
    fd=os.open(folder,DIR)
    try:
        os.mkdir("work",0o700,dir_fd=fd)
        work=os.open("work",DIR,dir_fd=fd)
        try:unpack(payload["files"],work)
        finally:os.close(work)
    finally:os.close(fd)
    return task


def collect(ident,turn_state):
    fd=os.open(DELIVERY/ident,DIR)
    try:
        work=os.open("work",DIR,dir_fd=fd)
        try:
            names=[n for n in os.listdir(work) if n != ".dow-dispatch" and not project_copy.skip(n)]
            bundle={"work/"+name:data for name,data in pack(work,names).items()}
        finally:os.close(work)
        receipt_present=False
        try:
            receipt=pack(fd,["receipt.md"])
            bundle.update(receipt)
            raw=read_at(fd,"receipt.md",128*1024)
            receipt_present=bool(raw.strip())
        except FileNotFoundError:pass
    finally:os.close(fd)
    if len(bundle)>MAX_FILES or sum(len(s)//4*3-s.count("=") for s in bundle.values())>MAX_BYTES:
        raise ValueError("结果超过交接大小限制，文件已保留在执行目录")
    publish(EXCHANGE,ident+".result.json",bundle)
    flags={"result":True,"turnStatus":turn_state}
    if turn_state!="completed":return record(ident,"blocked",**flags)
    if receipt_present:
        first=raw.decode("utf8",errors="replace").splitlines()[0].strip().lower()
        if first in ("done","blocked"):
            return record(ident,first,**flags)
    return record(ident,"needs_review",message="本轮已结束，尚无格式完整的回执",**flags)


def find_turn(rpc, row):
    cursor=None
    while True:
        params={"threadId":THREAD,"limit":16,"sortDirection":"desc","itemsView":"notLoaded" if row.get("turn") else "full"}
        if cursor:params["cursor"]=cursor
        page=rpc.call("thread/turns/list",params)
        for turn in page["data"]:
            if row.get("turn"):
                if turn["id"]==row["turn"]:return turn
            else:
                for item in turn.get("items",[]):
                    if item.get("type")=="userMessage" and any(
                        c.get("type")=="text" and c.get("text","").startswith(row["marker"]+"\n")
                        for c in item.get("content",[])):
                        return turn
        cursor=page.get("nextCursor")
        if not cursor:return None


def handle(rpc,ident,thread_state):
    try: row=read_json(STATE,ident+".json")
    except FileNotFoundError:row=None
    if row and row["state"] in FINAL:
        if not (EXCHANGE/(ident+".status.json")).is_file():
            publish(EXCHANGE,ident+".status.json",row)
        return False
    if row and row["state"] in ("sending","running"):
        turn=find_turn(rpc,row)
        if turn is None:
            record(ident,"uncertain",message="无法从旧对话核对该次投递；已停止重发")
            return True
        if turn["status"]=="inProgress":
            if row.get("turn")!=turn["id"]:record(ident,"running",turn=turn["id"])
            return True
        collect(ident,turn["status"])
        return False
    if thread_state!="idle":
        if not row:record(ident,"queued",message="等待旧对话空闲")
        return False
    task=stage(ident)
    path=TARGET+"/"+ident
    marker="[Dow handoff "+ident+" "+uuid.uuid4().hex+"]"
    prompt=(marker+"\nAntigravity 已派单。本单工作目录为 "+path+"/work，任务单为其中的 "+task+
            "。先读任务单和随附输入，在本单 work 中完成并验收。任务单中作为项目根的 /work/main 指本单 work。"
            "请遵守任务边界。完成或阻塞时，将回执另写到 "+path+"/receipt.md，首行单独写 done 或 blocked，"
            "在后面写明改动、实际测试和遗留问题。")
    record(ident,"sending",marker=marker)
    policy = {"approvalPolicy":"never","sandboxPolicy":{"type":"workspaceWrite","writableRoots":[path],"networkAccess":False}, "cwd":path} if os.environ.get("DOW_POOL")=="1" else {}
    turn=rpc.call("turn/start",{**policy,"threadId":THREAD,"clientUserMessageId":str(uuid.uuid4()),
                              "input":[{"type":"text","text":prompt,"text_elements":[]}]})["turn"]
    record(ident,"running",turn=turn["id"])
    return True


def cycle(rpc):
    info=rpc.call("thread/read",{"threadId":THREAD,"includeTurns":False})["thread"]
    state=info["status"]["type"]
    if state=="notLoaded" and os.environ.get("DOW_POOL")=="1":
        state=rpc.call("thread/resume",{"threadId":THREAD,"cwd":TARGET,"sandbox":"workspace-write"})["thread"]["status"]["type"]
    publish(EXCHANGE,"channel.json",{"target":"旧 Codex","state":state,"updated":int(time.time())})
    # Finish or reconcile in-flight deliveries before considering another new order.
    ids={f.name[:-13] for f in EXCHANGE.glob("*.request.json") if ID.fullmatch(f.name[:-13])}
    ids.update(f.stem for f in STATE.glob("*.json") if ID.fullmatch(f.stem))
    def priority(ident):
        try: row=read_json(STATE,ident+".json")
        except FileNotFoundError:return (1,ident)
        return (0 if row["state"] in ("sending","running") else 1,ident)
    for ident in sorted(ids,key=priority):
        try:
            if handle(rpc,ident,state):break
        except (OSError,KeyError,TypeError,ValueError) as error:
            if isinstance(error,(ConnectionError,TimeoutError)):raise
            record(ident,"blocked",message=str(error)[:240])


def main():
    if not THREAD or not TARGET.startswith("/work/"):raise ValueError("缺少配对配置")
    with (STATE/"worker.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                with Rpc() as rpc:
                    while True:
                        cycle(rpc)
                        time.sleep(3)
            except (OSError,RuntimeError,WebSocketException,ValueError) as error:
                publish(EXCHANGE,"channel.json",{"target":"旧 Codex","state":"unavailable",
                         "message":str(error)[:200],"updated":int(time.time())})
                time.sleep(4)


if __name__=="__main__":main()
