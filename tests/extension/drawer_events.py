"""Invented Event runs (docs/CONTRACT.md 2.6) for the drawer tests and screenshots. Every value is made up.

run_events(address, bad=[...], n=..., ...) -> list of Events. The first five claims quote words that are on
fixture/listing.html, so their marks can be found on the page; the rest are filler claims with no mark.
"""
from __future__ import annotations

# claim id -> (verdict, found, source) for the claims that do not match, worst listed in claim order
BAD = {
    "c1": ("contradicted", "1 bedroom and a den", "scan"),
    "c2": ("contradicted", "6 floors", "aerial"),
    "c3": ("close", "built in 2015", "county"),
    "c4": ("contradicted", "968 sq ft of floor, 12% smaller", "scan"),
    "c5": ("close", "not visible in the aerial photo", "aerial"),
    "c6": ("contradicted", "1 bathroom", "record"),
}
AMENITIES = ["Rooftop terrace", "Fitness center", "Dog park", "Bike storage", "Package lockers", "Courtyard grill",
             "Covered parking", "In-unit washer and dryer", "City views", "Co-working lounge", "EV charging",
             "Pet wash station", "Clubhouse", "Gated entry", "Storage units", "Playground"]
PLANS = ["Plan A", "Plan B", "Plan C", "Plan D", "Plan F", "Plan G"]


def _claims(n: int, long_quote: bool) -> list[dict]:
    first = [
        ("bedrooms", 2, "unit", "Plan E", "2 Beds"),
        ("stories", 5, "building", None, "5-story building"),
        ("year_built", 2016, "building", None, "Built in 2016"),
        ("area_sqft", 1100, "unit", "Plan E", "1,100 sq ft"),
        ("amenity", "pool", "building", None, "Resort-style pool"),
        ("bathrooms", 2, "unit", "Plan B", "2 full bathrooms with soaking tubs and dual vanities in the primary suite"
         if long_quote else "Plan B · 2 Baths"),
    ]
    out = []
    for i in range(n):
        if i < len(first):
            kind, value, scope, label, quote = first[i]
        elif i % 3 == 0:
            p = PLANS[(i // 3) % len(PLANS)]
            kind, value, scope, label, quote = "bedrooms", 1 + i % 3, "unit", p, f"{p} · {1 + i % 3} Bed"
        else:
            a = AMENITIES[i % len(AMENITIES)]
            kind, value, scope, label, quote = "amenity", a.lower(), "building", None, a
        out.append({"id": f"c{i + 1}", "kind": kind, "value": value, "unit": "sqft" if kind == "area_sqft" else None,
                    "scope": scope, "label": label, "quote": quote, "checkable": True})
    return out


def _ev(source, observed, summary="invented"):
    return {"source": source, "status": "ok", "summary": summary, "observed": observed, "images": [], "detail": "test"}


def run_events(address: str, bad=("c1", "c2", "c4", "c6", "c3"), n: int = 34, cant: int = 4, facts: int = 6,
               long_quote: bool = False, pause_before_verdicts: float = 0.0, run_id: str = "r1",
               no_match: bool = False) -> list[dict]:
    bad = [b for b in bad if b in BAD and int(b[1:]) <= n]
    claims = _claims(n, long_quote)
    evs: list[dict] = [
        {"type": "run_started", "run_id": run_id, "address": address},
        {"type": "step", "agent": "orchestrator", "text": "Reading the listing."},
        {"type": "step", "agent": "claims", "text": "Pulling out what the page says."},
        {"type": "claims", "claims": claims},
        {"type": "step", "agent": "matcher", "text": "Looking up the building at this address."},
    ]
    if no_match:
        evs.append({"type": "match", "match": {"status": "no_match", "confidence": 0.2, "how": "no building at this address"}})
    else:
        evs += [
            {"type": "match", "match": {"status": "matched", "confidence": 0.96, "address": address, "how": "invented"}},
            {"type": "evidence", "evidence": _ev("record", {"stories": 5, "year_built": 2016, "unit_count": 240})},
            {"type": "evidence", "evidence": _ev("record", {"plan": "Plan E", "bedrooms": 2, "bathrooms": 2, "area_sqft": 1100})},
            {"type": "evidence", "evidence": _ev("record", {"plan": "Plan B", "bedrooms": 2, "bathrooms": 1 if "c6" in bad else 2})},
            {"type": "step", "agent": "inside", "text": "Walking through the 3D scan of Plan E."},
            {"type": "evidence", "evidence": _ev("scan", {"plan": "Plan E", "bedrooms": 1 if "c1" in bad else 2,
                                                          "area_sqft": 968 if "c4" in bad else 1080})},
            {"type": "step", "agent": "outside", "text": "Measuring the roof height from above."},
            {"type": "evidence", "evidence": _ev("aerial", {"height_m": 23.1 if "c2" in bad else 18.0, "stories": 6 if "c2" in bad else 5})},
            {"type": "evidence", "evidence": _ev("aerial", {"feature_check": True, "claim_ids": ["c5"] + [c["id"] for c in claims[6::4]],
                                                            "feature": "pool", "presence": "seen", "presence_confidence": 0.8})},
            {"type": "step", "agent": "paper", "text": "Reading the county parcel record."},
            {"type": "evidence", "evidence": _ev("county", {"year_built": 2015 if "c3" in bad else 2016, "stories": 5})},
        ]
    if pause_before_verdicts:
        evs.append({"_pause": pause_before_verdicts})
    evs.append({"type": "step", "agent": "judge", "text": "Weighing each claim against the sources."})
    counts = {"confirmed": 0, "contradicted": 0, "close": 0, "cant_check": 0}
    filler_cant = 0
    for c in claims:
        cid = c["id"]
        if no_match:
            v = ("cant_check", "no source covers this", "record")
        elif cid in bad:
            v = BAD[cid]
        elif int(cid[1:]) > 6 and filler_cant < cant:
            filler_cant += 1
            v = ("cant_check", "no source covers this", "record")
        else:
            src = {"bedrooms": "scan" if c["label"] == "Plan E" else "record", "bathrooms": "record", "area_sqft": "scan",
                   "stories": "aerial", "year_built": "county", "unit_count": "county"}.get(c["kind"], "aerial")
            v = ("confirmed", c["quote"].lower(), src)
        counts[v[0]] += 1
        evs.append({"type": "verdict", "verdict": {"claim_id": cid, "verdict": v[0], "claimed": c["quote"].lower(), "found": v[1],
                                                   "source": v[2], "reason": "Invented reason.", "image": None}})
    if facts:
        pool = [("plan-a", "Homes & floor plans", "Plan A", "1 bed, 1 bath, 640 sq ft", "record", "From the building record."),
                ("pets", "Rules", "Pets", "Cats and small dogs, up to 2", "record", ""),
                ("lot", "Records", "Lot size", "0.9 acres", "county", "From the county parcel record."),
                ("roof", "Around it", "Rooftop", "Shared roof deck on the top floor", "aerial", "Seen from above."),
                ("class", "Building", "Building class", "Class B", "record", ""),
                ("rent", "Rent", "Asking rent", "$1,400 to $2,600 a month", "record", "As of the listing date.")]
        evs.append({"type": "fill_in", "facts": [{"id": i, "topic": t, "label": lab, "value": val, "source": s, "note": note, "image": None}
                                                 for i, t, lab, val, s, note in pool[:facts]]})
    evs.append({"type": "done", "run_id": run_id, "counts": counts, "headline": "Invented headline.", "report_url": f"/report/{run_id}"})
    return evs
