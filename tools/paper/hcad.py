"""Harris County (Houston): HCAD parcel point query -> HCAD certified bulk roll (building data per account).

The live parcel layer has no building fields, so the building facts come from HCAD's public bulk
download Real_building_land.zip. Three members are read with HTTP Range requests, once:
building_other.txt (~7 MB compressed: commercial and apartment buildings), building_res.txt (~73 MB:
houses, townhomes, condos, duplexes to fourplexes) and fixtures.txt (~28 MB: '# Stories' STC rows for
other buildings, 'Story Height Index' STY rows for residential buildings, units). The result is cached
under rc_cache/paper/hcad/: a small JSON index for the other buildings and a sorted fixed-width binary
file for the ~1.3M residential accounts, searched in place (nothing is loaded, so a cold lookup is fast).
"""
from __future__ import annotations

import datetime
import json
import math
import mmap
import os
import re
import shutil
import struct
import threading
import zlib

from rc_common import cache_dir

from . import net

PARCELS = "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0/query"
ZIP_URL = "https://download.hcad.org/data/CAMA/{year}/Real_building_land.zip"
BBOX = (29.45, 30.20, -96.00, -94.85)  # lat_min, lat_max, lon_min, lon_max (Harris County, generous)
INDEX_VERSION = 2  # 2: residential accounts (building_res.txt year, fixtures STY stories) in res_<year>.bin
_lock = threading.Lock()
_FIELDS = ("HCAD_NUM,acct_num,site_str_pfx,site_str_num,site_str_name,site_str_sfx,site_zip,state_class,tax_year,"
           "owner_name_1,confidential_flag")


def in_area(lat: float, lon: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def parcel_at(lat: float, lon: float, house_no: str = "") -> tuple[dict | None, str]:
    """Return (parcel attributes or None, query URL). Of the owner fields only the first owner name (and the
    confidential flag) is requested, so the caller can tell a company from an individual; the caller drops the
    name at once and never stores it. No mailing address, no other owners.
    If the point misses a parcel or hits one with another house number (geocoded points often sit
    on the street), take a parcel within 40 m that has the listing's house number."""
    params = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false", "f": "json",
              "outFields": _FIELDS}
    url, d = net.get_json_url(PARCELS, params)
    hit = [f["attributes"] for f in d.get("features", [])]
    if hit and (not house_no or str(hit[0].get("site_str_num") or "").strip() == house_no):
        return hit[0], url
    if house_no:
        url2, d = net.get_json_url(PARCELS, {**params, "distance": 40, "units": "esriSRUnit_Meter"})
        same = [f["attributes"] for f in d.get("features", [])
                if str(f["attributes"].get("site_str_num") or "").strip() == house_no]
        if same:
            return same[0], url2
    return (hit[0] if hit else None), url


# ---------- address fallback (the map point found no parcel, or one with another house number) ----------

_DIRS = {"N": "N", "NORTH": "N", "S": "S", "SOUTH": "S", "E": "E", "EAST": "E", "W": "W", "WEST": "W",
         "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW"}
_SFX = {"AVE": "AVE", "AVENUE": "AVE", "AV": "AVE", "ST": "ST", "STREET": "ST", "DR": "DR", "DRIVE": "DR",
        "RD": "RD", "ROAD": "RD", "LN": "LN", "LANE": "LN", "BLVD": "BLVD", "BOULEVARD": "BLVD", "CT": "CT",
        "COURT": "CT", "CIR": "CIR", "CIRCLE": "CIR", "PL": "PL", "PLACE": "PL", "WAY": "WAY", "PKWY": "PKWY",
        "PARKWAY": "PKWY", "TRL": "TRL", "TRAIL": "TRL", "HWY": "HWY", "HIGHWAY": "HWY", "FWY": "FWY",
        "FREEWAY": "FWY", "LOOP": "LOOP", "TER": "TER", "TERRACE": "TER", "SQ": "SQ", "XING": "XING",
        "CV": "CV", "COVE": "CV", "BND": "BND", "BEND": "BND", "PT": "PT", "PATH": "PATH", "WALK": "WALK",
        "ROW": "ROW", "EXPY": "EXPY", "PLZ": "PLZ", "PLAZA": "PLZ", "GRN": "GRN", "HTS": "HTS", "PASS": "PASS"}


def split_street(street: str) -> tuple[str, str, str]:
    """'N Linden Grove Ave Apt 5' -> ('N', 'LINDEN GROVE', 'AVE'): direction prefix, bare name, type suffix."""
    s = re.split(r"\s(?:APT|UNIT|STE|SUITE)\b|#", " " + (street or "").upper())[0]
    words = re.sub(r"[^A-Z0-9 ]", " ", s).split()
    pfx = sfx = ""
    # a leading direction is a prefix unless it is the name itself ('North Fwy' is NORTH + FWY)
    if len(words) > 1 and words[0] in _DIRS and not (len(words) == 2 and words[1] in _SFX):
        pfx = _DIRS[words.pop(0)]
    if len(words) > 1 and words[-1] in _DIRS:
        words.pop()
    if len(words) > 1 and words[-1] in _SFX:
        sfx = _SFX[words.pop()]
    return pfx, " ".join(words), sfx


def _centroid(g) -> tuple[float, float] | None:
    pts = [p for ring in (g or {}).get("rings") or [] for p in ring]
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)) if pts else None


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def pick_by_address(features: list[dict], house_no: str, street: str,
                    zip5: str = "") -> tuple[dict | None, int]:
    """The single parcel whose site address has this house number and street (and zip, when both sides have one).
    Returns (attributes plus '_centroid', or None; number of distinct matching accounts). Several matching
    accounts -> None: never a guess between candidates."""
    pfx, name, sfx = split_street(street)
    if not house_no.isdigit() or not name:
        return None, 0
    found: dict[str, dict] = {}
    for f in features:
        a = f.get("attributes") or {}
        if str(a.get("site_str_num") or "").strip() != str(int(house_no)):
            continue
        # the roll keeps prefix, name and suffix in their own fields: no parsing on that side
        p2 = _DIRS.get(str(a.get("site_str_pfx") or "").strip().upper(), "")
        n2 = " ".join(re.sub(r"[^A-Z0-9 ]", " ", str(a.get("site_str_name") or "").upper()).split())
        s2 = str(a.get("site_str_sfx") or "").strip().upper()
        s2 = _SFX.get(s2, s2)
        names = {name, f"{pfx} {name}".strip()} | ({f"{name} {sfx}"} if sfx else set())
        if not ({n2, f"{p2} {n2}".strip()} & names) or (sfx and s2 and s2 != sfx) or (pfx and p2 and p2 != pfx):
            continue
        z = str(a.get("site_zip") or "").strip()[:5]
        if zip5 and z and z != zip5:
            continue
        acct = str(a.get("HCAD_NUM") or a.get("acct_num") or "").strip()
        if acct:
            found.setdefault(acct, {**a, "_centroid": _centroid(f.get("geometry"))})
    return (next(iter(found.values())) if len(found) == 1 else None), len(found)


def parcel_by_address(house_no: str, street: str, zip5: str = "", lat: float | None = None,
                      lon: float | None = None, max_m: float = 1000) -> tuple[dict | None, str, int]:
    """Attribute query on the HCAD parcel layer by house number + street name. Returns (attributes or None,
    query URL, distinct matching accounts). Accepted only when exactly one account has the same house number and
    street (and zip, when both have one), and its parcel lies within max_m of the map point (when one is given)."""
    _, name, _ = split_street(street)
    if not house_no.isdigit() or not name:
        return None, "", 0
    where = f"site_str_num = {int(house_no)} AND site_str_name LIKE '{name.replace(chr(39), chr(39) * 2)}%'"
    url, d = net.get_json_url(PARCELS, {"where": where, "returnGeometry": "true", "outSR": 4326, "f": "json",
                                        "outFields": _FIELDS})
    hit, n = pick_by_address(d.get("features") or [], house_no, street, zip5)
    if hit is None:
        return None, url, n
    c = hit.pop("_centroid", None)
    if lat is not None and lon is not None and (c is None or _metres(c, (lat, lon)) > max_m):
        return None, url, n
    return hit, url, n


# HCAD state class (Texas property category) -> plain land use words. Unlisted codes: no land use is stated.
STATE_CLASS = {
    "A1": "Single-family homes", "A2": "Mobile homes", "A3": "Residential outbuildings", "A4": "Condominiums",
    "B1": "Apartments (multi-family)", "B2": "Duplex", "B3": "Triplex", "B4": "Fourplex",
    "C1": "Vacant lot", "C2": "Vacant commercial lot", "C3": "Vacant rural land",
    "D1": "Farm or ranch land", "D2": "Farm or ranch buildings", "E1": "Farm or ranch home",
    "F1": "Commercial", "F2": "Industrial", "J1": "Utility (water)", "J2": "Utility (gas)",
    "J3": "Utility (electric)", "J4": "Utility (telephone)", "J6": "Utility (pipeline)", "J7": "Utility (cable)",
    "O1": "Residential inventory", "X1": "Tax-exempt",
}


def land_use(state_class) -> str | None:
    """'B1' -> 'Apartments (multi-family)'; X-something -> 'Tax-exempt'; unknown -> None."""
    code = str(state_class or "").strip().upper()
    if not code:
        return None
    if code in STATE_CLASS:
        return STATE_CLASS[code]
    if code.startswith("X"):
        return "Tax-exempt"
    return STATE_CLASS.get(code[:2])


# ---------- bulk roll index ----------

def _zip_members(url: str) -> dict[str, tuple[int, int]]:
    """name -> (local header offset, compressed size), read from the zip's central directory."""
    size = net.head_size(url)
    tail = net.get(url, headers={"Range": f"bytes={max(0, size - 65536)}-{size - 1}"}).content
    e = tail.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack("<II", tail[e + 12:e + 20])
    cd = net.get(url, headers={"Range": f"bytes={cd_off}-{cd_off + cd_size - 1}"}).content
    out, p = {}, 0
    while cd[p:p + 4] == b"PK\x01\x02":
        csize = struct.unpack("<I", cd[p + 20:p + 24])[0]
        n, m, k = struct.unpack("<HHH", cd[p + 28:p + 34])
        off = struct.unpack("<I", cd[p + 42:p + 46])[0]
        out[cd[p + 46:p + 46 + n].decode()] = (off, csize)
        p += 46 + n + m + k
    return out


def _member_lines(url: str, off: int, csize: int):
    """Stream one deflated zip member over HTTP Range and yield its text lines."""
    lh = net.get(url, headers={"Range": f"bytes={off}-{off + 29}"}).content
    n, m = struct.unpack("<HH", lh[26:30])
    start = off + 30 + n + m
    r = net.get(url, headers={"Range": f"bytes={start}-{start + csize - 1}"}, stream=True)
    dec, buf = zlib.decompressobj(-15), b""
    for chunk in r.iter_content(1 << 20):
        buf += dec.decompress(chunk)
        lines = buf.split(b"\n")
        buf = lines.pop()
        for line in lines:
            yield line.decode("latin-1").rstrip("\r")
    buf += dec.flush()
    if buf:
        yield buf.decode("latin-1").rstrip("\r")


def _num(s: str) -> float:
    try:
        return float(s.strip() or 0)
    except ValueError:
        return 0.0


# residential record: account (int), year built (0 = none), stories x2 (1.5 stories -> 3), property use code
_RES = struct.Struct("<QHB2s")
_RES_MAGIC = b"HCADRES" + bytes([INDEX_VERSION])
# Floors from STY only where the record is a whole house or a small multi-family building. For condo units (Z*,
# A4) STY is the unit's own story count, not the building's, so it is not given as the building's floors.
STORY_USES = {"A1", "A2", "B2", "B3", "B4", "E1"}
# Units only where the building type itself fixes the count (single-family = 1, duplex = 2, ...); never guessed.
UNITS_BY_USE = {"A1": 1, "B2": 2, "B3": 3, "B4": 4}


def build_from_lines(other, res, fixtures) -> tuple[dict, dict]:
    """Parse the three tab-separated members (iterables of text lines, header first). Returns (other-building
    index {acct: {...}}, residential {acct int: (year, stories x2, use code)}) for the main (largest) building."""
    idx: dict[str, dict] = {}
    # building_other.txt: commercial / apartment buildings, one row per building
    rows = iter(other)
    cols = next(rows).split("\t")
    a, b, de, sf, un = (cols.index(c) for c in ("acct", "bld_num", "date_erected", "im_sq_ft", "units"))
    for line in rows:
        f = line.split("\t")
        if len(f) <= max(a, b, de, sf, un):
            continue
        r = idx.setdefault(f[a].strip(), {})
        r.setdefault("blds", []).append([int(_num(f[de])), int(_num(f[sf])), int(_num(f[un]))])
    # building_res.txt: one row per residential building; the largest (im_sq_ft) is the main one
    main: dict[int, list] = {}  # acct -> [year, sq ft, bld_num, use, STY x2 of main building, max STY x2]
    rows = iter(res)
    cols = next(rows).split("\t")
    a, b, de, sf, pu = (cols.index(c) for c in ("acct", "bld_num", "date_erected", "im_sq_ft", "property_use_cd"))
    for line in rows:
        f = line.split("\t")
        if len(f) <= max(a, b, de, sf, pu):
            continue
        k = f[a].strip()
        if not k.isdigit():
            continue
        year, sqft, cur = int(_num(f[de])), int(_num(f[sf])), main.get(int(k))
        if cur is None:
            main[int(k)] = [year, sqft, f[b].strip(), f[pu].strip()[:2], 0, 0]
        elif sqft > cur[1]:
            cur[:4] = [year, sqft, f[b].strip(), f[pu].strip()[:2]]
    # fixtures.txt: STC = '# Stories' and UNT = units (other buildings); STY = Story Height Index (residential)
    rows = iter(fixtures)
    cols = next(rows).split("\t")
    a, b, t, u = (cols.index(c) for c in ("acct", "bld_num", "type", "units"))
    for line in rows:
        f = line.split("\t")
        if len(f) <= max(a, b, t, u):
            continue
        typ = f[t].strip()
        if typ in ("STC", "UNT"):
            r = idx.setdefault(f[a].strip(), {})
            r.setdefault(typ, {})[f[b].strip()] = _num(f[u])
        elif typ == "STY":
            k = f[a].strip()
            m = main.get(int(k)) if k.isdigit() else None
            x2 = int(round(_num(f[u]) * 2))
            if m is None or not 0 < x2 <= 40:  # 0 or > 20 stories is not a house record
                continue
            m[5] = max(m[5], x2)
            if f[b].strip() == m[2]:
                m[4] = x2
    resd = {k: (v[0] if 1800 < v[0] < 65535 else 0, v[4] or v[5], v[3]) for k, v in main.items()}
    return idx, resd


def write_res(path, resd: dict) -> None:
    """Sorted fixed-width records, searched in place by res_lookup."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(_RES_MAGIC + struct.pack("<I", len(resd)))
        fh.write(b"".join(_RES.pack(k, y, s, u.encode("ascii", "replace")[:2].ljust(2))
                          for k, (y, s, u) in sorted(resd.items())))
    os.replace(tmp, path)


def res_lookup(path, acct: str) -> tuple[int, float, str] | None:
    """(year built or 0, stories or 0, property use code) of a residential account, by binary search on disk."""
    k = (acct or "").strip()
    if not k.isdigit() or path is None or not path.exists():
        return None
    k, hdr = int(k), len(_RES_MAGIC) + 4
    with open(path, "rb") as fh:
        if fh.read(len(_RES_MAGIC)) != _RES_MAGIC:
            return None
        n = struct.unpack("<I", fh.read(4))[0]
        if n == 0:
            return None
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                a, y, s, u = _RES.unpack_from(mm, hdr + mid * _RES.size)
                if a == k:
                    return y, s / 2, u.decode("ascii", "replace").strip()
                lo, hi = (mid + 1, hi) if a < k else (lo, mid)
    return None


def _build_index(url: str) -> tuple[dict, dict]:
    members = _zip_members(url)
    return build_from_lines(*(_member_lines(url, *members[m])
                              for m in ("building_other.txt", "building_res.txt", "fixtures.txt")))


_loaded: dict = {}


def _read(p) -> dict:
    """The JSON index, parsed once per file version (path, mtime)."""
    key = (str(p), p.stat().st_mtime_ns)
    if _loaded.get("key") != key:
        _loaded.update(key=key, idx=json.loads(p.read_text(encoding="utf-8")))
    return _loaded["idx"]


def _current(d, year: int) -> bool:
    p, rp = d / f"index_{year}.json", d / f"res_{year}.bin"
    if not (p.exists() and rp.exists()):
        return False
    with open(rp, "rb") as fh:
        if fh.read(len(_RES_MAGIC)) != _RES_MAGIC:
            return False
    return _read(p).get("_v") == INDEX_VERSION


def _index() -> tuple[dict, str, object]:
    """Load (or build once) the index of the newest published roll year: (other-building index, bulk zip URL,
    residential file path). An index of an older INDEX_VERSION is copied to a dated backup beside it and rebuilt;
    if the rebuild fails, the old index is used as it is."""
    with _lock:
        d = cache_dir("paper", "hcad")
        this_year = datetime.date.today().year
        for year in (this_year, this_year - 1):
            if _current(d, year):
                return _read(d / f"index_{year}.json"), ZIP_URL.format(year=year), d / f"res_{year}.bin"
        last = None
        for year in (this_year, this_year - 1):
            url = ZIP_URL.format(year=year)
            try:
                idx, resd = _build_index(url)
            except Exception as e:  # year not published yet -> try previous
                last = e
                continue
            p = d / f"index_{year}.json"
            if p.exists():  # never thrown away: the old build stays beside the new one
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
                shutil.copy2(p, d / f"index_{year}_backup_{stamp}.json")
            write_res(d / f"res_{year}.bin", resd)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({**idx, "_v": INDEX_VERSION}, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, p)
            return _read(p), url, d / f"res_{year}.bin"
        for year in (this_year, this_year - 1):  # stale but present: better than nothing
            p = d / f"index_{year}.json"
            if p.exists():
                return _read(p), ZIP_URL.format(year=year), d / f"res_{year}.bin"
        raise RuntimeError(f"HCAD bulk roll unavailable: {last}")


def building_facts(acct: str, state: tuple | None = None) -> tuple[dict, str]:
    """Return (observed facts for this account, bulk zip URL). Only fields HCAD publishes.
    Other buildings (building_other.txt) come first. A residential account (building_res.txt) gives year built,
    floors (fixtures STY of the main building; houses and duplex-to-fourplex only, not condo units) and a unit
    count only where the building type fixes it; its property use code is kept as building_use."""
    idx, url, res_path = state or _index()
    r = idx.get(acct.strip()) or {}
    obs: dict = {}
    if r.get("STC"):
        obs["stories"] = int(max(r["STC"].values()))
    blds = r.get("blds") or []
    if blds:
        main = max(blds, key=lambda x: x[1])
        if main[0] > 1800:
            obs["year_built"] = main[0]
        sf = sum(x[1] for x in blds)
        if sf > 0:
            obs["building_sf"] = sf
        units = max(x[2] for x in blds)  # building_other.units is per account, repeated per row
        if units > 0:
            obs["unit_count"] = units
    if "unit_count" not in obs and r.get("UNT"):
        n = int(sum(r["UNT"].values()))
        if n > 0:
            obs["unit_count"] = n
    res = None if blds else res_lookup(res_path, acct)
    if res:
        year, sty, use = res
        if year > 1800:
            obs["year_built"] = year
        if "stories" not in obs and sty > 0 and use in STORY_USES:
            obs["stories"] = int(sty) if sty == int(sty) else sty
        if "unit_count" not in obs and use in UNITS_BY_USE:
            obs["unit_count"] = UNITS_BY_USE[use]
        if obs:
            obs["building_use"] = use
    return obs, url
