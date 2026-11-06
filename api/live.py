"""Rebuild the whole edition on request, so the page is never stale.

Why this exists: the page cannot do it itself. Yahoo refuses cross-origin
browser requests on both of its hosts, and so do all but four of the RSS
feeds, so a static page has no way to refresh its own contents. The scheduled
GitHub Action that writes data/live.json is the alternative, and it is slow by
construction - GitHub drops about half its cron fires and lags the rest by 8
to 162 minutes, then the commit has to trigger a Pages rebuild.

This calls update_live.build_payload(), the same function the scheduled job
calls. One gate implementation, verified against tqqq-strategy/spec.py; a
second copy here would be free to drift from it silently.

data/live.json stays as the fallback for when this is unreachable.
"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from update_live import build_payload  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            payload, err = build_payload()
        except Exception:  # noqa: BLE001
            self._send(500, {"ok": False,
                             "error": traceback.format_exc(limit=4)})
            return
        if err:
            self._send(502, {"ok": False, "error": err})
            return
        # 60 seconds at the edge. The upstream feeds do not publish faster than
        # that, and without it a reload loop would put the whole feed list
        # through on every keypress. stale-while-revalidate keeps the page
        # instant while the next rebuild happens behind it.
        self._send(200, payload,
                   cache="public, s-maxage=60, stale-while-revalidate=900")

    def do_OPTIONS(self):
        self._send(204, None)

    def _send(self, code, body, cache="no-store"):
        raw = b"" if body is None else \
            json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        # Public market data and public RSS, no credentials, so any origin may
        # read it - the GitHub Pages copy of the page included.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if raw:
            self.wfile.write(raw)
