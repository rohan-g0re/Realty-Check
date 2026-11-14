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


def parse(address: str, cities: list[str]) -> dict:
    """Split free text into {num_lo, num_hi, streets, zip, city, unit}. Missing parts are None.

    `cities` are known lower-case city names, used to peel the city off text without commas.
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
    if toks:
        nm = re.fullmatch(r"(\d+)[a-z]?(?:-(\d+)[a-z]?)?", toks[0])
        if nm:
            lo = int(nm.group(1))
            hi = int(nm.group(2)) if nm.group(2) else lo
            toks = toks[1:]
    return {"num_lo": lo, "num_hi": hi, "streets": street_variants(" ".join(toks)), "zip": zip_code, "city": city,
            "unit": unit or None}
