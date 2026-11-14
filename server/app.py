"""Local server: POST /check streams Events (docs/CONTRACT.md section 5)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from rc_common import cache_dir
from server.report import render_report
from server import tour

EVENT_TYPES = {"run_started", "step", "claims", "match", "evidence", "verdict", "done", "error"}
PING_S = 10.0
REPLAY_GAP = 0.4
REPLAY_TOTAL_S = 6.0  # a replay never takes longer than this
RUN_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
TOKEN_RE = re.compile(r"^[a-f0-9]{16}$")

app = FastAPI(title="Reality Check")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://[a-z0-9]+|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)

_IMAGES: dict[str, str] = {}  # token -> absolute file path
_IMAGES_LOCK = threading.Lock()


# ---------- runner selection ----------

def pick_runner():
    """(name, run_check). Real harness unless RC_FAKE=1 or it cannot be imported."""
    if os.environ.get("RC_FAKE") != "1":
        try:
            from harness.graph import run_check
            return "real", run_check
        except Exception:
            pass
    from server.fake_run import run_fake
    return "fake", run_fake


# ---------- images ----------

def _register(path) -> str | None:
    if not path or not isinstance(path, str) or path.startswith("/img/"):
        return path
    token = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    with _IMAGES_LOCK:
        _IMAGES[token] = path
    return f"/img/{token}"


def rewrite_images(ev: dict, images: dict[str, str]) -> dict:
    """Replace absolute image paths with /img/<token>; record token -> path in `images`."""
    ev = json.loads(json.dumps(ev, default=str))  # deep copy, JSON-safe
    refs = []
    if ev.get("type") == "evidence":
        refs = [im for im in (ev.get("evidence") or {}).get("images") or [] if isinstance(im, dict)]
        for im in refs:
            old = im.get("path")
            im["path"] = _register(old)
            if old and im["path"] != old:
                images[im["path"][5:]] = old
    if ev.get("type") == "verdict":
        v = ev.get("verdict") or {}
        old = v.get("image")
        v["image"] = _register(old)
        if old and v["image"] != old:
            images[v["image"][5:]] = old
    return ev


def _lookup_image(token: str) -> str | None:
    with _IMAGES_LOCK:
        if token in _IMAGES:
            return _IMAGES[token]
    for f in cache_dir("runs").glob("*.json"):
        try:
            imgs = json.loads(f.read_text(encoding="utf-8")).get("images") or {}
        except Exception:
            continue
        if token in imgs:
            with _IMAGES_LOCK:
                _IMAGES.update(imgs)
            return imgs[token]
    return None


# ---------- persistence ----------

def norm_address(a: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).split())


def _run_path(run_id: str) -> Path:
    return cache_dir("runs") / f"{run_id}.json"


def save_run(run: dict) -> None:
    p = _run_path(run["run_id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load_run(run_id: str) -> dict | None:
    if not RUN_ID_RE.match(run_id or ""):
        return None
    p = _run_path(run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def find_finished_run(address: str) -> dict | None:
    key = norm_address(address)
    if not key:
        return None
    best = None
    for f in cache_dir("runs").glob("*.json"):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if run.get("address_key") == key and run.get("finished") \
                and run["events"] and run["events"][-1].get("type") == "done":
            if best is None or run.get("started_at", 0) > best.get("started_at", 0):
                best = run
    return best


# ---------- running ----------

def start_run(snapshot: dict, base_url: str) -> queue.Queue:
    """Run in a worker thread; events go to the returned queue (None = end). Run is saved either way."""
    q: queue.Queue = queue.Queue()
    run_id = time.strftime("%Y%m%d-%H%M%S-").lower() + uuid.uuid4().hex[:6]
    name, runner = pick_runner()
    run = {"run_id": run_id, "harness": name,
           "snapshot": {k: snapshot.get(k) for k in ("url", "title", "address_text")},
           "address_key": norm_address(snapshot.get("address_text")),
           "started_at": time.time(), "finished_at": None, "finished": False,
           "events": [], "event_times": [], "images": {}}
    lock = threading.Lock()

    def push(ev: dict) -> None:
        if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
            return
        with lock:
            if run["finished"]:
                return
            if ev["type"] == "run_started" and run["events"]:
                return  # the server already sent one
            if ev["type"] in ("run_started", "done"):
                ev = {**ev, "run_id": run_id}
            if ev["type"] == "done":
                ev["report_url"] = f"{base_url}/report/{run_id}"
            ev = rewrite_images(ev, run["images"])
            run["events"].append(ev)
            run["event_times"].append(time.time())
            if ev["type"] in ("done", "error"):
                run["finished"] = ev["type"] == "done"
                run["finished_at"] = time.time()
            try:
                save_run(run)
            except Exception:
                pass
        q.put(ev)

    def work() -> None:
        push({"type": "run_started", "run_id": run_id, "address": snapshot.get("address_text") or ""})
        try:
            result = runner({**snapshot, "run_id": run_id}, push)
            if not run["finished"]:
                if isinstance(result, dict) and result.get("type") == "done":
                    push(result)
                else:
                    push(_synth_done(run["events"]))
        except Exception as e:  # noqa: BLE001
            push({"type": "error", "message": f"The check stopped: {type(e).__name__}: {str(e)[:200]}"})
        finally:
            with lock:
                if run["finished_at"] is None:
                    run["finished_at"] = time.time()
                try:
                    save_run(run)
                except Exception:
                    pass
            q.put(None)

    threading.Thread(target=work, daemon=True, name=f"run-{run_id}").start()
    return q


def _synth_done(events: list[dict]) -> dict:
    counts = {"confirmed": 0, "contradicted": 0, "close": 0, "cant_check": 0}
    for e in events:
        if e.get("type") == "verdict":
            k = (e.get("verdict") or {}).get("verdict")
            if k in counts:
                counts[k] += 1
    return {"type": "done", "counts": counts,
            "headline": f"{counts['contradicted']} of {sum(counts.values())} claims did not hold up."}


def replay(run: dict, base_url: str = "") -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _IMAGES_LOCK:
        _IMAGES.update(run.get("images") or {})
    gap = min(REPLAY_GAP, REPLAY_TOTAL_S / max(1, len(run["events"])))

    def work() -> None:
        for i, ev in enumerate(run["events"]):
            if i:
                time.sleep(gap)
            if ev.get("type") == "done" and base_url:
                ev = {**ev, "report_url": f"{base_url}/report/{run['run_id']}"}  # this server, not the original
            q.put(ev)
        q.put(None)

    threading.Thread(target=work, daemon=True).start()
    return q


async def sse(q: queue.Queue):
    while True:
        try:
            ev = await asyncio.to_thread(q.get, True, PING_S)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        if ev is None:
            return
        yield f"data: {json.dumps(ev)}\n\n"


# ---------- routes ----------

@app.get("/health")
def health():
    return {"ok": True, "harness": pick_runner()[0]}


@app.post("/check")
async def check(snapshot: dict, request: Request):
    base = str(request.base_url).rstrip("/")
    old = find_finished_run(snapshot.get("address_text")) if os.environ.get("RC_REPLAY") == "1" else None
    q = replay(old, base) if old else start_run(snapshot, base)
    return StreamingResponse(sse(q), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/img/{token}")
def img(token: str):
    if not TOKEN_RE.match(token):
        return Response(status_code=404)
    path = _lookup_image(token)
    if not path or not Path(path).is_file():
        return Response(status_code=404)
    return FileResponse(path)


@app.get("/runs/{run_id}.json")
def run_json(run_id: str):
    run = load_run(run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    run.pop("images", None)  # token -> local path stays private
    return run


@app.get("/report/{run_id}", response_class=HTMLResponse)
def report(run_id: str):
    run = load_run(run_id)
    if run is None:
        return HTMLResponse("<!doctype html><title>Not found</title><p>No such check.</p>", status_code=404)
    return HTMLResponse(render_report(run))


@app.get("/tour/{run_id}", response_class=HTMLResponse)
def tour_page(run_id: str, claim: str | None = None):
    """The real 3D walkthrough for one claim, with one client-side label (server/tour.py)."""
    headers = {"Cache-Control": "no-store"}
    run = load_run(run_id)
    if run is None:
        return HTMLResponse(tour.message_page(None, "No such check."), status_code=404, headers=headers)
    if claim is not None and not tour.CLAIM_RE.match(claim):
        claim = ""
    target = tour.tour_target(run, claim)
    if target is None:
        return HTMLResponse(tour.message_page(run_id, "No 3D scan for this claim.",
                                              "Only claims checked against a 3D scan can be walked through."),
                            status_code=404, headers=headers)
    key = tour.sdk_key()
    spot = tour.spot_for(target) if key else None  # cached, or picked once in the background
    if spot is not None and spot.get("status") == "pending":
        return HTMLResponse(tour.finding_page(run_id), headers=headers)
    return HTMLResponse(tour.render_tour(run, target, key, cache_dir(), spot), headers=headers)
