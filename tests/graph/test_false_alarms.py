"""False-alarm fixes in the judge (invented data only): unnamed unit claims, scan room-label counts,
near-miss building facts, and the measured scan floor-area band."""
import fakes
from graph_events import check_run
from rc_common import evidence

from harness.graph import Toolbox, judge, run_check
from harness.graph import plans
from harness.graph import run as run_mod


def claim(kind, value, scope="unit", label=None, cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": str(value), "checkable": True}


def ev(source, observed, status="ok", images=None):
    return evidence(source, status, "s", observed, images)


# ---- 1. unit claims with no plan name -------------------------------------------------------------

def test_unnamed_claim_is_never_contradicted_by_one_plans_scan():
    scan_b = ev("scan", {"plan": "Plan B", "model_name": "2 BR, 2 BA - 990 SF", "bedrooms": 2, "bathrooms": 2,
                         "area_sqft": 911, "counts_from": "model_name"})
    for kind, value in (("bedrooms", 1), ("bathrooms", 1), ("area_sqft", 631)):
        v = judge(claim(kind, value), [scan_b])
        assert v["verdict"] == "cant_check", (kind, v)
        assert "without naming a floor plan" in v["reason"]


def test_unnamed_claim_confirmed_when_some_plan_has_the_numbers():
    rec_a = ev("record", {"plan": "Plan A", "kit_plan": "Plan A", "bedrooms": 1, "bathrooms": 1, "area_sqft": 640})
    rec_g = ev("record", {"plan": "Plan G", "kit_plan": "Plan G", "bedrooms": 3, "bathrooms": 2, "area_sqft": 1340})
    scan_g = ev("scan", {"plan": "Plan G", "bedrooms": 1, "room_labels": ["Bedroom"], "area_sqft": 500})
    v = judge(claim("bedrooms", 3), [scan_g, rec_a, rec_g])
    assert v["verdict"] == "confirmed" and v["source"] == "record" and "plan Plan G" in v["reason"]
    assert judge(claim("bedrooms", 2), [scan_g, rec_a, rec_g])["verdict"] == "cant_check"
    assert judge(claim("area_sqft", 645), [rec_a, rec_g])["verdict"] == "confirmed"  # within 5% of 640
    assert judge(claim("area_sqft", 700), [rec_a, rec_g])["verdict"] == "cant_check"  # 9% off: not close, unknown


def test_unnamed_claim_confirmed_from_the_building_plan_list_or_a_scan_title():
    plan_list = ev("record", {"floorplans": [{"name": "S1", "beds": 0, "baths": 1, "sqft": 540},
                                             {"name": "P1", "beds": 3, "baths": 3.5, "sqft": 3000}]})
    v = judge(claim("bathrooms", 3.5), [plan_list])
    assert v["verdict"] == "confirmed" and "plan P1 has 3.5 bathrooms" in v["reason"]
    assert judge(claim("bedrooms", 2), [plan_list])["verdict"] == "cant_check"
    titled = ev("scan", {"plan": "Plan X", "model_name": "Plan X - 2 Bed / 2 Bath", "bedrooms": 5,
                         "room_labels": ["Bedroom"] * 5})
    v = judge(claim("bedrooms", 2), [titled])
    assert v["verdict"] == "confirmed" and v["source"] == "scan"  # by the title, not the 5 labels
    assert judge(claim("bedrooms", 5), [titled])["verdict"] == "cant_check"  # labels never confirm or refute


def test_unnamed_building_wide_evidence_still_judged_normally():
    # county numbers are not one plan's: an unnamed area claim is still judged against them as before
    assert judge(claim("area_sqft", 1100), [ev("county", {"area_sqft": 968})])["verdict"] == "contradicted"


def test_labelled_claim_ignores_a_scan_tied_to_no_plan():
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"bedrooms": 1, "model_name": "1BR", "counts_from": "model_name"})])
    assert v["verdict"] == "cant_check"


def test_run_gives_unnamed_claims_the_plan_list(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    cl = [claim("bedrooms", 2, cid="c1"), claim("bedrooms", 3, cid="c2"), claim("area_sqft", 720, cid="c3")]
    tb = Toolbox.fake(extract_claims=lambda s: [dict(c) for c in cl])
    events = []
    done = run_check({"address_text": fakes.ADDRESS, "text": "x"}, events.append, tb)
    check_run(events)
    v = {e["verdict"]["claim_id"]: e["verdict"] for e in events if e["type"] == "verdict"}
    assert v["c1"]["verdict"] == "confirmed" and "plan Plan E" in v["c1"]["reason"]
    assert v["c2"]["verdict"] == "cant_check"
    assert v["c3"]["verdict"] == "confirmed"
    assert done["counts"]["contradicted"] == 0 and done["counts"]["close"] == 0


# ---- 2. bed / bath counts from scan room labels ---------------------------------------------------

def test_label_count_alone_is_cant_check():
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"plan": "Plan E", "bedrooms": 5, "room_labels": ["Bedroom"] * 5})])
    assert v["verdict"] == "cant_check" and "room labels" in v["reason"]


def test_title_agreeing_with_listing_beats_the_labels():
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"plan": "Plan E", "model_name": "Plan E - 2 Bed / 2 Bath", "bedrooms": 5,
                           "room_labels": ["Bedroom"] * 5})])
    assert v["verdict"] == "confirmed" and v["source"] == "scan" and v["found"] == "2 bedrooms"
    assert "titled 'Plan E - 2 Bed / 2 Bath'" in v["reason"] and "5 bedrooms" in v["reason"]


def test_record_decides_when_labels_disagree_with_it():
    rec = ev("record", {"plan": "Plan E", "kit_plan": "Plan E", "bedrooms": 2})
    labels = ev("scan", {"plan": "Plan E", "bedrooms": 0, "room_labels": ["Other", "Den"]})
    v = judge(claim("bedrooms", 2, label="Plan E"), [labels, rec])
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    # the record backs the labels: the label count may now contradict the listing
    rec1 = ev("record", {"plan": "Plan E", "kit_plan": "Plan E", "bedrooms": 1})
    one = ev("scan", {"plan": "Plan E", "bedrooms": 1, "room_labels": ["Bedroom", "Den"]})
    v = judge(claim("bedrooms", 2, label="Plan E"), [one, rec1])
    assert v["verdict"] == "contradicted" and v["source"] == "scan" and v["found"] == "1 bedroom + den"


def test_zero_bathrooms_from_labels_is_never_used():
    zero = ev("scan", {"plan": "Plan E", "bathrooms": 0, "room_labels": ["Other", "Other"]})
    assert judge(claim("bathrooms", 2, label="Plan E"), [zero])["verdict"] == "cant_check"
    rec0 = ev("record", {"plan": "Plan E", "kit_plan": "Plan E", "bathrooms": 0})  # even if a record agreed
    v = judge(claim("bathrooms", 1, label="Plan E"), [zero, rec0])
    assert v["source"] == "record"
    titled = ev("scan", {"plan": "Plan E", "model_name": "1 Bed / 2 Bath", "bathrooms": 0,
                         "room_labels": ["Other"]})
    assert judge(claim("bathrooms", 2, label="Plan E"), [titled])["verdict"] == "confirmed"


def test_title_counts_parse():
    assert plans.title_count("bedrooms", "Studio - 539 SF") == 0
    assert plans.title_count("bedrooms", "Plan 13 1 Bedroom + Den, 1 Bath") == 1
    assert plans.title_count("bathrooms", "2 BR, 2.5 BA - 990 SF") == 2.5
    assert plans.title_count("bedrooms", "Fitness Center") is None


# ---- 3. near-miss building facts ------------------------------------------------------------------

def test_year_built_one_year_off_is_close():
    b = dict(scope="building")
    assert judge(claim("year_built", 2020, **b), [ev("county", {"year_built": 2019})])["verdict"] == "close"
    assert judge(claim("year_built", 2020, **b), [ev("county", {"year_built": 2021})])["verdict"] == "close"
    assert judge(claim("year_built", 2020, **b), [ev("county", {"year_built": 2018})])["verdict"] == "contradicted"


def test_unit_count_within_two_percent_is_close():
    b = dict(scope="building")
    v = judge(claim("unit_count", 215, **b), [ev("county", {"unit_count": 216})])
    assert v["verdict"] == "close" and "close" in v["reason"]
    assert judge(claim("unit_count", 300, **b), [ev("county", {"unit_count": 306})])["verdict"] == "close"
    assert judge(claim("unit_count", 300, **b), [ev("county", {"unit_count": 307})])["verdict"] == "contradicted"
    assert judge(claim("unit_count", 20, **b), [ev("record", {"unit_count": 21})])["verdict"] == "close"  # 1 unit
    assert judge(claim("unit_count", 20, **b), [ev("record", {"unit_count": 22})])["verdict"] == "contradicted"
    # floors keep the exact rule
    assert judge(claim("stories", 7, **b), [ev("county", {"stories": 6})])["verdict"] == "contradicted"


# ---- 4. measured scan floor area ------------------------------------------------------------------

def test_measured_scan_area_band():
    def at(listed, measured, title=None):
        obs = {"plan": "Plan E", "area_sqft": measured}
        if title:
            obs["model_name"] = title
        return judge(claim("area_sqft", listed, label="Plan E"), [ev("scan", obs)])["verdict"]
    assert at(1000, 880) == "confirmed"       # 12% smaller
    assert at(1000, 820) == "close"           # 18% smaller: no longer a match
    assert at(1000, 800) == "close"           # 20% smaller
    assert at(1000, 790) == "contradicted"    # 21% smaller
    assert at(1000, 1050) == "confirmed"      # 5% larger
    assert at(1000, 1090) == "close"          # 9% larger
    assert at(1000, 1110) == "contradicted"   # 11% larger
    # a size in the scan's title keeps the 5% / 10% rule
    assert at(1000, 700, "2BR 2BA - 940 SF") == "close"
    assert at(1000, 700, "2BR 2BA - 880 SF") == "contradicted"
