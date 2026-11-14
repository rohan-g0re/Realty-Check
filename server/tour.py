""""See it yourself": the real Matterport 3D walkthrough for one claim of a saved run.

GET /tour/<run_id>?claim=<claim id>. The page embeds the scan the claim's scan evidence points to, moves into the
room the claim is about when the scan's own room tags single out exactly one room, and pins one client-side label
(Tag.add, never saved to the model) with what the listing says and what the scan shows.

The Showcase SDK key is read at request time from the environment or the private .env and only ever written into
the served page. No key: the page says the 3D view is not available.
"""
from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path

MODEL_RE = re.compile(r"^[A-Za-z0-9]{6,24}$")
CLAIM_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PANO_RE = re.compile(r"^[a-z0-9]{6,40}$")
SDK_BOOT = "https://api.matterport.com/sdk/bootstrap/3.1.0-0-g1cd7e0997a/sdk.es6.js"
NOT_A_ROOM_TYPE = {"unfurnished", "furnished", "other", "void"}
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


# ---------- which room ----------

def _target_word(claim: dict, verdict: dict) -> str | None:
    kind = claim.get("kind")
    found = str(verdict.get("found") or "").lower()
    if kind == "bedrooms":
        return "den" if re.search(r"\bden\b", found) else "bedroom"
    if kind == "bathrooms":
        return "bathroom"
    return None


def _room_names(room: dict) -> list:
    names = [room.get("label")] + list(room.get("tags") or []) \
        + [c.get("label") for c in room.get("classifications") or [] if isinstance(c, dict)]
    return [str(n).strip().lower() for n in names if n and str(n).strip()]


def pick_room(model_id: str, claim: dict, verdict: dict, cache_root: Path | None) -> dict:
    """Room from the scan's own cached room tags (saved when the run fetched the scan). Never guessed:
    a room is picked only when exactly one room carries the word the claim is about."""
    word = _target_word(claim, verdict)
    if not word:
        return {"word": None, "why": "This claim is about the whole unit, not one room."}
    model = None
    if cache_root is not None:
        p = cache_root / "scan" / model_id / "summary.json"
        try:
            model = ((json.loads(p.read_text(encoding="utf-8")).get("data") or {}).get("model")) or None
        except (OSError, ValueError, AttributeError):
            model = None
    if not model:
        return {"word": word, "why": f"The scan's room list was not saved, so we can't tell which room is the {word}."}
    rx = re.compile(rf"\b{re.escape(word)}s?\b")
    hits = [r for r in model.get("rooms") or [] if isinstance(r, dict) and any(rx.search(n) for n in _room_names(r))]
    if len(hits) != 1:
        why = (f"No room in the scan is tagged {word}." if not hits
               else f"{len(hits)} rooms in the scan are tagged {word}, so we can't tell which one the listing means.")
        return {"word": word, "why": why}
    room = hits[0]
    other = [n for n in _room_names(room) if not rx.search(n) and n not in NOT_A_ROOM_TYPE]
    if other:
        return {"word": word, "why": f"The scan's only {word} shares one space with the {other[0]}, "
                                     "so we can't stand you in it."}
    panos = [p["id"] for p in model.get("panoLocations") or []
             if isinstance(p, dict) and ((p.get("anchor") or {}).get("room") or {}).get("id") == room.get("id")
             and PANO_RE.match(str(p.get("id") or ""))]
    area = (room.get("dimensions") or {}).get("areaFloor")
    return {"word": word, "room_id": str(room.get("id") or ""), "sweeps": panos,
            "area_sqft": round(area * 10.7639) if isinstance(area, (int, float)) else None,
            "why": f"This is the only room the scan tags as a {word}."}


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
@media (max-width:640px){.pill{top:64px;max-width:calc(100% - 32px)}.card{right:16px;max-width:none}}
"""

# Runs in the page. CFG comes from the server; nothing here is saved to the model (Tag.add is client-side only).
SCRIPT = r"""
const CFG = window.TOUR_CFG, T = window.TOUR = {connected: false, placed: null, room: 'start', how: ''};
const note = document.getElementById('note');
const say = (s) => { T.how = s; if (note) note.textContent = s; };
const f = document.getElementById('mp');
f.src = 'https://my.matterport.com/show/?m=' + encodeURIComponent(CFG.model) +
  '&play=1&qs=1&title=0&brand=0&help=0&applicationKey=' + encodeURIComponent(CFG.key);
const wait = (ms) => new Promise(r => setTimeout(r, ms));
const coll = (c) => new Promise(r => { let d = 0; c.subscribe({onCollectionUpdated(x) { if (!d) { d = 1; r(Object.values(x)); } }});
  setTimeout(() => { if (!d) { d = 1; r([]); } }, 8000); });
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
try {
  const {connect} = await import(CFG.boot + '?applicationKey=' + encodeURIComponent(CFG.key));
  const sdk = await connect(f);
  T.connected = true; window.sdk = sdk;
  await new Promise(r => { let d = 0; sdk.App.state.subscribe(s => { if (!d && s.phase === sdk.App.Phase.PLAYING) { d = 1; r(); } });
    setTimeout(() => { if (!d) { d = 1; r(); } }, 20000); });
  const sweeps = await coll(sdk.Sweep.data);
  T.sweeps = sweeps.length;
  let rooms = [];
  try { rooms = await coll(sdk.Room.data); } catch (e) {}
  T.rooms = rooms.length; T.roomKeys = rooms[0] ? Object.keys(rooms[0]) : [];
  let tags = [];
  try { tags = await coll(sdk.Tag.data); } catch (e) {}
  T.tags = tags.length;
  // 1. the room the server picked from the scan's own room tags
  let inRoom = [];
  if (CFG.room && CFG.room.room_id) {
    const ids = new Set(CFG.room.sweeps || []);
    inRoom = sweeps.filter(s => ids.has(s.id) || ids.has(s.sid) || (s.roomInfo && s.roomInfo.id === CFG.room.room_id));
    T.matchedSweeps = inRoom.length;
  }
  // 2. else a room or an existing label in the viewer that names the word exactly once
  if (!inRoom.length && CFG.room && CFG.room.word) {
    const rx = new RegExp('\\b' + CFG.room.word + 's?\\b', 'i');
    const rl = rooms.filter(r => rx.test(String(r.label || r.name || '')));
    const tl = tags.filter(t => rx.test(String(t.label || '')));
    if (rl.length === 1) { inRoom = sweeps.filter(s => s.roomInfo && s.roomInfo.id === rl[0].id); T.how2 = 'viewer room label'; }
    else if (!rl.length && tl.length === 1 && tl[0].anchorPosition) {
      const a = tl[0].anchorPosition; inRoom = [sweeps.slice().sort((p, q) => dist(p.position, a) - dist(q.position, a))[0]];
      T.how2 = 'existing label in the scan';
    }
  }
  if (inRoom.length) {
    const c = inRoom.reduce((m, s) => ({x: m.x + s.position.x / inRoom.length, y: m.y + s.position.y / inRoom.length,
      z: m.z + s.position.z / inRoom.length}), {x: 0, y: 0, z: 0});
    const go = inRoom.slice().sort((p, q) => dist(p.position, c) - dist(q.position, c))[0];
    // face the middle of that room (its bounds in the viewer), so the view looks into it, not out of it
    const rid = go.roomInfo && go.roomInfo.id, rm = rooms.find(r => r.id === rid);
    let look = c;
    if (rm && rm.bounds && rm.bounds.min && rm.bounds.max) {
      const b = rm.bounds; look = {x: (b.min.x + b.max.x) / 2, y: go.position.y, z: (b.min.z + b.max.z) / 2};
    }
    const opts = {transition: sdk.Sweep.Transition.FLY, transitionTime: 1200};
    const dx = look.x - go.position.x, dz = look.z - go.position.z;
    if (Math.hypot(dx, dz) > 0.4) { opts.rotation = {x: 0, y: Math.atan2(-dx, -dz) * 180 / Math.PI}; T.faced = true; }
    await sdk.Sweep.moveTo(go.id, opts);
    await wait(1800);
    T.room = 'moved'; say(CFG.room.why || 'Moved into the room this claim is about.');
  } else {
    say((CFG.room && CFG.room.why ? CFG.room.why + ' ' : '') + 'Showing the scan from its start position; the label is placed in front of you.');
  }
  // label: in front of the current view, pulled back from the wall it hits
  const pose = await sdk.Camera.getPose();
  let at = null;
  try { const w = await sdk.Renderer.getWorldPositionData({x: f.clientWidth / 2, y: f.clientHeight * 0.55}); at = w && w.position; } catch (e) {}
  const p = pose.position;
  if (!at || dist(at, p) > 8 || dist(at, p) < 0.3) {
    const yaw = (pose.rotation.y || 0) * Math.PI / 180;
    at = {x: p.x - Math.sin(yaw) * 2, y: p.y, z: p.z - Math.cos(yaw) * 2};
  }
  T.hitDist = Math.round(dist(at, p) * 10) / 10;
  const k = Math.min(0.6, 1.6 / Math.max(dist(at, p), 0.01));
  const anchor = {x: at.x + (p.x - at.x) * k, y: p.y - 0.35, z: at.z + (p.z - at.z) * k};
  const ids = await sdk.Tag.add({label: CFG.tag.label, description: CFG.tag.description, anchorPosition: anchor,
    stemVector: {x: 0, y: 0.25, z: 0}, color: {r: 0.70, g: 0.15, b: 0.12}});
  T.placed = ids && ids[0];
  try { await sdk.Tag.open(T.placed); } catch (e) { T.openErr = String(e && e.message || e); }
} catch (e) {
  T.err = String(e && e.message || e);
  say('The 3D view could not start here.');
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


def _page(title: str, body: str, script: str = "") -> str:
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=referrer content=no-referrer>"
            f"<title>{escape(title)}</title><style>{CSS}</style></head><body>{body}{script}</body></html>")


def message_page(run_id: str | None, text: str, sub: str = "") -> str:
    back = f"<a class=back href='/report/{escape(run_id, True)}'>Back to the report</a>" if run_id else ""
    return _page("3D view", f"{back}<div class=msg><h1>{escape(text)}</h1><p>{escape(sub)}</p></div>")


def render_tour(run: dict, target: dict, key: str | None, cache_root: Path | None) -> str:
    run_id = str(run.get("run_id") or "")
    c, v = target["claim"], target["verdict"]
    claimed, found = _claimed_text(c, v), str(v.get("found") or "").strip()
    plan = c.get("label") or target.get("scan_title") or "This unit"
    what = KIND_WORDS.get(c.get("kind"), "Claim")
    back = f"<a class=back href='/report/{escape(run_id, True)}'>Back to the report</a>"
    pill = f"<div class=pill>{escape(str(plan))} <span>· {escape(what)}: {escape(claimed)}</span></div>"
    if not key:
        body = back + ("<div class=msg><h1>3D view not available</h1>"
                       "<p>The 3D viewer is not set up on this computer. The report still has the scan pictures.</p></div>")
        return _page("3D view not available", body)
    room = pick_room(target["model_id"], c, v, cache_root)
    note = room.get("why") or ""
    card = ("<div class=card>"
            f"<p class=was>Listing says <s>{escape(claimed)}</s></p>"
            f"<p class=is>The 3D scan shows {escape(found)}</p>"
            f"<p class=note id=note>{escape(note)}</p></div>")
    why = _first_sentence_after_listing(v.get("reason") or "")
    if target.get("scan_title"):
        why = f"From the 3D scan titled “{target['scan_title']}”."
    if room.get("room_id"):
        why = room["why"]
    cfg = {"model": target["model_id"], "key": key, "boot": SDK_BOOT,
           "room": {k: room.get(k) for k in ("word", "room_id", "sweeps", "why") if room.get(k)},
           "tag": {"label": f"Listing says: {_strike(claimed)}. Really: {found}", "description": why}}
    cfg_js = json.dumps(cfg).replace("</", "<\\/")
    script = (f"<script>window.TOUR_CFG={cfg_js};</script>"
              f"<script type=module>{SCRIPT}</script>")
    body = "<iframe id=mp allow='fullscreen; xr-spatial-tracking' title='3D walkthrough'></iframe>" + back + pill + card
    return _page(f"3D view: {plan}", body, script)


def tour_href(run: dict, claim_id: str) -> str:
    return f"/tour/{run.get('run_id')}?claim={claim_id}"
