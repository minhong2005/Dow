"""Correlate local evidence around a Chicago wall-clock time or a user-observed challenge."""
import argparse
import datetime as dt
import fcntl
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from collections import Counter
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Chicago')
ROOT = Path.home()/'Library/Logs/Dow'


def main():
    parser = argparse.ArgumentParser(description='登录事件回查，时间按芝加哥当地时间')
    parser.add_argument('when', nargs='?', help='HH:MM 或 YYYY-MM-DDTHH:MM')
    parser.add_argument('--mark', choices=['手机验证','Chrome验证','Antigravity授权验证'])
    args = parser.parse_args()
    now = dt.datetime.now(ZONE)
    center = now
    if args.when:
        value = args.when if 'T' in args.when else f'{now.date()}T{args.when}'
        center = dt.datetime.strptime(value,'%Y-%m-%dT%H:%M').replace(tzinfo=ZONE)
    ts = int(center.timestamp())
    if ts > now.timestamp() or (now.date()-center.date()).days >= 30:
        parser.error('请输入最近 30 天内已经发生的时间')
    start, end = ts-300, min(int(now.timestamp()),ts+300)
    os.umask(0o077)
    db = sqlite3.connect((ROOT/'history.sqlite').as_uri()+'?mode=rw',uri=True,timeout=10)
    if args.mark:
        with (ROOT/'.collect.lock').open('a') as lock, db:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db.execute('insert or ignore into events values(?,?,?,?)',(ts,'用户观察',args.mark,f'mark/{ts}/{args.mark}'))
            day = str(center.date())
            pending = db.execute("select value from meta where key='pending'").fetchone()
            dates = set(json.loads(pending[0])) if pending else set()
            db.execute("insert or replace into meta values('pending',?)",(json.dumps(sorted(dates|{day})),))
    fmt = lambda t: dt.datetime.fromtimestamp(t,ZONE).strftime('%F %T %Z')
    out = [f'登录故障回查  {fmt(start)} 至 {fmt(end)}']
    for source in ('终端出口','浏览器保护'):
        row = db.execute('select ts,detail from events where source=? and ts<=? order by ts desc,rowid desc limit 1',(source,start)).fetchone()
        out.append(f'{source}在窗口开始前的最近记录  '+(f'{fmt(row[0])}  {row[1]}' if row else '未知'))
    for t,source,detail,n in db.execute('select ts,source,detail,count(*) from events where ts between ? and ? group by ts,source,detail order by ts,min(rowid)',(start,end)):
        out.append(f'{fmt(t)}  {source}  {detail}（{n} 条）')
    db.close()
    for layer in ('Chrome','网关'):
        counts = Counter()
        for day in {dt.datetime.fromtimestamp(x,ZONE).date() for x in (start,end)}:
            for file in (ROOT/'连接'/layer).glob(f'{day}-*.jsonl'):
                with file.open('rb') as stream:
                    for line in stream:
                        if not line.endswith(b'\n'):break
                        row = json.loads(line)
                        if row['event']!='end':continue
                        host = row.get('host') or ''
                        if not any(host==d or host.endswith('.'+d) for d in ('google.com','googleapis.com','gmail.com','antigravity.google')):continue
                        moment=dt.datetime.fromisoformat(row['time']).timestamp()
                        if start<=moment<end+1:counts[(host,row.get('result'),str(row.get('proxy_status')))]+=1
        out.append(f'{layer}  窗口内有结束记录的 Google 连接 {sum(counts.values())} 条')
        for (host,result,status),n in sorted(counts.items()):
            out.append(f'  {host}  {result}  代理响应 {status}  {n} 条')
    out.append('时间线中的验证提示来自用户标记。代理响应描述隧道，Google 页面给出的验证理由需对照提示原文。最近事件可能仍待下一轮同步。')
    body='\n'.join(out)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',dir=ROOT,delete=False) as f:
        f.write(body)
    os.replace(f.name,ROOT/'最近登录回查.txt')
    print(body,end='')


if __name__=='__main__':
    main()
