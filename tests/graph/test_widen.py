"""Floor plans matched by size / scan title / record, and amenity picture checks. No model, no network."""
import fakes
from graph_events import check_run
from rc_common import evidence

from harness.graph import Toolbox, judge, run_check
from harness.graph import amenities, plans
from harness.graph import run as run_mod

KIT = [{"name": "A2s", "beds": 1, "baths": 1, "sqft": 750, "matterport_model_id": None},
       {"name": "B", "beds": 1, "baths": 1, "sqft": 791, "matterport_model_id": None},
       {"name": "Es", "beds": 2, "baths": 2, "sqft": 1065, "matterport_model_id": None},
       {"name": "Plan D 2 Bedroom", "beds": 2, "baths": 2, "sqft": 1060, "matterport_model_id": None}]


def claim(kind, value, scope="unit", label=None, cid="c1", checkable=True, quote=None):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote or str(value), "checkable": checkable}


def scan_ev(title, beds, baths):
    return evidence("scan", "ok", "s", {"model_name": title, "bedrooms": beds, "bathrooms": baths})


def test_size_match_needs_same_beds_baths_and_8_percent():
    facts = {"bedrooms": 1, "bathrooms": 1, "area_sqft": 745}
    assert plans.by_size(facts, KIT)["name"] == "A2s"  # 750 beats 791
    assert plans.by_size({"bedrooms": 0, "bathrooms": 1, "area_sqft": 520}, KIT) is None
    assert plans.by_size({"bedrooms": 2, "bathrooms": 2, "area_sqft": 1300}, KIT) is None  # 18% off
    assert plans.by_name("Plan D", KIT)["name"] == "Plan D 2 Bedroom"
    assert plans.by_name("Plan B", KIT) is None  # "B" is not "Plan B"


def test_scan_title_fit_and_ambiguity():
    fp = KIT[3]
    sums = {"x1": scan_ev("2BR, 2BA - 1060 SF", 2, 2), "x2": scan_ev("Fitness Center", None, None),
            "x3": scan_ev("1BR, 1BA - 735 SF", 1, 1)}
    assert plans.pick_scan(fp, sums, set()) == ("x1", "2BR, 2BA - 1060 SF")
    sums["x4"] = scan_ev("2BR, 2BA - 1060 SF", 2, 2)
    assert plans.pick_scan(fp, sums, set()) is None  # two equal fits: not guessing
    assert plans.pick_scan(fp, sums, {"x4"}) == ("x1", "2BR, 2BA - 1060 SF")  # unassigned scan preferred


def test_feature_classification():
    f = lambda v, kind="amenity": amenities.feature_for(claim(kind, v, scope="building"))
    assert f("Resort-style pool")[0] == "pool" and f("Resort-style pool")[2] == "aerial"
    assert f("24-hour fitness center") is None
    assert f("pet wash station and fenced dog run")[0] == "dog_run"
    assert f("Pet spa") is None and f("spa") is None and f("Co-working library") is None
    assert f("In-unit washer and dryer", "laundry")[:3:2] == ("washer_dryer", "scan")
    assert f("Laundry room on every floor", "laundry") is None
    assert f("Garage parking", "parking")[0] == "parking"
    assert f("quartz counters", "other")[0] == "quartz_counters"
    assert f("Granite countertops", "other")[0] == "granite_counters"
    assert amenities.feature_for(claim("amenity", "pool", checkable=False)) is None
    assert amenities.title_rooms(claim("amenity", "24-hour fitness center"))


def feat(source, cid, presence, conf, how=None, title=None):
    obs = {"feature_check": True, "claim_ids": [cid], "feature": "pool", "presence": presence,
           "presence_confidence": conf}
    if how:
        obs.update(how=how, scan_title=title)
    return evidence(source, "ok", "s", obs, [{"path": "C:/x/top.jpg", "caption": "Top-down"}])


def test_feature_verdicts():
    c = claim("amenity", "Resort-style pool", scope="building")
    v = judge(c, [feat("aerial", "c1", "seen", 0.9)])
    assert v["verdict"] == "confirmed" and v["found"] == "Visible in the aerial photo" and v["image"] == "C:/x/top.jpg"
    v = judge(c, [feat("aerial", "c1", "not_seen", 0.85)])
    assert v["verdict"] == "close" and v["found"] == "Not visible in the aerial photo"
    assert "cannot prove" in v["reason"]
    assert judge(c, [feat("aerial", "c1", "not_seen", 0.6)])["verdict"] == "cant_check"
    assert "don't show this clearly" in judge(c, [feat("aerial", "c1", "seen", 0.5)])["reason"]
    assert judge(c, [feat("aerial", "c2", "seen", 0.9)])["verdict"] == "cant_check"  # another claim's answer
    v = judge(claim("amenity", "concierge"), [])
    assert v["verdict"] == "cant_check" and "Nothing in our data measures this" in v["reason"]
    v = judge(claim("amenity", "fitness center"), [feat("scan", "c1", "seen", 1.0, "scan_title", "Fitness Center")])
    assert v["verdict"] == "confirmed" and v["found"] == "3D scan titled 'Fitness Center'"


def test_record_plan_wording_and_missing_plan():
    rec = evidence("record", "ok", "s", {"plan": "Plan B", "kit_plan": "A2s", "bedrooms": 1, "area_sqft": 750,
                                         "plan_match": "Matched to the building's plan A2s by size."})
    v = judge(claim("area_sqft", 745, label="Plan B"), [rec])
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    assert "According to the building record, its plan A2s has 750 sq ft" in v["reason"]
    assert "by size" in v["reason"]
    # unnamed unit: one kit plan's numbers may confirm it (some plan has 1 bedroom), never contradict it
    assert judge(claim("bedrooms", 1), [rec])["verdict"] == "confirmed"
    assert judge(claim("bedrooms", 3), [rec])["verdict"] == "cant_check"
    missing = evidence("record", "no_evidence", "s", {"plan": "Plan A", "plan_missing": True})
    v = judge(claim("bedrooms", 0, label="Plan A"), [missing])
    assert v["verdict"] == "cant_check" and "not in the building's records" in v["reason"]


def test_run_with_size_matched_plan_and_pictures(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    cl = [claim("bedrooms", 1, label="Plan Q", cid="c1"), claim("area_sqft", 745, label="Plan Q", cid="c2"),
          claim("amenity", "Resort-style pool", scope="building", cid="c3"),
          claim("amenity", "Rooftop deck", scope="building", cid="c4"),
          claim("laundry", "In-unit washer and dryer", cid="c5"),
          claim("amenity", "Concierge", scope="building", cid="c6"),
          claim("bedrooms", 0, label="Plan Z", cid="c7")]
    match = {**fakes.MATCH, "floorplans": [{"name": "A2s", "beds": 1, "baths": 1, "sqft": 750,
                                            "matterport_model_id": None}],
             "matterport_model_ids": ["FAKE_E"]}
    tb = Toolbox.fake(extract_claims=lambda s: [dict(c) for c in cl], match_address=lambda a, *x: dict(match))
    calls = tb.find_features.__globals__["CALLS"]  # Toolbox.fake loads its own copy of fakes.py
    calls.clear()
    events = []
    done = run_check({"address_text": fakes.ADDRESS, "text": "x"}, events.append, tb)
    check_run(events)
    v = {e["verdict"]["claim_id"]: e["verdict"] for e in events if e["type"] == "verdict"}
    assert v["c1"]["verdict"] == "confirmed" and v["c1"]["source"] == "record" and "A2s by size" in v["c1"]["reason"]
    assert v["c2"]["verdict"] == "confirmed"
    assert v["c3"]["verdict"] == "confirmed" and v["c3"]["source"] == "aerial"
    assert v["c4"]["verdict"] == "close"
    assert v["c5"]["verdict"] == "confirmed" and v["c5"]["source"] == "scan"
    assert v["c6"]["verdict"] == "cant_check" and "Nothing in our data" in v["c6"]["reason"]
    assert "not in the building's records" in v["c7"]["reason"]
    assert calls.count("find_features:aerial") == 1 and calls.count("find_features:scan") == 1
    plan_evs = [e["evidence"] for e in events if e["type"] == "evidence" and e["evidence"]["observed"].get("kit_plan")]
    assert plan_evs and all(e["observed"]["plan"] == "Plan Q" for e in plan_evs)  # grouped by listing label
    assert done["counts"]["confirmed"] == 4
