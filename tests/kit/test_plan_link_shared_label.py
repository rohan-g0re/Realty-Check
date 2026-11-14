"""A page line several plans share ('1/1 with Hardwood') is never linked to one record plan: each size claim under it
is compared with the record plan of that size, or left unchecked. Invented data only (418 Linden Grove Ave)."""
from tools.kit import check_type_claim, link_plan, link_plans
from tools.kit.plan_match import SIZE_TOL, label_values, plan_for_claim


def fp(name, beds, baths, sqft, mid=None):
    return {"name": name, "beds": beds, "baths": baths, "sqft": sqft, "matterport_model_id": mid}


KIT = [fp("S1", 0, 1, 520), fp("S2", 0, 1, 528),
       fp("A1", 1, 1, 600), fp("A2", 1, 1, 655), fp("A3", 1, 1, 710), fp("A4", 1, 1, 760), fp("B2", 1, 1, 880, "m900002"),
       fp("C1", 2, 2, 1000, "m900001"), fp("C2", 2, 2, 1090), fp("C3", 2, 2, 1180),
       fp("D1", 3, 2, 1350)]
MATCH = {"status": "matched", "property_id": "900001", "name": "418 Linden Grove Ave", "floorplans": KIT,
         "matterport_model_ids": ["m900001", "m900002"]}
TITLES = {"m900001": "2 BR, 2 BA - 1000 SF - C1", "m900002": "1 BR, 1 BA - 880 SF - B2"}


def claim(kind, value, label, cid, quote=None):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "unit", "label": label,
            "quote": quote or str(value), "checkable": True}


def page(label, beds, baths, sizes, start=1):
    out = [claim("bedrooms", beds, label, f"c{start}"), claim("bathrooms", baths, label, f"c{start + 1}")]
    return out + [claim("area_sqft", s, label, f"c{start + 2 + i}", f"{s:,} sqft") for i, s in enumerate(sizes)]


ONE = "1/1 with Hardwood"
CLAIMS = page(ONE, 1, 1, [600, 655, 710, 760, 880])


def test_shared_label_is_not_one_plan():
    r = link_plan(ONE, MATCH, CLAIMS, TITLES)
    assert r["shared"] and r["ambiguous"] and r["kit"] is None
    assert {f["name"] for f in r["kit_plans"]} == {"A1", "A2", "A3", "A4", "B2"}
    assert r["claim_plans"] == {"c3": "A1", "c4": "A2", "c5": "A3", "c6": "A4", "c7": "B2"}
    assert "by size" not in r["note"] and "share" in r["note"]


def test_each_size_matches_its_own_plan():
    r = link_plan(ONE, MATCH, CLAIMS, TITLES)
    for c in CLAIMS:
        v = check_type_claim(c, r)
        assert v["verdict"] == "confirmed", (c, v)
        assert v["verdict"] not in ("contradicted", "close")
    v = check_type_claim(CLAIMS[-1], r)  # the 880 sq ft line finds the 880 sq ft plan, not the first one
    assert v["found"] == "880 sq ft" and "B2" in v["reason"]


def test_size_no_plan_has_is_cant_check_never_mismatch():
    claims = CLAIMS + [claim("area_sqft", 1300, ONE, "c99", "1,300 sqft")]
    r = link_plan(ONE, MATCH, claims, TITLES)
    assert plan_for_claim(claims[-1], r) is None
    v = check_type_claim(claims[-1], r)
    assert v["verdict"] == "cant_check" and v["found"] is None
    near = claim("area_sqft", 820, ONE, "c98", "820 sqft")  # 7% above A4, inside SIZE_TOL but not RANGE_TOL
    assert check_type_claim(near, r)["verdict"] == "cant_check"


def test_no_size_link_beyond_tolerance():
    for label, claims in ((ONE, CLAIMS), ("2/2 with Hardwood", page("2/2 with Hardwood", 2, 2, [1000, 1090, 1180]))):
        r = link_plan(label, MATCH, claims, TITLES)
        if r["kit"] is not None:
            ks = r["kit"]["sqft"]
            assert all(abs(ks - s) / s <= SIZE_TOL for s in label_values(label, claims)["area_sqft"])
        assert r["kit"] is None and r["shared"]


def test_two_close_sizes_on_two_plans_go_per_claim():
    claims = page("Studio with Hardwood", 0, 1, [520, 528])
    r = link_plan("Studio with Hardwood", MATCH, claims, TITLES)
    assert r["shared"] and r["claim_plans"] == {"c3": "S1", "c4": "S2"}


def test_counts_that_differ_make_a_shared_label():
    claims = [claim("bedrooms", 1, "Hardwood homes", "c1"), claim("bedrooms", 2, "Hardwood homes", "c2"),
              claim("area_sqft", 655, "Hardwood homes", "c3"), claim("area_sqft", 1090, "Hardwood homes", "c4")]
    r = link_plan("Hardwood homes", MATCH, claims, TITLES)
    assert r["shared"] and r["kit"] is None
    assert {f["name"] for f in r["kit_plans"]} == {"A1", "A2", "A3", "A4", "B2", "C1", "C2", "C3"}
    assert [check_type_claim(c, r)["verdict"] for c in claims] == ["confirmed"] * 4
    assert r["claim_plans"] == {"c3": "A2", "c4": "C2"}


def test_scan_of_a_shared_line_is_only_an_example():
    r = link_plan(ONE, MATCH, CLAIMS, TITLES)
    assert r["scan_how"] in (None, "type_only", "type_example")  # never decides a claim as the plan's own scan


def test_one_consistent_label_still_links_one_plan():
    r = link_plan("1/1 with Hardwood", MATCH, page(ONE, 1, 1, [710]), TITLES)
    assert r["kit"]["name"] == "A3" and r["how"] == "size" and not r["shared"]
    r = link_plan(ONE, MATCH, page(ONE, 1, 1, [710, 712]), TITLES)  # the same size twice over: one plan
    assert r["kit"]["name"] == "A3" and not r["shared"]
    r = link_plan("A2", MATCH, page("A2", 1, 1, [655, 900]), TITLES)  # a plan code is one plan, whatever the sizes
    assert r["kit"]["name"] == "A2" and r["how"] == "name" and not r["shared"]


def test_link_plans_keeps_labels_apart():
    claims = CLAIMS + page("3/2 with Hardwood", 3, 2, [1350], start=50)
    links = link_plans(MATCH, claims, TITLES)
    assert links[ONE]["shared"] and links["3/2 with Hardwood"]["kit"]["name"] == "D1"
