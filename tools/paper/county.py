"""county_record and geocode: public, keyless county / city records (CONTRACT 2.4, 3).

Observed keys besides the claim kinds: land_use (plain words), owner_type ("company" | "individual", never a
name), and in Houston houston_311_count (an int) with city_calls {total, by_type, since, until, radius_m,
source_url}: counts by category only, never what a request says.
"""
from __future__ import annotations

import hashlib
import json
import re

from rc_common import cache_dir, evidence
from tools import cachectl

from . import h311, hcad, la, net, nyc
from .owner import owner_type

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NO_SOURCE = "No county source wired for this location yet."
CACHE_VERSION = 3  # 2: land use and owner type added; 3: Houston residential year/floors/units, address fallback


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:20]


def _norm(address: str) -> str:
    return " ".join((address or "").upper().replace(",", " ").split())


def _house_no(address: str) -> str:
    m = re.match(r"\s*(\d+)", address or "")
    return m.group(1) if m else ""


def _street(address: str) -> str:
    """'1250 S Dunmore Dr, Los Angeles' -> 'S Dunmore Dr'."""
    first = (address or "").split(",")[0]
    return re.sub(r"^\s*\d+[A-Za-z]?\s+", "", first).strip()


def geocode(address: str) -> dict | None:
    """US Census geocoder (keyless). Returns {"lat","lon","matched_address"} or None. Never raises."""
    if not (address or "").strip():
        return None
    path = cache_dir("paper", "geocode") / f"{_key(_norm(address))}.json"
    if cachectl.hit(path):
        return json.loads(path.read_text(encoding="utf-8")).get("result")
    try:
        _, d = net.get_json_url(CENSUS, {"address": address, "benchmark": "Public_AR_Current",
                                         "format": "json"})
    except Exception:
        return None  # network trouble: do not cache
    m = (d.get("result") or {}).get("addressMatches") or []
    res = None
    if m:
        c = m[0]["coordinates"]
        res = {"lat": float(c["y"]), "lon": float(c["x"]), "matched_address": m[0]["matchedAddress"]}
    path.write_text(json.dumps({"address": address, "result": res}), encoding="utf-8")
    return res


def _extras(land, owner) -> dict:
    """land_use / owner_type keys, only when known. `owner` is already the word, never a name."""
    return {k: v for k, v in (("land_use", land), ("owner_type", owner)) if v}


def _clean_use(v) -> str | None:
    s = " ".join(str(v or "").split())
    return s if s and len(s) <= 60 and re.fullmatch(r"[A-Za-z0-9 ,.()&/+'-]+", s) else None


def _summary(obs: dict) -> str:
    bits = []
    if "stories" in obs:
        bits.append(f"{obs['stories']} floor{'' if obs['stories'] == 1 else 's'}")
    if "unit_count" in obs:
        bits.append(f"{obs['unit_count']} unit{'' if obs['unit_count'] == 1 else 's'}")
    if "building_sf" in obs:
        bits.append(f"{obs['building_sf']:,} sq ft of building")
    s = "County roll lists " + ", ".join(bits) if bits else "County roll has the parcel"
    if "year_built" in obs:
        s += f"{',' if bits else ''} built {obs['year_built']}"
    s += "."
    if obs.get("land_use"):
        s += f" Land use: {obs['land_use']}."
    return s


def _zip(address: str) -> str:
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address or "")
    return m.group(1) if m else ""


def _houston(address: str, lat: float, lon: float) -> dict:
    num = _house_no(address)
    p, purl = hcad.parcel_at(lat, lon, num)
    how = "point"
    if num and (not p or str(p.get("site_str_num") or "").strip() != num):
        # the map point found no parcel, or one with another house number: look the parcel up by its address,
        # accepted only when exactly one account has this house number and street, near the point (closer when
        # the point did hit a parcel, so a good point hit is not traded for a far one)
        q, qurl, n = hcad.parcel_by_address(num, _street(address), _zip(address), lat, lon,
                                            max_m=1000 if not p else 250)
        if q:
            p, purl, how = q, qurl, "address"
        elif not p:
            return evidence("county", "no_evidence", "No Harris County parcel at this point.",
                            detail=f"HCAD Parcels MapServer point query returned nothing: {purl}. Address lookup "
                                   f"by house number and street found {n} matching accounts"
                                   f"{' (ambiguous, not guessed)' if n > 1 else ''}: {qurl}")
    if not p:
        return evidence("county", "no_evidence", "No Harris County parcel at this point.",
                        detail=f"HCAD Parcels MapServer point query returned nothing: {purl}")
    acct = (p.get("HCAD_NUM") or p.get("acct_num") or "").strip()
    facts, zurl = hcad.building_facts(acct)
    use = facts.pop("building_use", None)
    site = " ".join(str(p.get(k) or "") for k in ("site_str_num", "site_str_name", "site_str_sfx")).strip()
    withheld = str(p.pop("confidential_flag", "") or "").strip().upper() in ("Y", "YES", "1", "T", "TRUE")
    extra = _extras(hcad.land_use(p.get("state_class")), owner_type(p.pop("owner_name_1", None), withheld))
    obs = {**facts, **extra, "parcel_id": acct, "parcel_address": site, "parcel_match": how,
           "source_name": "Harris County Appraisal District (HCAD)", "source_url": purl}
    found = ("HCAD Parcels MapServer point query" if how == "point" else
             "HCAD Parcels MapServer address query (the map point found no parcel with this house number; the "
             "one account with the same house number and street was taken)")
    if use:
        roll = (f"building_res.txt (residential, property use {use}): date_erected of the largest building -> "
                f"year_built; fixtures.txt type STY 'Story Height Index' of that building -> stories "
                f"(houses and duplex to fourplex only, not condo units); unit_count from the building type only "
                f"(single-family 1, duplex 2, triplex 3, fourplex 4)")
    else:
        roll = ("fixtures.txt type STC '# Stories' (max over buildings) -> stories; building_other.txt "
                "date_erected of the largest building -> year_built, units -> unit_count (fixtures UNT if empty), "
                "sum of im_sq_ft -> building_sf")
    detail = (f"{found} (gis.hctx.net, fields HCAD_NUM, state_class {p.get('state_class')}, tax_year "
              f"{p.get('tax_year')}) gave account {acct}; building facts from HCAD certified bulk roll {zurl}: "
              f"{roll}; state_class -> land_use; owner_name_1 -> owner_type (company or individual only, the name "
              f"is not kept).")
    summary = _summary(facts) + (" Parcel matched by address." if how == "address" else "")
    if not facts and extra:
        return evidence("county", "ok", f"Harris County parcel {acct} has no building data on the roll."
                        + (f" Land use: {extra['land_use']}." if extra.get("land_use") else ""),
                        observed=obs, detail=detail)
    if not facts:
        return evidence("county", "no_evidence",
                        f"Harris County parcel {acct} has no building data on the roll.",
                        observed=obs, detail=detail + " No building or residential rows for this account.")
    return evidence("county", "ok", summary, observed=obs, detail=detail)


def _la(address: str, lat: float, lon: float) -> dict:
    p, purl = la.parcel_at(lat, lon, _house_no(address))
    if not p:
        return evidence("county", "no_evidence", "No Los Angeles County parcel at this point.",
                        detail=f"LA County Parcel MapServer point query returned nothing: {purl}")
    facts = la.parcel_facts(p)
    extra = _extras(_clean_use(p.get("UseDescription")), None)  # the assessor layer has no owner field
    detail = (f"LA County Assessor parcel layer (public.gis.lacounty.gov LACounty_Parcel MapServer/0, "
              f"roll year {p.get('Roll_Year')}): AIN {p.get('AIN')}, situs {p.get('SitusFullAddress')}, "
              f"use {p.get('UseDescription')}; YearBuilt1 -> year_built, sum Units1-5 -> unit_count, "
              f"sum SQFTmain1-5 -> building_sf, UseDescription -> land_use. Query: {purl}")
    if facts.get("units_basis"):
        detail += f" Units: {facts['units_basis']}."
    # stories: City of LA Certificates of Occupancy (assessor publishes no story count)
    tried = []
    for num, street in ((_house_no(address), _street(address)),
                        (str(p.get("SitusHouseNo") or "").strip(), p.get("SitusStreet") or "")):
        if (num, street.upper()) in tried or not num:
            continue
        tried.append((num, street.upper()))
        row, curl = la.cofo_stories(num, street)
        if row:
            facts["stories"] = int(float(row["of_stories"]))
            detail += (f" Stories from City of LA Certificate of Occupancy (data.lacity.org 3f9m-afei, "
                       f"field of_stories) CofO {row.get('cofo_number')} issued "
                       f"{(row.get('cofo_issue_date') or '')[:10]} for permit {row.get('pcis_permit')} "
                       f"({row.get('permit_sub_type')}), dwelling units "
                       f"{row.get('of_residential_dwelling_units')}. Query: {curl}")
            break
    obs = {**facts, **extra, "parcel_id": p.get("AIN"), "parcel_address": p.get("SitusFullAddress"),
           "source_name": "Los Angeles County Assessor"
                          + (" + City of LA Certificates of Occupancy" if "stories" in facts else ""),
           "source_url": purl}
    if (facts.get("parcels_combined") or 0) > 1:
        obs["units_estimated"] = True  # a unit count summed over sibling parcels is an estimate
    if not facts:
        return evidence("county", "no_evidence",
                        f"Los Angeles County parcel {p.get('AIN')} has no building data on the roll.",
                        observed=obs, detail=detail)
    return evidence("county", "ok", _summary(facts), observed=obs, detail=detail)


def _nyc(address: str, lat: float, lon: float) -> dict:
    row, url = nyc.pluto_at(lat, lon, _house_no(address), _street(address))
    if not row:
        return evidence("county", "no_evidence", "No New York City tax lot found at this point.",
                        detail=f"NYC PLUTO (data.cityofnewyork.us 64uk-42ks) query returned nothing: {url}")
    facts = nyc.facts(row)
    extra = _extras(nyc.land_use(row.get("landuse")), owner_type(row.pop("ownername", None)))
    obs = {**facts, **extra, "parcel_id": row.get("bbl"), "parcel_address": row.get("address"),
           "source_name": "NYC Department of City Planning PLUTO", "source_url": url}
    detail = (f"NYC PLUTO tax-lot dataset (data.cityofnewyork.us 64uk-42ks), BBL {row.get('bbl')}, "
              f"address {row.get('address')}: numfloors -> stories, yearbuilt -> year_built, "
              f"unitsres -> unit_count, bldgarea -> building_sf, landuse -> land_use, ownername -> owner_type "
              f"(company or individual only, the name is not kept). Query: {url}")
    if not facts:
        return evidence("county", "no_evidence", f"NYC tax lot {row.get('bbl')} has no building data.",
                        observed=obs, detail=detail)
    return evidence("county", "ok", _summary(facts), observed=obs, detail=detail)


def with_311(ev: dict, lat: float, lon: float, counts: dict | None = None) -> dict:
    """Houston parcel Evidence plus 311 COUNTS near the building, read from the local counts index (no network;
    unchanged when the index is not built). Counts by category only, never what a request says."""
    o = ev.get("observed") or {}
    if ev.get("status") not in ("ok", "no_evidence") or not o.get("parcel_id"):
        return ev
    c = counts if counts is not None else h311.counts_near(lat, lon)
    if not c or not isinstance(c.get("total"), int) or isinstance(c.get("total"), bool):
        return ev
    obs = {**o, "houston_311_count": c["total"], "city_calls": c,
           "source_name": f"{o.get('source_name') or 'Harris County'} + City of Houston 311"}
    line = (f" City of Houston 311: {c['total']:,} service request{'' if c['total'] == 1 else 's'} within "
            f"{c['radius_m']} m since {c.get('since')}.")
    detail = (f"{ev.get('detail') or ''} 311 counts: City of Houston 311 public extract {c.get('source_url')}, "
              f"cases within {c['radius_m']} m counted by case type from a local counts-only index (no text kept).")
    return {**ev, "status": "ok", "summary": ((ev.get("summary") or "").strip() + line).strip(), "observed": obs,
            "detail": detail.strip()}


def county_record(address: str, lat: float | None = None, lon: float | None = None,
                  city: str | None = None) -> dict:
    """Evidence (source "county") from public county records. Never raises."""
    try:
        if lat is None or lon is None:
            g = geocode(address)
            if not g:
                return evidence("county", "no_evidence", "Could not place this address on a map.",
                                detail="US Census geocoder found no match; no coordinates given.")
            lat, lon = g["lat"], g["lon"]
        route = next((name for name, mod in (("houston", hcad), ("la", la), ("nyc", nyc))
                      if mod.in_area(lat, lon)), None)
        if route is None:
            return evidence("county", "no_evidence", NO_SOURCE,
                            detail=f"Point {lat:.5f},{lon:.5f} (city {city!r}) is outside Harris County, "
                                   f"Los Angeles County and New York City.")
        key = _key(_norm(address), round(lat, 5), round(lon, 5), CACHE_VERSION)
        path = cache_dir("paper", "county") / f"{key}.json"
        if cachectl.hit(path):
            ev = json.loads(path.read_text(encoding="utf-8"))
        else:
            ev = {"houston": _houston, "la": _la, "nyc": _nyc}[route](address, lat, lon)
            path.write_text(json.dumps(ev, indent=1), encoding="utf-8")
        return with_311(ev, lat, lon) if route == "houston" else ev
    except Exception as e:  # network or service trouble: report, never raise, never cache
        return evidence("county", "error", "County records could not be reached.",
                        detail=f"{type(e).__name__}: {str(e)[:300]}")
