"""Pick the panorama spot in a 3D scan that shows what a mismatched claim is about (the tour page stands there).

pick_spot(model_id, claim, verdict) -> Pick, cached per (model, claim kind, claimed value) under
rc_cache/spots/<model_id>/. Order of methods, first that settles it:
  1. scan geometry: rooms the scan itself tags (the smallest of several bedrooms when the scan has more than
     the listing says; the only bathroom when it has fewer);
  2. picture check: low-res views around up to 10 spread-out spots, one narrow question (tools.vision.look);
     the answer names a spot and says in a few words what is visible there, or "none".
Never guesses: no answer, a vague answer or a failure gives status "none" and the tour keeps its start view.

Pick (plain JSON, never a URL):
  {"status": "found" | "none" | "skip", "how": "geometry" | "picture" | None,
   "sweep": panorama id (= the SDK sweep id) or None, "sweeps": [ids of the room's spots] (geometry),
   "room_id", "face": which of the four views (0-3, A-D) faces the thing, or None (the tour page turns
   the camera to it with FACE_YAW_BASE / FACE_YAW_STEP),
   "seen": what is visible there in plain words (picture check only), "why": one plain sentence for the card,
   "looked_at": number of spots shown to the picture check, "key": cache key}
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

import rc_common
from tools.scan.matterport import scrub

MAX_SPOTS = 10
FACE_PX = 384
MIN_CONFIDENCE = 0.6
# skybox children 1..4 are the horizontal faces, left to right around the camera. Face k (0-based among
# those four) looks along SDK camera yaw = sweep yaw + 180 - 90 * k, where the sweep yaw is the SDK's
# Sweep.data rotation.y (checked on a real model in the browser). The tour page applies it.
FACE_YAW_BASE, FACE_YAW_STEP = 180.0, -90.0
VIEWS = "ABCD"
NOT_A_ROOM_TYPE = {"unfurnished", "furnished", "other", "void", ""}
WHOLE_UNIT = "This claim is about the whole unit, not one spot in it."

# ---------- what to look for ----------

def _count(text: str, word: str) -> int | None:
    m = re.search(rf"(\d+(?:\.\d)?)\s+{word}", text or "")
    return int(float(m.group(1))) if m else None


def _num(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def plan_for(claim: dict, verdict: dict) -> dict:
    """What to look for: {"target": "den"|"bedroom"|"bathroom"|"feature"|None, "mode", "question", "why"}."""
    kind = claim.get("kind")
    found = str(verdict.get("found") or "").lower()
    claimed_text = str(verdict.get("claimed") or claim.get("quote") or "").strip()
    claimed_n = _num(claim.get("value"))
    if kind == "bedrooms":
        if re.search(r"\bden\b", found):
            return {"target": "den", "mode": "picture",
                    "question": (f'A rental listing says "{claimed_text}", but the 3D scan suggests the unit is '
                                 f'really {found}. Which spot stands in, or looks straight into, a small room or '
                                 "nook without a closet that could be called a den (not a full bedroom)?")}
        n = _count(found, "bedroom")
        if n is not None and claimed_n is not None and n > claimed_n:
            return {"target": "bedroom", "mode": "extra",
                    "question": "Which spot stands in, or looks straight into, a bedroom (a closed room with "
                                "space for a bed, usually with a closet)?"}
        if n is not None and claimed_n is not None and n == claimed_n:  # a confirmed count (report's Match tab)
            return {"target": None, "why": "The scan has as many bedrooms as the listing says; "
                                           "that is about the whole unit, not one spot in it."}
        return {"target": None, "why": "The scan has fewer bedrooms than the listing says, "
                                       "so there is no one spot that shows the difference."}
    if kind == "bathrooms":
        n = _count(found, "bathroom")
        mode = "extra" if (n is not None and claimed_n is not None and n > claimed_n) else "only"
        return {"target": "bathroom", "mode": mode,
                "question": "Which spot stands in, or looks straight into, a bathroom (toilet, tub or shower "
                            "visible)?"}
    if kind in ("amenity", "laundry", "parking", "view", "other"):
        return {"target": "feature", "mode": "picture",
                "question": (f'A rental listing claims: "{claimed_text}". Which spot gives the clearest view of '
                             "the place in this apartment where that would be (for example the kitchen for a "
                             "kitchen feature)? In seen, say plainly whether the feature itself is visible.")}
    return {"target": None, "why": WHOLE_UNIT}


def _slug(v) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v).lower()).strip("-")[:40] or "x"


def cache_key(claim: dict) -> str:
    return f"{_slug(claim.get('kind'))}_{_slug(claim.get('value'))}"


def cache_path(model_id: str, claim: dict) -> Path:
    return rc_common.cache_dir("spots", model_id) / f"{cache_key(claim)}.json"


def cached_pick(model_id: str, claim: dict) -> dict | None:
    p = cache_path(model_id, claim)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


# ---------- scan data ----------

def _names(room: dict) -> list[str]:
    names = [room.get("label")] + list(room.get("tags") or []) \
        + [c.get("label") for c in room.get("classifications") or [] if isinstance(c, dict)]
    return [str(n).strip().lower() for n in names if n and str(n).strip()]


def _room_of(pano: dict) -> str | None:
    return ((pano.get("anchor") or {}).get("room") or {}).get("id")


def _load_model(model_id: str, client) -> dict | None:
    """Rooms and spots (no URLs): the run's saved summary, else the spots cache, else one fresh query."""
    folder = rc_common.cache_dir("spots", model_id)
    for p in (rc_common.cache_dir("scan", model_id) / "summary.json", folder / "model.json"):
        if p.exists():
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
                m = (body.get("data") or {}).get("model") if "data" in body else body
                if m and m.get("panoLocations"):
                    return m
            except (OSError, ValueError, AttributeError):
                pass
    m = client.spot_query(model_id)
    if m:
        _save_model(folder, m)
    return m


LABEL_RE = re.compile(r"^\d{1,4}$")


def start_ss(model_id: str, pano_ids: list) -> str | None:
    """The showcase `ss=` deep-link value that opens the scan at the first of these spots, from the cached scan data
    only (never a network call). A spot's Model API `label` is its 0-based sweep number and `ss` counts from 1
    (checked in the browser: label 13 needs ss=14). None when the spot or its label is not cached."""
    want = [str(p) for p in pano_ids or [] if p]
    if not want:
        return None
    for p in (rc_common.cache_dir("scan", model_id) / "summary.json",
              rc_common.cache_dir("spots", model_id) / "model.json"):
        try:
            body = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            continue
        m = (body.get("data") or {}).get("model") if isinstance(body, dict) and "data" in body else body
        if not isinstance(m, dict):
            continue
        labels = {str(x.get("id")): str(x.get("label") or "") for x in m.get("panoLocations") or []
                  if isinstance(x, dict)}
        for pid in want:
            lab = labels.get(pid, "")
            if LABEL_RE.match(lab):
                return str(int(lab) + 1)
    return None


def _save_model(folder: Path, m: dict) -> None:
    (folder / "model.json").write_text(json.dumps(scrub(m), indent=1), encoding="utf-8")


def _face_paths(model_id: str, pano: dict) -> list[Path]:
    folder = rc_common.cache_dir("spots", model_id, "faces")
    pid = re.sub(r"\W", "", str(pano.get("id")))
    return [folder / f"{pid}_{k}.jpg" for k in range(4)]


def _has_urls(pano: dict) -> bool:
    kids = (pano.get("skybox") or {}).get("children") or []
    return len(kids) >= 5 and all(isinstance(u, str) and u.lower().startswith("http") for u in kids[1:5])


# ---------- 1. geometry ----------

def geometry_pick(plan: dict, model: dict) -> dict | None:
    """A room the scan's own tags settle, or None. Rooms that share their space with another room type are
    skipped (standing there would not show the thing)."""
    word = plan["target"]
    if word not in ("bedroom", "bathroom", "den"):
        return None
    rx = re.compile(rf"\b{word}s?\b")
    hits = [r for r in model.get("rooms") or [] if isinstance(r, dict) and any(rx.search(n) for n in _names(r))]
    pure = [r for r in hits if not [n for n in _names(r) if not rx.search(n) and n not in NOT_A_ROOM_TYPE]]
    room, why = None, ""
    if word == "den" and len(hits) == 1 and pure:
        room, why = pure[0], "This is the only room the scan tags as a den."
    elif plan.get("mode") == "only" and len(hits) == 1 and pure:
        room, why = pure[0], f"This is the only room the scan tags as a {word}."
    elif plan.get("mode") == "extra" and len(pure) >= 2 and len(pure) == len(hits):
        def area(r):
            a = (r.get("dimensions") or {}).get("areaFloor")
            return a if isinstance(a, (int, float)) else math.inf
        if all(area(r) < math.inf for r in pure):
            room = min(pure, key=area)
            why = f"The scan tags {len(pure)} rooms as {word}s; this is the smallest of them."
    if room is None:
        return None
    sweeps = [p["id"] for p in model.get("panoLocations") or [] if _room_of(p) == room.get("id") and p.get("id")]
    if not sweeps:
        return None
    a = (room.get("dimensions") or {}).get("areaFloor")
    return {"status": "found", "how": "geometry", "sweep": None, "sweeps": sweeps, "room_id": room.get("id"),
            "seen": None, "why": why,
            "area_sqft": round(a * 10.7639) if isinstance(a, (int, float)) else None}


# ---------- 2. picture check ----------

def spread_spots(panos: list[dict], n: int = MAX_SPOTS) -> list[dict]:
    """Up to n spots: one per room first (its most central spot), then the farthest from those picked."""
    usable = [p for p in panos if isinstance(p.get("position"), dict)]
    if len(usable) <= n:
        return usable

    def xy(p):
        return (p["position"].get("x") or 0.0, p["position"].get("y") or 0.0)

    picked: list[dict] = []
    by_room: dict[Any, list] = {}
    for p in usable:
        by_room.setdefault(_room_of(p), []).append(p)
    for room, ps in sorted(by_room.items(), key=lambda kv: -len(kv[1])):
        if room is None or len(picked) >= n:
            continue
        cx, cy = sum(xy(p)[0] for p in ps) / len(ps), sum(xy(p)[1] for p in ps) / len(ps)
        picked.append(min(ps, key=lambda p: math.dist(xy(p), (cx, cy))))
    while len(picked) < n:
        rest = [p for p in usable if p not in picked]
        if not rest:
            break
        if not picked:
            picked.append(rest[0])
            continue
        picked.append(max(rest, key=lambda p: min(math.dist(xy(p), xy(q)) for q in picked)))
    return picked


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _strip(faces: list[Image.Image], number: int) -> Image.Image:
    """The four views around one spot side by side, with "Spot n" and view letters A-D drawn on."""
    head = 44
    img = Image.new("RGB", (FACE_PX * 4, FACE_PX + head), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 6), f"Spot {number}", fill="black", font=_font(30))
    for i, f in enumerate(faces[:4]):
        img.paste(f.convert("RGB").resize((FACE_PX, FACE_PX)), (i * FACE_PX, head))
        d.rectangle([i * FACE_PX + 6, head + 6, i * FACE_PX + 46, head + 46], fill="white")
        d.text((i * FACE_PX + 14, head + 8), VIEWS[i], fill="black", font=_font(30))
    return img


def _faces(model_id: str, pano: dict, client) -> list[Image.Image]:
    """The four horizontal skybox faces of one spot, from the image cache or downloaded once."""
    paths = _face_paths(model_id, pano)
    if not all(p.exists() for p in paths):
        if not _has_urls(pano):
            raise RuntimeError("no skybox faces for this spot")
        for p, u in zip(paths, pano["skybox"]["children"][1:5]):
            if not p.exists():
                im = client.download_image(u)
                im.thumbnail((FACE_PX * 2, FACE_PX * 2))
                im.save(p, "JPEG", quality=85)
    return [Image.open(p) for p in paths]


def _sheets(model_id: str, spots: list[dict], client) -> tuple[list[str], list[dict]]:
    """Contact sheets with two spots each (spot numbers 1..n). Spots without faces are dropped."""
    folder = rc_common.cache_dir("spots", model_id)
    strips, kept = [], []
    for p in spots:
        try:
            faces = _faces(model_id, p, client)
        except Exception:
            continue
        kept.append(p)
        strips.append(_strip(faces, len(kept)))
    paths = []
    for i in range(0, len(strips), 2):
        pair = strips[i:i + 2]
        sheet = Image.new("RGB", (pair[0].width, sum(s.height for s in pair) + 12 * (len(pair) - 1)), "black")
        y = 0
        for s in pair:
            sheet.paste(s, (0, y))
            y += s.height + 12
        ids = "-".join(re.sub(r"\W", "", str(p.get("id")))[:6] for p in kept[i:i + 2])
        path = folder / f"sheet_{i // 2 + 1}_{ids}.jpg"
        sheet.save(path, "JPEG", quality=85)
        paths.append(str(path))
    return paths, kept


FIELDS = {
    "spot": 'number of the spot that best shows it, or "none" if no spot clearly shows it',
    "view": "letter A, B, C or D of the view at that spot that faces it, or null",
    "seen": ("at most 12 plain words a renter would understand, saying what is visible at that spot, e.g. "
             "'Small room off the living room, no closet, no door'; null if none"),
}


def _ask(plan: dict, sheets: list[str], n: int, look: Callable) -> dict:
    question = (f"Each image shows two spots of a 3D scan of one apartment. Every spot is one row of four "
                f"photos (views A, B, C, D) taken around the camera; spots are numbered 1 to {n}.\n\n"
                f"{plan['question']}\n\nPick only a spot where it is clearly visible. If no spot clearly shows "
                'it, answer "none". Do not guess.')
    return look(sheets, question, FIELDS)


def picture_pick(model_id: str, plan: dict, model: dict, client, look: Callable) -> dict:
    spots = spread_spots(model.get("panoLocations") or [])
    if not spots:
        return {"status": "none", "how": "picture", "why": "The scan has no panorama spots to look at.",
                "looked_at": 0}
    sheets, kept = _sheets(model_id, spots, client)
    if not kept:
        return {"status": "none", "how": "picture", "why": "The scan's panorama photos could not be loaded.",
                "looked_at": 0, "error": True}
    res = _ask(plan, sheets, len(kept), look)
    ans = res.get("answer") or {}
    if res.get("status") != "ok":
        return {"status": "none", "how": "picture", "looked_at": len(kept), "error": True,
                "why": "The picture check could not run just now."}
    n = _num(ans.get("spot")) if re.fullmatch(r"\s*\d+\s*", str(ans.get("spot"))) else None
    seen = " ".join(str(ans.get("seen") or "").split())[:120].rstrip(".")
    what = {"den": "a den", "bedroom": "a bedroom", "bathroom": "a bathroom"}.get(plan["target"], "it")
    if n is None or not (1 <= n <= len(kept)) or not seen or float(res.get("confidence") or 0) < MIN_CONFIDENCE:
        return {"status": "none", "how": "picture", "looked_at": len(kept),
                "why": f"We looked at {len(kept)} spots in the scan and none clearly shows {what}."}
    p = kept[n - 1]
    view = str(ans.get("view") or "").strip().upper()[:1]
    return {"status": "found", "how": "picture", "sweep": p["id"], "sweeps": [], "room_id": _room_of(p),
            "face": VIEWS.index(view) if view and view in VIEWS else None, "seen": seen,
            "why": f"Picture check of {len(kept)} spots in the scan: {seen}.", "looked_at": len(kept)}


# ---------- entry ----------

def _default_client():
    from tools.scan import matterport
    return matterport


def _default_look():
    from tools.vision import look
    return look


def pick_spot(model_id: str, claim: dict, verdict: dict, client=None, look: Callable | None = None) -> dict:
    """Cached pick of the spot that shows this claim's mismatch (see module doc). Errors are not cached."""
    key = cache_key(claim)
    hit = cached_pick(model_id, claim)
    if hit is not None:
        return hit
    plan = plan_for(claim, verdict)
    if not plan.get("target"):
        return {"status": "skip", "how": None, "sweep": None, "why": plan.get("why") or WHOLE_UNIT, "key": key}
    client = client or _default_client()
    model = _load_model(model_id, client)
    if not model:
        return {"status": "none", "how": None, "sweep": None, "key": key, "error": True,
                "why": "The scan's spots could not be loaded just now."}
    pick = geometry_pick(plan, model)
    if pick is None and plan.get("question"):
        panos = model.get("panoLocations") or []
        need = not any(isinstance(p.get("position"), dict) for p in panos) or any(
            not all(f.exists() for f in _face_paths(model_id, p)) and not _has_urls(p) for p in spread_spots(panos))
        if need:  # positions and face URLs live only in a fresh answer
            fresh = client.spot_query(model_id)
            if fresh:
                _save_model(rc_common.cache_dir("spots", model_id), fresh)
                model = fresh
        pick = picture_pick(model_id, plan, model, client, look or _default_look())
    if pick is None:
        pick = {"status": "none", "how": "geometry", "why": "The scan's own room tags do not single out one room."}
    pick = {"sweep": None, "sweeps": [], "room_id": None, "seen": None, "face": None,
            **pick, "key": key, "target": plan["target"]}
    if not pick.get("error"):
        cache_path(model_id, claim).write_text(json.dumps(scrub(pick), indent=1), encoding="utf-8")
    return pick
