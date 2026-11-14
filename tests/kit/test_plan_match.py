"""tools.kit.plan_match: page plan names -> record plans and scans by structure. Invented data only."""
import pytest

from tools.kit import check_type_claim, link_plan, link_plans, parse_plan, plan_types, type_evidence


def fp(name, beds, baths, sqft, mid=None):
    return {"name": name, "beds": beds, "baths": baths, "sqft": sqft, "matterport_model_id": mid}


KIT = [fp("S1", 0, 1, 500), fp("S2", 0, 1, 560),
       fp("A1", 1, 1, 640), fp("A2", 1, 1, 690), fp("A3", 1, 1, 720),
       fp("A4 + Den", 1, 1, 850, "mDen"),
       fp("B1", 2, 2, 1050), fp("B2", 2, 2, 1120, "mB2"),
       fp("Plan 3C", 3, 2, 1400)]
MATCH = {"status": "matched", "property_id": "1", "name": "Maple Court", "floorplans": KIT,
         "matterport_model_ids": ["mGym", "m1x1", "mDen", "mB2"]}
TITLES = {"mGym": "Fitness Center", "m1x1": "Maple Court - 1BR, 1BA - 690 SF", "mDen": "Plan A4 - 1 Bed + Den - 850 SF",
          "mB2": "2 BR, 2 BA - 1120 SF - B2"}


def claim(kind, value, label, cid="c1", quote=None):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "unit", "label": label,
            "quote": quote or str(value), "checkable": True}


@pytest.mark.parametrize("text,beds,baths,den,sqft,code", [
    ("1x1", 1, 1, False, None, None),
    ("2x2", 2, 2, False, None, None),
    ("Efficiency", 0, None, False, None, None),
    ("Studio", 0, None, False, None, None),
    ("S1", None, None, False, None, "S1"),
    ("1 Bed 1 Bath", 1, 1, False, None, None),
    ("1 Bedroom + Den", 1, None, True, None, None),
    ("Plan E - 1 Bed + Den - 850 SF", 1, None, True, 850, "E"),
    ("2BR,2BA_1,120 Sq Ft", 2, 2, False, 1120, None),
    ("One Bedroom, One Bathroom", 1, 1, False, None, None),
    ("2 bed / 2.5 bath, 1,450 sq. ft.", 2, 2.5, False, 1450, None),
    ("B6+Den", None, None, True, None, "B6"),
    ("X Floor Plan, Studio / 1 Bath", 0, 1, False, None, "X"),
    ("Fitness Center", None, None, False, None, None),
])
def test_parse_plan(text, beds, baths, den, sqft, code):
    p = parse_plan(text)
    assert (p["beds"], p["baths"], p["den"], p["sqft"], p["code"]) == (beds, baths, den, sqft, code)


def test_exact_name_still_wins():
    r = link_plan("A2", MATCH, [], TITLES)
    assert r["how"] == "name" and r["kit"]["name"] == "A2" and not r["ambiguous"]


def test_plan_code_matches_normalized_name():
    r = link_plan("Plan A1", MATCH)
    assert r["kit"]["name"] == "A1" and "by its plan name" in r["note"]
    assert link_plan("3C", MATCH)["kit"]["name"] == "Plan 3C"


def test_plan_code_coincidence_does_not_override_page_numbers():
    # the page says 2 beds for "Plan A1": the record's A1 has 1 bed, so a loose code match is not trusted
    r = link_plan("Plan A1", MATCH, [claim("bedrooms", 2, "Plan A1"), claim("area_sqft", 1060, "Plan A1", "c2")])
    assert r["kit"]["name"] == "B1" and r["how"] == "size"


def test_generic_name_is_a_type_level_match():
    claims = [claim("bedrooms", 1, "1x1"), claim("area_sqft", 650, "1x1", "c2", "from 650 sq ft")]
    r = link_plan("1x1", MATCH, claims, TITLES)
    assert r["ambiguous"] and r["kit"] is None and r["how"] == "type"
    assert [f["name"] for f in r["kit_plans"]] == ["A1", "A2", "A3"]  # the den plan stays out of a plain 1x1
    assert (r["sqft_min"], r["sqft_max"], r["beds"], r["baths"]) == (640, 720, 1, 1)
    assert "kind of home, not one floor plan" in r["note"]


def test_type_level_claims_checked_against_the_range_never_contradicted():
    r = link_plan("1x1", MATCH, [claim("area_sqft", 650, "1x1", quote="from 650 sq ft")])
    v = check_type_claim(claim("area_sqft", 650, "1x1"), r)
    assert v["verdict"] == "confirmed" and "640–720 sq ft" in v["reason"]
    v = check_type_claim(claim("area_sqft", 2000, "1x1"), r)
    assert v["verdict"] == "cant_check"
    assert check_type_claim(claim("bedrooms", 1, "1x1"), r)["verdict"] == "confirmed"
    assert check_type_claim(claim("bathrooms", 3, "1x1"), r)["verdict"] == "cant_check"
    for v in (check_type_claim(claim(k, x, "1x1"), r) for k, x in
              [("area_sqft", 100), ("area_sqft", 5000), ("bedrooms", 4), ("bathrooms", 0)]):
        assert v["verdict"] not in ("contradicted", "close")
    words = " ".join(str(v) for v in r.values()).lower()
    assert not any(w in words for w in ("fake", "lie", "scam"))


def test_type_evidence_has_no_single_size():
    r = link_plan("1x1", MATCH, [claim("bedrooms", 1, "1x1")])
    ev = type_evidence(r, MATCH)
    obs = ev["observed"]
    assert ev["source"] == "record" and ev["status"] == "ok" and "area_sqft" not in obs
    assert obs["plan"] == "1x1" and obs["plan_type"] and obs["bedrooms"] == 1
    assert (obs["area_sqft_min"], obs["area_sqft_max"]) == (640, 720) and len(obs["floorplans"]) == 3


def test_type_level_scan_only_when_it_is_the_only_one_of_that_kind():
    r = link_plan("1x1", MATCH, [], TITLES)
    assert r["scan_id"] == "m1x1" and r["scan_how"] == "type_only"
    assert "as an example" in r["note"] and "may be a different" in r["note"]
    two = {**TITLES, "m1x1b": "1BR, 1BA - 720 SF"}
    r = link_plan("1x1", MATCH, [], two)
    assert r["scan_id"] is None and "none was picked" in r["note"]
    labelled = {**two, "m1x1b": "1 Bed 1 Bath - typical layout, features vary"}
    r = link_plan("1x1", MATCH, [], labelled)
    assert r["scan_id"] == "m1x1b" and r["scan_how"] == "type_example"


def test_one_plan_of_that_kind_is_picked():
    r = link_plan("2 Bedrooms", {**MATCH, "floorplans": [f for f in KIT if f["name"] != "B1"]}, [], TITLES)
    assert r["kit"]["name"] == "B2" and r["scan_id"] == "mB2" and r["scan_how"] == "kit"
    assert "only 2-bed, 2-bath plan" in r["note"]


def test_den_name_and_scan_title_with_den():
    r = link_plan("1 Bedroom + Den", MATCH, [], TITLES)
    assert r["kit"]["name"] == "A4 + Den" and r["scan_id"] == "mDen"


def test_size_in_the_name_picks_the_nearest_plan():
    r = link_plan("1BR 688 sq ft", MATCH, [], TITLES)
    assert r["kit"]["name"] == "A2" and r["how"] == "size" and "by size" in r["note"]
    r = link_plan("Maple Court - Studio", MATCH, [claim("area_sqft", 560, "Maple Court - Studio")])
    assert r["kit"]["name"] == "S2"  # a generic name with an exact size: the size breaks the tie


def test_size_tie_stays_ambiguous():
    kit = KIT + [fp("A2X", 1, 1, 690)]
    r = link_plan("1 bed 690 sq ft", {**MATCH, "floorplans": kit})
    assert r["ambiguous"] and {f["name"] for f in r["kit_plans"]} == {"A2", "A2X"} and r["kit"] is None


def test_duplicate_record_names_are_narrowed_or_left_ambiguous():
    kit = [fp("Q", 0, 1, 560), fp("Q", 1, 1, 600)]
    m = {**MATCH, "floorplans": kit}
    assert link_plan("Q", m, [claim("bedrooms", 1, "Q")])["kit"]["sqft"] == 600
    r = link_plan("Q", m, [claim("bathrooms", 1, "Q")])
    assert r["ambiguous"] and r["beds"] is None
    assert check_type_claim(claim("bathrooms", 1, "Q"), r)["verdict"] == "confirmed"


def test_name_and_numbers_disagree_no_pick():
    r = link_plan("2x2", MATCH, [claim("bedrooms", 1, "2x2")])
    assert r["kit"] is None and not r["kit_plans"] and not r["ambiguous"]


def test_unknown_plan_and_no_match():
    assert link_plan("Penthouse Z9", MATCH)["kit"] is None
    assert link_plan("1x1", {"status": "no_match"})["kit_plans"] == []
    assert link_plan("", MATCH)["how"] is None


def test_scan_for_a_plan_without_its_own():
    r = link_plan("A2", MATCH, [], TITLES)
    assert r["scan_id"] == "m1x1" and r["scan_how"] == "title"


def test_link_plans_and_plan_types():
    claims = [claim("bedrooms", 1, "1x1"), claim("bedrooms", 0, "Efficiency", "c2"), claim("bedrooms", 2, None, "c3")]
    links = link_plans(MATCH, claims, TITLES)
    assert set(links) == {"1x1", "Efficiency"} and links["Efficiency"]["sqft_max"] == 560
    types = plan_types(KIT)
    one = [t for t in types if t["beds"] == 1 and not t["den"]][0]
    assert one["plans"] == ["A1", "A2", "A3"] and (one["sqft_min"], one["sqft_max"]) == (640, 720)
    assert [t for t in types if t["den"]][0]["scan_ids"] == ["mDen"]


def test_multi_plan_page_scan_of_the_3_bed_plan_is_linked_only_to_that_plan():
    kit = [fp("S1", 0, 1, 500), fp("A1", 1, 1, 640), fp("A2", 1, 1, 700), fp("C3", 3, 2, 1200, "SAMPLEscan01")]
    match = {"status": "matched", "property_id": "900001", "name": "Linden Grove", "floorplans": kit,
             "matterport_model_ids": ["SAMPLEscan01"]}
    titles = {"SAMPLEscan01": "418 Linden Grove Ave - Plan C3 - 3 Bed / 2 Bath + Den"}
    for label in ("1 Bedroom", "1x1", "A1", "Plan A1", "Studio", "Linden Grove Apartments"):
        assert link_plan(label, match, [], titles)["scan_id"] is None, label
    for label in ("C3", "3 Bedrooms"):
        r = link_plan(label, match, [], titles)
        assert r["kit"]["name"] == "C3" and r["scan_id"] == "SAMPLEscan01", label
    # a 1-bed type-level claim is never contradicted, even with the 3-bed scan in the building
    r = link_plan("1 Bedroom", match, [], titles)
    assert check_type_claim(claim("bedrooms", 1, "1 Bedroom"), r)["verdict"] == "confirmed"
    assert check_type_claim(claim("bedrooms", 3, "1 Bedroom"), r)["verdict"] == "cant_check"
