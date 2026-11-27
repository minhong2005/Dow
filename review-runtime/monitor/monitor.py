"""Sample Dow locally. Persist a private timeline, without opening a browser or running model prompts."""
import concurrent.futures
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path.home() / 'Library/Logs/Dow'
APP = Path.home() / 'Applications/Dow.app/Contents/Resources'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_store
sys.path.insert(0, str(APP))
import backend as dow

TZ = ZoneInfo('America/Chicago')
DAY = 86400
TOOLS = {'Antigravity', 'Claude Code', 'Codex'}
CODES = {'reauth': 'OAuth 要求重新认证 invalid_rapt', 'grant': 'OAuth 授权失效 invalid_grant',
         'signed_out': 'Antigravity 报告未登录', 'refresh_failed': '凭据刷新失败', 'other': '工具报告错误（未分类）', '401': '401 请求认证失败', '403': '403 请求被拒绝', '429': '429 配额或速率限制', '5xx': '5xx 服务端错误'}
GATE = {'checking': '核验中', 'expired': '核验已过期', 'probe_failed': '探测失败', 'mismatch': '出口不符', 'unknown': '未取得状态'}
SOURCE = Path(__file__).with_name('agent_events.py').read_text()
DOCKER = dow.DOCKER[:-1]


def stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, TZ).strftime('%Y-%m-%d %H:%M:%S %Z')


def browser():
    try:
        blocked = dow.browser_relay('blockState').get('block')
        if blocked is not None:
            return '已阻断：浏览器曾报告出口不符'
        dow.browser_relay('health')
    except (OSError, ValueError, RuntimeError):
        return '中转未就绪或无法读取'
    try:
        result = dow.chrome('guard')
        return '中转和启动保护就绪；Chrome 已打开' if result.get('pid') else '中转就绪；Chrome 已关闭'
    except (OSError, ValueError, RuntimeError):
        return '中转就绪；启动保护未通过或无法读取'


GATE_MESSAGES = {
    'fixed exit confirmed by independent probes': '重新核验通过',
    'probe_failed, closing clients': '探测失败，关闭已有连接',
    'expired, closing clients': '核验过期，关闭已有连接',
    'mismatch, closing clients': '检测到出口不符，关闭已有连接',
}


def gateway_events(since):
    cutoff = int(time.time())
    try:
        result = subprocess.run([*DOCKER,'logs','--timestamps','--since',str(since-2),
                                 '--until',str(cutoff),'--tail','4000','aiwork-gateway'],
                                capture_output=True,timeout=6,check=True)
    except (OSError, subprocess.SubprocessError):
        return [], since, '无法读取网关事件'
    found = []
    lines = (result.stdout + b'\n' + result.stderr).splitlines()
    for line in lines:
        when, sep, msg = line.partition(b' Dow gateway: ')
        detail = GATE_MESSAGES.get(msg.decode('ascii', 'replace'))
        if sep and detail and re.fullmatch(rb'[\d:T.Z-]{20,35}',when):
            iso = when.decode('ascii')
            # Apple's Python 3.9 accepts microsecond, not nanosecond ISO strings.
            moment = dt.datetime.strptime(iso[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=dt.timezone.utc)
            found.append((int(moment.timestamp()), '网关事件', detail, 'gateway/'+iso+'/'+detail))
    return found, cutoff, '已读取（本轮达到行数上限）' if len(lines)>4000 else '已读取'


def runtime_homes():
    names = dow.run([*DOCKER, 'ps', '--format', '{{.Names}}'], timeout=4).splitlines()
    names = sorted(n for n in names if n in ('aiwork-tools','aiwork-codex') or dow.project_runtime.PROJECT_CONTAINER.fullmatch(n))
    if not names:
        return []
    fmt = '{{.Name}} {{range .Mounts}}{{if eq .Destination "/home/node"}}{{.Name}}{{end}}{{end}}'
    rows = dow.run([*DOCKER, 'inspect', '-f', fmt, *names], timeout=5)
    homes = {}
    for row in rows.splitlines():
        fields = row.strip('/ ').split()
        if fields and fields[0] in names:
            homes.setdefault(fields[-1], fields[0])
    return list(homes.values())


def read_agents(offsets):
    # The worker only returns bounded classifications; raw diagnostic lines stay in the container.
    events, updates, sources = [], {}, set()
    failed = 0
    try:
        homes = runtime_homes()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return events, updates, '无法连接 Docker'
    for box in homes:
        try:
            proc = subprocess.run([*DOCKER,'exec','-i',box,'/usr/bin/python3','-I','-c',SOURCE],
                input=json.dumps(offsets), text=True, capture_output=True, timeout=9, check=True)
            data = json.loads(proc.stdout)
            for key, pos in data['offsets'].items():
                if re.fullmatch(r'(?:db:)?\d+:\d+', key) and type(pos) is int and pos >= 0:
                    updates[key] = pos
            sources.update(TOOLS.intersection(data['sources']))
            for key, pos, tool, ts, code in data['events']:
                if key in updates and tool in TOOLS and code in CODES and type(ts) is int and type(pos) is int:
                    events.append((ts, tool, CODES[code], f'{key}/{pos}/{ts}/{code}'))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            failed += 1
    msg = '有可读诊断日志：' + '、'.join(sorted(sources)) if sources else '未找到可读的诊断日志'
    if failed:
        msg += f'；{failed} 个环境采集失败'
    return events, updates, msg


def write_text(path, text):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def render_day(db, day):
    start = int(dt.datetime.combine(day, dt.time(), TZ).timestamp())
    end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), TZ).timestamp())
    rows = db.execute('''select ts,source,detail,count(*) from events where ts>=? and ts<?
                         group by ts,source,detail order by ts,min(rowid)''', (start,end))
    lines = [f'{stamp(ts)}  [{source}]  {detail}' + (f'（同秒 {n} 条日志）' if n>1 else '')
             for ts,source,detail,n in rows]
    write_text(ROOT / f'{day}.log', '\n'.join(lines)+'\n')


def sample():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(ROOT/'history.sqlite', timeout=4)
    with db:
        db.executescript("""
          create table if not exists events(ts integer, source text, detail text, fingerprint text unique);
          create index if not exists event_time on events(ts);
          create table if not exists meta(key text primary key, value text);
          create table if not exists cursors(key text primary key, pos integer, seen integer);
        """)
        meta = dict(db.execute('select key,value from meta'))
        offsets = dict(db.execute('select key,pos from cursors'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            g = pool.submit(dow.gateway_status)
            b = pool.submit(browser)
            a = pool.submit(read_agents, offsets)
            w = pool.submit(gateway_events, int(meta.get('gateway_cursor', int(time.time())-DAY)))
            gate, chrome, (events, updates, coverage) = g.result(), b.result(), a.result()
            transitions, watermark, journal = w.result()
        try:
            mirrored = flow_store.mirror(ROOT)
        except (OSError, ValueError, subprocess.SubprocessError):
            mirrored = '网关连接记录暂未同步'
        now = int(time.time())
        dirty = set()
        def event(ts, source, detail, fingerprint=None):
            cursor = db.execute('insert or ignore into events values(?,?,?,?)',(ts,source,detail,fingerprint))
            if cursor.rowcount:
                dirty.add(dt.datetime.fromtimestamp(ts,TZ).date())
        if not meta:
            event(now,'监控','开始采集；此前的工具错误来自可用诊断日志，历史网络状态未知')
        previous = int(meta.get('heartbeat',now))
        if now - previous > 100:
            event(now,'监控',f'观测间隔 {now-previous} 秒；中间状态未知')
        terminal = GATE.get(gate['state'],'未知')
        if gate['state'] == 'verified':
            terminal = '已验证 ' + gate['ip']
            generation = gate['generation']
            if meta.get('generation') and meta['generation'] != generation:
                event(now,'终端出口','网关已重新确认出口；自上次有效采样后核验周期发生变化')
            db.execute('insert or replace into meta values(?,?)',('generation',generation))
        current = {'终端出口':terminal, '浏览器保护':chrome, '网关日志':journal, '工具日志':coverage, '连接记录':mirrored}
        for label, value in current.items():
            if meta.get(label) != value:
                event(now,label,value)
        for ts, tool, detail, fingerprint in events + transitions:
            if now-30*DAY <= ts <= now+300:
                event(ts,tool,detail,fingerprint)
        db.executemany('insert or replace into cursors values(?,?,?)',[(k,v,now) for k,v in updates.items()])
        today = dt.datetime.fromtimestamp(now,TZ).date()
        oldest = today - dt.timedelta(days=29)
        if meta.get('date') != str(today):
            event(now,'监控','当日首次采样')
            for label,value in current.items():
                if meta.get(label) == value:
                    event(now,label,value)
        db.executemany('insert or replace into meta values(?,?)',list(current.items())+
                       [('heartbeat',str(now)),('date',str(today)),('gateway_cursor',str(watermark))])
        # If the process stops after commit, the next pass still exports each affected day.
        pending = set(json.loads(meta.get('pending','[]'))) | {str(d) for d in dirty}
        db.execute('insert or replace into meta values(?,?)',('pending',json.dumps(sorted(pending))))
    for day in sorted(pending):
        date = dt.date.fromisoformat(day)
        if date >= oldest:
            render_day(db,date)
    with db:
        db.execute('delete from meta where key="pending"')
    lines = ['Dow 监控', '最后采样  '+stamp(now), *(f'{k}  {v}' for k,v in current.items()),
             '', '同目录的日期日志按芝加哥时间保存最近 30 个日历日。',
             '每条代理连接的起止及结果见「连接」子目录，按同一 id 配对。',
             '最后采样超过两分钟，请把这段时间视为未知。浏览器状态来自保护服务，未发起网页请求。']
    write_text(ROOT/'当前状态.txt','\n'.join(lines)+'\n')
    db.close()


if __name__ == '__main__':
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROOT/'.collect.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if sys.argv[1:] == ['--daily']:
            from daily import inspect_and_clean
            sys.exit(inspect_and_clean(ROOT))
        sample()
