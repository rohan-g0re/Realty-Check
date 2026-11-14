"""tools.kit.units: per-unit lookup (unit rows, unit scans, unit text). Offline, invented data only."""
import json

import pytest

from tools.kit import units as ku
from tools.kit import find_unit, unit_from_text, unit_scan, units_for

MP = 36  # ExternalReferenceType MatterportScan


def scan(key, caption=None):
    return {"videoKey": key, "videoExternalReferenceType": MP, "caption": caption}


def unit(num, beds, baths, sqft, scans=(), building=None):
    return {"id": 1, "unitNumber": num, "numBeds": beds, "numBaths": baths, "minSquareFeet": sqft,
            "attachments": list(scans), **({"buildingName": building} if building else {})}


RECORD = {  # "418 Linden Grove Ave": two buildings, unit numbers repeat across them
    "id": 900001,
    "attachments": [scan("SAMPLEscan02", "2BR, 2BA - 1050SF - Unit 1204"), scan("SAMPLEscan03", "Fitness Center"),
                    scan("SAMPLEscan04", "Suite 1101-1112")],
    "propertyApartment": {"models": [
        {"id": 71, "modelName": "A1", "numBeds": 1, "numBaths": 1,
         "attachments": [scan("SAMPLEscan05", "1BR, 1BA - #0214")],
         "units": [unit("0101", 1, 1, 640, building="Bldg 1"), unit("101", 1, 1, 640, building="Bldg 2"),
                   unit("214", 1, 1, 650), unit("A321", 1, 1, 655, [scan("SAMPLEscan06")]),
                   unit("305", 1, 1, 640, building="Bldg 1")]},
        {"id": 72, "modelName": "B2  Plan", "numBeds": 2, "numBaths": 2,
         "units": [unit("1204", 2, 2, 1050), unit("1101", 2, 2, 1050, [scan("SAMPLEscan01")]),
                   unit("305", 2, 2, 1040, building="Bldg 2"), unit("410", 2, 2, 1050, building="Bldg 1")]},
        {"id": 73, "modelName": "B3", "numBeds": 2, "numBaths": 2,
         "units": [unit("410", 2, 2, 1100, building="Bldg 2"), unit("808", 2, 2, 980)]},
        {"id": 74, "modelName": "B3 (income restricted)", "numBeds": 2, "numBaths": 2,
         "attachments": [scan("SAMPLEscan07", "2BR, 2BA - Unit 808")],
         "units": [unit("808", 2, 2, 980)]},
    ]},
}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(ku, "units_dir", lambda: tmp_path)
    monkeypatch.setattr(ku, "_mem", type(ku._mem)())
    f = ku.extract(RECORD)
    (tmp_path / "900001.json").write_text(json.dumps(f), encoding="utf-8")
    (tmp_path / "_version.json").write_text(json.dumps({"version": ku.UNITS_VERSION}), encoding="utf-8")
    return "900001"


def test_units_for_rows_and_shape(store):
    us = units_for(store)
    assert len(us) == 12
    u = next(x for x in us if x["unit"] == "1101")
    assert u == {"unit": "1101", "beds": 2, "baths": 2, "sqft": 1050, "plan": "B2 Plan", "plan_id": 72,
                 "scan_ids": ["SAMPLEscan01"], "building": None}
    us[0]["scan_ids"].append("x")  # callers get copies
    assert "x" not in units_for(store)[0]["scan_ids"]
    assert ku.store_ready()


def test_units_for_empty(store):
    assert units_for("900002") == []
    assert units_for(None) == [] and units_for("../900001") == []
    assert ku.extract({"id": 900002, "propertyApartment": {"models": [{"id": 1, "units": []}]}}) is None


@pytest.mark.parametrize("text", ["1204", "#1204", "Unit 1204", "Apt 1204", "apt. 1204", "APARTMENT #1204",
                                  "01204", " unit 1204 "])
def test_find_unit_normalises(store, text):
    u = find_unit(store, text)
    assert u and u["unit"] == "1204" and u["plan"] == "B2 Plan" and u["ambiguous"] is False


def test_find_unit_letters_and_zeros(store):
    assert find_unit(store, "a321")["sqft"] == 655
    assert find_unit(store, "Unit A-0321")["scan_ids"] == ["SAMPLEscan06"]
    assert find_unit(store, "0214")["unit"] == "214"
    assert find_unit(store, "9999") is None and find_unit(store, "") is None and find_unit("900002", "1204") is None


def test_repeated_number_resolved_by_beds_baths(store):
    assert find_unit(store, "305") is None  # 1-bed in one building, 2-bed in the other
    assert find_unit(store, "305", beds=2)["plan"] == "B2 Plan"
    one = find_unit(store, "Unit 305", beds=1, baths=1)
    assert one["plan_id"] == 71 and one["building"] == "Bldg 1"


def test_repeated_number_still_ambiguous(store):
    assert find_unit(store, "410", beds=2, baths=2) is None  # two 2x2 plans: no single row fits
    assert find_unit(store, "410", beds=3) is None


def test_repeated_number_same_facts_counts_as_one(store):
    u = find_unit(store, "101")  # "0101" in Bldg 1 and "101" in Bldg 2, same plan and size
    assert u["plan"] == "A1" and u["sqft"] == 640 and u["building"] is None


def test_same_unit_under_two_plan_records(store):
    u = find_unit(store, "808")  # one home listed under two plan records: size is certain, the plan is not
    assert (u["beds"], u["sqft"], u["plan"], u["plan_id"]) == (2, 980, None, None)
    assert unit_scan(store, "808") == "SAMPLEscan07"


def test_single_row_returned_even_if_beds_disagree(store):
    assert find_unit(store, "1204", beds=3)["beds"] == 2  # the disagreement is the judge's finding


def test_unit_scan_order(store):
    assert unit_scan(store, "1101") == "SAMPLEscan01"   # the unit row's own scan
    assert unit_scan(store, "#1204") == "SAMPLEscan02"  # building scan titled with the unit
    assert unit_scan(store, "214") == "SAMPLEscan05"    # plan-level scan titled '#0214'
    assert unit_scan(store, "A321") == "SAMPLEscan06"
    assert unit_scan(store, "1101-1112") is None and unit_scan(store, "1101") != "SAMPLEscan04"
    assert unit_scan(store, "305") is None             # repeated with different facts
    assert unit_scan(store, "9999") is None and unit_scan("900002", "1204") is None
    assert ku.unit_scan_title(store, "SAMPLEscan02").endswith("Unit 1204")


@pytest.mark.parametrize("text,want", [
    ("418 Linden Grove Ave Apt 1204", "1204"),
    ("418 Linden Grove Ave #1204, Houston, TX", "1204"),
    ("418 Linden Grove Ave, Unit 4B", "4B"),
    ("https://example.com/418-linden-grove-ave/unit-1204/", "1204"),
    ("https://example.com/listing?unit=1204", "1204"),
    ("#1204", "1204"),
    ("Apartment A321 at Linden Grove", "A321"),
    ("2BR, 2BA - 966SF - Unit 503", "503"),
    ("Suite 210", "210"),
])
def test_unit_from_text_positive(text, want):
    assert unit_from_text(text) == want


@pytest.mark.parametrize("text", [
    "Plan B2", "The Aspen", "A1 – 1 Bed", "A1 - 1 Bed", "418 Linden Grove Ave", "418 Linden Grove Ave, Houston, TX 77006",
    "https://example.com/the-aspen-apartments-houston/abc123/", "Apartment 1 Bed", "2 units available",
    "#1 Best Apartments", "Unit Amenities", "Suite 1101-1112", "Unit 1939.5", "Unit 1204 and Unit 1205",
    "https://example.com/page#2", "", None,
])
def test_unit_from_text_negative(text):
    assert unit_from_text(text) is None
