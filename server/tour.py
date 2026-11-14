""""See it yourself": the real Matterport 3D walkthrough for one claim of a saved run.

GET /tour/<run_id>?claim=<claim id>. The page embeds the scan the claim's scan evidence points to, stands at the
panorama spot that shows the mismatch (picked by tools/scan/spots.py from the scan's room tags, else by a picture
check; cached), turns toward it, and pins one blue client-side marker (Tag.add, never saved to the model) with
Matterport's own popup switched off; our callout next to it says what is visible there. The page never waits for a
pick: not picked yet, or no spot found = the scan's start view, and the card says so. The card is worded by verdict:
strike-through only for a contradicted claim; "close", "checks out" and "Couldn't see it in this scan" otherwise.
Look: the report's (server/report_a.py) serif type, white/ink, one blue accent, square corners, hairline rules.

?embed=1 is the report's inline viewer (an iframe on /report/<run_id>, mounted without a click): no "Back to the
report" link, the page fills the frame, and the page tells
the report how far it got with window.parent.postMessage({rc3d: 1, state, note}) (same origin only). States:
"loading", "playing" (showcase running), "ready" (standing at the picked spot, or at the start view when there is none),
"off" (no viewer here), "error". Standalone, a blurred still of the scan covers the frame until "ready". The report keeps its still picture over the frame
until "ready", so the reader never sees the scan's start position fly over to the spot.

Speed: the showcase URL carries ss=<panorama label> when the cached scan data knows the picked spot's label, so the
scan opens at the spot instead of its start position (ss counts from 1, the label from 0); the SDK then
only turns the camera, or moves instantly while the report's still picture still covers the frame.
prewarm(run) starts the spot picks of every claim with a viewer in the background (on run end and on report load), so
the viewer seldom opens before its pick is cached.

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
from urllib.parse import quote

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
# tools/scan/spots.py; until it is ready the viewer opens at the scan's start view.

PICKER = spots.pick_spot  # tests replace this
WAIT_S = 0.0  # the viewer never waits for a pick: not cached yet = the scan's start view (the pick runs on behind)
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
PREWARM_MAX = 12  # picks warmed per run (mismatches first); the rest are picked when their viewer first opens


def prewarm(run: dict) -> int:
    """Start (never wait for) the spot picks of every claim that has a 3D viewer (mismatches first, at most
    PREWARM_MAX), so the viewers open at the spot; a viewer never waits for a pick. One background thread runs them one after another, in report order
    (gentle on the Matterport rate limit and the picture model); cached picks cost nothing; a viewer that asks first
    starts its own pick. Skipped for the fake runner (invented scan ids) and when there is no viewer key. Returns how
    many picks it will look at."""
    if not PREWARM or run.get("harness") == "fake" or not sdk_key():
        return 0
    targets, keys = [], set()
    try:
        _, verdicts, _ = _parts(run)
        rank = {"contradicted": 0, "close": 1, "confirmed": 3}  # every claim with a viewer; couldn't-check = 2
        for v in sorted(verdicts, key=lambda x: rank.get(x.get("verdict"), 2)):
            if not CLAIM_RE.match(str(v.get("claim_id") or "")):
                continue
            t = tour_target(run, v["claim_id"])
            if not t or not spots.plan_for(t["claim"], t["verdict"]).get("target"):
                continue  # no scan, or a whole-unit claim (nothing to pick)
            k = f"{t['model_id']}/{spots.cache_key(t['claim'])}"
            if k not in keys and len(targets) < PREWARM_MAX:
                keys.add(k)
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


def _spot_note(spot: dict, cant: bool = False) -> str:
    """The card's plain line: what is visible at this spot, or why the tour stays at the start view."""
    st = spot.get("status")
    if st == "found" and spot.get("how") == "picture" and spot.get("seen"):
        return f"At this spot: {spot['seen']}."
    if st == "found":
        return str(spot.get("why") or "")
    why = str(spot.get("why") or "").strip()
    if st == "none" and spot.get("how") == "picture" and spot.get("looked_at"):
        why = why.replace(" spots in the scan and ", " spots and ")
    if st == "pending":
        why = ""
    if cant and why:
        return why
    return (why + " " if why else "") + "Showing the scan from its start position."


# ---------- page ----------
# Same look as the report (server/report_a.py): Newsreader serif, white and ink, one blue accent, square corners,
# hairline rules, small uppercase meta labels. No shadows, no rounded boxes, no red.

FONT = ("<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
        "<link rel=stylesheet media=print onload=\"this.media='all'\" "
        "href='https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap'>")

CSS = """
:root{--ink:#111;--blue:#1a4be0;--mute:#6b6b6b;--ghost:#858585;--soft:#f4f4f4;--line:#d9d9d9;--bg:#fff}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
font:17px/1.45 Newsreader,Georgia,'Times New Roman',serif;-webkit-font-smoothing:antialiased}
#mp{position:fixed;inset:0;width:100%;height:100%;border:0;background:#ededeb}
.meta{margin:0;font-size:12px;line-height:1.3;letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
.back{position:fixed;top:16px;left:16px;z-index:6;display:inline-flex;align-items:center;gap:6px;min-height:40px;
padding:0 14px;background:#fff;color:var(--ink);text-decoration:none;font-size:15px}
.back:hover{color:var(--blue)}.back:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
.still{position:fixed;inset:0;z-index:3;background-color:#ededeb;background-repeat:no-repeat;
background-size:max(200vw,200vh);background-position:calc(50vw - max(50vw,50vh)) calc(50vh - max(50vw,50vh));
transition:opacity .6s ease,visibility 0s linear 0s}
.still::before{content:'';position:absolute;inset:0;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
background:rgba(255,255,255,.18)}
.still .meta{position:absolute;right:16px;top:16px;background:#fff;padding:9px 12px;color:var(--ink);
display:flex;align-items:center;gap:10px}
.still .meta i{width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--blue);border-radius:50%;
animation:spin 1s linear infinite}
body.live .still{opacity:0;visibility:hidden;pointer-events:none;transition:opacity .6s ease,visibility 0s linear .6s}
@keyframes spin{to{transform:rotate(360deg)}}
.card{position:fixed;left:16px;bottom:16px;z-index:5;background:#fff;padding:16px 20px 14px;width:min(400px,calc(100% - 32px));
display:flex;flex-direction:column;gap:6px}
.card p{margin:0}
.card .said{font-size:19px;line-height:1.25;color:var(--ghost);overflow-wrap:anywhere}
.card .truth,.card .head{font-weight:300;font-size:32px;line-height:1.02;letter-spacing:-.03em;overflow-wrap:anywhere}
.card .truth{color:var(--blue)}.card .truth.calm,.card .head{color:var(--ink)}
.card .note{font-size:15px;line-height:1.35;color:var(--mute);border-top:1px solid var(--line);padding-top:10px;margin-top:6px}
.strike{background:linear-gradient(var(--blue),var(--blue)) no-repeat 0 58%/100% .075em;
-webkit-box-decoration-break:clone;box-decoration-break:clone;padding:0 .03em}
.call{position:fixed;left:0;top:0;z-index:4;pointer-events:none;will-change:transform}
.call[hidden]{display:none}
.call::before{content:'';position:absolute;left:16px;top:0;width:18px;height:1px;background:#fff}
.call i{position:absolute;left:-9px;top:-9px;width:18px;height:18px;border-radius:50%;background:var(--blue);
box-shadow:0 0 0 3px #fff}
.call div{position:absolute;left:34px;top:0;transform:translateY(-50%);background:#fff;border-left:2px solid var(--blue);
padding:8px 12px;width:max-content;max-width:min(280px,60vw);font-size:15px;line-height:1.3}
.call div .meta{margin-bottom:2px}
.msg{max-width:560px;margin:18vh auto 0;padding:0 24px}
.msg h1{font-weight:300;font-size:40px;line-height:1.02;letter-spacing:-.03em;margin:0 0 10px}
.msg p{color:var(--mute);margin:0}
body.embed{overflow:hidden}.embed .card{width:min(320px,calc(100% - 24px));left:12px;bottom:12px;padding:12px 16px 10px}
.embed .card .truth,.embed .card .head{font-size:24px}.embed .card .said{font-size:16px}
.embed .card .note{font-size:14px;padding-top:8px;margin-top:4px}
@media (max-width:640px){.card .truth,.card .head{font-size:26px}.still .meta{top:auto;bottom:auto;top:64px}}
@media (prefers-reduced-motion:reduce){.still,body.live .still{transition:none}.still .meta i{animation:none}}
"""

# Runs in the page. CFG comes from the server; nothing here is saved to the model (Tag.add is client-side only).
SCRIPT = r"""
const CFG = window.TOUR_CFG, T = window.TOUR = {connected: false, placed: null, room: 'start', how: ''};
// the report frames this page from the same server under either name (this page itself always runs on localhost)
const PARENTS = [location.origin, location.origin.replace('//localhost', '//127.0.0.1')].filter((o, i, a) => a.indexOf(o) === i);
const note = document.getElementById('note'), call = document.getElementById('call');
const say = (s) => { T.how = s; if (note && s) note.textContent = s; };
const f = document.getElementById('mp');
const stamp = (k) => { T['t_' + k] = Math.round(performance.now()); };
const tell = (state, extra) => { T.state = state; stamp(state);
  if (state === 'ready' || state === 'error') document.body.classList.add('live');
  if (CFG.embed && window.parent !== window) for (const o of PARENTS) { try { window.parent.postMessage(Object.assign({rc3d: 1, state, note: T.how}, extra || {}), o); } catch (e) {} } };
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
const BLUE = {r: 26 / 255, g: 75 / 255, b: 224 / 255};
// one blue marker where the view looks, with no Matterport popup; our own callout next to it carries the words
async function mark(sdk) {
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
  const ids = await sdk.Tag.add({label: CFG.tag.label, description: '', anchorPosition: anchor,
    stemVector: {x: 0, y: 0.25, z: 0}, color: BLUE});
  T.placed = ids && ids[0]; stamp('tag');
  try { await sdk.Tag.allowAction(T.placed, {opening: false, navigating: false, docking: false, sharing: false}); T.noPopup = 'allowAction'; }
  catch (e) { try { await sdk.Mattertag.preventAction(T.placed, {opening: true, navigating: true, sharing: true}); T.noPopup = 'preventAction'; }
    catch (e2) { T.noPopup = false; } }
  if (!call) return;
  const box = call.querySelector('div'), card = document.querySelector('.card');
  const head = {x: anchor.x, y: anchor.y + 0.25, z: anchor.z};
  const place = (ps) => { if (!ps || !ps.position) return;
    const sz = {w: f.clientWidth, h: f.clientHeight}, yaw = (ps.rotation.y || 0) * Math.PI / 180;
    const front = (head.x - ps.position.x) * -Math.sin(yaw) + (head.z - ps.position.z) * -Math.cos(yaw) > 0.1;
    let s = null; try { s = sdk.Conversion.worldToScreen(head, ps, sz); } catch (e) {}
    const on = !!(s && front && s.x > 8 && s.x < sz.w - 40 && s.y > 8 && s.y < sz.h - 8);
    call.hidden = !on; if (!on) return;
    call.style.transform = 'translate(' + Math.round(s.x) + 'px,' + Math.round(s.y) + 'px)';
    // the words stay on screen and clear of the card; the dot itself stays on the marker
    const bh = box.offsetHeight, bw = box.offsetWidth, cr = card ? card.getBoundingClientRect() : null;
    let dy = Math.max(12 + bh / 2 - s.y, Math.min(0, sz.h - 12 - bh / 2 - s.y));
    if (cr && s.x + 34 < cr.right && s.x + 34 + bw > cr.left && s.y + dy + bh / 2 > cr.top - 8) dy = Math.min(dy, cr.top - 8 - bh / 2 - s.y);
    box.style.marginTop = Math.round(dy) + 'px'; };
  place(await sdk.Camera.getPose());
  try { sdk.Camera.pose.subscribe(place); } catch (e) {}
  T.callout = !call.hidden;
}
try {
  const {connect} = await import(CFG.boot + '?applicationKey=' + encodeURIComponent(CFG.key));
  stamp('sdk');
  const sdk = await connect(f);
  T.connected = true; window.sdk = sdk; stamp('connected');
  await new Promise(r => { let d = 0; sdk.App.state.subscribe(s => { if (!d && s.phase === sdk.App.Phase.PLAYING) { d = 1; r(); } });
    setTimeout(() => { if (!d) { d = 1; r(); } }, 20000); });
  tell('playing');
  const S = CFG.spot || {};
  if (!S.sweep && !(S.sweeps && S.sweeps.length)) {
    say(CFG.note);  // no spot: the start view is the answer, nothing to move to
  } else {
  // fast path: ss= opened the showcase right at the picked spot, so only turn to the view that shows it
  const cur0 = S.sweep && CFG.ss ? await here(sdk) : null;
  if (cur0 && (cur0.id === S.sweep || cur0.sid === S.sweep)) {
    T.startedAtSpot = true; T.at = cur0.id;
    if (Number.isInteger(S.face) && cur0.rotation) {
      try { await sdk.Camera.setRotation({x: 0, y: (cur0.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face}, {speed: 2000}); T.faced = true; } catch (e) {}
    }
    T.room = 'moved'; stamp('moved'); say(CFG.note);
  } else {
  const sweeps = await coll(sdk.Sweep.data);
  T.sweeps = sweeps.length; stamp('sweeps');
  let rooms = [];
  if (!S.sweep && S.sweeps && S.sweeps.length) { try { rooms = await coll(sdk.Room.data, 4000); } catch (e) {} }
  let go = null, rot = null;
  if (S.sweep) {
    // a. the one spot the server picked (Model API panoLocation id = SDK sweep id), facing the view that shows it
    go = sweeps.find(s => s.id === S.sweep || s.sid === S.sweep) || null;
    if (go && Number.isInteger(S.face)) rot = {x: 0, y: (go.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face};
  } else {
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
    // the still picture (or the report's) covers the frame until "ready", so the move is instant
    const opts = {transition: sdk.Sweep.Transition.INSTANT};
    if (rot) { opts.rotation = rot; T.faced = true; }
    // with ss= the showcase already opened at the spot: then only turn the camera (a move there costs seconds)
    const start = await here(sdk); T.startedAtSpot = !!(start && (start.id === go.id || start.sid === go.sid));
    if (T.startedAtSpot) { if (rot) { try { await sdk.Camera.setRotation(rot, {speed: 2000}); } catch (e) { T.moveErr = 'turn'; } } }
    else {
      try { await sdk.Sweep.moveTo(go.id, opts); }
      catch (e) { T.moveErr = String(e && e.message || e); if (rot) { try { await sdk.Camera.setRotation(rot, {speed: 1000}); } catch (e2) {} } }
    }
    stamp('moved');
    await wait(250);
    // where the camera really stands now (the SDK's current sweep), not where it was asked to go
    const cur = await here(sdk); T.at = cur && cur.id; stamp('here');
    if (T.at === go.id || (T.at && T.at === go.sid)) { T.room = 'moved'; say(CFG.note); }
    else say('The picked spot could not be reached in this viewer, so this is the scan\'s start position.');
  } else {
    // no sweep list (slow load): the showcase may already stand at the spot through ss=
    const cur = await here(sdk); T.at = cur && cur.id;
    const want = new Set([S.sweep].concat(S.sweeps || []).filter(Boolean));
    if (cur && (want.has(cur.id) || want.has(cur.sid))) {
      T.room = 'moved'; T.viaSs = true; say(CFG.note);
      if (S.sweep && Number.isInteger(S.face) && cur.rotation) {
        try { await sdk.Camera.setRotation({x: 0, y: (cur.rotation.y || 0) + CFG.faceBase + CFG.faceStep * S.face}, {speed: 1000}); T.faced = true; } catch (e) {}
      }
    } else say('The picked spot is not in this viewer, so this is the scan\'s start position.');
  }
  }
  }
  tell('ready', {room: T.room});
  if (T.room === 'moved') await mark(sdk);
} catch (e) {
  T.err = String(e && e.message || e);
  say(T.connected ? 'The 3D view could not turn to the spot here.' : 'The 3D view could not open here just now.');
  if (T.state !== 'ready') tell(T.connected ? 'ready' : 'error', {room: T.room});
}
T.done = true;
"""


def _claimed_text(claim: dict, verdict: dict) -> str:
    return str(verdict.get("claimed") or claim.get("quote") or "").strip()


def _embed_note(state: str, text: str) -> str:
    """In the report's frame, tell the report what this message page says (same origin only; words only)."""
    msg = json.dumps({"rc3d": 1, "state": state, "note": text}).replace("</", "<\\/")
    return ("<script>(function(){if(window.parent===window)return;var o=location.origin;"
            "[o,o.replace('//localhost','//127.0.0.1')].forEach(function(t,i,a){if(a.indexOf(t)!==i)return;"
            f"try{{window.parent.postMessage({msg},t)}}catch(e){{}}}});}})();</script>")


# In the report's frame (any embed page): Esc is sent up to the report, which never hears keys typed in here
# (cross-origin frame), so its panel closes as it would on the page; the scan's own frame keeps keys from this page,
# so focus comes back here when the pointer leaves it (onto the label card or the note).
ESC_FORWARD = ("<script>(function(){if(window.parent===window)return;var o=location.origin;"
               "var to=[o,o.replace('//localhost','//127.0.0.1')].filter(function(t,i,a){return a.indexOf(t)===i;});"
               "addEventListener('keydown',function(e){if(e.key!=='Escape')return;to.forEach(function(t){"
               "try{window.parent.postMessage({rc3d:1,key:'Escape'},t)}catch(x){}});});"
               "addEventListener('mouseover',function(e){var a=document.activeElement;"
               "if(a&&a.tagName==='IFRAME'&&e.target!==a){a.blur();window.focus();}});})();</script>")


def _page(title: str, body: str, script: str = "", embed: bool = False, head: str = "") -> str:
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=referrer content=no-referrer>{PRECONNECT}{head}{FONT}"
            f"<title>{escape(title)}</title><style>{CSS}</style></head>"
            f"<body{' class=embed' if embed else ''}>{body}{script}{ESC_FORWARD if embed else ''}</body></html>")


def _back(run_id: str | None, embed: bool) -> str:
    """"Back to the report": standalone pages only (inside the report's own frame it makes no sense)."""
    if not run_id or embed:
        return ""
    return f"<a class=back href='/report/{escape(run_id, True)}'>&larr; Back to the report</a>"


def message_page(run_id: str | None, text: str, sub: str = "", embed: bool = False, state: str = "off") -> str:
    tell = _embed_note(state, text) if embed else ""
    return _page("3D view", f"{_back(run_id, embed)}<div class=msg><h1>{escape(text)}</h1><p>{escape(sub)}</p></div>",
                 tell, embed=embed)


IMG_RE = re.compile(r"^/img/[a-f0-9]{16}$")


def _still(run: dict, target: dict) -> str | None:
    """A picture from this scan to show while the live view loads: the verdict's own, else one of the scan's
    (the claim's picture check first). Only /img/<token> URLs, never a file path."""
    v, cid, mid = target["verdict"], target["claim"].get("id"), target["model_id"]
    if IMG_RE.match(str(v.get("image") or "")):
        return v["image"]
    _, _, evidence = _parts(run)
    scans = [e for e in _scan_ev(evidence) if e["observed"].get("model_id") == mid]
    scans.sort(key=lambda e: cid not in (e["observed"].get("claim_ids") or []))
    for e in scans:
        for im in e.get("images") or []:
            if isinstance(im, dict) and IMG_RE.match(str(im.get("path") or "")):
                return im["path"]
    return None


def _card(target: dict, spot: dict, note: str) -> str:
    """The caption card, worded by verdict: a strike-through only when the scan contradicts the listing."""
    c, v = target["claim"], target["verdict"]
    claimed, found = _claimed_text(c, v), str(v.get("found") or "").strip()
    plan = c.get("label") or target.get("scan_title") or "This unit"
    meta = f"<p class=meta>{escape(KIND_WORDS.get(c.get('kind'), 'Claim'))} · {escape(str(plan))}</p>"
    verdict = v.get("verdict")
    if verdict == "contradicted" and found:
        body = (f"<p class=said>Listing says <span class=strike>{escape(claimed)}</span></p>"
                f"<p class=truth>The 3D scan shows {escape(found)}</p>")
    elif verdict == "close" and found:
        body = (f"<p class=said>Listing says {escape(claimed)}</p>"
                f"<p class='truth calm'>Close: the 3D scan shows {escape(found)}</p>")
    elif verdict == "confirmed":
        body = (f"<p class=said>Listing says {escape(claimed)}</p>"
                "<p class='truth calm'>Checks out in the 3D scan</p>")
    else:  # couldn't check: no strike, no "unclear"
        body = (f"<p class=said>Listing says “{escape(claimed)}”</p>"
                "<p class=head>Couldn’t see it in this scan</p>")
    return f"<aside class=card aria-live=polite>{meta}{body}<p class=note id=note>{escape(note)}</p></aside>"


def render_tour(run: dict, target: dict, key: str | None, cache_root: Path | None = None,
                spot: dict | None = None, embed: bool = False) -> str:
    """The viewer page. Never waits for anything: the spot is whatever is cached ("pending" = start view)."""
    run_id = str(run.get("run_id") or "")
    c, v = target["claim"], target["verdict"]
    claimed = _claimed_text(c, v)
    plan = c.get("label") or target.get("scan_title") or "This unit"
    back = _back(run_id, embed)
    if not key:
        body = back + ("<div class=msg><h1>3D view not available</h1>"
                       "<p>The 3D viewer is not set up on this computer. The report still has the scan pictures.</p></div>")
        off = _embed_note("off", "The 3D view is not set up on this computer.") if embed else ""
        return _page("3D view not available", body, off, embed=embed)
    spot = spot or {"status": "skip", "why": spots.WHOLE_UNIT}
    found_spot = spot.get("status") == "found"
    cant = v.get("verdict") not in ("contradicted", "close", "confirmed")
    note = _spot_note(spot, cant)
    sweep = spot.get("sweep") if found_spot and PANO_RE.match(str(spot.get("sweep") or "")) else None
    room_sweeps = [s for s in spot.get("sweeps") or [] if PANO_RE.match(str(s))] if found_spot and not sweep else []
    face = spot.get("face") if isinstance(spot.get("face"), int) and 0 <= spot["face"] < 4 else None
    ss = spots.start_ss(target["model_id"], [sweep] if sweep else room_sweeps) if (sweep or room_sweeps) else None
    callout = ""
    if found_spot:  # the callout at the marker says what is there; the card's note then says how it was picked
        if spot.get("how") == "picture" and spot.get("seen"):
            callout = f"{spot['seen']}."
            n = spot.get("looked_at")
            note = f"Picked by a picture check of {n} spots in the scan." if n else "Picked by a picture check of the scan."
        else:
            callout = str(spot.get("why") or "")
            note = "Picked from the scan's own room tags."
    cfg = {"model": target["model_id"], "key": key, "boot": SDK_BOOT, "note": note, "embed": bool(embed), "ss": ss,
           "spot": {"status": spot.get("status"), "sweep": sweep, "sweeps": room_sweeps, "face": face},
           "faceBase": spots.FACE_YAW_BASE, "faceStep": spots.FACE_YAW_STEP,
           "pull": 0.5 if sweep else 1.6,
           "tag": {"label": claimed[:80], "callout": callout}}
    cfg_js = json.dumps(cfg).replace("</", "<\\/")
    script = (f"<script>window.TOUR_CFG={cfg_js};</script>"
              f"<script type=module>{SCRIPT}</script>")
    # the showcase starts loading while the page is still being parsed (quick start, no intro, help or title)
    src = (f"https://my.matterport.com/show/?m={target['model_id']}&play=1&qs=1&title=0&brand=0&help=0&hl=0&gt=0"
           f"&dh=1&mls=0&search=0&vr=0" + (f"&ss={ss}" if ss else "") + ("&hr=0&wh=0" if embed else "")
           + "&applicationKey=" + quote(key, safe=""))
    boot = f"{SDK_BOOT}?applicationKey={quote(key, safe='')}"
    head = f"<link rel=modulepreload href='{escape(boot, True)}' crossorigin>"
    still = _still(run, target) if not embed else None  # in the report, the report's own still covers the frame
    bg = f" style='background-image:url({still})'" if still else ""
    cover = "" if embed else (
        f"<div class=still id=still{bg}>"
        "<p class=meta><i aria-hidden=true></i>Opening the 3D scan…</p></div>")
    call = ""
    if callout:
        call = (f"<div class=call id=call hidden><i></i><div><p class=meta>This spot</p>{escape(callout)}</div></div>")
    body = (f"<iframe id=mp src='{escape(src, True)}' allow='fullscreen; xr-spatial-tracking' allowfullscreen "
            "title='3D walkthrough'></iframe>" + cover + call + back + _card(target, spot, note))
    return _page(f"3D view: {plan}", body, script, embed=embed, head=head)


def tour_url(run_id: str, claim_id: str | None = None, embed: bool = False) -> str:
    q = [f"claim={claim_id}"] if claim_id else []
    if embed:
        q.append("embed=1")
    return f"/tour/{run_id}" + ("?" + "&".join(q) if q else "")


def tour_href(run: dict, claim_id: str, embed: bool = False) -> str:
    return tour_url(str(run.get("run_id")), claim_id, embed)
