"""Resolve a listing floor plan (name + beds + baths + sq ft) to the building's own plan and, if possible, a scan.

Order (docs/DECISIONS.md, floor plans):
 a. kit plan whose name equals / starts with / contains the listing label, with its own scan id;
 b. no name match: kit plan with the same beds and baths and the nearest size within SIZE_TOL;
 c. kit plan without a scan id: a property-level scan whose title fits that plan (beds, baths, title sq ft
    within TITLE_TOL or the plan name as a word of the title);
 d. otherwise the kit plan's own numbers (source "record"); no kit plan at all = "not in the building's records".
Pure functions here; fetching scan summaries is the caller's job.
"""
from __future__ import annotations

import re

SIZE_TOL = 0.08    # listing sq ft vs kit plan sq ft for a size match
TITLE_TOL = 0.03   # scan title sq ft vs kit plan sq ft for a title match
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def by_name(label: str, floorplans: list[dict]) -> dict | None:
    """Exact, then word-boundary prefix, then substring match on the kit plan name (case-insensitive)."""
    p = (label or "").strip().lower()
    if not p:
        return None
    names = [(str(fp.get("name") or "").strip().lower(), fp) for fp in floorplans or []]
    for n, fp in names:
        if n == p:
            return fp
    pre = re.compile(r"^" + re.escape(p) + r"(\b|\s|$)")
    for n, fp in names:
        if pre.match(n):
            return fp
    for n, fp in names:
        if p in n:
            return fp
    return None


def listing_facts(label: str, claims: list[dict]) -> dict:
    """{bedrooms, bathrooms, area_sqft} the listing gives for one plan label (None when absent)."""
    out = {k: None for k in UNIT_KINDS}
    for c in claims or []:
        if (c.get("label") or "").strip().lower() == (label or "").strip().lower() and c.get("kind") in UNIT_KINDS:
            if out[c["kind"]] is None:
                out[c["kind"]] = _num(c.get("value"))
    return out


def by_size(facts: dict, floorplans: list[dict]) -> dict | None:
    """Kit plan with the listing's beds (and baths, when given) and the nearest sq ft within SIZE_TOL."""
    beds, baths, sqft = facts.get("bedrooms"), facts.get("bathrooms"), facts.get("area_sqft")
    if beds is None or not sqft:
        return None
    best = None
    for fp in floorplans or []:
        kb, kba, ks = _num(fp.get("beds")), _num(fp.get("baths")), _num(fp.get("sqft"))
        if kb != beds or not ks or (baths is not None and kba is not None and kba != baths):
            continue
        d = abs(ks - sqft) / sqft
        if d <= SIZE_TOL and (best is None or d < best[0]):
            best = (d, fp)
    return best[1] if best else None


def title_sqft(title: str) -> float | None:
    m = re.search(r"(\d[\d,]{2,5})\s*(?:SF|sq\.?\s*ft|square feet)\b", title or "", re.I)
    return _num(m.group(1)) if m else None


def title_count(kind: str, title: str) -> float | None:
    """Bedrooms or bathrooms a scan's own title states ('2 BR, 2 BA - 990 SF', 'Studio - 539 SF'), else None."""
    title = title or ""
    if kind == "bedrooms":
        if re.search(r"\bstudio\b", title, re.I):
            return 0.0
        m = re.search(r"\b(\d+)\s*(?:BR|BD|BED)", title, re.I)
    elif kind == "bathrooms":
        m = re.search(r"\b(\d+(?:\.\d)?)\s*(?:BA|BATH)", title, re.I)
    else:
        return None
    return _num(m.group(1)) if m else None


def scan_fits(fp: dict, summary: dict) -> float | None:
    """How well a scan summary (Evidence) fits a kit plan: relative sq ft gap (smaller is better), None = no fit."""
    if not isinstance(summary, dict) or summary.get("status") != "ok":
        return None
    obs = summary.get("observed") or {}
    title = str(obs.get("model_name") or "")
    beds, kb = _num(obs.get("bedrooms")), _num(fp.get("beds"))
    if beds is None or kb is None or beds != kb:
        return None
    baths, kba = _num(obs.get("bathrooms")), _num(fp.get("baths"))
    if baths is not None and kba is not None and baths != kba:
        return None
    ts, ks = title_sqft(title), _num(fp.get("sqft"))
    name = str(fp.get("name") or "").strip()
    named = bool(name) and re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", title, re.I) is not None
    if ts and ks:
        gap = abs(ts - ks) / ks
        return gap if gap <= TITLE_TOL or (named and gap <= SIZE_TOL) else None
    return 0.05 if named else None


def pick_scan(fp: dict, summaries: dict[str, dict], taken: set[str]) -> tuple[str, str] | None:
    """(model id, title) of the one property scan that fits the kit plan; None when none or several fit equally.
    Scans the kit already gives to another plan count only when nothing else fits."""
    fits = []
    for mid, s in summaries.items():
        gap = scan_fits(fp, s)
        if gap is not None:
            fits.append((mid in taken, gap, mid, str((s.get("observed") or {}).get("model_name") or "")))
    if not fits:
        return None
    fits.sort()
    if len(fits) > 1 and fits[0][:2] == fits[1][:2]:
        return None  # two scans fit equally well: not guessing
    return fits[0][2], fits[0][3]


def plan_label(fp: dict) -> str:
    return str(fp.get("name") or "").strip()
