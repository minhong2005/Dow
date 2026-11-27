"""One short-lived UI operation at a time on the work window shared by its user."""
import contextlib
import json
import re
import secrets
import sqlite3
import time

TTL = 18
TOKEN = re.compile(r"[a-f0-9]{32}")
RECOVERY = {"restore", "reselect"}
ACTIONS = {"inspect", "click", "fill", "scroll_down", "scroll_up"} | RECOVERY
DESKTOP = {"screen","point_click","double_click","right_click","wheel_up","wheel_down","key","type"}
WINDOW_STATES = {"unknown", "desktop", "ready", "minimized", "hidden", "gone", "selecting"}
LEGACY_WINDOW_ERROR = "窗口已关闭或最小化，请恢复窗口后再查看。"


@contextlib.contextmanager
def transaction(root):
    db = sqlite3.connect(root / "window.sqlite3", timeout=2)
    try:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS session (slot INTEGER PRIMARY KEY, token TEXT, seen REAL)")
        db.execute("""CREATE TABLE IF NOT EXISTS request
            (slot INTEGER PRIMARY KEY, id TEXT, token TEXT, expires REAL, args TEXT,
             claimed INTEGER DEFAULT 0, reply TEXT)""")
        db.execute("DELETE FROM request WHERE expires <= ?", (time.time(),))
        yield db
        db.commit()
    finally:
        db.close()


def status(root):
    if not (root / "window.sqlite3").exists():
        return "not_shared"
    try:
        with contextlib.closing(sqlite3.connect(root / "window.sqlite3", timeout=1)) as db:
            row = db.execute("SELECT seen FROM session WHERE slot=1").fetchone()
    except sqlite3.OperationalError:
        return "not_shared"
    return "shared" if row and 0 <= time.time() - row[0] < 8 else "not_shared"


def validate(args):
    action = args.get("action") if isinstance(args,dict) else None
    if not isinstance(action,str) or action not in ACTIONS | DESKTOP:
        raise ValueError("不支持的窗口操作。")
    if action in DESKTOP:
        keys = {"action"} if action=="screen" else {"action","frame"}
        if action in {"point_click","double_click","right_click","wheel_up","wheel_down"}: keys.update(("x","y"))
        if action=="key": keys.add("key")
        if action=="type": keys.add("text")
        if set(args)!=keys or ("frame" in keys and not TOKEN.fullmatch(str(args["frame"]))):
            raise ValueError("请先用 screen 查看桌面并引用返回的 frame。")
        if any(type(args[k]) is not int or not 0<=args[k]<1600 for k in ("x","y") if k in keys):
            raise ValueError("坐标超出截图。")
        if "key" in keys and (not isinstance(args["key"],str) or not re.fullmatch(r"[a-zA-Z0-9+]{1,40}",args["key"])):
            raise ValueError("组合键格式无效。")
        if "text" in keys and (not isinstance(args["text"],str) or len(args["text"])>1000):
            raise ValueError("文本过长。")
        return
    keys = {"action"} if action == "inspect" or action in RECOVERY else {"action", "element"}
    if action == "fill":
        keys.add("text")
    if set(args) != keys or ("element" in args and not TOKEN.fullmatch(str(args["element"]))):
        raise ValueError("请先查看当前窗口，再使用返回的控件编号。")
    if action == "fill" and (not isinstance(args["text"], str) or len(args["text"]) > 1000):
        raise ValueError("每次输入最多 1000 个字符。")


def operate(root, args):
    validate(args)
    ident = secrets.token_hex(16)
    with transaction(root) as db:
        session = db.execute("SELECT token, seen FROM session WHERE slot=1").fetchone()
        if not session or not 0 <= time.time() - session[1] < 8:
            raise ValueError("Windows 当前没有共享窗口。请在 Windows 打开「共享工作窗口」并选择窗口。")
        if db.execute("SELECT 1 FROM request").fetchone():
            raise ValueError("另一个窗口操作仍在进行。")
        db.execute("INSERT INTO request(slot,id,token,expires,args) VALUES (1,?,?,?,?)",
                   (ident, session[0], time.time() + TTL, json.dumps(args, ensure_ascii=False)))
    try:
        deadline = time.monotonic() + TTL
        while time.monotonic() < deadline:
            with transaction(root) as db:
                row = db.execute("SELECT reply FROM request WHERE id=?", (ident,)).fetchone()
            if not row:
                break
            if row[0] is not None:
                reply = json.loads(row[0])
                if not reply["ok"] and reply["text"] == LEGACY_WINDOW_ERROR:
                    reply["text"] = "旧版 Windows 组件无法确定共享目标是失效、隐藏还是最小化。可在 Windows 重新选择窗口，或升级组件使用远程恢复。"
                return reply
            time.sleep(0.12)
        raise ValueError("工作窗口已切换或回执超时。操作可能已执行，请重新查看再决定。")
    finally:
        with transaction(root) as db:
            db.execute("DELETE FROM request WHERE id=?", (ident,))


def exchange(root, msg):
    token = msg.get("session")
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("invalid window session")
    with transaction(root) as db:
        op = msg["op"]
        if op == "windowStop":
            db.execute("DELETE FROM session WHERE token=?", (token,))
            db.execute("DELETE FROM request WHERE token=?", (token,))
            return {}
        if op == "windowPoll":
            prior = db.execute("SELECT token,seen FROM session WHERE slot=1").fetchone()
            if prior and prior[0] != token and time.time() - prior[1] < 8:
                return {"busy": True}
            db.execute("DELETE FROM request WHERE token<>?", (token,))
            db.execute("INSERT OR REPLACE INTO session VALUES(1,?,?)", (token,time.time()))
            row = db.execute("SELECT id,args,expires FROM request WHERE token=? AND claimed=0", (token,)).fetchone()
            if row:
                args = json.loads(row[1])
                if args["action"] in DESKTOP and msg.get("state")!="desktop":
                    reply={"ok":False,"text":"Windows 当前没有共享整个桌面。旧窗口共享继续可用。"}
                    db.execute("UPDATE request SET claimed=1, reply=? WHERE id=?", (json.dumps(reply),row[0]))
                    return {}
                if args["action"] in RECOVERY and msg.get("version") != 2:
                    reply = {"ok":False,"text":"Windows 正在运行旧版窗口共享组件。升级后可用 restore 和 reselect。"}
                    db.execute("UPDATE request SET claimed=1, reply=? WHERE id=?", (json.dumps(reply),row[0]))
                    return {}
                db.execute("UPDATE request SET claimed=1 WHERE id=?", (row[0],))
                return {"task": {"id": row[0], "args": args,
                                 "remainingMs": max(0,int((row[2]-time.time())*1000))}}
            return {}
        if op == "windowResult":
            reply = msg.get("reply")
            if (not isinstance(reply,dict) or set(reply)!={"ok","text"} or type(reply["ok"]) is not bool
                    or not isinstance(reply["text"],str) or len(reply["text"].encode())>64000):
                raise ValueError("invalid window result")
            db.execute("UPDATE request SET reply=? WHERE id=? AND token=? AND claimed=1 AND reply IS NULL",
                       (json.dumps(reply,ensure_ascii=False),msg.get("id"),token))
            return {}
        raise ValueError("unknown window operation")
