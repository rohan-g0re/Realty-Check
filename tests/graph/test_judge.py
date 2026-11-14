"""judge() unit tests: no model, no network."""
from rc_common import evidence

from harness.graph import judge


def claim(kind, value, scope="unit", label=None, checkable=True, cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": str(value), "checkable": checkable}


def ev(source, observed, status="ok", images=None):
    return evidence(source, status, "s", observed, images)


def test_bedrooms_2_vs_scan_1_is_contradicted_by_scan():
    # room labels count 1 bedroom and the scan's own title agrees, so the labels may contradict
    v = judge(claim("bedrooms", 2, label="Plan E"),
              [ev("scan", {"bedrooms": 1, "room_labels": ["Bedroom", "Den"], "model_name": "1BR W Den",
                           "plan": "Plan E"})])
    assert v["verdict"] == "contradicted" and v["source"] == "scan"
    assert v["found"] == "1 bedroom + den"
    assert v["reason"] == "Listing says 2 bedrooms. The 3D scan shows 1 bedroom + den."


def test_stories_county_decides_over_the_aerial_estimate():
    # floors: county and the building record are the better sources (trust.py), the roof height an estimate;
    # county and record tie on trust, so the old fixed order (county before record) decides
    v = judge(claim("stories", 5, scope="building"),
              [ev("record", {"stories": 5}), ev("county", {"stories": 6}),
               ev("aerial", {"stories": 6, "height_m": 23.1}),
               ev("aerial", {}, images=[{"path": "C:/x/oblique.jpg", "caption": "Angled"}])])
    assert v["verdict"] == "contradicted" and v["source"] == "county"
    assert v["reason"].startswith("Listing says 5 floors. County records say 6 floors.")
    assert v["image"] == "C:/x/oblique.jpg"  # the aerial picture is still shown
    assert v["confidence"]["decided_by"] == "county"


def test_area_bands_for_county_and_record():
    # written sizes (county, building record) keep the plain 5% / 10% rule
    def at(found, source="county"):
        return judge(claim("area_sqft", 1100), [ev(source, {"area_sqft": found})])
    assert at(968)["verdict"] == "contradicted" and "12%" in at(968)["reason"]
    assert at(1060)["verdict"] == "confirmed"
    assert at(1010)["verdict"] == "close"
    assert at(968, "record")["verdict"] == "contradicted"


def test_scan_measured_area_allows_the_usual_shortfall():
    def at(listed, measured):
        return judge(claim("area_sqft", listed, label="Plan E"),
                     [ev("scan", {"area_sqft": measured, "plan": "Plan E"})])
    v = at(712, 640)  # 10% smaller: normal for a scan
    assert v["verdict"] == "confirmed" and v["found"] == "640 sq ft"
    assert v["reason"] == ("Listing says 712 sq ft. The 3D scan measures 640 sq ft of floor, 10% smaller; "
                           "scans usually measure about a tenth less than the advertised size.")
    assert at(1000, 880)["verdict"] == "confirmed"      # 12% smaller
    assert at(1100, 968)["verdict"] == "confirmed"      # exactly 12% smaller (float edge)
    assert at(1000, 1050)["verdict"] == "confirmed"     # 5% larger
    assert at(1000, 870)["verdict"] == "close"          # 13% smaller
    assert at(1000, 820)["verdict"] == "close"          # 18% smaller
    assert at(1000, 800)["verdict"] == "close"          # 20% smaller
    assert at(1000, 1080)["verdict"] == "close"         # 8% larger
    assert at(1000, 1100)["verdict"] == "close"         # 10% larger
    assert at(640, 498)["verdict"] == "contradicted"    # 22% smaller
    assert at(1000, 790)["verdict"] == "contradicted"   # 21% smaller
    assert at(1000, 1110)["verdict"] == "contradicted"  # 11% larger


def test_scan_title_size_beats_measured_area():
    def at(listed, measured, title):
        return judge(claim("area_sqft", listed, label="Plan E"),
                     [ev("scan", {"area_sqft": measured, "model_name": title, "plan": "Plan E"})])
    v = at(815, 744, "1BR W Den 1BA - 815 SF")
    assert v["verdict"] == "confirmed" and v["source"] == "scan" and v["found"] == "815 sq ft (scan title)"
    assert v["reason"] == ("Listing says 815 sq ft. The 3D scan of this plan is titled "
                           "'1BR W Den 1BA - 815 SF', which gives 815 sq ft.")
    assert at(712, 640, "1BR 1BA - 735 SF")["verdict"] == "confirmed"
    v = at(1240, 1058, "2BR 2BA - 1,060 SF")
    assert v["verdict"] == "contradicted" and "15% smaller" in v["reason"]
    # a title without a size falls back to the measured area
    assert at(815, 744, "1BR W Den")["found"] == "744 sq ft"


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
              [ev("scan", {"bedrooms": 1, "model_name": "1BR W Den 1BA", "plan": "Plan E 1 Bed + Den"})])
    assert v["verdict"] == "contradicted" and v["found"] == "1 bedroom + den"
    assert judge(claim("bedrooms", 2, label="Plan E"),
                 [ev("scan", {"bedrooms": 1, "plan": "Plan EF"})])["verdict"] == "cant_check"
