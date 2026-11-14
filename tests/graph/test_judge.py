"""judge() unit tests: no model, no network."""
from rc_common import evidence

from harness.graph import judge


def claim(kind, value, scope="unit", label=None, checkable=True, cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": str(value), "checkable": checkable}


def ev(source, observed, status="ok", images=None):
    return evidence(source, status, "s", observed, images)


def test_bedrooms_2_vs_scan_1_is_contradicted_by_scan():
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"bedrooms": 1, "room_labels": ["Bedroom", "Den"], "plan": "Plan E"})])
    assert v["verdict"] == "contradicted" and v["source"] == "scan"
    assert v["found"] == "1 bedroom + den"
    assert v["reason"] == "Listing says 2 bedrooms. The 3D scan shows 1 bedroom + den."


def test_stories_aerial_beats_county_and_record():
    v = judge(claim("stories", 5, scope="building"),
              [ev("record", {"stories": 5}), ev("county", {"stories": 6}),
               ev("aerial", {"stories": 6, "height_m": 23.1}),
               ev("aerial", {}, images=[{"path": "C:/x/oblique.jpg", "caption": "Angled"}])])
    assert v["verdict"] == "contradicted" and v["source"] == "aerial"
    assert v["reason"].startswith("Listing says 5 floors. The measured roof height (23.1 m) means about 6 floors.")
    assert v["image"] == "C:/x/oblique.jpg"


def test_area_bands():
    def at(found):
        return judge(claim("area_sqft", 1100), [ev("scan", {"area_sqft": found})])
    assert at(968)["verdict"] == "contradicted" and "12%" in at(968)["reason"]
    assert at(1060)["verdict"] == "confirmed"
    assert at(1010)["verdict"] == "close"


def test_year_built_confirmed_by_county():
    v = judge(claim("year_built", 2016, scope="building"), [ev("county", {"year_built": 2016})])
    assert v["verdict"] == "confirmed" and v["source"] == "county"
    assert "2,016" not in v["reason"]


def test_amenity_without_evidence_cant_check():
    v = judge(claim("amenity", "rooftop pool", scope="building"), [ev("county", {"stories": 6})])
    assert v["verdict"] == "cant_check" and v["source"] is None
    v2 = judge(claim("amenity", "luxury living", scope="building", checkable=False), [])
    assert v2["verdict"] == "cant_check"


def test_error_and_no_evidence_are_ignored():
    v = judge(claim("stories", 5, scope="building"),
              [ev("aerial", {"stories": 9}, status="error"), ev("county", {"stories": 7}, status="no_evidence"),
               ev("record", {"stories": 5})])
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    only_error = judge(claim("stories", 5, scope="building"), [ev("aerial", {"stories": 9}, status="error")])
    assert only_error["verdict"] == "cant_check"


def test_scope_and_plan_filters():
    # a unit scan never answers a building claim; another floor plan's scan never answers this plan
    assert judge(claim("stories", 5, scope="building"), [ev("scan", {"stories": 1})])["verdict"] == "cant_check"
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"bedrooms": 1, "plan": "Plan A"}), ev("record", {"bedrooms": 2})])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_photo_floor_count_never_decides_stories():
    photo = ev("aerial", {"photo_floor_count": 7, "illustrative": True})
    unmeasured = ev("aerial", {"stories": 7})  # aerial floors without a roof height are not a measurement
    assert judge(claim("stories", 5, scope="building"), [photo, unmeasured])["verdict"] == "cant_check"
    v = judge(claim("stories", 5, scope="building"), [photo, unmeasured, ev("county", {"stories": 6})])
    assert v["source"] == "county" and v["found"] == "6 floors"


def test_short_plan_label_matches_kit_plan_name():
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"bedrooms": 1, "model_name": "1BR W Den 1BA", "plan": "Plan E 1 Bedroom + Den"})])
    assert v["verdict"] == "contradicted" and v["found"] == "1 bedroom + den"
    assert judge(claim("bedrooms", 2, label="Plan E"),
                 [ev("scan", {"bedrooms": 1, "plan": "Plan EF"})])["verdict"] == "cant_check"
