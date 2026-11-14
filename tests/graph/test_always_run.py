"""Aerial and paper sources always run when there is a location; record amenities decide amenity claims; a
single-unit page may be contradicted by its own scan title. The deep agent runs for real on scripted CLI replies
(scripted_cli.py). Invented data only (418 Linden Grove Ave, ids 900001 / 900002, scan SAMPLEscan01)."""
import time

import scripted_cli
from graph_events import check_run
from harness.fill_in import build_fill_in
from harness.graph import Toolbox, run_check
from harness.graph import agent as agent_mod
from harness.graph import run as run_mod
from harness.graph.judge import judge
from harness.graph.run import helpers_needed
from rc_common import evidence

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
SNAP = {"url": "https://example.test/listing", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
        "text": "2 Beds 1,100 sq ft", "jsonld": []}
FAKE = Toolbox.fake()
UNITS = [c for c in FAKE.extract_claims({}) if c["kind"] in ("bedrooms", "area_sqft")]  # Plan E beds + size only


def claims_tb(claims, **over):
    return Toolbox.fake(extract_claims=lambda s, use_model=True: [dict(c) for c in claims], **over)


def go(tb):
    events = []
    done = run_check(SNAP, events.append, tb)
    assert events[-1] == done
    check_run(events)
    return events, done


def verdicts(events):
    return {e["verdict"]["claim_id"]: e["verdict"] for e in events if e["type"] == "verdict"}


def sources(events):
    return {e["evidence"]["source"] for e in events if e["type"] == "evidence" and e["evidence"]["status"] == "ok"}


def claim(cid, kind, value, label=None, scope="unit", quote=None, checkable=True):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote or str(value), "checkable": checkable}


# ---- job 1: aerial and county always run with a location ------------------------------------------------

def test_helpers_needed_adds_outside_and_paper_with_a_location():
    units = [claim("c1", "bedrooms", 2, "Plan E")]
    assert set(helpers_needed(units)) == {"inside"}  # no location: as before
    assert set(helpers_needed(units, {"status": "no_match", "lat": None, "lon": None})) == {"inside"}
    for m in ({"status": "matched", "lat": 29.75, "lon": -95.37}, {"status": "no_match", "lat": 29.75, "lon": -95.37}):
        need = helpers_needed(units, m)
        assert set(need) == {"inside", "outside", "paper"}
        assert "always" in need["outside"] and "always" in need["paper"]
    need = helpers_needed([claim("c2", "stories", 5, scope="building")], {"status": "matched"})
    assert "roof height for floors" in need["outside"] and "stories" in need["paper"]


def _calls_tb(claims, log, **over):
    def wrap(name):
        fn = getattr(FAKE, name)
        return lambda *a, **k: log.append(name) or fn(*a, **k)
    names = ("building_height", "aerial_images", "county_record", "record_evidence", "find_features")
    return claims_tb(claims, **{**{n: wrap(n) for n in names}, **over})


def test_units_only_listing_runs_every_source_in_parallel(monkeypatch):
    seen = scripted_cli.install(monkeypatch)
    log = []
    events, done = go(_calls_tb(UNITS, log))
    h = done["harness"]
    assert h["needed"] == ["inside", "outside", "paper"] == h["helpers"] and h["direct"] == []
    assert h["judged_by"] == "agent"
    assert sorted(w for w, _ in seen if w != "orchestrator") == ["inside", "outside", "paper"]
    assert [w for w, _ in seen].count("orchestrator") == 3  # one delegation turn for all three helpers
    assert {"aerial", "county", "record", "scan"} <= sources(events)
    assert "find_features" not in log  # no outdoor claim: no photo check, no extra model call
    assert log.count("building_height") == 1 and log.count("aerial_images") == 1
    assert log.count("county_record") == 1 and log.count("record_evidence") == 1
    facts = next(e["facts"] for e in events if e["type"] == "fill_in")
    ids = {f["id"] for f in facts}
    assert {"aerial-view", "roof-height", "county-floors"} <= ids  # "Fill me in" has its aerial and county rows
    roof = next(f for f in facts if f["id"] == "roof-height")
    assert "Vexcel" in roof["note"] and "Vexcel" in next(f for f in facts if f["id"] == "aerial-view")["value"]
    types = [e["type"] for e in events]
    first_verdict = types.index("verdict")
    assert all(i < first_verdict for i, e in enumerate(events) if e["type"] == "evidence"
               and e["evidence"]["source"] in ("aerial", "county"))
    assert done["timing"]["model_calls"] == 6


def test_judge_refusal_asks_for_the_always_run_helpers(monkeypatch):
    seen = scripted_cli.install(monkeypatch, skip={"outside", "paper"})
    t = time.perf_counter()
    events, done = go(_calls_tb(UNITS, []))
    assert time.perf_counter() - t < 10  # fill_in alongside the refused judge does not wait out its 15 s
    assert [w for w, _ in seen].count("outside") == 1 and [w for w, _ in seen].count("paper") == 1
    assert done["harness"]["helpers"] == ["inside", "outside", "paper"] and done["harness"]["direct"] == []
    assert {"aerial", "county"} <= sources(events)
    assert done["harness"]["fill_in_by"] == "agent"


def test_stubborn_orchestrator_gets_them_called_directly(monkeypatch):
    scripted_cli.install(monkeypatch, skip={"outside", "paper"}, stubborn=True)
    log = []
    events, done = go(_calls_tb(UNITS, log))
    h = done["harness"]
    assert h["judged_by"] == "agent" and h["direct"] == ["outside", "paper"]
    assert {"aerial", "county", "record"} <= sources(events)
    assert log.count("building_height") == 1 and log.count("county_record") == 1 and "find_features" not in log
    assert any(e["type"] == "step" and "Vexcel aerial and county checks the planner skipped" in e["text"]
               for e in events)


def test_close_calls_them_directly_within_the_budget(monkeypatch):
    monkeypatch.setattr(run_mod, "RUN_BUDGET_S", 4.0)
    monkeypatch.setattr(run_mod, "CLOSE_RESERVE_S", 0.5)
    scripted_cli.install(monkeypatch)
    real_init = agent_mod.BudgetedCLIModel.__init__
    monkeypatch.setattr(agent_mod.BudgetedCLIModel, "__init__", lambda self, **kw: real_init(self, **{**kw, "max_calls": 2}))
    slow_county = lambda *a, **k: time.sleep(6) or FAKE.county_record(*a, **k)
    t = time.perf_counter()
    events, done = go(_calls_tb(UNITS, [], county_record=slow_county))
    assert time.perf_counter() - t < 4.0
    h = done["harness"]
    assert h["judged_by"] == "close" and h["direct"] == ["outside", "paper"]
    assert "aerial" in sources(events) and "county" not in sources(events)  # the slow lookup is left out


def test_no_location_behaves_as_before(monkeypatch):
    scripted_cli.install(monkeypatch)
    log = []
    tb = _calls_tb(UNITS, log, match_address=lambda *a, **k: {"status": "no_match", "confidence": 0.0, "how": "none"},
                   geocode=lambda a: None)
    events, done = go(tb)
    assert done["harness"]["needed"] == ["inside"] and done["harness"]["direct"] == []
    assert not {"building_height", "county_record", "record_evidence"} & set(log)


# ---- job 2: amenity claims against the building record ---------------------------------------------------

REC = evidence("record", "ok", "Amenities on record.", {"amenities": ["Pool", "Fitness center"]})


def test_amenity_named_in_the_record_is_confirmed():
    v = judge(claim("a1", "amenity", "Resort-style pool", scope="building"), [REC])
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    assert v["reason"].endswith("The building record names it.")
    v = judge(claim("a2", "amenity", "24-hour fitness center", scope="building"), [REC])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_amenity_missing_from_the_record_is_never_a_mismatch():
    roof = claim("a3", "amenity", "Rooftop deck", scope="building")
    assert judge(roof, [REC])["verdict"] == "cant_check"
    photo = evidence("aerial", "ok", "x", {"feature_check": True, "claim_ids": ["a3"], "feature": "rooftop_deck",
                                            "presence": "not_seen", "presence_confidence": 0.9})
    assert judge(roof, [REC, photo])["verdict"] == "cant_check"  # not seen in a photo is absence: couldn't check
    both = claim("a4", "amenity", "Pool and rooftop deck", scope="building")
    assert judge(both, [REC])["verdict"] == "cant_check"  # every named amenity must be on record
    assert judge(claim("a5", "amenity", "Resort-style pool", scope="building", checkable=False), [REC])["verdict"] \
        == "cant_check"


def test_building_record_tool_stores_amenities_and_fill_in_uses_them(monkeypatch):
    kit = {"version": 2, "property_id": "900001", "amenities": ["Pool", "Dog park", "Package room"]}
    run = run_mod.Run(SNAP, (events := []).append, Toolbox.fake(kit_facts=lambda pid: kit))
    run.read_claims()
    run.match_listing()
    out = run.tool("paper", run.building_record)
    assert "Pool, Dog park, Package room" in out
    amen = [e for e in run.evidence.values() if "amenities" in e["observed"]]
    assert len(amen) == 1 and amen[0]["source"] == "record"
    facts = build_fill_in({"status": "matched"}, list(run.evidence.values()), [claim("c5", "amenity", "Pool")],
                          {}, "A pool.")
    row = next(f for f in facts if f["id"] == "amenities")
    assert row["value"] == "Dog park, Package room" and row["source"] == "record"


# ---- job 3: single-unit pages ----------------------------------------------------------------------------

def scan_ev(title, beds, counts_from=None, plan=None):
    obs = {"model_id": "SAMPLEscan01", "model_name": title, "bedrooms": beds,
           "room_labels": ["Bedroom"] * beds}
    if counts_from:
        obs["counts_from"] = counts_from
    if plan:
        obs["plan"] = plan
    return evidence("scan", "ok", "Scan.", obs, [{"path": "C:/x/still.jpg", "caption": "Panorama 1"}])


def test_single_unit_page_mismatch_from_the_scan_title():
    one_bed = claim("c1", "bedrooms", 1, quote="1 Bed")
    v = judge(one_bed, [scan_ev("3BR 2BA", 3)], single_unit=True)
    assert v["verdict"] == "contradicted" and v["source"] == "scan" and v["found"] == "3 bedrooms"
    assert "titled '3BR 2BA'" in v["reason"] and v["image"] == "C:/x/still.jpg"
    assert judge(one_bed, [scan_ev("3BR 2BA", 3)])["verdict"] != "contradicted"  # without the flag: never
    assert judge(one_bed, [scan_ev("Unit 2", 3)], single_unit=True)["verdict"] == "cant_check"  # labels only
    assert judge(one_bed, [scan_ev("1BR", 3)], single_unit=True)["verdict"] == "confirmed"  # the title decides
    assert judge(one_bed, [scan_ev("3BR 2BA", 3, plan="Plan B")], single_unit=True)["verdict"] != "contradicted"
    only = evidence("record", "ok", "1 plan.", {"floorplans": [{"name": "Unit 2", "beds": 2, "baths": 1, "sqft": 900}]})
    v = judge(one_bed, [only], single_unit=True)
    assert v["verdict"] == "contradicted" and v["source"] == "record"
    two = evidence("record", "ok", "2 plans.", {"floorplans": [{"name": "A", "beds": 2}, {"name": "B", "beds": 3}]})
    assert judge(one_bed, [two], single_unit=True)["verdict"] == "cant_check"


def test_single_unit_flag_is_conservative():
    def flag(claims, match):
        run = run_mod.Run(SNAP, lambda e: None, Toolbox.fake())
        run.claims, run.match = claims, match
        return run.single_unit_page()
    one = [claim("c1", "bedrooms", 1), claim("c2", "bathrooms", 1), claim("c3", "area_sqft", 650)]
    home = {"status": "matched", "kind": "home", "floorplans": [{"name": "Unit 2"}]}
    building = {"status": "matched", "kind": "building", "floorplans": [{"name": "A"}, {"name": "B"}]}
    assert flag(one, home) and flag(one, {"status": "no_match"})
    assert flag(one, {"status": "matched", "kind": "building", "floorplans": [{"name": "A"}]})
    assert not flag(one, building)  # several plans: which one is this home?
    assert not flag(one + [claim("c4", "bedrooms", 2)], home)  # several homes on the page
    assert not flag([claim("c1", "bedrooms", 1, "Plan A")], {"status": "no_match"})  # a plan list
    assert not flag([claim("c3", "area_sqft", 650)], {"status": "no_match"})  # size only
    assert not flag([], {"status": "no_match"})


def test_single_home_run_contradicts_one_bed(monkeypatch):
    scripted_cli.install(monkeypatch)
    match = {"status": "matched", "confidence": 0.97, "property_id": "900002", "kind": "home",
             "name": "418 Linden Grove Ave Unit 2", "address": ADDRESS, "city": "Houston", "state": "TX",
             "lat": 29.75, "lon": -95.37, "record": {"stories": 3, "year_built": 2001, "unit_count": None},
             "floorplans": [{"name": "Unit 2", "beds": None, "baths": None, "sqft": None,
                             "matterport_model_id": "SAMPLEscan01"}],
             "matterport_model_ids": ["SAMPLEscan01"], "how": "street number + street name + zip"}
    scan = scan_ev("3BR 2BA", 3)
    tb = claims_tb([claim("c1", "bedrooms", 1, quote="1 Bed")], match_address=lambda *a, **k: dict(match),
                   scan_summary=lambda mid: scan)
    events, done = go(tb)
    v = verdicts(events)["c1"]
    assert v["verdict"] == "contradicted" and v["source"] == "scan"
    assert done["harness"]["single_unit"] is True
    assert done["headline"].startswith("Listing says 1 bedroom, but the 3D scan shows 3 bedrooms")
