"""county_record and geocode: public, keyless county / city records (CONTRACT 2.4, 3)."""
from __future__ import annotations

import hashlib
import json
import re

from rc_common import cache_dir, evidence
from tools import cachectl

from . import hcad, la, net, nyc

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NO_SOURCE = "No county source wired for this location yet."


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


def _summary(obs: dict) -> str:
    bits = []
    if "stories" in obs:
        bits.append(f"{obs['stories']} floors")
    if "unit_count" in obs:
        bits.append(f"{obs['unit_count']} units")
    if "building_sf" in obs:
        bits.append(f"{obs['building_sf']:,} sq ft of building")
    s = "County roll lists " + ", ".join(bits) if bits else "County roll has the parcel"
    if "year_built" in obs:
        s += f"{',' if bits else ''} built {obs['year_built']}"
    return s + "."


def _houston(address: str, lat: float, lon: float) -> dict:
    p, purl = hcad.parcel_at(lat, lon, _house_no(address))
    if not p:
        return evidence("county", "no_evidence", "No Harris County parcel at this point.",
                        detail=f"HCAD Parcels MapServer point query returned nothing: {purl}")
    acct = (p.get("HCAD_NUM") or p.get("acct_num") or "").strip()
    facts, zurl = hcad.building_facts(acct)
    site = " ".join(str(p.get(k) or "") for k in ("site_str_num", "site_str_name", "site_str_sfx")).strip()
    obs = {**facts, "parcel_id": acct, "parcel_address": site,
           "source_name": "Harris County Appraisal District (HCAD)", "source_url": purl}
    detail = (f"HCAD Parcels MapServer point query (gis.hctx.net, fields HCAD_NUM, state_class "
              f"{p.get('state_class')}, tax_year {p.get('tax_year')}) gave account {acct}; building facts "
              f"from HCAD certified bulk roll {zurl}: fixtures.txt type STC '# Stories' (max over "
              f"buildings) -> stories; building_other.txt date_erected of the largest building -> "
              f"year_built, units -> unit_count (fixtures UNT if empty), sum of im_sq_ft -> building_sf.")
    if not facts:
        return evidence("county", "no_evidence",
                        f"Harris County parcel {acct} has no building data on the roll.",
                        observed=obs, detail=detail + " No building rows for this account (residential "
                                                      "accounts are not indexed).")
    return evidence("county", "ok", _summary(facts), observed=obs, detail=detail)


def _la(address: str, lat: float, lon: float) -> dict:
    p, purl = la.parcel_at(lat, lon, _house_no(address))
    if not p:
        return evidence("county", "no_evidence", "No Los Angeles County parcel at this point.",
                        detail=f"LA County Parcel MapServer point query returned nothing: {purl}")
    facts = la.parcel_facts(p)
    detail = (f"LA County Assessor parcel layer (public.gis.lacounty.gov LACounty_Parcel MapServer/0, "
              f"roll year {p.get('Roll_Year')}): AIN {p.get('AIN')}, situs {p.get('SitusFullAddress')}, "
              f"use {p.get('UseDescription')}; YearBuilt1 -> year_built, sum Units1-5 -> unit_count, "
              f"sum SQFTmain1-5 -> building_sf. Query: {purl}")
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
    obs = {**facts, "parcel_id": p.get("AIN"), "parcel_address": p.get("SitusFullAddress"),
           "source_name": "Los Angeles County Assessor"
                          + (" + City of LA Certificates of Occupancy" if "stories" in facts else ""),
           "source_url": purl}
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
    obs = {**facts, "parcel_id": row.get("bbl"), "parcel_address": row.get("address"),
           "source_name": "NYC Department of City Planning PLUTO", "source_url": url}
    detail = (f"NYC PLUTO tax-lot dataset (data.cityofnewyork.us 64uk-42ks), BBL {row.get('bbl')}, "
              f"address {row.get('address')}: numfloors -> stories, yearbuilt -> year_built, "
              f"unitsres -> unit_count, bldgarea -> building_sf. Query: {url}")
    if not facts:
        return evidence("county", "no_evidence", f"NYC tax lot {row.get('bbl')} has no building data.",
                        observed=obs, detail=detail)
    return evidence("county", "ok", _summary(facts), observed=obs, detail=detail)


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
        path = cache_dir("paper", "county") / f"{_key(_norm(address), round(lat, 5), round(lon, 5))}.json"
        if cachectl.hit(path):
            return json.loads(path.read_text(encoding="utf-8"))
        ev = {"houston": _houston, "la": _la, "nyc": _nyc}[route](address, lat, lon)
        path.write_text(json.dumps(ev, indent=1), encoding="utf-8")
        return ev
    except Exception as e:  # network or service trouble: report, never raise, never cache
        return evidence("county", "error", "County records could not be reached.",
                        detail=f"{type(e).__name__}: {str(e)[:300]}")
