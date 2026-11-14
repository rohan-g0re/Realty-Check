"""judge(claim, evidences) -> Verdict. Pure Python, deterministic (docs/CONTRACT.md section 4)."""
from __future__ import annotations

import re
from typing import Any

from . import amenities
from .plans import title_count, title_sqft

COUNT_KINDS = ("bedrooms", "bathrooms", "stories", "unit_count", "year_built")
RANK = {"scan": 0, "aerial": 0, "county": 1, "record": 2}  # lower wins
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
SOURCE_NAME = {"scan": "the 3D scan", "aerial": "the Vexcel aerial photo", "county": "county records",
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


def _usable(claim: dict, evidences: list[dict], any_plan: bool = False) -> list[dict]:
    """Evidence that may decide this claim. any_plan=True keeps one floor plan's numbers for an unnamed unit
    (they may only confirm it: see _judge_unnamed)."""
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
        if obs.get("kit_plan") and not label and not any_plan:
            continue  # one kit floor plan's numbers say nothing about an unnamed unit
        if label and plan and not _same_plan(str(plan), str(label)):
            continue  # evidence about another floor plan
        if label and src == "scan" and not plan:
            continue  # a scan not tied to this floor plan says nothing about it
        out.append(e)
    return out


def _specific(e: dict) -> bool:
    """Evidence about one floor plan or one scan (not the whole building)."""
    obs = e.get("observed") or {}
    return e.get("source") == "scan" or bool(obs.get("plan") or obs.get("kit_plan") or obs.get("floorplans"))


def _same(a: float, b: float) -> bool:
    return round(a) == round(b)


def _scan_band(found: float, claimed: float) -> str:
    """Measured scan floor area vs the advertised size: a scan measures interior floor, which in 89 floor plans
    ran a median 11% under the advertised size. Up to 12% smaller or 5% larger = confirmed; 12-20% smaller or
    5-10% larger = close; beyond = contradicted."""
    d = round((found - claimed) / claimed * 100, 1) if claimed else -100.0
    return "confirmed" if -12 <= d <= 5 else "close" if -20 <= d <= 10 else "contradicted"


def _written_band(found: float, claimed: float) -> str:
    pct = abs(found - claimed) / claimed * 100 if claimed else 100.0
    return "confirmed" if pct <= 5 else "close" if pct <= 10 else "contradicted"


# ---- bed / bath counts read off a scan's room labels (fix: labels alone never contradict) ----------------

def _from_labels(e: dict, kind: str) -> bool:
    """A scan's bed/bath count that came from its room labels (or a photo reading), not from its title."""
    return (e.get("source") == "scan" and kind in ("bedrooms", "bathrooms")
            and (e.get("observed") or {}).get("counts_from") != "model_name")


def _vet_labels(kind: str, claimed: float, usable: list[dict]) -> tuple[list[dict], list[str]]:
    """Room labels are often wrong (5 'bedrooms' in a 2-bed plan, no bathroom at all). A label count may
    contradict the listing only when the scan's title or the building record for this plan says the same.
    Returns the evidence to judge with (label counts replaced by the title's count where the title decides)
    and notes about label counts that were set aside."""
    records = [_num(e["observed"][kind]) for e in usable if e.get("source") == "record"]
    out, notes = [], []
    for e in usable:
        if not _from_labels(e, kind):
            out.append(e)
            continue
        obs = e["observed"]
        lab = _num(obs.get(kind))
        if kind == "bathrooms" and lab == 0:
            lab = None  # a unit with no bathroom is a labelling gap, never a finding
        title = str(obs.get("model_name") or "").strip()
        tc = title_count(kind, title)
        said = (f"Its room labels count {phrase(kind, lab)}" if lab is not None
                else "Its room labels show no bathroom")
        if lab is not None and _same(lab, claimed):
            out.append(e)  # labels agree with the listing
        elif tc is not None and lab is not None and _same(tc, lab):
            out.append(e)  # the title backs the labels
        elif tc is not None:  # the title decides; the labels are set aside
            out.append({**e, "observed": {**obs, kind: tc, "count_from_title": True,
                                          "label_note": f"{said}, which we do not rely on."}})
        elif lab is not None and any(r is not None and _same(r, lab) for r in records):
            out.append(e)  # the building record for this plan backs the labels
        else:
            notes.append(f"The 3D scan's room labels count {phrase(kind, lab)}, but room labels alone are not "
                         "reliable, so we did not use them." if lab is not None else
                         "The 3D scan's room labels show no bathroom, which is a labelling gap, not a finding.")
    return out, notes


# ---- unit claims that name no floor plan (fix: one plan's or one scan's numbers may only confirm) -------

def _judge_unnamed(claim: dict, evidences: list[dict], base: dict) -> dict | None:
    """'1 Bed' or '645 sq ft' with no plan name: confirmed when some plan in the building record or some scan
    has that number; never contradicted or close by evidence about one particular plan or scan.
    Returns None when only building-wide evidence remains (judged the normal way)."""
    kind, claimed = claim.get("kind"), _num(claim.get("value"))
    claimed_txt = phrase(kind, claim.get("value"))
    usable = _usable(claim, evidences, any_plan=True)
    specific = [e for e in usable if _specific(e)]
    lists = [e for e in evidences or [] if isinstance(e, dict) and e.get("status") == "ok"
             and e.get("source") == "record" and isinstance((e.get("observed") or {}).get("floorplans"), list)]
    best = None  # (rank, gap, how, evidence)
    key = {"bedrooms": "beds", "bathrooms": "baths", "area_sqft": "sqft"}[kind]
    for e in lists:
        for fp in e["observed"]["floorplans"]:
            v = _num((fp or {}).get(key)) if isinstance(fp, dict) else None
            if v is None:
                continue
            ok = _same(v, claimed) if kind != "area_sqft" else _written_band(v, claimed) == "confirmed"
            if ok:
                gap = abs(v - claimed) / claimed if claimed else 0.0
                how = f"the building record's plan {fp.get('name') or 'one plan'} has {phrase(kind, v)}"
                cand = (0, gap, how, e, v)
                best = min(best, cand, key=lambda x: x[:2]) if best else cand
    for e in specific:
        obs, src = e["observed"], e["source"]
        title = str(obs.get("model_name") or "").strip()
        v = _num(obs.get(kind))
        if src == "scan" and kind != "area_sqft":
            v = v if obs.get("counts_from") == "model_name" else title_count(kind, title)  # labels never confirm
            if v is None:
                continue
            ok, how = _same(v, claimed), f"the 3D scan titled '{title}' shows {phrase(kind, v)}"
        elif src == "scan":
            ts = title_sqft(title) if title else None
            if ts:
                v, ok = ts, _written_band(ts, claimed) == "confirmed"
                how = f"the 3D scan titled '{title}' gives {phrase(kind, ts)}"
            else:
                ok = _scan_band(v, claimed) == "confirmed"
                named = f" titled '{title}'" if title else ""
                how = (f"the 3D scan{named} measures {phrase(kind, v)} of floor (scans usually measure about a "
                       "tenth less than the advertised size)")
        else:
            ok = _same(v, claimed) if kind != "area_sqft" else _written_band(v, claimed) == "confirmed"
            name = obs.get("kit_plan") or obs.get("plan")
            how = (f"the building record's plan {name} has {phrase(kind, v)}" if name
                   else f"{SOURCE_NAME[src]} {VERB[src]} {phrase(kind, v)}")
        if ok:
            gap = abs(v - claimed) / claimed if claimed else 0.0
            cand = (0 if src == "record" else 1, gap, how, e, v)
            best = min(best, cand, key=lambda x: x[:2]) if best else cand
    if best:
        e = best[3]
        return {**base, "verdict": "confirmed", "found": phrase(kind, best[4]), "source": e["source"],
                "reason": f"Listing says {claimed_txt} without naming a floor plan; {best[2]}.",
                "image": _image(e, [e], evidences) if e["source"] == "scan" else None}
    if [e for e in usable if not _specific(e)]:
        return None
    if specific or lists:
        return {**base, "verdict": "cant_check", "found": "no plan with this", "source": None,
                "reason": f"Listing says {claimed_txt} without naming a floor plan, and no floor plan in the "
                          f"building record or the 3D scans has {claimed_txt}, so we could not check it."}
    return {**base, "verdict": "cant_check", "found": "no data", "source": None,
            "reason": f"Listing says {claimed_txt}. None of our sources measured this."}


def _same_plan(a: str, b: str) -> bool:
    """'Plan E' and 'Plan E 1 Bed + Den' are the same plan; 'Plan E' and 'Plan EF' are not, and neither are
    '1 Bed' and '1 Bed + Den' (names that state different kinds of home)."""
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if not short or re.match(re.escape(short) + r"(\s|$)", long_) is None:
        return False
    ka, kb = _kind_of_home(short), _kind_of_home(long_)
    return not (ka and kb and ka != kb)


def _kind_of_home(name: str) -> tuple | None:
    """(bedrooms, den) a plan name states ('1 Bed + Den' -> (1, True), 'Studio' -> (0, False)), else None."""
    beds = title_count("bedrooms", name)
    return None if beds is None else (beds, re.search(r"\b(?:den|study)\b", name, re.I) is not None)


# ---- a scan linked to this plan that shows another kind of home (fix: a wrong pairing never contradicts) --

_PLAN_CODE = re.compile(r"\bplan\s+([a-z]{0,3}\d{1,3}[a-z]{0,2}|[a-z]{1,3})(?![\w])", re.I)


def _plan_code(name: str) -> str | None:
    for m in _PLAN_CODE.finditer(name or ""):
        code = m.group(1).upper()
        if code not in ("FOR", "THE", "AND", "OF", "IN", "ON", "TO", "AT"):  # 'floor plan for ...' is no code
            return code
    return None


def _other_plan(e: dict, claim: dict, evidences: list[dict]) -> str | None:
    """A scan linked to this claim's plan whose own title names a different plan code ('Plan C3' for a 'Plan A1'
    claim). On a page offering several plans such a scan is of another home, so it may not decide the claim.
    (A title whose bed count differs from the record is kept: a plan's own scan showing fewer bedrooms than
    advertised is exactly what the check is for.) Returns a plain note, or None when the scan fits."""
    if e.get("source") != "scan":
        return None
    obs = e.get("observed") or {}
    title = str(obs.get("model_name") or "").strip()
    if not title:
        return None
    plan = str(obs.get("plan") or claim.get("label") or "")
    recs = [x for x in evidences or [] if isinstance(x, dict) and x.get("source") == "record"
            and x.get("status") == "ok" and (x.get("observed") or {}).get("plan")
            and not (x.get("observed") or {}).get("plan_type")
            and _same_plan(str(x["observed"]["plan"]), plan)]
    names = [str(claim.get("label") or ""), plan] + [str(x["observed"].get("kit_plan") or "") for x in recs]
    codes = {c for c in (_plan_code(n) for n in names) if c}
    tc = _plan_code(title)
    if tc and codes and tc not in codes:
        return (f"The 3D scan linked to {plan} is titled '{title}', which names another floor plan, so it was "
                "not used for this plan.")
    return None


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


def record_names(claim: dict, evidences: list[dict]) -> str | None:
    """The record amenity name(s) this claim names, when the building record's amenity list (record Evidence
    observed.amenities) has every one of them; else None. A name missing from the record decides nothing: the
    record's list is incomplete. Only names count, never how many photos show them."""
    from tools.kit.facts import AMENITIES  # lazy: the fixed list of record amenity names and their patterns
    words = amenities.text_of(claim)
    wanted = [name for name, rx in AMENITIES if re.search(rx, words, re.I)]
    if not wanted:
        return None
    on_record = {str(a).strip().lower() for e in evidences or [] if isinstance(e, dict) and e.get("source") == "record"
                 and e.get("status") == "ok" and isinstance((e.get("observed") or {}).get("amenities"), list)
                 for a in e["observed"]["amenities"]}
    if not all(n.lower() in on_record for n in wanted):
        return None
    return ", ".join(wanted)


def judge_feature(claim: dict, evidences: list[dict], base: dict) -> dict:
    """Amenity / parking / laundry / view claims: the building record's amenity names, else presence answers from
    pictures or scan titles (soft checks)."""
    quote = amenities.text_of(claim).strip()
    said = f"Listing says \u201c{quote}\u201d."
    named = record_names(claim, evidences)
    if named:
        return {**base, "verdict": "confirmed", "found": "Named in the building record", "source": "record",
                "reason": f"{said} The building record names it.", "image": None}
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
    where = "the Vexcel aerial photos" if src == "aerial" else (f"photos from the 3D scan '{title}'" if title
                                                         else "photos from the 3D scan")
    if out == "confirmed" and obs.get("how") == "scan_title":
        aerial = [amenities.outcome(e.get("observed") or {}) for e in mine if e.get("source") == "aerial"]
        also = (" The Vexcel aerial photos did not show it from above, so it may be indoors."
                if aerial and "confirmed" not in aerial else "")
        return {**base, "verdict": "confirmed", "found": f"3D scan titled '{title}'", "source": "scan",
                "reason": f"{said} The building's 3D scans include one titled '{title}', which matches this by "
                          f"name.{also}", "image": _first_image(best)}
    if out == "confirmed":
        found = "Visible in the Vexcel aerial photo" if src == "aerial" else "Visible in the 3D scan photos"
        return {**base, "verdict": "confirmed", "found": found, "source": src, "image": _first_image(best),
                "reason": f"{said} A vision model looked for {nice} in {where} and saw it (confidence {conf:.2f}). "
                          "This is a picture check, not a measurement."}
    if out == "close":
        found = "Not visible in the Vexcel aerial photo" if src == "aerial" else "Not visible in the 3D scan photos"
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


def _unit_scan_value(kind: str, e: dict) -> tuple[float, str] | None:
    """(value, how) from a scan of this very home that is tied to no floor plan: bed/bath counts from its title
    only (never its room labels), size from its title or its measured floor. None when it gives nothing."""
    obs = e.get("observed") or {}
    if (e.get("source") != "scan" or e.get("status") != "ok" or obs.get("plan") or obs.get("example_for")
            or obs.get("feature_check") or obs.get("kit_plan")):
        return None
    title = str(obs.get("model_name") or "").strip()
    if kind in ("bedrooms", "bathrooms"):
        v = _num(obs.get(kind)) if obs.get("counts_from") == "model_name" else title_count(kind, title)
        if v is None or not title:
            return None
        return v, f"the 3D scan of this home is titled '{title}', which gives {phrase(kind, v)}"
    ts = title_sqft(title) if title else None
    if ts:
        return ts, f"the 3D scan of this home is titled '{title}', which gives {phrase(kind, ts)}"
    v = _num(obs.get(kind))
    return (v, f"the 3D scan of this home measures {phrase(kind, v)} of floor") if v is not None else None


def _judge_single_unit(claim: dict, evidences: list[dict], base: dict) -> dict | None:
    """A unit claim with no plan name on a page about one home (the run's single-unit flag): this home's own scan
    (title counts, never room labels) or the record's only floor plan may decide it, mismatch included.
    None when neither says anything."""
    kind, claimed = claim.get("kind"), _num(claim.get("value"))
    claimed_txt = phrase(kind, claim.get("value"))
    cands = []  # (value, how, evidence, measured scan floor)
    for e in evidences or []:
        if not isinstance(e, dict):
            continue
        got = _unit_scan_value(kind, e)
        if got:
            title = str((e.get("observed") or {}).get("model_name") or "")
            cands.append((got[0], got[1], e, kind == "area_sqft" and not (title and title_sqft(title))))
    key = {"bedrooms": "beds", "bathrooms": "baths", "area_sqft": "sqft"}[kind]
    for e in evidences or []:
        fps = (e.get("observed") or {}).get("floorplans") if isinstance(e, dict) else None
        if e.get("source") == "record" and e.get("status") == "ok" and isinstance(fps, list) and len(fps) == 1:
            v = _num((fps[0] or {}).get(key)) if isinstance(fps[0], dict) else None
            if v is not None:
                name = fps[0].get("name") or "its only plan"
                cands.append((v, f"the building record's only floor plan, {name}, has {phrase(kind, v)}", e, False))
    if not cands or claimed is None:
        return None
    v, how, e, floor = sorted(cands, key=lambda c: 0 if c[2].get("source") == "scan" else 1)[0]
    if kind == "area_sqft":
        verdict = _scan_band(v, claimed) if floor else _written_band(v, claimed)
    else:
        verdict = "confirmed" if _same(v, claimed) else "contradicted"
    return {**base, "verdict": verdict, "found": phrase(kind, v), "source": e["source"],
            "reason": f"Listing says {claimed_txt} for this home; {how}.",
            "image": _image(e, [e], evidences) if e["source"] == "scan" else None}


def judge(claim: dict, evidences: list[dict], single_unit: bool = False) -> dict:
    """single_unit: the run found the page is about one home (run.Run.single_unit_page), so a unit claim that names
    no floor plan may be decided by that home's own scan or the record's only plan, mismatch included."""
    kind = claim.get("kind")
    claimed_txt = phrase(kind, claim.get("value"))
    base = {"claim_id": claim.get("id"), "claimed": claimed_txt, "image": None}
    if not claim.get("checkable", True):
        return {**base, "verdict": "cant_check", "found": "nothing to measure", "source": None,
                "reason": "This is a marketing phrase with nothing we can measure."}
    if amenities.is_feature_claim(claim):
        return judge_feature(claim, evidences, base)
    claimed = _num(claim.get("value"))
    unnamed = (kind in UNIT_KINDS and not claim.get("label") and claim.get("scope") != "building"
               and claimed is not None)
    if unnamed and single_unit:
        v = _judge_single_unit(claim, evidences, base)
        if v:
            return v
    if unnamed:
        v = _judge_unnamed(claim, evidences, base)
        if v:
            return v
    usable = _usable(claim, evidences) if kind in (*COUNT_KINDS, "area_sqft") else []
    if unnamed:
        usable = [e for e in usable if not _specific(e)]  # building-wide evidence only
    misfit_notes: list[str] = []
    if claim.get("label") and kind in UNIT_KINDS:
        kept = []
        for e in usable:
            note = _other_plan(e, claim, evidences)
            if note:
                misfit_notes.append(note)
            else:
                kept.append(e)
        usable = kept
    if claimed is not None and not usable and misfit_notes:
        return {**base, "verdict": "cant_check", "found": "scan of another plan", "source": None,
                "reason": f"Listing says {claimed_txt}. {misfit_notes[0]}"}
    label_notes: list[str] = []
    if claimed is not None and kind in ("bedrooms", "bathrooms"):
        usable, label_notes = _vet_labels(kind, claimed, usable)
    if claimed is not None and not usable and label_notes:
        return {**base, "verdict": "cant_check", "found": "room labels only", "source": None,
                "reason": f"Listing says {claimed_txt}. {label_notes[0]}"}
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
            # A scan measures the scanned interior floor; the advertised size is the whole unit (see _scan_band).
            verdict = _scan_band(found, claimed)
            how = (f"The 3D scan measures {found_txt} of floor{diff}; scans usually measure about a tenth less "
                   "than the advertised size.")
        else:
            verdict = _written_band(found, claimed)
            if titled:
                found_txt = f"{phrase(kind, found)} (scan title)"
                how = f"The 3D scan of this plan is titled '{title}', which gives {phrase(kind, found)}{diff}."
            else:
                how = f"{_cap(SOURCE_NAME[src])} measures {found_txt}{diff}."
        if kit_plan:
            how = f"According to the building record, its plan {kit_plan} has {found_txt}{diff}."
    else:
        gap = abs(found - claimed)
        if round(found) == round(claimed):
            verdict = "confirmed"
        elif (kind == "year_built" and gap <= 1) or (kind == "unit_count" and gap <= max(1.0, 0.02 * claimed)):
            verdict = "close"  # a year off, or a couple of percent of the units: records drift, not a red flag
        else:
            verdict = "contradicted"
        if verdict == "confirmed":
            how = f"{_cap(SOURCE_NAME[src])} agree{'s' if VERB[src] != 'say' else ''}: {found_txt}."
        else:
            how = f"{_cap(SOURCE_NAME[src])} {VERB[src]} {found_txt}."
        if verdict == "close":
            how = how[:-1] + ", which is close."
        if obs.get("count_from_title"):
            how = f"The 3D scan is titled '{str(obs.get('model_name') or '').strip()}', which gives {found_txt}."
        if kit_plan:
            how = f"According to the building record, its plan {kit_plan} has {found_txt}."
        if obs.get("label_note"):
            how += " " + str(obs["label_note"])
        for note in label_notes:
            how += " " + note
        if kind == "stories" and src == "aerial" and _num(obs.get("height_m")) is not None:
            how = (f"The measured roof height from Vexcel aerial imagery ({_n(_num(obs['height_m']))} m) means "
                   f"about {found_txt}.")
    if obs.get("plan_match"):
        how += " " + str(obs["plan_match"]).strip()
    for note in dict.fromkeys(misfit_notes):
        how += " " + note

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
