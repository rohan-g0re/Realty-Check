"""Bed / bath claims are judged only against scans of the same plan (invented data only).

A page for a community lists several plans (studio, 1 bed, 2 bed, 3 bed ...) and a 3D scan is of one of them.
A claim about one plan may never be called wrong by a scan of another; a den is not a mismatch; photo readings
never decide a count."""
from rc_common import evidence

from harness.claims import extract_claims
from harness.graph import judge
from harness.graph.judge import _same_plan

ADDR = "418 Linden Grove Ave"
BAD_WORDS = ("fake", "lie", "scam")


def claim(kind, value, label=None, cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "unit", "label": label,
            "quote": str(value), "checkable": True}


def ev(source, observed, status="ok"):
    return evidence(source, status, "s", observed)


def record(plan, beds, baths, sqft, kit_plan=None):
    return ev("record", {"plan": plan, "kit_plan": kit_plan or plan, "bedrooms": beds, "bathrooms": baths,
                         "area_sqft": sqft})


def scan(plan, title, beds, baths=2, labels=None, from_title=False, **extra):
    obs = {"model_id": "SAMPLEscan01", "model_name": title, "bedrooms": beds, "bathrooms": baths,
           "room_labels": labels or ["Bedroom"] * beds, "area_sqft": 1180, **extra}
    if plan:
        obs["plan"] = plan
    if from_title:
        obs["counts_from"] = "model_name"
    return ev("scan", obs)


def soft(v):
    assert not any(w in v["reason"].lower() for w in BAD_WORDS), v["reason"]
    return v


# ---- multi-plan page, scan of the 3-bed plan ----------------------------------------------------------------

def test_multi_plan_page_scan_of_3_bed_unit_confirms_the_3_bed_plan_not_a_mismatch_for_1_bed():
    evs = [record("Plan A1", 1, 1, 640), record("Plan C3", 3, 2, 1200),
           scan("Plan C3", f"{ADDR} - Plan C3 - 3 Bed / 2 Bath plus Den", 4,
                labels=["Bedroom", "Bedroom", "Bedroom", "Bedroom", "Bathroom", "Bathroom"])]
    one = soft(judge(claim("bedrooms", 1, "Plan A1"), evs))
    assert one["verdict"] == "confirmed" and one["source"] == "record", one
    three = soft(judge(claim("bedrooms", 3, "Plan C3", cid="c2"), evs))
    assert three["verdict"] == "confirmed" and three["source"] == "scan", three
    assert "3 bedrooms + den" in three["found"]


def test_scan_linked_to_the_1_bed_plan_but_titled_as_another_plan_never_contradicts():
    # the record files the 3-bed tour under the 1-bed plan (or the run linked it there): its title says otherwise
    wrong = scan("Plan A1", f"{ADDR} - Plan C3 - 3 Bed / 2 Bath + Den", 3, from_title=True)
    v = soft(judge(claim("bedrooms", 1, "Plan A1"), [wrong, record("Plan A1", 1, 1, 640)]))
    assert v["verdict"] == "confirmed" and v["source"] == "record", v
    assert "names another floor plan" in v["reason"]
    v = soft(judge(claim("bedrooms", 1, "Plan A1"), [wrong]))
    assert v["verdict"] == "cant_check" and "another floor plan" in v["reason"], v


def test_a_plans_own_scan_titled_with_the_same_plan_still_decides():
    # the title names the claim's own plan: confidently linked, so a different count stays a mismatch
    own = scan("Plan A1", f"{ADDR} - Plan A1 - 2 Bed / 1 Bath", 2, from_title=True)
    v = soft(judge(claim("bedrooms", 1, "Plan A1"), [own, record("Plan A1", 1, 1, 640)]))
    assert v["verdict"] == "contradicted" and v["source"] == "scan", v
    # a kit name with the code ('Plan A1 + Den') counts as the same plan
    v = judge(claim("area_sqft", 640, "A1"), [scan("A1", "Plan A1 - 1 Bed + Den", 1),
                                              record("A1", 1, 1, 640, kit_plan="Plan A1 + Den")])
    assert "another floor plan" not in v["reason"], v


def test_scan_not_linked_to_any_plan_says_nothing_about_a_plan_claim():
    loose = scan(None, f"{ADDR} - 3 Bed / 2 Bath", 3, from_title=True)
    v = judge(claim("bedrooms", 1, "Plan A1"), [loose])
    assert v["verdict"] == "cant_check", v


def test_unnamed_1_bed_claim_on_a_multi_plan_page_is_not_contradicted_by_a_3_bed_scan():
    plan_list = ev("record", {"floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 640},
                                             {"name": "C3", "beds": 3, "baths": 2, "sqft": 1200}]})
    s = scan(None, f"{ADDR} - 3 Bed / 2 Bath", 3, from_title=True)
    v = judge(claim("bedrooms", 1), [s, plan_list])
    assert v["verdict"] == "confirmed" and v["source"] == "record", v
    v = judge(claim("bedrooms", 1), [s])
    assert v["verdict"] == "cant_check", v


def test_a_plan_name_that_states_another_kind_of_home_is_another_plan():
    assert _same_plan("Plan E", "Plan E 1 Bed + Den")
    assert _same_plan("Plan 1I", "Plan 1I + Den")
    assert not _same_plan("1 Bed", "1 Bed + Den")
    assert not _same_plan("Plan E", "Plan EF")
    s = scan("1 Bed + Den", "1 Bed + Den - 850 SF", 1, baths=2, from_title=True)
    assert judge(claim("bathrooms", 1, "1 Bed"), [s])["verdict"] == "cant_check"


# ---- single-unit page: the mismatch stays ---------------------------------------------------------------------

def test_single_unit_page_scan_of_that_unit_with_3_bedrooms_is_still_a_mismatch():
    unit = "Unit 900002"
    s = scan(unit, f"{ADDR} {unit} - 3 Bed / 2 Bath", 3, from_title=True)
    v = soft(judge(claim("bedrooms", 1, unit), [s]))
    assert v["verdict"] == "contradicted" and v["found"] == "3 bedrooms" and v["source"] == "scan", v
    # the building record for the same unit agrees with the scan: still a mismatch
    v = soft(judge(claim("bedrooms", 1, unit), [s, record(unit, 3, 2, 1200)]))
    assert v["verdict"] == "contradicted", v


# ---- den -------------------------------------------------------------------------------------------------------

def test_den_never_turns_a_match_into_a_mismatch():
    s = scan("Plan B2", f"{ADDR} - Plan B2 - 1 Bed / 1 Bath plus Den", 1, baths=1, from_title=True)
    v = soft(judge(claim("bedrooms", 1, "Plan B2"), [s]))
    assert v["verdict"] == "confirmed" and v["found"] == "1 bedroom + den", v
    # labels that call the den a bedroom are not relied on when the title says 1 bed + den
    lab = scan("Plan B2", f"{ADDR} - Plan B2 - 1 Bed / 1 Bath plus Den", 2, baths=1,
               labels=["Bedroom", "Bedroom", "Bathroom"])
    v = soft(judge(claim("bedrooms", 1, "Plan B2"), [lab]))
    assert v["verdict"] == "confirmed", v


# ---- photos never decide a count -------------------------------------------------------------------------------

def test_photo_reading_alone_never_decides_a_count():
    photos = ev("scan", {"plan": "Plan A1", "bedrooms": 3, "bathrooms": 2, "has_den": True})
    stills = ev("scan", {"plan": "Plan A1", "model_id": "SAMPLEscan01", "image_count": 4})
    v = judge(claim("bedrooms", 1, "Plan A1"), [photos, stills])
    assert v["verdict"] == "cant_check", v


# ---- page metadata listing several kinds of home is not a claim about one -------------------------------------

def test_jsonld_room_list_of_a_community_is_not_a_bed_claim():
    snap = {"address_text": f"{ADDR}, Springfield, ST", "text": "",
            "jsonld": [{"@type": "ApartmentComplex", "name": "Linden Grove Apartments",
                        "address": {"streetAddress": ADDR},
                        "numberOfRooms": [{"@type": "QuantitativeValue", "value": 0},
                                          {"@type": "QuantitativeValue", "value": 1},
                                          {"@type": "QuantitativeValue", "value": 3}],
                        "numberOfBathroomsTotal": {"minValue": 1, "maxValue": 2}}]}
    claims = extract_claims(snap, use_model=False)
    assert not [c for c in claims if c["kind"] in ("bedrooms", "bathrooms")], claims
    one = {**snap, "jsonld": [{**snap["jsonld"][0], "numberOfRooms": [{"value": 2}, {"value": 2}],
                               "numberOfBathroomsTotal": {"value": 1}}]}
    claims = extract_claims(one, use_model=False)
    assert [c["value"] for c in claims if c["kind"] == "bedrooms"] == [2]
    assert [c["value"] for c in claims if c["kind"] == "bathrooms"] == [1]
