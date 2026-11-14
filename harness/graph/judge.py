"""judge(claim, evidences) -> Verdict. Pure Python, deterministic (docs/CONTRACT.md section 4)."""
from __future__ import annotations

import re
from typing import Any

from . import amenities, trust
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
        if label and (obs.get("unit_row") or obs.get("scan_of")):
            continue  # the page's own unit (its record row or scan) says nothing about a named floor plan
        if obs.get("kit_plan") and not label and not any_plan:
            continue  # one kit floor plan's numbers say nothing about an unnamed unit
        if label and plan and not _same_plan(str(plan), str(label)):
            continue  # evidence about another floor plan
        if label and src == "scan" and not plan:
            continue  # a scan not tied to this floor plan says nothing about it
        out.append(e)
    return out


def _specific(e: dict) -> bool:
    """Evidence about one floor plan, one unit or one scan (not the whole building)."""
    obs = e.get("observed") or {}
    return e.get("source") == "scan" or bool(obs.get("plan") or obs.get("kit_plan") or obs.get("floorplans")
                                             or obs.get("unit_row"))


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
            how = (f"the building record lists unit {obs.get('unit')} with {phrase(kind, v)}" if obs.get("unit_row")
                   else f"the building record's plan {name} has {phrase(kind, v)}" if name
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


PROPERTY_NICE = {"pool": "a pool", "hot_tub": "a hot tub", "sport_court": "a sport court",
                 "playground": "a playground", "solar": "solar panels"}
PROPERTY_MISS = {"pool": "it can miss rooftop and indoor pools", "hot_tub": "it can miss rooftop and indoor ones",
                 "solar": "small or flat panels can be missed"}


def property_reading(claim: dict, evidences: list[dict]) -> tuple[str, dict, dict] | None:
    """(key, entry, evidence) from Vexcel property data (aerial Evidence observed.property) for a pool, hot tub,
    sport court, playground or solar claim, when it gives a yes or no; else None."""
    key = trust.property_key(claim)
    if not key:
        return None
    for e in evidences or []:
        if not isinstance(e, dict) or e.get("source") != "aerial" or e.get("status") != "ok":
            continue
        prop = (e.get("observed") or {}).get("property")
        entry = prop.get(key) if isinstance(prop, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("present"), bool):
            return key, entry, e
    return None


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10}
_COUNT_NOUN = {"pool": r"(?:swimming\s+)?pools\b(?!\s*tables?)",
               "hot_tub": r"(?:hot\s?tubs|spas|jacuzzis|whirlpools)\b"}
_COUNT_STOP = {"with", "and", "or", "plus", "of", "in", "on", "at", "to", "the", "a", "an", "near", "by", "from",
               "bed", "beds", "bedroom", "bedrooms", "bath", "baths", "bathroom", "bathrooms", "story", "stories",
               "floor", "floors", "acre", "acres", "car", "cars", "level", "levels"}
COUNT_NAME = {"pool": ("pool", "pools"), "hot_tub": ("hot tub", "hot tubs")}


def claimed_count(claim: dict, key: str) -> int | None:
    """How many pools / hot tubs the claim states ('2 pools', 'two resort-style pools', '3 hot tubs'), 2 or more;
    else None. One is a presence claim, left to the presence rule."""
    noun = _COUNT_NOUN.get(key)
    if not noun:
        return None
    rx = re.compile(rf"\b(\d+|{'|'.join(_NUM_WORDS)})\s+((?:[a-z]+(?:-[a-z]+)*\s+){{0,3}}?){noun}", re.I)
    for text in (amenities.text_of(claim), str(claim.get("quote") or "")):
        for m in rx.finditer(text):
            if any(w.lower() in _COUNT_STOP for w in m.group(2).split()):
                continue
            raw = m.group(1).lower()
            n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
            if 2 <= n <= 20:
                return n
    return None


def count_reading(claim: dict, pr: tuple | None) -> tuple[int, int] | None:
    """(claimed, counted) when the claim states 2+ pools / hot tubs and Vexcel property data shows that feature
    present, stable over two survey years, with a count. Unstable or no count: None (the presence rule decides)."""
    if not pr or pr[0] not in COUNT_NAME:
        return None
    key, entry, _ = pr
    n = entry.get("count")
    if entry.get("present") is not True or entry.get("stable") is not True or isinstance(n, bool) \
            or not isinstance(n, (int, float)) or n < 1:
        return None
    want = claimed_count(claim, key)
    return (want, int(n)) if want else None


def _count_words(key: str, n: int) -> str:
    one, many = COUNT_NAME[key]
    return f"{n} {one if n == 1 else many}"


def _property_says(key: str, entry: dict, count: int | None = None) -> str:
    """'Vexcel property data shows a pool, about 1,900 sq ft' / 'Vexcel property data does not show a hot tub' /
    'Vexcel property data shows 2 pools' (count: the claim stated a number the data meets)."""
    thing = PROPERTY_NICE.get(key, key.replace("_", " "))
    if not entry.get("present"):
        return f"Vexcel property data does not show {thing}"
    if count is not None and key in COUNT_NAME:
        return f"Vexcel property data shows {_count_words(key, count)}"
    area = _num(entry.get("area_sqft"))
    size = f", about {_n(round(area))} sq ft" if key == "pool" and area and area > 0 else ""
    return f"Vexcel property data shows {thing}{size}"


def _property_unsure(entry: dict) -> str:
    return (" The two latest surveys do not agree on this, or only one exists, so it is less certain."
            if entry.get("stable") is False else "")


def _aerial_image(evidences: list[dict], prefer: dict | None = None) -> str | None:
    for e in [prefer] + [x for x in evidences or [] if isinstance(x, dict)]:
        if e and e.get("source") == "aerial" and e.get("status") == "ok" \
                and not (e.get("observed") or {}).get("outlined"):  # the outlined photo only for what it boxed
            img = _first_image(e)
            if img:
                return img
    return None


def _outlined_image(key: str | None, evidences: list[dict]) -> str | None:
    """The outlined top-down photo (run.outlined_photo: aerial Evidence observed.outlined) when it boxed this
    feature (pool, sport_court, solar). Callers use it only when property data or the photo look reports the
    feature present."""
    if not key:
        return None
    for e in evidences or []:
        if isinstance(e, dict) and e.get("source") == "aerial" and e.get("status") == "ok" \
                and key in ((e.get("observed") or {}).get("outlined") or []):
            img = _first_image(e)
            if img:
                return img
    return None


def judge_feature(claim: dict, evidences: list[dict], base: dict) -> dict:
    """Amenity / parking / laundry / view claims: the building record's amenity names, else presence answers from
    pictures, scan titles or Vexcel property data (soft checks). A "no" never makes a mismatch on its own."""
    quote = amenities.text_of(claim).strip()
    said = f"Listing says \u201c{quote}\u201d."
    named = record_names(claim, evidences)
    if named:
        return {**base, "verdict": "confirmed", "found": "Named in the building record", "source": "record",
                "reason": f"{said} The building record names it.", "image": None}
    pr = property_reading(claim, evidences)
    yes = bool(pr and pr[1].get("present"))
    no = bool(pr and not pr[1].get("present"))
    cnt = count_reading(claim, pr)
    if cnt and cnt[1] < cnt[0]:  # fewer than claimed: aerial counts miss rooftop / indoor ones, so never a mismatch
        return _count_close(claim, evidences, base, said, pr, cnt)
    met = cnt[1] if cnt else None  # a count at or above the claim (a neighbour's pool may be in it): confirmed
    p_says = _property_says(pr[0], pr[1], met) if pr else ""
    p_miss = PROPERTY_MISS.get(pr[0], "not seeing something proves little") if pr else ""
    mine = [e for e in evidences or [] if isinstance(e, dict)
            and claim.get("id") in ((e.get("observed") or {}).get("claim_ids") or [])]
    if not mine:
        if yes:
            return _property_confirmed(evidences, base, said, pr, None, count=met)
        if no:
            return {**base, "verdict": "cant_check", "found": "Not in Vexcel property data", "source": None,
                    "reason": f"{said} {p_says}, but {p_miss}, so we could not check it.{_property_unsure(pr[1])}"}
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
    also_p = (f" {p_says} as well." if yes else
              f" {p_says}, but {p_miss}; a sighting counts." if no else "")
    if out == "confirmed" and obs.get("how") == "scan_title":
        aerial = [amenities.outcome(e.get("observed") or {}) for e in mine if e.get("source") == "aerial"]
        also = (" The Vexcel aerial photos did not show it from above, so it may be indoors."
                if aerial and "confirmed" not in aerial and not yes else "")
        return {**base, "verdict": "confirmed", "found": f"3D scan titled '{title}'", "source": "scan",
                "reason": f"{said} The building's 3D scans include one titled '{title}', which matches this by "
                          f"name.{also}{also_p}", "image": _first_image(best)}
    if out == "confirmed":
        found = "Visible in the Vexcel aerial photo" if src == "aerial" else "Visible in the 3D scan photos"
        img = (_outlined_image(trust.VISION_TO_PROPERTY.get(str(obs.get("feature"))), evidences)
               if src == "aerial" else None) or _first_image(best)
        return {**base, "verdict": "confirmed", "found": found, "source": src, "image": img,
                "reason": f"{said} A vision model looked for {nice} in {where} and saw it (confidence {conf:.2f}). "
                          f"This is a picture check, not a measurement.{also_p}"}
    if yes:  # the property data saw it; the vision model did not (or could not tell)
        return _property_confirmed(evidences, base, said, pr, best if src == "aerial" else None,
                                   vision_missed=out == "close" and src == "aerial", count=met)
    if out == "close":
        found = "Not visible in the Vexcel aerial photo" if src == "aerial" else "Not visible in the 3D scan photos"
        why = ("it may be indoors or on a part of the site the photo does not show" if src == "aerial"
               else "the scan photos show only some rooms")
        p_note = f" {p_says} either, but {p_miss}." if no else ""
        return {**base, "verdict": "close", "found": found, "source": src, "image": _first_image(best),
                "reason": f"{said} A vision model looked for {nice} in {where} and did not see it "
                          f"(confidence {conf:.2f}). A photo cannot prove something is absent: {why}.{p_note}",
                "confidence": _feature_confidence(claim, evidences, best, mine, pr)}
    p_note = f" {p_says}, but {p_miss}." if no else ""
    if out == "skipped":
        return {**base, "verdict": "cant_check", "found": "not checked", "source": None,
                "reason": f"{said} {best.get('summary') or 'The photos were not checked.'}{p_note}"}
    return {**base, "verdict": "cant_check", "found": "unclear", "source": None,
            "reason": f"{said} The photos don't show this clearly (we looked for {nice} in {where}).{p_note}"}


def _property_confirmed(evidences: list[dict], base: dict, said: str, pr: tuple, vision: dict | None,
                        vision_missed: bool = False, count: int | None = None) -> dict:
    key, entry, _ = pr
    miss = (" A vision model did not see it on the aerial photos, but the property data counts as a sighting."
            if vision_missed else "")
    return {**base, "verdict": "confirmed", "found": "In Vexcel property data", "source": "aerial",
            "image": _outlined_image(key, evidences) or _aerial_image(evidences, vision),
            "reason": f"{said} {_property_says(key, entry, count)}.{miss}{_property_unsure(entry)}"}


def _count_close(claim: dict, evidences: list[dict], base: dict, said: str, pr: tuple, cnt: tuple) -> dict:
    """Vexcel property data (stable) counts fewer pools / hot tubs than the listing states: at most "close", with
    a low-to-medium confidence (trust.count_confidence). The feature is there, so the outlined photo may show."""
    key, entry, e = pr
    want, got = cnt
    return {**base, "verdict": "close", "found": f"{_count_words(key, got)} in Vexcel property data",
            "source": "aerial", "image": _outlined_image(key, evidences) or _aerial_image(evidences),
            "reason": f"{said} Vexcel property data shows {_count_words(key, got)}; {PROPERTY_MISS[key]}.",
            "confidence": trust.count_confidence(claim, e, key, entry, want, got)}


def _feature_confidence(claim: dict, evidences: list[dict], best: dict, mine: list[dict],
                        pr: tuple | None = None) -> dict:
    """Confidence on a "not seen" amenity verdict. An absence never outvotes a sighting or a naming (that is
    why such a verdict exists only when nothing saw or named it), so this score always stays modest. Vexcel
    property data saying no too is a second reading from the same source: it raises the aerial look's weight a
    little (trust "property_agrees") and shows as its own row, but never counts as an independent source."""
    rows = []
    for e in [best, *[x for x in mine if x is not best]]:
        p = (e.get("observed") or {}).get("presence")
        if p not in ("seen", "not_seen"):
            continue  # an unclear look says nothing either way
        if pr and e.get("source") == "aerial":
            e = {**e, "observed": {**(e.get("observed") or {}), "property_present": bool(pr[1].get("present"))}}
        rows.append({"source": e["source"], "says": "seen" if p == "seen" else "not seen",
                     "weight": trust.weight(e, claim), "agrees_with_listing": p == "seen",
                     "same_as_decider": p == "not_seen", "covers_listing": False})
    if pr:  # only a "no" reaches here (a "yes" confirms)
        rows.append({"source": "aerial", "says": "not in property data",
                     "weight": trust.weight(trust.property_evidence(pr[2], pr[0], pr[1]), claim),
                     "agrees_with_listing": False, "same_as_decider": True, "covers_listing": False})
    has_list = any(isinstance(e, dict) and e.get("source") == "record" and e.get("status") == "ok"
                   and isinstance((e.get("observed") or {}).get("amenities"), list) for e in evidences or [])
    if has_list:  # the record's amenity list does not name it (a naming would have confirmed it)
        rows.append({"source": "record", "says": "not named", "weight": trust.named_weight(claim, False),
                     "agrees_with_listing": False, "same_as_decider": True, "covers_listing": False})
    out = trust.confidence(trust.kind_key(claim), str(claim.get("kind")), None, None, best["source"], rows,
                           absence=True)
    if pr and best["source"] == "aerial":
        out["why"] = out["why"][:-1] + "; Vexcel property data does not show it either."
    return out


def _plan_numbers(fp: Any) -> dict:
    """A record floor plan list entry ({"beds", "baths", "sqft"}) as Evidence-style observed keys."""
    fp = fp if isinstance(fp, dict) else {}
    return {"bedrooms": fp.get("beds"), "bathrooms": fp.get("baths"), "area_sqft": fp.get("sqft")}


def _flag_implausible(e: dict, ref: dict) -> dict:
    """A scan whose own readings do not fit the record (trust.scan_implausible) comes back as a copy marked
    observed.implausible (trust's "implausible" factor); anything else unchanged."""
    if e.get("source") != "scan":
        return e
    note = trust.scan_implausible(e.get("observed") or {}, ref)
    return {**e, "observed": {**(e.get("observed") or {}), "implausible": note}} if note else e


def _flag_scans(usable: list[dict], evidences: list[dict]) -> list[dict]:
    """Each scan in `usable` checked against the building record for the same floor plan (or, for the page's own
    unit, its record row): see _flag_implausible."""
    recs = [x for x in evidences or [] if isinstance(x, dict) and x.get("source") == "record"
            and x.get("status") == "ok" and not (x.get("observed") or {}).get("plan_type")]
    out = []
    for e in usable:
        obs = e.get("observed") or {}
        ref = None
        if e.get("source") == "scan":
            plan = str(obs.get("plan") or "")
            for r in recs:
                ro = r.get("observed") or {}
                if (plan and ro.get("plan") and _same_plan(str(ro["plan"]), plan)) \
                        or (not plan and obs.get("scan_of") and ro.get("unit_row")):
                    ref = ro
                    break
        out.append(_flag_implausible(e, ref) if ref else e)
    return out


def _own_record(e: dict) -> bool:
    """The building record's numbers for this very floor plan or unit, not paired with it by its size (a pairing
    by size agrees with the listing's size by construction)."""
    obs = e.get("observed") or {}
    return (e.get("source") == "record" and bool(obs.get("plan") or obs.get("kit_plan") or obs.get("unit_row"))
            and not re.search(r"\bby size\b", str(obs.get("plan_match") or "")))


def _record_over_scan(kind: str, readings: list[tuple[dict, float, str]]) -> tuple[dict, dict, float] | None:
    """Size only: the listing and the building record for this plan agree (written tolerance) and only the 3D scan
    differs. A scan's floor area is measured differently from a listed living area, so the record decides and the
    claim is confirmed. Returns (record evidence, the differing scan, its value), else None. Every other source
    must back the listing too."""
    if kind != "area_sqft":
        return None
    scans = [(e, v) for e, v, alone in readings if e.get("source") == "scan" and alone != "confirmed"]
    rest = [(e, alone) for e, _, alone in readings if e.get("source") != "scan"]
    if not scans or not rest or any(alone != "confirmed" for _, alone in rest):
        return None
    rec = next((e for e, _ in rest if _own_record(e)), None)
    return (rec, scans[0][0], scans[0][1]) if rec else None


def _scan_differs_note(e: dict, v: float, claimed: float) -> str:
    title = str((e.get("observed") or {}).get("model_name") or "").strip()
    if title and title_sqft(title):
        return (f"The 3D scan's title gives {phrase('area_sqft', v)}, which differs; the building record for this "
                "plan matches the listing, so it decides here.")
    if v > claimed:
        return (f"The 3D scan measured {phrase('area_sqft', v)} of floor; a scan's floor area often includes more "
                "than the listed living area (such as a balcony or hallway), so the building record decides here.")
    return (f"The 3D scan measured {phrase('area_sqft', v)} of floor; scans usually measure less than the "
            "advertised size, so the building record decides here.")


def _unit_scan_value(kind: str, e: dict) -> tuple[float, str] | None:
    """(value, how) from a scan of this very home that is tied to no floor plan: bed/bath counts from its title
    only (never its room labels), size from its title or its measured floor. None when it gives nothing."""
    obs = e.get("observed") or {}
    if (e.get("source") != "scan" or e.get("status") != "ok" or obs.get("plan") or obs.get("example_for")
            or obs.get("feature_check") or obs.get("kit_plan")):
        return None
    title = str(obs.get("model_name") or "").strip()
    who = "the 3D scan of this home"
    if obs.get("scan_of") == "unit" and obs.get("unit"):
        who = f"the 3D scan of unit {obs['unit']}"
    elif obs.get("scan_of") == "plan":  # the unit's record plan's scan: the same layout, not this very home
        who = f"the 3D scan of this unit's floor plan{' ' + str(obs['unit_plan']) if obs.get('unit_plan') else ''}"
    if kind in ("bedrooms", "bathrooms"):
        v = _num(obs.get(kind)) if obs.get("counts_from") == "model_name" else title_count(kind, title)
        if v is None or not title:
            return None
        return v, f"{who} is titled '{title}', which gives {phrase(kind, v)}"
    ts = title_sqft(title) if title else None
    if ts:
        return ts, f"{who} is titled '{title}', which gives {phrase(kind, ts)}"
    v = _num(obs.get(kind))
    return (v, f"{who} measures {phrase(kind, v)} of floor") if v is not None else None


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
    for e in evidences or []:  # the building record's own row for the page's unit (run.Run.unit_record)
        obs = (e.get("observed") or {}) if isinstance(e, dict) else {}
        if obs.get("unit_row") and e.get("source") == "record" and e.get("status") == "ok":
            v = _num(obs.get(kind))
            if v is not None:
                says = obs.get("unit_says") or phrase(kind, v)
                cands.append((v, f"the building record lists unit {obs.get('unit')} as {says}", e, False))
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
    # A scan whose own readings do not fit the record for this home (its row, else the only plan) weighs little.
    row = next((c[2]["observed"] for c in cands if c[2].get("source") == "record"
                and (c[2].get("observed") or {}).get("unit_row")), None)
    only = next((c[2] for c in cands if c[2].get("source") == "record"
                 and isinstance((c[2].get("observed") or {}).get("floorplans"), list)), None)
    ref = row or (_plan_numbers(only["observed"]["floorplans"][0]) if only else None)
    if ref:
        cands = [(v, how, _flag_implausible(e, ref), floor) for v, how, e, floor in cands]
    # the fixed order: the record's row for this unit (the unit's own written numbers), this home's scan, the
    # unit's plan scan (the same layout, supporting only), the only record plan
    def order(c):
        e = c[2]
        obs = e.get("observed") or {}
        if e.get("source") == "scan":
            return 2 if obs.get("scan_of") == "plan" else 1
        return 0 if obs.get("unit_row") else 3
    cands = sorted(cands, key=order)
    items = [(c[2], c[0], _reading(kind, claimed, c[2], (c[0], c[3]))[1]) for c in cands]
    pick = _pick(claim, kind, claimed, items, cands[0][2])
    v, how, e, floor = next(c for c in cands if c[2] is pick)
    verdict = _reading(kind, claimed, e, (v, floor))[1]
    out = {**base, "verdict": verdict, "found": phrase(kind, v), "source": e["source"],
           "reason": f"Listing says {claimed_txt} for this home; {how}.",
           "image": _image(e, [e], evidences) if e["source"] == "scan" else None}
    if verdict in MISMATCH:
        out["confidence"] = _numeric_confidence(claim, kind, claimed, e, v, items)
    return out


# ---- source trust: the tiebreak when our sources disagree, and the confidence on a mismatch (trust.py) -----

MISMATCH = ("contradicted", "close")


def _reading(kind: str, claimed: float, e: dict, single: tuple[float, bool] | None = None) -> tuple[float, str]:
    """(value, verdict this evidence alone would give) by the same rules the judge uses. single: (value, measured
    scan floor) from the single-unit path, which reads a scan's title or measured floor itself."""
    obs, src = e.get("observed") or {}, e.get("source")
    if single is not None:
        v, floor = single
        if kind == "area_sqft":
            return v, (_scan_band(v, claimed) if floor else _written_band(v, claimed))
        return v, ("confirmed" if _same(v, claimed) else "contradicted")
    v = _num(obs.get(kind))
    if kind == "area_sqft":
        title = str(obs.get("model_name") or "").strip() if src == "scan" else ""
        titled = title_sqft(title) if title else None
        if titled:
            return titled, _written_band(titled, claimed)
        return v, (_scan_band(v, claimed) if src == "scan" else _written_band(v, claimed))
    gap = abs(v - claimed)
    if round(v) == round(claimed):
        return v, "confirmed"
    if (kind == "year_built" and gap <= 1) or (kind == "unit_count" and gap <= max(1.0, 0.02 * claimed)):
        return v, "close"
    return v, "contradicted"


def _says(kind: str, v: float) -> str:
    return phrase(kind, v)


def _alike(kind: str, a: float, b: float) -> bool:
    if kind == "area_sqft":
        return abs(a - b) <= 0.05 * max(a, b)
    return round(a) == round(b)


def _pick(claim: dict, kind: str, claimed: float, items: list[tuple[dict, float, str]], default: dict) -> dict:
    """The deciding evidence. items: (evidence, its value, the verdict it alone gives). When the values disagree,
    the evidence with the highest trust weight decides; a tie keeps `default` (the old fixed order). Nothing
    changes when the sources agree, only one speaks, or every one of them backs the listing."""
    if len({round(v, 1) for _, v, _ in items}) <= 1 or all(alone == "confirmed" for _, _, alone in items):
        return default
    order = [e for e, _, _ in items]
    first = next(i for i, e in enumerate(order) if e is default)
    ranked = sorted(range(len(order)), key=lambda i: (-trust.weight(order[i], claim), i != first,
                                                      RANK[order[i]["source"]], i))
    return order[ranked[0]]


def _numeric_confidence(claim: dict, kind: str, claimed: float, winner: dict, found: float,
                        items: list[tuple[dict, float, str]]) -> dict:
    """items: (evidence, value, verdict alone) for every evidence that could speak; winner among them."""
    rows = []
    for e, v, alone in items:
        obs = e.get("observed") or {}
        rng = obs.get("stories_range") if kind == "stories" else None
        covers = (isinstance(rng, list) and len(rng) == 2 and all(_num(x) is not None for x in rng)
                  and _num(rng[0]) <= claimed <= _num(rng[1]))
        says = _says(kind, v)
        if covers:
            says = f"about {says} ({_n(_num(rng[0]))} to {_n(_num(rng[1]))})"
        rows.append({"source": e["source"], "says": says, "weight": trust.weight(e, claim),
                     "agrees_with_listing": alone == "confirmed", "same_as_decider": _alike(kind, v, found),
                     "covers_listing": bool(covers), "_e": e})
    rows.sort(key=lambda r: r["_e"] is not winner)
    seen, out = set(), []
    for r in rows:  # one row per distinct (source, says): the same reading twice adds nothing
        k = (r["source"], r["says"])
        if k in seen:
            continue
        seen.add(k)
        r.pop("_e")
        out.append(r)
    small = None
    if kind == "stories" and claimed >= 10:
        small = "one or two floors on a tall building is a small difference"
    elif kind == "year_built":
        small = "records often differ by a year"
    elif kind == "unit_count":
        small = "records often differ by a few homes"
    return trust.confidence(trust.kind_key(claim), kind, claimed, found, winner["source"], out, small)


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

    if kind in UNIT_KINDS:
        usable = _flag_scans(usable, evidences)  # a scan far off the record for its plan weighs little (trust.py)
    deciders = usable
    if kind == "stories" and any(e["source"] in ("county", "record") for e in usable):
        # a low roof height is a rough floor guide (pitched roofs): it never decides against county or record
        deciders = [e for e in usable if not _low_roof(e)]
    first = sorted(deciders, key=lambda e: RANK[e["source"]])[0]  # the fixed order: stable, first found wins a tie
    readings = [(e, *_reading(kind, claimed, e)) for e in usable]
    winner = _pick(claim, kind, claimed, [r for r in readings if any(r[0] is d for d in deciders)], first)
    over = _record_over_scan(kind, readings)
    scan_note = None
    if over:  # the listing and the record for this plan agree; only the scan measured differently
        winner, scan_note = over[0], _scan_differs_note(over[1], over[2], claimed)
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
        if kind == "stories" and src == "aerial" and _low_roof(winner):
            if verdict == "contradicted":
                verdict = "close"  # a rough guide alone never makes a firm mismatch
            how = (f"Vexcel roof height ({_n(_num(obs['height_m']))} m) suggests about {found_txt}; on a low "
                   "building with a pitched roof this is only a rough guide.")
        elif kind == "stories" and src == "aerial" and _num(obs.get("height_m")) is not None:
            how = (f"The measured roof height from Vexcel aerial imagery ({_n(_num(obs['height_m']))} m) means "
                   f"about {found_txt}.")
        if _estimate_alone(kind, winner, found, usable) and verdict == "contradicted":
            verdict = "close"  # a count added up over several parcels never makes a firm mismatch on its own
            how = (f"County records give about {found_txt}, but that count is added up over several parcels, so it "
                   "is only an estimate.")
    if obs.get("plan_match"):
        how += " " + str(obs["plan_match"]).strip()
    for note in dict.fromkeys(misfit_notes):
        how += " " + note

    # Mention other sources briefly so the reader sees agreement or the disagreement we overruled.
    if scan_note:
        how += " " + scan_note
    others = []
    for e in usable:
        if e is winner or e["source"] == src or (scan_note and e["source"] == "scan"):
            continue
        v = _num(e["observed"][kind])
        num = str(int(v)) if kind == "year_built" else _n(v)
        also = "also " if v == found else ""
        if kind == "stories" and e["source"] == "aerial" and _num(e["observed"].get("height_m")) is not None:
            bit = f"Vexcel roof height {also}suggests about {phrase(kind, v)}"  # an estimate, not the photo
        else:
            bit = f"{SOURCE_NAME[e['source']]} {also}{VERB[e['source']]} {num}"
        if bit not in others:
            others.append(bit)
    extra = (" " + _cap("; ".join(others)) + ".") if others else ""
    reason = f"Listing says {claimed_txt}. {how}{extra}"
    image = _image(winner, usable, evidences) or (_image(first, usable, evidences) if winner is not first else None)
    out = {**base, "verdict": verdict, "found": found_txt, "source": src, "reason": reason, "image": image}
    if verdict in MISMATCH:
        out["confidence"] = _numeric_confidence(claim, kind, claimed, winner, found, readings)
        if _estimate_alone(kind, winner, found, usable):
            c = out["confidence"]
            c["score"] = min(c["score"], ESTIMATE_MAX_SCORE)
            c["band"] = trust.band(c["score"])
            c["why"] = c["why"][:-1] + "; the county count is an estimate added up over several parcels."
    return out


ESTIMATE_MAX_SCORE = 40  # an estimated county unit count alone: low confidence at most
ROUGH_FLOORS = 3  # a roof-height floor estimate this low is rough (a pitched roof reads as extra floors)


def _low_roof(e: dict) -> bool:
    """A floor count estimated from Vexcel roof height that comes out at ROUGH_FLOORS or fewer."""
    obs = e.get("observed") or {}
    n = _num(obs.get("stories"))
    return (e.get("source") == "aerial" and _num(obs.get("height_m")) is not None and n is not None
            and n <= ROUGH_FLOORS)


def _estimate_alone(kind: str, winner: dict, found: float, usable: list[dict]) -> bool:
    """The deciding evidence is a county unit count added up over several parcels (observed.units_estimated) and
    no other source with a firm count says the same."""
    if kind != "unit_count" or winner.get("source") != "county" or not (winner.get("observed") or {}).get(
            "units_estimated"):
        return False
    return not any(e is not winner and not (e.get("observed") or {}).get("units_estimated")
                   and _num(e["observed"].get(kind)) is not None and _alike(kind, _num(e["observed"][kind]), found)
                   for e in usable)