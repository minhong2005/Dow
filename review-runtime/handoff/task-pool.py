"""One Codex thread per submitted task. Poll running turns, never resubmit them."""
import sys
sys.path.insert(0, "/opt/dow")
import service as s
import time
import fcntl

def tick(rpc, ident):
    binding = ident + ".thread"
    try:
        row = s.read_json(s.STATE, ident + ".json")
    except FileNotFoundError:
        row = {}
    if row.get("state") in s.FINAL:
        return
    try:
        thread = s.read_json(s.STATE, binding)
    except FileNotFoundError:
        s.publish(s.STATE, binding, None, exclusive=True)
        thread = rpc.call("thread/start", {"cwd":s.TARGET,"sandbox":"workspace-write","approvalPolicy":"never"})["thread"]["id"]
        s.publish(s.STATE, binding, thread)
    if not thread:
        s.record(ident,"uncertain",message="创建会话时连接中断，请核对后处理")
        return
    s.THREAD = thread
    status = rpc.call("thread/read", {"threadId":thread,"includeTurns":False})["thread"]["status"]
    state = status["type"]
    if state == "notLoaded":
        rpc.call("thread/resume", {"threadId":thread,"cwd":s.TARGET,"sandbox":"workspace-write","approvalPolicy":"never"})
        return
    s.handle(rpc,ident,state)
    row = s.read_json(s.STATE,ident+".json")
    if row["state"] == "running":
        visible = dict(row)
        if "waitingOnApproval" in status.get("activeFlags", []):
            visible.update(state="waiting_on_approval", message="Codex 正在等待批准")
        if s.read_json(s.EXCHANGE, ident+".status.json") != visible:
            s.publish(s.EXCHANGE, ident+".status.json", visible)
    if row["state"] in s.FINAL and row.get("result"):
        try:
            rpc.call("thread/archive",{"threadId":thread})
        except RuntimeError as error:
            # Delivery already succeeded. Keep the receipt available on archive failure.
            print("Archive failed for", ident, str(error)[:180], flush=True)

def main():
    with (s.STATE/"worker.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                with s.Rpc() as rpc:
                    while True:
                        s.publish(s.EXCHANGE,"channel.json",dict(target="按单新建 Codex",state="ready",updated=int(time.time())))
                        for path in sorted(s.EXCHANGE.glob("*.request.json")):
                            ident=path.name[:-13]
                            if not s.ID.fullmatch(ident):continue
                            try:tick(rpc,ident)
                            except (ValueError,KeyError,RuntimeError) as e:
                                s.record(ident,"needs_review",message=str(e)[:200])
                        time.sleep(2)
            except (OSError,s.WebSocketException,RuntimeError):
                time.sleep(5)
if __name__=="__main__":main()
