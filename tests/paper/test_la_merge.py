"""LA County condo / multi-parcel merge in tools.paper.la. Offline: the assessor service is faked through
net.get_json_url; invented data only."""
import json
import re

import pytest

from tools.paper import county, la, net

LAT, LON = 34.0501, -118.2501  # an invented point inside LA County


def _parcel(ain, no="418", street="LINDEN GROVE AVE", use="0500", desc="Five or more apartments", ptype=0,
            year="1990", units=(10,), sf=(9000,), unit=" ", direction=" "):
    a = {"AIN": ain, "SitusHouseNo": no, "SitusDirection": direction, "SitusStreet": street, "SitusUnit": unit,
         "SitusFullAddress": f"{no} {street} {unit}".strip(), "SitusCity": "EXAMPLE CA", "UseCode": use,
         "UseDescription": desc, "ParcelTypeCode": ptype, "Roll_Year": "2026"}
    for i in range(1, 6):
        a[f"YearBuilt{i}"] = year if i == 1 else None
        a[f"Units{i}"] = units[i - 1] if i <= len(units) else None
        a[f"SQFTmain{i}"] = sf[i - 1] if i <= len(sf) else None
    return a


def _condo(ain, unit, sf=1200, no="418"):
    return _parcel(ain, no=no, use="010C", desc="Single", ptype=1, year="1985", units=(1,), sf=(sf,), unit=unit)


@pytest.fixture
def fake(monkeypatch):
    """Install a fake assessor: point hit rows, 40 m rows, and rows near the point for the address query."""
    state = {"calls": [], "hit": [], "near": [], "area": [], "over": False}

    def get_json_url(url, params=None):
        assert url == la.PARCELS
        params = params or {}
        state["calls"].append(params)
        if params.get("where"):
            m = re.match(r"SitusHouseNo='(.*)' AND SitusStreet='(.*)'$", params["where"])
            no, street = m.group(1), m.group(2).replace("''", "'")
            rows = [a for a in state["area"] if a["SitusHouseNo"] == no and a["SitusStreet"] == street]
        elif params.get("distance"):
            rows = state["near"]
        else:
            rows = state["hit"]
        d = {"features": [{"attributes": dict(a)} for a in rows]}
        if state["over"] and params.get("where"):
            d["exceededTransferLimit"] = True
        return url + "?fake=1", d

    monkeypatch.setattr(net, "get_json_url", get_json_url)
    return state


def test_condo_unit_point_sums_sibling_units(fake):
    u1, u2, u3 = _condo("9000010001", "1", 1100), _condo("9000010002", "2", 1300), _condo("9000010003", "3", 900)
    fake["hit"] = [u1, u2]                    # two units stacked on the lot under the point
    fake["area"] = [u1, u2, u3, _condo("9000020001", "1", no="420")]  # u3 on its own footprint; 420 is another
    p, url = la.parcel_at(LAT, LON, "418")
    f = la.parcel_facts(p)
    assert f["unit_count"] == 3 and f["parcels_combined"] == 3 and f["building_sf"] == 3300
    assert f["units_basis"] == "combined from 3 parcels (condominium units)" and f["year_built"] == 1985
    assert len(fake["calls"]) == 2  # point query + one sibling query


def test_condo_without_address_merges_only_stacked_units_on_the_same_page(fake):
    blank = [_condo(f"900001{i:04d}", f"{i}", no="0") for i in range(1, 5)]
    other_page = _condo("9000020009", "9", no="0")
    fake["hit"] = blank + [other_page]
    p, _ = la.parcel_at(LAT, LON, "418")  # no parcel has 418, so the hit's own address (none) is used
    f = la.parcel_facts(p)
    assert f["unit_count"] == 4 and f["parcels_combined"] == 4
    assert "9000020009" not in [m["AIN"] for m in p["_members"]]


def test_lone_condo_unit_does_not_claim_one_unit(fake):
    u1 = _condo("9000010001", "1", 1100)
    fake["hit"], fake["area"] = [u1], [u1]
    p, _ = la.parcel_at(LAT, LON, "418")
    f = la.parcel_facts(p)
    assert "unit_count" not in f and "building_sf" not in f and f["unit_sf"] == 1100
    assert "one unit of a condominium" in f["units_basis"] and f["year_built"] == 1985


def test_multi_parcel_complex_summed_unrelated_neighbour_not_merged(fake):
    main = _parcel("9000010001", year="2019", units=(60,), sf=(70000,))
    wing = _parcel("9000010002", year="2021", units=(20,), sf=(15000,))
    neighbour = _parcel("9000020001", no="420", units=(30,), sf=(25000,))       # next door: other number
    house = _parcel("9000010003", use="0100", desc="Single", units=(1,), sf=(1500,))  # same number, other use
    north = _parcel("9000010004", direction="N", units=(12,), sf=(9000,))        # N Linden Grove: other street
    fake["hit"] = [wing]
    fake["area"] = [main, wing, neighbour, house, north]
    p, _ = la.parcel_at(LAT, LON, "418")
    f = la.parcel_facts(p)
    assert f["unit_count"] == 80 and f["parcels_combined"] == 2 and f["building_sf"] == 85000
    assert f["year_built"] == 2019  # from the main building line (largest floor area), not the hit
    assert f["units_basis"] == "combined from 2 parcels (parcels with the same address)"
    assert sorted(m["AIN"] for m in p["_members"]) == ["9000010001", "9000010002"]


def test_single_ordinary_parcel_unchanged(fake):
    home = _parcel("9000010001", use="0100", desc="Single", year="1950", units=(1,), sf=(1800,))
    fake["hit"] = [home]
    p, url = la.parcel_at(LAT, LON, "418")
    assert la.parcel_facts(p) == {"year_built": 1950, "unit_count": 1, "building_sf": 1800}
    assert len(fake["calls"]) == 1 and "_members" not in p and url.endswith("?fake=1")
    one = _parcel("9000010002", units=(12,), sf=(10000,))
    assert la.parcel_facts(one) == {"year_built": 1990, "unit_count": 12, "building_sf": 10000}


def test_five_plus_parcel_listing_one_unit_is_not_used(fake):
    odd = _parcel("9000010001", year="2021", units=(1,), sf=(32000,))
    fake["hit"], fake["area"] = [odd], [odd]
    f = la.parcel_facts(la.parcel_at(LAT, LON, "418")[0])
    assert "unit_count" not in f and f["building_sf"] == 32000 and "not used" in f["units_basis"]


def test_too_many_siblings_for_one_query_is_not_summed(fake):
    u1 = _condo("9000010001", "1")
    fake["hit"], fake["area"], fake["over"] = [u1], [u1, _condo("9000010002", "2")], True
    f = la.parcel_facts(la.parcel_at(LAT, LON, "418")[0])
    assert "unit_count" not in f and "units not summed" in f["units_basis"]


def test_county_evidence_carries_combined_units(fake, monkeypatch):
    u1, u2 = _condo("9000010001", "1"), _condo("9000010002", "2")
    fake["hit"], fake["area"] = [u1, u2], [u1, u2]
    monkeypatch.setattr(la, "cofo_stories", lambda *a: (None, ""))
    ev = county._la("418 Linden Grove Ave, Example, CA 90000", LAT, LON)
    o = ev["observed"]
    assert ev["status"] == "ok" and o["unit_count"] == 2 and o["units_basis"].startswith("combined from 2 parcels")
    assert "_members" not in json.dumps(ev) and "2 units" in ev["summary"]
