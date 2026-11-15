"""Building-wide ranges on a listing page ("BEDS Studios–2 · BATHS 1–2 · SQFT 475–966"): extracted as building
range claims (value = low end, value_max = high end) and judged against the building record's floor plan list.
Invented data only (418 Linden Grove Ave, property 900001, Example Property Management); no model, no network."""
import pytest

from harness.citations import cite
from harness.claims.extract import extract_claims, validate_claim
from harness.graph import Toolbox
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
FORBIDDEN = ("fake", "lie", "scam", "fraud")


def page(text):
    return extract_claims({"address_text": ADDRESS, "text": text, "jsonld": []}, use_model=False)


def ranges(claims):
    return {c["kind"]: (c["value"], c["value_max"], c["scope"], c["label"]) for c in claims if "value_max" in c}


# ---- extraction ---------------------------------------------------------------------------------------------

def test_header_ranges_become_building_range_claims():
    claims = page("418 Linden Grove Ave\nExample Property Management\nBEDS Studios–2 · BATHS 1–2 · SQFT 475–966\n"
                  "Pool\nFitness center")
    assert ranges(claims) == {"bedrooms": (0, 2, "building", None), "bathrooms": (1, 2, "building", None),
                              "area_sqft": (475, 966, "building", None)}
    assert all(not validate_claim(c) for c in claims), [validate_claim(c) for c in claims]
    # no single-value unit claim from either end of a range
    assert not [c for c in claims if c["kind"] in ("bedrooms", "bathrooms", "area_sqft") and "value_max" not in c]
    assert {c["value"] for c in claims if c["kind"] == "amenity"} == {"pool", "fitness center"}


def test_header_labels_on_their_own_lines():
    claims = page("418 Linden Grove Ave\nBEDS\nStudios–2\nBATHS\n1–2\nSQFT\n475–966")
    assert ranges(claims) == {"bedrooms": (0, 2, "building", None), "bathrooms": (1, 2, "building", None),
                              "area_sqft": (475, 966, "building", None)}


@pytest.mark.parametrize("text,kind,want", [
    ("Studio - 2 Beds", "bedrooms", (0, 2)),
    ("Studios–2 Beds", "bedrooms", (0, 2)),
    ("1-3 Bedrooms", "bedrooms", (1, 3)),
    ("Beds: 1-3", "bedrooms", (1, 3)),
    ("1–2 Baths", "bathrooms", (1, 2)),
    ("1 - 2.5 Bathrooms", "bathrooms", (1, 2.5)),
    ("475–966 sq ft", "area_sqft", (475, 966)),
    ("475 to 966 Sq Ft", "area_sqft", (475, 966)),
    ("Square Feet: 650 - 1,400", "area_sqft", (650, 1400)),
])
def test_range_forms(text, kind, want):
    got = ranges(page(f"418 Linden Grove Ave\n{text}"))
    assert got[kind][:2] == want and got[kind][2:] == ("building", None)


def test_a_range_inside_a_plan_card_stays_that_plans_size():
    claims = page("Plan A1\n1 Bed\n1 Bath\n559 - 603 sq ft\n\nPlan B2\n2 Beds\n2 Baths\n900 sq ft")
    assert not ranges(claims)
    assert {(c["kind"], c["value"], c["label"]) for c in claims if c["kind"] == "area_sqft"} == {
        ("area_sqft", 559, "Plan A1"), ("area_sqft", 900, "Plan B2")}


def test_single_values_are_unchanged():
    claims = page("Interior\nBedrooms: 3\nBathrooms: 2\nSquare Feet: 1,450")
    assert {(c["kind"], c["value"], c["scope"], c["label"]) for c in claims} == {
        ("bedrooms", 3, "unit", None), ("bathrooms", 2, "unit", None), ("area_sqft", 1450, "unit", None)}
    assert not any("value_max" in c for c in claims)


def test_price_ranges_and_lot_sizes_are_not_ranges():
    claims = page("418 Linden Grove Ave\n$1,450 - $2,300\nLot: 5,000 - 6,000 sq ft")
    assert not ranges(claims)


def test_validate_rejects_a_bad_range():
    c = {"id": "c1", "kind": "bedrooms", "value": 2, "value_max": 1, "unit": None, "scope": "building",
         "label": None, "quote": "2-1 beds", "checkable": True}
    assert validate_claim(c)
    assert validate_claim({**c, "value": 0, "value_max": 2, "scope": "unit"})
    assert not validate_claim({**c, "value": 0, "value_max": 2})


def test_model_answer_on_a_range_statement_is_dropped(monkeypatch):
    # the model reading "Studios–2 Beds" as one unit's 2 bedrooms: post-checked away; the rules' range stands
    from harness.claims import extract as ex
    monkeypatch.setattr(ex, "_ask_cached", lambda prompt: [{"n": 1, "kind": "bedrooms", "value": 2, "scope": "unit",
                                                             "label": None, "quote": "Studios–2 Beds"}])
    monkeypatch.setattr(ex, "_page_cache_path", lambda snap: None)
    claims = ex.extract_claims({"address_text": ADDRESS, "text": "Studios–2 Beds", "jsonld": []})
    assert [(c["kind"], c["value"], c.get("value_max")) for c in claims] == [("bedrooms", 0, 2)]


# ---- judging ------------------------------------------------------------------------------------------------

def rclaim(kind, lo, hi, cid="c1"):
    return {"id": cid, "kind": kind, "value": lo, "value_max": hi, "unit": "sqft" if kind == "area_sqft" else None,
            "scope": "building", "label": None, "quote": f"{lo}-{hi}", "checkable": True}


def plan_list(*plans, key="floorplans"):
    fps = [{"name": f"Plan {i + 1}", "beds": b, "baths": ba, "sqft": s} for i, (b, ba, s) in enumerate(plans)]
    return evidence("record", "ok", f"The building record lists {len(fps)} floor plans.", {key: fps},
                    detail="CoStar hackathon kit floor plan list of property 900001.")


PLANS = plan_list((0, 1, 520), (1, 1, 700), (2, 2, 990))


def soft(v):
    text = f"{v['reason']} {v.get('found', '')} {(v.get('confidence') or {}).get('why', '')}".lower()
    return not any(w in text for w in FORBIDDEN)


def test_range_within_the_records_plans_is_confirmed():
    v = judge(rclaim("bedrooms", 0, 2), [PLANS])
    assert v["verdict"] == "confirmed" and v["source"] == "record" and "confidence" not in v
    assert v["claimed"] == "studio to 2 bedrooms" and v["found"] == "studio to 2 bedrooms"
    assert v["reason"] == ("Listing says studio to 2 bedrooms. The building record's floor plans run from studio to "
                           "2 bedrooms, which covers that.")
    assert judge(rclaim("bathrooms", 1, 2), [PLANS])["verdict"] == "confirmed"
    assert judge(rclaim("bedrooms", 1, 2), [PLANS])["verdict"] == "confirmed"  # narrower is within


def test_sqft_range_uses_the_written_size_tolerance():
    v = judge(rclaim("area_sqft", 500, 1000), [PLANS])  # 4% under the smallest plan, 1% over the largest
    assert v["verdict"] == "confirmed"
    assert "The building record's floor plans run from 520 to 990 sq ft" in v["reason"]


def test_range_past_the_records_plans_is_close_with_low_confidence():
    one_bed = plan_list((0, 1, 520), (1, 1, 700))
    v = judge(rclaim("bedrooms", 0, 2), [one_bed])
    assert v["verdict"] == "close" and v["source"] == "record"
    assert v["found"] == "studio to 1 bedroom"
    assert "floor plans run from studio to 1 bedroom" in v["reason"] and "often incomplete" in v["reason"]
    assert v["confidence"]["band"] == "low" and v["confidence"]["decided_by"] == "record"
    assert soft(v)
    v = judge(rclaim("area_sqft", 475, 966), [PLANS])  # 9% under the smallest plan
    assert v["verdict"] == "close" and v["confidence"]["band"] == "low" and soft(v)


def test_range_far_past_the_records_plans_is_contradicted_still_low_confidence():
    v = judge(rclaim("bedrooms", 1, 4), [PLANS])
    assert v["verdict"] == "contradicted" and v["confidence"]["band"] == "low" and soft(v)
    v = judge(rclaim("area_sqft", 300, 966), [PLANS])
    assert v["verdict"] == "contradicted" and v["confidence"]["band"] == "low" and soft(v)


def test_no_floor_plan_data_is_cant_check():
    v = judge(rclaim("bedrooms", 0, 2), [])
    assert v["verdict"] == "cant_check" and v["source"] is None and soft(v)
    v = judge(rclaim("area_sqft", 475, 966), [plan_list((1, 1, None), (2, 2, None))])
    assert v["verdict"] == "cant_check" and v["found"] == "no floor plans"


def test_a_unit_scan_or_a_plan_record_never_decides_a_building_range():
    scan = evidence("scan", "ok", "s", {"model_id": "SAMPLEscan01", "bedrooms": 1, "bathrooms": 1, "area_sqft": 610})
    plan = evidence("record", "ok", "s", {"plan": "Plan 2", "kit_plan": "Plan 2", "bedrooms": 1, "bathrooms": 1,
                                          "area_sqft": 700})
    v = judge(rclaim("bedrooms", 0, 2), [scan, plan])
    assert v["verdict"] == "cant_check"
    v = judge(rclaim("bedrooms", 0, 2), [scan, plan, PLANS])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_single_value_claim_is_judged_as_before():
    one = {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": None,
           "quote": "2 beds", "checkable": True}
    v = judge(one, [PLANS])
    assert v["claimed"] == "2 bedrooms" and v["verdict"] == "confirmed"
    assert v["reason"].startswith("Listing says 2 bedrooms without naming a floor plan;")
    v = judge({**one, "kind": "area_sqft", "value": 1100}, [evidence("county", "ok", "s", {"area_sqft": 1060})])
    assert v["verdict"] == "confirmed" and v["claimed"] == "1,100 sq ft"


def test_citation_names_the_record_plan_list():
    v = cite(judge(rclaim("bedrooms", 0, 2), [PLANS]), rclaim("bedrooms", 0, 2), [PLANS],
             {"property_id": "900001"})
    assert v["verdict"] == "confirmed"
    dec = next(c for c in v["citations"] if c["role"] == "decided")
    assert dec["source"] == "CoStar" and "900001" in dec["where"] and "floor plan Plan" not in dec["where"]


# ---- the run ------------------------------------------------------------------------------------------------

MATCH = {"status": "matched", "kind": "building", "property_id": "900001", "confidence": 0.9, "how": "address",
         "floorplans": [{"name": "Plan 1", "beds": 0, "baths": 1, "sqft": 520},
                        {"name": "Plan 2", "beds": 1, "baths": 1, "sqft": 700}]}


def make_run(claims):
    t = Toolbox.fake(extract_claims=lambda s, use_model=True: [dict(c) for c in claims],
                     match_address=lambda *a, **k: dict(MATCH))
    run = run_mod.Run({"title": "418 Linden Grove Ave", "address_text": ADDRESS, "text": ""}, lambda e: None, t)
    run.claims, run.match = [dict(c) for c in claims], dict(MATCH)
    run.claims_ready = run.match_ready = True
    return run


def test_run_adds_the_plan_list_for_range_claims_and_keeps_unit_logic_apart():
    claims = [rclaim("bedrooms", 0, 2, "c1"), rclaim("area_sqft", 475, 700, "c2")]
    run = make_run(claims)
    assert not run.single_unit_page()          # a range is no single home's numbers
    assert "inside" not in run_mod.helpers_needed(run.claims, run.match)  # a unit scan cannot decide a range
    run.plan_list_for_ranges()
    lists = [e for e in run.evidence.values() if "range_plans" in e["observed"]]
    assert len(lists) == 1 and not any("floorplans" in e["observed"] for e in run.evidence.values())
    run.plan_list_for_ranges()                  # once only
    assert len([e for e in run.evidence.values() if "range_plans" in e["observed"]]) == 1
    evs = list(run.evidence.values())
    v1, v2 = judge(run.claims[0], evs), judge(run.claims[1], evs)
    assert v1["verdict"] == "close" and v1["confidence"]["band"] == "low"
    assert v2["verdict"] == "close" and "run from 520 to 700 sq ft" in v2["reason"]


def test_run_without_range_claims_adds_nothing():
    one = {"id": "c1", "kind": "bedrooms", "value": 1, "unit": None, "scope": "unit", "label": None,
           "quote": "1 bed", "checkable": True}
    run = make_run([one])
    run.plan_list_for_ranges()
    assert not run.evidence
