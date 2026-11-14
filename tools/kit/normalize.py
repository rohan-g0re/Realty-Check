"""Address normalization shared by the index builder and the matcher."""
from __future__ import annotations

import re

DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west",
              "northeast", "northwest", "southeast", "southwest"}
STREET_TYPES = {
    "st", "street", "str", "ave", "av", "avenue", "blvd", "boulevard", "rd", "road", "dr", "drive", "ln", "lane",
    "ct", "court", "pl", "place", "pkwy", "pky", "parkway", "hwy", "highway", "fwy", "freeway", "way", "cir",
    "circle", "ter", "terrace", "trl", "trail", "sq", "square", "plz", "plaza", "row", "loop", "vlg", "village", "holw", "hollow", "grv", "grove", "hts", "heights", "ests", "estates", "landing", "lndg", "walk", "aly",
    "alley", "park", "expy", "expressway", "cv", "cove", "crk", "creek", "xing", "crossing", "pt", "point", "bnd", "bend",
}
ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th",
            "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th", "eleventh": "11th",
            "twelfth": "12th"}
STATES = {"tx", "texas", "ca", "california", "al", "ak", "az", "ar", "co", "ct", "de", "fl", "ga", "hi", "id",
          "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh",
          "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "ut", "vt", "va", "wa",
          "wv", "wi", "wy", "dc", "new jersey", "new york"}
UNIT_RE = re.compile(r"\s*(?:#|\b(?:apt|apartment|unit|ste|suite|floor|rm|room|bldg|building)\b)\s*#?\s*([a-z0-9-]*)")
# street type spellings -> one canonical form, so 'Dr' and 'Drive' agree
_TYPE_GROUPS = (("st", "street", "str"), ("ave", "av", "avenue"), ("blvd", "boulevard"), ("rd", "road"),
                ("dr", "drive"), ("ln", "lane"), ("ct", "court"), ("pl", "place"), ("pkwy", "pky", "parkway"),
                ("hwy", "highway"), ("fwy", "freeway"), ("cir", "circle"), ("ter", "terrace"), ("trl", "trail"),
                ("sq", "square"), ("plz", "plaza"), ("vlg", "village"), ("holw", "hollow"), ("grv", "grove"),
                ("hts", "heights"), ("ests", "estates"), ("lndg", "landing"), ("aly", "alley"),
                ("expy", "expressway"), ("cv", "cove"), ("crk", "creek"), ("xing", "crossing"), ("pt", "point"),
                ("bnd", "bend"))
TYPE_CANON = {t: g[0] for g in _TYPE_GROUPS for t in g}
ABBREV_MIN = 3  # an abbreviated street-name word keeps at least this many letters ('Lind' for 'Linden')
ZIP_RE = re.compile(r"\b(\d{5})(?:-[0-9x]{1,4})?[\s,]*(?:usa|us|united states)?[\s,]*$")


def zip5(v) -> str | None:
    m = re.match(r"\s*(\d{5})", str(v or ""))
    return m.group(1) if m else None


def _peel(toks: list[str], types_first: bool) -> list[str]:
    def trail(kinds):
        nonlocal toks
        while len(toks) > 1 and any(toks[-1] in k for k in kinds):
            toks = toks[:-1]
    if types_first:
        trail((STREET_TYPES, DIRECTIONS))
    else:
        trail((DIRECTIONS,))
    while len(toks) > 1 and toks[0] in DIRECTIONS:
        toks = toks[1:]
    trail((STREET_TYPES, DIRECTIONS))
    return toks


def _tokens(name: str) -> list[str]:
    return [ORDINALS.get(t, t) for t in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()]


def street_key(name: str) -> str:
    """Kit streetName -> key. 'S Corbel Blvd' -> 'corbel'; 'Park Row' -> 'park'; 'First St' -> '1st'.

    The kit keeps direction and type out of streetName, so peel trailing types/directions and a
    leading direction, always keeping at least one word.
    """
    return " ".join(_peel(_tokens(name), True))


def street_variants(name: str) -> list[str]:
    """Keys to try for a free-text street. The kit is inconsistent for names made of direction and
    type words ('West Loop South' is streetName 'West', 'N Loop W' is 'Loop'), so try both peel orders."""
    out = []
    for first in (True, False):
        k = " ".join(_peel(_tokens(name), first))
        if k and k not in out:
            out.append(k)
    return out


def street_type(street: str) -> str | None:
    """Canonical street type of free street text ('Linden Drive' -> 'dr', 'S Corbel Blvd N' -> 'blvd'), from the
    last type word after trailing directions are dropped; None when it names none."""
    toks = _tokens(street)
    while len(toks) > 1 and toks[-1] in DIRECTIONS:
        toks = toks[:-1]
    if len(toks) > 1 and toks[-1] in STREET_TYPES:
        return TYPE_CANON.get(toks[-1], toks[-1])
    return None


def _num_unit(tok: str) -> tuple[int, str] | None:
    """'41811-310' -> (41811, '310'): a street number with its unit after a dash, not a number range. Only when the
    right part is shorter than the left, smaller, and 3+ digits ('101-105' and '1200-08' stay ranges)."""
    m = re.fullmatch(r"(\d{4,6})-(\d{3,5})", tok or "")
    if m and len(m.group(2)) < len(m.group(1)) and int(m.group(2)) < int(m.group(1)):
        return int(m.group(1)), m.group(2)
    return None


def unit_label(text, street_num=None) -> str | None:
    """Comparable unit key of a page's unit label, as tools.kit.units.norm_unit gives it: '#1204', 'Apt 1204',
    'Unit 1204' -> '1204'. '41811-310' -> '310' when the page's street number is 41811 (`street_num`), or, with no
    street number given, when it reads as one (see _num_unit). Other dashed labels are left to norm_unit."""
    from tools.kit.units import norm_unit
    t = str(text if text is not None else "").strip()
    m = re.fullmatch(r"(?:#\s*)?(\d+)\s*-\s*([a-z0-9]+)", t, re.I)
    if m:
        try:
            num = int(str(street_num).strip()) if street_num is not None and str(street_num).strip() else None
        except ValueError:
            num = None
        if (num is not None and int(m.group(1)) == num) or (num is None and _num_unit(f"{m.group(1)}-{m.group(2)}")):
            return norm_unit(m.group(2))
    return norm_unit(t)


def abbrev_match(a: str, b: str) -> bool:
    """Two street keys that differ only by abbreviated words ('lind' / 'linden'): same word count, and every word
    pair equal or one a prefix of the other with at least ABBREV_MIN letters. Equal keys are not an abbreviation."""
    ta, tb = a.split(), b.split()
    if a == b or not ta or len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        short, long_ = sorted((x, y), key=len)
        if not (short.isalpha() and long_.isalpha() and len(short) >= ABBREV_MIN and long_.startswith(short)):
            return False
    return True


def abbrev_hits(q: dict, by_street: dict, address_of=None) -> list:
    """Fallback when no street key equals the query's: index hits whose street name is an abbreviation of the
    query's or the other way round ('Lind Dr' / 'Linden Dr'). Safe only when everything else agrees, so a hit must
    have the street number in range, the same zip (or, with no zip given, the same city), and the same street type
    (`address_of(property) -> display address` gives the record's type; a hit whose type is unknown is dropped).
    `by_street` is {street key: [(property, key)]}. [] when hits fall on more than one street name (not guessing)."""
    if q.get("num_lo") is None or not q.get("streets") or not (q.get("zip") or q.get("city")) \
            or not q.get("street_type"):
        return []
    address_of = address_of or (lambda p: p.get("address") or "")
    found: dict[str, list] = {}
    for key, rows in by_street.items():
        if not any(abbrev_match(st, key) for st in q["streets"]):
            continue
        for p, k in rows:
            if k.get("num_lo") is None or not (k["num_lo"] <= q["num_lo"] <= (k.get("num_hi") or k["num_lo"])):
                continue
            if q.get("zip") and k.get("zip") != q["zip"]:
                continue
            if not q.get("zip") and k.get("city") != q["city"]:
                continue
            if record_street_type(address_of(p)) != q["street_type"]:
                continue
            found.setdefault(key, []).append((p, k))
    return next(iter(found.values())) if len(found) == 1 else []


def record_street_type(address: str) -> str | None:
    """Street type of a display address ('11 Linden Grove Ave, Houston, TX 77006' -> 'ave')."""
    line = str(address or "").lower().replace(".", " ").split(",")[0]
    toks = line.split()
    if toks and re.fullmatch(r"\d+[a-z]?(?:-\d+[a-z]?)?", toks[0]):
        toks = toks[1:]
    return street_type(" ".join(toks))


def parse(address: str, cities: list[str]) -> dict:
    """Split free text into {num_lo, num_hi, streets, street_type, zip, city, unit}. Missing parts are None.

    `cities` are known lower-case city names, used to peel the city off text without commas. A leading
    '41811-310' (street number, dash, a shorter smaller number) is the street number and a unit, not a range,
    unless the text names another unit.
    """
    s = " ".join(address.lower().replace(".", " ").split())
    um = UNIT_RE.search(s)
    unit = um.group(1) if um else None
    s = UNIT_RE.sub("", s)
    zip_code = None
    m = ZIP_RE.search(s)
    if m and m.start() > 0:
        zip_code, s = m.group(1), (s[:m.start()] + s[m.end():])
    s = re.sub(r"[\s,]*\b(usa|us|united states)\s*$", "", s.strip(" ,"))
    for st in sorted(STATES, key=len, reverse=True):
        if re.search(rf"(^|[\s,]){re.escape(st)}$", s):
            s = s[: -len(st)].strip(" ,")
            break
    city = None
    for c in sorted(cities, key=len, reverse=True):
        if re.search(rf"(^|[\s,]){re.escape(c)}$", s) and len(s) > len(c):
            city, s = c, s[: -len(c)].strip(" ,")
            break
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if city is None and len(parts) > 1:
        city = parts[-1]
    line = next((p for p in parts if p[0].isdigit()), parts[0] if parts else "")
    toks = line.split()
    lo = hi = None
    if toks and not unit and (nu := _num_unit(toks[0])):
        lo = hi = nu[0]
        unit = nu[1]
        toks = toks[1:]
    elif toks:
        nm = re.fullmatch(r"(\d+)[a-z]?(?:-(\d+)[a-z]?)?", toks[0])
        if nm:
            lo = int(nm.group(1))
            hi = int(nm.group(2)) if nm.group(2) else lo
            toks = toks[1:]
    street = " ".join(toks)
    return {"num_lo": lo, "num_hi": hi, "streets": street_variants(street), "street_type": street_type(street),
            "zip": zip_code, "city": city, "unit": unit or None}
