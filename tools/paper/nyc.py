"""New York City: Department of City Planning PLUTO tax-lot table on NYC Open Data (keyless SODA)."""
from __future__ import annotations

import math
import re

from . import net

PLUTO = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
BBOX = (40.49, 40.92, -74.27, -73.68)  # the five boroughs
D = 0.0012  # search half-width in degrees (~130 m)


def in_area(lat: float, lon: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def _m(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)))


def _tok(street: str) -> str:
    """First street-name token, ordinals stripped: 'W 5th Ave' -> '5'."""
    t = [w for w in street.upper().split() if w not in ("N", "S", "E", "W", "EAST", "WEST")]
    m = re.match(r"^(\d+)(ST|ND|RD|TH)$", t[0]) if t else None
    return m.group(1) if m else (t[0] if t else "")


def pluto_at(lat: float, lon: float, house_no: str = "", street: str = "") -> tuple[dict | None, str]:
    """Tax lot near the point whose PLUTO address has the listing's house number and street,
    else a lot whose centroid is within 15 m of the point, else None (no guessing)."""
    params = {"$select": "bbl,address,numfloors,yearbuilt,unitsres,bldgarea,landuse,ownername,latitude,longitude",
              "$where": f"latitude between {lat - D} and {lat + D} and "
                        f"longitude between {lon - D} and {lon + D}",
              "$limit": 500}
    url, rows = net.get_json_url(PLUTO, params)
    rows = [r for r in rows if r.get("latitude") and r.get("longitude")]
    for r in rows:
        r["_m"] = _m(lat, lon, float(r["latitude"]), float(r["longitude"]))
    rows.sort(key=lambda r: r["_m"])
    tok = _tok(street)
    same = [r for r in rows if house_no and (r.get("address") or "").split(" ")[0] == house_no
            and (not tok or tok in (r.get("address") or "").upper().split()[1:])]
    if same:
        return same[0], url
    if rows and rows[0]["_m"] <= 15:
        return rows[0], url
    return None, url


LAND_USE = {"01": "One- and two-family homes", "02": "Multi-family walk-up buildings",
            "03": "Multi-family elevator buildings", "04": "Mixed residential and commercial",
            "05": "Commercial and office", "06": "Industrial and manufacturing", "07": "Transportation and utility",
            "08": "Public facilities and institutions", "09": "Open space and outdoor recreation",
            "10": "Parking facilities", "11": "Vacant land"}


def land_use(code) -> str | None:
    c = str(code or "").strip()
    return LAND_USE.get(c.zfill(2)) if c.isdigit() else None


def _n(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def facts(r: dict) -> dict:
    obs: dict = {}
    f = _n(r.get("numfloors"))
    if f > 0:
        obs["stories"] = int(f) if f == int(f) else f
    if _n(r.get("yearbuilt")) > 1700:
        obs["year_built"] = int(_n(r["yearbuilt"]))
    if _n(r.get("unitsres")) > 0:
        obs["unit_count"] = int(_n(r["unitsres"]))
    if _n(r.get("bldgarea")) > 0:
        obs["building_sf"] = int(_n(r["bldgarea"]))
    return obs
