"""Run inside a Dow workspace. Return classifications and file positions only."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

DAY = 86400
GLOG = re.compile(r'^E(\d{2})(\d{2}) (\d\d:\d\d:\d\d)\.\d+\s+\d+ errorreport\.go:\d+\] (.*)')
AUTH_LOG = re.compile(r'^[EW](\d{2})(\d{2}) (\d\d:\d\d:\d\d)\.\d+\s+\d+ [\w.-]+\.go:\d+\] (.*)')
AUTH = {
    'reauth': re.compile(r'\binvalid_rapt\b', re.I),
    'grant': re.compile(r'\binvalid_grant\b', re.I),
    'signed_out': re.compile(r'not logged into Antigravity', re.I),
    'refresh_failed': re.compile(r'failed to refresh (?:the )?(?:oauth|access token|token source|auth token|credentials)', re.I),
}
CLAUDE = re.compile(r'^(\d{4}-\d\d-\d\dT[\d:.]+Z) \[(?:ERROR|WARN)\] (.*)', re.I)
BUDDY = re.compile(r'^\[([^\]]+)\] \[(?:ERROR|WARN)\] (.*)', re.I)
CODES = {
    '401': re.compile(r'\bUNAUTHENTICATED\b|invalid authentication credentials|\b(?:code|status(?: code)?|HTTP(?:/\S+)?)\W{1,6}401\b|\b401 (?:Unauthorized|authentication_error)', re.I),
    '429': re.compile(r'\bRESOURCE_EXHAUSTED\b|individual quota reached|\b(?:code|status(?: code)?|HTTP(?:/\S+)?)\W{1,6}429\b|\b429 (?:Too Many Requests|rate_limit_error)', re.I),
    '403': re.compile(r'\b(?:code|status(?: code)?|HTTP(?:/\S+)?)\W{1,6}403\b|\b403 Forbidden', re.I),
    '5xx': re.compile(r'\b(?:code|status(?: code)?|HTTP(?:/\S+)?)\W{1,6}5\d\d\b', re.I),
}


def classify(body):
    return next((code for code, pattern in CODES.items() if pattern.search(body)), None)


def parse_line(tool, line, modified):
    if tool == 'Antigravity':
        m = AUTH_LOG.match(line) if any(rx.search(line) for rx in AUTH.values()) else GLOG.match(line)
        if not m:
            return
        ref = dt.datetime.fromtimestamp(modified)
        stamp = dt.datetime.strptime(f'{ref.year} {m[1]} {m[2]} {m[3]}', '%Y %m %d %H:%M:%S')
        if stamp > ref + dt.timedelta(days=2):
            stamp = stamp.replace(year=stamp.year - 1)
    elif tool == 'Claude Code':
        m = CLAUDE.match(line)
        if not m:
            return
        stamp = dt.datetime.fromisoformat(m[1].replace('Z', '+00:00'))
    else:
        m = BUDDY.match(line)
        if not m:
            return
        stamp = dt.datetime.strptime(m[1], '%m/%d/%Y, %I:%M:%S %p.%f')
    body = m.groups()[-1]
    code = next((k for k,rx in AUTH.items() if rx.search(body)), None) if tool == "Antigravity" else None
    code = code or classify(body)
    if not code and (tool=='Antigravity' or '[ERROR]' in line.upper()):
        code = 'other'
    if code:
        return int(stamp.timestamp()), code


def scan(cursors):
    home = Path.home()
    now = int(time.time())
    offsets, events, sources = {}, [], set()
    files = [('Antigravity', '.gemini/antigravity-cli/log/cli-*.log'),
             ('Claude Code', '.claude/debug/*.txt')]
    def record(key, pos, tool, parsed):
        if parsed and now - 30 * DAY <= parsed[0] <= now + 300:
            events.append([key, pos, tool, *parsed])

    for tool, pattern in files:
        for f in home.glob(pattern):
            if f.is_symlink() or not f.resolve().is_relative_to(home) or not f.is_file():
                continue
            st = f.stat()
            key = f'{st.st_dev}:{st.st_ino}'
            if st.st_mtime < now - DAY and key not in cursors:
                continue
            if st.st_mtime < now - 30 * DAY:
                continue
            sources.add(tool)
            pos = cursors.get(key, 0)
            if pos > st.st_size:
                pos = 0
            if pos == st.st_size:
                offsets[key] = pos
                continue
            with f.open('rb') as fd:
                fd.seek(pos)
                stop = fd.tell() + 2 * 1024**2
                while fd.tell() < stop:
                    start = fd.tell()
                    line = fd.readline(65536)
                    if not line or (not line.endswith(b'\n') and len(line) < 65536):
                        fd.seek(start)
                        break
                    try:
                        parsed = parse_line(tool, line.decode('utf-8','replace'), st.st_mtime)
                    except ValueError:
                        continue
                    record(key, start, tool, parsed)
                offsets[key] = fd.tell()

    db = Path(os.environ.get('CODEX_HOME', str(home/'.codex'))) / 'logs_2.sqlite'
    if db.is_file() and db.resolve().is_relative_to(home):
        st = db.stat()
        key = f'db:{st.st_dev}:{st.st_ino}'
        c = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=1)
        try:
            last = c.execute('select coalesce(max(id),0) from logs').fetchone()[0]
            pos = cursors.get(key, max(0, last - 20000))
            if pos > last:
                pos = 0
            stop = min(last, pos + 20000)
            rows = c.execute("""select id,ts,substr(feedback_log_body,1,4096),level from logs
                where id>? and id<=? and level in ('ERROR','WARN') and
                (target like 'codex_api::%' or target like 'codex_core::client%' or target like 'codex_core::stream%')""", (pos, stop))
            for row, ts, body, level in rows:
                code = classify(body or '') or ('other' if level=='ERROR' else None)
                if code:
                    record(key, row, 'Codex', (ts, code))
            sources.add('Codex')
            offsets[key] = stop
        finally:
            c.close()
    return {'offsets': offsets, 'events': events, 'sources': sorted(sources)}


if __name__ == '__main__':
    print(json.dumps(scan(json.load(sys.stdin))))
