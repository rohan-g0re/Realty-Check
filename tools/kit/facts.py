"""Renter-relevant extras from the CoStar kit, one small JSON file per building, for "Fill me in".

The address index (build_index.py) keeps only what matching and the fact-check need. This module reads the
kit once more and keeps what a renter would want to know: class and rating, style, rules (pets, parking),
vacancy, the management company, nearby schools, every floor plan with its size range and the asking rent
per plan from the Apartments.com listing, the amenities the record's photos are captioned with, and whether
the owner is a company or an individual. Files go to cache_dir("kit", "facts") (outside the repo).

Rules applied here, so the harness never sees them:
- An enum 0 / Unknown, a null, or a 0 count is "not recorded" and is dropped, never turned into "none".
- Only the management company's name is kept. The owner is kept only as the word "company" or "individual"
  (`owner_type`), never a name. No person names, phones or emails, and no free-text fields (pet restrictions,
  comments, descriptions) since those can carry contact details.
- Amenities: the kit's amenity ids have no name table, so names come from the record's own photo captions
  ("Pool", "Fitness Center"), mapped onto a fixed list (AMENITIES); a caption is never copied as is.

Run once: python -m tools.kit.facts   (about a minute; reads the zip, writes one file per building)
At run time: kit_facts(property_id) -> dict or None (one small file read; never builds, never networks).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import rc_common

FACTS_VERSION = 2       # 2: amenities, owner_type (the owner's name is no longer kept)
READ_VERSIONS = (1, 2)  # version 1 files lack the new keys but are otherwise the same shape
LISTING_AS_OF = "2026-11-12"  # the kit's apartment listings are the ones active on this date (kit docs)

CLASS = {1: "A", 2: "B", 3: "C"}
STYLE = {1: "Garden", 2: "Mid-rise", 3: "High-rise", 4: "Low-rise", 5: "Single-family homes", 6: "Townhomes"}
LIFESTYLE = {2: "Senior", 3: "Student", 4: "Military", 5: "Corporate", 6: "Vacation"}  # 1 = All: nothing to say
RENT_TYPE = {1: "Market rate", 2: "Affordable", 3: "Market rate and affordable"}
AFFORDABLE = {1: "rent restricted", 2: "rent subsidized", 3: "rent controlled", 4: "rent stabilized",
              5: "some affordable units"}
METERING = {1: "Master metered (one meter for the building)", 2: "Individually metered (each home has its own)"}
LEED = {1: "LEED Certified", 2: "LEED Gold", 3: "LEED Platinum", 4: "LEED Silver", 5: "LEED pre-certified",
        6: "LEED registered"}
RES_PARKING = {1: "Surface lot", 3: "Covered lot", 4: "Street", 5: "Garage", 7: "Other", 8: "Carport",
               9: "Compact car", 10: "EV parking", 11: "Attached garage", 12: "Detached garage", 13: "Accessible",
               14: "Motorcycle", 15: "Oversized", 16: "Parking", 17: "Tandem", 18: "Valet", 19: "Guest parking"}
PARKING = {1: "Surface", 2: "Surface tandem", 3: "Covered", 4: "Covered tandem", 6: "One-car garage",
           7: "Two-car garage", 8: "Reserved", 9: "Attached garage"}
PET = {1: "Birds", 2: "Cats", 3: "Dogs", 4: "Fish", 5: "Reptiles", 6: "Other pets"}
GRADE = {1: "Elementary", 2: "Middle", 3: "High"}
SCHOOL_TYPE = {1: "public", 2: "private", 3: "charter"}
ROOM_TYPE = {1: "Den", 2: "Study", 4: "Bachelor", 5: "Efficiency", 6: "Junior", 7: "Loft", 8: "Office",
             11: "Studio", 12: "Townhouse", 13: "Alcove studio", 14: "Convertible", 15: "Penthouse"}
RENOVATED = (4, 16, 17)  # ConstructionEventType RenovationFinished, RedevelopmentComplete, Redeveloped
OWNER_ROLES = (7,)       # ContactRole TrueOwner
RECORDED_OWNER_ROLES = (3,)  # ContactRole RecordedOwner: the owner type when no true owner is on record
MANAGER_ROLES = (4,)     # ContactRole PropertyManager
INDIVIDUAL_OWNER = 5     # company-detail businessStatus IndividualOwner

# Amenity names, in display order, and the photo captions that name them. A caption longer than CAPTION_MAX is
# a description, not a name, and is skipped. Parking is left out (its own "Fill me in" rows cover it), and so
# are things inside a home (kitchen, balcony, washer/dryer).
CAPTION_MAX = 60
AMENITIES = [
    ("Pool", r"\b(swimming |lap |resort[- ]style |infinity )?pools?\b(?!\s*(table|room))"),
    ("Spa or hot tub", r"(?<!pet )\bspa\b|hot ?tub|jacuzzi|whirlpool"),
    ("Fitness center", r"fitness|\bgym\b|yoga|work ?out room|exercise room|spin room"),
    ("Clubhouse", r"club ?house|club ?room"),
    ("Resident lounge", r"(?<!sky )(?<!roof )(?<!rooftop )(?<!outdoor )\blounge\b|community room|resident room|"
                        r"great room|social room|party room"),
    ("Rooftop deck", r"roof ?top|roof (deck|terrace)|sky ?(lounge|deck|terrace)"),
    ("Sun deck", r"sun ?deck"),
    ("Courtyard", r"courtyard"),
    ("Outdoor lounge", r"outdoor lounge"),
    ("Grills or outdoor kitchen", r"\bgrill(s|ing)?\b|\bbbq\b|barbe(cue|que)|outdoor kitchen"),
    ("Picnic area", r"picnic"),
    ("Business center", r"business center|co-?working|work ?space|conference room"),
    ("Library", r"\blibrary\b"),
    ("Game room", r"game ?room|billiards?|pool table"),
    ("Media room", r"theater|theatre|cinema|media room|screening room"),
    ("Coffee bar", r"coffee bar|coffee lounge|\bcaf[eé]\b"),
    ("Dog park", r"dog (park|run)|bark park|pet park"),
    ("Pet spa", r"pet (spa|wash|grooming)|dog wash"),
    ("Playground", r"playground|play area|tot lot"),
    ("Sports court", r"(tennis|basketball|pickleball|volleyball|sport|sports) courts?"),
    ("Laundry room", r"laundry (room|facilit|center|area)"),
    ("Package room", r"package|parcel locker|mail ?room|mail center"),
    ("Bike storage", r"bike (storage|room|shop|repair)|bicycle (storage|room)"),
]
_AMENITIES = [(name, re.compile(rx, re.I)) for name, rx in AMENITIES]
NOT_AMENITY_PHOTOS = (1, 9, 10, 11, 12, 16)  # AttachmentType Aerial, PlatMap, FloorPlan, SitePlan, TypicalFloorPlan, Map

# A company name must carry a company word; anything else may be a person and is dropped.
COMPANY_RE = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|company|co|lp|l\.p|llp|ltd|limited|partners|partnership|"
    r"group|management|mgmt|properties|property|realty|real estate|residential|communities|community|living|"
    r"apartments?|homes|housing|holdings|investments?|investors|capital|fund|trust|reit|associates|development|"
    r"developers?|ventures|enterprises|equities|asset|assets|authority|university|services|advisors|"
    r"multifamily|estates?|land|partners?hip)\b\.?", re.I)


def facts_dir() -> Path:
    return rc_common.cache_dir("kit", "facts")


def is_company(name) -> bool:
    return isinstance(name, str) and bool(name.strip()) and bool(COMPANY_RE.search(name)) \
        and not re.search(r"@|\d{3}[\s.-]?\d{3}[\s.-]?\d{4}", name)


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _pos(v):
    """A positive number, else None: 0 means 'not recorded' in this kit."""
    n = _num(v)
    return n if n is not None and n > 0 else None


def _name(s):
    return " ".join(str(s).split()) if s else None


def _drop_empty(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], {}, "")}


def _parking(r: dict, prof: dict) -> dict:
    pols = []
    for p in prof.get("parkingPolicies") or []:
        kind = RES_PARKING.get(p.get("parkingType"))
        if not kind:
            continue
        lo, hi = _num(p.get("minSpaceFee")), _num(p.get("maxSpaceFee"))
        if p.get("monetaryUnit") not in (None, 0, 1):
            lo = hi = None  # not dollars
        pols.append(_drop_empty({"type": kind, "assigned": True if p.get("hasAssignedSpacesAvailable") else None,
                                 "fee_min": lo, "fee_max": hi, "spaces": _pos(p.get("numSpaces"))}))
    spaces = {}
    for p in r.get("parking") or []:
        kind, n = PARKING.get(p.get("parkingType")), _pos(p.get("totalSpaces"))
        if kind and n:
            spaces[kind] = spaces.get(kind, 0) + n
    return _drop_empty({
        "none": True if prof.get("hasNoParkingAvailable") else None,
        "visitor": True if prof.get("hasVisitorParkingAvailable") else None,
        "policies": pols,
        "spaces": [{"type": k, "count": n} for k, n in spaces.items()],
    })


def _pets(prof: dict) -> dict:
    pols = []
    for p in prof.get("petPolicies") or []:
        kind = PET.get(p.get("petType"))
        if not kind:
            continue
        money = p.get("monetaryUnit") in (None, 0, 1)
        pols.append(_drop_empty({
            "type": kind, "max": _pos(p.get("numPets")),
            "deposit": _pos(p.get("deposit")) if money else None, "rent": _pos(p.get("rent")) if money else None,
            "fee_min": _pos(p.get("minFee")) if money else None, "fee_max": _pos(p.get("maxFee")) if money else None,
            "spayed": True if p.get("isSpayedNeutered") else None,
            "interview": True if p.get("isPetInterviewRequired") else None}))
    return _drop_empty({"none_allowed": True if prof.get("hasNoPetsAllowed") else None,
                        "negotiable": True if prof.get("isPetNegotiable") else None, "policies": pols})


def _schools(prof: dict) -> list:
    out = []
    for s in prof.get("schools") or []:
        if s.get("isMarketingPublished") is False or not _name(s.get("schoolName")):
            continue
        d = _num(s.get("distanceMile"))
        out.append(_drop_empty({"name": _name(s.get("schoolName")), "grade": GRADE.get(s.get("gradeLevel")),
                                "type": SCHOOL_TYPE.get(s.get("schoolType")),
                                "distance_mi": round(d, 1) if d is not None and d >= 0 else None,
                                "zoned": True if s.get("assignmentType") in (1, 4) else None}))
    out.sort(key=lambda s: s.get("distance_mi", 99))
    return out[:8]


def _plans(apt: dict) -> list:
    out = []
    for m in apt.get("models") or []:
        lo, hi = _pos(m.get("minLeaseTermsMonths")), _pos(m.get("maxLeaseTermsMonths"))
        out.append(_drop_empty({
            "id": m.get("id"), "name": _name(m.get("modelName")), "beds": _num(m.get("numBeds")),
            "baths": _pos(m.get("numBaths")), "sqft_min": _pos(m.get("minSquareFeet")),
            "sqft_max": _pos(m.get("maxSquareFeet")), "units": _pos(m.get("numUnits")),
            "max_occupants": _pos(m.get("maxOccupants")), "levels": _pos(m.get("numFloors")),
            "lease_min": lo, "lease_max": hi, "short_term": True if m.get("isShortTermLeaseAvailable") else None,
            "room_type": ROOM_TYPE.get(m.get("nonstandardApartmentRoomType")),
            "approx_size": True if m.get("isSquareFeetApproximate") else None}))
    return out


def _listing(li: dict | None) -> dict:
    if not li:
        return {}
    plans = []
    for m in li.get("apartmentModels") or []:
        dollars = m.get("monetaryUnit") in (None, 0, 1)
        plans.append(_drop_empty({
            "id": m.get("id"), "name": _name(m.get("name")), "beds": _num(m.get("numBeds")),
            "baths": _pos(m.get("numBaths")), "sqft_min": _pos(m.get("minSquareFeet")),
            "sqft_max": _pos(m.get("maxSquareFeet")),
            "rent_min": _pos(m.get("minRent")) if dollars else None,
            "rent_max": _pos(m.get("maxRent")) if dollars else None,
            "per_bed": True if m.get("rentPricingType") in (2, 3) else None,
            "units": _pos(m.get("numUnits")), "available": _num(m.get("numUnitsAvailable"))}))
    dollars = li.get("monetaryUnit") in (None, 0, 1)
    return _drop_empty({"as_of": LISTING_AS_OF,
                        "rent_min": _pos(li.get("minRent")) if dollars else None,
                        "rent_max": _pos(li.get("maxRent")) if dollars else None,
                        "available": _num(li.get("numUnitsAvailable")), "units": _pos(li.get("totalNumUnits")),
                        "lifestyle": LIFESTYLE.get(li.get("lifestyleType")), "plans": plans})


def _company(contacts: list, roles: tuple, companies: dict) -> str | None:
    for c in contacts or []:
        if c.get("contactRole") in roles and c.get("endDate") is None:
            name = companies.get(c.get("locationId"))
            if is_company(name):
                return name
    return None


def _owner_type(contacts: list, companies: dict, statuses: dict | None) -> str | None:
    """'company' | 'individual' for the current true owner (else the recorded owner); None when the record does
    not say. Only the word is returned, never the name."""
    from tools.paper.owner import owner_type  # lazy: tools.paper.owner imports this module
    for roles in (OWNER_ROLES, RECORDED_OWNER_ROLES):
        for c in contacts or []:
            if c.get("contactRole") not in roles or c.get("endDate") is not None:
                continue
            loc = c.get("locationId")
            if (statuses or {}).get(loc) == INDIVIDUAL_OWNER:
                return "individual"
            t = owner_type(companies.get(loc))
            if t:
                return t
    return None


def amenities_from_captions(attachments: list) -> list[str]:
    """Amenity names (from AMENITIES, in its order) that the record's photo captions name. Never a raw caption."""
    found = set()
    for a in attachments or []:
        cap = " ".join(str(a.get("caption") or "").split())
        if not cap or len(cap) > CAPTION_MAX or a.get("attachmentType") in NOT_AMENITY_PHOTOS:
            continue
        for name, rx in _AMENITIES:
            if rx.search(cap):
                found.add(name)
    return [name for name, _ in AMENITIES if name in found]


def extract(r: dict, li: dict | None, companies: dict, statuses: dict | None = None) -> dict:
    """One commercial-property record (+ its apartment listing) -> the compact facts dict. `companies` maps a
    contact's locationId to the company name, `statuses` to its company-detail businessStatus."""
    apt = r.get("propertyApartment") or {}
    prof = apt.get("residentialProfile") or {}
    renov = [e.get("year") for e in r.get("constructionEvents") or [] if e.get("eventType") in RENOVATED and e.get("year")]
    vac = _num(apt.get("vacancyPercent"))
    lot, lot_unit = _pos(r.get("lotSize")), {1: "sq ft", 2: "acres", 3: "sq ft"}.get(r.get("lotSizeMeasurementType"))
    elev = sum(_pos(e.get("numElevators")) or 0 for e in (apt.get("elevators") or [])
               if e.get("elevatorType") == 1 and e.get("elevatorState") == 1)
    leed = [LEED.get(x.get("propertyLeedType")) for x in ((r.get("environmental") or {}).get("leedCertifications") or [])]
    manager = (li or {}).get("propertyManagementCompanyName")
    manager = _name(manager) if is_company(manager) else _company(r.get("contacts"), MANAGER_ROLES, companies)
    return _drop_empty({
        "version": FACTS_VERSION, "property_id": str(r["id"]),
        "building_class": CLASS.get(r.get("buildingClass")),
        "building_rating": r.get("buildingRating") if r.get("buildingRating") in (1, 2, 3, 4, 5) else None,
        "style": STYLE.get(apt.get("apartmentStyleType")),
        "lifestyle": LIFESTYLE.get(apt.get("apartmentLifestyleType")),
        "condo": True if apt.get("apartmentOwnershipType") == 1 or r.get("isCondo") else None,
        "rent_type": RENT_TYPE.get(apt.get("apartmentRentType")),
        "affordable_kind": AFFORDABLE.get(apt.get("affordableRentSubtype")),
        "affordable_units": _pos(apt.get("affordableUnits")),
        "buildings": _pos(apt.get("numBuildings")),
        "vacancy_percent": vac if vac is not None and 0 < vac <= 100 else None,  # 0 is ambiguous; >100 is an error
        "metering": METERING.get(apt.get("apartmentMeteringType")),
        "walkup": True if apt.get("isWalkup") else None,
        "elevators": elev or None,
        "renovated": max(renov) if renov else None,
        "lot": {"size": lot, "unit": lot_unit} if lot and lot_unit else None,
        "leed": next((x for x in leed if x), None),
        "manager": manager,
        "owner_type": _owner_type(r.get("contacts"), companies, statuses),
        "amenities": amenities_from_captions(r.get("attachments")),
        "parking": _parking(r, prof),
        "pets": _pets(prof),
        "schools": _schools(prof),
        "plans": _plans(apt),
        "listing": _listing(li),
    })


# ---------- build (offline, once) ----------

def build() -> int:
    import pyzipper
    from tools.kit.build_index import _rows, kit_zip_path
    z = pyzipper.AESZipFile(kit_zip_path())
    z.setpassword(rc_common.secret("COSTAR_ZIP_PASSWORD").encode())
    listings = {r["propertyId"]: r for r in _rows(z, "data/apartment-listing.jsonl") if r.get("propertyId")}
    wanted = set()
    props = []
    for r in _rows(z, "data/commercial-property.jsonl"):
        props.append(r)
        for c in r.get("contacts") or []:
            if c.get("contactRole") in OWNER_ROLES + RECORDED_OWNER_ROLES + MANAGER_ROLES and c.get("locationId"):
                wanted.add(c["locationId"])
    companies, statuses = {}, {}
    for c in _rows(z, "data/company-detail.jsonl"):
        if c.get("id") in wanted:
            companies[c["id"]] = _name(c.get("name") or (c.get("profile") or {}).get("companyName"))
            statuses[c["id"]] = c.get("businessStatus")
    out = facts_dir()
    n = 0
    for r in props:
        f = extract(r, listings.get(r["id"]), companies, statuses)
        (out / f"{r['id']}.json").write_text(json.dumps(f, separators=(",", ":")), encoding="utf-8")
        n += 1
    (out / "_version.json").write_text(json.dumps({"version": FACTS_VERSION, "count": n}), encoding="utf-8")
    return n


# ---------- run time ----------

_lock = threading.Lock()
_mem: dict[str, dict | None] = {}


def kit_facts(property_id) -> dict | None:
    """The building's extra kit facts, or None when not built yet / not a building. One small file read."""
    pid = str(property_id or "")
    if not re.fullmatch(r"\d{1,12}", pid):
        return None
    with _lock:
        if pid in _mem:
            return _mem[pid]
    try:
        p = facts_dir() / f"{pid}.json"
        f = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        if f and f.get("version") not in READ_VERSIONS:
            f = None
    except Exception:
        f = None
    with _lock:
        _mem[pid] = f
    return f


def main() -> None:
    n = build()
    print(f"wrote kit facts for {n} properties")


if __name__ == "__main__":
    main()
