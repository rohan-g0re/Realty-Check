"""A listing page for ONE apartment ("Unit 1204", "#207"): its beds, baths and size are judged against that unit's own
row in the building record (tools.kit.units), its own 3D scan goes first, and an unknown or repeated unit number
changes nothing. Also the county trust notes (an estimated unit count, a parcel found by address). The deep agent
runs on scripted CLI replies. Invented data only (418 Linden Grove Ave, ids 900001 / 900002, SAMPLEscan01...);
no network."""
import json

import pytest

import scripted_cli
from graph_events import check_run
from harness.graph import Toolbox, run_check, trust
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence
from tools.kit import units as ku

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
MP = 36  # ExternalReferenceType MatterportScan


def _scan(key, caption=None):
    return {"videoKey": key, "videoExternalReferenceType": MP, "caption": caption}


def _unit(num, beds, baths, sqft, scans=(), building=None):
    return {"id": 1, "unitNumber": num, "numBeds": beds, "numBaths": baths, "minSquareFeet": sqft,
            "attachments": list(scans), **({"buildingName": building} if building else {})}


RECORD = {  # the kit record of "418 Linden Grove Ave": plans A1 and B2, unit 305 in both of its buildings
    "id": 900001,
    "attachments": [_scan("SAMPLEscan02", "2BR, 2BA - 1050SF - Unit 1204")],
    "propertyApartment": {"models": [
        {"id": 71, "modelName": "A1", "numBeds": 1, "numBaths": 1,
         "units": [_unit("207", 1, 1, 640, [_scan("SAMPLEscan01")]), _unit("305", 1, 1, 640, building="Bldg 1")]},
        {"id": 72, "modelName": "B2", "numBeds": 2, "numBaths": 2,
         "units": [_unit("1204", 2, 2, 1050), _unit("305", 2, 2, 1040, building="Bldg 2"), _unit("410", 2, 2, 1050),
                   _unit("A1", 2, 2, 1050)]},
    ]},
}
MATCH = {"status": "matched", "confidence": 0.97, "property_id": "900001", "kind": "building",
         "name": "Linden Grove", "address": ADDRESS, "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
         "record": {"stories": 4, "year_built": 2012, "unit_count": 80},
         "floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 640, "matterport_model_id": "SAMPLEscan03"},
                        {"name": "B2", "beds": 2, "baths": 2, "sqft": 1050, "matterport_model_id": "SAMPLEscan04"}],
         "matterport_model_ids": ["SAMPLEscan02", "SAMPLEscan03", "SAMPLEscan04"],
         "how": "street number + street name + zip"}
TITLES = {"SAMPLEscan01": ("1BR, 1BA - 640SF", 1, 1), "SAMPLEscan02": ("2BR, 2BA - 1050SF - Unit 1204", 2, 2),
          "SAMPLEscan03": ("A1 1BR 1BA", 1, 1), "SAMPLEscan04": ("B2 2BR 2BA", 2, 2)}


def scan_summary(mid):
    title, beds, baths = TITLES[mid]
    return evidence("scan", "ok", f"The 3D scan titled '{title}'.",
                    {"model_id": mid, "model_name": title, "bedrooms": beds, "bathrooms": baths,
                     "counts_from": "model_name", "area_sqft": 900})


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(ku, "units_dir", lambda: tmp_path)
    monkeypatch.setattr(ku, "_mem", type(ku._mem)())
    (tmp_path / "900001.json").write_text(json.dumps(ku.extract(RECORD)), encoding="utf-8")
    (tmp_path / "_version.json").write_text(json.dumps({"version": ku.UNITS_VERSION}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def no_store(monkeypatch, tmp_path):
    monkeypatch.setattr(ku, "units_dir", lambda: tmp_path)  # empty: the unit store is not built
    monkeypatch.setattr(ku, "_mem", type(ku._mem)())


def claim(cid, kind, value, label=None, scope="unit"):
    return {"id": cid, "kind": kind, "value": value, "unit": "sqft" if kind == "area_sqft" else None,
            "scope": scope, "label": label, "quote": str(value), "checkable": True}


def tb(claims, units=True):
    over = {}
    if units:
        over = {"find_unit": ku.find_unit, "unit_scan": ku.unit_scan}
    t = Toolbox.fake(extract_claims=lambda s, use_model=True: [dict(c) for c in claims],
                     match_address=lambda *a, **k: dict(MATCH), scan_summary=scan_summary)
    for k, v in over.items():
        setattr(t, k, v)
    return t


def go(monkeypatch, title, claims, units=True, text=""):
    scripted_cli.install(monkeypatch)
    snap = {"url": "https://example.test/listing/unit-9999", "title": title, "address_text": ADDRESS,
            "text": text, "jsonld": []}
    events = []
    done = run_check(snap, events.append, tb(claims, units))
    check_run(events)
    return events, done


def verdicts(events):
    return {e["verdict"]["claim_id"]: e["verdict"] for e in events if e["type"] == "verdict"}


def evs(events):
    return [e["evidence"] for e in events if e["type"] == "evidence"]


def soft(v):
    for word in ("fake", "lie", "scam"):
        assert word not in v["reason"].lower()
        assert word not in (v.get("confidence") or {}).get("why", "").lower()


UNIT_1204 = [claim("c1", "bedrooms", 3, "Unit 1204"), claim("c2", "bathrooms", 2, "Unit 1204"),
             claim("c3", "area_sqft", 1050, "Unit 1204")]


# ---- the run ------------------------------------------------------------------------------------------

def test_unit_page_is_judged_against_its_own_row_and_scan(monkeypatch, store):
    events, done = go(monkeypatch, "418 Linden Grove Ave Unit 1204", UNIT_1204)
    claims = next(e["claims"] for e in events if e["type"] == "claims")
    assert [c["label"] for c in claims] == [None, None, None]  # "Unit 1204" is this home, not a floor plan
    assert done["harness"]["single_unit"] is True and done["harness"]["unit"] == "1204"
    rows = [e for e in evs(events) if e["observed"].get("unit_row")]
    assert len(rows) == 1 and rows[0]["source"] == "record"
    assert rows[0]["summary"] == "The building record lists unit 1204 as 2 beds, 2 baths, 1,050 sq ft (floor plan B2)."
    assert rows[0]["observed"]["unit"] == "1204" and rows[0]["observed"]["sqft_min"] is True
    own = [e for e in evs(events) if e["source"] == "scan" and e["observed"].get("scan_of") == "unit"]
    assert own and own[0]["observed"]["model_id"] == "SAMPLEscan02" and own[0]["observed"]["unit"] == "1204"
    v = verdicts(events)
    assert v["c1"]["verdict"] == "contradicted" and v["c1"]["found"] == "2 bedrooms"
    assert "for this home" in v["c1"]["reason"] and v["c1"]["confidence"]["band"] in ("medium", "high")
    assert v["c2"]["verdict"] == "confirmed" and v["c3"]["verdict"] == "confirmed"
    soft(v["c1"])
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert "The page is about unit 1204; the building record has a row for it." in steps


def test_unknown_unit_changes_nothing(monkeypatch, store):
    cs = [claim("c1", "bedrooms", 3, "Unit 9999"), claim("c2", "area_sqft", 1050, "Unit 9999")]
    on, done_on = go(monkeypatch, "418 Linden Grove Ave Unit 9999", cs)
    off, done_off = go(monkeypatch, "418 Linden Grove Ave Unit 9999", cs, units=False)
    assert done_on["harness"]["unit"] is None and done_on["harness"]["single_unit"] is False
    assert not [e for e in evs(on) if e["observed"].get("unit_row") or e["observed"].get("scan_of")]
    keep = lambda v: (v["claim_id"], v["verdict"], v["found"], v["source"], v["reason"])
    assert [keep(v) for v in verdicts(on).values()] == [keep(v) for v in verdicts(off).values()]
    assert next(e["claims"] for e in on if e["type"] == "claims")[0]["label"] == "Unit 9999"


def test_repeated_unit_number_beds_and_baths_cannot_settle_changes_nothing(monkeypatch, store):
    cs = [claim("c1", "area_sqft", 900, "Unit 305")]  # 305 is in both buildings, one 1-bed and one 2-bed
    on, done_on = go(monkeypatch, "418 Linden Grove Ave Unit 305", cs)
    off, _ = go(monkeypatch, "418 Linden Grove Ave Unit 305", cs, units=False)
    assert done_on["harness"]["unit"] is None and done_on["harness"]["single_unit"] is False
    assert [v["reason"] for v in verdicts(on).values()] == [v["reason"] for v in verdicts(off).values()]


def test_repeated_unit_number_settled_by_the_page_beds(monkeypatch, store):
    cs = [claim("c1", "bedrooms", 1, "Unit 305"), claim("c2", "area_sqft", 900, "Unit 305")]
    events, done = go(monkeypatch, "418 Linden Grove Ave Unit 305", cs)
    assert done["harness"]["unit"] == "305"
    v = verdicts(events)
    assert v["c1"]["verdict"] == "confirmed" and v["c2"]["verdict"] == "contradicted"  # 900 vs the row's 640
    assert "lists unit 305 as 1 bed, 1 bath, 640 sq ft" in v["c2"]["reason"]


def test_store_not_built_changes_nothing(monkeypatch, no_store):
    on, done = go(monkeypatch, "418 Linden Grove Ave Unit 1204", UNIT_1204)
    off, _ = go(monkeypatch, "418 Linden Grove Ave Unit 1204", UNIT_1204, units=False)
    assert done["harness"]["unit"] is None and done["harness"]["single_unit"] is False
    assert [v["reason"] for v in verdicts(on).values()] == [v["reason"] for v in verdicts(off).values()]
    # the real toolbox's lookups load only once the store is built
    real_like = Toolbox.fake()
    real_like.match_address = lambda *a, **k: None
    real_like.match_address.__module__ = "tools.kit.match"
    assert run_mod._unit_fns(real_like) is None
    assert run_mod._unit_fns(Toolbox.fake()) is None  # the fake toolbox looks nothing up


def test_unit_name_that_is_also_a_plan_name_stays_a_plan(monkeypatch, store):
    cs = [claim("c1", "bedrooms", 1, "Apt A1")]
    events, done = go(monkeypatch, "418 Linden Grove Ave Apt A1", cs)
    assert done["harness"]["unit"] is None  # the kit has plan A1 (and a unit A1): read as the plan, as before
    assert next(e["claims"] for e in events if e["type"] == "claims")[0]["label"] == "Apt A1"


def test_plan_page_is_not_a_unit_page(monkeypatch, store):
    cs = [claim("c1", "bedrooms", 2, "Plan B2")]
    events, done = go(monkeypatch, "418 Linden Grove Ave - Plan B2", cs, text="Plan B2\n2 beds")
    assert done["harness"]["unit"] is None
    assert next(e["claims"] for e in events if e["type"] == "claims")[0]["label"] == "Plan B2"


def test_unit_own_scan_goes_ahead_of_plan_and_building_scans(monkeypatch, store):
    run = run_mod.Run({"title": "Apartment #207", "address_text": ADDRESS, "text": ""}, lambda e: None,
                      tb([claim("c1", "bedrooms", 1)]))
    run.read_claims()
    assert run.unit == "207" and run.unit_scan_id == "SAMPLEscan01"
    assert run.resolve("")["scan_id"] == "SAMPLEscan01" and run.resolve("")["scan_how"] == "unit"
    run.scan_rooms("")
    scan = next(e for e in run.evidence.values() if e["source"] == "scan" and e["status"] == "ok")
    assert scan["observed"]["scan_of"] == "unit" and scan["observed"]["model_id"] == "SAMPLEscan01"
    c = claim("c1", "bedrooms", 1)
    assert "same_plan" not in trust.quality(scan, c) and trust.weight(scan, c) == 0.9
    row = next(e for e in run.evidence.values() if e["observed"].get("unit_row"))
    assert trust.quality(row, c) == [] and trust.weight(row, c) == 0.9  # no building-level or plan reduction


def test_unit_without_its_own_scan_uses_its_record_plans_scan(monkeypatch, store):
    run = run_mod.Run({"title": "Unit 410", "address_text": ADDRESS, "text": ""}, lambda e: None,
                      tb([claim("c1", "bedrooms", 3)]))
    run.read_claims()
    assert run.unit == "410" and run.unit_scan_id is None and run.unit_plan_scan == "SAMPLEscan04"
    run.scan_rooms("")
    scan = next(e for e in run.evidence.values() if e["source"] == "scan" and e["status"] == "ok")
    assert scan["observed"]["scan_of"] == "plan" and scan["observed"]["unit_plan"] == "B2"
    assert "same_plan" in trust.quality(scan, claim("c1", "bedrooms", 3))  # the same layout, not this home
    run.single_unit = run.single_unit_page()
    v = judge(run.claims[0], list(run.evidence.values()), single_unit=run.single_unit)
    assert v["verdict"] == "contradicted" and v["source"] == "record"  # the unit's own row outweighs the plan scan
    assert "lists unit 410 as 2 beds, 2 baths, 1,050 sq ft" in v["reason"]


# ---- the judge on a unit row ------------------------------------------------------------------------------

ROW = evidence("record", "ok", "The building record lists unit 1204 as 2 beds, 2 baths, 1,050 sq ft.",
               {"unit": "1204", "unit_row": True, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1050,
                "sqft_min": True, "unit_says": "2 beds, 2 baths, 1,050 sq ft"})


def test_unit_row_beds_mismatch_is_soft_and_confident():
    v = judge(claim("c1", "bedrooms", 3), [ROW], single_unit=True)
    assert v["verdict"] == "contradicted" and v["source"] == "record" and v["found"] == "2 bedrooms"
    assert v["reason"] == ("Listing says 3 bedrooms for this home; the building record lists unit 1204 as "
                           "2 beds, 2 baths, 1,050 sq ft.")
    assert v["confidence"]["sources"][0]["weight"] == 0.9
    soft(v)
    assert judge(claim("c1", "bedrooms", 2), [ROW], single_unit=True)["verdict"] == "confirmed"


@pytest.mark.parametrize("sqft, want", [(1050, "confirmed"), (1090, "confirmed"), (1140, "close"),
                                        (980, "close"), (1300, "contradicted")])
def test_unit_row_size_uses_the_written_size_tolerance(sqft, want):
    assert judge(claim("c3", "area_sqft", sqft), [ROW], single_unit=True)["verdict"] == want


def test_unit_row_never_decides_a_named_plan_or_an_unflagged_page():
    assert judge(claim("c1", "bedrooms", 3, "Plan A1"), [ROW], single_unit=True)["verdict"] == "cant_check"
    assert judge(claim("c1", "bedrooms", 3), [ROW])["verdict"] != "contradicted"  # without the flag: confirm only


# ---- county trust notes ------------------------------------------------------------------------------------

def county(units, **extra):
    return evidence("county", "ok", "County.", {"unit_count": units, **extra})


def test_estimated_county_unit_count_alone_never_makes_a_firm_mismatch():
    c = claim("u1", "unit_count", 120, scope="building")
    est = county(80, units_estimated=True)
    v = judge(c, [est])
    assert v["verdict"] == "close" and v["confidence"]["band"] == "low"
    assert "estimate" in v["reason"] and "estimate" in v["confidence"]["why"]
    soft(v)
    assert trust.quality(est, c) == ["units_estimated"] and trust.weight(est, c) == 0.72
    assert judge(c, [county(80)])["verdict"] == "contradicted"  # a firm county count still can
    rec = evidence("record", "ok", "Record.", {"unit_count": 80})
    assert judge(c, [est, rec])["verdict"] == "contradicted"  # the building record says the same
    assert judge(claim("u1", "unit_count", 80, scope="building"), [est])["verdict"] == "confirmed"


def test_estimated_county_count_loses_to_a_firm_record():
    c = claim("u1", "unit_count", 120, scope="building")
    rec = evidence("record", "ok", "Record.", {"unit_count": 120})
    v = judge(c, [county(80, units_estimated=True), rec])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_county_parcel_found_by_address_is_trusted_a_little_less():
    c = claim("y1", "year_built", 2012, scope="building")
    by_addr = evidence("county", "ok", "County.", {"year_built": 2012, "parcel_match": "address"})
    assert "parcel_by_address" in trust.quality(by_addr, c) and trust.weight(by_addr, c) == 0.81
    point = evidence("county", "ok", "County.", {"year_built": 2012, "parcel_match": "point"})
    assert trust.quality(point, c) == [] and trust.weight(point, c) == 0.9
