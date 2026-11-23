"""One-use loopback page served inside the workspace for a real Chrome IP sample."""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = sys.argv[1]
if not re.fullmatch(r"[0-9a-f]{48}", TOKEN):
    sys.exit(2)
PATH = "/" + TOKEN
PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Dow 浏览器出口</title>
<body><p>正在检测 Chrome 出口…</p><script nonce="TOKEN">
(async () => {
  async function sample(url, trace) {
    try {
      const response = await fetch(url + '?' + crypto.randomUUID(), {
        cache:'no-store', credentials:'omit', referrerPolicy:'no-referrer', signal:AbortSignal.timeout(12000)
      });
      if (!response.ok) return '';
      const text = (await response.text()).trim();
      return (trace ? (text.match(/^ip=(.+)$/m) || [])[1] || '' : text).slice(0, 64);
    } catch { return ''; }
  }
  const samples = await Promise.all([sample('https://api.ipify.org/', false),
    sample('https://www.cloudflare.com/cdn-cgi/trace', true), sample('https://checkip.amazonaws.com/', false)]);
  try {
    const response = await fetch(location.pathname, {method:'POST', credentials:'omit',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(samples), signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw Error();
    document.querySelector('p').textContent='检测结束，可以返回 Dow。';
    window.close();
  } catch { document.querySelector('p').textContent='检测结果未能送达 Dow，可以关闭此页。'; }
})();
</script></body></html>""".replace("TOKEN", TOKEN).encode()


class Page(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def valid_path(self):
        return self.path == PATH and self.headers.get("Host") == self.server.host

    def do_GET(self):
        if not self.valid_path():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{TOKEN}'; "
                         "connect-src 'self' https://api.ipify.org https://www.cloudflare.com https://checkip.amazonaws.com; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        if not self.valid_path() or self.headers.get("Origin") != "http://" + self.server.host:
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 256:
                raise ValueError()
            values = json.loads(self.rfile.read(size))
            if not isinstance(values, list) or len(values) != 3 or not all(isinstance(v, str) for v in values):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        self.send_response(204)
        self.end_headers()
        self.server.result = values

    def setup(self):
        super().setup()
        self.connection.settimeout(4)


with HTTPServer(("127.0.0.1", 0), Page) as server:
    server.host = f"dow-exit-check.localhost:{server.server_port}"
    server.timeout = 1
    server.result = None
    print(server.server_port, flush=True)
    deadline = time.monotonic() + 65
    while server.result is None and time.monotonic() < deadline:
        server.handle_request()
    if server.result is not None:
        print(json.dumps(server.result), flush=True)
