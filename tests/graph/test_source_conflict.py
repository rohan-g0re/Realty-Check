"""When our own sources disagree: the records (CoStar, County) against each other, a roof-height estimate that
never breaks their tie, absence that is never a mismatch, found text without provenance tags, and the headline
that follows the report's pick. Invented data only (418 Linden Grove Ave, ids 900001 / 900002, scan
SAMPLEscan01, unit 1204); no model, no network."""
from rc_common import evidence

from harness.graph import judge
from harness.graph.run import _headline

ADDRESS = "418 Linden Grove Ave"


def claim(kind, value, scope="building", label=None, cid="c1", quote=None):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote or str(value), "checkable": True}


def ev(source, observed, images=None):
    return evidence(source, "ok", f"{ADDRESS}: synthetic {source}", observed, images)


REC = lambda **o: ev("record", {"property_id": 900001, **o})  # noqa: E731
COUNTY = lambda **o: ev("county", {"parcel": "900002", **o})  # noqa: E731
ROOF = lambda floors, m: ev("aerial", {"stories": floors, "height_m": m})  # noqa: E731


def by_source(v):
    return {s["source"]: s for s in v["confidence"]["sources"]}


def soft(v):
    words = (v["reason"] + " " + str(v.get("found"))).lower()
    assert not any(w in words for w in ("fake", " lie", "scam", "fraud"))


# ---- (a) one record backs the listing, the other does not: not a mismatch ------------------------------------

def test_record_backs_listing_county_differs_is_couldnt_check():
    v = judge(claim("stories", 5, quote="5-story"), [REC(stories=5), COUNTY(stories=6), ROOF(6, 23.1)])
    assert v["verdict"] == "cant_check" and v["source"] is None and "confidence" not in v
    assert v["found"] == "sources differ"
    assert v["reason"].startswith("Listing says 5 floors. CoStar lists 5 floors, the county record lists 6, so the "
                                  "sources differ.")
    assert "estimate" in v["reason"]  # the roof height is mentioned, and settles nothing
    soft(v)


def test_county_backs_listing_record_differs_is_never_a_mismatch():
    # equal trust: the fixed order (county first) decides, and the county backs the listing, so it stays
    # confirmed, with CoStar's other value named in the reason
    v = judge(claim("stories", 5), [REC(stories=6), COUNTY(stories=5)])
    assert v["verdict"] == "confirmed" and v["source"] == "county"
    assert "The building record says 6." in v["reason"]
    # a county record found by its address weighs a little less, so CoStar would decide: still not a mismatch
    v = judge(claim("stories", 5), [REC(stories=6), COUNTY(stories=5, parcel_match="address")])
    assert v["verdict"] == "cant_check"
    assert "The county record lists 5 floors, CoStar lists 6, so the sources differ." in v["reason"]


def test_roof_height_never_breaks_a_tie_between_records():
    for roof in (ROOF(6, 23.1), ROOF(5, 19.0), ROOF(7, 26.5)):
        v = judge(claim("stories", 5), [REC(stories=5), COUNTY(stories=6), roof])
        assert v["verdict"] == "cant_check", roof


def test_year_and_unit_count_with_a_backing_record_are_couldnt_check():
    v = judge(claim("year_built", 2018), [COUNTY(year_built=2019), REC(year_built=2018)])
    assert v["verdict"] == "cant_check"
    assert "CoStar lists 2018 as the year built, the county record lists 2019, so the sources differ." in v["reason"]
    v = judge(claim("unit_count", 184), [COUNTY(unit_count=216), REC(unit_count=184)])
    assert v["verdict"] == "cant_check" and "CoStar lists 184 apartments" in v["reason"]


def test_records_agreeing_with_each_other_still_decide():
    v = judge(claim("stories", 5), [REC(stories=6), COUNTY(stories=6)])
    assert v["verdict"] == "contradicted" and v["confidence"]["decided_by"] == "county"
    v = judge(claim("stories", 5), [REC(stories=5), COUNTY(stories=5), ROOF(6, 23.1)])
    assert v["verdict"] == "confirmed"


# ---- (b) no source backs the listing and the records differ: the mismatch stands, both values shown -----------

def test_unit_count_no_record_backs_listing_names_both_values():
    v = judge(claim("unit_count", 184), [COUNTY(unit_count=216), REC(unit_count=149)])
    assert v["verdict"] == "contradicted" and v["source"] == "county" and v["found"] == "216 apartments"
    assert v["reason"] == "Listing says 184 apartments. County lists 216 apartments; CoStar lists 149."
    c = v["confidence"]
    assert c["decided_by"] == "county"
    s = by_source(v)
    assert s["county"]["says"] == "216 apartments" and s["county"]["agrees_with_listing"] is False
    assert s["record"]["says"] == "149 apartments" and s["record"]["agrees_with_listing"] is False
    assert "agree" not in c["why"]  # CoStar's 149 does not back County's 216
    soft(v)


def test_year_built_no_record_backs_listing_names_both_values():
    v = judge(claim("year_built", 2018), [COUNTY(year_built=2019), REC(year_built=2020)])
    assert v["verdict"] == "close" and v["source"] == "county"
    assert v["reason"] == ("Listing says built in 2018. County lists 2019 as the year built, which is close; "
                           "CoStar lists 2020.")
    s = by_source(v)
    assert s["county"]["says"] == "built in 2019" and s["record"]["says"] == "built in 2020"
    assert not s["county"]["agrees_with_listing"] and not s["record"]["agrees_with_listing"]


def test_stories_confidence_rows_are_right_per_source():
    v = judge(claim("stories", 5), [COUNTY(stories=7), REC(stories=6), ROOF(7, 26.5)])
    assert v["verdict"] == "contradicted" and v["confidence"]["decided_by"] == "county"
    s = by_source(v)
    assert s["county"]["says"] == "7 floors" and s["record"]["says"] == "6 floors"
    assert s["aerial"]["says"] == "about 7 floors"  # an estimate from the roof height, said as one
    assert not any(r["agrees_with_listing"] for r in v["confidence"]["sources"])
    assert "County lists 7 floors; CoStar lists 6." in v["reason"]


# ---- absence is never a mismatch ---------------------------------------------------------------------------

def test_quartz_counters_not_seen_in_scan_photos_is_couldnt_check():
    c = claim("other", "Quartz counters", scope="unit", cid="c9", quote="quartz counters")
    look = ev("scan", {"feature_check": True, "claim_ids": ["c9"], "feature": "quartz_counters",
                       "presence": "not_seen", "presence_confidence": 0.9, "scan_title": "SAMPLEscan01 Unit 1204"})
    v = judge(c, [REC(amenities=["Fitness center"]), look])
    assert v["verdict"] == "cant_check" and v["source"] is None and "confidence" not in v
    assert v["found"] == "Not seen in the 3D scan photos"
    assert "cannot prove something is absent" in v["reason"]
    soft(v)


def test_quartz_counters_seen_still_confirms():
    c = claim("other", "Quartz counters", scope="unit", cid="c9")
    look = ev("scan", {"feature_check": True, "claim_ids": ["c9"], "feature": "quartz_counters",
                       "presence": "seen", "presence_confidence": 0.9})
    assert judge(c, [look])["verdict"] == "confirmed"


# ---- found text carries no provenance tag ------------------------------------------------------------------

def test_scan_title_size_found_has_no_tag():
    scan = ev("scan", {"plan": "Plan A", "area_sqft": 600, "model_name": "1BR, 1BA 640 SF"})
    v = judge(claim("area_sqft", 480, scope="unit", label="Plan A"), [scan])
    assert v["found"] == "640 sq ft" and "(" not in v["found"]
    assert v["found_from"] == "scan_title"
    assert v["verdict"] == "contradicted"


# ---- the headline follows the report's pick ----------------------------------------------------------------

def test_headline_follows_the_top_mismatch_pick():
    claims = {"c1": claim("stories", 5, cid="c1"), "c3": claim("unit_count", 184, cid="c3")}
    vs = [{"claim_id": "c1", "verdict": "contradicted", "claimed": "5 floors", "found": "7 floors", "source": "county"},
          {"claim_id": "c3", "verdict": "contradicted", "claimed": "184 apartments", "found": "216 apartments",
           "source": "county"}]
    assert _headline(vs, claims).startswith("Listing says 5 floors")  # no pick: the old order
    h = _headline(vs, claims, lead="c3")
    assert h.startswith("Listing says 184 apartments, but county records say 216 apartments")
    assert h.endswith("1 more claim did not match.")
    assert _headline(vs, claims, lead="c404").startswith("Listing says 5 floors")  # an unknown pick changes nothing


# ---- (c) the records disagree with each other by a lot: never a high-confidence mismatch on the outlier -------

def test_home_count_far_below_the_other_record_never_overrides_it():
    # CoStar 300 and a county parcel of 100 for a listing of 317: the county parcel may cover only part of the
    # community, so CoStar (nearest the listing) is credited and the claim is close, at low confidence
    v = judge(claim("unit_count", 317, quote="317 units"), [COUNTY(unit_count=100), REC(unit_count=300)])
    assert v["verdict"] == "close" and v["source"] == "record" and v["found"] == "300 apartments"
    assert v["reason"].startswith("Listing says 317 apartments. CoStar lists 300 apartments, which is close; "
                                  "County lists 100 — sources differ.")
    c = v["confidence"]
    assert c["decided_by"] == "record" and c["band"] == "low"
    assert "County lists 100" in c["why"]
    soft(v)


def test_home_count_far_apart_either_way_round_credits_the_record_nearest_the_listing():
    # the order of evidence, and which record is the lower one, do not matter
    v = judge(claim("unit_count", 317), [REC(unit_count=300), COUNTY(unit_count=100, parcel_match="address")])
    assert v["verdict"] == "close" and v["source"] == "record"
    v = judge(claim("unit_count", 110), [REC(unit_count=300), COUNTY(unit_count=104)])
    assert v["verdict"] == "close" and v["source"] == "county" and v["found"] == "104 apartments"
    assert "CoStar lists 300 — sources differ." in v["reason"]
    assert "parcel" not in v["reason"]  # the county is not the low one here
    assert v["confidence"]["band"] == "low"


def test_home_count_far_apart_and_neither_near_the_listing_is_couldnt_check():
    v = judge(claim("unit_count", 400), [COUNTY(unit_count=100), REC(unit_count=300)])
    assert v["verdict"] == "cant_check" and v["source"] is None and "confidence" not in v
    assert v["found"] == "sources differ"
    assert v["reason"].startswith("Listing says 400 apartments. CoStar lists 300; County lists 100 — sources differ.")
    soft(v)


def test_home_count_far_apart_with_a_matching_record_stays_couldnt_check():
    v = judge(claim("unit_count", 300), [COUNTY(unit_count=100), REC(unit_count=300)])
    assert v["verdict"] == "cant_check" and v["found"] == "sources differ"
    assert "CoStar lists 300 apartments, the county record lists 100, so the sources differ." in v["reason"]


def test_a_record_close_to_the_listing_backs_it_when_the_other_is_far_off():
    # CoStar 312 is within a couple of percent of 317; County 250 differs from CoStar by a lot: sources differ
    v = judge(claim("unit_count", 317), [COUNTY(unit_count=250), REC(unit_count=312)])
    assert v["verdict"] == "cant_check" and v["found"] == "sources differ" and "confidence" not in v
    assert "CoStar lists 312 apartments, the county record lists 250, so the sources differ." in v["reason"]


def test_records_far_apart_with_neither_backing_cap_the_confidence():
    # both records off the listing on the same side, but far from each other: the mismatch stands, medium at most
    v = judge(claim("stories", 12), [COUNTY(stories=20), REC(stories=16)])
    assert v["verdict"] == "contradicted"
    c = v["confidence"]
    assert c["band"] != "high" and c["score"] <= 60
    assert "CoStar lists 16, so we are less sure" in c["why"]
    # records only a year apart are not far apart: nothing is capped
    v = judge(claim("year_built", 2010), [COUNTY(year_built=2019), REC(year_built=2020)])
    assert v["verdict"] == "contradicted" and "less sure" not in v["confidence"]["why"]
