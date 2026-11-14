"""Houston 311: service-request COUNTS near a point, by category, from the City of Houston's public extract.

The city publishes every 311 case of the year so far as one pipe-delimited file (keyless, ~270 MB). It is read
once, offline (`python -m tools.paper.h311`), into a small index under rc_cache/paper/h311/: for each spot
(lat/lon rounded to ~10 m) the number of cases per case type. Nothing else is kept: no case numbers, addresses,
names, phones, descriptions or notes (311 free text can carry callers' names and numbers).

At run time counts_near(lat, lon) reads that index (one file, cached in memory); no index = None, never a
network call during a check.
"""
from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path

from rc_common import cache_dir

from . import hcad, net

URL = "https://hfdapp.houstontx.gov/311/311-CRIS-Public-Data-Extract-D365-YTD-compressed-{year}.txt"
INDEX = "index.json"
RADIUS_M = 100   # "near the building": a large complex is ~100-200 m across
TOP_TYPES = 5    # categories kept per lookup (the rest are in the total)
_lock = threading.Lock()
_mem: dict = {}


def index_path() -> Path:
    return cache_dir("paper", "h311") / INDEX


def _date(s: str) -> str | None:
    m = re.match(r"\s*(20\d\d)-(\d\d)-(\d\d)", s or "")
    if m:
        return "-".join(m.groups())
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(20\d\d)", s or "")
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def build_from_lines(lines, source_url: str = "") -> dict:
    """Index from the extract's text lines (header line first). Counts only: case type per ~10 m spot."""
    ix = None
    types: list[str] = []
    tix: dict[str, int] = {}
    tiles: dict[str, dict[str, dict[int, int]]] = {}
    since = until = None
    rows = 0
    for line in lines:
        if ix is None:
            if line.startswith("365 Case Number"):
                cols = [c.strip() for c in line.split("|")]
                ix = [cols.index(c) for c in ("Latitude", "Longitude", "Created Date Local", "Incident Case Type")]
            continue
        f = line.split("|")
        if len(f) <= max(ix) or not f[0].strip().isdigit():
            continue  # a description's continuation line, or a broken row
        try:
            lat, lon = float(f[ix[0]]), float(f[ix[1]])
        except ValueError:
            continue
        day, typ = _date(f[ix[2]]), " ".join(f[ix[3]].split())
        if not day or not typ or len(typ) > 60 or not hcad.in_area(lat, lon):
            continue
        rows += 1
        since, until = min(since or day, day), max(until or day, day)
        if typ not in tix:
            tix[typ] = len(types)
            types.append(typ)
        tile = tiles.setdefault(_tile(math.floor(lat * 1000), math.floor(lon * 1000)), {})
        spot = tile.setdefault(f"{lat:.4f},{lon:.4f}", {})
        spot[tix[typ]] = spot.get(tix[typ], 0) + 1
    return {"version": 1, "source_url": source_url, "since": since, "until": until, "rows": rows,
            "types": types, "tiles": tiles}


def build(year: int | None = None) -> Path:
    """Stream the year's extract once and write the counts index. Offline only (one large download)."""
    import datetime
    year = year or datetime.date.today().year
    url = URL.format(year=year)
    r = net.get(url, stream=True)

    def lines():
        for raw in r.iter_lines():
            yield raw.decode("utf-8", "replace")
    idx = build_from_lines(lines(), url)
    p = index_path()
    p.write_text(json.dumps(idx, separators=(",", ":")), encoding="utf-8")
    return p


def _tile(a: int, b: int) -> str:
    """Tile key of the 0.001-degree cell whose corner is (a/1000, b/1000)."""
    return f"{a},{b}"


def _index() -> dict | None:
    with _lock:
        if "idx" in _mem:
            return _mem["idx"]
        p = index_path()
        try:
            idx = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            idx = None
        _mem["idx"] = idx
        return idx


def _m(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)))


def counts_near(lat: float, lon: float, radius_m: int = RADIUS_M, idx: dict | None = None) -> dict | None:
    """{"total", "by_type": {case type: n} (top TOP_TYPES), "since", "until", "radius_m", "source_url"} for the
    311 cases within radius_m of the point; None when no index is built or the point is outside Houston's area.
    A total of 0 is returned as such (the extract covers the whole city: no cases nearby is a real count)."""
    idx = idx if idx is not None else _index()
    if not idx or lat is None or lon is None or not hcad.in_area(lat, lon):
        return None
    types, tiles = idx.get("types") or [], idx.get("tiles") or {}
    dlat = radius_m / 111_320 + 0.0006
    dlon = radius_m / (111_320 * math.cos(math.radians(lat))) + 0.0006
    by: dict[str, int] = {}
    la0, la1 = math.floor((lat - dlat) * 1000), math.floor((lat + dlat) * 1000)
    lo0, lo1 = math.floor((lon - dlon) * 1000), math.floor((lon + dlon) * 1000)
    for a in range(la0, la1 + 1):
        for b in range(lo0, lo1 + 1):
            tile = tiles.get(_tile(a, b))
            for spot, cnt in (tile or {}).items():
                s_lat, s_lon = (float(x) for x in spot.split(","))
                if _m(lat, lon, s_lat, s_lon) > radius_m:
                    continue
                for t, n in cnt.items():
                    name = types[int(t)] if int(t) < len(types) else None
                    if name:
                        by[name] = by.get(name, 0) + int(n)
    top = dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_TYPES])
    return {"total": sum(by.values()), "by_type": top, "since": idx.get("since"), "until": idx.get("until"),
            "radius_m": radius_m, "source_url": idx.get("source_url") or ""}


def main() -> None:
    p = build()
    idx = json.loads(p.read_text(encoding="utf-8"))
    print(f"wrote Houston 311 counts index: {idx['rows']} cases, {idx['since']} to {idx['until']}")


if __name__ == "__main__":
    main()
