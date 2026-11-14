""""Fill me in": things our sources know about this place that the listing does not say.

Not a fact-check: nothing here is right or wrong. Pure data shaping, no model call, no network:
build_fill_in(match, evidences, claims, kit=None, page_text="") -> list of facts (docs/CONTRACT.md 2.7).

Fact: {"id", "topic", "label", "value", "source", "note", "image"}
- topic in TOPICS; source in record | scan | aerial | county; note "" or one plain sentence; image an absolute
  cache path or None (the server turns it into /img/<token>).

Honesty rules:
- A topic the page already talks about belongs to the fact-check, so it is left out here. The page's claims
  (kinds, plan labels, quotes) decide that, plus a few plain keyword checks on the page text for topics the
  claim reader has no kind for (rent, pets, parking, schools, ...). When in doubt, leave the fact out.
- Blank, 0 or unknown values are "not recorded" and are skipped, never stated as "none".
- Record values say they are as of when the building was last researched; rents give the listing date.
- Company names only (cleaned in tools/kit/facts.py); never people, phones, emails or free text.
- No complaint data is available offline, so none is shown (Houston 311 text would never be shown anyway).
"""
from __future__ import annotations

import re
from typing import Any

from harness.graph import plans as plan_rules

TOPICS = ["Building", "Homes & floor plans", "Rent", "Rules", "Around it", "Records"]
RECORD_NOTE = "From the building record, as of when it was last researched."
MAX_PLANS = 16   # floor plan facts shown before a "more on record" line
MAX_RENTS = 16
MAX_SCHOOLS = 4
STUDIO_WORDS = ("Studio", "Efficiency", "Alcove studio", "Bachelor")
VAGUE_PARKING = ("Other", "Parking")  # kit parking types that say nothing on their own

# Topics the claim reader has no kind for: a keyword in a claim's quote or in the page text means the page
# talks about it, so the fact belongs to the listing, not here.
PAGE_SAYS = {
    "year_built": r"\b(built|constructed|completed)\s+(in\s+)?(19|20)\d\d\b|\byear\s+built\b",
    "stories": r"\b\d+[- ]?(story|stories|storey)\b|\bstories\b",
    "unit_count": r"\b\d{2,4}\s+(units|homes|apartment homes|apartments|residences)\b",
    "rent": r"\$\s?\d[\d,]{2,}",
    "pets": r"\bpets?\b|\bdogs?\b|\bcats?\b",
    "parking": r"\bparking\b|\bgarage\b|\bcarport\b",
    "schools": r"\bschools?\b",
    "manager": r"\bmanaged\s+by\b|\bmanagement\s+company\b|\bproperty\s+management\b",
    "owner": r"\bowned\s+by\b",
    "available": r"\bavailable\s+(units?|homes?|apartments?)\b|\b\d+\s+(units?\s+)?available\b",
    "vacancy": r"\bvacan(cy|t)\b|\boccupancy\b",
    "renovated": r"\brenovat|\bremodel",
    "lease": r"\blease\s+terms?\b|\b\d+[- ]month\s+lease|\bshort[- ]term\b",
    "utilities": r"\butilit(y|ies)\b|\bmetered\b",
    "rent_type": r"\baffordable\b|\bincome[- ](restricted|qualified|based)\b|\bsection\s+8\b",
    "lifestyle": r"\bseniors?\b|\b55\s?\+|\bstudent\s+housing\b|\bcorporate\s+housing\b",
    "style": r"\bhigh[- ]?rise\b|\bmid[- ]?rise\b|\blow[- ]?rise\b|\bgarden[- ]style\b|\btownhomes?\b",
    "elevators": r"\belevators?\b",
    "condo": r"\bcondo(minium)?s?\b",
    "leed": r"\bleed\b",
}
KIND_TOPICS = {"year_built": "year_built", "stories": "stories", "unit_count": "unit_count", "parking": "parking"}


def _slug(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")[:40] or "x"


def _n(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(v) -> str:
    f = _n(v)
    return f"{f:,.0f}" if f is not None else ""


def _num_word(v) -> str:
    f = _n(v)
    if f is None:
        return ""
    return f"{f:g}"


def _money(v) -> str:
    return f"${_n(v):,.0f}"


def _range(lo, hi, fmt=_int) -> str:
    lo, hi = _n(lo) or None, _n(hi) or None
    if lo and hi and hi != lo:
        return f"{fmt(min(lo, hi))}–{fmt(max(lo, hi))}"
    v = lo or hi
    return fmt(v) if v else ""


def _beds_baths(beds, baths) -> str:
    bits = []
    b = _n(beds)
    if b is not None:
        bits.append("Studio" if b == 0 else f"{b:g} bed")
    ba = _n(baths)
    if ba:
        bits.append(f"{ba:g} bath")
    return ", ".join(bits)


class _Page:
    """What the listing page already says: claim kinds, plan labels, sizes, and keyword topics."""

    def __init__(self, claims: list[dict], page_text: str):
        claims = [c for c in claims or [] if isinstance(c, dict)]
        self.claims = claims
        self.kinds = {c.get("kind") for c in claims}
        quotes = " \n ".join(f"{c.get('quote') or ''} {c.get('value') or ''}" for c in claims)
        text = f"{quotes}\n{page_text or ''}"
        self.text = text
        self.topics = {k for k, rx in PAGE_SAYS.items() if re.search(rx, text, re.I)}
        self.topics |= {t for k, t in KIND_TOPICS.items() if k in self.kinds}
        self.labels = sorted({(c.get("label") or "").strip() for c in claims if (c.get("label") or "").strip()})
        self.sizes = [_n(c.get("value")) for c in claims if c.get("kind") == "area_sqft"]

    def says(self, topic: str) -> bool:
        return topic in self.topics

    def plan_claims(self, label: str, kinds=("bedrooms", "bathrooms", "area_sqft")) -> set:
        lab = (label or "").strip().lower()
        return {c.get("kind") for c in self.claims if (c.get("label") or "").strip().lower() == lab and c.get("kind") in kinds}

    def shows_plan(self, name: str, sqft, kit_plans: list[dict]) -> bool:
        """The page shows this kit plan: a claim label resolves to it, its name is on the page, or a size on the
        page is its size."""
        nm = (name or "").strip()
        for lab in self.labels:
            fp = plan_rules.by_name(lab, kit_plans) or plan_rules.by_size(plan_rules.listing_facts(lab, self.claims), kit_plans)
            if fp and (fp.get("name") or "").strip().lower() == nm.lower():
                return True
        if nm and re.search(r"[a-z]", nm, re.I) and re.search(r"(?<![\w-])" + re.escape(nm) + r"(?![\w-])", self.text, re.I):
            return True
        s = _n(sqft)
        return bool(s) and any(x and abs(x - s) <= max(5.0, 0.01 * s) for x in self.sizes)


class _Facts:
    def __init__(self):
        self.items: list[dict] = []
        self.ids: set[str] = set()

    def add(self, fid: str, topic: str, label: str, value: str, source: str, note: str = "", image=None) -> None:
        value = str(value or "").strip()
        if not value or fid in self.ids:
            return
        self.ids.add(fid)
        self.items.append({"id": fid, "topic": topic, "label": label, "value": value, "source": source,
                           "note": note or "", "image": image or None})


def _ok(evs: list[dict], source: str) -> list[dict]:
    return [e for e in evs or [] if isinstance(e, dict) and e.get("source") == source and e.get("status") == "ok"
            and not (e.get("observed") or {}).get("feature_check")]


# ---------- Building ----------

def _building(f: _Facts, page: _Page, rec: dict, kit: dict) -> None:
    if not page.says("year_built") and _n(rec.get("year_built")):
        f.add("year-built", "Building", "Year built", f"{_n(rec['year_built']):.0f}", "record", RECORD_NOTE)
    if not page.says("stories") and _n(rec.get("stories")):
        s = _n(rec["stories"])
        f.add("floors", "Building", "Floors", f"{s:g} floor{'s' if s != 1 else ''} above ground", "record", RECORD_NOTE)
    units = rec.get("unit_count") or (kit.get("listing") or {}).get("units")
    if not page.says("unit_count") and _n(units):
        f.add("homes", "Building", "Number of homes", _int(units), "record", RECORD_NOTE)
    if kit.get("buildings") and kit["buildings"] > 1:
        f.add("buildings", "Building", "Buildings in the community", _int(kit["buildings"]), "record", RECORD_NOTE)
    if kit.get("style") and not page.says("style"):
        f.add("style", "Building", "Building style", kit["style"], "record", RECORD_NOTE)
    if kit.get("building_class"):
        f.add("class", "Building", "Building class", f"Class {kit['building_class']}", "record",
              "CoStar's quality grade within the local market (A is the top). As of when it was last researched.")
    if kit.get("building_rating"):
        f.add("rating", "Building", "CoStar building rating", f"{kit['building_rating']} of 5 stars", "record",
              "Compared with similar buildings nationally. As of when it was last researched.")
    if kit.get("renovated") and not page.says("renovated"):
        f.add("renovated", "Building", "Last renovated", str(kit["renovated"]), "record", RECORD_NOTE)
    if not page.says("elevators"):
        if kit.get("elevators"):
            f.add("elevators", "Building", "Passenger elevators", _int(kit["elevators"]), "record", RECORD_NOTE)
        elif kit.get("walkup"):
            f.add("elevators", "Building", "Elevator", "Walk-up building (no elevator on record)", "record", RECORD_NOTE)
    if kit.get("condo") and not page.says("condo"):
        f.add("condo", "Building", "Ownership", "Condominium: homes are owned one by one", "record", RECORD_NOTE)
    if kit.get("leed") and not page.says("leed"):
        f.add("leed", "Building", "Green building", kit["leed"], "record", RECORD_NOTE)
    if kit.get("manager") and not page.says("manager"):
        f.add("manager", "Building", "Management company", kit["manager"], "record",
              "Company named in the building record or its rental listing.")
    if kit.get("owner") and not page.says("owner") and kit.get("owner") != kit.get("manager"):
        f.add("owner", "Building", "Owner (company)", kit["owner"], "record", RECORD_NOTE)
    if _n(rec.get("building_sf")):
        f.add("building-size", "Building", "Building size", f"{_int(rec['building_sf'])} sq ft in all", "record",
              RECORD_NOTE)
    lot = kit.get("lot") or {}
    if _n(lot.get("size")) and lot.get("unit"):
        v = _n(lot["size"])
        f.add("lot", "Building", "Lot size", f"{v:,.2f}".rstrip("0").rstrip(".") + f" {lot['unit']}"
              if lot["unit"] == "acres" else f"{v:,.0f} sq ft", "record", RECORD_NOTE)


# ---------- Homes & floor plans ----------

def _plan_value(p: dict) -> str:
    bits = [_beds_baths(p.get("beds"), p.get("baths"))]
    size = _range(p.get("sqft_min") or p.get("sqft"), p.get("sqft_max"))
    if size:
        bits.append(f"{size} sq ft")
    if p.get("room_type") and not (_n(p.get("beds")) == 0 and p["room_type"] in STUDIO_WORDS):
        bits.append(f"with {p['room_type'].lower()}" if p["room_type"] in ("Den", "Study", "Loft", "Office")
                    else p["room_type"].lower())
    if _n(p.get("levels")) and _n(p["levels"]) > 1:
        bits.append(f"{_num_word(p['levels'])} levels")
    if _n(p.get("units")):
        bits.append(f"{_int(p['units'])} home{'s' if _n(p['units']) != 1 else ''} of this plan")
    return ", ".join(b for b in bits if b)


def _kit_plans(match: dict, kit: dict) -> list[dict]:
    kit_plans = kit.get("plans") or []
    if not kit_plans:
        kit_plans = [{"name": p.get("name"), "beds": p.get("beds"), "baths": p.get("baths"), "sqft_min": p.get("sqft")}
                     for p in match.get("floorplans") or [] if isinstance(p, dict)]
    return kit_plans


def _lookup(kit_plans: list[dict]) -> list[dict]:
    """Kit plans in the shape harness/graph/plans.py matches against."""
    return [{"name": p.get("name"), "beds": p.get("beds"), "baths": p.get("baths"),
             "sqft": p.get("sqft_min") or p.get("sqft")} for p in kit_plans if (p.get("name") or "").strip()]


def _homes(f: _Facts, page: _Page, match: dict, kit: dict) -> None:
    kit_plans = _kit_plans(match, kit)
    named = [p for p in kit_plans if (p.get("name") or "").strip()]
    lookup = _lookup(kit_plans)
    hidden = [p for p in named if not page.shows_plan(p.get("name"), p.get("sqft_min"), lookup)]
    if len(named) > 1 and hidden:
        shown = len(named) - len(hidden)
        f.add("plan-count", "Homes & floor plans", "Floor plans on record",
              f"{len(named)} floor plans" + (f"; the listing shows {shown}" if shown else ""), "record", RECORD_NOTE)
    if hidden:
        hidden.sort(key=lambda p: (_n(p.get("beds")) if _n(p.get("beds")) is not None else 99,
                                   _n(p.get("sqft_min")) or 0))
        for p in hidden[:MAX_PLANS]:
            label = p["name"] if re.match(r"(?i)plan\b", p["name"]) else f"Plan {p['name']}"
            fid = "plan-" + _slug(re.sub(r"(?i)^plan\s+", "", p["name"]))
            f.add(fid, "Homes & floor plans", label, _plan_value(p), "record",
                  "A floor plan the listing does not show. " + RECORD_NOTE)
        if len(hidden) > MAX_PLANS:
            f.add("plan-more", "Homes & floor plans", "More floor plans",
                  f"{len(hidden) - MAX_PLANS} more on record", "record", RECORD_NOTE)
    if not page.says("lease"):
        los = [_n(p.get("lease_min")) for p in kit_plans if _n(p.get("lease_min"))]
        his = [_n(p.get("lease_max")) for p in kit_plans if _n(p.get("lease_max"))]
        if los or his:
            f.add("lease", "Homes & floor plans", "Lease lengths",
                  f"{_range(min(los or his), max(his or los))} months", "record", RECORD_NOTE)
        if any(p.get("short_term") for p in kit_plans):
            f.add("short-term", "Homes & floor plans", "Short-term leases", "Offered for some floor plans", "record",
                  RECORD_NOTE)


def _scans(f: _Facts, page: _Page, evs: list[dict], lookup: list[dict]) -> None:
    stills: dict[str, str] = {}
    for e in _ok(evs, "scan"):
        o = e.get("observed") or {}
        for im in e.get("images") or []:
            if o.get("model_id") and str(im.get("caption", "")).lower().startswith("schematic floor plan") and im.get("path"):
                stills.setdefault(o["model_id"], im["path"])
    seen = set()
    for e in _ok(evs, "scan"):
        o = e.get("observed") or {}
        mid = o.get("model_id")
        if not mid or mid in seen or not (o.get("room_labels") or o.get("area_sqft")):
            continue
        seen.add(mid)
        plan = (o.get("plan") or "").strip()
        name = f"the 3D scan of {plan}" if plan else "a 3D scan of a home here"
        key = _slug(plan or o.get("model_name") or mid)
        title = str(o.get("model_name") or "").strip()
        titled = f" The scan is titled '{title}'." if title else ""
        kp = plan_rules.by_name(plan, lookup) if plan else None
        sized = "area_sqft" in page.plan_claims(plan) if plan else bool(page.sizes)
        if kp and page.sizes and page.shows_plan(kp.get("name"), kp.get("sqft"), lookup):
            sized = True  # the page gives this plan's size: the fact-check covers it
        if _n(o.get("area_sqft")) and not sized:
            f.add(f"scan-size-{key}", "Homes & floor plans", f"Measured floor area in {name}",
                  f"About {_int(o['area_sqft'])} sq ft", "scan",
                  "Scans measure interior floor, usually about a tenth less than the advertised size." + titled,
                  stills.pop(mid, None))  # one picture per scan
            titled = ""
        labels = [str(x) for x in o.get("room_labels") or [] if str(x).strip()]
        if labels:
            counts: dict[str, int] = {}
            for x in labels:
                counts[x] = counts.get(x, 0) + 1
            value = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in counts.items())
            f.add(f"scan-rooms-{key}", "Homes & floor plans", f"Rooms in {name}", value, "scan",
                  "Labelled automatically by the scan; labels can be wrong." + titled, stills.pop(mid, None))
        if _n(o.get("floors")) and _n(o["floors"]) > 1:
            f.add(f"scan-levels-{key}", "Homes & floor plans", f"Levels in {name}", _int(o["floors"]), "scan")


# ---------- Rent ----------

def _rent(f: _Facts, page: _Page, kit: dict) -> None:
    li = kit.get("listing") or {}
    as_of = li.get("as_of")
    note = f"Asking rent on record as of {as_of}; today's prices may differ." if as_of else \
        "Asking rent on record; today's prices may differ."
    if not page.says("rent"):
        lo, hi = li.get("rent_min"), li.get("rent_max")
        if lo and hi:
            f.add("rent-range", "Rent", "Asking rent across the building", f"{_range(lo, hi, _money)} a month", "record", note)
        plans = [p for p in li.get("plans") or [] if p.get("rent_min") or p.get("rent_max")]
        plans.sort(key=lambda p: (_n(p.get("beds")) if _n(p.get("beds")) is not None else 99,
                                  _n(p.get("rent_min") or p.get("rent_max"))))
        for p in plans[:MAX_RENTS]:
            name = (p.get("name") or "").strip() or _beds_baths(p.get("beds"), p.get("baths")) or "a floor plan"
            plain = bool(re.match(r"(?i)plan\b", name)) or not p.get("name")
            label = "Asking rent, " + (name if plain else "plan " + name)
            per = "a bed per month" if p.get("per_bed") else "a month"
            if p.get("rent_min") and p.get("rent_max"):
                value = f"{_range(p['rent_min'], p['rent_max'], _money)} {per}"
            elif p.get("rent_min"):
                value = f"From {_money(p['rent_min'])} {per}"
            else:
                value = f"Up to {_money(p['rent_max'])} {per}"
            f.add(f"rent-{_slug(name)}-{_slug(p.get('id'))}", "Rent", label, value, "record", note)
        if len(plans) > MAX_RENTS:
            f.add("rent-more", "Rent", "More asking rents", f"{len(plans) - MAX_RENTS} more floor plans", "record", note)
    if not page.says("available") and _n(li.get("available")) and _n(li.get("units")):
        f.add("available", "Rent", "Homes available to rent", f"{_int(li['available'])} of {_int(li['units'])}",
              "record", f"From the rental listing on record as of {as_of}." if as_of else RECORD_NOTE)
    if not page.says("vacancy") and kit.get("vacancy_percent"):
        f.add("vacancy", "Rent", "Vacancy", f"{_num_word(kit['vacancy_percent'])}% of homes empty", "record",
              "Share of homes empty when the building was last researched.")
    if not page.says("rent_type") and kit.get("rent_type"):
        v = kit["rent_type"]
        if kit.get("affordable_kind"):
            v += f" ({kit['affordable_kind']})"
        if kit.get("affordable_units"):
            v += f", {_int(kit['affordable_units'])} affordable homes"
        f.add("rent-type", "Rent", "Rent type", v, "record", RECORD_NOTE)
    if not page.says("utilities") and kit.get("metering"):
        f.add("metering", "Rent", "Utility meters", kit["metering"], "record", RECORD_NOTE)


# ---------- Rules ----------

def _pets(f: _Facts, page: _Page, kit: dict) -> None:
    pets = kit.get("pets") or {}
    if page.says("pets") or not pets:
        return
    note = ("The pet policy is negotiable. " if pets.get("negotiable") else "") + RECORD_NOTE
    if pets.get("none_allowed"):
        f.add("pets", "Rules", "Pets", "No pets allowed", "record", note)
        return
    for p in pets.get("policies") or []:
        bits = ["Allowed"]
        if p.get("max"):
            bits.append(f"up to {_int(p['max'])}")
        if p.get("deposit"):
            bits.append(f"{_money(p['deposit'])} deposit")
        if p.get("rent"):
            bits.append(f"{_money(p['rent'])} a month pet rent")
        fee = _range(p.get("fee_min"), p.get("fee_max"), _money)
        if fee:
            bits.append(f"{fee} fee")
        if p.get("spayed"):
            bits.append("must be spayed or neutered")
        if p.get("interview"):
            bits.append("pet interview required")
        f.add(f"pets-{_slug(p['type'])}", "Rules", p["type"], ", ".join(bits), "record", note)


def _parking(f: _Facts, page: _Page, kit: dict) -> None:
    pk = kit.get("parking") or {}
    if page.says("parking") or not pk:
        return
    if pk.get("none"):
        f.add("parking", "Rules", "Parking", "No parking available", "record", RECORD_NOTE)
        return
    plain = []
    for p in pk.get("policies") or []:
        bits = []
        fee = _range(p.get("fee_min"), p.get("fee_max"), _money)
        if fee:
            bits.append(f"{fee} a month")
        if p.get("spaces"):
            bits.append(f"{_int(p['spaces'])} spaces")
        if p.get("assigned"):
            bits.append("assigned spaces available")
        if bits:
            f.add(f"parking-{_slug(p['type'])}", "Rules", f"Parking: {p['type'].lower()}", ", ".join(bits),
                  "record", RECORD_NOTE)
        elif p["type"] not in VAGUE_PARKING and p["type"].lower() not in plain:
            plain.append(p["type"].lower())
    if plain:
        f.add("parking-types", "Rules", "Parking", "Offered: " + ", ".join(plain), "record", RECORD_NOTE)
    spaces = pk.get("spaces") or []
    if spaces and not pk.get("policies"):
        f.add("parking-spaces", "Rules", "Parking spaces",
              ", ".join(f"{_int(s['count'])} {s['type'].lower()}" for s in spaces), "record", RECORD_NOTE)
    if pk.get("visitor"):
        f.add("parking-visitor", "Rules", "Visitor parking", "Available", "record", RECORD_NOTE)


def _rules(f: _Facts, page: _Page, kit: dict) -> None:
    _pets(f, page, kit)
    _parking(f, page, kit)
    life = kit.get("lifestyle") or (kit.get("listing") or {}).get("lifestyle")
    if life and not page.says("lifestyle"):
        f.add("lifestyle", "Rules", "Who the community is for", f"{life} housing", "record", RECORD_NOTE)


# ---------- Around it ----------

def _around(f: _Facts, page: _Page, kit: dict, evs: list[dict]) -> None:
    if not page.says("schools"):
        for s in (kit.get("schools") or [])[:MAX_SCHOOLS]:
            bits = [" ".join(x for x in (s.get("type"), (s.get("grade") or "").lower(), "school") if x).capitalize()]
            if s.get("distance_mi"):  # 0 is "not recorded" in this kit
                bits.append(f"{s['distance_mi']:g} mi away")
            if s.get("zoned"):
                bits.append("zoned for this address")
            f.add(f"school-{_slug(s['name'])}", "Around it", s["name"], ", ".join(bits), "record", RECORD_NOTE)
    for e in _ok(evs, "aerial"):
        imgs = [im for im in e.get("images") or [] if im.get("path")]
        top = next((im for im in imgs if "top-down" in str(im.get("caption", "")).lower()), None)
        if top:
            date = (e.get("observed") or {}).get("capture_date")
            f.add("aerial-view", "Around it", "The block from above",
                  "Top-down aerial photo of the building and what sits next to it", "aerial",
                  f"Photo taken {date}." if date else "", top["path"])
            break
    if not page.says("stories"):
        for e in _ok(evs, "aerial"):
            o = e.get("observed") or {}
            if _n(o.get("height_m")) and _n(o.get("stories")):
                f.add("roof-height", "Building", "Measured roof height",
                      f"{_n(o['height_m']):.1f} m, about {_n(o['stories']):g} floors", "aerial",
                      "Measured from aerial elevation data.")
                break


# ---------- Records ----------

def _records(f: _Facts, page: _Page, evs: list[dict]) -> None:
    for e in _ok(evs, "county"):
        o = e.get("observed") or {}
        who = o.get("source_name") or "the county"
        note = f"From {who}, as last published."
        if _n(o.get("year_built")) and not page.says("year_built"):
            f.add("county-year-built", "Records", "Year built (county)", f"{_n(o['year_built']):.0f}", "county", note)
        if _n(o.get("stories")) and not page.says("stories"):
            f.add("county-floors", "Records", "Floors (county)", _num_word(o["stories"]), "county", note)
        if _n(o.get("unit_count")) and not page.says("unit_count"):
            f.add("county-homes", "Records", "Homes (county)", _int(o["unit_count"]), "county", note)
        if _n(o.get("building_sf")):
            f.add("county-size", "Records", "Building size (county)", f"{_int(o['building_sf'])} sq ft", "county", note)
        if o.get("parcel_id"):
            f.add("county-parcel", "Records", "County parcel number", str(o["parcel_id"]), "county",
                  f"Look it up with {who}.")
        break


def build_fill_in(match: dict | None, evidences: list[dict] | None, claims: list[dict] | None,
                  kit: dict | None = None, page_text: str = "") -> list[dict]:
    """Facts about this place the listing does not say, in display order (topics in TOPICS order)."""
    match = match if isinstance(match, dict) else {}
    evs = [e for e in evidences or [] if isinstance(e, dict)]
    page = _Page(claims or [], page_text or "")
    matched = match.get("status") == "matched"
    kit = kit if isinstance(kit, dict) and matched else {}
    f = _Facts()
    if matched:
        _building(f, page, match.get("record") or {}, kit)
        _homes(f, page, match, kit)
    _scans(f, page, evs, _lookup(_kit_plans(match, kit)) if matched else [])
    if matched:
        _rent(f, page, kit)
        _rules(f, page, kit)
    _around(f, page, kit, evs)
    _records(f, page, evs)
    order = {t: i for i, t in enumerate(TOPICS)}
    return sorted(f.items, key=lambda x: order.get(x["topic"], len(TOPICS)))  # stable: keeps order within a topic
