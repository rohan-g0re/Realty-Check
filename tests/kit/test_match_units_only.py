"""A building known only from its condo units' home listings (invented data, no kit)."""
import pytest

import tools.kit.match as km
from tools.kit.normalize import street_key

NAME = "Linden Grove Condominiums"
ZIP = "08999"


def _key(num, street):
    return {"num_lo": num, "num_hi": num, "street_name": street.rsplit(" ", 1)[0], "zip": ZIP, "city": "faketown",
            "lat": 40.1, "lon": -74.6}


def home(pid, unit, beds, baths, sqft, scan=None, year=1985, name=NAME, num=418, street="Linden Grove Ave"):
    return {"property_id": pid, "kind": "home", "name": name,
            "address": f"{num} {street}, Faketown, NJ {ZIP}" + (f" (unit {unit})" if unit else ""), "unit": unit,
            "city": "faketown", "state": "NJ", "lat": 40.1, "lon": -74.6, "keys": [_key(num, street)],
            "record": {"stories": None, "year_built": year, "unit_count": None, "building_sf": None},
            "floorplans": [{"name": f"Unit {unit}" if unit else "Home", "beds": beds, "baths": baths, "sqft": sqft,
                            "matterport_model_id": scan}],
            "matterport_model_ids": [scan] if scan else []}


def building(pid, num=418, street="Linden Grove Ave"):
    return {"property_id": pid, "kind": "building", "name": NAME, "address": f"{num} {street}, Faketown, NJ {ZIP}",
            "city": "faketown", "state": "NJ", "lat": 40.1, "lon": -74.6, "keys": [_key(num, street)],
            "record": {"stories": 12, "year_built": 1985, "unit_count": 140, "building_sf": 150000.0},
            "floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 700, "matterport_model_id": None}],
            "matterport_model_ids": []}


def units():
    return [home("900001", "1204", 2, 2, 1060, "SAMPLEscan01"),
            home("900002", "1204", 2, 2, 1060, "SAMPLEscan01"),  # the same unit listed twice
            home("900003", "1310", 1, 1, 660, "SAMPLEscan02")]


ADDR = f"418 Linden Grove Ave, Faketown, NJ {ZIP}"


@pytest.fixture
def kit(monkeypatch):
    def use(props):
        by_street, cities = {}, set()
        for p in props:
            for k in p["keys"]:
                k["street"] = street_key(k["street_name"])
                by_street.setdefault(k["street"], []).append((p, k))
                cities.add(k["city"])
        monkeypatch.setattr(km, "_IDX", {"props": props, "by_street": by_street, "cities": sorted(cities)})
    return use


def test_building_page_with_only_unit_records_gets_a_units_only_match(kit):
    kit(units())
    m = km.match_address(ADDR)
    assert m["status"] == "matched" and m["units_only"] is True and m["basis"] == "units"
    assert m["kind"] == "building" and m["confidence"] == 0.8 and m["name"] == NAME
    assert m["units_on_file"] == ["1204", "1310"]  # the duplicate listing of 1204 is merged
    assert m["how"].startswith("No building record; units 1204 and 1310 of this building are on record")
    assert m["address"] == ADDR and m["lat"] == 40.1
    assert m["unit_not_on_record"] is None


def test_units_only_never_supplies_floors_unit_count_or_size(kit):
    kit(units())
    m = km.match_address(ADDR)
    assert m["record"] == {"stories": None, "year_built": 1985, "unit_count": None, "building_sf": None}
    ev = km.record_evidence(m)
    assert ev["status"] == "ok"
    assert ev["summary"] == "CoStar has units 1204 and 1310 of this building on record (built 1985)."
    assert not {"stories", "unit_count", "building_sf"} & set(ev["observed"])
    assert ev["observed"]["year_built"] == 1985 and ev["observed"]["units_on_file"] == ["1204", "1310"]


def test_units_plans_and_scans_are_tagged_examples_not_the_buildings_own(kit):
    kit(units())
    m = km.match_address(ADDR)
    assert m["floorplans"] == [] and m["matterport_model_ids"] == [] and m["plan_types"] == []
    assert [(f["unit"], f["beds"], f["baths"], f["sqft"], f["matterport_model_id"], f["listings"], f["example"])
            for f in m["unit_plans"]] == [("1204", 2, 2, 1060, "SAMPLEscan01", 2, True),
                                         ("1310", 1, 1, 660, "SAMPLEscan02", 1, True)]
    assert m["unit_plans"][0]["property_id"] == "900001"  # the lower id of the duplicate pair
    assert m["unit_scans"] == {"SAMPLEscan01": "1204", "SAMPLEscan02": "1310"}


def test_year_left_out_when_units_disagree(kit):
    kit([home("900001", "1204", 2, 2, 1060, "SAMPLEscan01"), home("900003", "1310", 1, 1, 660, year=1990)])
    m = km.match_address(ADDR)
    assert m["units_only"] and m["record"]["year_built"] is None
    ev = km.record_evidence(m)
    assert ev["status"] == "no_evidence"
    assert ev["summary"] == "CoStar has units 1204 and 1310 of this building on record."


def test_unit_page_still_matches_its_own_home_record(kit):
    kit(units())
    m = km.match_address(f"418 Linden Grove Ave #1204, Faketown, NJ {ZIP}")
    assert m["status"] == "matched" and m["kind"] == "home" and m["property_id"] == "900001"
    assert not m.get("units_only") and m["matterport_model_ids"] == ["SAMPLEscan01"]


def test_unit_page_for_a_unit_not_on_record_gets_the_units_only_match(kit):
    kit(units())
    m = km.match_address(f"418 Linden Grove Ave Unit 159, Faketown, NJ {ZIP}")
    assert m["status"] == "matched" and m["units_only"] and m["unit_not_on_record"] == "159"
    assert m["units_on_file"] == ["1204", "1310"] and "but not unit 159" in m["how"]


def test_a_real_building_record_is_unchanged(kit):
    kit([building("900010")] + units())
    m = km.match_address(ADDR)
    assert m["property_id"] == "900010" and not m.get("units_only") and m["record"]["stories"] == 12
    m = km.match_address(f"418 Linden Grove Ave #1310, Faketown, NJ {ZIP}")
    assert m["property_id"] == "900010" and "SAMPLEscan02" in m["matterport_model_ids"]  # _add_unit, as before


def test_only_one_unit_on_file_is_matched_as_that_home_as_before(kit):
    kit(units()[:2])
    m = km.match_address(ADDR)
    assert m["kind"] == "home" and m["property_id"] == "900001" and not m.get("units_only")


def test_different_building_names_are_not_combined(kit):
    kit([units()[0], home("900003", "1310", 1, 1, 660, name="Another Example Tower")])
    assert km.match_address(ADDR)["status"] == "no_match"


def test_missing_names_combine_on_the_same_address(kit):
    kit([home("900001", "1204", 2, 2, 1060, name=None), home("900003", "1310", 1, 1, 660, name=None)])
    m = km.match_address(ADDR)
    assert m["units_only"] and m["units_on_file"] == ["1204", "1310"] and m["name"] is None


def test_a_house_without_a_unit_is_not_combined(kit):
    kit([home("900001", None, 3, 2, 1800), home("900003", "1310", 1, 1, 660)])
    assert km.match_address(ADDR)["status"] == "no_match"


def test_units_words():
    assert km.units_words(["1204"]) == "unit 1204"
    assert km.units_words(["1204", "1310"]) == "units 1204 and 1310"
    assert km.units_words(["1", "2", "3"]) == "units 1, 2 and 3"
