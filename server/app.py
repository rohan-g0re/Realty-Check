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
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)

from rc_common import cache_dir
from server.address_pick import pick_address
from server.report import render_report
from server.report_a import render_report as render_report_a
from server import tour

EVENT_TYPES = {"run_started", "step", "claims", "match", "evidence", "verdict", "fill_in", "done", "error"}
PING_S = 10.0
REPLAY_GAP = 0.4
REPLAY_TOTAL_S = 6.0  # a replay never takes longer than this
RUN_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
TOKEN_RE = re.compile(r"^[a-f0-9]{16}$")
SNAPSHOT_KEYS = ("url", "title", "address_text", "address_locked", "address_candidates", "address_pick")
FIELD_SOURCES = ("record", "scan", "aerial", "county")

app = FastAPI(title="Realty Check")
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
        v["citations"] = [c for c in v.get("citations") or [] if isinstance(c, dict)]
        for c in v["citations"]:
            old = c.get("image")
            c["image"] = _register(old) if old else None
            if old and c["image"] != old:
                images[c["image"][5:]] = old
    if ev.get("type") == "done" and isinstance(ev.get("harness"), dict):
        for f in ev["harness"].get("top_facts") or []:
            if isinstance(f, dict) and f.get("image"):
                old = f["image"]
                f["image"] = _register(old)
                if f["image"] != old:
                    images[f["image"][5:]] = old
    if ev.get("type") == "fill_in":
        ev["facts"] = [f for f in ev.get("facts") or [] if isinstance(f, dict)]
        for f in ev["facts"]:
            old = f.get("image")
            f["image"] = _register(old) if old else None
            if old and f["image"] != old:
                images[f["image"][5:]] = old
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


# ---------- address pick, cache switch, drawer field ----------

def no_cache() -> bool:
    """RC_NO_CACHE=1: a fresh run every time (the cache helpers honour it; here it turns replays off)."""
    try:
        from tools.cachectl import enabled
        return not enabled()
    except Exception:  # noqa: BLE001  the cache switch module is missing: read the variable the same way
        return os.environ.get("RC_NO_CACHE", "").strip().lower() in ("1", "true", "yes", "on")


def public_pick(pick) -> dict | None:
    """The address pick as the browser may see it: no Match, no property ids."""
    if not isinstance(pick, dict) or not pick.get("chosen"):
        return None
    alts = [{"text": str(a.get("text") or ""), "name": a.get("name") or None, "why": str(a.get("why") or "")}
            for a in pick.get("alternatives") or [] if isinstance(a, dict) and a.get("text")]
    conf = pick.get("confidence")
    return {"chosen": str(pick["chosen"]), "why": str(pick.get("why") or ""),
            "confidence": round(float(conf), 2) if isinstance(conf, (int, float)) else None, "alternatives": alts}


def apply_pick(snapshot: dict) -> dict:
    """Cross-check the page's address candidates against the kit (server/address_pick.py) and check the chosen
    one. Returns a new snapshot; `address_pick` on it is the full pick. Any failure keeps the snapshot as sent."""
    snapshot = dict(snapshot or {})
    try:
        pick = pick_address(snapshot)
    except Exception:  # noqa: BLE001  a broken kit lookup must never stop a check
        return snapshot
    if isinstance(pick, dict) and pick.get("chosen"):
        snapshot["address_text"] = pick["chosen"]
        snapshot["address_pick"] = pick
    return snapshot


def drawer_field(run: dict) -> dict | None:
    """The claim field's cell states (server/claim_field.py, the report's own rules) for the drawer's mini grid:
    {"claims": [{"id", "cells": {record|scan|aerial|county: match|contra|close|data|none}, "shade", "image",
    "credit"}]}. `shade`: {source key: high|medium|low} for the blue (contra / close) cells that carry one (how strong
    the drawer draws the blue); missing: contra full, close faint.
    `image`: the best proof picture (an /img/<token> URL) for a claim that does not match, else null.
    `credit`: for a claim that does not match, the source keys the report credits with the found value
    (server.report_a._credit: the deciding one first, never a source that says something else), else null."""
    try:
        from server.claim_field import IMG_RE, claim_field
        field = claim_field(run)
    except Exception:  # noqa: BLE001
        return None
    verdicts = {}
    for e in run.get("events") or []:
        if isinstance(e, dict) and e.get("type") == "verdict" and isinstance(e.get("verdict"), dict):
            verdicts[e["verdict"].get("claim_id")] = e["verdict"]
    out = []
    for c in field.get("claims") or []:
        cells = c.get("cells") or {}
        img = None
        if c.get("highlight"):
            v = verdicts.get(c.get("id")) or {}
            pool = [v.get("image"), (cells.get(v.get("source")) or {}).get("image")]
            pool += [(cells.get(k) or {}).get("image") for k in FIELD_SOURCES
                     if (cells.get(k) or {}).get("state") in ("contra", "close")]
            img = next((p for p in pool if isinstance(p, str) and IMG_RE.match(p)), None)
        shade = {k: (cells.get(k) or {}).get("shade") for k in FIELD_SOURCES}
        out.append({"id": c.get("id"), "cells": {k: (cells.get(k) or {}).get("state") or "none" for k in FIELD_SOURCES},
                    "shade": {k: x for k, x in shade.items() if x in ("high", "medium", "low")},
                    "image": img, "credit": drawer_credit(verdicts.get(c.get("id")))})
    return {"claims": out}


def drawer_credit(v) -> list | None:
    """The sources the report credits with a mismatch's found value (server.report_a._credit, the same rule), so the
    drawer never names a source that says something else. None for a claim that matches, or when it cannot be built."""
    if not isinstance(v, dict) or v.get("verdict") not in ("contradicted", "close"):
        return None
    try:
        from server.report_a import _credit
        return list(_credit(v)[0])
    except Exception:  # noqa: BLE001
        return None


def drawer_top(run: dict) -> dict | None:
    """The report's headline and lead finding (server.report_a.top_finding: its counts, ranking and words), so the
    drawer's headline never names a different mismatch than the report. None when it cannot be built."""
    try:
        from server.report_a import top_finding
        return top_finding(run)
    except Exception:  # noqa: BLE001
        return None


MANAGED_RE = re.compile(r"(?i)\s*[;,]\s*(?:managed|operated|owned)\s+by\b.*$")
DRAWER_FACTS = 5


def _drawer_rows(R, run, off: list) -> list:
    """Every mismatch (contradicted or close) in the report's order (server.report_a: its words, credit, score, the
    blue's strength `shade` high | medium | low, the tag '82 / 100 · high' and `also`: the sources that spoke but
    do not back the found value, the report's "also checked"; `previews`: {source key: the report's preview card,
    R.cite_preview: source, role, tag, value, read, where (ids stripped), date, href}, {} for old runs), as text only."""
    rows = []
    rid = str(run.raw.get("run_id") or "")
    for v in off:
        c = run.claim(v)
        kind = R._kind(v, c)
        hard = v.get("verdict") == "contradicted"
        soft = not hard and kind in R.SOFT_KINDS
        said, is_q = R._strike_text(run, v, c)
        what = R.KIND_LABEL.get(kind) or R._cap(str(kind or "").replace("_", " ")) or "Claim"
        why = R._kind_text(R._tidy(R._pick_why(run, v) or R._first_sentence(v.get("reason") or "")))
        credit = list(R._credit(v)[0])
        also = R._also_keys(run, v, credit)
        score, band, _cwhy = R._conf(v)
        cid = str(v.get("claim_id") or "")
        previews = {}  # the report's source preview card, per source key (credit first = decided, then also)
        for i, k in enumerate(credit + also):
            p = R.cite_preview(v, k, lead=i == 0)
            if p:
                previews[k] = {f: p[f] for f in ("source", "role", "tag", "value", "read", "where", "date")}
                previews[k]["href"] = R._art_href(run, cid, k) or None
        walk = None
        try:
            w = R._walk_hrefs(run, v)
            walk = w[0] if w else None
        except Exception:  # noqa: BLE001
            walk = None
        rows.append({"id": cid, "verdict": v.get("verdict"), "kind": kind,
                     "label": " · ".join(x for x in (what, c.get("label")) if x),
                     "said": said if is_q else R._cap(said), "quoted": bool(is_q), "strike": hard,
                     "found": "Not seen in the photos" if soft else R._truth_text(v).rstrip("."),
                     "why": why, "credit": credit, "also": also, "previews": previews, "score": score,
                     "band": band or None,
                     "shade": R._shade(v), "tag": R._tag(v),
                     "proof": f"/report/{rid}#claim-{cid}" if rid and cid else None, "walk": walk})
    return rows


def _drawer_facts(R, run, off: list) -> list:
    """'Not in the listing': the report's top five (done.harness.top_facts, else its rule), text only; never a
    manager, owner, parcel, class or lot size; any '; managed by ...' clause cut."""
    out = []
    for f in R._top_facts(run, R._fill_in(run), off):
        value = MANAGED_RE.sub("", f.get("value") or "").strip()
        if not value or R.TOPF_BANNED.search(f.get("label") or ""):
            continue
        out.append({"label": f["label"], "value": value, "why": f.get("why") or "",
                    "sources": [R.SRC_NAME[k] for k in f.get("keys") or [] if k in R.SRC_NAME]})
        if len(out) >= DRAWER_FACTS:
            break
    return out


def _drawer_checked(R, run, vs: list, cant: bool) -> list:
    out = []
    for v in vs:
        c = dict(run.claim(v))
        c["_claimed"] = R._claimed_words(v, c)
        said, is_q = R._strike_text(run, v, c)
        kind = R._kind(v, c)
        row = {"id": str(v.get("claim_id") or ""), "label": R.KIND_LABEL.get(kind) or "",
               "text": f"“{said}”" if is_q else R._cap(said)}
        if cant:
            try:
                row["why"] = R._kind_text(R._cant_reason(run, v, c))
            except Exception:  # noqa: BLE001
                row["why"] = ""
        out.append(row)
    return out


def drawer_report(run: dict) -> dict | None:
    """Everything the report says, as text for the drawer (docs/CONTRACT.md 7, done.report), built read-only with
    server/report_a.py's own helpers so the two never disagree:
    {"rows": [{id, verdict, kind, label, said, quoted, strike, found, why, credit, also, score, band, shade, tag, proof,
               walk}],
     "facts": [{label, value, why, sources}] (at most five), "questions": [str], "ok": [{id, label, text}],
     "cant": [{id, label, text, why}]}. No pictures, drawings or viewers. None when it cannot be built."""
    try:
        import server.report_a as R
        r = R.Run(R.drop_dead_images(run) if hasattr(R, "drop_dead_images") else run)
        off = R._ranked_off(r, r.verdicts)
        ok = [v for v in r.verdicts if v.get("verdict") == "confirmed"]
        cant = [v for v in r.verdicts if v.get("verdict") not in ("contradicted", "close", "confirmed")]
        out = {"rows": _drawer_rows(R, r, off)}
    except Exception:  # noqa: BLE001
        return None
    try:
        out["facts"] = _drawer_facts(R, r, off)
    except Exception:  # noqa: BLE001
        out["facts"] = []
    qs: list = []
    try:  # one per mismatch (every one), then the couldn't-checks up to the report's cap
        qs = [q for q in R._question_list(r, off, cant) if not R.UNKIND.search(q)]
    except Exception:  # noqa: BLE001
        pass
    out["questions"] = qs
    for key, vs, is_cant in (("ok", ok, False), ("cant", cant, True)):
        try:
            out[key] = _drawer_checked(R, r, vs, is_cant)
        except Exception:  # noqa: BLE001
            out[key] = []
    return out


def with_drawer_keys(ev: dict, run: dict) -> dict:
    """A `done` event with the additive drawer keys (`field`, `top`, `report`) the report computes, where missing."""
    add = {}
    if "report" not in ev:
        rep = drawer_report(run)
        if rep is not None:
            add["report"] = rep
    if "field" not in ev:
        field = drawer_field(run)
        if field is not None:
            add["field"] = field
    if "top" not in ev:
        top = drawer_top(run)
        if top is not None:
            add["top"] = top
    return {**ev, **add} if add else ev


# ---------- running ----------

def start_run(snapshot: dict, base_url: str) -> queue.Queue:
    """Run in a worker thread; events go to the returned queue (None = end). Run is saved either way.
    `run_started` carries the browser-safe address pick and whether the cache is on (CONTRACT 2.6)."""
    q: queue.Queue = queue.Queue()
    run_id = time.strftime("%Y%m%d-%H%M%S-").lower() + uuid.uuid4().hex[:6]
    name, runner = pick_runner()
    run = {"run_id": run_id, "harness": name,
           "snapshot": {**{k: snapshot.get(k) for k in SNAPSHOT_KEYS},
                        "address_pick": public_pick(snapshot.get("address_pick"))},
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
                ev.pop("field", None)  # always the server's own, from the events this run sent
                ev.pop("top", None)
                ev = with_drawer_keys(ev, {"run_id": run_id, "events": run["events"] + [ev]})
            ev = rewrite_images(ev, run["images"])
            if ev["type"] == "fill_in":
                run["fill_in"] = ev["facts"]  # also kept top level, for the report and later readers
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
        if ev["type"] == "done":  # pick the 3D spots now, so the report's viewers open at them straight away
            tour.prewarm(run)

    def work() -> None:
        push({"type": "run_started", "run_id": run_id, "address": snapshot.get("address_text") or "",
              "address_pick": public_pick(snapshot.get("address_pick")), "cache": "off" if no_cache() else "on"})
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
            if ev.get("type") == "done":  # always today's field / top / report, never the copy saved with the run
                ev = {k: x for k, x in ev.items() if k not in ("field", "top", "report")}
                ev = with_drawer_keys(ev, {"run_id": run["run_id"], "events": run["events"][:i + 1]})
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


FAVICON = Path(__file__).resolve().parent.parent / "extension" / "icons" / "icon32.png"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """The browser asks every page here for /favicon.ico; answer with the extension's icon, not a 404."""
    if FAVICON.is_file():
        return FileResponse(FAVICON, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
    return Response(status_code=204)


@app.post("/check")
async def check(snapshot: dict, request: Request):
    base = str(request.base_url).rstrip("/")
    snapshot = await asyncio.to_thread(apply_pick, snapshot)  # before the replay lookup: replay the chosen address
    replay_on = os.environ.get("RC_REPLAY") == "1" and not no_cache()
    old = find_finished_run(snapshot.get("address_text")) if replay_on else None
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


@app.get("/field/{run_id}")
def field_json(run_id: str):
    """The drawer's claim field for a saved run: the report's cell states (server/claim_field.py)."""
    run = load_run(run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    field = drawer_field(run)
    return field if field is not None else JSONResponse({"error": "unavailable"}, status_code=500)


@app.get("/report/{run_id}", response_class=HTMLResponse)
def report(run_id: str, v: str | None = None):
    """The full report (server/report_a.py); ?v=old serves the previous tabbed page (server/report.py)."""
    run = load_run(run_id)
    if run is None:
        return HTMLResponse("<!doctype html><title>Not found</title><p>No such check.</p>", status_code=404)
    tour.prewarm(run)  # starts any 3D spot pick not cached yet; never waits
    return HTMLResponse(render_report(run) if v == "old" else render_report_a(run))


@app.get("/tour/{run_id}", response_class=HTMLResponse)
def tour_page(request: Request, run_id: str, claim: str | None = None, embed: str | None = None):
    """The real 3D walkthrough for one claim, with one client-side label (server/tour.py).
    embed=1: the report's inline viewer (same-origin iframe); no back link, fills the frame. No X-Frame-Options or
    frame-ancestors header is set here, so the report can frame it."""
    headers = {"Cache-Control": "no-store"}
    em = embed == "1"
    # The Showcase SDK key only accepts pages served from "localhost" (127.0.0.1 gets HTTP 400 on the SDK), so the
    # viewer always runs on localhost; the report at 127.0.0.1 frames it cross-origin and hears it by postMessage.
    if request.url.hostname == "127.0.0.1":
        return RedirectResponse(str(request.url.replace(hostname="localhost")), status_code=307, headers=headers)
    run = load_run(run_id)
    if run is None:
        return HTMLResponse(tour.message_page(None, "No such check.", embed=em), status_code=404, headers=headers)
    if claim is not None and not tour.CLAIM_RE.match(claim):
        claim = ""
    target = tour.tour_target(run, claim)
    if target is None:
        return HTMLResponse(tour.message_page(run_id, "No 3D scan for this claim.",
                                              "Only claims checked against a 3D scan can be walked through.",
                                              embed=em),
                            status_code=404, headers=headers)
    key = tour.sdk_key()
    spot = tour.spot_for(target) if key else None  # cached, else start view now (picked in the background)
    return HTMLResponse(tour.render_tour(run, target, key, cache_dir(), spot, embed=em), headers=headers)


from server import artifacts as _artifacts  # noqa: E402

_artifacts.register(app)
