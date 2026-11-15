"""Extract checkable claims from a listing page snapshot (CONTRACT 2.1 -> list of 2.2).

Two layers, deterministic first:
1. Regex over the page lines that belong to the clicked address, plus JSON-LD facts.
2. One ask_json call over a compact excerpt; its claims are post-checked in code.
If the model fails, the deterministic claims are returned. Site-agnostic: no selectors, no site names.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

NUMERIC_KINDS = ("bedrooms", "bathrooms", "area_sqft", "stories", "year_built", "unit_count")
KINDS = NUMERIC_KINDS + ("amenity", "parking", "laundry", "view", "other")
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
# Claims read from page metadata (JSON-LD) carry "origin": METADATA; their quote is a plain phrase, not page text.
METADATA = "metadata"
MAX_CLAIMS = 40
PROMPT_BUDGET = 9000
# Bump whenever the prompt, the candidate rules or the post-model rules change: it keys the per-page claim cache.
EXTRACTOR_VERSION = "claims-2026-11-14.4"

# ---------------------------------------------------------------- addresses and page regions

SUFFIX = (r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway|Ct|Court|"
          r"Pl|Place|Ter|Terrace|Cir|Circle|Hwy|Highway|Trl|Trail|Bend|Pike|Row)")
ADDR_RE = re.compile(rf"(?<![\d,.$])\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9][\w.']*\s+){{1,3}}{SUFFIX}\b")
OTHER_LISTINGS_RE = re.compile(
    r"\b(similar|nearby|you may (also )?like|recently viewed|recommended|other (listings|properties|homes|apartments)|"
    r"more (homes|listings|apartments)|people also)\b", re.I)
ZONE_MAX_LINES = 60
NEAR_FOREIGN = 4
# Sections about the area or the market, not this listing: rent trends, schools, scores, nearby buildings. A short
# heading opens the zone; it runs until our address, a heading of the listing's own sections, or ZONE_MAX_LINES.
OFF_TOPIC_RE = re.compile(
    r"^\s*(?:rent(?:al)?\s+trends?|rent\s+(?:history|comparison)|price\s+(?:history|trends?)|market\s+trends?|"
    r"average\s+rents?|(?:nearby\s+|local\s+|assigned\s+)?schools?|school\s+(?:ratings?|district)|"
    r"walk\s?score|transit\s?score|bike\s?score|getting\s+around|transportation|commute|points\s+of\s+interest|"
    r"nearby\s+(?:buildings|properties|apartments|homes|rentals)|compare\b|comparable|"
    r"(?:about\s+the\s+)?neighbou?rhood)\b", re.I)
ON_TOPIC_RE = re.compile(
    r"^\s*(?:(?:building|community|unit|apartment|property)\s+)?(?:amenities|features|units|available\s+units|"
    r"floor\s?plans|pet\s+policy|parking|utilities|fees|lease\s+terms|details|overview|facts)\b", re.I)


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
        elif len(ln) <= 40 and OFF_TOPIC_RE.search(ln) and i not in selfset:
            j = i
            while j < len(lines) and j - i < ZONE_MAX_LINES and j not in selfset \
                    and not (j > i and len(lines[j].strip()) <= 40 and ON_TOPIC_RE.search(lines[j])):
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
SQFT_WORD = r"(?:sq\.?\s?ft\.?|sqft|square\s+feet|sf|ft²)(?![a-z])"
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
                              r"(?!\s*(?:now\s+)?(?:available|left|for rent|remaining))", re.I)),
    ("unit_count", re.compile(r"\b(?:units|number of units|total units)\s*:\s*(\d{1,4})\b", re.I)),
]
# Ranges over the whole building ("BEDS Studios–2", "Studio - 2 Beds", "1–2 Baths", "SQFT 475–966", "475 to 966 Sq
# Ft"): a building-scope claim per kind, value = the low end, value_max = the high end (Studio = 0 bedrooms). Only
# outside a plan card or unit row; inside one, a sq ft range stays that plan's size (its low end, as before).
RANGE_DASH = r"\s*(?:-|–|—|to)\s*"
BED_WORD = r"(?:bd|bds|bdrms?|beds?|bedrooms?|br)"
BATH_WORD = r"(?:ba|baths?|bathrooms?)"
RANGE_PATTERNS = [
    ("bedrooms", re.compile(rf"(?<![\d,.])(studios?|\d)(?:\s*{BED_WORD})?{RANGE_DASH}(\d)\s*{BED_WORD}\b", re.I)),
    ("bedrooms", re.compile(rf"\b(?:beds?|bedrooms?)\s*:?\s*(studios?|\d){RANGE_DASH}(\d)(?![\d.,])", re.I)),
    ("bathrooms", re.compile(rf"(?<![\d,.])(\d(?:\.\d)?)(?:\s*{BATH_WORD})?{RANGE_DASH}(\d(?:\.\d)?)\s*{BATH_WORD}\b",
                             re.I)),
    ("bathrooms", re.compile(rf"\b(?:baths?|bathrooms?)\s*:?\s*(\d(?:\.\d)?){RANGE_DASH}(\d(?:\.\d)?)(?![\d.,])", re.I)),
    ("area_sqft", re.compile(rf"(?<![\d,.]){SQFT_NUM}(?:\s*{SQFT_WORD})?{RANGE_DASH}{SQFT_NUM}\s*{SQFT_WORD}", re.I)),
    ("area_sqft", re.compile(r"\b(?:sq\.?\s*ft\.?|sqft|square\s+(?:feet|footage))\s*:?\s*"
                             rf"{SQFT_NUM}{RANGE_DASH}{SQFT_NUM}(?![\d,])", re.I)),
]
RANGE_TOP = {"bedrooms": 8, "bathrooms": 8, "area_sqft": 20000}
# a line that is only a spec label ("BEDS", "Sq Ft:"): its value is on the next line
BARE_LABEL_RE = re.compile(r"^\s*(?:beds?|bedrooms?|baths?|bathrooms?|sq\.?\s*ft\.?|sqft|square\s+(?:feet|footage))"
                           r"\s*:?\s*$", re.I)


def ranges_in(s: str) -> dict[str, tuple]:
    """{kind: (low, high, quote, (start, end))} for the bed / bath / sq ft ranges one text states (first per kind)."""
    got: dict[str, tuple] = {}
    for kind, rx in RANGE_PATTERNS:
        if kind in got or (kind == "area_sqft" and re.search(r"\blot\b|\bacres?\b", s, re.I)):
            continue
        for m in rx.finditer(s):
            lo = 0 if m.group(1).lower().startswith("studio") else to_number(m.group(1))
            hi = to_number(m.group(2))
            if lo is None or hi is None or not lo < hi <= RANGE_TOP[kind]:
                continue
            got[kind] = (lo, hi, m.group(0), m.span())
            break
    return got


def is_range(claim: Any) -> bool:
    """A building-wide range claim ("Studios–2 beds"): value is the low end, value_max the high end."""
    return isinstance(claim, dict) and claim.get("value_max") is not None


RANGE_BEFORE = re.compile(r"(?:\d|studio)\s*(?:-|–|to)\s*$", re.I)
# A statement with one of these words counts homes for rent, not the building's total ("11 Units Available",
# "11 unit Rentals", "Showing 11 units"): no unit_count claim from it.
AVAIL_RE = re.compile(r"\b(?:availab\w*|for\s+(?:rent|lease)|left|remaining|vacan\w*|listings?|listed|rentals?|"
                      r"showing|results?|matching|found)\b", re.I)
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


# ---- unit rows: a table of the building's apartments, one row per unit ("1204" / "#101" / "418-367" / "Unit 1204",
# then its beds, baths, sq ft and amenities). Every line of a row is labelled "Unit <id>"; a row with no id is
# labelled by its own facts ("2 bed, 1.5 bath, 1,264 sq ft"), never by an invented number.
UNIT_WORD = r"(?:unit|apt\.?|apartment)"
ROW_ID = r"(\d{1,6}-\d{1,5}[A-Za-z]?|\d{1,6}[A-Za-z]?|[A-Za-z]-?\d{1,4})"
BARE_ID_RE = re.compile(rf"^\s*((?i:{UNIT_WORD})\s*#?\s*|#\s?)?{ROW_ID}\s*$")
# a row on one line: "1204 | $1,100 | 1 Bed | 1 Bath | 648 sq ft" (the id then a price or a number)
ROW_LEAD_RE = re.compile(rf"^\s*((?i:{UNIT_WORD})\s*#?\s*|#\s?)?{ROW_ID}(?:\s*[\t|·•]\s*|\s+)(?=\$|\d|(?i:studio))")
UNIT_PLAN_RE = re.compile(rf"^\s*(?i:{UNIT_WORD})\s*#?\s*(\S+)$")  # a PLAN_RE label that names a unit
SPEC_WORD_START_RE = re.compile(r"^\s*(?:sq|sqft|sf\b|ft|square|bed|bd|br\b|bath|ba\b|units?\b|stor|floor|level)", re.I)
ROW_BLOCK_MAX = 8   # lines in one unit row (id, price, beds/baths, size, amenities, ...)
ROW_GAP = 3         # lines after a row in which an id-less row may still start
ROW_TEXT_MAX = 80   # an id-less row starts only at a short table cell, never in a prose sentence
FACT_LABEL_RE = re.compile(r"^(?:Studio|\d[\d.]* bed|\d[\d.]* bath|[\d,]+ sq ft)(?:, (?:\d[\d.]* bath|[\d,]+ sq ft))*$")


def street_number(address_text: str) -> str | None:
    """'418 Linden Grove Ave #1204' -> '418' (the left part of a '418-367' unit id on that page)."""
    m = re.match(r"\s*(\d+)\b", address_text or "")
    return m.group(1) if m else None


def unit_label(raw: str, street: str | None = None) -> str:
    """'#101' -> 'Unit 101', 'Apt 1310' -> 'Unit 1310', '418-367' on a page at street number 418 -> 'Unit 367'."""
    t = re.sub(rf"^\s*(?i:{UNIT_WORD})\s*", "", str(raw or "").strip())
    t = re.sub(r"^#\s*", "", t).strip()
    m = re.fullmatch(r"(\d+)-(\w+)", t)
    if m and street and m.group(1) == street:
        t = m.group(2)
    return f"Unit {t.upper()}"


def _spec_kinds(s: str) -> dict[str, Any]:
    """The bedrooms / bathrooms / area_sqft one text states (first value per kind; ranges and labels' values skipped)."""
    got: dict[str, Any] = {}
    for kind, rx in UNIT_PATTERNS:
        for m in rx.finditer(s):
            before = s[:m.start()]
            if RANGE_BEFORE.search(before) or SPEC_TAIL_RE.search(before) or ((rx.groups or 0) >= 2 and m.group(2)):
                continue
            if kind == "area_sqft" and re.search(r"\blot\b|\bacres?\b", s, re.I):
                continue
            got.setdefault(kind, to_number(m.group(1)))
    if "bedrooms" not in got and (sm := STUDIO_RE.search(s)) and (got or len(s.strip()) <= 40) \
            and not RANGE_BEFORE.search(s[:sm.start()]):
        got["bedrooms"] = 0
    return got


def fact_label(facts: dict[str, Any]) -> str | None:
    """A stable label for a unit row with no id, from its own facts: '2 bed, 1.5 bath, 1,264 sq ft'."""
    parts = []
    b = facts.get("bedrooms")
    if b is not None:
        parts.append("Studio" if b == 0 else f"{b:g} bed")
    if facts.get("bathrooms") is not None:
        parts.append(f"{facts['bathrooms']:g} bath")
    if facts.get("area_sqft") is not None:
        parts.append(f"{int(facts['area_sqft']):,} sq ft")
    return ", ".join(parts) or None


def _unit_heads(lines: list[str], street: str | None, stops: set[int]) -> dict[int, str]:
    """{line: "Unit <id>"} for the lines that open a unit row. "Unit 1204" / "Apt 1204" lines always do (as PLAN_RE
    did); a bare "1204", "#101" or "418-367" line, or a one-line row "1204 | 1 Bed | ...", does when a bed / bath /
    size follows (on the line, or within 3 lines) and the page has two or more such rows."""
    heads: dict[int, str] = {}
    keyed: set[int] = set()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 160 or i in stops:
            continue
        m = BARE_ID_RE.match(s)
        if m:
            pre, rid = m.group(1), m.group(2)
            if pre and pre.strip() != "#":  # "Unit 1204" / "Apt 1310" / "Unit 418-367"
                if re.search(r"\d", rid) or PLAN_RE.match(s):
                    heads[i], _ = unit_label(rid, street), keyed.add(i)
                continue
            if not re.search(r"\d", rid) or (not pre and not re.fullmatch(r"\d{2,6}(?:-\d{1,5})?", rid)):
                continue  # a bare "A1" is a plan name; a bare "2" is a count
            nxt = _next_nonempty(lines, i, 3)
            if nxt and SPEC_WORD_START_RE.match(lines[nxt[0]]):
                continue  # "648" over "sq ft": a number cell, not a row id
            if any(_spec_kinds(lines[j]) for j in nxt if not BARE_ID_RE.match(lines[j].strip())):
                heads[i] = unit_label(rid, street)
            continue
        m = ROW_LEAD_RE.match(s)
        if m and (m.group(1) or re.fullmatch(r"\d{2,6}(?:-\d{1,5})?", m.group(2))) and _spec_kinds(s[m.end():]):
            heads[i] = unit_label(m.group(2), street)
            if m.group(1) and m.group(1).strip() != "#":
                keyed.add(i)
            continue
        p = PLAN_RE.match(ln)
        if p and (u := UNIT_PLAN_RE.match(p.group(1).strip())):  # "Unit 1204 | 2 beds" / "Apt. 1310"
            heads[i], _ = unit_label(u.group(1), street), keyed.add(i)
    return heads if len(heads) >= 2 else {i: lab for i, lab in heads.items() if i in keyed}


class Blocks:
    """The page layout: labels[i] = the floor plan / unit card line i sits in; headed = {card start: (bedrooms,
    heading quote, heading line)} for bare-name cards under a group heading; rows = lines inside unit rows of a unit
    table (two or more rows); heads = lines that open a unit row."""
    __slots__ = ("labels", "headed", "rows", "heads")

    def __init__(self, labels, headed, rows, heads):
        self.labels, self.headed, self.rows, self.heads = labels, headed, rows, heads


def _plan_blocks(lines: list[str], street: str | None = None, excluded: list[bool] | None = None,
                 self_lines: list[int] | None = None) -> Blocks:
    """Floor plan / unit label each line belongs to (table row on one line, or a card over a few lines)."""
    named = [i for i, ln in enumerate(lines)
             if NAME_RE.match(ln.strip()) and not NOT_NAME_RE.search(ln) and not SPEC_COLON_RE.search(ln)
             and any(SPEC_COLON_RE.search(lines[j]) for j in _next_nonempty(lines, i, 2))]
    named = set(named) if len(named) >= 2 else set()  # plan cards repeat; one hit is a single-home fact sheet
    excl = excluded or [False] * len(lines)
    stops = set(self_lines or []) | {i for i, x in enumerate(excl) if x}  # our address / other listings end a row
    heads = _unit_heads(lines, street, stops)
    table = len(heads) >= 2
    labels: list[str | None] = [None] * len(lines)
    headed: dict[int, tuple[int, str, int]] = {}
    rows: set[int] = set()
    anon: dict[str, dict] = {}  # placeholder label of an id-less row -> its facts
    current, start, heading, row_mode, seen, row_end = None, 0, None, False, set(), -10**9
    for i, ln in enumerate(lines):
        m = PLAN_RE.match(ln)
        g = GROUP_RE.match(ln)
        # A "1 Bed" line is a group heading only right above a bare-name card; inside a card ("Plan 1A" /
        # "1 Bed" / "1 Bath") it is the card's own spec and must keep the card's label.
        is_head = bool(g and (nxt := _next_nonempty(lines, i, 1)) and nxt[0] in named)
        if is_head:
            heading = (0 if g.group(1) else to_number(g.group(2)), ln.strip(), i)
        if i in heads:
            current, start, row_mode = heads[i], i, table
            seen = set(_spec_kinds(ln)) if table else set()
        elif m:
            current, start, row_mode = m.group(1).strip(), i, False
        elif i in named:
            current, start, row_mode = ln.strip(), i, False
            if heading:
                headed[i] = heading
        else:
            if current and (not ln.strip() or is_head or i - start >= (ROW_BLOCK_MAX if row_mode else PLAN_BLOCK_MAX)
                            or (row_mode and i in stops)):
                if row_mode:  # a row ended by our address or another listing: the table is over
                    row_end = -10**9 if i in stops else i
                current, row_mode = None, False
            cell = len(ln.strip()) <= ROW_TEXT_MAX and len(ln.split()) <= 10 and not re.search(r"[.!?]\s*$", ln)
            facts = _spec_kinds(ln) if table and i not in stops and cell else {}
            if facts:
                # a second bed / bath / size inside one row, or one right after a row: a row with no id
                if (current and row_mode and set(facts) & seen) or (current is None and i - row_end <= ROW_GAP):
                    current, start, row_mode, seen = f"\x00row{i}", i, True, set()
                    anon[current] = {}
                if current and row_mode:
                    seen |= set(facts)
                    if current in anon:
                        for k, v in facts.items():
                            anon[current].setdefault(k, v)
        labels[i] = current
        if current and row_mode and table:
            rows.add(i)
    names = {ph: fact_label(f) for ph, f in anon.items()}
    labels = [names.get(lab, lab) if lab in names else lab for lab in labels]
    return Blocks(labels, headed, rows, set(heads) if table else set())


def _plan_labels(lines: list[str]) -> list[str | None]:
    return _plan_blocks(lines).labels


def _claim(kind, value, scope, label, quote, checkable=True, line=-1) -> dict:
    return {"kind": kind, "value": value, "scope": scope, "label": label,
            "quote": quote.strip()[:120], "checkable": checkable, "_line": line}


def _regex_claims(lines: list[str], excluded: list[bool], self_lines: list[int], street: str | None = None) -> list[dict]:
    blocks = _plan_blocks(lines, street, excluded, self_lines)
    labels, headed = blocks.labels, blocks.headed
    out: list[dict] = []
    ranged: set[str] = set()  # kinds with a building range already (the first on the page wins)
    for i, ln in enumerate(lines):
        if excluded[i] or not ln.strip():
            continue
        label = labels[i]
        unit_ln = ln
        if (SPEC_TAIL_RE.search(ln) or BARE_LABEL_RE.match(ln)) and i + 1 < len(lines) and not excluded[i + 1]:
            unit_ln = f"{ln.rstrip()} {lines[i + 1].strip()}"  # "SQFT:" / "559-603" on two lines
        unit_hits = []
        spans = []
        if not label:  # a range outside any plan card or unit row: the building's range
            for kind, (lo, hi, quote, span) in ranges_in(unit_ln).items():
                spans.append(span)
                if kind not in ranged:
                    ranged.add(kind)
                    out.append({**_claim(kind, lo, "building", None, quote, line=i), "value_max": hi})
        for kind, rx in UNIT_PATTERNS:
            for m in rx.finditer(unit_ln):
                if any(a < m.end() and m.start() < b for a, b in spans):
                    continue  # one end of a building range
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
                if i in blocks.rows and label:
                    unit_hits[-1]["_row"] = True  # a unit row's own number: the rules own it, not the model
        # "Studio" counts as 0 bedrooms only inside a plan block or next to other unit numbers on the line
        sm = STUDIO_RE.search(ln)
        if sm and (label or unit_hits) and not RANGE_BEFORE.search(ln[:sm.start()]) \
                and not any(a < sm.end() and sm.start() < b for a, b in spans):
            unit_hits.append(_claim("bedrooms", 0, "unit", label, sm.group(0), line=i))
        out += unit_hits
        for kind, rx in BUILDING_PATTERNS:
            for m in rx.finditer(ln):
                if RANGE_BEFORE.search(ln[:m.start()]):
                    continue
                v = to_number(m.group(1))
                if kind == "unit_count" and (label or v is None or v < 2 or AVAIL_RE.search(ln)):
                    continue
                out.append(_claim(kind, v, "building", None, m.group(0), line=i))
        for rx, kind, scope, canon in AMENITY_RES:
            m = rx.search(ln)
            if m:  # inside a unit row the feature is that unit's
                row = i in blocks.rows and label
                out.append(_claim(kind, canon, "unit" if row else scope, label if row else None, m.group(0), line=i))
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
    keep = [c for c in claims if c["kind"] not in NUMERIC_KINDS or c["label"] or is_range(c)]
    for kind in NUMERIC_KINDS:
        unl = [c for c in claims if c["kind"] == kind and not c["label"] and not is_range(c)]
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


# ---------------------------------------------------------------- candidate statements (deterministic)

KEYWORD_RE = re.compile(r"\b(bed|bd|bath|ba|sq|sqft|feet|story|stories|floor|built|year|unit|amenit|pool|garage|"
                        r"parking|laundry|washer|view|balcon|gym|fitness|elevator|plan|studio|roof|luxury)", re.I)
NEAR_SELF = 8
FEATURE_HEAD_RE = re.compile(r"amenit|features|appliances|utilities included", re.I)
# Statements longer than this are cut into sentences; each sentence is its own candidate.
SENTENCE_SPLIT_AT = 160
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'(])")
CANDIDATE_MAX = 400
# Short lines that only carry what changes from day to day (price, date, "6 Available Units"). They are no
# claim kind and would make the same page read as a new page: left out of the candidates and the cache key.
VOLATILE_TOKEN_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s*(?:-|–|to)\s*\$?\s?[\d,]+(?:\.\d+)?)?(?:\s*/\s*mo(?:nth)?\.?)?"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b"
    r"|\b\d+\s+available\s+units?\b", re.I)
VOLATILE_FILLER_RE = re.compile(
    r"\b(?:starting|starts|base|rent|from|price[sd]?|pricing|available|availability|as|soon|of|on|now|today|"
    r"date|move-?in|per|month|mo|monthly|at|is|the|a|an|for|up|to)\b|[\s:/|·•,.\-–()+*]", re.I)


def volatile(line: str) -> bool:
    """A short line that is only a price, a date or an availability count (no claim kind; changes daily)."""
    s = line.strip()
    if not s or len(s) > 60 or not VOLATILE_TOKEN_RE.search(s):
        return False
    return not VOLATILE_FILLER_RE.sub("", VOLATILE_TOKEN_RE.sub("", s))


def _picked_lines(lines: list[str], excluded: list[bool], self_lines: list[int], labels: list[str | None],
                  budget: int, rows: set[int] = frozenset()) -> list[tuple[int, str]]:
    """(line number, stripped text) of the page lines about the clicked listing, in page order, within budget.
    Plan card lines repeat ("1 Bed", "2 Baths"); they are kept per card, other repeats only once."""
    picked, seen, size = [], set(), 0
    in_list = 0  # lines left in an "Amenities" / "Features" list
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            in_list = 0
            continue
        if excluded[i] or volatile(s):
            continue
        if len(s) <= 60 and FEATURE_HEAD_RE.search(s):
            in_list = 30
        listed, in_list = in_list > 0, max(in_list - 1, 0)
        near = any(abs(i - j) <= NEAR_SELF for j in self_lines)
        keyword = KEYWORD_RE.search(s) and (re.search(r"\d|studio", s, re.I) or len(s) <= 80
                                            or any(rx.search(s) for rx, *_ in AMENITY_RES))
        plan_name = labels[i] is not None and s == labels[i]  # a bare plan name line ("B") above its specs
        spec_value = i > 0 and SPEC_TAIL_RE.search(lines[i - 1])  # "559-603" under "SQFT:"
        if not (near or keyword or (listed and len(s) <= 80) or PLAN_RE.match(s) or plan_name or spec_value
                or i in rows):
            continue
        key = (labels[i], re.sub(r"\s+", " ", s.lower()))
        if key in seen:
            continue
        seen.add(key)
        if size + min(len(s), 4000) + 1 > budget:
            break
        picked.append((i, s[:4000]))
        size += min(len(s), 4000) + 1
    return picked


def prefilter(lines: list[str], excluded: list[bool], self_lines: list[int], budget: int = PROMPT_BUDGET,
              street: str | None = None) -> str:
    """Compact excerpt of the page about the clicked listing (the picked lines joined)."""
    b = _plan_blocks(lines, street, excluded, self_lines)
    return "\n".join(s for _, s in _picked_lines(lines, excluded, self_lines, b.labels, budget, b.rows))


def candidates(lines: list[str], excluded: list[bool], self_lines: list[int],
               budget: int = PROMPT_BUDGET, street: str | None = None) -> list[dict]:
    """The page's candidate statements, numbered 1.. in page order: one per picked line, long lines cut into
    sentences. Each: {"n", "text", "line", "label", "row"} where label is the floor plan / unit card the line sits
    in (from the page layout, not the model) and row is True inside a unit row of a unit table (its features are
    that unit's). Same page text in -> the same list out."""
    b = _plan_blocks(lines, street, excluded, self_lines)
    labels = b.labels
    out: list[dict] = []
    seen = set()
    for i, s in _picked_lines(lines, excluded, self_lines, labels, budget, b.rows):
        parts = SENTENCE_RE.split(s) if len(s) > SENTENCE_SPLIT_AT else [s]
        for p in parts:
            p = p.strip()[:CANDIDATE_MAX].strip()
            if not re.search(r"[A-Za-z0-9]", p):
                continue  # "/" between "1 Bed" and "1 Bath"
            key = (labels[i], _norm(p))
            if key in seen:
                continue
            seen.add(key)
            out.append({"n": len(out) + 1, "text": p, "line": i, "label": labels[i], "row": i in b.rows})
    return out


def _render(cands: list[dict]) -> str:
    return "\n".join(f"{c['n']}. {c['text']}" + (f"   [plan: {c['label']}]" if c["label"] else "") for c in cands)


# ---------------------------------------------------------------- model layer: a judge over the fixed list

PROMPT = """You read a rental listing page about the property at: {address}
Below are the page's statements, numbered, in page order (navigation, ads and other listings mostly removed;
ignore any that remain). A tag [plan: X] means the statement sits in the card of floor plan or unit X.
<<<
{candidates}
>>>
For EACH numbered statement decide whether it states a fact about this property that a building record, a 3D scan
or a photo could check. Return a JSON array with one item per claim:
{{"n": statement number, "kind": "bedrooms|bathrooms|area_sqft|stories|year_built|unit_count|amenity|parking|laundry|view|other",
 "value": number for the first six kinds, else the feature in a few words taken from the statement,
 "scope": "unit" or "building", "label": the floor plan or unit name the claim is about, else null,
 "quote": the exact words of that statement that make the claim (max 120 chars),
 "checkable": false when nothing could measure it (e.g. "largest in North America")}}
Rules: only this property. One item per number and one per amenity: a statement listing several amenities gives one
item for each. A floor plan card gives its bedrooms, bathrooms and area_sqft, labelled with the plan. Studio = 0
bedrooms. A size range inside one plan card: value = the low end. unit_count is only the building's total number of
units ("120 units", "This building has 11 units"); "11 units available" or "for rent" is availability: skip it.
Skip prices, fees, specials, availability, dates,
phone numbers, hours, schools, reviews, neighborhood and nearby places, and ranges over the whole building like
"1-3 Beds", "Studios–2" or "475–966 sq ft" (those are read separately). Skip pure marketing words. A statement that states no fact gets no item.
Items in statement order. JSON only."""


def _ask_cached(prompt: str) -> Any:
    """ask_json, with the parsed reply cached under rc_cache/claims by prompt hash (repeat runs are instant)."""
    from harness.model.ask import ask_json  # imported lazily: keeps the deterministic path dependency-free
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


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def _norm_label(s: Any) -> str:
    return re.sub(r"^(floor\s?plan|plan|unit|model|residence|layout|apt\.?|apartment)\s+", "", _norm(s or ""))


QUOTE_CHARS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "\u00a0": " "})


def clean_text(s: Any, n: int = 80) -> str:
    """Canonical form of a feature value: straight quotes, one space, no bullets or trailing punctuation,
    first letter upper case, at most n chars."""
    t = re.sub(r"\s+", " ", str(s or "").translate(QUOTE_CHARS)).strip()
    t = re.sub(r"^[•*·\-\s]+", "", t)
    t = re.sub(r"[\s.,;:!\-]+$", "", t)
    if t[:1].islower():
        t = t[0].upper() + t[1:]
    return t[:n].strip()


LIST_LEAD_RE = re.compile(r"\b(?:with|including|includes|include|like|such as|featuring|features)\b\s*:?\s*|:\s*",
                          re.I)
LIST_SPLIT_RE = re.compile(r"\s*(?:,\s+|;\s*|\s•\s*|\s\|\s|\s·\s)\s*")


def split_list(text: str) -> list[str]:
    """Fixed rule for a compound amenity list: "grills, lounge seating, and firepits" -> 3 items. Only a
    comma / semicolon / bullet list is split ("pool and spa" stays one); a lead before "with" / "including" /
    ":" ("Outdoor spaces with ...") is dropped when it has no comma itself."""
    t = clean_text(text, 400)
    if not LIST_SPLIT_RE.search(t):
        return [clean_text(t)] if t else []
    m = LIST_LEAD_RE.search(t)
    if m and "," not in t[:m.start()] and m.end() < len(t):
        t = t[m.end():]
    parts = [re.sub(r"^(?:and|&|or|plus)\s+", "", p.strip(), flags=re.I) for p in LIST_SPLIT_RE.split(t)]
    out: list[str] = []
    for p in parts:
        p = clean_text(p)
        if re.search(r"[A-Za-z]", p) and p.casefold() not in {o.casefold() for o in out}:
            out.append(p)
    return out or [clean_text(text)]


SPLIT_KINDS = ("amenity", "parking")
FEATURE_KINDS = ("amenity", "parking", "laundry", "view", "other")
SHORT_STATEMENT = 80  # a list item / bullet: its own words are the feature value
RANGE_RE = re.compile(r"(\d{1,2},\d{3}|\d{3,5})\s*(?:-|–|to)\s*(\d{1,2},\d{3}|\d{3,5})")


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _read_reply(items: Any, cands: list[dict], page_norm: str) -> list[dict]:
    """The model's per-statement answers -> claims, in statement order. Everything the model does not decide is
    fixed here: the label of a statement in a plan card, numeric scope, quotes (the statement's own words), the
    value of a short list item (its own words, split by split_list), the low end of a size range."""
    by_n = {c["n"]: c for c in cands}
    rows: list[tuple[dict, dict, int]] = []  # (item, candidate, reply order)
    for k, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        cand = by_n.get(_int(it.get("n")))
        if cand is None:  # no / bad number: the statement whose words it quotes
            q = _norm(it.get("quote") or "")
            cand = next((c for c in cands if q and q in _norm(c["text"])), None)
        if cand is None:
            continue
        rows.append((it, cand, k))
    # statement order, then a fixed kind order inside one statement; the reply order only among one kind
    rows.sort(key=lambda r: (r[1]["n"], KINDS.index(r[0].get("kind")) if r[0].get("kind") in KINDS else len(KINDS), r[2]))
    # short statements answered with one feature kind: their own words are the value(s)
    kinds_of: dict[int, set] = {}
    for it, cand, _ in rows:
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        if kind in FEATURE_KINDS:
            kinds_of.setdefault(cand["n"], set()).add(kind)
    out: list[dict] = []
    done_short: set[tuple] = set()
    for it, cand, k in rows:
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        text = cand["text"]
        q = str(it.get("quote") or "").strip()
        if len(text) <= 120:
            quote = text
        elif q and _norm(q) in _norm(text) and _norm(q) in page_norm:
            quote = q[:120]
        else:
            quote = text[:120].rsplit(" ", 1)[0] if " " in text[:120] else text[:120]
        checkable = it.get("checkable", True) not in (False, "false", "False", 0)
        if kind in NUMERIC_KINDS:
            value = to_number(it.get("value"))
            if value is None:
                value = to_number(q or text)
            if value is None:
                continue
            if kind == "unit_count" and (AVAIL_RE.search(text) or cand.get("row")):
                continue  # homes for rent now, not the building's total
            if kind in UNIT_KINDS and not cand["label"] and kind in ranges_in(text):
                continue  # a building range: read by the rules as a range claim, never as one unit's number
            if kind == "area_sqft" and (r := RANGE_RE.search(text)):
                lo, hi = to_number(r.group(1)), to_number(r.group(2))
                if value in (lo, hi):
                    value = lo
            scope = "unit" if kind in UNIT_KINDS else "building"
            label = cand["label"] if kind in UNIT_KINDS else None
            if label is None and kind in UNIT_KINDS:
                lab = it.get("label")
                lab = re.sub(r"\s+", " ", str(lab)).strip()[:60] if lab not in (None, "", "null") else ""
                label = lab if lab and _norm(lab) in page_norm else None
            out.append(_claim(kind, value, scope, label, quote, checkable, line=cand["line"]))
            out[-1]["_n"] = cand["n"]
            continue
        scope = it.get("scope") if it.get("scope") in ("unit", "building") else (
            "unit" if kind in ("laundry", "view") else "building")
        row_label = cand["label"] if cand.get("row") else None  # a feature listed in a unit row is that unit's
        if row_label:
            scope = "unit"
        if len(text) <= SHORT_STATEMENT and kinds_of.get(cand["n"]) == {kind}:
            if (cand["n"], kind) in done_short:
                continue
            done_short.add((cand["n"], kind))
            values = split_list(text) if kind in SPLIT_KINDS else [clean_text(text)]
        else:
            raw = it.get("value") or q or text
            values = split_list(raw) if kind in SPLIT_KINDS else [clean_text(raw)]
        for v in values:
            if v:
                out.append(_claim(kind, v, scope, row_label, quote, checkable, line=cand["line"]))
                out[-1]["_n"] = cand["n"]
    return out


def _model_claims(address: str, cands: list[dict], page_norm: str) -> list[dict]:
    if not cands:
        return []
    prompt = PROMPT.format(address=address or "(unknown)", candidates=_render(cands))
    return _read_reply(_ask_cached(prompt), cands, page_norm)


# ---------------------------------------------------------------- merge and finish

def _amenity_names(value: str) -> tuple:
    """The building-record amenity names a feature value names (tools.kit.facts.AMENITIES, the list the judge
    uses), else the local amenity patterns. Two claims naming the same names get the same verdict."""
    try:
        from tools.kit.facts import _AMENITIES as names
    except Exception:
        names = [(canon, rx) for rx, _k, _s, canon in AMENITY_RES]
    return tuple(sorted({name for name, rx in names if rx.search(value)}))


def _key(c: dict) -> tuple:
    v = c["value"]
    if isinstance(v, str):
        if c["kind"] == "amenity" and (names := _amenity_names(v)):
            return c["kind"], _norm_label(c["label"]), names
        v = " ".join(re.sub(r"[^a-z0-9]+", " ", _norm(v.translate(QUOTE_CHARS))).split())
    return c["kind"], _norm_label(c["label"]), v, c.get("value_max")


def _drop_unlabeled_on_plan_pages(claims: list[dict]) -> list[dict]:
    """On a page with two or more labelled plans for a unit kind, an unlabeled unit number of that kind belongs
    to no home ("2 Bedrooms" in a hero line): dropped. Page metadata is kept unless a labelled claim already
    states the same value (the page unit's "705 sq ft" in JSON-LD repeats its row in the unit table)."""
    plans = {k: {_norm_label(c["label"]) for c in claims if c["kind"] == k and c["label"]} for k in UNIT_KINDS}
    vals = {(c["kind"], c["value"]) for c in claims if c["kind"] in UNIT_KINDS and c["label"]}
    return [c for c in claims if is_range(c) or not (c["kind"] in UNIT_KINDS and not c["label"] and len(plans[c["kind"]]) >= 2
                                      and (c.get("origin") != METADATA or (c["kind"], c["value"]) in vals))]


def _rank(c: dict) -> int:
    """Which claims fill the MAX_CLAIMS slots first: checkable numbers (each unit row's beds / baths / size among
    them), then checkable features a building record names (pool, courtyard, ...) or parking / laundry, then other
    checkable features, then features listed in unit rows (one unit's own), then the rest."""
    if not c["checkable"]:
        return 4
    if c["kind"] in NUMERIC_KINDS:
        return 0
    if c["label"]:
        return 3
    if c["kind"] in ("parking", "laundry") or (c["kind"] == "amenity" and _amenity_names(str(c["value"]))):
        return 1
    return 2


def _finish(groups: list[list[dict]]) -> list[dict]:
    """Merge -> page order (page claims by line, then page metadata) -> drop duplicates (first wins) ->
    stable sort by _rank -> at most MAX_CLAIMS, ids c1.."""
    allc = [(g_i, k, c) for g_i, g in enumerate(groups) for k, c in enumerate(g)]
    allc.sort(key=lambda t: (1 if t[2].get("origin") == METADATA else 0,
                             t[2]["_line"] if t[2].get("origin") != METADATA else 0, t[0], t[1]))
    seen, merged = set(), []
    for _, _, c in allc:
        k = _key(c)
        if c["value"] is None or k in seen:
            continue
        seen.add(k)
        merged.append(c)
    merged = _drop_unlabeled_on_plan_pages(merged)
    merged.sort(key=_rank)  # stable: page order kept within each rank
    out = []
    for n, c in enumerate(merged[:MAX_CLAIMS], 1):
        out.append({"id": f"c{n}", "kind": c["kind"], "value": c["value"], "unit": None,
                    "scope": c["scope"], "label": c["label"], "quote": c["quote"], "checkable": c["checkable"]})
        if is_range(c):
            out[-1]["value_max"] = c["value_max"]
        if c.get("origin"):
            out[-1]["origin"] = c["origin"]
    for c in out:
        if c["kind"] == "area_sqft":
            c["unit"] = "sqft"
    return out


# ---------------------------------------------------------------- per-page cache of the final list

def page_key(snapshot: dict) -> str:
    """Hash of what the extractor reads: version, address, the page text (one space, no empty or volatile
    lines) and the page metadata. Same page content -> same key; a changed page -> a new key."""
    text = str(snapshot.get("text") or "")[:60000]
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln and not volatile(ln))
    meta = json.dumps(snapshot.get("jsonld") or [], sort_keys=True, ensure_ascii=False, default=str)
    blob = "\x1f".join([EXTRACTOR_VERSION, re.sub(r"\s+", " ", str(snapshot.get("address_text") or "")).strip(),
                        body, meta])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _page_cache_path(snapshot: dict) -> Path | None:
    """<private>/rc_cache/claims/pages/<page_key>.json, or None when no private dir is set."""
    if not os.environ.get("RC_PRIVATE_DIR"):
        return None
    try:
        import rc_common
        return rc_common.cache_dir("claims", "pages") / f"{page_key(snapshot)}.json"
    except Exception:
        return None


def _page_cache_read(path: Path | None) -> list[dict] | None:
    if path is None:
        return None
    try:
        from tools import cachectl
        if not cachectl.hit(path):  # RC_NO_CACHE=1: never read
            return None
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return None
    if isinstance(got, dict) and got.get("version") == EXTRACTOR_VERSION and isinstance(got.get("claims"), list) \
            and all(not validate_claim(c) for c in got["claims"]):
        return got["claims"]
    return None


def _page_cache_write(path: Path | None, claims: list[dict]) -> None:
    if path is None:
        return
    try:
        path.write_text(json.dumps({"version": EXTRACTOR_VERSION, "claims": claims}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass


def extract_claims(snapshot: dict, use_model: bool = True) -> list[dict]:
    """PageSnapshot -> list of Claim. Never raises on model failure: falls back to the deterministic layer.
    With the model: the same page content returns the cached list of the first reading (RC_NO_CACHE=1 rereads)."""
    text = str(snapshot.get("text") or "")[:60000]
    address = str(snapshot.get("address_text") or "")
    lines = text.splitlines()
    self_re = _self_re(address)
    street = street_number(address)
    self_lines, excluded = _line_roles(lines, self_re)
    regex = _regex_claims(lines, excluded, self_lines, street)
    jsonld = _jsonld_claims(snapshot.get("jsonld"), self_re)
    if not use_model:
        return _finish([regex, jsonld])
    cache = _page_cache_path(snapshot)
    hit = _page_cache_read(cache)
    if hit is not None:
        return [dict(c) for c in hit]
    try:
        model = _model_claims(address, candidates(lines, excluded, self_lines, street=street), _norm(text))
    except Exception:  # model unavailable, timeout, bad JSON: deterministic claims still stand (not cached)
        return _finish([regex, jsonld])
    # A unit row's beds / baths / size are read by the rules: they replace the model's answer for that slot.
    rows = {(c["kind"], _norm_label(c["label"])) for c in regex if c.get("_row")}
    model = [c for c in model if (c["kind"], _norm_label(c["label"])) not in rows]
    # Other regex claims fill only (kind, label) slots the model left empty, so model judgement wins on conflicts.
    covered = {(c["kind"], _norm_label(c["label"])) for c in model}
    covered_kinds = {c["kind"] for c in model}
    fill = [c for c in regex if is_range(c) or ((c["kind"], _norm_label(c["label"])) not in covered
                                               and (c["kind"] in NUMERIC_KINDS or c["kind"] not in covered_kinds))]
    out = _finish([model, jsonld, fill])
    _page_cache_write(cache, out)
    return out


# ---------------------------------------------------------------- the page's own unit

UNIT_HEAD_LINES = 6  # leading non-empty page lines read as the heading


def _unit_tools():
    """(unit_from_text, norm_unit) from tools.kit.units, loaded lazily; None when the kit is not importable."""
    try:
        from tools.kit.units import norm_unit, unit_from_text
        return unit_from_text, norm_unit
    except Exception:
        return None


def page_unit(snapshot: dict, claims: list[dict] | None = None) -> str | None:
    """The one apartment the page is about ('1204' for "Unit 1204", "#1204", "Apt 1204"), read from the address
    line, the page title and the heading lines (never the URL), and from unit claim labels when every one names
    the same unit ("Unit 1204"). None when nothing names a unit, two different units are named, or the labels are
    plan names ("Plan C", "The Aspen", "A1"). A unit, not a plan: the run looks it up in the kit's unit rows and
    drops that label from its claims only when the building has that unit and no plan of that name."""
    tools = _unit_tools()
    if not tools:
        return None
    from_text, norm = tools
    heads = [ln.strip() for ln in str(snapshot.get("text") or "")[:20000].splitlines() if ln.strip()]
    found = [from_text(s) for s in [snapshot.get("address_text"), snapshot.get("title")]
             + [ln for ln in heads[:UNIT_HEAD_LINES] if len(ln) <= 120]]
    labels = {str(c.get("label")).strip() for c in claims or []
              if c.get("label") and c.get("kind") in UNIT_KINDS}
    if len(labels) >= 2 and any(FACT_LABEL_RE.match(lab) for lab in labels):
        return None  # a unit table (rows with and without ids): a list of homes, whatever the heading names
    named = {from_text(lab) for lab in labels}
    if labels and None not in named and len({norm(u) for u in named}) == 1:
        found.append(next(iter(named)))
    found = [u for u in found if u]
    keys = {norm(u) for u in found} | {norm(u) for u in named if u}  # labels naming other units: a list of homes
    return found[0] if found and len(keys) == 1 else None


# ---------------------------------------------------------------- validation

def validate_claim(c: Any) -> list[str]:
    """Problems with a Claim dict (CONTRACT 2.2); empty list when valid."""
    if not isinstance(c, dict):
        return ["not a dict"]
    errs = []
    want = {"id", "kind", "value", "unit", "scope", "label", "quote", "checkable"}
    if set(c) - {"origin", "value_max"} != want:
        errs.append(f"keys {sorted((set(c) - {'origin', 'value_max'}) ^ want)} differ")
    if "value_max" in c:
        hi, lo = c["value_max"], c.get("value")
        if (c.get("kind") not in UNIT_KINDS or c.get("scope") != "building" or c.get("label") is not None
                or isinstance(hi, bool) or not isinstance(hi, (int, float)) or isinstance(lo, bool)
                or not isinstance(lo, (int, float)) or not lo < hi):
            errs.append("value_max: only on a building bed / bath / sq ft range, above value")
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
