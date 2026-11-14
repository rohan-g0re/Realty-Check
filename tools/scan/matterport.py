"""Matterport scan evidence: scan_summary and scan_stills (docs/CONTRACT.md 2.4, 3).

Read-only GraphQL queries only (never a mutation), at most one request every 0.6 s.
Every GraphQL response is cached under rc_cache/scan/<model_id>/ with URLs stripped.
Signed asset URLs live only in memory for the few seconds needed to download them.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import requests
from PIL import Image

import rc_common
from rc_common import evidence

API = "https://api.matterport.com/api/models/graph"
M2_TO_SQFT = 10.7639
MAX_SIDE = 1568
LIMIT = rc_common.RateLimit(0.6)
CALLS = {"n": 0}  # live network calls made by this module (GraphQL + downloads)

SUMMARY_Q = """query Summary($id: ID!) { model(id: $id) {
  id name
  address { address locality postalCode }
  dimensionEstimates(units: metric) { area areaIndoor areaIndoorWithWalls }
  floors { id label sequence }
  rooms { id label tags floor { id }
    classifications { label confidence }
    dimensions(units: metric) { areaFloor } }
  panoLocations { id label anchor { room { id } } }
} }"""

STILLS_Q = """query Stills($id: ID!) { model(id: $id) {
  id
  panoLocations { id label anchor { room { id } } skybox(resolution: "2k") { status children } }
  assets { floorplans(formats: ["png"], flags: [schematic]) { url floor { id } } }
} }"""

# Every panorama spot with its position and room, for picking the spot that shows a claim (tools/scan/spots.py).
# The SDK's sweep id is this panoLocation id (checked on a real model); SDK position = (x, z, -y) of this one.
SPOTS_Q = """query Spots($id: ID!) { model(id: $id) {
  id name
  rooms { id label tags classifications { label confidence } dimensions(units: metric) { areaFloor } }
  panoLocations { id label position { x y z } anchor { room { id } floor { id } }
    skybox(resolution: "low") { status children } }
} }"""

_SESSION: requests.Session | None = None


# ---------- network ----------

def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.auth = (rc_common.secret("MATTERPORT_API_KEY"), rc_common.secret("MATTERPORT_API_TOKEN"))
        _SESSION = s
    return _SESSION


def _gql(query: str, model_id: str) -> dict:
    """One read-only GraphQL POST. Returns the parsed body; raises on HTTP / parse failure."""
    if "mutation" in query.split("{", 1)[0].lower():
        raise RuntimeError("mutations are forbidden")
    LIMIT.wait()
    CALLS["n"] += 1
    r = _session().post(API, json={"query": query, "variables": {"id": model_id}}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Matterport API answered HTTP {r.status_code}")
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError("Matterport API returned an unexpected body")
    return body


def _download(url: str) -> Image.Image:
    LIMIT.wait()
    CALLS["n"] += 1
    r = requests.get(url, timeout=90)  # signed URL: no auth header, never logged
    if r.status_code != 200:
        raise RuntimeError(f"image download answered HTTP {r.status_code}")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def spot_query(model_id: str) -> dict | None:
    """Fresh read-only query for rooms and panorama spots (positions, low-res skybox faces). Not cached here:
    the result holds signed URLs that must stay in memory; callers store only scrub()-bed copies."""
    if not _valid_id(model_id):
        return None
    return (_gql(SPOTS_Q, model_id).get("data") or {}).get("model")


def download_image(url: str) -> Image.Image:
    """One signed-URL download through the shared rate limit. The URL is never logged or stored."""
    return _download(url)


def scrub(obj: Any) -> Any:
    """Copy of obj with every URL-looking string removed (safe to write to disk)."""
    return _scrub(obj)


# ---------- helpers ----------

def _valid_id(model_id: Any) -> bool:
    return isinstance(model_id, str) and bool(re.fullmatch(r"[A-Za-z0-9]{6,20}", model_id))


def _scrub(obj: Any) -> Any:
    """Drop every URL-looking string before anything touches disk."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str) and obj.lower().startswith("http"):
        return "<url removed>"
    return obj


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(_scrub(obj), indent=1), encoding="utf-8")


def _errors(body: dict) -> list[str]:
    return [str(e.get("message") or "")[:120] for e in body.get("errors") or []]


def _room_label(room: dict) -> str | None:
    if (room.get("label") or "").strip():
        return room["label"].strip()
    cls = [c for c in room.get("classifications") or [] if c.get("label")]
    if not cls:
        return None
    return max(cls, key=lambda c: c.get("confidence") or 0)["label"]


def _is_bedroom(label: str) -> bool:
    return "bedroom" in label.lower()


def _is_bathroom(label: str) -> bool:
    low = label.lower()
    return "bathroom" in low or low in ("toilet", "half bath", "powder room")


def _beds_from_name(name: str) -> int | None:
    if re.search(r"\bstudio\b", name, re.I):
        return 0
    m = re.search(r"(\d+)\s*(?:BR|BD|BED)", name, re.I)
    return int(m.group(1)) if m else None


def _baths_from_name(name: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d)?)\s*(?:BA|BATH)", name, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return int(v) if v.is_integer() else v


def _model(model_id: str, query: str, cache_name: str | None) -> tuple[dict | None, list[str]]:
    """Fetch (or load cached) model dict. Returns (model or None, error messages)."""
    path = rc_common.cache_dir("scan", model_id) / cache_name if cache_name else None
    if path and path.exists():
        body = json.loads(path.read_text(encoding="utf-8"))
    else:
        body = _gql(query, model_id)
        if path:
            _write_json(path, body)
    return (body.get("data") or {}).get("model"), _errors(body)


# ---------- scan_summary ----------

def scan_summary(model_id: str) -> dict:
    """Evidence(source="scan") with room labels, bed/bath counts, floor area and floors of one scan."""
    if not _valid_id(model_id):
        return evidence("scan", "no_evidence", "No usable Matterport model id was given.",
                        detail=f"invalid model id {model_id!r}")
    try:
        m, errs = _model(model_id, SUMMARY_Q, "summary.json")
    except Exception as e:  # network, auth, HTTP
        return evidence("scan", "error", "The Matterport API could not be reached for this scan.",
                        detail=f"Matterport Model API (GraphQL) query failed: {type(e).__name__}: {str(e)[:150]}")
    if not m:
        return evidence("scan", "no_evidence", f"Matterport has no accessible scan with id {model_id}.",
                        detail="Matterport Model API (GraphQL) returned no model. " + "; ".join(errs)[:300])
    return _summarize(model_id, m)


def _summarize(model_id: str, m: dict) -> dict:
    name = (m.get("name") or "").strip()
    rooms = m.get("rooms") or []
    labels = [lab for lab in (_room_label(r) for r in rooms) if lab]
    obs: dict[str, Any] = {"model_id": model_id}
    if name:
        obs["model_name"] = name
    notes: list[str] = []

    if labels:
        obs["room_labels"] = labels
        obs["bedrooms"] = sum(_is_bedroom(x) for x in labels)
        obs["bathrooms"] = sum(_is_bathroom(x) for x in labels)
        notes.append(f"bedrooms/bathrooms counted from {len(labels)} labelled rooms")
    else:
        beds, baths = _beds_from_name(name), _baths_from_name(name)
        if beds is not None:
            obs["bedrooms"] = beds
        if baths is not None:
            obs["bathrooms"] = baths
        if beds is not None or baths is not None:
            obs["counts_from"] = "model_name"
            notes.append(f"no room in the scan carries a label ({len(rooms)} rooms); "
                         f"bedroom/bathroom counts parsed from the scan title {name!r}")

    de = m.get("dimensionEstimates") or {}
    room_m2 = [(r.get("dimensions") or {}).get("areaFloor") for r in rooms]
    room_m2 = [x for x in room_m2 if x]
    if de.get("areaIndoor"):
        area_m2, how = de["areaIndoor"], "model indoor area estimate"
    elif de.get("area"):
        area_m2, how = de["area"], "model area estimate"
    elif room_m2:
        area_m2, how = sum(room_m2), f"sum of floor area of {len(room_m2)} scanned rooms"
    else:
        area_m2, how = None, None
    if area_m2:
        obs["area_m2"] = round(area_m2, 1)
        obs["area_sqft"] = round(area_m2 * M2_TO_SQFT)
        notes.append(f"area_sqft = {how} ({area_m2:.1f} m2 x 10.7639)")

    floors = m.get("floors") or []
    if floors:
        obs["floors"] = len(floors)
    panos = m.get("panoLocations") or []
    obs["panoramas"] = len(panos)

    usable = any(k in obs for k in ("room_labels", "bedrooms", "bathrooms", "area_sqft"))
    detail = ("Matterport Model API (GraphQL, read-only query) for model " + model_id + ": "
              + ("; ".join(notes) if notes else "no rooms, labels or dimensions exposed") + ".")
    if not usable:
        return evidence("scan", "no_evidence",
                        f"Scan {model_id} exposes no room labels, counts or floor area.",
                        observed=obs, detail=detail)

    parts = []
    if "bedrooms" in obs:
        src = "its title" if obs.get("counts_from") == "model_name" else "its room labels"
        parts.append(f"{obs['bedrooms']} bedroom{'s' if obs['bedrooms'] != 1 else ''} by {src}")
    if "area_sqft" in obs:
        parts.append(f"about {obs['area_sqft']} sq ft of scanned floor")
    if "floors" in obs:
        parts.append(f"{obs['floors']} floor{'s' if obs['floors'] != 1 else ''}")
    title = f" titled '{name}'" if name else ""
    summary = f"The 3D scan{title} shows " + ", ".join(parts) + "." if parts else f"The 3D scan{title} was found."
    return evidence("scan", "ok", summary, observed=obs, detail=detail)


# ---------- scan_stills ----------

def scan_stills(model_id: str, max_n: int = 6) -> dict:
    """Evidence(source="scan") with up to max_n images (floor plan + panorama views) saved in the cache."""
    if not _valid_id(model_id):
        return evidence("scan", "no_evidence", "No usable Matterport model id was given.",
                        detail=f"invalid model id {model_id!r}")
    max_n = max(1, int(max_n))
    folder = rc_common.cache_dir("scan", model_id)
    manifest_path = folder / "stills.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if manifest and _manifest_covers(folder, manifest, max_n):
        return _stills_evidence(model_id, folder, manifest["images"][:max_n], cached=True)

    try:
        summary_model, _ = _model(model_id, SUMMARY_Q, "summary.json")
        if not summary_model:
            return evidence("scan", "no_evidence", f"Matterport has no accessible scan with id {model_id}.",
                            detail="Matterport Model API (GraphQL) returned no model.")
        m, errs = _model(model_id, STILLS_Q, None)  # fresh: signed URLs expire in ~5 minutes
    except Exception as e:
        return evidence("scan", "error", "The Matterport API could not be reached for this scan.",
                        detail=f"Matterport Model API (GraphQL) query failed: {type(e).__name__}: {str(e)[:150]}")
    if not m:
        return evidence("scan", "no_evidence", f"Matterport has no accessible scan with id {model_id}.",
                        detail="Matterport Model API (GraphQL) returned no model. " + "; ".join(errs)[:300])

    room_names = {r["id"]: _room_label(r) for r in summary_model.get("rooms") or []}
    plans, panos, total = _candidates(m, max_n)
    images, failures = [], 0
    for kind, key, url_or_faces, caption in plans + panos:
        if len(images) >= max_n:
            break
        fname = f"{kind}_{key}.jpg"
        path = folder / fname
        try:
            if not path.exists():
                img = _download(url_or_faces) if kind == "floorplan" else _grid(url_or_faces)
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                img.save(path, "JPEG", quality=85)
        except Exception:
            failures += 1
            continue
        if kind == "pano":
            room = room_names.get(caption)
            caption = (f"Panorama {key} in the {room}" if room else f"Panorama {key} (room not labelled in scan)") \
                + ": the four horizontal views around the camera in a 2x2 grid"
        images.append({"file": fname, "caption": caption})

    if not images:
        return evidence("scan", "no_evidence", f"No downloadable images were found for scan {model_id}.",
                        detail=f"Matterport skybox/floor plan download: {failures} failures, "
                               f"{len(panos)} panoramas with 2k skybox, {len(plans)} floor plans.")
    # everything the scan offers (floor plans + panoramas), so a later larger max_n refetches
    available = total - failures
    _write_json(manifest_path, {"model_id": model_id, "available": available, "images": images})
    return _stills_evidence(model_id, folder, images, cached=False)


def _manifest_covers(folder: Path, manifest: dict, max_n: int) -> bool:
    imgs = manifest.get("images") or []
    need = min(max_n, manifest.get("available") or len(imgs))
    return len(imgs) >= need and all((folder / i["file"]).exists() for i in imgs[:need])


def _candidates(m: dict, max_n: int) -> tuple[list, list, int]:
    """Floor plans first (one per floor), then evenly spaced panoramas with a 2k skybox."""
    plans = []
    for i, fp in enumerate((m.get("assets") or {}).get("floorplans") or []):
        if fp.get("url"):
            plans.append(("floorplan", str(i + 1), fp["url"], f"Schematic floor plan {i + 1} of the scan"))
    plans = plans[:1] if max_n <= 2 else plans[:2]
    ok = [p for p in m.get("panoLocations") or [] if len((p.get("skybox") or {}).get("children") or []) >= 5]
    total = len(plans) + len(ok)
    want = max(0, max_n - len(plans))
    if want and ok:
        step = max(1, len(ok) // want)
        ok = ok[::step][:want]
    else:
        ok = []
    panos = [("pano", re.sub(r"\W", "", str(p.get("label") or p["id"])) or p["id"],
              p["skybox"]["children"][1:5], ((p.get("anchor") or {}).get("room") or {}).get("id"))
             for p in ok]
    return plans, panos, total


def _grid(face_urls: list[str]) -> Image.Image:
    """2x2 grid of the four horizontal skybox faces."""
    half = MAX_SIDE // 2
    grid = Image.new("RGB", (half * 2, half * 2))
    for i, u in enumerate(face_urls[:4]):
        grid.paste(_download(u).resize((half, half)), ((i % 2) * half, (i // 2) * half))
    return grid


def _stills_evidence(model_id: str, folder: Path, images: list[dict], cached: bool) -> dict:
    out = [{"path": (folder / i["file"]).resolve().as_posix(), "caption": i["caption"]} for i in images]
    n_plan = sum(i["file"].startswith("floorplan") for i in images)
    summary = (f"Saved {len(out)} still{'s' if len(out) != 1 else ''} from the 3D scan "
               f"({n_plan} floor plan{'s' if n_plan != 1 else ''}, {len(out) - n_plan} panorama views).")
    detail = ("Matterport Model API (GraphQL, read-only): schematic PNG floor plans and 2k skybox faces "
              "(four horizontal faces per panorama in a 2x2 grid), resized to at most 1568 px"
              + ("; served from local cache." if cached else "; downloaded now and cached."))
    return evidence("scan", "ok", summary, observed={"model_id": model_id, "image_count": len(out)},
                    images=out, detail=detail)
