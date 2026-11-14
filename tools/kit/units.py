"""Per-unit lookup: the kit's unit rows (unit number, beds, baths, sq ft, floor plan, own 3D scan), one small JSON
file per building under cache_dir("kit", "units") (outside the repo), read on demand.

The address index (build_index.py) keeps floor plans only and folds unit scans into the plan's scans, so the unit
itself is lost there. This store keeps it, without touching the index (whose cold load must stay fast).

File shape (columnar, to stay small): {"version", "property_id", "cols": [...], "rows": [[...], ...],
"titled": [[scan_id, title], ...]}; `titled` are the building's own scans (property- or plan-level) whose title
names a unit ("2BR, 2BA - 966SF - Unit 503", "#214"). A building with neither unit rows nor unit-titled scans has
no file.

Run once: python -m tools.kit.units   (reads the zip; about a minute)
At run time (never builds, never networks):
  units_for(pid) -> [unit dict]          find_unit(pid, unit, beds=None, baths=None) -> unit dict | None
  unit_scan(pid, unit) -> scan id | None  unit_from_text(text) -> unit text | None
A unit dict: {"unit", "beds", "baths", "sqft", "plan", "plan_id", "scan_ids", "building"}; `plan` is the record
plan's name exactly as in the index's floorplans[].name, `plan_id` the record plan (model) id as in kit facts
plans[].id, `building` the kit's sub-building name in multi-building complexes (else None).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path

import rc_common

UNITS_VERSION = 1
COLS = ["unit", "beds", "baths", "sqft", "plan", "plan_id", "scan_ids", "building"]
TITLE_MAX = 120
MEM_MAX = 64  # buildings kept in memory

# ---------- unit text ----------

_KEYWORD = r"(?<![a-z])(?:unit|apt|apartment|suite|ste|residence)(?![a-z])\.?"
_TOKEN = r"(\d{1,3}-\d{1,4}|[a-z]{0,2}-?\d{1,5}(?:-?[a-z])?)"
# what may not follow a unit number: more of a word or number, a decimal, a range, or a bed/bath/size/count word
_AFTER = (r"(?![\w])(?![.,-]\d)(?!\s*(?:bed|br\b|bd\b|bdrm|bath|ba\b|sq|sf\b|square|x\s*\d|stor|level|floor|"
          r"units?\b|homes?\b|best\b|rated\b|in\s+[a-z]))")
_KEYED = re.compile(_KEYWORD + r"[\s#:._=/-]{0,3}" + _TOKEN + _AFTER, re.I)
_HASH = re.compile(r"(?:^|(?<=[\s,(\[]))#\s?" + _TOKEN + _AFTER, re.I)
_PREFIX = re.compile(r"^\s*(?:(?:unit|apt|apartment|suite|ste|residence|no|number)(?![a-z])\.?|#)\s*[:#.]?\s*", re.I)


def norm_unit(s) -> str | None:
    """Comparable unit key: '#1204', 'Unit 1204', 'Apt 01204' -> '1204'; 'A-0321' -> 'a321'; case-blind."""
    t = str(s if s is not None else "").strip()
    for _ in range(3):
        t2 = _PREFIX.sub("", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"[^a-z0-9]", "", t.lower())
    t = re.sub(r"(?<!\d)0+(?=\d)", "", t)
    return t or None


def unit_from_text(text: str) -> str | None:
    """The unit number an address line, page title or URL names: '2111 Linden Grove Ave Apt 1204' -> '1204',
    '.../unit-1204' -> '1204', 'Suite 4B' -> '4B', '#1204' -> '1204'. None for plan names ('Plan B2', 'A1 - 1 Bed'),
    bare street numbers, counts ('2 units'), and anything unclear (two different units named)."""
    t = str(text or "")
    if not t.strip():
        return None
    found = [m.group(1) for m in _KEYED.finditer(t)] + [m.group(1) for m in _HASH.finditer(t)]
    found = [f for f in found if re.search(r"\d", f)]
    keys = {norm_unit(f) for f in found}
    if len(keys) != 1:
        return None
    return found[0].upper()


# ---------- build (offline, once) ----------

def units_dir() -> Path:
    return rc_common.cache_dir("kit", "units")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _pos(v):
    n = _num(v)
    return n if n is not None and n > 0 else None


def _clean(s):
    return " ".join(str(s).split()) if s else None


def _scans(attachments) -> list[tuple[str, str | None]]:
    from tools.kit.build_index import MATTERPORT_TYPES
    out = []
    for a in attachments or []:
        k = a.get("videoKey")
        if a.get("videoExternalReferenceType") in MATTERPORT_TYPES and k and k not in [x for x, _ in out]:
            out.append((k, _clean(a.get("caption"))))
    return out


def _row(u: dict, m: dict | None) -> list:
    return [_clean(u.get("unitNumber")) or _clean(u.get("unitName")), _num(u.get("numBeds")),
            _pos(u.get("numBaths")), _pos(u.get("minSquareFeet")),
            _clean(m.get("modelName")) if m else None, m.get("id") if m else None,
            [k for k, _ in _scans(u.get("attachments"))], _clean(u.get("buildingName"))]


def extract(r: dict) -> dict | None:
    """One commercial-property record -> the building's unit file, or None when it has no unit rows and no
    unit-titled scans."""
    apt = r.get("propertyApartment") or {}
    rows = [_row(u, m) for m in apt.get("models") or [] for u in m.get("units") or []]
    rows += [_row(u, None) for u in apt.get("units") or []]
    rows = [x for x in rows if x[0]]
    titled, seen = [], set()
    cand = _scans(r.get("attachments")) + [s for m in apt.get("models") or [] for s in _scans(m.get("attachments"))]
    for k, cap in cand:
        if cap and k not in seen and unit_from_text(cap):
            seen.add(k)
            titled.append([k, cap[:TITLE_MAX]])
    if not rows and not titled:
        return None
    return {"version": UNITS_VERSION, "property_id": str(r["id"]), "cols": COLS, "rows": rows, "titled": titled}


def build() -> dict:
    """Write the whole store into a fresh folder, then swap it in; an existing store is kept beside it as a dated
    backup (never deleted)."""
    import pyzipper
    from tools.kit.build_index import _rows, kit_zip_path
    z = pyzipper.AESZipFile(kit_zip_path())
    z.setpassword(rc_common.secret("COSTAR_ZIP_PASSWORD").encode())
    final = units_dir()
    tmp = final.with_name("units.tmp")
    if tmp.exists():
        tmp.rename(final.with_name(f"units.tmp-old-{int(time.time())}"))
    tmp.mkdir(parents=True)
    n = rows = 0
    for r in _rows(z, "data/commercial-property.jsonl"):
        f = extract(r)
        if f:
            (tmp / f"{f['property_id']}.json").write_text(json.dumps(f, separators=(",", ":")), encoding="utf-8")
            n += 1
            rows += len(f["rows"])
    (tmp / "_version.json").write_text(json.dumps({"version": UNITS_VERSION, "buildings": n, "rows": rows}),
                                       encoding="utf-8")
    if any(final.iterdir()):
        final.rename(final.with_name(f"units.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    else:
        shutil.rmtree(final)  # the empty folder cache_dir just made
    tmp.rename(final)
    with _lock:
        _mem.clear()
    return {"buildings": n, "rows": rows}


# ---------- run time ----------

_lock = threading.Lock()
_mem: OrderedDict[str, dict | None] = OrderedDict()


def store_ready() -> bool:
    """True once the unit store has been built (else every lookup is empty / None)."""
    try:
        v = json.loads((units_dir() / "_version.json").read_text(encoding="utf-8"))
        return v.get("version") == UNITS_VERSION
    except Exception:
        return False


def _load(property_id) -> dict | None:
    """{"units": [unit dict], "by_key": {norm: [unit dict]}, "titled": [(scan_id, title, norm)]} or None."""
    pid = str(property_id if property_id is not None else "").strip()
    if not re.fullmatch(r"\d{1,12}", pid):
        return None
    with _lock:
        if pid in _mem:
            _mem.move_to_end(pid)
            return _mem[pid]
    out = None
    try:
        p = units_dir() / f"{pid}.json"
        f = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        if f and f.get("version") == UNITS_VERSION:
            cols = f.get("cols") or COLS
            units = [dict(zip(cols, r)) for r in f.get("rows") or []]
            by_key: dict[str, list] = {}
            for u in units:
                u.setdefault("building", None)
                u["scan_ids"] = list(u.get("scan_ids") or [])
                k = norm_unit(u.get("unit"))
                if k:
                    by_key.setdefault(k, []).append(u)
            titled = [(s, t, norm_unit(unit_from_text(t))) for s, t in f.get("titled") or []]
            out = {"units": units, "by_key": by_key, "titled": titled}
    except Exception:
        out = None
    with _lock:
        _mem[pid] = out
        while len(_mem) > MEM_MAX:
            _mem.popitem(last=False)
    return out


def _copy(u: dict) -> dict:
    return {**u, "scan_ids": list(u.get("scan_ids") or [])}


def units_for(property_id) -> list[dict]:
    """Every unit row the kit has for this building, [] when none (or the store is not built)."""
    d = _load(property_id)
    return [_copy(u) for u in d["units"]] if d else []


FACT_KEYS = ("beds", "baths", "sqft")


def _agree(rows: list[dict], keys) -> bool:
    return all(u.get(k) == rows[0].get(k) for u in rows[1:] for k in keys)


def _fit(u: dict, beds, baths) -> bool:
    for k, want in (("beds", beds), ("baths", baths)):
        if want is not None and u.get(k) is not None and float(u[k]) != float(want):
            return False
    return True


def find_unit(property_id, unit: str, beds=None, baths=None) -> dict | None:
    """The one kit unit this unit text names, or None unless exactly one fits.

    When the number repeats in the building (multi-building complexes, a plan listed twice), beds/baths choose.
    Rows left that agree on beds, baths and sq ft are one answer: the kit often lists the same unit under two
    plan records ('2 Bedrooms' twice, or one plan per income tier). Then plan / plan_id / building are kept only
    when every row has the same value (else None), scan_ids only when identical (else []).
    beds/baths only choose among repeats: a single row is returned even if they disagree (that is a finding)."""
    d, key = _load(property_id), norm_unit(unit)
    if not d or not key:
        return None
    rows = d["by_key"].get(key) or []
    if len(rows) > 1:
        rows = [u for u in rows if _fit(u, _num(beds), _num(baths))]
    if not rows or not _agree(rows, FACT_KEYS):
        return None
    out = _copy(rows[0])
    for k, empty in (("plan", None), ("plan_id", None), ("building", None), ("scan_ids", [])):
        if not _agree(rows, (k,)):
            out[k] = empty
    return {**out, "ambiguous": False}


def unit_scan(property_id, unit: str) -> str | None:
    """The 3D scan of this very unit: the unit row's own scan, else a building scan whose title names the unit,
    else None. None too when the number names different homes (rows differ in beds, baths, sq ft or
    sub-building), the unit has several own scans, or two titled scans name it."""
    d, key = _load(property_id), norm_unit(unit)
    if not d or not key:
        return None
    rows = d["by_key"].get(key) or []
    if rows and not _agree(rows, FACT_KEYS + ("building",)):
        return None  # different homes share the number: use find_unit(beds, baths) and its scan_ids
    own = {s for u in rows for s in u.get("scan_ids") or []}
    if own:
        return own.pop() if len(own) == 1 else None
    titled = {s for s, _, k in d["titled"] if k == key}
    return titled.pop() if len(titled) == 1 else None


def unit_scan_title(property_id, scan_id: str) -> str | None:
    """The kit title of a unit-titled building scan (as stored), else None."""
    d = _load(property_id)
    return next((t for s, t, _ in (d or {}).get("titled") or [] if s == scan_id), None)


def main() -> None:
    t = time.perf_counter()
    r = build()
    print(f"wrote unit files for {r['buildings']} buildings, {r['rows']} unit rows in {time.perf_counter() - t:.0f}s")


if __name__ == "__main__":
    main()
