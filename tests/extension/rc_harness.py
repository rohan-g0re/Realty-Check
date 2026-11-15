"""Shared helpers for the extension tests and the headed walk-through.

- serve_fixtures(directory=None): static server for tests/extension/fixture (or `directory`) on a random 127.0.0.1 port.
- StubChecker: a tiny local checker speaking docs/CONTRACT.md (POST /check -> SSE Events).
- launch(): bundled Chromium with the unpacked extension loaded (launch_persistent_context).
"""
from __future__ import annotations

import functools
import io
import json
import shutil
import socket
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXT = REPO / "extension"
FIXTURE = HERE / "fixture"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass


def serve_fixtures(directory=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(directory or FIXTURE)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def free_port() -> int:
    """A port with nothing listening on it (used to make the checker 'not running')."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 40), (220, 228, 240)).save(buf, "PNG")
    return buf.getvalue()


STUB_HEADLINE = "Stub check: listed as 2 bedrooms, the scan shows 1."


def stub_events(address: str, quotes=None) -> list[dict]:
    """Contract Events for one run. `quotes` (3 strings) replaces the quotes of claims c1..c3."""
    evs = [
        {"type": "run_started", "run_id": "r1", "address": address},
        {"type": "step", "agent": "orchestrator", "text": "Reading the listing…"},
        {"type": "claims", "claims": [
            {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan E", "quote": "2 Beds", "checkable": True},
            {"id": "c2", "kind": "stories", "value": 5, "unit": None, "scope": "building", "label": None, "quote": "5-story building", "checkable": True},
            {"id": "c3", "kind": "year_built", "value": 2016, "unit": None, "scope": "building", "label": None, "quote": "Built in 2016", "checkable": True},
        ]},
        {"type": "step", "agent": "inside", "text": "Checking the 3D scan…"},
        {"type": "verdict", "verdict": {"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom",
                                        "source": "scan", "reason": "Stub reason.", "image": "/img/t1"}},
        {"type": "verdict", "verdict": {"claim_id": "c2", "verdict": "close", "claimed": "5 stories", "found": "5 or 6 floors",
                                        "source": "aerial", "reason": "Stub reason.", "image": None}},
        {"type": "verdict", "verdict": {"claim_id": "c3", "verdict": "confirmed", "claimed": "built in 2016", "found": "built in 2016",
                                        "source": "county", "reason": "Stub reason.", "image": None}},
        {"type": "done", "run_id": "r1", "counts": {"confirmed": 1, "contradicted": 1, "close": 1, "cant_check": 0},
         "headline": STUB_HEADLINE, "report_url": "/report/r1"},
    ]
    if quotes:
        for c, q in zip(evs[2]["claims"], quotes):
            c["quote"] = q
    return evs


class StubChecker:
    """`events`: a function address -> list of Events used instead of stub_events. An item {"_pause": s} waits s seconds."""

    def __init__(self, delay: float = 0.15, quotes=None, events=None):
        self.requests: list[dict] = []
        outer = self
        png = _png()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):
                if self.path == "/health":
                    body, ctype = b'{"ok": true}', "application/json"
                elif self.path == "/img/t1":
                    body, ctype = png, "image/png"
                elif self.path.split("?")[0] == "/tour/r1":
                    body, ctype = b"<!doctype html><title>Tour</title><h1>Tour</h1>", "text/html; charset=utf-8"
                elif self.path.split("?")[0] == "/artifacts/r1":
                    body, ctype = b"<!doctype html><title>Sources</title><h1>Sources</h1>", "text/html; charset=utf-8"
                elif self.path == "/report/r1":
                    body, ctype = f"<!doctype html><title>Report</title><h1 id='headline'>{STUB_HEADLINE}</h1>".encode(), "text/html; charset=utf-8"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                snap = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append(snap)
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                evs = events(snap.get("address_text", "")) if events else stub_events(snap.get("address_text", ""), quotes)
                for ev in evs:
                    if "_pause" in ev:
                        time.sleep(ev["_pause"])
                        continue
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(delay)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def launch(pw, headless: bool = True, slow_mo: int = 0, viewport=(1400, 900)):
    """Bundled Chromium with the unpacked extension. Returns (context, service_worker, user_data_dir)."""
    udd = tempfile.mkdtemp(prefix="rc_ext_")
    kw = dict(
        user_data_dir=udd,
        headless=headless,
        args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        viewport={"width": viewport[0], "height": viewport[1]},
        slow_mo=slow_mo,
    )
    if headless:
        kw["channel"] = "chromium"  # new headless, which supports extensions
    ctx = pw.chromium.launch_persistent_context(**kw)
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    return ctx, sw, udd


def set_server(sw, url: str):
    sw.evaluate("u => chrome.storage.local.set({rcServer: u})", url)


def cleanup(udd: str):
    shutil.rmtree(udd, ignore_errors=True)
