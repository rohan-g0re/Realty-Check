"""match_address and record_evidence (docs/CONTRACT.md sections 2.3, 2.4, 3)."""
from __future__ import annotations

import json
import math

import rc_common
from tools.kit.normalize import parse, street_key
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
        tiers = [("street number + street name + zip", 0.97, q["zip"] and [h for h in same if h[1]["zip"] == q["zip"]]),
                 ("street number + street name + city", 0.9, q["city"] and [h for h in same if h[1]["city"] == q["city"]]),
                 ("street number + street name (no city or zip given; unique in kit)", 0.75,
                  not q["zip"] and not q["city"] and len({p["property_id"] for p, _ in same}) == 1 and same)]
        for how, conf, hits in tiers:
            if hits:
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
