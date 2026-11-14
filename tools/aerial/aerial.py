"""Aerial evidence from the Vexcel API v2: top-down and angled photos, and roof height above ground.

Every response is cached under rc_cache/aerial/<lat>_<lon>/ so a repeat call makes no network call.
The API key travels only in the X-API-KEY header; it is never logged, cached or put in evidence.
"""
from __future__ import annotations

import io
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import requests
import tifffile
from PIL import Image

import rc_common
from tools import cachectl

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
MAX_PX = 1568
CALLS = {"n": 0}  # live network calls made by this module (tests read it)
_RATE = rc_common.RateLimit(0.6)

# Stories rule (see building_height): ground floor + roof parapet allowance, then one floor per 3.2 m.
GROUND_PLUS_PARAPET_M = 6.0  # ~4.5 m tall ground floor + ~1.5 m parapet / roof equipment
FLOOR_M = 3.2                # typical residential floor-to-floor (range 3.0-3.3 m)


# ---------- small helpers ----------

def _key(lat: float, lon: float) -> str:
    return f"{lat:.5f}_{lon:.5f}"


def _dir(lat: float, lon: float) -> Path:
    return rc_common.cache_dir("aerial", _key(lat, lon))


def _square(lat: float, lon: float, half: float) -> str:
    dlat = half / 111_320
    dlon = half / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _point(lat: float, lon: float) -> str:
    return f"POINT({lon} {lat})"


class _NoCoverage(Exception):
    pass


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None,
          timeout: float = 120) -> bytes:
    """One rate-limited Vexcel call. Returns body bytes; raises _NoCoverage on 403/404, RuntimeError otherwise."""
    headers = {"X-API-KEY": rc_common.secret("VEXCEL_API_KEY")}
    r = None
    for attempt in range(3):
        _RATE.wait()
        CALLS["n"] += 1
        r = requests.request(method, f"{API}/{path}", headers=headers, params=params, json=body, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        break
    if r.status_code in (403, 404):
        raise _NoCoverage(f"{path} answered {r.status_code}")
    if not r.ok:
        raise RuntimeError(f"Vexcel {path} answered HTTP {r.status_code}")
    return r.content


def _fetch(path_on_disk: Path, method: str, api_path: str, params=None, body=None) -> bytes:
    """Cached raw response: read from disk, or call the API and save the bytes."""
    if cachectl.hit(path_on_disk):
        return path_on_disk.read_bytes()
    data = _call(method, api_path, params=params, body=body)
    path_on_disk.write_bytes(data)
    return data


def _capture_date(blob: bytes) -> str | None:
    """'Fri Jan 02 18:43:55 UTC 2026' from the Vexcel XMP block -> '2026-01-02'."""
    text = blob[:300_000].decode("latin-1", "ignore")
    m = re.search(r'Vexcel:last-capture-date[">]+([^"<]+)', text)
    if not m:  # oblique frames carry a single 'capture-date' as 2025-12-31 17:36:51.0
        m = re.search(r'Vexcel:capture-date[">]+(\d{4}-\d\d-\d\d)', text)
        return m.group(1) if m else None
    try:
        return time.strftime("%Y-%m-%d", time.strptime(m.group(1).strip(), "%a %b %d %H:%M:%S UTC %Y"))
    except ValueError:
        return m.group(1).strip()[:40]


def _collection(blob: bytes) -> str | None:
    m = re.search(r'Vexcel:collection[">]+([^"<]+)', blob[:300_000].decode("latin-1", "ignore"))
    return m.group(1).strip() if m else None


def _save_jpg(raw: bytes, out: Path) -> Path:
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((MAX_PX, MAX_PX))
    im.save(out, "JPEG", quality=85)
    return out


_SPONSOR = re.compile(r"\b(top-down |angled )?aerial (photos?|views?)", re.I)


def named(ev: dict) -> dict:
    """Evidence whose display text names the sponsor ('Top-down Vexcel aerial photo', 'Vexcel elevation data: ...'),
    also for evidence cached before the name was added. Keys, paths and numbers are unchanged."""
    if not isinstance(ev, dict):
        return ev

    def fix(text: str) -> str:
        if not isinstance(text, str) or "vexcel" in text.lower():
            return text
        return _SPONSOR.sub(lambda m: (m.group(1) or "") + "Vexcel aerial " + m.group(2), text, count=1)
    ev = {**ev, "summary": fix(ev.get("summary") or "")}
    if (ev.get("observed") or {}).get("height_m") is not None and "vexcel" not in ev["summary"].lower():
        ev["summary"] = "Vexcel elevation data: " + ev["summary"][:1].lower() + ev["summary"][1:]
    ev["images"] = [{**im, "caption": fix(im.get("caption") or "")} if isinstance(im, dict) else im
                    for im in ev.get("images") or []]
    return ev


def _cached_evidence(path: Path, build) -> dict:
    """Return cached Evidence, or build it; cache it unless it is an error (errors may be transient)."""
    if cachectl.hit(path):
        ev = json.loads(path.read_text(encoding="utf-8"))
        if all(Path(i["path"]).exists() for i in ev.get("images", [])):
            return ev
    ev = build()
    if ev["status"] != "error":
        path.write_text(json.dumps(ev, indent=1), encoding="utf-8")
    return ev


NO_COVERAGE = ("Vexcel has no aerial coverage at this point "
               "(confirmed coverage: Houston and Los Angeles).")


# ---------- aerial_images ----------

def aerial_images(lat: float, lon: float, half_m: int = 60) -> dict:
    """One top-down photo (square of 2*half_m metres) and up to 2 angled views, saved as JPEGs in the cache."""
    try:
        d = _dir(lat, lon)
        return named(_cached_evidence(d / f"images_h{half_m}.json", lambda: _build_images(lat, lon, half_m, d)))
    except Exception as ex:  # never raise
        return rc_common.evidence("aerial", "error", "Vexcel aerial photos could not be fetched.",
                                  detail=f"{type(ex).__name__}: {str(ex)[:200]}")


def _build_images(lat: float, lon: float, half_m: int, d: Path) -> dict:
    try:
        raw = _fetch(d / f"ortho_h{half_m}.raw.jpg", "POST", "ortho/extract", body={
            "wkt": _square(lat, lon, half_m), "srid": 4326, "layer": LAYERS, "crop": "bounding_box",
            "image-format": "jpeg", "destination-dimensions": f"{MAX_PX},{MAX_PX}"})
    except _NoCoverage as ex:
        return rc_common.evidence("aerial", "no_evidence", NO_COVERAGE, detail=f"Vexcel ortho/extract: {ex}.")
    date, coll = _capture_date(raw), _collection(raw)
    images = [{"path": str(_save_jpg(raw, d / f"topdown_h{half_m}.jpg")),
               "caption": "Top-down Vexcel aerial photo" + (f" ({date})" if date else "")}]
    skipped = []
    for direction in ("north", "east"):
        try:
            oraw = _fetch(d / f"oblique_{direction}_h{half_m}.raw.jpg", "POST", "oriented/extract", body={
                "wkt": _point(lat, lon), "buffer-meter": half_m, "srid": 4326, "layer": "urban",
                "product-type": f"oblique-{direction}", "rotation": "natural", "crop": "bbox", "image-format": "jpeg"})
        except (_NoCoverage, RuntimeError) as ex:
            skipped.append(f"oblique-{direction}: {ex}")
            continue
        odate = _capture_date(oraw)
        images.append({"path": str(_save_jpg(oraw, d / f"oblique_{direction}_h{half_m}.jpg")),
                       "caption": f"Angled Vexcel aerial view, camera looking {direction}"
                                  + (f" ({odate})" if odate else "")})
    n_obl = len(images) - 1
    summary = f"Top-down Vexcel aerial photo and {n_obl} angled view{'s' if n_obl != 1 else ''} of the point."
    detail = (f"Vexcel API v2 ortho/extract (layers {LAYERS}, {2 * half_m} m square) and oriented/extract "
              f"(oblique, layer urban, {half_m} m buffer). Top-down collection {coll or 'unknown'}, captured {date or 'unknown'}.")
    if skipped:
        detail += " Skipped: " + "; ".join(skipped) + "."
    return rc_common.evidence("aerial", "ok", summary, {"capture_date": date, "angled_views": n_obl}, images, detail)


# ---------- building_height ----------

def stories_from_height(h: float) -> tuple[int, list[int]]:
    """Estimate floors from roof height: 1 ground floor + parapet (6.0 m together), then 3.2 m per floor.
    Range uses 3.3 m (fewest) and 3.0 m (most) per floor. 23.1 m -> 6 [6, 7]; 16.2 m -> 4 [4, 4]."""
    def n(per):
        return max(1, 1 + round((h - GROUND_PLUS_PARAPET_M) / per))
    return n(FLOOR_M), [n(3.3), n(3.0)]


def _tif(raw: bytes):
    p = tifffile.TiffFile(io.BytesIO(raw)).pages[0]
    a = p.asarray().astype("float64")
    a[a <= -9000] = np.nan
    return a, p.tags["ModelPixelScaleTag"].value[0], p.tags["ModelTiepointTag"].value


def building_height(lat: float, lon: float) -> dict:
    """Roof height above the surrounding ground from Vexcel elevation models, plus a floor estimate."""
    try:
        d = _dir(lat, lon)
        return named(_cached_evidence(d / "height.json", lambda: _build_height(lat, lon, d)))
    except Exception as ex:  # never raise
        return rc_common.evidence("aerial", "error", "The roof height could not be measured from Vexcel data.",
                                  detail=f"{type(ex).__name__}: {str(ex)[:200]}")


def _build_height(lat: float, lon: float, d: Path) -> dict:
    sq = {"wkt": _square(lat, lon, 40), "srid": 4326}
    try:
        dsm_raw = _fetch(d / "dsm_80m.tif", "GET", "dsm/extract", params=sq)
    except _NoCoverage as ex:
        return rc_common.evidence("aerial", "no_evidence", NO_COVERAGE, detail=f"Vexcel dsm/extract: {ex}.")
    dsm, sx, tp = _tif(dsm_raw)
    H, W = dsm.shape
    yy, xx = np.mgrid[0:H, 0:W]
    # tiepoint is the centre pixel (the requested point); scale is Web-Mercator metres -> ground metres
    rr = np.hypot(yy - tp[1], xx - tp[0]) * sx * math.cos(math.radians(lat))
    try:
        dtm_raw = _fetch(d / "dtm_80m.tif", "GET", "dtm/extract", params=sq)
        dtm, sx2, tp2 = _tif(dtm_raw)
        dy = ((yy - tp[1]) * sx / sx2 + tp2[1]).round().astype(int).clip(0, dtm.shape[0] - 1)
        dx = ((xx - tp[0]) * sx / sx2 + tp2[0]).round().astype(int).clip(0, dtm.shape[1] - 1)
        above = dsm - dtm[dy, dx]
        ground_how = "ground = Vexcel terrain model (DTM)"
    except (_NoCoverage, RuntimeError):
        ring = dsm[(rr >= 25) & (rr <= 40) & np.isfinite(dsm)]
        if ring.size < 100:
            return rc_common.evidence("aerial", "no_evidence", "No ground level could be measured around the point.",
                                      detail="Vexcel dsm/extract ok, dtm/extract unavailable, ring too small.")
        above = dsm - float(np.percentile(ring, 5))
        ground_how = "ground = 5th percentile of the surface model in a 25-40 m ring"
    v = above[(rr <= 20) & np.isfinite(above)]
    if v.size < 100:
        return rc_common.evidence("aerial", "no_evidence", "The Vexcel elevation model has no data at this point.",
                                  detail="Vexcel dsm/extract returned no valid pixels within 20 m.")
    p98 = float(np.percentile(v, 98))
    if p98 < 3.0:
        return rc_common.evidence("aerial", "no_evidence", "No raised roof found within 20 m of the point.",
                                  detail=f"98th percentile height within 20 m is {p98:.1f} m.")
    roof = round(float(np.median(v[v >= 0.6 * p98])), 1)
    stories, rng = stories_from_height(roof)
    date, coll = _capture_date(dsm_raw), _collection(dsm_raw)
    summary = f"Vexcel elevation data: roof is {roof} m above the ground, about {stories} floors."
    detail = (f"Vexcel API v2 dsm/extract and dtm/extract, 80 m square. Roof level = median of heights at or above "
              f"60% of the 98th percentile within 20 m of the point ({ground_how}). Floors = 1 + round((height - 6.0 m) / 3.2 m) "
              f"(6.0 m = ~4.5 m ground floor + ~1.5 m parapet; range uses 3.3-3.0 m per floor). "
              f"Surface model {coll or 'unknown'}, captured {date or 'unknown'}.")
    return rc_common.evidence("aerial", "ok", summary,
                              {"height_m": roof, "stories": stories, "stories_range": rng, "capture_date": date},
                              detail=detail)


# ---------- property_attributes ----------

PROPERTY_VERSION = 2   # bump when the parsing below changes; the cache file name carries it
PROPERTY_TIMEOUT_S = 8

# Our name -> the service's yes/no field (property/extract answers with flat, dotted keys).
# hot_tub is reported by the service as a pool type ("pool.types.hot-tub.detected").
_FLAGS = {"hot_tub": "pool.types.hot-tub.detected", "sport_court": "sport-court.detected",
          "playground": "playground.detected", "trampoline": "trampoline.detected", "deck": "deck.detected"}
_POOL_KINDS = (("in-ground", "in-ground"), ("above-ground", "above-ground"), ("hot-tub", "hot tub"))


def _get(p: dict, key: str):
    """Field by its dotted name: flat key as the service sends it, or nested dicts as a fallback."""
    if key in p:
        return p[key]
    cur = p
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _bool(v) -> bool | None:
    return v if isinstance(v, bool) else None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _solar(p: dict) -> bool | None:
    """Solar is per building ('structures' list: roof-objects.solar.value, or a photovoltaic panel count > 0)."""
    seen = []
    for s in p.get("structures") or []:
        if not isinstance(s, dict):
            continue
        pv = _num(_get(s, "roof-objects.solar-panel-photovoltaic.count"))
        seen += [_bool(_get(s, "roof-objects.solar.value")), (pv > 0) if pv is not None else None]
    if True in seen:
        return True
    return False if False in seen else None


def _type_count(p: dict, kind: str) -> int | None:
    """Objects of one pool type: 'pool.types.<kind>.count', or else enclosed + non-enclosed counts (the service fills
    one or the other depending on the survey). None when neither is a number."""
    n = _num(_get(p, f"pool.types.{kind}.count"))
    if n is not None:
        return int(n)
    parts = [_num(_get(p, f"pool.types.{kind}.{e}.count")) for e in ("enclosed", "non-enclosed")]
    return int(sum(x for x in parts if x is not None)) if any(x is not None for x in parts) else None


def _pool_count(p: dict) -> int | None:
    """Pools (in-ground + above-ground; hot tubs are counted separately)."""
    parts = [_type_count(p, k) for k in ("in-ground", "above-ground")]
    return sum(x for x in parts if x is not None) if any(x is not None for x in parts) else None


def _flag(p: dict, name: str) -> bool | None:
    if name == "pool":
        return _bool(_get(p, "pool.detected"))
    if name == "solar":
        return _solar(p)
    return _bool(_get(p, _FLAGS[name]))


def _per_year(data) -> list[tuple[str, dict]]:
    """[(capture date, properties)] newest year first, one result per year (the latest-dated one in that year).
    The service returns every survey it has (several per year, two layers) even when asked for the latest only."""
    best: dict[str, tuple[str, dict]] = {}
    for f in (data or {}).get("features") or []:
        p = (f or {}).get("properties") if isinstance(f, dict) else None
        date = _get(p, "metadata.ortho-date-estimate") if isinstance(p, dict) else None
        if not isinstance(date, str) or not re.match(r"\d{4}-\d\d-\d\d", date):
            continue
        date = date[:10]
        if date[:4] not in best or date > best[date[:4]][0]:
            best[date[:4]] = (date, p)
    return [best[y] for y in sorted(best, reverse=True)]


def _parse_property(data) -> dict | None:
    years = _per_year(data)
    if not years:
        return None
    captured, p = years[0]
    prev = years[1][1] if len(years) > 1 else None  # the latest earlier survey year present

    def stable(name: str, now: bool) -> bool:
        # Same yes/no in the newest year and the year before. With one year only (or no answer the year before)
        # we cannot tell, so this is False, not True.
        before = _flag(prev, name) if prev is not None else None
        return before is not None and before == now

    out: dict = {"captured": captured}
    present = _flag(p, "pool")
    if present is not None:
        pool = {"present": present, "confidence": None, "area_sqft": None}
        if present:
            score, area = _num(_get(p, "pool.score")), _num(_get(p, "pool.total-area"))  # area: sq ft (imperial)
            pool["confidence"] = round(min(1.0, max(0.0, score)), 2) if score is not None else None
            pool["area_sqft"] = int(round(area)) if area is not None else None
            kinds = [text for key, text in _POOL_KINDS if _get(p, f"pool.types.{key}.detected") is True]
            if kinds:
                pool["kind"] = ", ".join(kinds)
            n = _pool_count(p)
            if n is not None:
                pool["count"] = n
        pool["stable"] = stable("pool", present)
        out["pool"] = pool
    for name in ("hot_tub", "sport_court", "playground", "solar", "trampoline", "deck"):
        now = _flag(p, name)
        if now is not None:
            out[name] = {"present": now, "stable": stable(name, now)}
            if name == "hot_tub" and now and _type_count(p, "hot-tub") is not None:
                out[name]["count"] = _type_count(p, "hot-tub")
    count = _num(_get(p, "vehicles.count"))
    if count is not None:
        out["vehicles"] = int(count)
    elif _get(p, "vehicles.detected") is False:  # the service leaves the count empty when it saw none
        out["vehicles"] = 0
    tree = _num(_get(p, "parcel-tree-cover.percent"))
    if tree is not None:
        out["tree_cover_pct"] = round(tree, 1)
    n = _num(_get(p, "structure.count"))
    if n is not None:
        out["buildings"] = int(n)
    if len(out) == 1:  # a date and nothing else
        return None
    out["source"] = "Vexcel property data"
    # Not exposed on purpose: building type, footprint, stories, roof height. They flip between years or come back
    # empty on tall apartment buildings (live probe), so they would mislead.
    return out


def property_attributes(lat: float, lon: float) -> dict | None:
    """Outdoor features of the parcel at the point (pool, courts, solar, vehicles, tree cover) from Vexcel
    property/extract, newest survey year, each yes/no marked stable when the year before agrees.
    Counts: pool["count"] (in-ground + above-ground) and hot_tub["count"] when the service gives one, plus
    "vehicles" and "buildings". The service gives no count for sport courts, playgrounds or decks (yes/no only).
    None on failure, timeout or no data; never raises. Only successful answers are cached."""
    try:
        path = _dir(lat, lon) / f"property_v{PROPERTY_VERSION}.json"
        if cachectl.hit(path):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        raw = _call("GET", "property/extract", params={
            "wkt": _point(lat, lon), "srid": 4326, "layer": LAYERS, "latest": "true",
            "include": "precomputed", "unit-system": "imperial"}, timeout=PROPERTY_TIMEOUT_S)
        out = _parse_property(json.loads(raw))
        if out is not None:
            path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out
    except Exception:  # never raise; nothing cached
        return None


# ---------- feature_outlines (elements/detect) ----------
# property/extract has no geometry for pools, courts or solar (only the parcel and building footprints), so the boxes
# come from Vexcel elements/detect run on the same square and layers as the top-down photo of aerial_images.

OUTLINE_VERSION = 1
OUTLINE_TIMEOUT_S = 15       # live detection took 1.2-1.6 s in the probe
OUTLINE_HALF_M = 60          # same square as aerial_images' default top-down photo
OUTLINE_THRESHOLD = 0.5
_DETECT_LABELS = {"pool": "swimming pool", "sport_court": "tennis court", "solar": "solar panel"}
_SHOWN = {"pool": "Pool", "sport_court": "Sport court", "solar": "Solar panels"}


def _bbox_of_wkt(wkt) -> list[float] | None:
    """'POLYGON ((x y, ...))' -> [w, s, e, n]; None when it does not parse."""
    if not isinstance(wkt, str):
        return None
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", wkt)]
    if len(nums) < 4 or len(nums) % 2:
        return None
    xs, ys = nums[0::2], nums[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


def _parse_detect(data) -> list[dict]:
    back = {v: k for k, v in _DETECT_LABELS.items()}
    out = []
    for f in (data or {}).get("features") or []:
        props = (f or {}).get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict) or props.get("label") not in back:
            continue
        for det in props.get("detections") or []:
            box = _bbox_of_wkt((det or {}).get("bbox")) if isinstance(det, dict) else None
            if box is None:
                continue
            conf = _num(det.get("confidence"))
            out.append({"feature": back[props["label"]], "bbox": [round(v, 7) for v in box],
                        "confidence": round(conf, 2) if conf is not None else None})
    out.sort(key=lambda o: (o["feature"], -(o["confidence"] or 0)))
    return out


def feature_outlines(lat: float, lon: float) -> list[dict] | None:
    """Boxes around pools, sport courts and solar panels seen in the top-down photo square (2 x 60 m around the
    point; may include neighbouring parcels), from Vexcel elements/detect:
    [{"feature": "pool", "bbox": [w, s, e, n], "confidence": 0.81}, ...]. [] when nothing was found.
    None on failure or timeout; never raises. Only successful answers are cached."""
    try:
        path = _dir(lat, lon) / f"outlines_v{OUTLINE_VERSION}.json"
        if cachectl.hit(path):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        sq = _square(lat, lon, OUTLINE_HALF_M)
        raw = _call("POST", "elements/detect", body={
            "wkt": sq, "srid": 4326, "buffer-meter": 0,
            "images": [{"endpoint": "ortho/extract",
                        "request": {"layer": LAYERS, "srid": 4326, "wkt": sq, "crop": "bounding_box"}}],
            "model-input": {"elements": [{"label": l, "model": "sam3", "detection-threshold": OUTLINE_THRESHOLD}
                                         for l in _DETECT_LABELS.values()],
                            "include": ["bbox", "confidence"]}}, timeout=OUTLINE_TIMEOUT_S)
        data = json.loads(raw)
        if not isinstance(data, dict) or "features" not in data:
            return None
        out = _parse_detect(data)
        path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out
    except Exception:  # never raise; nothing cached
        return None


def _to_px(lat: float, lon: float, half_m: int, size: tuple[int, int], x: float, y: float) -> tuple[float, float]:
    """Map coordinates -> pixel in the top-down photo of the square _square(lat, lon, half_m) (north up)."""
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    w, n = lon - dlon, lat + dlat
    return (x - w) / (2 * dlon) * size[0], (n - y) / (2 * dlat) * size[1]


def draw_outlines(photo: Path, lat: float, lon: float, outlines: list[dict], out: Path,
                  half_m: int = OUTLINE_HALF_M) -> Path:
    """Thin box plus a short label ('Pool') per outline on a copy of the top-down photo."""
    from PIL import ImageDraw, ImageFont
    im = Image.open(photo).convert("RGB")
    dr = ImageDraw.Draw(im)
    lw = max(2, im.width // 500)
    try:
        font = ImageFont.load_default(size=max(12, im.width // 60))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    for o in outlines:
        w, s, e, n = o["bbox"]
        x0, y0 = _to_px(lat, lon, half_m, im.size, w, n)
        x1, y1 = _to_px(lat, lon, half_m, im.size, e, s)
        x0, x1 = sorted((max(0.0, min(im.width - 1.0, x0)), max(0.0, min(im.width - 1.0, x1))))
        y0, y1 = sorted((max(0.0, min(im.height - 1.0, y0)), max(0.0, min(im.height - 1.0, y1))))
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue  # wholly outside the photo
        dr.rectangle([x0, y0, x1, y1], outline=(255, 214, 0), width=lw)
        label = _SHOWN.get(o["feature"], o["feature"])
        th = dr.textbbox((0, 0), label, font=font)[3] + 6
        ty = y0 - th if y0 >= th else (y1 + 4 if y1 + th < im.height else y0 + 4)
        tb = dr.textbbox((x0 + 4, ty), label, font=font)
        dr.rectangle([tb[0] - 3, tb[1] - 3, tb[2] + 3, tb[3] + 3], fill=(0, 0, 0))
        dr.text((x0 + 4, ty), label, fill=(255, 214, 0), font=font)
    im.save(out, "JPEG", quality=85)
    return out


def outlined_image(lat: float, lon: float) -> dict | None:
    """The top-down Vexcel aerial photo with detected pools / sport courts / solar panels boxed and labelled, as an
    evidence image {"path", "caption"}. None when there is no photo, the detection failed or found nothing.
    Never raises."""
    try:
        outlines = feature_outlines(lat, lon)
        if not outlines:
            return None
        ev = aerial_images(lat, lon, OUTLINE_HALF_M)
        top = next((i for i in ev.get("images") or [] if (i.get("caption") or "").startswith("Top-down")), None)
        if ev.get("status") != "ok" or top is None or not Path(top["path"]).exists():
            return None
        out = _dir(lat, lon) / f"topdown_h{OUTLINE_HALF_M}_outlined_v{OUTLINE_VERSION}.jpg"
        if not cachectl.hit(out):
            draw_outlines(Path(top["path"]), lat, lon, outlines, out)
        names = []
        for o in outlines:
            name = _SHOWN.get(o["feature"], o["feature"]).lower()
            if name not in names:
                names.append(name)
        m = re.search(r"\((\d{4}-\d\d-\d\d)\)", top.get("caption") or "")
        return {"path": str(out),
                "caption": "Top-down Vexcel aerial photo" + (f" ({m.group(1)})" if m else "")
                           + ", detected " + ", ".join(names) + " outlined"}
    except Exception:
        return None
