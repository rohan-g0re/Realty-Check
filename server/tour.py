""""See it yourself": the real Matterport 3D walkthrough for one claim of a saved run.

GET /tour/<run_id>?claim=<claim id>. The page embeds the scan the claim's scan evidence points to, stands at the
panorama spot that shows the mismatch (picked by tools/scan/spots.py from the scan's room tags, else by a picture
check; cached; "Finding the spot…" page while the first pick runs), turns toward it, and pins one client-side label
(Tag.add, never saved to the model) there with what the listing says and what the scan shows. No spot found: the
scan's start view, and the card says so. Confirmed claims get the same view, worded as "checks out".

?embed=1 is the report's inline viewer (an iframe on /report/<run_id>, mounted without a click): no "Back to the
report" link, the page fills the frame, the "Finding the spot…" reload keeps embed=1 and the claim, and the page tells
the report how far it got with window.parent.postMessage({rc3d: 1, state, note}) (same origin only). States:
"loading", "playing" (showcase running), "ready" (standing at the picked spot, or at the start view when there is none),
"finding" (spot pick still running), "off" (no viewer here), "error". The report keeps its still picture over the frame
until "ready", so the reader never sees the scan's start position fly over to the spot.

Speed: the showcase URL carries ss=<panorama label> when the cached scan data knows the picked spot's label, so the
scan opens at the spot instead of its start position (ss counts from 1, the label from 0); the SDK then
only turns the camera, or moves instantly while the report's still picture still covers the frame.
prewarm(run) starts the spot picks of every mismatch in the background (on run end and on report load), so the
viewer seldom has to show "Finding the spot…".

The Showcase SDK key is read at request time from the environment or the private .env and only ever written into
the served page. No key: the page says the 3D view is not available.
"""
from __future__ import annotations

import json
import os
import re
import threading
from html import escape
from pathlib import Path

from tools.scan import spots

MODEL_RE = re.compile(r"^[A-Za-z0-9]{6,24}$")
CLAIM_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PANO_RE = re.compile(r"^[A-Za-z0-9]{6,40}$")
SDK_BOOT = "https://api.matterport.com/sdk/bootstrap/3.1.0-0-g1cd7e0997a/sdk.es6.js"
# hosts the showcase and the SDK load from; the report and the tour page open connections to them early
MP_HOSTS = ("https://my.matterport.com", "https://api.matterport.com", "https://static.matterport.com",
            "https://cdn-2.matterport.com")
PRECONNECT = "".join(f"<link rel=preconnect href='{h}' crossorigin><link rel=dns-prefetch href='{h}'>" for h in MP_HOSTS)
KIND_WORDS = {"bedrooms": "Bedrooms", "bathrooms": "Bathrooms", "area_sqft": "Size", "stories": "Floors",
              "amenity": "Amenity", "parking": "Parking", "laundry": "Laundry", "view": "View"}


# ---------- key ----------

def sdk_key() -> str | None:
    """MATTERPORT_SDK_KEY from the environment, else from $RC_PRIVATE_DIR/.env. None when missing."""
    k = (os.environ.get("MATTERPORT_SDK_KEY") or "").strip()
    if k:
        return k
    p = os.environ.get("RC_PRIVATE_DIR")
    if not p:
        return None
    try:
        lines = (Path(p) / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        m = re.match(r"\s*MATTERPORT_SDK_KEY\s*=\s*(.*)$", line)
        if m:
            v = m.group(1).strip().strip('"').strip("'")
            return v or None
    return None


# ---------- which scan, which claim ----------

def _norm(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _same_plan(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def _parts(run: dict):
    ev = run.get("events") or []
    claims = {}
    for e in ev:
        if e.get("type") == "claims":
            for c in e.get("claims") or []:
                if isinstance(c, dict) and c.get("id") not in claims:
                    claims[c.get("id")] = c
    verdicts = [e["verdict"] for e in ev if e.get("type") == "verdict" and isinstance(e.get("verdict"), dict)]
    evidence = [e["evidence"] for e in ev if e.get("type") == "evidence" and isinstance(e.get("evidence"), dict)]
    return claims, verdicts, evidence


def _scan_ev(evidence: list) -> list:
    return [e for e in evidence if e.get("source") == "scan" and e.get("status", "ok") == "ok"
            and MODEL_RE.match(str((e.get("observed") or {}).get("model_id") or ""))]


def _model_for(claim: dict, verdict: dict, evidence: list):
    """(model_id, scan title or '') of the scan this claim's verdict rests on, else (None, '')."""
    scans = _scan_ev(evidence)
    title_of = {}
    for e in scans:
        o = e["observed"]
        if o.get("model_name") and not o.get("feature_check"):
            title_of.setdefault(o["model_id"], str(o["model_name"]))
        elif o.get("scan_title"):
            title_of.setdefault(o["model_id"], str(o["scan_title"]))
    pick = None
    label = claim.get("label")
    if label:  # a. the listing plan's own scan
        pick = next((e for e in scans if not e["observed"].get("feature_check")
                     and _same_plan(e["observed"].get("plan"), label)), None)
    if pick is None and verdict.get("image"):  # b. the scan whose picture is this verdict's proof
        pick = next((e for e in scans if any(isinstance(im, dict) and im.get("path") == verdict["image"]
                                             for im in e.get("images") or [])), None)
    if pick is None:  # c. a picture check that answered this claim
        pick = next((e for e in scans if claim.get("id") in (e["observed"].get("claim_ids") or [])), None)
    if pick is None:
        ids = {e["observed"]["model_id"] for e in scans if not e["observed"].get("feature_check")}
        if verdict.get("source") == "scan" and len(ids) == 1 and not label:  # d. the run's only unit scan
            mid = next(iter(ids))
            return mid, title_of.get(mid, "")
        return None, ""
    mid = pick["observed"]["model_id"]
    return mid, title_of.get(mid, "")


def tour_target(run: dict, claim_id: str | None = None) -> dict | None:
    """Claim, verdict and scan for the tour; claim_id None = first mismatch that has a scan. None if no scan."""
    claims, verdicts, evidence = _parts(run)
    order = verdicts
    if claim_id is None:
        order = [v for v in verdicts if v.get("verdict") in ("contradicted", "close")]
    for v in order:
        if claim_id is not None and v.get("claim_id") != claim_id:
            continue
        c = claims.get(v.get("claim_id")) or {}
        mid, title = _model_for(c, v, evidence)
        if mid:
            return {"claim": c, "verdict": v, "model_id": mid, "scan_title": title}
        if claim_id is not None:
            return None
    return None


# ---------- which spot ----------
# The pick runs once per (model, claim kind, claimed value) in a background thread and is cached on disk by
# tools/scan/spots.py; the page shows "Finding the spot…" (and reloads itself) until it is ready.

PICKER = spots.pick_spot  # tests replace this
WAIT_S = 1.5  # a pick that finishes this fast (cached scan geometry) is served without the waiting page
_JOBS: dict[str, threading.Thread] = {}
_RESULTS: dict[str, dict] = {}
_LOCK = threading.Lock()


def reset_spots() -> None:
    with _LOCK:
        _JOBS.clear()
        _RESULTS.clear()


def _pick_job(key: str, model_id: str, claim: dict, verdict: dict) -> None:
    try:
        res = PICKER(model_id, claim, verdict)
    except Exception as e:  # noqa: BLE001 - never surfaces details that could hold a URL
        res = {"status": "none", "error": True, "why": f"The spot could not be looked up just now ({type(e).__name__})."}
    with _LOCK:
        _RESULTS[key] = res


def spot_for(target: dict, wait_s: float | None = None) -> dict:
    """The spot pick for a tour target: from cache, or started now. {"status": "pending"} while it runs."""
    model_id, c, v = target["model_id"], target["claim"], target["verdict"]
    plan = spots.plan_for(c, v)
    if not plan.get("target"):
        return {"status": "skip", "why": plan.get("why") or spots.WHOLE_UNIT}
    hit = spots.cached_pick(model_id, c)
    if hit is not None:
        return hit
    key = f"{model_id}/{spots.cache_key(c)}"
    with _LOCK:
        if key in _RESULTS:
            return _RESULTS[key]
        th = _JOBS.get(key)
        if th is None:
            th = threading.Thread(target=_pick_job, args=(key, model_id, c, v), daemon=True, name=f"spot-{key}")
            _JOBS[key] = th
            th.start()
    th.join(WAIT_S if wait_s is None else wait_s)
    with _LOCK:
        return _RESULTS.get(key) or {"status": "pending"}


PREWARM = True  # tests switch it off (tests/conftest.py) and turn it on with a picker stub


def prewarm(run: dict) -> int:
    """Start (never wait for) the spot picks of every mismatch that has a scan, so the report's viewers open at the
    spot without the "Finding the spot…" wait. One background thread runs them one after another, in report order
    (gentle on the Matterport rate limit and the picture model); cached picks cost nothing; a viewer that asks first
    starts its own pick. Skipped for the fake runner (invented scan ids) and when there is no viewer key. Returns how
    many picks it will look at."""
    if not PREWARM or run.get("harness") == "fake" or not sdk_key():
        return 0
    targets = []
    try:
        _, verdicts, _ = _parts(run)
        for v in verdicts:
            if v.get("verdict") not in ("contradicted", "close") or not CLAIM_RE.match(str(v.get("claim_id") or "")):
                continue
            t = tour_target(run, v["claim_id"])
            if t:
                targets.append(t)
    except Exception:  # noqa: BLE001 - a warm-up never breaks the report
        return 0
    if not targets:
        return 0

    def work():
        for t in targets:
            try:
                spot_for(t, wait_s=180)
            except Exception:  # noqa: BLE001
                pass
    th = threading.Thread(target=work, daemon=True, name="spot-prewarm")
    _WARMERS.append(th)
    del _WARMERS[:-8]
    th.start()
    return len(targets)


_WARMERS: list[threading.Thread] = []


def _spot_note(spot: dict) -> str:
    """The card's plain line: what is visible at this spot, or why the tour stays at the start view."""
    st = spot.get("status")
    if st == "found" and spot.get("how") == "picture" and spot.get("seen"):
        return f"At this spot: {spot['seen']}."
    if st == "found":
        return str(spot.get("why") or "")
    why = str(spot.get("why") or "").strip()
    return (why + " " if why else "") + "Showing the scan from its start position."


# ---------- page ----------

CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#fff;color:#17181A;
font:15px/1.45 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
#mp{position:fixed;inset:0;width:100%;height:100%;border:0;background:#17181A}
.back{position:fixed;top:16px;left:16px;z-index:5;background:#fff;color:#17181A;text-decoration:none;
font-weight:600;font-size:14px;padding:9px 14px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.back:hover{background:#f3f3f1}
.pill{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:5;background:#17181A;color:#fff;
font-size:13px;font-weight:600;padding:8px 14px;border-radius:999px;white-space:nowrap;max-width:calc(100% - 340px);
overflow:hidden;text-overflow:ellipsis}
.pill span{font-weight:400;opacity:.75}
.card{position:fixed;left:16px;bottom:16px;z-index:5;background:#fff;border-radius:12px;padding:14px 16px;
max-width:380px;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.card p{margin:0}.card .was{font-size:13px;color:#5f6368}.card s{color:#B3261E;text-decoration-thickness:2px}
.card .is{font-size:17px;font-weight:600;margin-top:2px}.card .note{font-size:12px;color:#5f6368;margin-top:8px}
.msg{max-width:520px;margin:18vh auto 0;padding:0 24px}.msg h1{font-size:20px;font-weight:600;margin:0 0 6px}
.msg p{color:#5f6368;margin:0}
body.embed{overflow:hidden}.embed .pill{max-width:calc(100% - 32px)}
@media (max-width:640px){.pill{top:64px;max-width:calc(100% - 32px)}.card{right:16px;max-width:none}
.embed .pill{top:16px}}
"""

# Runs in the page. CFG comes from the server; nothing here is saved to the model (Tag.add is client-side only).
SCRIPT = r"""
const CFG = window.TOUR_CFG, T = window.TOUR = {connected: false, placed: null, room: 'start', how: ''};
// the report frames this page from the same server under either name (this page itself always runs on localhost)
const PARENTS = [location.origin, location.origin.replace('//localhost', '//127.0.0.1')].filter((o, i, a) => a.indexOf(o) === i);
const note = document.getElementById('note');
const say = (s) => { T.how = s; if (note) note.textContent = s; };
const f = document.getElementById('mp');
const tell = (state, extra) => { T.state = state; T['t_' + state] = Math.round(performance.now());
  if (CFG.embed && window.parent !== window) for (const o of PARENTS) { try { window.parent.postMessage(Object.assign({rc3d: 1, state, note: T.how}, extra || {}), o); } catch (e) {} } };
f.src = 'https://my.matterport.com/show/?m=' + encodeURIComponent(CFG.model) +
  '&play=1&qs=1&title=0&brand=0&help=0' + (CFG.ss ? '&ss=' + encodeURIComponent(CFG.ss) : '') +
  (CFG.embed ? '&hr=0&wh=0' : '') + '&applicationKey=' + encodeURIComponent(CFG.key);
tell('loading');
const wait = (ms) => new Promise(r => setTimeout(r, ms));
// a collection's first non-empty state (the first update can be empty while the model is still loading)
const coll = (c, ms) => new Promise(r => { let d = 0, last = [], sub = null;
  const fin = () => { if (!d) { d = 1; r(last); try { sub && sub.cancel(); } catch (e) {} } };
  sub = c.subscribe({onCollectionUpdated(x) { last = Object.values(x || {}); if (last.length) fin(); }});
  setTimeout(fin, ms || 15000); });
// the sweep the camera stands at now (id), or null
const here = (sdk) => new Promise(r => { let d = 0, sub = null; const fin = (x) => { if (!d) { d = 1; r(x); try { sub && sub.cancel(); } catch (e) {} } };
  try { sub = sdk.Sweep.current.subscribe(c => { if (c && c.id) fin(c); }); } catch (e) { fin(null); } setTimeout(() => fin(null), 3000); });
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
try {
  const {connect} = await import(CFG.boot + '?applicationKey=' + encodeURIComponent(CFG.key));
  const sdk = await connect(f);
  T.connected = true; window.sdk = sdk;
  await new Promise(r => { let d = 0; sdk.App.state.subscribe(s => { if (!d && s.phase === sdk.App.Phase.PLAYING) { d = 1; r(); } });
    setTimeout(() => { if (!d) { d = 1; r(); } }, 20000); });
  tell('playing');
  const S = CFG.spot || {};
  // fast path: ss= opened the showcase right at the picked spot, so only turn to the view that shows it
  const cur0 = S.sweep && CFG.ss ? await here(sdk) : null;
  if (cur0 && (cur0.id === S.sweep || cur0.sid === S.sweep)) {
    T.startedAtSpot = true; T.at = cur0.id;
    if (Number.isInteger(S.face) && cur0.rotation) {
      try { await sdk.Camera.setRotation({x: 0, y: (cur0.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face}, {speed: 2000}); T.faced = true; } catch (e) {}
    }
    T.room = 'moved'; T.t_moved = Math.round(performance.now()); say(CFG.note);
  } else {
  const sweeps = await coll(sdk.Sweep.data);
  T.sweeps = sweeps.length; T.t_sweeps = Math.round(performance.now());
  let rooms = [];
  if (!S.sweep && S.sweeps && S.sweeps.length) { try { rooms = await coll(sdk.Room.data, 4000); } catch (e) {} }
  let go = null, rot = null;
  if (S.sweep) {
    // a. the one spot the server picked (Model API panoLocation id = SDK sweep id), facing the view that shows it
    go = sweeps.find(s => s.id === S.sweep || s.sid === S.sweep) || null;
    if (go && Number.isInteger(S.face)) rot = {x: 0, y: (go.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face};
  } else if (S.sweeps && S.sweeps.length) {
    // b. a room the scan's own tags single out: stand at its edge spot and look across to the middle of the room
    const ids = new Set(S.sweeps);
    const inRoom = sweeps.filter(s => ids.has(s.id) || ids.has(s.sid));
    if (inRoom.length) {
      const c = inRoom.reduce((m, s) => ({x: m.x + s.position.x / inRoom.length, y: m.y + s.position.y / inRoom.length,
        z: m.z + s.position.z / inRoom.length}), {x: 0, y: 0, z: 0});
      go = inRoom.slice().sort((p, q) => dist(q.position, c) - dist(p.position, c))[0];
      const rid = go.roomInfo && go.roomInfo.id, rm = rooms.find(r => r.id === rid);
      let look = c;
      if (rm && rm.bounds && rm.bounds.min && rm.bounds.max) {
        const b = rm.bounds; look = {x: (b.min.x + b.max.x) / 2, y: go.position.y, z: (b.min.z + b.max.z) / 2};
      }
      const dx = look.x - go.position.x, dz = look.z - go.position.z;
      if (Math.hypot(dx, dz) > 0.4) rot = {x: 0, y: Math.atan2(-dx, -dz) * 180 / Math.PI};
    }
  }
  if (go) {
    // embedded: the report's still picture covers the frame until "ready", so the move is instant
    const opts = CFG.embed ? {transition: sdk.Sweep.Transition.INSTANT} : {transition: sdk.Sweep.Transition.FLY, transitionTime: 1200};
    if (rot) { opts.rotation = rot; T.faced = true; }
    // with ss= the showcase already opened at the spot: then only turn the camera (a move there costs seconds)
    const start = await here(sdk); T.startedAtSpot = !!(start && (start.id === go.id || start.sid === go.sid));
    if (T.startedAtSpot) { if (rot) { try { await sdk.Camera.setRotation(rot, {speed: 2000}); } catch (e) { T.moveErr = 'turn'; } } }
    else {
      try { await sdk.Sweep.moveTo(go.id, opts); }
      catch (e) { T.moveErr = String(e && e.message || e); if (rot) { try { await sdk.Camera.setRotation(rot, {speed: 1000}); } catch (e2) {} } }
    }
    T.t_moved = Math.round(performance.now());
    await wait(CFG.embed ? 350 : 1800);
    // where the camera really stands now (the SDK's current sweep), not where it was asked to go
    const cur = await here(sdk); T.at = cur && cur.id; T.t_here = Math.round(performance.now());
    if (T.at === go.id || (T.at && T.at === go.sid)) { T.room = 'moved'; say(CFG.note); }
    else say('The picked spot could not be reached in this viewer, so this is the scan\'s start position.');
  } else if (S.sweep || (S.sweeps && S.sweeps.length)) {
    // no sweep list (slow load): the showcase may already stand at the spot through ss=
    const cur = await here(sdk); T.at = cur && cur.id;
    const want = new Set([S.sweep].concat(S.sweeps || []).filter(Boolean));
    if (cur && (want.has(cur.id) || want.has(cur.sid))) {
      T.room = 'moved'; T.viaSs = true; say(CFG.note);
      if (S.sweep && Number.isInteger(S.face) && cur.rotation) {
        try { await sdk.Camera.setRotation({x: 0, y: (cur.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face}, {speed: 1000}); T.faced = true; } catch (e) {}
      }
    } else say('The picked spot is not in this viewer, so this is the scan\'s start position.');
  } else {
    say(CFG.note);
  }
  }
  tell('ready', {room: T.room});
  // label: on what the view looks at, pulled a little toward the camera so it does not sit inside a wall
  const pose = await sdk.Camera.getPose();
  let at = null;
  try { const w = await sdk.Renderer.getWorldPositionData({x: f.clientWidth / 2, y: f.clientHeight * 0.55}); at = w && w.position; } catch (e) {}
  const p = pose.position;
  if (!at || dist(at, p) > 8 || dist(at, p) < 0.3) {
    const yaw = (pose.rotation.y || 0) * Math.PI / 180;
    at = {x: p.x - Math.sin(yaw) * 2, y: p.y, z: p.z - Math.cos(yaw) * 2};
  }
  T.hitDist = Math.round(dist(at, p) * 10) / 10;
  const k = Math.min(0.6, CFG.pull / Math.max(dist(at, p), 0.01));
  const anchor = {x: at.x + (p.x - at.x) * k, y: p.y - 0.35, z: at.z + (p.z - at.z) * k};
  const ids = await sdk.Tag.add({label: CFG.tag.label, description: CFG.tag.description, anchorPosition: anchor,
    stemVector: {x: 0, y: 0.25, z: 0}, color: {r: 0.70, g: 0.15, b: 0.12}});
  T.placed = ids && ids[0];
  try { await sdk.Tag.open(T.placed); } catch (e) { T.openErr = String(e && e.message || e); }
} catch (e) {
  T.err = String(e && e.message || e);
  say(T.connected ? 'The 3D view could not turn to the spot here.' : 'The 3D view could not go to the spot here, so this is the scan\'s start position.');
  if (T.state !== 'ready') tell(T.connected ? 'ready' : 'error', {room: T.room});
}
T.done = true;
"""


def _strike(s: str) -> str:
    """Struck-through text for the in-scene label (the SDK label is plain text)."""
    return "".join(ch + "̶" if not ch.isspace() else ch for ch in s)


def _claimed_text(claim: dict, verdict: dict) -> str:
    return str(verdict.get("claimed") or claim.get("quote") or "").strip()


def _first_sentence_after_listing(reason: str) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(reason or "").strip()) if p.strip()]
    if len(parts) > 1 and parts[0].lower().startswith("listing says"):
        parts = parts[1:]
    return parts[0] if parts else ""


def _embed_note(state: str, text: str) -> str:
    """In the report's frame, tell the report what this message page says (same origin only; words only)."""
    msg = json.dumps({"rc3d": 1, "state": state, "note": text}).replace("</", "<\\/")
    return ("<script>(function(){if(window.parent===window)return;var o=location.origin;"
            "[o,o.replace('//localhost','//127.0.0.1')].forEach(function(t,i,a){if(a.indexOf(t)!==i)return;"
            f"try{{window.parent.postMessage({msg},t)}}catch(e){{}}}});}})();</script>")


def _page(title: str, body: str, script: str = "", refresh_s: int | None = None, embed: bool = False,
          refresh_url: str | None = None) -> str:
    meta = ""
    if refresh_s:
        to = f";url={refresh_url}" if refresh_url else ""
        meta = f"<meta http-equiv=refresh content='{escape(f'{int(refresh_s)}{to}', True)}'>"
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=referrer content=no-referrer>{meta}{PRECONNECT}"
            f"<title>{escape(title)}</title><style>{CSS}</style></head>"
            f"<body{' class=embed' if embed else ''}>{body}{script}</body></html>")


def _back(run_id: str | None, embed: bool) -> str:
    """"Back to the report": standalone pages only (inside the report's own frame it makes no sense)."""
    if not run_id or embed:
        return ""
    return f"<a class=back href='/report/{escape(run_id, True)}'>Back to the report</a>"


def message_page(run_id: str | None, text: str, sub: str = "", refresh_s: int | None = None,
                 embed: bool = False, refresh_url: str | None = None, state: str = "off") -> str:
    tell = _embed_note(state, text) if embed else ""
    return _page("3D view", f"{_back(run_id, embed)}<div class=msg><h1>{escape(text)}</h1><p>{escape(sub)}</p></div>",
                 tell, refresh_s=refresh_s, embed=embed, refresh_url=refresh_url)


def finding_page(run_id: str, claim_id: str | None = None, embed: bool = False) -> str:
    """Shown while the spot is being picked; reloads itself (same claim, same embed mode) until the pick is ready."""
    return message_page(run_id, "Finding the spot…",
                        "Looking through the scan's photos for the spot that shows this. "
                        "The first look takes about half a minute; after that it opens straight away.",
                        refresh_s=3, embed=embed, refresh_url=tour_url(run_id, claim_id, embed), state="finding")


def render_tour(run: dict, target: dict, key: str | None, cache_root: Path | None = None,
                spot: dict | None = None, embed: bool = False) -> str:
    run_id = str(run.get("run_id") or "")
    c, v = target["claim"], target["verdict"]
    claimed, found = _claimed_text(c, v), str(v.get("found") or "").strip()
    plan = c.get("label") or target.get("scan_title") or "This unit"
    what = KIND_WORDS.get(c.get("kind"), "Claim")
    back = _back(run_id, embed)
    pill = f"<div class=pill>{escape(str(plan))} <span>· {escape(what)}: {escape(claimed)}</span></div>"
    if not key:
        body = back + ("<div class=msg><h1>3D view not available</h1>"
                       "<p>The 3D viewer is not set up on this computer. The report still has the scan pictures.</p></div>")
        off = _embed_note("off", "The 3D view is not set up on this computer.") if embed else ""
        return _page("3D view not available", body, off, embed=embed)
    spot = spot or {"status": "skip", "why": spots.WHOLE_UNIT}
    found_spot = spot.get("status") == "found"
    note = _spot_note(spot)
    confirmed = v.get("verdict") == "confirmed"
    if confirmed:  # a Match row's viewer: same view, no strike-through
        was = f"Listing says {escape(claimed)} — checks out"
        tag_label = f"Listing says: {claimed}. The 3D scan agrees: {found}"
    else:
        was = f"Listing says <s>{escape(claimed)}</s>"
        tag_label = f"Listing says: {_strike(claimed)}. Really: {found}"
    card = ("<div class=card>"
            f"<p class=was>{was}</p>"
            f"<p class=is>The 3D scan shows {escape(found)}</p>"
            f"<p class=note id=note>{escape(note)}</p></div>")
    why = _first_sentence_after_listing(v.get("reason") or "")
    if target.get("scan_title"):
        why = f"From the 3D scan titled “{target['scan_title']}”."
    if found_spot:
        why = f"Here: {spot['seen']}." if spot.get("how") == "picture" and spot.get("seen") else str(spot.get("why") or why)
    sweep = spot.get("sweep") if found_spot and PANO_RE.match(str(spot.get("sweep") or "")) else None
    room_sweeps = [s for s in spot.get("sweeps") or [] if PANO_RE.match(str(s))] if found_spot and not sweep else []
    face = spot.get("face") if isinstance(spot.get("face"), int) and 0 <= spot["face"] < 4 else None
    ss = spots.start_ss(target["model_id"], [sweep] if sweep else room_sweeps) if (sweep or room_sweeps) else None
    cfg = {"model": target["model_id"], "key": key, "boot": SDK_BOOT, "note": note, "embed": bool(embed), "ss": ss,
           "spot": {"status": spot.get("status"), "sweep": sweep, "sweeps": room_sweeps, "face": face},
           "faceBase": spots.FACE_YAW_BASE, "faceStep": spots.FACE_YAW_STEP,
           "pull": 0.5 if sweep else 1.6,
           "tag": {"label": tag_label, "description": why}}
    cfg_js = json.dumps(cfg).replace("</", "<\\/")
    script = (f"<script>window.TOUR_CFG={cfg_js};</script>"
              f"<script type=module>{SCRIPT}</script>")
    body = "<iframe id=mp allow='fullscreen; xr-spatial-tracking' title='3D walkthrough'></iframe>" + back + pill + card
    return _page(f"3D view: {plan}", body, script, embed=embed)


def tour_url(run_id: str, claim_id: str | None = None, embed: bool = False) -> str:
    q = [f"claim={claim_id}"] if claim_id else []
    if embed:
        q.append("embed=1")
    return f"/tour/{run_id}" + ("?" + "&".join(q) if q else "")


def tour_href(run: dict, claim_id: str, embed: bool = False) -> str:
    return tour_url(str(run.get("run_id")), claim_id, embed)
