"""HCAD residential index (building_res.txt + fixtures STY) and the address fallback. Offline, invented data only."""
import json

import pytest

from tools.paper import county, hcad

LAT, LON = 29.7601, -95.3701  # an invented point inside Harris County
OTHER = ["acct\tbld_num\tdate_erected\tim_sq_ft\tunits",
         "0000000900009\t1\t2016\t90000\t100"]
RES = ["acct\tproperty_use_cd\tbld_num\tdate_erected\tim_sq_ft",
       "0000000900001  \tA1\t1\t1948\t1176",          # house: one building
       "0000000900002\tB2\t1\t1990\t600",             # duplex: garage apartment is building 1 (smaller)
       "0000000900002\tB2\t2\t1931\t2400",            # main building
       "0000000900003\tZ4\t1\t1977\t811",             # condo unit: STY is the unit's, not the building's
       "0000000900004\tA1\t1\t\t1500",                # no year on the roll
       "short\trow"]
FIX = ["acct\tbld_num\ttype\ttype_dscr\tunits",
       "0000000900001\t1\tSTY\tStory Height Index\t1.50",
       "0000000900002\t1\tSTY\tStory Height Index\t1.00",
       "0000000900002\t2\tSTY\tStory Height Index\t2.00",
       "0000000900003\t1\tSTY\tStory Height Index\t1.00",
       "0000000900009\t1\tSTC\t# Stories\t5",
       "0000000900001\t1\tRMB\tBedrooms\t3"]


@pytest.fixture
def state(tmp_path):
    idx, resd = hcad.build_from_lines(OTHER, RES, FIX)
    p = tmp_path / "res_2099.bin"
    hcad.write_res(p, resd)
    return idx, "https://example.test/roll.zip", p


def test_story_rows_are_kept_for_residential_accounts():
    idx, resd = hcad.build_from_lines(OTHER, RES, FIX)
    assert resd[900001] == (1948, 3, "A1")       # 1.5 stories stored as 3 half-stories
    assert resd[900002] == (1931, 4, "B2")       # STY of the main (largest) building, not the max of any
    assert resd[900004] == (0, 0, "A1")
    assert idx["0000000900009"]["STC"] == {"1": 5.0} and "0000000900001" not in idx


def test_residential_account_gets_year_and_floors(state):
    obs, url = hcad.building_facts("0000000900001", state)
    assert obs == {"year_built": 1948, "stories": 1.5, "unit_count": 1, "building_use": "A1"}
    obs, _ = hcad.building_facts("0000000900002", state)
    assert obs["year_built"] == 1931 and obs["stories"] == 2 and isinstance(obs["stories"], int)
    assert obs["unit_count"] == 2
    obs, _ = hcad.building_facts("0000000900003", state)
    assert obs == {"year_built": 1977, "building_use": "Z4"}  # condo unit: no floors, no unit count
    assert hcad.building_facts("0000000900004", state)[0] == {"unit_count": 1, "building_use": "A1"}
    assert hcad.building_facts("0000000900005", state)[0] == {}
    obs, _ = hcad.building_facts("0000000900009", state)  # other buildings unchanged
    assert obs == {"stories": 5, "year_built": 2016, "building_sf": 90000, "unit_count": 100}
    assert hcad.res_lookup(state[2], "not-a-number") is None


def test_houston_residential_evidence(monkeypatch, state):
    parcel = {"HCAD_NUM": "0000000900001", "site_str_num": 418, "site_str_name": "LINDEN GROVE",
              "site_str_sfx": "AVE", "state_class": "A1", "tax_year": "2026", "owner_name_1": "DOE JANE",
              "confidential_flag": "N"}
    monkeypatch.setattr(hcad, "parcel_at", lambda *a: (dict(parcel), "https://example.test/parcels?q=1"))
    monkeypatch.setattr(hcad, "_index", lambda: state)
    ev = county._houston("418 Linden Grove Ave, Houston, TX 77006", LAT, LON)
    o = ev["observed"]
    assert ev["status"] == "ok" and o["year_built"] == 1948 and o["stories"] == 1.5 and o["unit_count"] == 1
    assert o["parcel_match"] == "point" and "building_use" not in o and o["owner_type"] == "individual"
    assert "building_res.txt" in ev["detail"] and "DOE" not in json.dumps(ev)


def _feat(acct, num=418, name="LINDEN GROVE", sfx="AVE", zip5="77006", lat=LAT, lon=LON):
    ring = [[lon - 0.0001, lat - 0.0001], [lon + 0.0001, lat - 0.0001], [lon + 0.0001, lat + 0.0001],
            [lon - 0.0001, lat + 0.0001]]
    return {"attributes": {"HCAD_NUM": acct, "site_str_pfx": "", "site_str_num": num, "site_str_name": name,
                           "site_str_sfx": sfx, "site_zip": zip5, "state_class": "A1", "tax_year": "2026",
                           "owner_name_1": "DOE JANE", "confidential_flag": "N"},
            "geometry": {"rings": [ring]}}


def test_split_street():
    assert hcad.split_street("N Linden Grove Ave Apt 5") == ("N", "LINDEN GROVE", "AVE")
    assert hcad.split_street("Linden Grove Avenue #2") == ("", "LINDEN GROVE", "AVE")
    assert hcad.split_street("Main") == ("", "MAIN", "")
    assert hcad.split_street("North Fwy") == ("", "NORTH", "FWY")  # the direction word is the name here
    assert hcad.split_street("S Linden Grove") == ("S", "LINDEN GROVE", "")


def test_pick_by_address_single_and_ambiguous():
    hit, n = hcad.pick_by_address([_feat("0000000900001"), _feat("0000000900001"),  # same account twice
                                   _feat("0000000900002", num=420), _feat("0000000900003", name="LINDEN")],
                                  "418", "Linden Grove Ave", "77006")
    assert n == 1 and hit["HCAD_NUM"] == "0000000900001"
    hit, n = hcad.pick_by_address([_feat("0000000900001"), _feat("0000000900002")], "418", "Linden Grove Ave")
    assert hit is None and n == 2  # two accounts at the same address: never guessed
    hit, n = hcad.pick_by_address([_feat("0000000900001"), _feat("0000000900002", zip5="77099")],
                                  "418", "Linden Grove Ave", "77006")
    assert n == 1 and hit["HCAD_NUM"] == "0000000900001"  # the zip tells them apart
    assert hcad.pick_by_address([_feat("0000000900001", sfx="ST")], "418", "Linden Grove Ave") == (None, 0)
    f = _feat("0000000900002", name="LOOP", sfx="")
    f["attributes"]["site_str_pfx"] = "S "  # roll: prefix in its own field, no suffix
    assert hcad.pick_by_address([f], "418", "S Loop W")[1] == 1
    assert hcad.pick_by_address([_feat("0000000900001", name="NORTH", sfx="FWY")], "418", "North Fwy")[1] == 1


def _houston_with(monkeypatch, state, feats):
    monkeypatch.setattr(hcad, "parcel_at", lambda *a: (None, "https://example.test/parcels?point"))
    monkeypatch.setattr(hcad.net, "get_json_url", lambda url, params: ("https://example.test/parcels?where",
                                                                      {"features": feats}))
    monkeypatch.setattr(hcad, "_index", lambda: state)
    return county._houston("418 Linden Grove Ave, Houston, TX 77006", LAT + 0.001, LON)


def test_address_fallback_single_hit_accepted(monkeypatch, state):
    ev = _houston_with(monkeypatch, state, [_feat("0000000900001")])
    o = ev["observed"]
    assert ev["status"] == "ok" and o["parcel_id"] == "0000000900001" and o["parcel_match"] == "address"
    assert o["year_built"] == 1948 and o["stories"] == 1.5
    assert "matched by address" in ev["summary"] and "address query" in ev["detail"]
    assert "DOE" not in json.dumps(ev)


def test_address_fallback_multiple_hits_rejected(monkeypatch, state):
    ev = _houston_with(monkeypatch, state, [_feat("0000000900001"), _feat("0000000900002")])
    assert ev["status"] == "no_evidence" and "parcel_id" not in (ev["observed"] or {})
    assert "2 matching accounts (ambiguous, not guessed)" in ev["detail"]


def test_address_fallback_far_hit_rejected(monkeypatch, state):
    ev = _houston_with(monkeypatch, state, [_feat("0000000900001", lat=LAT + 0.05)])  # ~5.5 km away
    assert ev["status"] == "no_evidence"


def test_la_combined_units_marked_estimated(monkeypatch):
    from tools.paper import la
    p = {"AIN": "9000019000", "SitusFullAddress": "418 LINDEN GROVE AVE", "UseDescription": "Condominium",
         "Roll_Year": "2026", "SitusHouseNo": "418", "SitusStreet": "LINDEN GROVE AVE"}
    monkeypatch.setattr(la, "parcel_at", lambda *a: (dict(p), "https://example.test/la?q=1"))
    monkeypatch.setattr(la, "cofo_stories", lambda *a: (None, "https://example.test/cofo"))
    monkeypatch.setattr(la, "parcel_facts", lambda p: {"unit_count": 24, "year_built": 2005, "parcels_combined": 24,
                                                       "units_basis": "combined from 24 parcels (condominium units)"})
    ev = county._la("418 Linden Grove Ave, Los Angeles, CA 90000", 34.05, -118.25)
    assert ev["observed"]["units_estimated"] is True
    assert "Units: combined from 24 parcels (condominium units)." in ev["detail"]
    monkeypatch.setattr(la, "parcel_facts", lambda p: {"unit_count": 1, "units_basis": "one unit of a condominium"})
    ev = county._la("418 Linden Grove Ave, Los Angeles, CA 90000", 34.05, -118.25)
    assert "units_estimated" not in ev["observed"] and "Units: one unit of a condominium." in ev["detail"]
