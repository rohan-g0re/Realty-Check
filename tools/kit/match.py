"""match_address and record_evidence (docs/CONTRACT.md sections 2.3, 2.4, 3)."""
from __future__ import annotations

import json
import math
import re

import rc_common
from tools.kit.normalize import abbrev_hits, parse, street_key
from tools.kit.plan_match import plan_types

NEAR_M = 60.0
_IDX: dict | None = None


def _load() -> dict:
    """Index plus lookup tables, loaded once per process; built from the kit zip if missing."""
    global _IDX
    if _IDX is None:
        from tools.kit.build_index import INDEX_VERSION, index_path, write_index
        path = index_path()
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if not data or data.get("version") != INDEX_VERSION:
            write_index()
            data = json.loads(path.read_text(encoding="utf-8"))
        by_street: dict[str, list] = {}
        cities = set()
        for p in data["properties"]:
            for k in p["keys"]:
                k["street"] = street_key(k["street_name"])
                by_street.setdefault(k["street"], []).append((p, k))
                if k["city"]:
                    cities.add(k["city"])
        _IDX = {"props": data["properties"], "by_street": by_street, "cities": sorted(cities)}
    return _IDX


def _meters(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def _no_match(how: str, lat=None, lon=None) -> dict:
    return {"status": "no_match", "confidence": 0.0, "how": how, "lat": lat, "lon": lon}


def _matched(p: dict, confidence: float, how: str) -> dict:
    return {"status": "matched", "confidence": round(confidence, 2), "property_id": p["property_id"],
            "kind": p["kind"], "name": p["name"], "address": p["address"], "city": p["city"], "state": p["state"],
            "lat": p["lat"], "lon": p["lon"], "record": dict(p["record"]),
            "floorplans": [dict(f) for f in p["floorplans"]],
            "matterport_model_ids": list(p["matterport_model_ids"]), "how": how,
            "plan_types": plan_types(p["floorplans"])}


def _pick(hits: list, q: dict, lat, lon):
    """One property from address hits, or (None, reason) when it would be a guess."""
    props = {p["property_id"] + p["kind"]: (p, k) for p, k in hits}
    cands = [pk for pk in props.values() if pk[0]["kind"] == "building"] or list(props.values())
    if len(cands) > 1:  # prefer an exact street number over a number range
        exact = [pk for pk in cands if pk[1]["num_lo"] == q["num_lo"] == pk[1]["num_hi"]]
        cands = exact or cands
    if cands and cands[0][0]["kind"] == "home":
        if q["unit"]:  # a home listing is one unit: the unit must agree
            cands = [pk for pk in cands if str(pk[0].get("unit") or "").lower() == q["unit"]]
            if not cands:
                return None, f"no kit home listing for unit {q['unit']} at this address"
        if len({str(pk[0].get("unit") or "").lower() for pk in cands}) == 1:
            best = _best_home([pk[0] for pk in cands])
            cands = [pk for pk in cands if pk[0] is best][:1]
    if len(cands) > 1:  # a rental listing points at the property that has floor plans
        cands = [pk for pk in cands if pk[0]["floorplans"]] if sum(bool(pk[0]["floorplans"]) for pk in cands) == 1 else cands
    if len(cands) > 1 and lat is not None and lon is not None:
        near = [pk for pk in cands if pk[1]["lat"] is not None
                and _meters(lat, lon, pk[1]["lat"], pk[1]["lon"]) <= NEAR_M]
        cands = near if len(near) == 1 else cands
    if len(cands) == 1:
        return cands[0][0], None
    return None, f"{len(cands)} kit properties share this address; not guessing"


def _best_home(homes: list) -> dict:
    """Several listings of the same home: keep the one with a scan, then the lowest id."""
    return min(homes, key=lambda h: (not h["matterport_model_ids"], int(h["property_id"])))


UNITS_CONF = 0.8  # a building known only from its units' home listings


def _unit_sort(u: str):
    return (0, int(u), u) if u.isdigit() else (1, 0, u)


def units_words(units: list[str]) -> str:
    """['207', '222'] -> 'units 207 and 222'; ['207'] -> 'unit 207'."""
    units = [str(u) for u in units]
    if len(units) == 1:
        return f"unit {units[0]}"
    return "units " + ", ".join(units[:-1]) + f" and {units[-1]}"


def units_building(hits: list, q: dict, conf: float, how: str) -> dict | None:
    """A building known only from its units: every hit is a home (condo unit) listing at the same street number and
    street (same building name where names are given), with no building record among them. Returns a building-level
    Match with `units_only: True`, or None when the rule does not apply.

    Applies when the address names no unit and the homes hold at least two different units, or when it names a unit
    none of them has (`unit_not_on_record`). Listings of the same unit are merged (_best_home). Building facts are
    only those every unit agrees on (year built); stories, unit count and building size never come from a unit.
    The units' plans and scans stay out of `floorplans` / `matterport_model_ids` (those are the building's own);
    they are in `unit_plans` / `unit_scans`, each tagged with its unit, as examples of a home in this building."""
    props: dict[str, tuple] = {}
    for p, k in hits:
        props.setdefault(str(p.get("property_id")), (p, k))
    if not props or any(p.get("kind") != "home" for p, _ in props.values()):
        return None
    if any(not (k.get("num_lo") == q.get("num_lo") == k.get("num_hi")) for _, k in props.values()):
        return None
    if len({street_key(k.get("street_name") or "") for _, k in props.values()}) != 1:
        return None
    names = {re.sub(r"\W+", " ", str(p.get("name") or "")).strip().lower() for p, _ in props.values()} - {""}
    if len(names) > 1:
        return None
    groups: dict[str, list] = {}
    for p, _ in props.values():
        u = str(p.get("unit") or "").strip()
        if not u:
            return None  # a home with no unit number: a house, not a unit of a building
        groups.setdefault(u.lower(), []).append(p)
    asked = str(q.get("unit") or "").strip().lower() or None
    if asked in groups or (not asked and len(groups) < 2):
        return None  # the page's own unit is on record, or only one home: matched as that home, as before
    homes = sorted((_best_home(g) for g in groups.values()), key=lambda h: _unit_sort(str(h["unit"])))
    units = [str(h["unit"]) for h in homes]
    years = {(h.get("record") or {}).get("year_built") for h in homes}
    year = years.pop() if len(years) == 1 else None
    unit_plans, unit_scans = [], {}
    for h in homes:
        fp = dict((h.get("floorplans") or [{}])[0])
        mid = fp.get("matterport_model_id") or next(iter(h.get("matterport_model_ids") or []), None)
        unit_plans.append({"name": f"Unit {h['unit']}", "unit": str(h["unit"]), "property_id": h["property_id"],
                           "beds": fp.get("beds"), "baths": fp.get("baths"), "sqft": fp.get("sqft"),
                           "matterport_model_id": mid, "listings": len(groups[str(h["unit"]).lower()]),
                           "example": True})
        for m in h.get("matterport_model_ids") or []:
            unit_scans.setdefault(m, str(h["unit"]))
    first = homes[0]
    pts = [(h["lat"], h["lon"]) for h in homes if h.get("lat") is not None and h.get("lon") is not None]
    words = units_words(units)
    why = f"No building record; {words} of this building {'is' if len(units) == 1 else 'are'} on record"
    if asked:
        why += f", but not unit {q.get('unit')}"
    return {"status": "matched", "confidence": round(min(conf, UNITS_CONF), 2),
            "property_id": "units:" + ",".join(h["property_id"] for h in homes), "kind": "building",
            "units_only": True, "basis": "units", "name": first.get("name"),
            "address": re.sub(r"\s*\(unit [^)]*\)\s*$", "", str(first.get("address") or "")),
            "city": first.get("city"), "state": first.get("state"),
            "lat": pts[0][0] if pts else None, "lon": pts[0][1] if pts else None,
            "record": {"stories": None, "year_built": year, "unit_count": None, "building_sf": None},
            "floorplans": [], "matterport_model_ids": [], "plan_types": [],
            "units_on_file": units, "unit_plans": unit_plans, "unit_scans": unit_scans,
            "unit_not_on_record": q.get("unit") if asked else None,
            "how": f"{why} ({how})"}


def _add_unit(m: dict, p: dict, hits: list, q: dict) -> dict:
    """Building matched and the text names a unit with its own home listing: add that unit's plan and scan."""
    homes = [h for h, _ in hits if h["kind"] == "home" and q["unit"] and str(h.get("unit") or "").lower() == q["unit"]]
    if p["kind"] != "building" or not homes:
        return m
    h = _best_home(homes)
    m["floorplans"] += [dict(f) for f in h["floorplans"]]
    m["matterport_model_ids"] += [k for k in h["matterport_model_ids"] if k not in m["matterport_model_ids"]]
    m["how"] += f"; unit {h['unit']} plan and scan from home listing {h['property_id']}"
    m["plan_types"] = plan_types(m["floorplans"])
    return m


def _num_ok(k: dict, q: dict) -> bool:
    return k["num_lo"] is not None and k["num_lo"] <= q["num_lo"] <= k["num_hi"]


def match_address(address: str, lat: float | None = None, lon: float | None = None) -> dict:
    idx = _load()
    q = parse(address or "", idx["cities"])
    if q["num_lo"] is not None and q["streets"]:
        same = [(p, k) for st in q["streets"] for p, k in idx["by_street"].get(st, []) if _num_ok(k, q)]
        same = same or abbrev_hits(q, idx["by_street"])
        tiers = [("street number + street name + zip", 0.97, q["zip"] and [h for h in same if h[1]["zip"] == q["zip"]]),
                 ("street number + street name + city", 0.9, q["city"] and [h for h in same if h[1]["city"] == q["city"]]),
                 ("street number + street name (no city or zip given; unique in kit)", 0.75,
                  not q["zip"] and not q["city"] and len({p["property_id"] for p, _ in same}) == 1 and same)]
        for how, conf, hits in tiers:
            if hits:
                units = units_building(hits, q, conf, how)
                if units:
                    return units if units["lat"] is not None else {**units, "lat": lat, "lon": lon}
                p, why = _pick(hits, q, lat, lon)
                if p:
                    return _add_unit(_matched(p, conf, how), p, hits, q)
                return _no_match(why, lat, lon)
    if lat is not None and lon is not None:
        near = []  # (prefer buildings, distance, property)
        for p in idx["props"]:
            for k in p["keys"]:
                if k["lat"] is None or abs(k["lat"] - lat) > 0.001:
                    continue
                d = _meters(lat, lon, k["lat"], k["lon"])
                if d <= NEAR_M:
                    near.append((p["kind"] != "building", d, p))
        if near:
            _, best_d, best = min(near, key=lambda t: (t[0], t[1]))
            return _matched(best, 0.9 - 0.4 * best_d / NEAR_M, f"nearest kit property, {best_d:.0f} m from the given point")
    return _no_match("no kit property at this street number, street and zip/city"
                     + (f" or within {NEAR_M:.0f} m of the point" if lat is not None and lon is not None else ""), lat, lon)


RECORD_KEYS = ("stories", "year_built", "unit_count", "building_sf")


def record_evidence(match: dict) -> dict:
    if not match or match.get("status") != "matched":
        return rc_common.evidence("record", "no_evidence", "No building record: the address did not match the kit.",
                                  detail=f"CoStar hackathon kit; match: {(match or {}).get('how', 'none')}")
    rec = match.get("record") or {}
    obs = {k: rec[k] for k in RECORD_KEYS if rec.get(k) is not None}
    if match.get("units_only"):  # no building record: only the units' home listings
        units = match.get("units_on_file") or []
        built = f" (built {obs['year_built']})" if "year_built" in obs else ""
        summary = f"CoStar has {units_words(units)} of this building on record{built}."
        detail = (f"CoStar hackathon kit home listings {', '.join(str(f.get('property_id')) for f in match.get('unit_plans') or [])}"
                  f"; no building record. Matched by {match.get('how')}")
        obs = {"year_built": obs["year_built"]} if "year_built" in obs else {}
        return rc_common.evidence("record", "ok" if obs else "no_evidence", summary,
                                  {**obs, "units_only": True, "units_on_file": list(units)}, detail=detail)
    detail = f"CoStar hackathon kit property {match.get('property_id')}, matched by {match.get('how')}"
    if not obs:
        return rc_common.evidence("record", "no_evidence", "The kit record for this property has no building facts.",
                                  detail=detail)
    bits = []
    if "stories" in obs:
        bits.append(f"{obs['stories']} stories")
    if "year_built" in obs:
        bits.append(f"built {obs['year_built']}")
    if "unit_count" in obs:
        bits.append(f"{obs['unit_count']} units")
    if "building_sf" in obs:
        bits.append(f"{obs['building_sf']:,.0f} sq ft")
    name = match.get("name") or match.get("address") or "this property"
    return rc_common.evidence("record", "ok", f"CoStar record for {name}: {', '.join(bits)}.", obs, detail=detail)
