"""Run the app on a normal computer without Pyodide: the same Python core (standalone/boot.py) behind a small web
server. Open http://localhost:8099/?dev=1 - quick for developing the core and the panel.

    python3 tools/dev_server.py            (needs numpy and scikit-learn)
"""
import asyncio
import http.server
import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

WWW = Path(__file__).resolve().parent.parent / "www"
os.environ.setdefault("DATA_DIR", str(Path.home() / ".energipilot-dev"))
sys.path.insert(0, str(WWW / "py"))
from standalone import boot  # noqa: E402

loop = asyncio.new_event_loop()


def run_loop() -> None:
    asyncio.set_event_loop(loop)
    boot.start(os.environ.get("EP_MODE", "demo"))
    loop.run_forever()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WWW), **kw)

    def _api(self, method: str) -> bool:
        u = urlsplit(self.path)
        if not u.path.startswith("/api/"):
            return False
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() if method == "POST" else ""
        fut = asyncio.run_coroutine_threadsafe(boot.handle(method, u.path, json.dumps(dict(parse_qsl(u.query))), body), loop)
        r = json.loads(fut.result(120))
        data = r["body"].encode()
        self.send_response(r["status"])
        self.send_header("Content-Type", r["content_type"])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):  # noqa: N802
        if not self._api("GET"):
            super().do_GET()

    def do_POST(self):  # noqa: N802
        if not self._api("POST"):
            self.send_error(404)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    threading.Thread(target=run_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8099))
    print(f"Energipilot (dev): http://localhost:{port}/?dev=1")
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
