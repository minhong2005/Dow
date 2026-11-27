"""Check the event chain and purge only expired Dow monitoring records."""
import collections
import datetime as dt
import json
import os
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

import flow_store

TZ = ZoneInfo("America/Chicago")


def inspect_and_clean(root):
    now = dt.datetime.now(TZ)
    today = now.date()
    oldest = today - dt.timedelta(days=29)
    cutoff = int(dt.datetime.combine(oldest,dt.time(),TZ).timestamp())
    issues = []
    lines = [now.isoformat(timespec='seconds') + '  Dow 每日自检']
    db = sqlite3.connect(root/'history.sqlite', timeout=5)
    try:
        if db.execute('pragma quick_check').fetchall() != [('ok',)]:
            raise sqlite3.DatabaseError('quick_check')
        meta = dict(db.execute('select key,value from meta'))
        if time.time()-int(meta.get('heartbeat',0)) > 120:
            issues.append('状态记录超过两分钟未更新')
        if not meta.get('终端出口','').startswith('已验证') or '中转' not in meta.get('浏览器保护','') or '就绪；Chrome' not in meta.get('浏览器保护',''):
            issues.append('最近一次网关或浏览器检查未通过')
        if '失败' in meta.get('工具日志','') or '可读诊断日志：' not in meta.get('工具日志',''):
            issues.append('工具诊断日志采集不完整')
        counts = db.execute('select source,detail,count(*) from events where ts>=? and source in (?,?,?) group by source,detail',
                            (int(time.time())-86400,'Antigravity','Claude Code','Codex')).fetchall()
        for source,detail,n in counts:
            lines.append(f'近 24 小时  {source}  {detail}  {n} 条')
        with db:
            db.execute('delete from events where ts<?',(cutoff,))
            db.execute('delete from cursors where seen<?',(cutoff,))
        for f in root.glob('????-??-??.log'):
            if f.stem < str(oldest):
                f.unlink()
    except (sqlite3.DatabaseError, ValueError):
        issues.append('历史数据库检查失败')
    finally:
        db.close()
    try:
        if '积压' in flow_store.mirror(root):
            issues.append('网关副本正在补齐')
        flow_store.cleanup(root,str(oldest))
    except (OSError,ValueError,subprocess.SubprocessError):
        issues.append('网关副本或清理未完成')
    size = 0
    for source in ('网关','Chrome'):
        files = sorted((root/'连接'/source).glob('*.jsonl'))
        size += sum(f.stat().st_size for f in files)
        counters = collections.Counter()
        # Include yesterday so pre-midnight starts are matched with today's closes.
        recent = [f for f in files if f.name[:10] >= str(today-dt.timedelta(days=1))]
        outstanding = {}
        latest_run = None
        outcomes = collections.Counter()
        for f in sorted(recent,key=lambda f:f.stat().st_mtime):
            with f.open('rb') as fd:
                for raw in fd:
                    if not raw.endswith(b'\n'):
                        if f != recent[-1] and f.name[:10] < str(today):
                            issues.append(source+' 存在未完成的日志行')
                        break
                    try:
                        row = json.loads(raw); event = row['event']; run = row['run']
                        if event == 'recorder_started':
                            latest_run = run
                        if event == 'start':
                            outstanding[row['id']] = run
                        elif event == 'end':
                            outstanding.pop(row['id'],None)
                        if row['time'][:10]==str(today):
                            counters[event] += 1
                            if event == 'end':
                                result = row.get('result', 'unknown')
                                outcomes[result] += 1
                                status = row.get('proxy_status')
                                if isinstance(status, int) and status >= 400:
                                    counters['rejected_response'] += 1
                        if row.get('previous_write_failures'):
                            counters['lost'] += row['previous_write_failures']
                    except (ValueError,KeyError,TypeError):
                        counters['invalid'] += 1
        if recent and latest_run is None:
            latest_run = max(recent,key=lambda f:f.stat().st_mtime).stem[11:]
        unclosed = sum(r!=latest_run for r in outstanding.values())
        lines.append(f"{source} 今天开始 {counters['start']}，结束 {counters['end']}；旧进程缺结束记录 {unclosed}")
        lines.append("  结束结果  " + ("，".join(f"{k} {v}" for k,v in sorted(outcomes.items())) or "暂无"))
        if counters['rejected_response']:
            lines.append(f"  代理可见的 4xx/5xx 响应 {counters['rejected_response']} 条")
        if not files or counters['invalid'] or counters['lost'] or unclosed:
            issues.append(source+' 记录存在缺口或尚未生成')
    lines.append(f'连接文件共 {size/1024/1024:.2f} MiB；保留日期自 {oldest} 起')
    lines.extend('需检查  '+i for i in issues)
    if not issues:
        lines.append('检查通过')
    path = root/'最近每日自检.txt'
    tmp = path.with_suffix('.tmp')
    tmp.write_text('\n'.join(lines)+'\n')
    os.replace(tmp,path)
    print('\n'.join(lines))
    return int(bool(issues))
