"""Which source is worth more for which claim: the judge's tiebreak and the mismatch confidence score.

Pure functions, no I/O. Two parts, both meant to be read by a person:

  RELEVANCE  claim kind x source -> (base relevance 0..1, why). How well this kind of source can speak to this
             kind of claim at all.
  QUALITY    small named factors for how this particular piece of evidence got its answer (seen directly or
             estimated, this very home or the same plan, saw it or merely did not see it, how old, how much of
             the home it covers). Only attributes Evidence really carries are read.

weight = base relevance x every quality factor that applies. When our own sources disagree on a claim, the
judge takes the answer of the source with the highest weight (a tie keeps the old fixed order), never the
answer that happens to be closest to or furthest from the listing.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from . import amenities
from .plans import title_count, title_sqft

SOURCES = ("record", "scan", "aerial", "county")
NAME = {"record": "CoStar", "scan": "Matterport", "aerial": "Vexcel", "county": "County"}

# Feature claims are grouped by where the thing is: seen from above, or inside.
OUTDOOR = "outdoor_amenity"   # pool, rooftop deck, dog run, courts, courtyard, playground, parking
INDOOR = "indoor_amenity"     # in-unit laundry, finishes, balcony, fitness center, club room ...

RELEVANCE: dict[str, dict[str, tuple[float, str]]] = {
    "bedrooms": {
        "scan":   (0.90, "walks through the home and its title states the room count"),
        "record": (0.90, "lists each floor plan's bedrooms"),
        "county": (0.00, "does not list rooms per home"),
        "aerial": (0.00, "cannot see inside"),
    },
    "bathrooms": {
        "scan":   (0.90, "walks through the home and its title states the room count"),
        "record": (0.90, "lists each floor plan's bathrooms"),
        "county": (0.00, "does not list rooms per home"),
        "aerial": (0.00, "cannot see inside"),
    },
    "area_sqft": {
        "scan":   (0.85, "measures the floor of the scanned home"),
        "record": (0.85, "lists each floor plan's size"),
        "county": (0.10, "gives whole-building area, rarely one home's"),
        "aerial": (0.00, "cannot measure a home's inside"),
    },
    "stories": {
        "county": (0.90, "lists the floor count on the tax roll"),
        "record": (0.90, "lists the building's floor count"),
        "aerial": (0.60, "estimates floors from the measured roof height"),
        "scan":   (0.00, "covers one home, not the building"),
    },
    "year_built": {
        "county": (0.90, "records the year the building went up"),
        "record": (0.90, "records the year built"),
        "aerial": (0.00, "cannot date a building"),
        "scan":   (0.00, "cannot date a building"),
    },
    "unit_count": {
        "record": (0.90, "lists the number of homes"),
        "county": (0.90, "lists the number of homes on the parcel"),
        "aerial": (0.00, "cannot count homes"),
        "scan":   (0.00, "covers one home"),
    },
    OUTDOOR: {
        "aerial": (0.90, "photographs the site from above, where outdoor features show directly"),
        "record": (0.75, "names the building's amenities"),
        "scan":   (0.25, "covers rooms inside, rarely the grounds"),
        "county": (0.10, "rarely lists amenities"),
    },
    INDOOR: {
        "record": (0.85, "names the building's amenities"),
        "scan":   (0.75, "shows the rooms it scanned"),
        "aerial": (0.00, "cannot see inside"),
        "county": (0.00, "does not list amenities"),
    },
    "solar": {
        "aerial": (0.85, "property data from the aerial survey marks solar panels on the roofs"),
        "record": (0.10, "rarely mentions solar panels"),
        "county": (0.05, "rarely lists solar panels"),
        "scan":   (0.00, "covers rooms inside, not the roof"),
    },
    "view": {
        "scan":   (0.50, "shows what the windows look out on"),
        "aerial": (0.40, "shows what surrounds the building"),
        "record": (0.20, "rarely describes views"),
        "county": (0.00, "does not describe views"),
    },
    "other": {
        "record": (0.50, "describes the building in general"),
        "scan":   (0.40, "shows the rooms it scanned"),
        "aerial": (0.30, "shows the outside only"),
        "county": (0.20, "lists basic building facts"),
    },
}

# Quality factors: (multiplier, plain note). Each applies on top of the base relevance.
QUALITY: dict[str, tuple[float, str]] = {
    "estimated":       (0.90, "an estimate, not a direct reading"),
    "room_labels":     (0.70, "counted from room labels, which are often wrong"),
    "same_plan":       (0.95, "about the same floor plan, not this very home"),
    "building_level":  (0.80, "about the building, not this home"),
    "matched_by_size": (0.90, "paired with this plan by its size"),
    "stand_in":        (0.85, "a scan of a similar home standing in for this plan"),
    "absence":         (0.40, "did not see or name it, which proves little"),
    "partial":         (0.80, "covers only part of the home"),
    "older_5y":        (0.90, "more than 5 years old"),
    "older_10y":       (0.75, "more than 10 years old"),
    "unstable":        (0.80, "the two latest surveys do not agree, or only one exists"),
    "property_agrees": (1.10, "Vexcel property data says the same"),
    "property_differs": (0.80, "Vexcel property data says otherwise"),
    "units_estimated": (0.80, "a unit count added up over several parcels, an estimate"),
    "parcel_by_address": (0.90, "the county parcel was found by its address, not the map point"),
    "aerial_count":    (0.60, "a count from the aerial survey, which misses rooftop and indoor ones"),
    "implausible":     (0.30, "the scan's own readings do not fit the building record for this home"),
}
IMPLAUSIBLE_BEDS = 2       # room labels counting this many bedrooms more than the record: an implausible scan
IMPLAUSIBLE_AREA = 0.40    # a scan size more than this far off the record's (either way): an implausible scan
COUNT_SCORE_MAX = 60  # a lower aerial pool / hot tub count alone never reaches "high" confidence

# Outdoor features Vexcel property data marks yes / no per parcel: claim pattern -> its key in observed.property.
# Rooftop decks, parking, courtyards and dog runs are not in it (the vision model alone looks for those).
# A "spa" is a hot tub only where it is used as one: next to a pool ("pool and spa", "spa/hot tub"), with a tub
# word before it ("heated spa", "outdoor spa"), or as the whole claim ("Spa", "Two spas"). A wellness or day spa,
# "spa-like" / "spa-inspired" finishes, spa services and named spas ("Serenity Wellness Spa") are not.
_SPA = r"spas?\b(?![- ]?(?:inspired|like|style|quality|bath|tub|services?|treatments?|rooms?|suites?|menu))"
_TUB_WORDS = (r"heated|outdoor|outside|in-?ground|above-?ground|private|rooftop|poolside|pool-?side|bubbling|"
              r"built-?in|sparkling|hydrotherapy|jetted|relaxing|soaking|resort-?style")
_COUNT_WORDS = r"\d+|one|two|three|four|five|six|seven|eight|nine|ten"
HOT_TUB = (r"hot ?tubs?|jacuzzis?|whirlpools?"
           rf"|\bpools?\s*(?:,|and|&|/|\+|with|plus|or)\s*(?:an?\s+|(?:{_COUNT_WORDS})\s+)?"
           rf"(?:(?:{_TUB_WORDS})\s+)?{_SPA}"
           rf"|\b{_SPA}\s*(?:,|and|&|/|\+|or)\s*(?:an?\s+)?(?:(?:{_TUB_WORDS})\s+)?"
           r"(?:pools?\b(?!\s*tables?)|hot ?tubs?|jacuzzis?)"
           rf"|\b(?:{_TUB_WORDS})\s+{_SPA}"
           rf"|^\s*(?:(?:{_COUNT_WORDS})\s+)?{_SPA}\s*$")
PROPERTY_FEATURES = [
    (r"\bpools?\b(?!\s*tables?)", "pool"),  # plural too: "2 pools" (judge.claimed_count reads the number)
    (HOT_TUB, "hot_tub"),
    (r"(?:tennis|pickleball|basketball|sport|sports|volleyball) courts?|\btennis\b|\bpickleball\b", "sport_court"),
    (r"playground|play area|tot lot", "playground"),
    (r"\bsolar\b", "solar"),
]
_PROPERTY_FEATURES = [(re.compile(p, re.I), k) for p, k in PROPERTY_FEATURES]
VISION_TO_PROPERTY = {"pool": "pool", "sports_court": "sport_court", "playground": "playground"}
_VISION_TO_PROPERTY = VISION_TO_PROPERTY


def property_key(claim: dict) -> str | None:
    """The observed.property key that speaks to this claim (pool, hot_tub, sport_court, playground, solar), else
    None. A claim the vision model reads as another feature first (rooftop deck, parking ...) gets None."""
    if not amenities.is_feature_claim(claim) or not claim.get("checkable", True):
        return None
    feat = amenities.feature_for(claim)
    if feat:
        return _VISION_TO_PROPERTY.get(feat[0])
    words = amenities.text_of(claim)
    hits = [(m.start(), i, k) for i, (rx, k) in enumerate(_PROPERTY_FEATURES) for m in [rx.search(words)] if m]
    return min(hits)[2] if hits else None  # the first named; at the same place the earlier row (pool) wins

UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")


def kind_key(claim: dict) -> str:
    """The RELEVANCE row for a claim: its own kind, or the outdoor / indoor amenity group for feature claims."""
    kind = claim.get("kind")
    if kind in ("bedrooms", "bathrooms", "area_sqft", "stories", "year_built", "unit_count"):
        return kind
    feat = amenities.feature_for(claim)
    if feat:
        return OUTDOOR if feat[2] == "aerial" else INDOOR
    pk = property_key(claim)
    if pk:
        return "solar" if pk == "solar" else OUTDOOR  # hot tub: outdoors, seen from above
    if amenities.title_rooms(claim):
        return INDOOR  # fitness center, club room, lounge ...: rooms a scan or the record can name
    return "view" if kind == "view" else "other"


def relevance(key: str, source: str) -> tuple[float, str]:
    return RELEVANCE.get(key, RELEVANCE["other"]).get(source, (0.0, "says nothing about this"))


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _age_years(obs: dict, today: _dt.date | None) -> float | None:
    raw = str(obs.get("capture_date") or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return ((today or _dt.date.today()) - d).days / 365.25


def quality(e: dict, claim: dict, today: _dt.date | None = None) -> list[str]:
    """Names of the QUALITY factors that apply to this evidence for this claim (read from what it carries)."""
    obs = e.get("observed") or {}
    src, kind = e.get("source"), claim.get("kind")
    out = []
    if kind == "stories" and src == "aerial":
        out.append("estimated")  # floors from roof height
    if src == "scan" and kind in ("bedrooms", "bathrooms") and obs.get("counts_from") != "model_name" \
            and not obs.get("count_from_title"):
        tc = title_count(kind, str(obs.get("model_name") or ""))
        lab = _num(obs.get(kind))
        if tc is None or lab is None or round(tc) != round(lab):
            out.append("room_labels")  # labels the scan's own title does not back
    if src == "scan" and kind in UNIT_KINDS and obs.get("implausible") and not _title_reading(obs, kind):
        out.append("implausible")  # 5 bedroom labels in a 2-bed plan, or a size far off the record's
    if src == "county" and kind == "unit_count" and obs.get("units_estimated"):
        out.append("units_estimated")
    if src == "county" and obs.get("parcel_match") == "address":
        out.append("parcel_by_address")
    if kind in UNIT_KINDS:
        fps = obs.get("floorplans")
        if (src == "record" and obs.get("unit_row")) or (src == "scan" and obs.get("scan_of") == "unit"):
            pass  # the building record's row for this very unit, or this unit's own scan: no reduction
        elif src in ("scan", "record") and (obs.get("plan") or obs.get("kit_plan") or obs.get("example_for")
                                            or obs.get("scan_of") == "plan"):
            out.append("same_plan")
        elif src == "record" and isinstance(fps, list):
            out.append("same_plan" if len(fps) == 1 else "building_level")  # the only plan, or a list of plans
        elif src in ("county", "aerial"):
            out.append("building_level")
    note = str(obs.get("plan_match") or "")
    if re.search(r"\bby size\b", note):
        out.append("matched_by_size")
    if obs.get("example_for") or "no 3D scan of its own" in note:
        out.append("stand_in")
    if obs.get("feature_check") and obs.get("presence") == "not_seen":
        out.append("absence")
        if src == "scan":
            out.append("partial")  # scan photos show only some rooms
    if obs.get("how") == "property_data" and obs.get("stable") is False:
        out.append("unstable")  # the survey years disagree, or there is only one
    if obs.get("how") == "property_data" and obs.get("count") is not None:
        out.append("aerial_count")  # a count read from the survey: rooftop and indoor ones are missed
    pp = obs.get("property_present")
    if isinstance(pp, bool) and obs.get("how") != "property_data" and obs.get("presence") in ("seen", "not_seen"):
        out.append("property_agrees" if pp == (obs["presence"] == "seen") else "property_differs")
    age = _age_years(obs, today)
    if age is not None and age > 10:
        out.append("older_10y")
    elif age is not None and age > 5:
        out.append("older_5y")
    return out


def _title_reading(obs: dict, kind: str) -> bool:
    """The scan's bed / bath count for this kind came from its title (not its room labels)."""
    return kind in ("bedrooms", "bathrooms") and (obs.get("counts_from") == "model_name"
                                                  or bool(obs.get("count_from_title")))


def scan_implausible(scan_obs: dict, ref: dict) -> str | None:
    """A plain note when a scan's own readings do not fit the building record for the same home or plan (`ref`:
    the record's bedrooms / area_sqft): its room labels count IMPLAUSIBLE_BEDS or more bedrooms than the record,
    or its size (the title's when it states one, else the measured floor) is more than IMPLAUSIBLE_AREA off the
    record's. Such a scan gets the "implausible" quality factor, so it cannot decide alone against the record.
    None when it fits, or when there is nothing to compare."""
    notes = []
    lab, rb = _num(scan_obs.get("bedrooms")), _num(ref.get("bedrooms"))
    if lab is not None and rb is not None and scan_obs.get("counts_from") != "model_name" \
            and lab - rb >= IMPLAUSIBLE_BEDS:
        notes.append(f"its room labels count {lab:g} bedrooms where the building record has {rb:g}")
    title = str(scan_obs.get("model_name") or "").strip()
    area = (title_sqft(title) if title else None) or _num(scan_obs.get("area_sqft"))
    ra = _num(ref.get("area_sqft"))
    if area and ra and abs(area - ra) / ra > IMPLAUSIBLE_AREA:
        notes.append(f"it gives {area:,.0f} sq ft where the building record has {ra:,.0f}")
    return "; ".join(notes) or None


def property_evidence(e: dict | None, key: str, entry: dict) -> dict:
    """A property-data reading as Evidence-like dict for weight(): observed.how = "property_data", presence
    seen / not_seen, the pool's own confidence when it saw one, stable, and the survey date."""
    obs = {"feature_check": True, "how": "property_data", "feature": key,
           "presence": "seen" if entry.get("present") else "not_seen", "stable": entry.get("stable")}
    conf = _num(entry.get("confidence"))
    if entry.get("present") and conf is not None:
        obs["presence_confidence"] = conf
    prop = ((e or {}).get("observed") or {}).get("property") or {}
    if prop.get("captured"):
        obs["capture_date"] = prop["captured"]
    return {"source": "aerial", "status": "ok", "observed": obs}


def count_confidence(claim: dict, e: dict | None, key: str, entry: dict, claimed: float, found: float,
                     extra: list[dict] | None = None) -> dict:
    """Confidence on a "close" pool / hot tub count: Vexcel property data counts fewer than the listing says. The
    count reading decides (weight x "aerial_count"); `extra` rows (other sources, decider first excluded) can lower
    it; the score is capped at COUNT_SCORE_MAX so an aerial count alone stays low to medium."""
    pe = property_evidence(e, key, entry)
    pe["observed"]["count"] = found
    rows = [{"source": "aerial", "says": f"{int(found)} in property data", "weight": weight(pe, claim),
             "agrees_with_listing": False, "same_as_decider": True, "covers_listing": False}] + list(extra or [])
    out = confidence(kind_key(claim), str(claim.get("kind")), claimed, found, "aerial", rows)
    if out["score"] > COUNT_SCORE_MAX:
        out["score"] = COUNT_SCORE_MAX
        out["band"] = band(COUNT_SCORE_MAX)
    out["why"] = out["why"][:-1] + "; the count is from aerial survey data, which can miss rooftop and indoor ones."
    return out


def weight(e: dict, claim: dict, today: _dt.date | None = None) -> float:
    """Base relevance x quality factors, 0..1, rounded to 3 places (so equal evidence ties exactly)."""
    base, _ = relevance(kind_key(claim), str(e.get("source")))
    w = base
    for q in quality(e, claim, today):
        w *= QUALITY[q][0]
    obs = e.get("observed") or {}
    if obs.get("feature_check") and obs.get("presence") in ("seen", "not_seen"):
        conf = _num(obs.get("presence_confidence"))
        if conf is not None:
            w *= max(0.0, min(1.0, conf))  # the vision model's (or the property data's) own certainty
    return round(min(1.0, w), 3)


def named_weight(claim: dict, named: bool) -> float:
    """The building record's amenity list: naming the amenity is a direct statement; not naming it is absence."""
    base, _ = relevance(kind_key(claim), "record")
    return round(base * (1.0 if named else QUALITY["absence"][0]), 3)


# ---- confidence on a mismatch --------------------------------------------------------------------------

def gap_strength(kind: str, claimed: float | None, found: float | None) -> float:
    """0..1: how big the difference is for this kind of claim. One floor off a 50-floor tower is tiny; 723 vs
    1,184 sq ft is large; one bedroom off is large."""
    if claimed is None or found is None:
        return 0.3  # presence answers: no size to the gap
    d = abs(found - claimed)
    if kind in ("bedrooms", "bathrooms"):
        return 0.4 if d < 1 else 0.8 if d < 2 else 1.0
    if kind == "year_built":
        return 0.2 if d <= 1 else 0.5 if d < 5 else 0.8 if d < 10 else 1.0
    rel = d / claimed if claimed else 1.0
    scale = {"area_sqft": 0.4, "stories": 0.3, "unit_count": 0.25}.get(kind, 0.3)
    return min(1.0, rel / scale)


def band(score: int) -> str:
    return "high" if score >= 75 else "medium" if score >= 45 else "low"


def score(win_weight: float, gap: float, support: float, against: list[float]) -> int:
    """0..100. Rises with the deciding source's weight, with the size of the gap and with another independent
    source that also finds the listing off (support 0..1); falls for each relevant source that sides with the
    listing (its weight against the decider's)."""
    s = 100 * (0.3 + 0.7 * gap) * (0.55 + 0.3 * win_weight + 0.15 * support)
    for w in against:
        s -= 30 * min(1.0, w / win_weight) if win_weight > 0 else 30
    return int(round(max(0.0, min(100.0, s))))


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def confidence(key: str, kind: str, claimed: float | None, found: float | None, decided_by: str,
               sources: list[dict], small_note: str | None = None, absence: bool = False) -> dict:
    """The verdict's `confidence` block. sources: [{"source", "says", "weight", "agrees_with_listing",
    "same_as_decider": bool, "covers_listing": bool}] with the decider first; the two helper keys are dropped
    from the output."""
    win = sources[0]
    others = sources[1:]
    support, agree, against, siding = 0.0, [], [], []
    for s in others:
        if s["source"] == decided_by or s["weight"] <= 0:
            continue  # the same source twice is not independent; an irrelevant source does not count
        if s["agrees_with_listing"]:
            against.append(s["weight"])
            siding.append(NAME[s["source"]])
            continue
        part = (1.0 if s.get("same_as_decider") and not s.get("covers_listing") else 0.5) \
            * min(1.0, s["weight"] / 0.6)
        if part > support:
            support = part
        if s.get("same_as_decider") and not s.get("covers_listing") and NAME[s["source"]] not in agree:
            agree.append(NAME[s["source"]])
    gap = gap_strength(kind, claimed, found)
    sc = score(win["weight"], gap, support, against)
    _, why_src = relevance(key, decided_by)
    why = f"{NAME[decided_by]} {why_src}"
    if absence:
        why += ", but did not see it there; not seeing something proves little"
    if agree and absence:
        why += f"; {_join(agree)} did not show or name it either"
    elif agree:
        why += f"; {_join(agree)} agree{'s' if len(agree) == 1 else ''}"
    if siding:
        why += f"; {_join(sorted(set(siding)))} match{'es' if len(set(siding)) == 1 else ''} the listing, so we " \
               "are less sure"
    if small_note and gap < 0.3:
        why += f"; {small_note}"
    out_sources = [{"source": s["source"], "says": s["says"], "weight": round(float(s["weight"]), 2),
                    "agrees_with_listing": bool(s["agrees_with_listing"])} for s in sources]
    return {"score": sc, "band": band(sc), "why": why + ".", "decided_by": decided_by, "sources": out_sources}
