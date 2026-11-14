"""Los Angeles County: assessor parcel point query (year built, units, main sq ft per building line),
plus City of LA Certificate of Occupancy rows for the number of stories (the assessor has none).

One building is often several assessor parcels: every condominium unit is its own parcel (stacked on the lot,
ParcelTypeCode 1), and some apartment complexes span adjoining lots with the same situs address. parcel_at
gathers those sibling parcels and parcel_facts sums their units, so the roll's unit count is the building's,
not one unit's. The layer carries no owner field; no owner or legal-description field is ever requested."""
from __future__ import annotations

import re

from . import net

PARCELS = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
COFO = "https://data.lacity.org/resource/3f9m-afei.json"
BBOX = (33.65, 34.85, -118.95, -117.60)  # lat_min, lat_max, lon_min, lon_max (LA County, generous)
_FIELDS = ["AIN", "SitusFullAddress", "SitusHouseNo", "SitusDirection", "SitusStreet", "SitusUnit", "SitusCity",
           "UseCode", "UseDescription", "ParcelTypeCode", "Roll_Year"] + \
          [f"{k}{i}" for i in range(1, 6) for k in ("YearBuilt", "Units", "SQFTmain")]
SIBLING_M = 100  # search radius for parcels sharing the building's street number and street


def in_area(lat: float, lon: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def _query(lat: float, lon: float, dist: int = 0, where: str = "") -> tuple[str, list[dict], bool]:
    """(query URL, attribute rows, truncated?) for parcels intersecting the point or within `dist` metres."""
    params = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(_FIELDS),
              "returnGeometry": "false", "f": "json"}
    if dist:
        params.update(distance=dist, units="esriSRUnit_Meter")
    if where:
        params["where"] = where
    url, d = net.get_json_url(PARCELS, params)
    return url, [f["attributes"] for f in d.get("features", [])], bool(d.get("exceededTransferLimit"))


def parcel_at(lat: float, lon: float, house_no: str = "") -> tuple[dict | None, str]:
    """Parcel containing the point, when it carries the listing's house number and a building.
    Otherwise look within 40 m for a parcel with the listing's house number, or with the same
    situs address as the hit (a building on a split or corner lot is often a sibling parcel).
    The returned dict carries "_members" (every parcel of the same building, itself included) and
    "_condo" / "_truncated" flags for parcel_facts."""
    url, hit, over = _query(lat, lon)
    p, purl = None, url
    if hit and hit[0].get("SQFTmain1") and (not house_no or _no(hit[0]) == house_no):
        p = hit[0]
    else:
        url2, near, _ = _query(lat, lon, 40)
        near = [a for a in near if a.get("SQFTmain1")]
        for keep in (lambda a: house_no and _no(a) == house_no,
                     lambda a: hit and a.get("SitusFullAddress") == hit[0].get("SitusFullAddress")):
            same = [a for a in near if keep(a)]
            if same:
                p, purl = max(same, key=lambda a: a.get("SQFTmain1") or 0), url2
                break
        if p is None:
            if not hit:
                return None, url2
            p = hit[0]
    return _with_siblings(p, lat, lon, hit, over), purl


def _no(a: dict) -> str:
    return str(a.get("SitusHouseNo") or "").strip()


def _is_condo(a: dict) -> bool:
    """Condominium unit parcel: an air-space parcel (ParcelTypeCode 1) or a use code ending C / E
    (010C condominium, 010E condominium conversion)."""
    code = str(a.get("UseCode") or "").strip().upper()
    return str(a.get("ParcelTypeCode") or "").strip() == "1" or (len(code) == 4 and code[3] in "CE")


def _min_units(a: dict) -> int:
    """Units implied by a residential use code: 02xx two units ... 05xx five or more. 0 when not implied."""
    code = str(a.get("UseCode") or "").strip()
    return {"02": 2, "03": 3, "04": 4, "05": 5}.get(code[:2], 0) if len(code) == 4 else 0


def _addr(a: dict) -> tuple | None:
    """(house number, direction, street) of the situs address, or None when it has no street number."""
    no, street = _no(a), " ".join(str(a.get("SitusStreet") or "").upper().split())
    if no in ("", "0") or not street:
        return None
    return no, str(a.get("SitusDirection") or "").strip().upper(), street


def _sql(s: str) -> str:
    return s.replace("'", "''")


def _with_siblings(p: dict, lat: float, lon: float, hit: list[dict], over: bool) -> dict:
    """Gather the parcels that clearly make up the same building as `p`:
    - condo units stacked on the same lot (in the point hit, same address or both without one, same map page);
    - parcels within SIBLING_M metres with the same street number, direction and street, that are condo units
      too (for a condo) or carry the same residential use class (for a 2+ unit building).
    Single-family homes and non-residential parcels are never merged."""
    condo, key = _is_condo(p), _addr(p)
    if not condo and _min_units(p) < 2:
        return p
    members, trunc = {p.get("AIN"): p}, False
    if condo:
        page = str(p.get("AIN") or "")[:7]
        for a in hit:
            if _is_condo(a) and _addr(a) == key and str(a.get("AIN") or "")[:7] == page:
                members.setdefault(a.get("AIN"), a)
        trunc = over
    if key:
        where = f"SitusHouseNo='{_sql(key[0])}' AND SitusStreet='{_sql(key[2])}'"
        _, rows, over2 = _query(lat, lon, SIBLING_M, where)
        trunc = trunc or over2
        for a in rows:
            if _addr(a) != key or _is_condo(a) != condo:
                continue
            if condo or str(a.get("UseCode") or "")[:2] == str(p.get("UseCode") or "")[:2]:
                members.setdefault(a.get("AIN"), a)
    return {**p, "_members": list(members.values()), "_condo": condo, "_truncated": trunc}


def _sf(a: dict) -> int:
    return sum(int(a.get(f"SQFTmain{i}") or 0) for i in range(1, 6))


def _units(a: dict) -> int:
    return sum(int(a.get(f"Units{i}") or 0) for i in range(1, 6))


def _year(a: dict) -> int | None:
    years = [a.get(f"YearBuilt{i}") for i in range(1, 6)]
    years = [int(y) for y in years if y and str(y).strip().isdigit() and int(y) > 1800]
    return years[0] if years else None


def parcel_facts(p: dict) -> dict:
    """year_built (main building line), unit_count and building_sf (summed over the building's parcels).
    Extra keys: parcels_combined (int, when more than one parcel was summed) and units_basis (plain words:
    how unit_count was reached, or why it is left out)."""
    members = p.get("_members") or [p]
    n = len(members)
    main = max(members, key=_sf) if n > 1 else p  # main building line: the parcel with the most floor area
    obs: dict = {}
    year = _year(main) or _year(p)
    if year:
        obs["year_built"] = year
    units = sum(_units(a) for a in members)
    sf = sum(_sf(a) for a in members)
    if units > 0:
        obs["unit_count"] = units
    if sf > 0:
        obs["building_sf"] = sf
    if not obs:
        return obs
    kind = "condominium units" if p.get("_condo") else "parcels with the same address"
    if p.get("_truncated"):
        obs.pop("unit_count", None), obs.pop("building_sf", None)
        obs["units_basis"] = (f"the building has more parcels ({kind}) than the assessor service returns "
                              f"in one query; units not summed")
    elif n > 1:
        obs["parcels_combined"] = n
        obs["units_basis"] = f"combined from {n} parcels ({kind})"
    elif p.get("_condo"):
        obs.pop("unit_count", None)
        if "building_sf" in obs:
            obs["unit_sf"] = obs.pop("building_sf")
        obs["units_basis"] = ("one unit of a condominium; the building's unit count is not on this parcel "
                              "and no sibling unit parcels were found")
    need = _min_units(main)
    if "unit_count" in obs and obs["unit_count"] < need:
        got = obs.pop("unit_count")
        obs["units_basis"] = (f"roll lists {got} unit{'' if got == 1 else 's'} on a parcel classed "
                              f"'{main.get('UseDescription')}' ({need}+ units); not used")
    return obs


def cofo_stories(house_no: str, street: str) -> tuple[dict | None, str]:
    """Latest City of LA Certificate of Occupancy with a story count at this address (city only).
    Only non-personal columns are selected."""
    num = (house_no or "").strip()
    name = re.sub(r"^(N|S|E|W)\s+", "", (street or "").strip().upper()).split(" ")[0] if street else ""
    if not num.isdigit() or not name:
        return None, ""
    params = {"$select": "cofo_number,cofo_issue_date,pcis_permit,permit_sub_type,of_stories,"
                         "of_residential_dwelling_units,address_start,street_name",
              "$where": f"address_start='{num}' and street_name like '%{name}%'",
              "$order": "cofo_issue_date DESC", "$limit": 10}
    url, rows = net.get_json_url(COFO, params)
    rows = [r for r in rows if r.get("of_stories")]
    return (rows[0] if rows else None), url
