"""Land use, owner type and Houston 311 counts on county Evidence. Offline, invented data only."""
import json

from tools.paper import county, h311, hcad, nyc
from tools.paper.owner import owner_type

HEADER = ("365 Case Number|Case Number|Incident Address|Latitude|Longitude|Status|Created Date Local|Closed Date|"
          "Title|Incident Case Type|Description")
LAT, LON = 29.7601, -95.3701  # an invented point inside Harris County


def _row(n, lat, lon, day, typ, text="caller Sam at 555-0100 says hello"):
    return f"{n}|C{n}|418 LINDEN GROVE AVE|{lat}|{lon}|Closed|{day} 08:00:00|{day}|t|{typ}|{text}"


def _index():
    lines = [HEADER,
             _row(1, LAT, LON, "2026-01-03", "Water Leak"),
             _row(2, LAT + 0.0003, LON, "2026-02-03", "Water Leak"),
             _row(3, LAT, LON + 0.0004, "2026-03-03", "Pothole"),
             "continued description line | with a pipe",
             _row(4, LAT + 0.01, LON, "2026-04-03", "Pothole"),        # ~1.1 km away
             _row(5, 40.0, -74.0, "2026-05-03", "Pothole"),            # outside Harris County
             _row(6, "bad", LON, "2026-05-03", "Pothole")]
    return h311.build_from_lines(lines, "https://example.test/311.txt")


def test_311_index_keeps_counts_only():
    idx = _index()
    blob = json.dumps(idx)
    assert idx["rows"] == 4 and idx["since"] == "2026-01-03" and idx["until"] == "2026-04-03"
    assert "Sam" not in blob and "555" not in blob and "LINDEN" not in blob and "C1" not in blob


def test_311_counts_near_by_category():
    c = h311.counts_near(LAT, LON, idx=_index())
    assert c["total"] == 3 and c["by_type"] == {"Water Leak": 2, "Pothole": 1} and c["radius_m"] == 100
    assert h311.counts_near(40.0, -74.0, idx=_index()) is None  # outside Houston: no count at all
    assert h311.counts_near(LAT, LON, idx={}) is None  # no index built: None, never a network call


def test_owner_type_words_only():
    assert owner_type("LINDEN GROVE APARTMENTS LP") == "company"
    assert owner_type("Sample Holdings LLC") == "company"
    assert owner_type("CITY OF EXAMPLE") == "company"
    assert owner_type("DOE JANE & JOHN") == "individual"
    assert owner_type("Jane Q Doe") == "individual"
    assert owner_type("") is None and owner_type(None) is None
    assert owner_type("CONFIDENTIAL") is None and owner_type("Jane Doe", withheld=True) is None
    assert owner_type("12345 Example") is None  # neither: not guessed


def test_land_use_maps():
    assert hcad.land_use("B1") == "Apartments (multi-family)" and hcad.land_use("x2") == "Tax-exempt"
    assert hcad.land_use("") is None and hcad.land_use("Q9") is None
    assert nyc.land_use("3") == "Multi-family elevator buildings" and nyc.land_use("99") is None


def test_houston_evidence_has_land_use_and_owner_type_never_the_name(monkeypatch):
    parcel = {"HCAD_NUM": "0000000900001", "site_str_num": 418, "site_str_name": "LINDEN GROVE", "site_str_sfx": "AVE",
              "state_class": "B1", "tax_year": "2026", "owner_name_1": "LINDEN GROVE OWNER LLC",
              "confidential_flag": "N"}
    monkeypatch.setattr(hcad, "parcel_at", lambda *a: (dict(parcel), "https://example.test/parcels?q=1"))
    monkeypatch.setattr(hcad, "building_facts", lambda acct: ({"stories": 5, "year_built": 2016, "unit_count": 100},
                                                               "https://example.test/roll.zip"))
    ev = county._houston("418 Linden Grove Ave, Houston, TX 77006", LAT, LON)
    o = ev["observed"]
    assert ev["status"] == "ok" and o["land_use"] == "Apartments (multi-family)" and o["owner_type"] == "company"
    assert "OWNER LLC" not in json.dumps(ev) and "5 floors" in ev["summary"]
    monkeypatch.setattr(hcad, "parcel_at", lambda *a: ({**parcel, "confidential_flag": "Y"}, "u"))
    ev = county._houston("418 Linden Grove Ave, Houston, TX 77006", LAT, LON)
    assert "owner_type" not in ev["observed"]  # a withheld owner is not typed


def test_with_311_adds_counts_and_keeps_text_out():
    ev = {"source": "county", "status": "ok", "summary": "County roll lists 5 floors.", "detail": "d", "images": [],
          "observed": {"stories": 5, "parcel_id": "0000000900001", "source_name": "Invented County"}}
    out = county.with_311(ev, LAT, LON, counts=h311.counts_near(LAT, LON, idx=_index()))
    o = out["observed"]
    assert o["houston_311_count"] == 3 and o["city_calls"]["by_type"]["Water Leak"] == 2
    assert out["summary"].startswith("County roll lists 5 floors. City of Houston 311: 3 service requests")
    assert list(o).index("houston_311_count") < list(o).index("city_calls")  # the total is the first 311 key
    assert county.with_311(ev, LAT, LON, counts={}) is ev  # no index: the evidence is unchanged
    no_parcel = {**ev, "status": "no_evidence", "observed": {}}
    assert county.with_311(no_parcel, LAT, LON, counts={"total": 3}) is no_parcel
