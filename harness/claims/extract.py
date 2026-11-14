"""Extract checkable claims from a listing page snapshot (CONTRACT 2.1 -> list of 2.2).

Two layers, deterministic first:
1. Regex over the page lines that belong to the clicked address, plus JSON-LD facts.
2. One ask_json call over a compact excerpt; its claims are post-checked in code.
If the model fails, the deterministic claims are returned. Site-agnostic: no selectors, no site names.
"""
from __future__ import annotations

import json
import re
from typing import Any

NUMERIC_KINDS = ("bedrooms", "bathrooms", "area_sqft", "stories", "year_built", "unit_count")
KINDS = NUMERIC_KINDS + ("amenity", "parking", "laundry", "view", "other")
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
# Claims read from page metadata (JSON-LD) carry "origin": METADATA; their quote is a plain phrase, not page text.
METADATA = "metadata"
MAX_CLAIMS = 40
PROMPT_BUDGET = 6000

# ---------------------------------------------------------------- addresses and page regions

SUFFIX = (r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway|Ct|Court|"
          r"Pl|Place|Ter|Terrace|Cir|Circle|Hwy|Highway|Trl|Trail|Bend|Pike|Row)")
ADDR_RE = re.compile(rf"(?<![\d,.$])\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9][\w.']*\s+){{1,3}}{SUFFIX}\b")
OTHER_LISTINGS_RE = re.compile(
    r"\b(similar|nearby|you may (also )?like|recently viewed|recommended|other (listings|properties|homes|apartments)|"
    r"more (homes|listings|apartments)|people also)\b", re.I)
ZONE_MAX_LINES = 60
NEAR_FOREIGN = 4


def _self_re(address_text: str) -> re.Pattern | None:
    m = re.match(r"\s*(\d+[A-Za-z]?)\s+(?:(?:[NSEW]|North|South|East|West)\.?\s+)?([A-Za-z0-9]+)", address_text or "")
    if not m:
        return None
    num, name = re.escape(m.group(1)), re.escape(m.group(2))
    return re.compile(rf"(?<![\d,.]){num}\s+(?:(?:[NSEW]|North|South|East|West)\.?\s+)?{name}\b", re.I)


def _line_roles(lines: list[str], self_re: re.Pattern | None) -> tuple[list[int], list[bool]]:
    """Return (self address line numbers, excluded flags). Excluded = belongs to another listing."""
    self_lines, foreign_lines = [], []
    for i, ln in enumerate(lines):
        if self_re and self_re.search(ln):
            self_lines.append(i)
        elif any(not (self_re and self_re.search(m.group(0))) for m in ADDR_RE.finditer(ln)):
            foreign_lines.append(i)
    excluded = [False] * len(lines)
    # 1. lines closer to another listing's address than to ours
    for i in range(len(lines)):
        df = min((abs(i - f) for f in foreign_lines), default=10**9)
        ds = min((abs(i - s) for s in self_lines), default=10**9)
        if df <= NEAR_FOREIGN and df < ds:
            excluded[i] = True
    # 2. "similar / nearby / recently viewed" zones, until our address shows up again
    selfset = set(self_lines)
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if len(ln) <= 60 and OTHER_LISTINGS_RE.search(ln) and i not in selfset:
            j = i
            while j < len(lines) and j - i < ZONE_MAX_LINES and j not in selfset:
                excluded[j] = True
                j += 1
            i = j
        else:
            i += 1
    return self_lines, excluded


# ---------------------------------------------------------------- numbers

WORD_NUM = {"studio": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def to_number(v: Any) -> int | float | None:
    """'Studio' -> 0, '1,100 sq ft' -> 1100, '5-story' -> 5, 2.0 -> 2, 'two' -> 2. None if no number."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = str(v or "").strip().lower()
        m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
        if m:
            n = float(m.group(0).replace(",", ""))
        else:
            w = re.match(r"([a-z]+)", s)
            if not w or w.group(1) not in WORD_NUM:
                return None
            n = float(WORD_NUM[w.group(1)])
    return int(n) if n == int(n) else n


# ---------------------------------------------------------------- regex layer

NOT_AFTER = r"(?!\s*(?:apartments|houses|homes|rentals|condos|townhomes|units)\b)"
NOT_RANGE = r"(?![\d.,])(?!\s*(?:-|–|to)\s*\d)"
SQFT_NUM = r"(\d{1,2},\d{3}|\d{3,5})"
SQFT_WORD = r"(?:sq\.?\s?ft\.?|sqft|square\s+feet|sf)(?![a-z])"
# label-colon layouts ("Bedroom: 1", "SQFT: 559-603", "Sq. Ft.: 700"); the label may end a line with the
# value on the next one, so lines ending in such a label are matched joined with the next line.
SPEC_LABEL = (r"(?:bed(?:room)?s?|bath(?:room)?s?|sq\.?\s*ft\.?|sqft|square\s+(?:feet|footage))")
SPEC_COLON_RE = re.compile(rf"\b{SPEC_LABEL}\s*:", re.I)
SPEC_TAIL_RE = re.compile(rf"\b{SPEC_LABEL}\s*:\s*$", re.I)
# A pattern with a second group reads a sq ft range: the claim takes the low end, the quote keeps both ends.
UNIT_PATTERNS = [
    ("bedrooms", re.compile(rf"(?<![\d,.])(\d{{1,2}}(?:\.\d)?)\s*(?:bd|bds|bdrms?|beds?|bedrooms?|br)\b{NOT_AFTER}", re.I)),
    ("bedrooms", re.compile(rf"\b(?:bedrooms?|beds?)\s*:\s*(\d{{1,2}}|studio\b){NOT_RANGE}", re.I)),
    ("bathrooms", re.compile(rf"(?<![\d,.])(\d{{1,2}}(?:\.\d)?)\s*(?:ba|baths?|bathrooms?)\b{NOT_AFTER}", re.I)),
    ("bathrooms", re.compile(rf"\b(?:bathrooms?|baths?)\s*:\s*(\d{{1,2}}(?:\.\d)?){NOT_RANGE}", re.I)),
    ("area_sqft", re.compile(rf"(?<![\d,.]){SQFT_NUM}\s*{SQFT_WORD}", re.I)),
    ("area_sqft", re.compile(rf"(?<![\d,.]){SQFT_NUM}\s*(?:-|–|to)\s*{SQFT_NUM}\s*{SQFT_WORD}", re.I)),
    ("area_sqft", re.compile(r"\b(?:sq\.?\s*ft\.?|sqft|square\s+(?:feet|footage))\s*:\s*"
                             rf"{SQFT_NUM}(?:\s*(?:-|–|to)\s*{SQFT_NUM})?(?![\d,])", re.I)),
]
STUDIO_RE = re.compile(r"\bstudio\b(?!\s*[-–])", re.I)
BUILDING_PATTERNS = [
    ("stories", re.compile(r"(?<![\d,.])(\d{1,3})[\s-]*(?:story|stories|storey|storeys)\b", re.I)),
    ("stories", re.compile(r"\b(?:stories|floors)\s*:\s*(\d{1,3})\b", re.I)),
    ("year_built", re.compile(r"\b(?:built|constructed|completed|year built)(?:\s+in)?\s*:?\s*((?:18|19|20)\d\d)\b", re.I)),
    ("unit_count", re.compile(r"(?<![\d,.$])(\d{1,4})[\s-]*(?:units?|residences|apartment homes|apartments)\b"
                              r"(?!\s*(?:available|left|for rent|remaining))", re.I)),
    ("unit_count", re.compile(r"\b(?:units|number of units|total units)\s*:\s*(\d{1,4})\b", re.I)),
]
RANGE_BEFORE = re.compile(r"(?:\d|studio)\s*(?:-|–|to)\s*$", re.I)
# (pattern, kind, scope, canonical value)
AMENITY_PATTERNS = [
    (r"(?:resort-style |swimming |heated )?pool\b", "amenity", "building", "pool"),
    (r"(?:24-hour )?fitness center|\bgym\b", "amenity", "building", "fitness center"),
    (r"rooftop (?:terrace|deck|lounge|pool)", "amenity", "building", "rooftop terrace"),
    (r"dog park", "amenity", "building", "dog park"),
    (r"\belevators?\b", "amenity", "building", "elevator"),
    (r"(?:attached |detached |controlled-access |covered )?garage(?: parking)?|parking garage|covered parking",
     "parking", "building", "garage parking"),
    (r"in-unit (?:washer and dryer|washer/dryer|laundry)|laundry:\s*in unit|washer and dryer", "laundry", "unit",
     "in-unit laundry"),
    (r"(?:private )?balcon(?:y|ies)", "amenity", "unit", "balcony"),
    (r"(?:skyline|city|lake|water|downtown|park) views?", "view", "unit", "view"),
]
AMENITY_RES = [(re.compile(p, re.I), k, sc, canon) for p, k, sc, canon in AMENITY_PATTERNS]
PLAN_RE = re.compile(r"^\s*((?i:floor\s?plan|plan|unit|model|residence|layout|apt\.?|apartment)\s+#?"
                     r"(?:[A-Z]{1,2}\d{0,3}|\d{1,4}[A-Z]?))\b")
PLAN_BLOCK_MAX = 6
# A bare plan name ("B", "The Birch", "A2") on its own line, right above label-colon spec lines.
NAME_RE = re.compile(r"^[A-Za-z0-9][\w .'#&/+-]{0,23}$")
NOT_NAME_RE = re.compile(r"\b(studios?|bed|beds|bedrooms?|bath|baths|bathrooms?|sq|sqft|feet|floor ?plans?|plans|"
                         r"units|all|tour|view|explore|schedule|apply|availab\w*|details?|features?|facts|overview|"
                         r"interior|exterior|summary|info|specs?|about|amenit\w*|price|rent|contact|home|menu)\b", re.I)
# A group heading over plan cards ("Studio", "One Bedroom", "2 Bedrooms"); gives bedrooms to the cards under it.
GROUP_RE = re.compile(r"^\s*(?:(studios?)|(\d|one|two|three|four|five)[\s-]*(?:bed(?:room)?s?|br|bd))\s*$", re.I)


def _next_nonempty(lines: list[str], i: int, n: int) -> list[int]:
    out = []
    j = i + 1
    while j < len(lines) and len(out) < n:
        if lines[j].strip():
            out.append(j)
        j += 1
    return out


def _plan_blocks(lines: list[str]) -> tuple[list[str | None], dict[int, tuple[int, str, int]]]:
    """Floor plan / unit label each line belongs to (table row on one line, or a card over a few lines),
    plus {block start line: (bedrooms, heading quote, heading line)} for bare-name cards under a group heading."""
    named = [i for i, ln in enumerate(lines)
             if NAME_RE.match(ln.strip()) and not NOT_NAME_RE.search(ln) and not SPEC_COLON_RE.search(ln)
             and any(SPEC_COLON_RE.search(lines[j]) for j in _next_nonempty(lines, i, 2))]
    named = set(named) if len(named) >= 2 else set()  # plan cards repeat; one hit is a single-home fact sheet
    labels: list[str | None] = [None] * len(lines)
    headed: dict[int, tuple[int, str, int]] = {}
    current, start, heading = None, 0, None
    for i, ln in enumerate(lines):
        m = PLAN_RE.match(ln)
        g = GROUP_RE.match(ln)
        if g and (nxt := _next_nonempty(lines, i, 1)) and nxt[0] in named:
            heading = (0 if g.group(1) else to_number(g.group(2)), ln.strip(), i)
        if m:
            current, start = m.group(1).strip(), i
        elif i in named:
            current, start = ln.strip(), i
            if heading:
                headed[i] = heading
        elif current and (not ln.strip() or i - start >= PLAN_BLOCK_MAX or g):
            current = None
        labels[i] = current
    return labels, headed


def _plan_labels(lines: list[str]) -> list[str | None]:
    return _plan_blocks(lines)[0]


def _claim(kind, value, scope, label, quote, checkable=True, line=-1) -> dict:
    return {"kind": kind, "value": value, "scope": scope, "label": label,
            "quote": quote.strip()[:120], "checkable": checkable, "_line": line}


def _regex_claims(lines: list[str], excluded: list[bool], self_lines: list[int]) -> list[dict]:
    labels, headed = _plan_blocks(lines)
    out: list[dict] = []
    for i, ln in enumerate(lines):
        if excluded[i] or not ln.strip():
            continue
        label = labels[i]
        unit_ln = ln
        if SPEC_TAIL_RE.search(ln) and i + 1 < len(lines) and not excluded[i + 1]:
            unit_ln = f"{ln.rstrip()} {lines[i + 1].strip()}"  # "SQFT:" / "559-603" on two lines
        unit_hits = []
        for kind, rx in UNIT_PATTERNS:
            for m in rx.finditer(unit_ln):
                before = unit_ln[:m.start()]
                if RANGE_BEFORE.search(before) or SPEC_TAIL_RE.search(before):
                    continue  # part of a range, or the value of another label ("Bedrooms: 2 Bathrooms: 1")
                if kind == "area_sqft" and re.search(r"\blot\b|\bacres?\b", unit_ln, re.I):
                    continue
                if (rx.groups or 0) >= 2 and m.group(2):
                    lo, hi = to_number(m.group(1)), to_number(m.group(2))
                    if not label or lo is None or hi is None or lo > hi:
                        continue  # a range not tied to one plan describes the whole building: no unit claim
                unit_hits.append(_claim(kind, to_number(m.group(1)), "unit", label, m.group(0), line=i))
        # "Studio" counts as 0 bedrooms only inside a plan block or next to other unit numbers on the line
        sm = STUDIO_RE.search(ln)
        if sm and (label or unit_hits) and not RANGE_BEFORE.search(ln[:sm.start()]):
            unit_hits.append(_claim("bedrooms", 0, "unit", label, sm.group(0), line=i))
        out += unit_hits
        for kind, rx in BUILDING_PATTERNS:
            for m in rx.finditer(ln):
                if RANGE_BEFORE.search(ln[:m.start()]):
                    continue
                v = to_number(m.group(1))
                if kind == "unit_count" and (label or v is None or v < 2):
                    continue
                out.append(_claim(kind, v, "building", None, m.group(0), line=i))
        for rx, kind, scope, canon in AMENITY_RES:
            m = rx.search(ln)
            if m:
                out.append(_claim(kind, canon, scope, None, m.group(0), line=i))
    # cards under a "Studio" / "One Bedroom" heading that do not state bedrooms themselves
    has_beds = {c["label"] for c in out if c["kind"] == "bedrooms" and c["label"]}
    for start, (beds, quote, hline) in headed.items():
        if labels[start] not in has_beds and not excluded[start] and not excluded[hline] and beds is not None:
            has_beds.add(labels[start])
            out.append(_claim("bedrooms", beds, "unit", labels[start], quote, line=start))
    out.sort(key=lambda c: c["_line"])  # page order (stable within a line)
    return _resolve_unlabeled(out, self_lines)


def _resolve_unlabeled(claims: list[dict], self_lines: list[int]) -> list[dict]:
    """Per numeric kind: drop unlabeled unit numbers when plan-labeled ones exist; otherwise keep the
    value found closest to the clicked address (other values are usually page clutter)."""
    def dist(c):
        return min((abs(c["_line"] - s) for s in self_lines), default=c["_line"])
    keep = [c for c in claims if c["kind"] not in NUMERIC_KINDS or c["label"]]
    for kind in NUMERIC_KINDS:
        unl = [c for c in claims if c["kind"] == kind and not c["label"]]
        if not unl:
            continue
        if kind in UNIT_KINDS and any(c["kind"] == kind and c["label"] for c in claims):
            continue
        best = min(unl, key=dist)
        keep.append(best)
    return keep


# ---------------------------------------------------------------- JSON-LD layer

SKIP_TYPES = {"ItemList", "BreadcrumbList", "Organization", "WebSite", "WebPage", "SearchAction", "ListItem",
              "Offer", "AggregateOffer", "AggregateRating", "Review", "Person", "ImageObject", "VideoObject",
              "RealEstateAgent", "LocalBusiness", "SiteNavigationElement"}
CHILD_KEYS = ("containsPlace", "accommodationFloorPlan", "floorPlan", "mainEntity", "about", "@graph")


def _types(o: dict) -> set[str]:
    t = o.get("@type")
    return set(t) if isinstance(t, list) else {t} if isinstance(t, str) else set()


def _street(o: dict) -> str:
    a = o.get("address")
    if isinstance(a, dict):
        return str(a.get("streetAddress") or "")
    return str(a or "")


def _qv(v: Any) -> tuple[Any, str]:
    """QuantitativeValue or plain -> (number, unit code)."""
    if isinstance(v, dict):
        return to_number(v.get("value")), str(v.get("unitCode") or v.get("unitText") or "")
    return to_number(v), ""


def _one(v: Any) -> tuple[Any, str]:
    """One unit's value, or (None, "") when the page lists several: a complex's numberOfRooms
    [0, 1, 2, 3] or {minValue: 1, maxValue: 3} is the kinds of home it offers, not a fact about one home."""
    if isinstance(v, list):
        got = {_qv(x) for x in v if _qv(x)[0] is not None}
        return got.pop() if len(got) == 1 else (None, "")
    if isinstance(v, dict) and v.get("value") is None and (v.get("minValue") is not None or v.get("maxValue") is not None):
        lo, hi = to_number(v.get("minValue")), to_number(v.get("maxValue"))
        return (lo, str(v.get("unitCode") or v.get("unitText") or "")) if lo is not None and lo == hi else (None, "")
    return _qv(v)


def _amenity_kind(name: str) -> tuple[str, str]:
    n = name.lower()
    if "parking" in n or "garage" in n:
        return "parking", "building"
    if "laundry" in n or "washer" in n:
        return "laundry", "unit"
    if "view" in n:
        return "view", "unit"
    return "amenity", "building"


def plain_quote(kind: str, value: Any) -> str:
    """What a renter would read for a claim found only in page metadata: '1 bedroom', '850 sq ft', 'Built 2019'."""
    if kind not in NUMERIC_KINDS:
        return str(value)
    n = value
    num = f"{n:,}" if isinstance(n, int) else f"{n:g}"
    s = "" if n == 1 else "s"
    if kind == "bedrooms":
        return "Studio" if n == 0 else f"{num} bedroom{s}"
    if kind == "bathrooms":
        return f"{num} bathroom{s}"
    if kind == "area_sqft":
        return f"{num} sq ft"
    if kind == "year_built":
        return f"Built {n}"
    if kind == "stories":
        return f"{num} {'story' if n == 1 else 'stories'}"
    return f"{num} unit{s}"  # unit_count


def _jsonld_from(o: dict, label: str | None, out: list[dict]) -> None:
    def add(kind, value, scope, lab=label):
        if value is not None:
            c = _claim(kind, value, scope, lab, plain_quote(kind, value))
            c["origin"] = METADATA
            out.append(c)

    key = "numberOfBedrooms" if o.get("numberOfBedrooms") is not None else "numberOfRooms"
    add("bedrooms", _one(o.get(key))[0], "unit")
    for key in ("numberOfBathroomsTotal", "numberOfFullBathrooms"):
        if o.get(key) is not None:
            add("bathrooms", _one(o[key])[0], "unit")
            break
    if o.get("floorSize") is not None:
        n, code = _one(o["floorSize"])
        if n is not None and code.upper() in ("MTK", "M2", "SQM"):
            n = round(n * 10.7639)
        add("area_sqft", n, "unit")
    if o.get("yearBuilt") is not None:
        add("year_built", to_number(o["yearBuilt"]), "building", None)
    for key in ("numberOfFloors", "numberOfStories"):
        if o.get(key) is not None:
            add("stories", to_number(o[key]), "building", None)
    if o.get("numberOfAccommodationUnits") is not None:
        n, _ = _qv(o["numberOfAccommodationUnits"])
        add("unit_count", n, "building", None)
    feats = o.get("amenityFeature") or []
    for f in feats if isinstance(feats, list) else [feats]:
        name = f.get("name") if isinstance(f, dict) else f
        if name and (not isinstance(f, dict) or f.get("value", True) not in (False, "false", "False")):
            kind, scope = _amenity_kind(str(name))
            add(kind, str(name)[:80], scope, None)


def _jsonld_claims(jsonld: Any, self_re: re.Pattern | None) -> list[dict]:
    out: list[dict] = []

    def walk(o: Any, label: str | None, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(o, list):
            for x in o:
                walk(x, label, depth + 1)
            return
        if not isinstance(o, dict):
            return
        if _types(o) & SKIP_TYPES:
            return
        street = _street(o)
        if street and self_re and not self_re.search(street):
            return  # another property
        if depth > 0 and o.get("name") and (_types(o) & {"FloorPlan", "Apartment", "Accommodation", "Room", "Suite"}):
            label = str(o["name"])[:60]
        _jsonld_from(o, label, out)
        for k in CHILD_KEYS:
            if k in o:
                walk(o[k], label, depth + 1)

    walk(jsonld or [], None, 0)
    return out


# ---------------------------------------------------------------- model layer

KEYWORD_RE = re.compile(r"\b(bed|bd|bath|ba|sq|sqft|feet|story|stories|floor|built|year|unit|amenit|pool|garage|"
                        r"parking|laundry|washer|view|balcon|gym|fitness|elevator|plan|studio|roof|luxury)", re.I)
NEAR_SELF = 8
FEATURE_HEAD_RE = re.compile(r"amenit|features|appliances|utilities included", re.I)


def prefilter(lines: list[str], excluded: list[bool], self_lines: list[int], budget: int = PROMPT_BUDGET) -> str:
    """Compact excerpt of the page about the clicked listing, for the model prompt."""
    picked, seen, size = [], set(), 0
    labels = _plan_labels(lines)
    in_list = 0  # lines left in an "Amenities" / "Features" list
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            in_list = 0
            continue
        if excluded[i]:
            continue
        if len(s) <= 60 and FEATURE_HEAD_RE.search(s):
            in_list = 30
        listed, in_list = in_list > 0, max(in_list - 1, 0)
        near = any(abs(i - j) <= NEAR_SELF for j in self_lines)
        keyword = KEYWORD_RE.search(s) and (re.search(r"\d|studio", s, re.I) or len(s) <= 80
                                            or any(rx.search(s) for rx, *_ in AMENITY_RES))
        plan_name = labels[i] is not None and s == labels[i]  # a bare plan name line ("B") above its specs
        spec_value = i > 0 and SPEC_TAIL_RE.search(lines[i - 1])  # "559-603" under "SQFT:"
        if not (near or keyword or (listed and len(s) <= 80) or PLAN_RE.match(s) or plan_name or spec_value):
            continue
        key = re.sub(r"\s+", " ", s.lower())
        if key in seen and labels[i] is None:  # plan cards repeat "Bathroom: 1"; keep each card whole
            continue
        seen.add(key)
        s = s[:600]
        if size + len(s) + 1 > budget:
            break
        picked.append(s)
        size += len(s) + 1
    return "\n".join(picked)


PROMPT = """Extract the claims this rental listing page makes about the property at: {address}
Page excerpt (nav, ads and other listings mostly removed; ignore any that remain):
<<<
{excerpt}
>>>
Return a JSON array. Each item:
{{"kind": "bedrooms|bathrooms|area_sqft|stories|year_built|unit_count|amenity|parking|laundry|view|other",
 "value": number for the first six kinds else a short string, "scope": "unit" or "building",
 "label": floor plan or unit name the claim belongs to, else null,
 "quote": exact words copied from the excerpt (max 120 chars), "checkable": false for pure marketing words}}
Rules: only this property. One bedrooms, bathrooms and area_sqft claim per floor plan, labelled. Studio = 0 bedrooms.
Skip prices, fees, schools, reviews, neighborhood text and ranges like "1-3 Beds". Max 40 items. JSON only."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def _norm_label(s: Any) -> str:
    return re.sub(r"^(floor\s?plan|plan|unit|model|residence|layout|apt\.?|apartment)\s+", "", _norm(s or ""))


def _post_check(items: Any, page_norm: str) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        quote = str(it.get("quote") or "").strip()[:120]
        if not quote or _norm(quote) not in page_norm:
            continue
        if kind in NUMERIC_KINDS:
            value = to_number(it.get("value"))
            if value is None:
                value = to_number(quote)
            if value is None:
                continue
        else:
            value = str(it.get("value") or quote)[:80]
        scope = it.get("scope") if it.get("scope") in ("unit", "building") else (
            "unit" if kind in UNIT_KINDS else "building")
        label = it.get("label")
        label = str(label).strip()[:60] if label not in (None, "", "null") else None
        out.append(_claim(kind, value, scope, label, quote, bool(it.get("checkable", True))))
    return out


def _ask_cached(prompt: str) -> Any:
    """ask_json, with the parsed reply cached under rc_cache/claims by prompt hash (repeat runs are instant)."""
    from harness.model.ask import ask_json  # imported lazily: keeps the deterministic path dependency-free
    import hashlib
    import os
    path = None
    if os.environ.get("RC_PRIVATE_DIR"):
        try:
            import rc_common
            path = rc_common.cache_dir("claims") / (hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32] + ".json")
            from tools import cachectl
            if cachectl.hit(path):
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path = None
    reply = ask_json(prompt)
    if path is not None:
        try:
            path.write_text(json.dumps(reply), encoding="utf-8")
        except OSError:
            pass
    return reply


def _model_claims(address: str, excerpt: str, page_norm: str) -> list[dict]:
    return _post_check(_ask_cached(PROMPT.format(address=address or "(unknown)", excerpt=excerpt)), page_norm)


# ---------------------------------------------------------------- merge and finish

def _key(c: dict) -> tuple:
    v = _norm(c["value"]) if isinstance(c["value"], str) else c["value"]
    return c["kind"], _norm_label(c["label"]), v


def _finish(groups: list[list[dict]]) -> list[dict]:
    seen, merged = set(), []
    for g in groups:
        for c in g:
            k = _key(c)
            if c["value"] is None or k in seen:
                continue
            seen.add(k)
            merged.append(c)
    rank = lambda c: (0 if c["kind"] in NUMERIC_KINDS and c["checkable"] else 1 if c["checkable"] else 2)
    merged.sort(key=rank)  # stable: page order kept within each rank
    out = []
    for n, c in enumerate(merged[:MAX_CLAIMS], 1):
        out.append({"id": f"c{n}", "kind": c["kind"], "value": c["value"], "unit": None,
                    "scope": c["scope"], "label": c["label"], "quote": c["quote"], "checkable": c["checkable"]})
        if c.get("origin"):
            out[-1]["origin"] = c["origin"]
    for c in out:
        if c["kind"] == "area_sqft":
            c["unit"] = "sqft"
    return out


def extract_claims(snapshot: dict, use_model: bool = True) -> list[dict]:
    """PageSnapshot -> list of Claim. Never raises on model failure: falls back to the deterministic layer."""
    text = str(snapshot.get("text") or "")[:60000]
    address = str(snapshot.get("address_text") or "")
    lines = text.splitlines()
    self_re = _self_re(address)
    self_lines, excluded = _line_roles(lines, self_re)
    regex = _regex_claims(lines, excluded, self_lines)
    jsonld = _jsonld_claims(snapshot.get("jsonld"), self_re)
    if not use_model:
        return _finish([regex, jsonld])
    try:
        model = _model_claims(address, prefilter(lines, excluded, self_lines), _norm(text))
    except Exception:  # model unavailable, timeout, bad JSON: deterministic claims still stand
        return _finish([regex, jsonld])
    # Regex claims fill only (kind, label) slots the model left empty, so model judgement wins on conflicts.
    covered = {(c["kind"], _norm_label(c["label"])) for c in model}
    covered_kinds = {c["kind"] for c in model}
    fill = [c for c in regex if (c["kind"], _norm_label(c["label"])) not in covered
            and (c["kind"] in NUMERIC_KINDS or c["kind"] not in covered_kinds)]
    return _finish([model, jsonld, fill])


# ---------------------------------------------------------------- validation

def validate_claim(c: Any) -> list[str]:
    """Problems with a Claim dict (CONTRACT 2.2); empty list when valid."""
    if not isinstance(c, dict):
        return ["not a dict"]
    errs = []
    want = {"id", "kind", "value", "unit", "scope", "label", "quote", "checkable"}
    if set(c) - {"origin"} != want:
        errs.append(f"keys {sorted((set(c) - {'origin'}) ^ want)} differ")
    if "origin" in c and c["origin"] != METADATA:
        errs.append(f"origin must be {METADATA!r} when present")
    if not (isinstance(c.get("id"), str) and re.fullmatch(r"c\d+", c["id"])):
        errs.append("id must be c<n>")
    if c.get("kind") not in KINDS:
        errs.append(f"bad kind {c.get('kind')!r}")
    v = c.get("value")
    if c.get("kind") in NUMERIC_KINDS:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errs.append("value must be a number for numeric kinds")
    elif not isinstance(v, str):
        errs.append("value must be a string for non-numeric kinds")
    if c.get("unit") is not None and not isinstance(c.get("unit"), str):
        errs.append("unit must be string or null")
    if c.get("scope") not in ("unit", "building"):
        errs.append("scope must be unit|building")
    if c.get("label") is not None and not isinstance(c.get("label"), str):
        errs.append("label must be string or null")
    q = c.get("quote")
    if not isinstance(q, str) or not q or len(q) > 120:
        errs.append("quote must be 1-120 chars")
    if not isinstance(c.get("checkable"), bool):
        errs.append("checkable must be bool")
    try:
        json.dumps(c)
    except (TypeError, ValueError):
        errs.append("not JSON-serializable")
    return errs
