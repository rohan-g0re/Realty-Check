"""judge(claim, evidences) -> Verdict. Pure Python, deterministic (docs/CONTRACT.md section 4)."""
from __future__ import annotations

import re
from typing import Any

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
    for e in [winner, *usable, *[x for x in evidences or [] if isinstance(x, dict)
                                 and x.get("source") == winner.get("source") and x.get("status") == "ok"]]:
        for im in e.get("images") or []:
            if isinstance(im, dict) and im.get("path"):
                return im["path"]
    return None


def judge(claim: dict, evidences: list[dict]) -> dict:
    kind = claim.get("kind")
    claimed_txt = phrase(kind, claim.get("value"))
    base = {"claim_id": claim.get("id"), "claimed": claimed_txt, "image": None}
    if not claim.get("checkable", True):
        return {**base, "verdict": "cant_check", "found": "nothing to measure", "source": None,
                "reason": "This is a marketing phrase with nothing we can measure."}
    claimed = _num(claim.get("value"))
    usable = _usable(claim, evidences) if kind in (*COUNT_KINDS, "area_sqft") else []
    if claimed is None or not usable:
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

    if kind == "area_sqft":
        pct = abs(found - claimed) / claimed * 100 if claimed else 100.0
        verdict = "confirmed" if pct <= 5 else "close" if pct <= 10 else "contradicted"
        diff = f", {pct:.0f}% {'smaller' if found < claimed else 'larger'}" if pct >= 0.5 else ""
        how = f"{_cap(SOURCE_NAME[src])} measures {found_txt}{diff}."
    else:
        verdict = "confirmed" if round(found) == round(claimed) else "contradicted"
        if verdict == "confirmed":
            how = f"{_cap(SOURCE_NAME[src])} agree{'s' if VERB[src] != 'say' else ''}: {found_txt}."
        else:
            how = f"{_cap(SOURCE_NAME[src])} {VERB[src]} {found_txt}."
        if kind == "stories" and src == "aerial" and _num(obs.get("height_m")) is not None:
            how = how[:-1] + f" (measured roof height {_n(_num(obs['height_m']))} m above the ground)."

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
