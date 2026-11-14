"""judge(claim, evidences) -> Verdict. Pure Python, deterministic (docs/CONTRACT.md section 4)."""
from __future__ import annotations

import re
from typing import Any

from . import amenities
from .plans import title_sqft

COUNT_KINDS = ("bedrooms", "bathrooms", "stories", "unit_count", "year_built")
RANK = {"scan": 0, "aerial": 0, "county": 1, "record": 2}  # lower wins
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
SOURCE_NAME = {"scan": "the 3D scan", "aerial": "the aerial photo", "county": "county records",
               "record": "the building record"}
VERB = {"scan": "shows", "aerial": "shows", "county": "say", "record": "says"}


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _n(x: float) -> str:
    return f"{int(x):,}" if float(x).is_integer() else f"{x:,.1f}"


def phrase(kind: str, value: Any) -> str:
    """Plain-English value: '2 bedrooms', '1,100 sq ft', '5 floors', 'built in 2016'."""
    x = _num(value)
    if x is None:
        return str(value)
    one = x == 1
    if kind == "bedrooms":
        return f"{_n(x)} bedroom" + ("" if one else "s")
    if kind == "bathrooms":
        return f"{_n(x)} bathroom" + ("" if one else "s")
    if kind == "area_sqft":
        return f"{_n(x)} sq ft"
    if kind == "stories":
        return f"{_n(x)} floor" + ("" if one else "s")
    if kind == "year_built":
        return f"built in {int(x)}"
    if kind == "unit_count":
        return f"{_n(x)} apartment" + ("" if one else "s")
    return _n(x)


def _usable(claim: dict, evidences: list[dict]) -> list[dict]:
    kind, scope, label = claim.get("kind"), claim.get("scope"), claim.get("label")
    out = []
    for e in evidences or []:
        if not isinstance(e, dict) or e.get("status") != "ok":
            continue
        obs = e.get("observed") or {}
        if _num(obs.get(kind)) is None:
            continue
        src = e.get("source")
        if src not in RANK:
            continue
        if obs.get("illustrative"):
            continue  # e.g. a vision-model floor count from a photo: shown, never decides
        if kind == "stories" and src == "aerial" and _num(obs.get("height_m")) is None:
            continue  # floors come only from measured roof height (and paper records), never a photo count
        if scope == "building" and src == "scan":
            continue  # a unit scan says nothing about the whole building
        if (scope == "unit" or kind in UNIT_KINDS) and src == "aerial":
            continue
        plan = obs.get("plan")
        if obs.get("kit_plan") and not label:
            continue  # one kit floor plan's numbers say nothing about an unnamed unit
        if label and plan and not _same_plan(str(plan), str(label)):
            continue  # evidence about another floor plan
        out.append(e)
    return out


def _same_plan(a: str, b: str) -> bool:
    """'Plan E' and 'Plan E 1 Bedroom + Den' are the same plan; 'Plan E' and 'Plan EF' are not."""
    a, b = a.strip().lower(), b.strip().lower()
    short, long_ = sorted((a, b), key=len)
    return a == b or bool(short) and re.match(re.escape(short) + r"(\s|$)", long_) is not None


def _image(winner: dict, usable: list[dict], evidences: list[dict]) -> str | None:
    # Fallback images must come from the same floor plan as the winning evidence: with several plan
    # scans, any scan's stills would otherwise prove every plan's claim.
    plan = (winner.get("observed") or {}).get("plan")
    same = [x for x in evidences or [] if isinstance(x, dict) and x.get("source") == winner.get("source")
            and x.get("status") == "ok" and not (x.get("observed") or {}).get("feature_check")
            and not (plan and (x.get("observed") or {}).get("plan")
                     and not _same_plan(str(plan), str(x["observed"]["plan"])))]
    for e in [winner, *usable, *same]:
        for im in e.get("images") or []:
            if isinstance(im, dict) and im.get("path"):
                return im["path"]
    return None


def _plan_missing(claim: dict, evidences: list[dict]) -> bool:
    label = claim.get("label")
    return bool(label) and any(isinstance(e, dict) and (e.get("observed") or {}).get("plan_missing")
                               and _same_plan(str(e["observed"].get("plan") or ""), str(label))
                               for e in evidences or [])


def _first_image(e: dict) -> str | None:
    for im in e.get("images") or []:
        if isinstance(im, dict) and im.get("path"):
            return im["path"]
    return None


FEATURE_RANK = {"confirmed": 0, "close": 1, "unclear": 2, "skipped": 3}


def judge_feature(claim: dict, evidences: list[dict], base: dict) -> dict:
    """Amenity / parking / laundry / view claims: presence answers from pictures or scan titles (soft checks)."""
    quote = amenities.text_of(claim).strip()
    said = f"Listing says \u201c{quote}\u201d."
    mine = [e for e in evidences or [] if isinstance(e, dict)
            and claim.get("id") in ((e.get("observed") or {}).get("claim_ids") or [])]
    if not mine:
        why = ("We had no photos that could show this." if amenities.feature_for(claim)
               else "Nothing in our data measures this.")
        return {**base, "verdict": "cant_check", "found": "no data", "source": None, "reason": f"{said} {why}"}
    best = min(mine, key=lambda e: FEATURE_RANK[amenities.outcome(e.get("observed") or {})])
    obs, src = best.get("observed") or {}, best.get("source")
    out = amenities.outcome(obs)
    nice = amenities.NICE.get(str(obs.get("feature")), str(obs.get("feature") or "it"))
    conf = _num(obs.get("presence_confidence")) or 0.0
    title = str(obs.get("scan_title") or "")
    where = "the aerial photos" if src == "aerial" else (f"photos from the 3D scan '{title}'" if title
                                                         else "photos from the 3D scan")
    if out == "confirmed" and obs.get("how") == "scan_title":
        aerial = [amenities.outcome(e.get("observed") or {}) for e in mine if e.get("source") == "aerial"]
        also = (" The aerial photos did not show it from above, so it may be indoors."
                if aerial and "confirmed" not in aerial else "")
        return {**base, "verdict": "confirmed", "found": f"3D scan titled '{title}'", "source": "scan",
                "reason": f"{said} The building's 3D scans include one titled '{title}', which matches this by "
                          f"name.{also}", "image": _first_image(best)}
    if out == "confirmed":
        found = "Visible in the aerial photo" if src == "aerial" else "Visible in the 3D scan photos"
        return {**base, "verdict": "confirmed", "found": found, "source": src, "image": _first_image(best),
                "reason": f"{said} A vision model looked for {nice} in {where} and saw it (confidence {conf:.2f}). "
                          "This is a picture check, not a measurement."}
    if out == "close":
        found = "Not visible in the aerial photo" if src == "aerial" else "Not visible in the 3D scan photos"
        why = ("it may be indoors or on a part of the site the photo does not show" if src == "aerial"
               else "the scan photos show only some rooms")
        return {**base, "verdict": "close", "found": found, "source": src, "image": _first_image(best),
                "reason": f"{said} A vision model looked for {nice} in {where} and did not see it "
                          f"(confidence {conf:.2f}). A photo cannot prove something is absent: {why}."}
    if out == "skipped":
        return {**base, "verdict": "cant_check", "found": "not checked", "source": None,
                "reason": f"{said} {best.get('summary') or 'The photos were not checked.'}"}
    return {**base, "verdict": "cant_check", "found": "unclear", "source": None,
            "reason": f"{said} The photos don't show this clearly (we looked for {nice} in {where})."}


def judge(claim: dict, evidences: list[dict]) -> dict:
    kind = claim.get("kind")
    claimed_txt = phrase(kind, claim.get("value"))
    base = {"claim_id": claim.get("id"), "claimed": claimed_txt, "image": None}
    if not claim.get("checkable", True):
        return {**base, "verdict": "cant_check", "found": "nothing to measure", "source": None,
                "reason": "This is a marketing phrase with nothing we can measure."}
    if amenities.is_feature_claim(claim):
        return judge_feature(claim, evidences, base)
    claimed = _num(claim.get("value"))
    usable = _usable(claim, evidences) if kind in (*COUNT_KINDS, "area_sqft") else []
    if claimed is None or not usable:
        if kind in UNIT_KINDS and _plan_missing(claim, evidences):
            return {**base, "verdict": "cant_check", "found": "not in the building's records", "source": None,
                    "reason": f"Listing says {claimed_txt}. This floor plan is not in the building's records."}
        return {**base, "verdict": "cant_check", "found": "no data", "source": None,
                "reason": f"Listing says {claimed_txt}. None of our sources measured this."}

    winner = sorted(usable, key=lambda e: RANK[e["source"]])[0]  # stable: first found wins a tie
    obs = winner["observed"]
    found = _num(obs[kind])
    src = winner["source"]
    found_txt = phrase(kind, found)
    if kind == "bedrooms" and (obs.get("has_den") or any("den" == str(x).strip().lower()
                                                        for x in obs.get("room_labels") or [])
                               or re.search(r"\bden\b", str(obs.get("model_name") or ""), re.I)):
        found_txt += " + den"

    kit_plan = obs.get("kit_plan") if src == "record" else None
    if kind == "area_sqft":
        title = str(obs.get("model_name") or "").strip() if src == "scan" else ""
        titled = title_sqft(title) if title else None
        if titled:
            found = titled  # the scan's own title states the plan's size: compare like any written size
        pct = abs(found - claimed) / claimed * 100 if claimed else 100.0
        diff = f", {pct:.0f}% {'smaller' if found < claimed else 'larger'}" if pct >= 0.5 else ""
        if src == "scan" and not titled:
            # A scan measures the scanned interior floor; the advertised size is the whole unit, which in
            # 89 floor plans ran a median 11% larger. Allow that shortfall, but not much extra size.
            d = (found - claimed) / claimed * 100 if claimed else -100.0
            verdict = ("confirmed" if -20 <= d <= 5 else "close" if -30 <= d <= 10 else "contradicted")
            how = (f"The 3D scan measures {found_txt} of floor{diff}; scans usually measure about a tenth less "
                   "than the advertised size.")
        else:
            verdict = "confirmed" if pct <= 5 else "close" if pct <= 10 else "contradicted"
            if titled:
                found_txt = f"{phrase(kind, found)} (scan title)"
                how = f"The 3D scan of this plan is titled '{title}', which gives {phrase(kind, found)}{diff}."
            else:
                how = f"{_cap(SOURCE_NAME[src])} measures {found_txt}{diff}."
        if kit_plan:
            how = f"According to the building record, its plan {kit_plan} has {found_txt}{diff}."
    else:
        verdict = "confirmed" if round(found) == round(claimed) else "contradicted"
        if verdict == "confirmed":
            how = f"{_cap(SOURCE_NAME[src])} agree{'s' if VERB[src] != 'say' else ''}: {found_txt}."
        else:
            how = f"{_cap(SOURCE_NAME[src])} {VERB[src]} {found_txt}."
        if kit_plan:
            how = f"According to the building record, its plan {kit_plan} has {found_txt}."
        if kind == "stories" and src == "aerial" and _num(obs.get("height_m")) is not None:
            how = f"The measured roof height ({_n(_num(obs['height_m']))} m) means about {found_txt}."
    if obs.get("plan_match"):
        how += " " + str(obs["plan_match"]).strip()

    # Mention other sources briefly so the reader sees agreement or the disagreement we overruled.
    others = []
    for e in usable:
        if e is winner or e["source"] == src:
            continue
        v = _num(e["observed"][kind])
        num = str(int(v)) if kind == "year_built" else _n(v)
        also = "also " if v == found else ""
        bit = f"{SOURCE_NAME[e['source']]} {also}{VERB[e['source']]} {num}"
        if bit not in others:
            others.append(bit)
    extra = (" " + _cap("; ".join(others)) + ".") if others else ""
    reason = f"Listing says {claimed_txt}. {how}{extra}"
    return {**base, "verdict": verdict, "found": found_txt, "source": src, "reason": reason,
            "image": _image(winner, usable, evidences)}
