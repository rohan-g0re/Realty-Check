""""Fill me in" fact builder (harness/fill_in.py) and its run wiring. Invented data only."""
import time

from graph_events import check_fact, check_run
from harness.fill_in import TOPICS, build_fill_in
from harness.graph import Toolbox, run_check
from harness.graph import run as run_mod
from tools.kit.facts import extract, is_company

MATCH = {
    "status": "matched", "confidence": 0.97, "property_id": "123", "kind": "building", "name": "Test Lofts",
    "address": "1 Test St, Houston, TX 77002", "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
    "record": {"stories": 6, "year_built": 2016, "unit_count": 120, "building_sf": None},
    "floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 700, "matterport_model_id": "SCANA1"},
                   {"name": "B2", "beds": 2, "baths": 2, "sqft": 1100, "matterport_model_id": None}],
    "matterport_model_ids": ["SCANA1"], "how": "test",
}
KIT = {
    "version": 1, "property_id": "123", "building_class": "B", "building_rating": 3, "style": "Mid-rise",
    "manager": "Example Residential LLC", "vacancy_percent": 5,
    "pets": {"policies": [{"type": "Dogs", "max": 2, "deposit": 250, "rent": 20}]},
    "parking": {"policies": [{"type": "Garage", "fee_min": 50, "fee_max": 90}, {"type": "Other"}]},
    "schools": [{"name": "Example Elementary", "grade": "Elementary", "type": "public", "distance_mi": 0.4,
                 "zoned": True}, {"name": "Nowhere Middle", "grade": "Middle", "type": "public"}],
    "plans": [{"name": "A1", "beds": 1, "baths": 1, "sqft_min": 700, "units": 60},
              {"name": "B2", "beds": 2, "baths": 2, "sqft_min": 1100, "sqft_max": 1150, "units": 60}],
    "listing": {"as_of": "2026-11-12", "rent_min": 1300, "rent_max": 2400, "units": 120, "available": 7,
                "plans": [{"id": 1, "name": "A1", "beds": 1, "baths": 1, "rent_min": 1300, "rent_max": 1450},
                          {"id": 2, "name": "B2", "beds": 2, "baths": 2, "rent_min": 2100, "rent_max": 2400}]},
}
CLAIMS = [
    {"id": "c1", "kind": "bedrooms", "value": 1, "unit": None, "scope": "unit", "label": "A1", "quote": "1 Bed",
     "checkable": True},
    {"id": "c2", "kind": "year_built", "value": 2016, "unit": None, "scope": "building", "label": None,
     "quote": "Built in 2016", "checkable": True},
]
SCAN = {"source": "scan", "status": "ok", "summary": "s", "detail": "",
        "observed": {"model_id": "SCANA1", "model_name": "1BR 1BA", "room_labels": ["Bedroom", "Kitchen", "Bathroom"],
                     "area_sqft": 640, "plan": "A1"},
        "images": [{"path": "C:/x/floorplan_0.jpg", "caption": "Schematic floor plan 0 of the scan"}]}
COUNTY = {"source": "county", "status": "ok", "summary": "s", "detail": "", "images": [],
          "observed": {"year_built": 2015, "unit_count": 118, "parcel_id": "0000000000001",
                       "source_name": "Example County Appraisal District"}}


def facts(claims=CLAIMS, evs=(SCAN, COUNTY), kit=KIT, text="", match=MATCH):
    out = build_fill_in(match, list(evs), claims, kit, text)
    for f in out:
        check_fact(f)
    return {f["id"]: f for f in out}, out


def test_shape_order_and_sources():
    by_id, out = facts()
    assert [TOPICS.index(f["topic"]) for f in out] == sorted(TOPICS.index(f["topic"]) for f in out)
    assert by_id["class"]["value"] == "Class B" and by_id["class"]["source"] == "record"
    assert by_id["rating"]["value"] == "3 of 5 stars"
    assert by_id["manager"]["value"] == "Example Residential LLC"
    assert by_id["plan-b2"]["value"].startswith("2 bed, 2 bath, 1,100–1,150 sq ft")
    assert "as of when it was last researched" in by_id["plan-b2"]["note"]
    rent = [f for f in out if f["topic"] == "Rent" and f["id"].startswith("rent-")]
    assert rent and all("as of 2026-11-12" in f["note"] for f in rent)
    assert by_id["rent-range"]["value"] == "$1,300–$2,400 a month"
    assert by_id["pets-dogs"]["value"] == "Allowed, up to 2, $250 deposit, $20 a month pet rent"
    assert by_id["parking-garage"]["value"] == "$50–$90 a month"
    assert by_id["county-homes"]["source"] == "county" and by_id["county-homes"]["value"] == "118"
    assert by_id["scan-rooms-a1"]["source"] == "scan" and by_id["scan-rooms-a1"]["value"] == "Bedroom, Kitchen, Bathroom"
    assert by_id["scan-size-a1"]["image"] == "C:/x/floorplan_0.jpg" and by_id["scan-rooms-a1"]["image"] is None


def test_page_topics_are_left_out():
    by_id, _ = facts()
    assert "year-built" not in by_id and "county-year-built" not in by_id  # claimed: belongs to the fact-check
    assert "floors" in by_id and "homes" in by_id                          # not claimed
    assert "plan-a1" not in by_id and "plan-b2" in by_id                   # page shows A1 only
    # the same facts disappear when the page talks about them
    claims = CLAIMS + [{"id": "c3", "kind": "stories", "value": 6, "unit": None, "scope": "building", "label": None,
                        "quote": "6 stories", "checkable": True},
                       {"id": "c4", "kind": "unit_count", "value": 120, "unit": None, "scope": "building",
                        "label": None, "quote": "120 homes", "checkable": True}]
    text = "Rent from $1,350/mo. Pet friendly: dogs welcome. Garage parking. Near great schools. Managed by Someone."
    by_id, out = facts(claims, text=text)
    for gone in ("floors", "homes", "county-homes", "rent-range", "pets-dogs", "parking-garage", "manager"):
        assert gone not in by_id, gone
    assert not any(f["id"].startswith(("rent-a1", "rent-b2", "school-")) for f in out)
    assert "class" in by_id  # never on a listing page, so still shown


def test_page_size_hides_plan_and_scan_size():
    claims = [{"id": "c1", "kind": "area_sqft", "value": 1100, "unit": "sq ft", "scope": "unit",
               "label": "2 bed / 2 bath, 1100 sq ft", "quote": "1,100 sq ft", "checkable": True}]
    by_id, _ = facts(claims)
    assert "plan-b2" not in by_id and "plan-a1" in by_id
    claims.append({"id": "c2", "kind": "area_sqft", "value": 700, "unit": "sq ft", "scope": "unit", "label": "A1",
                   "quote": "700 sq ft", "checkable": True})
    by_id, _ = facts(claims)
    assert "scan-size-a1" not in by_id and "scan-rooms-a1" in by_id


def test_unrecorded_values_are_skipped_not_negated():
    kit = {"version": 1, "property_id": "123", "listing": {"as_of": "2026-11-12", "units": 120, "available": 0,
                                                            "plans": [{"id": 1, "name": "A1", "rent_max": None}]},
           "schools": [{"name": "Far School", "grade": "High", "type": "public"}], "pets": {}, "parking": {}}
    match = {**MATCH, "record": {"stories": None, "year_built": 0, "unit_count": None, "building_sf": None}}
    by_id, out = facts([], evs=(), kit=kit, match=match)
    for gone in ("floors", "year-built", "available", "vacancy", "class", "rating", "pets", "parking", "rent-range"):
        assert gone not in by_id, gone
    assert by_id["school-far-school"]["value"] == "Public high school"  # no distance on record: none stated
    assert not any("none" in f["value"].lower() or " 0 " in f" {f['value']} " for f in out)
    # the kit extractor drops 0 / unknown enums before the builder sees them
    rec = {"id": 9, "buildingClass": 0, "buildingRating": 0, "propertyApartment": {
        "vacancyPercent": 0, "apartmentStyleType": 0, "residentialProfile": {"petPolicies": [{"petType": 0}]}}}
    x = extract(rec, {"minRent": 0, "maxRent": 0, "apartmentModels": []}, {})
    assert set(x) <= {"version", "property_id", "listing"} and "rent_min" not in x.get("listing", {})


def test_no_people_only_companies():
    assert is_company("Example Residential LLC") and is_company("Sample Property Management")
    assert not is_company("Jane Doe") and not is_company("John Q Smith") and not is_company("")
    assert not is_company("Acme LLC 555-123-4567") and not is_company("info@example.com LLC")
    rec = {"id": 9, "contacts": [{"contactRole": 7, "locationId": 1, "endDate": None},
                                 {"contactRole": 4, "locationId": 2, "endDate": None}],
           "propertyApartment": {}}
    x = extract(rec, {"propertyManagementCompanyName": "Jane Doe", "apartmentModels": []},
                {1: "John Smith", 2: "Example Residential LLC"})
    assert "owner" not in x and x["manager"] == "Example Residential LLC"
    by_id, out = facts(kit={**KIT, "manager": None, "owner": None})
    assert "manager" not in by_id and "owner" not in by_id


def test_complaints_counts_only_and_no_free_text():
    """No complaint source exists offline: nothing about complaints is produced, and free-text county or kit
    fields (which could carry 311 text or contacts) never reach a fact."""
    county = {**COUNTY, "observed": {**COUNTY["observed"], "complaints_text": "caller Jane at 555-0100 said ...",
                                     "notes": "free text"}}
    kit = {**KIT, "pets": {"policies": [{"type": "Dogs", "max": 1, "restrictions": "call Jane 555-0100"}]}}
    _, out = facts(evs=(SCAN, county), kit=kit)
    blob = " ".join(f"{f['label']} {f['value']} {f['note']}" for f in out)
    assert "Jane" not in blob and "555" not in blob and "complain" not in blob.lower()


def test_no_match_still_uses_gathered_evidence_and_is_fast():
    aerial = {"source": "aerial", "status": "ok", "summary": "s", "detail": "", "observed": {"capture_date": "2026-01-02"},
              "images": [{"path": "C:/x/topdown.jpg", "caption": "Top-down aerial photo (2026-01-02)"}]}
    t = time.perf_counter()
    by_id, out = facts([], evs=(aerial, COUNTY), kit=KIT, match={"status": "no_match", "confidence": 0, "how": "x"})
    assert time.perf_counter() - t < 0.2
    assert by_id["aerial-view"]["image"] == "C:/x/topdown.jpg"
    assert all(f["source"] in ("aerial", "county") for f in out)  # no kit facts without a matched building


def test_run_check_emits_fill_in_before_done(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    events = []
    snap = {"url": "https://example.test/l", "title": "t", "address_text": "100 Example St, Houston, TX 77002",
            "text": "2 Beds 1,100 sq ft", "jsonld": []}
    run_check(snap, events.append, Toolbox.fake())
    check_run(events)
    types = [e["type"] for e in events]
    assert types[-2] == "fill_in"
    ids = {f["id"] for f in events[-2]["facts"]}
    assert {"class", "manager", "plan-a", "pets-dogs"} <= ids
    assert "year-built" not in ids and "floors" not in ids  # the fake listing claims both


def test_run_check_survives_broken_kit_facts(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)

    def boom(*a, **k):
        raise RuntimeError("kit down")
    events = []
    run_check({"address_text": "100 Example St, Houston, TX 77002", "text": ""}, events.append,
              Toolbox.fake(kit_facts=boom))
    check_run(events)
    fill = next(e for e in events if e["type"] == "fill_in")
    assert not any(e["type"] == "error" for e in events)
    assert all(f["topic"] for f in fill["facts"])  # still built from the match and the evidence
