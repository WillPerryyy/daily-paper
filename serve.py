"""Serve the paper on this machine, with a live /api/live endpoint.

The repository is private, so there is no GitHub Pages copy to read any more.
This replaces it, and is better than what it replaces: Pages served whatever
the scheduled Action last committed, while this rebuilds the edition on the
spot in about three seconds, so the gate is current to the last close and the
headlines to the minute.

    python serve.py            # starts on http://localhost:8731 and opens it
    python serve.py --port N   # if that port is busy
    python serve.py --no-open  # just serve

Nothing is committed and nothing is written to data/live.json: the page gets
its edition straight from update_live.build_payload(). The committed file
stays as the offline fallback for when a fetch fails.
"""
import argparse
import json
import os
import sys
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from update_live import build_payload  # noqa: E402


class Paper(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") == "/api/live":
            return self.live()
        return super().do_GET()

    def live(self):
        try:
            payload, err = build_payload()
        except Exception:  # noqa: BLE001
            return self.json(500, {"ok": False,
                                   "error": traceback.format_exc(limit=4)})
        if err:
            return self.json(502, {"ok": False, "error": err})
        return self.json(200, payload)

    def json(self, code, body):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        # One line per page load is useful; the chart and font requests are not.
        if self.path.split("?")[0] in ("/", "/index.html", "/api/live"):
            sys.stderr.write("  %s %s\n" % (self.command, self.path.split("?")[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    url = f"http://localhost:{a.port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Paper)
    print(f"The Daily Paper is at {url}")
    print("The edition is rebuilt on every load. Ctrl-C to stop.")
    if not a.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
