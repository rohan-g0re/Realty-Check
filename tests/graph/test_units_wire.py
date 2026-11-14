"""Units on a listing page, wired through the run: a building known only from its units (match.units_only), and a
multi-unit page ("Unit 1204", "Unit 1310" rows) on a building with its own unit rows. A listed unit's claims are
judged against that unit's own row and scan; another unit's row or scan is an example only and never decides a
claim. Also the "Fill me in" line for units on record and done.harness.top_mismatch. The deep agent runs on
scripted CLI replies. Invented data only (418 Linden Grove Ave, ids 900001-900003, SAMPLEscan01...); no network."""
import json

import pytest

import scripted_cli
from graph_events import check_run
from harness.fill_in import build_fill_in
from harness.graph import Toolbox, run_check
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence
from tools.kit import match as kit_match
from tools.kit import units as ku
from tools.kit.plan_match import link_plan

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"

UNITS_ONLY = {  # no building record: two condo units' home listings at this address; the page is about unit 9999
    "status": "matched", "confidence": 0.8, "property_id": "units:900001,900002", "kind": "building",
    "units_only": True, "basis": "units", "name": "Linden Grove", "address": "418 Linden Grove Ave",
    "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
    "record": {"stories": None, "year_built": 2016, "unit_count": None, "building_sf": None},
    "floorplans": [], "matterport_model_ids": [], "plan_types": [], "units_on_file": ["1204", "1310"],
    "unit_plans": [
        {"name": "Unit 1204", "unit": "1204", "property_id": "900001", "beds": 2, "baths": 2, "sqft": 1050,
         "matterport_model_id": "SAMPLEscan01", "listings": 1, "example": True},
        {"name": "Unit 1310", "unit": "1310", "property_id": "900002", "beds": 1, "baths": 1, "sqft": 640,
         "matterport_model_id": None, "listings": ["Community Pool", "In-unit washer"], "example": True}],
    "unit_scans": {"SAMPLEscan01": "1204"}, "unit_not_on_record": "9999",
    "how": "No building record; units 1204 and 1310 of this building are on record, but not unit 9999 (test)"}
TITLES = {"SAMPLEscan01": ("2BR, 2BA - Unit 1204", 2, 2), "SAMPLEscan02": ("1BR, 1BA - Unit 1310", 1, 1),
          "SAMPLEscan03": ("2BR, 2BA", 2, 2), "SAMPLEscan04": ("1BR, 1BA", 1, 1), "SAMPLEscan05": ("1BR, 1BA", 1, 1)}


def scan_summary(mid):
    title, beds, baths = TITLES[mid]
    return evidence("scan", "ok", f"The 3D scan titled '{title}'.",
                    {"model_id": mid, "model_name": title, "bedrooms": beds, "bathrooms": baths,
                     "counts_from": "model_name", "area_sqft": 900})


def claim(cid, kind, value, label=None, scope="unit"):
    return {"id": cid, "kind": kind, "value": value, "unit": "sqft" if kind == "area_sqft" else None,
            "scope": scope, "label": label, "quote": str(value), "checkable": True}


def tb(claims, match, calls=None, **over):
    def summary(mid):
        if calls is not None:
            calls.append(mid)
        return scan_summary(mid)
    t = Toolbox.fake(extract_claims=lambda s, use_model=True: [dict(c) for c in claims],
                     match_address=lambda *a, **k: dict(match), scan_summary=summary,
                     record_evidence=kit_match.record_evidence)
    for k, v in over.items():
        setattr(t, k, v)
    return t


def new_run(claims, match, title="418 Linden Grove Ave", calls=None, **over):
    run = run_mod.Run({"title": title, "address_text": ADDRESS, "text": ""}, lambda e: None,
                      tb(claims, match, calls, **over))
    run.read_claims()
    return run


def evs_of(run):
    return list(run.evidence.values())


def verdicts_of(run):
    return {v["claim_id"]: v for v in run.judge_all("test")}


# ---- a building known only from its units ----------------------------------------------------------------------

def test_listed_unit_on_record_is_judged_against_its_own_row_and_scan():
    cs = [claim("c1", "bedrooms", 3, "Unit 1204"), claim("c2", "area_sqft", 1050, "Unit 1204")]
    run = new_run(cs, UNITS_ONLY)
    assert run.unit_links["unit 1204"]["unit"] == "1204" and run.unit_links["unit 1204"]["scan"] == "SAMPLEscan01"
    assert run.resolve("Unit 1204")["scan_id"] == "SAMPLEscan01"
    run.scan_rooms("Unit 1204")
    rec = next(e for e in evs_of(run) if e["observed"].get("unit_listed") and e["source"] == "record")
    assert rec["summary"] == "The building record lists unit 1204 as 2 beds, 2 baths, 1,050 sq ft."
    assert rec["observed"]["plan"] == "Unit 1204" and "home listing 900001" in rec["detail"]
    scan = next(e for e in evs_of(run) if e["source"] == "scan")
    assert scan["observed"]["plan"] == "Unit 1204" and scan["observed"]["unit"] == "1204"
    v = verdicts_of(run)
    v1, v2 = v["c1"], v["c2"]
    assert v1["verdict"] == "contradicted" and v1["source"] == "record" and v1["found"] == "2 bedrooms"
    assert v1["reason"] == ("Listing says 3 bedrooms for unit 1204; the building record lists unit 1204 as 2 beds, "
                            "2 baths, 1,050 sq ft.")
    assert v1["confidence"]["band"] in ("medium", "high")
    assert v2["verdict"] == "confirmed"
    for word in ("fake", "lie", "scam"):
        assert word not in v1["reason"].lower()


def test_other_units_are_examples_only_and_never_a_mismatch():
    cs = [claim("c1", "bedrooms", 3), claim("c2", "area_sqft", 2000), claim("c3", "bedrooms", 3, "Unit 9999")]
    run = new_run(cs, UNITS_ONLY, title="418 Linden Grove Ave Unit 9999")
    assert run.unit_links == {}
    run.scan_rooms("")
    run.scan_rooms("Unit 9999")
    run.building_record()
    ex = [e for e in evs_of(run) if e["observed"].get("example_unit")]
    says = sorted(e["summary"] for e in ex if e["source"] == "record")
    assert says == ["Unit 1204 of this building (on record): 2 beds, 2 baths, 1,050 sq ft.",
                    "Unit 1310 of this building (on record): 1 bed, 1 bath, 640 sq ft."]
    scan = next(e for e in ex if e["source"] == "scan")
    assert scan["observed"]["example_unit"] == {"unit": "1204", "bedrooms": 2, "bathrooms": 2, "area_sqft": 900}
    assert not any(k in scan["observed"] for k in ("bedrooms", "bathrooms", "area_sqft", "plan"))
    assert len(ex) == 3  # once per run, however many checks ask
    run.single_unit = run.single_unit_page()
    for c in cs:
        v = judge(c, evs_of(run), single_unit=run.single_unit)
        assert v["verdict"] == "cant_check", v


def test_year_built_from_the_units_record_checks_normally():
    c = claim("y1", "year_built", 2016, scope="building")
    run = new_run([c], UNITS_ONLY)
    run.building_record()
    assert judge(c, evs_of(run))["verdict"] == "confirmed"
    assert judge(claim("y1", "year_built", 1990, scope="building"), evs_of(run))["verdict"] == "contradicted"


def test_unit_listing_features_may_confirm_an_amenity_never_deny_one():
    pool, gym = claim("a1", "amenity", "Resort-style pool", scope="building"), \
        claim("a2", "amenity", "Fitness center", scope="building")
    run = new_run([pool, gym], UNITS_ONLY)
    run.building_record()
    am = next(e for e in evs_of(run) if e["observed"].get("amenities"))
    assert am["source"] == "record" and am["observed"]["amenities"] == ["Pool"]
    v = judge(pool, evs_of(run))
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    assert judge(gym, evs_of(run))["verdict"] != "contradicted"
    plain = {**UNITS_ONLY, "unit_plans": [{**p, "listings": 1} for p in UNITS_ONLY["unit_plans"]]}
    run = new_run([pool], plain)
    run.building_record()
    assert not any(e["observed"].get("amenities") for e in evs_of(run))  # a count, not features: nothing


def test_units_only_run_end_to_end(monkeypatch):
    scripted_cli.install(monkeypatch)
    cs = [claim("c1", "bedrooms", 3, "Unit 1204"), claim("c2", "bedrooms", 3, "Unit 9999"),
          claim("y1", "year_built", 2016, scope="building")]
    snap = {"url": "https://example.test/listing/9999", "title": "418 Linden Grove Ave Unit 9999",
            "address_text": ADDRESS, "text": "", "jsonld": []}
    events = []
    done = run_check(snap, events.append, tb(cs, UNITS_ONLY))
    check_run(events)
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert steps.count("Unit 9999 is not in our records.") == 1
    v = {e["verdict"]["claim_id"]: e["verdict"] for e in events if e["type"] == "verdict"}
    assert v["c1"]["verdict"] == "contradicted" and v["c2"]["verdict"] == "cant_check"
    assert v["y1"]["verdict"] == "confirmed"
    facts = next(e["facts"] for e in events if e["type"] == "fill_in")
    row = next(f for f in facts if f["id"] == "units-on-record")
    assert row["value"] == "We have units 1204 and 1310 of this building on record, with a 3D scan of unit 1204."
    assert done["harness"]["top_mismatch"] == {"claim_id": "c1", "why": "", "by": "rule"}


@pytest.mark.parametrize("plans, scans, want", [
    ([{"unit": "1204", "matterport_model_id": "SAMPLEscan01"}], {"SAMPLEscan01": "1204"},
     "We have unit 1204 of this building on record, with a 3D scan."),
    ([{"unit": "1204"}, {"unit": "1310"}], {}, "We have units 1204 and 1310 of this building on record."),
    ([{"unit": "1204"}, {"unit": "1310"}], {"SAMPLEscan01": "1204", "SAMPLEscan02": "1310"},
     "We have units 1204 and 1310 of this building on record, with 3D scans.")])
def test_fill_in_names_the_units_on_record(plans, scans, want):
    m = {**UNITS_ONLY, "unit_plans": plans, "unit_scans": scans}
    row = next(f for f in build_fill_in(m, [], []) if f["id"] == "units-on-record")
    assert row["value"] == want and row["source"] == "record" and row["topic"] == "Homes & floor plans"
    assert not [f for f in build_fill_in({**m, "units_only": False}, [], []) if f["id"] == "units-on-record"]


# ---- a multi-unit page on a building with its own unit rows ----------------------------------------------------

MP = 36


def _unit(num, beds, baths, sqft, scans=()):
    return {"id": 1, "unitNumber": num, "numBeds": beds, "numBaths": baths, "minSquareFeet": sqft,
            "attachments": [{"videoKey": k, "videoExternalReferenceType": MP} for k in scans]}


RECORD = {"id": 900003, "attachments": [], "propertyApartment": {"models": [
    {"id": 81, "modelName": "A1", "numBeds": 1, "numBaths": 1,
     "units": [_unit("1310", 1, 1, 640, ["SAMPLEscan02"]), _unit("1311", 1, 1, 640, ["SAMPLEscan04"]),
               _unit("1312", 1, 1, 640, ["SAMPLEscan05"])]},
    {"id": 82, "modelName": "B2", "numBeds": 2, "numBaths": 2, "units": [_unit("1204", 2, 2, 1050, ["SAMPLEscan03"])]},
]}}
BUILDING = {"status": "matched", "confidence": 0.97, "property_id": "900003", "kind": "building",
            "name": "Linden Grove", "address": ADDRESS, "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
            "record": {"stories": 4, "year_built": 2016, "unit_count": 80},
            "floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 640, "matterport_model_id": None},
                           {"name": "B2", "beds": 2, "baths": 2, "sqft": 1050, "matterport_model_id": None}],
            "matterport_model_ids": [], "how": "street number + street name + zip"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(ku, "units_dir", lambda: tmp_path)
    monkeypatch.setattr(ku, "_mem", type(ku._mem)())
    (tmp_path / "900003.json").write_text(json.dumps(ku.extract(RECORD)), encoding="utf-8")
    (tmp_path / "_version.json").write_text(json.dumps({"version": ku.UNITS_VERSION}), encoding="utf-8")


def units_tb():
    return {"find_unit": ku.find_unit, "unit_scan": ku.unit_scan}


def test_each_listed_unit_gets_its_own_row_and_scan(store):
    cs = [claim("c1", "bedrooms", 2, "Unit 1204"), claim("c2", "bedrooms", 2, "Unit 1310"),
          claim("c3", "area_sqft", 640, "Unit 1310")]
    run = new_run(cs, BUILDING, **units_tb())
    assert {k: x["unit"] for k, x in run.unit_links.items()} == {"unit 1204": "1204", "unit 1310": "1310"}
    assert run.model_for("Unit 1310") == "SAMPLEscan02" and run.model_for("Unit 1204") == "SAMPLEscan03"
    run.check_floor_plans()
    v = verdicts_of(run)
    assert v["c1"]["verdict"] == "confirmed"
    assert v["c2"]["verdict"] == "contradicted" and v["c2"]["found"] == "1 bedroom" and v["c2"]["source"] == "record"
    assert v["c3"]["verdict"] == "confirmed"
    rows = [e for e in evs_of(run) if e["observed"].get("unit_listed") and e["source"] == "record"]
    assert sorted(e["observed"]["unit"] for e in rows) == ["1204", "1310"]


def test_unit_scans_are_capped_per_run(store):
    cs = [claim(f"c{u}", "bedrooms", 1, f"Unit {u}") for u in ("1310", "1311", "1312", "1204")]
    calls = []
    run = new_run(cs, BUILDING, calls=calls, **units_tb())
    assert sum(1 for x in run.unit_links.values() if x["scan"]) == run_mod.MAX_UNIT_SCANS == 3
    capped = run.unit_links["unit 1204"]
    assert capped["capped"] and capped["scan"] is None
    run.check_floor_plans()
    assert len(set(calls)) == 3 and "SAMPLEscan03" not in calls
    note = next(e for e in evs_of(run) if e["observed"].get("scan_capped"))
    assert note["status"] == "no_evidence" and "at most 3 unit scans" in note["summary"]


def test_no_scan_of_a_listed_unit_checks_its_own_scan_first(store):
    c = claim("f1", "amenity", "In-unit washer and dryer", label="Unit 1310")
    run = new_run([c, claim("c1", "bedrooms", 1, "Unit 1310"), claim("c2", "bedrooms", 2, "Unit 1204")], BUILDING,
                  **units_tb())
    out = run._unit_features([(c, ("washer_dryer", "a washer and dryer", "scan"))])
    assert not any("No 3D scan of Unit 1310" in e["summary"] for e in out)
    assert any(e["observed"].get("model_id") == "SAMPLEscan02" for e in out)


def test_unknown_listed_unit_and_fact_labels_fall_back_to_plan_pairing(store):
    fact = "2 bed, 2 bath, 1,050 sq ft"
    cs = [claim("c1", "bedrooms", 2, "Unit 9999"), claim("c2", "bedrooms", 2, fact),
          claim("c3", "bathrooms", 2, fact), claim("c4", "area_sqft", 1050, fact)]
    run = new_run(cs, BUILDING, **units_tb())
    assert run.unit_links == {}
    link = link_plan(fact, BUILDING, cs)  # a fact label pairs by beds, baths and size, as a plan name would
    assert link["kit"]["name"] == "B2" and link["how"] == "size"


# ---- done.harness.top_mismatch -----------------------------------------------------------------------------------

def test_top_mismatch_failure_never_breaks_a_run(monkeypatch):
    from harness import top_mismatch
    scripted_cli.install(monkeypatch)
    monkeypatch.setattr(top_mismatch, "pick_top", lambda *a, **k: 1 / 0)
    snap = {"url": "https://example.test/listing/1", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
            "text": "", "jsonld": []}
    events = []
    done = run_check(snap, events.append, tb([claim("y1", "year_built", 1990, scope="building")], UNITS_ONLY))
    check_run(events)
    assert done["type"] == "done" and done["harness"]["top_mismatch"] is None


def test_top_mismatch_uses_the_stubbed_model_for_several(monkeypatch):
    from harness import top_mismatch
    top_mismatch._CACHE.clear()
    scripted_cli.install(monkeypatch)
    monkeypatch.setattr(run_mod, "TOP_MISMATCH_LLM",
                        lambda p: json.dumps({"claim_id": "y1", "why": "The building's age matters here."}))
    cs = [claim("c1", "bedrooms", 3, "Unit 1204"), claim("y1", "year_built", 1990, scope="building")]
    snap = {"url": "https://example.test/listing/2", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
            "text": "", "jsonld": []}
    done = run_check(snap, lambda e: None, tb(cs, UNITS_ONLY))
    assert done["harness"]["top_mismatch"] == {"claim_id": "y1", "why": "The building's age matters here.",
                                               "by": "model"}
    top_mismatch._CACHE.clear()
