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


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> bytes:
    """One rate-limited Vexcel call. Returns body bytes; raises _NoCoverage on 403/404, RuntimeError otherwise."""
    headers = {"X-API-KEY": rc_common.secret("VEXCEL_API_KEY")}
    r = None
    for attempt in range(3):
        _RATE.wait()
        CALLS["n"] += 1
        r = requests.request(method, f"{API}/{path}", headers=headers, params=params, json=body, timeout=120)
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
        return _cached_evidence(d / f"images_h{half_m}.json", lambda: _build_images(lat, lon, half_m, d))
    except Exception as ex:  # never raise
        return rc_common.evidence("aerial", "error", "Aerial photos could not be fetched.",
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
               "caption": "Top-down aerial photo" + (f" ({date})" if date else "")}]
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
                       "caption": f"Angled aerial view, camera looking {direction}" + (f" ({odate})" if odate else "")})
    n_obl = len(images) - 1
    summary = f"Top-down aerial photo and {n_obl} angled view{'s' if n_obl != 1 else ''} of the point."
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
        return _cached_evidence(d / "height.json", lambda: _build_height(lat, lon, d))
    except Exception as ex:  # never raise
        return rc_common.evidence("aerial", "error", "Roof height could not be measured.",
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
        return rc_common.evidence("aerial", "no_evidence", "The elevation model has no data at this point.",
                                  detail="Vexcel dsm/extract returned no valid pixels within 20 m.")
    p98 = float(np.percentile(v, 98))
    if p98 < 3.0:
        return rc_common.evidence("aerial", "no_evidence", "No raised roof found within 20 m of the point.",
                                  detail=f"98th percentile height within 20 m is {p98:.1f} m.")
    roof = round(float(np.median(v[v >= 0.6 * p98])), 1)
    stories, rng = stories_from_height(roof)
    date, coll = _capture_date(dsm_raw), _collection(dsm_raw)
    summary = f"Roof is {roof} m above the ground: about {stories} floors."
    detail = (f"Vexcel API v2 dsm/extract and dtm/extract, 80 m square. Roof level = median of heights at or above "
              f"60% of the 98th percentile within 20 m of the point ({ground_how}). Floors = 1 + round((height - 6.0 m) / 3.2 m) "
              f"(6.0 m = ~4.5 m ground floor + ~1.5 m parapet; range uses 3.3-3.0 m per floor). "
              f"Surface model {coll or 'unknown'}, captured {date or 'unknown'}.")
    return rc_common.evidence("aerial", "ok", summary,
                              {"height_m": roof, "stories": stories, "stories_range": rng, "capture_date": date},
                              detail=detail)
