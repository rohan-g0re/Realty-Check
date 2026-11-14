"""Source trust (harness/graph/trust.py): the tiebreak when our sources disagree, and the confidence score on
every mismatch. Invented data only (418 Linden Grove Ave, ids 900001/900002, scan SAMPLEscan01)."""
import datetime as dt

from rc_common import evidence

from harness.graph import judge, trust

ADDRESS = "418 Linden Grove Ave"


def claim(kind, value, scope="unit", label=None, cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": str(value), "checkable": True}


def ev(source, observed, status="ok", images=None):
    return evidence(source, status, "s", observed, images)


def record(**obs):
    return ev("record", {"property_id": "900001", **obs})


def feature(source, presence, conf, cid="c1", feat="pool"):
    return ev(source, {"feature_check": True, "claim_ids": [cid], "feature": feat, "presence": presence,
                       "presence_confidence": conf, "looked_at": "aerial photos"})


def keys_ok(c):
    assert set(c) == {"score", "band", "why", "decided_by", "sources"}
    assert isinstance(c["score"], int) and 0 <= c["score"] <= 100
    assert c["band"] == ("high" if c["score"] >= 75 else "medium" if c["score"] >= 45 else "low")
    for s in c["sources"]:
        assert set(s) == {"source", "says", "weight", "agrees_with_listing"}
        assert 0 <= s["weight"] <= 1
    for word in ("fake", "lie", "scam"):
        assert word not in c["why"].lower()


# ---- the table ----------------------------------------------------------------------------------------

def test_table_has_a_reason_for_every_cell():
    for key, row in trust.RELEVANCE.items():
        assert set(row) == set(trust.SOURCES), key
        for src, (w, why) in row.items():
            assert 0 <= w <= 1 and why.strip(), (key, src)


def test_kind_keys_for_amenities():
    assert trust.kind_key(claim("amenity", "resort-style pool", "building")) == trust.OUTDOOR
    assert trust.kind_key(claim("parking", "garage parking", "building")) == trust.OUTDOOR
    assert trust.kind_key(claim("laundry", "in-unit washer and dryer")) == trust.INDOOR
    assert trust.kind_key(claim("amenity", "fitness center", "building")) == trust.INDOOR
    assert trust.kind_key(claim("stories", 5, "building")) == "stories"


def test_old_aerial_photos_count_for_less():
    today = dt.date(2026, 11, 14)
    c = claim("amenity", "pool", "building")
    new = feature("aerial", "not_seen", 1.0)
    new["observed"]["capture_date"] = "2026-01-02"
    old = feature("aerial", "not_seen", 1.0)
    old["observed"]["capture_date"] = "2014-05-01"
    assert trust.weight(old, c, today) < trust.weight(new, c, today)
    assert "older_10y" in trust.quality(old, c, today)


# ---- 1. pool: aerial does not see it, the record names it ---------------------------------------------

def test_pool_named_by_record_beats_aerial_not_seen():
    # Not seeing something is weak (absence, x0.4); the record naming it is a direct statement. The record wins,
    # the claim is a match, and a match carries no confidence.
    c = claim("amenity", "resort-style pool", "building")
    evs = [record(amenities=["Pool", "Fitness center"]), feature("aerial", "not_seen", 0.9)]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    assert "confidence" not in v
    assert trust.named_weight(c, True) > trust.weight(evs[1], c)


def test_pool_only_not_seen_is_couldnt_check():
    c = claim("amenity", "resort-style pool", "building")
    v = judge(c, [record(amenities=["Fitness center"]), feature("aerial", "not_seen", 0.9)])
    assert v["verdict"] == "cant_check" and v["source"] is None  # absence is never a mismatch, not even close
    assert "confidence" not in v and "cannot prove" in v["reason"]


def test_a_sighting_is_never_outvoted_by_not_seeing():
    c = claim("amenity", "pool", "building")
    seen = ev("scan", {"feature_check": True, "claim_ids": ["c1"], "feature": "pool", "presence": "seen",
                       "presence_confidence": 0.9, "how": "scan_title", "scan_title": "SAMPLEscan01 Pool"})
    v = judge(c, [feature("aerial", "not_seen", 0.95), seen])
    assert v["verdict"] == "confirmed" and "confidence" not in v


# ---- 2. floors: county and aerial disagree --------------------------------------------------------------

def test_floors_county_beats_aerial_estimate():
    c = claim("stories", 7, "building")
    aerial = ev("aerial", {"stories": 6, "height_m": 23.1, "stories_range": [6, 7], "capture_date": "2026-01-02"})
    v = judge(c, [aerial, ev("county", {"stories": 7})])
    assert v["verdict"] == "confirmed" and v["source"] == "county"  # before: the aerial estimate decided (6)
    assert "confidence" not in v
    v = judge(claim("stories", 6, "building"),
              [ev("aerial", {"stories": 7, "height_m": 26.0, "stories_range": [7, 7]}), ev("county", {"stories": 5})])
    assert v["verdict"] == "contradicted" and v["source"] == "county" and v["found"] == "5 floors"
    keys_ok(v["confidence"])
    assert v["confidence"]["decided_by"] == "county"


def test_one_floor_off_with_the_aerial_range_covering_the_listing():
    c = claim("stories", 7, "building")
    aerial = ev("aerial", {"stories": 6, "height_m": 23.1, "stories_range": [6, 7]})
    v = judge(c, [aerial, ev("county", {"stories": 6}), record(stories=5)])
    assert v["verdict"] == "contradicted" and v["source"] == "county"
    conf = v["confidence"]
    keys_ok(conf)
    assert conf["band"] == "medium"
    aerial_row = next(s for s in conf["sources"] if s["source"] == "aerial")
    assert aerial_row["says"] == "about 6 floors (6 to 7)" and aerial_row["agrees_with_listing"] is False


def test_tall_tower_one_floor_off_is_low():
    c = claim("stories", 50, "building")
    v = judge(c, [ev("county", {"stories": 49}), record(stories=49),
                  ev("aerial", {"stories": 49, "height_m": 160.0, "stories_range": [48, 50]})])
    assert v["verdict"] == "contradicted"
    keys_ok(v["confidence"])
    assert v["confidence"]["band"] == "low"
    assert "tall building" in v["confidence"]["why"]


# ---- 3. square feet: this home's scan vs the plan table -------------------------------------------------

def test_sqft_exact_unit_scan_beats_plan_record():
    # a page about one home: its own scan (no plan) outranks the record's only floor plan. (950 sq ft: within 40% of
    # the record; a scan further off than that is implausible and no longer decides alone, see test_judge_fixes.py)
    c = claim("area_sqft", 723)
    scan = ev("scan", {"model_id": "SAMPLEscan01", "area_sqft": 950, "area_m2": 88.3})
    rec = record(floorplans=[{"name": "Plan A", "beds": 1, "baths": 1, "sqft": 723}])
    v = judge(c, [rec, scan], single_unit=True)
    assert v["verdict"] == "contradicted" and v["source"] == "scan" and v["found"] == "950 sq ft"
    conf = v["confidence"]
    keys_ok(conf)
    assert conf["decided_by"] == "scan"
    by = {s["source"]: s for s in conf["sources"]}
    assert by["scan"]["weight"] > by["record"]["weight"]
    assert by["record"]["agrees_with_listing"] is True
    assert "Matterport" in conf["why"] and "CoStar" in conf["why"]


def test_sqft_same_plan_scan_and_plan_record_tie_keeps_the_old_order():
    # both differ from the listing (when the record matches the listing, the record decides: test_judge_fixes.py)
    c = claim("area_sqft", 723, label="Plan B")
    scan = ev("scan", {"model_id": "SAMPLEscan01", "plan": "Plan B", "area_sqft": 1000})
    rec = record(plan="Plan B", kit_plan="Plan B", area_sqft=900)
    v = judge(c, [rec, scan])
    assert v["verdict"] == "contradicted" and v["source"] == "scan" and v["found"] == "1,000 sq ft"
    keys_ok(v["confidence"])


def test_room_labels_do_not_outvote_the_plan_record():
    # labels say 2 bedrooms (as the listing does) but the scan's title does not back them; the record says 1
    c = claim("bedrooms", 2, label="Plan C")
    scan = ev("scan", {"plan": "Plan C", "bedrooms": 2, "room_labels": ["Bedroom", "Bedroom"],
                       "model_name": "SAMPLEscan01"})
    v = judge(c, [scan, record(plan="Plan C", kit_plan="Plan C", bedrooms=1)])
    assert v["verdict"] == "contradicted" and v["source"] == "record"
    keys_ok(v["confidence"])


# ---- 4. a second source agreeing raises the score ---------------------------------------------------------

def test_two_sources_agree_scores_higher_than_one():
    c = claim("bedrooms", 2, label="Plan E")
    scan = ev("scan", {"plan": "Plan E", "model_name": "Plan E - 1 Bed / 1 Bath", "bedrooms": 1,
                       "counts_from": "model_name"})
    one = judge(c, [scan])
    two = judge(c, [scan, record(plan="Plan E", kit_plan="Plan E", bedrooms=1)])
    against = judge(c, [scan, record(plan="Plan E", kit_plan="Plan E", bedrooms=2)])
    for v in (one, two, against):
        assert v["verdict"] == "contradicted" and v["source"] == "scan"
        keys_ok(v["confidence"])
    assert two["confidence"]["score"] > one["confidence"]["score"] > against["confidence"]["score"]
    assert two["confidence"]["band"] == "high"
    assert "CoStar agrees" in two["confidence"]["why"]


def test_bigger_gap_scores_higher():
    c = lambda v: claim("area_sqft", v, label="Plan B")
    rec = lambda n: record(plan="Plan B", kit_plan="Plan B", area_sqft=n)
    big = judge(c(1184), [rec(723)])
    small = judge(c(800), [rec(723)])
    assert big["confidence"]["score"] > small["confidence"]["score"]


# ---- 5. matches carry nothing; agreeing sources change nothing ---------------------------------------------

def test_matches_and_cant_check_carry_no_confidence():
    assert "confidence" not in judge(claim("year_built", 2016, "building"),
                                     [ev("county", {"year_built": 2016}), record(year_built=2016)])
    assert "confidence" not in judge(claim("stories", 5, "building"), [])
    assert "confidence" not in judge(claim("amenity", "pool", "building"), [])


def test_verdicts_unchanged_where_sources_agree():
    # aerial and county agree on 6: the old order (aerial first) still decides, word for word
    v = judge(claim("stories", 5, "building"),
              [ev("county", {"stories": 6}), ev("aerial", {"stories": 6, "height_m": 23.1})])
    assert v["source"] == "aerial" and v["verdict"] == "contradicted"
    assert v["reason"].startswith("Listing says 5 floors. The measured roof height from Vexcel aerial imagery")
    # a single source speaks: it decides as before
    v = judge(claim("year_built", 2016, "building"), [ev("county", {"year_built": 2012})])
    assert v["verdict"] == "contradicted" and v["source"] == "county"


def test_near_miss_year_with_record_siding_with_listing_is_low():
    v = judge(claim("year_built", 2020, "building"),
              [ev("county", {"year_built": 2019}), record(year_built=2020)])
    # the building record backs the listing, the county does not: the sources differ, so not a mismatch
    assert v["verdict"] == "cant_check" and "confidence" not in v
    assert "CoStar lists 2020 as the year built, the county record lists 2019, so the sources differ." in v["reason"]


# ---- 6. the field travels in the verdict events of a whole run ------------------------------------------------

def test_confidence_reaches_the_verdict_events(monkeypatch):
    import scripted_cli
    from graph_events import check_run
    from harness.graph import Toolbox, run_check
    scripted_cli.install(monkeypatch)
    events = []
    snap = {"url": "https://example.test/listing", "title": "Sample", "address_text": f"{ADDRESS}, Houston, TX",
            "text": "2 Beds", "jsonld": []}
    run_check(snap, events.append, Toolbox.fake())
    check_run(events)
    vs = [e["verdict"] for e in events if e["type"] == "verdict"]
    assert vs
    for v in vs:
        if v["verdict"] in ("contradicted", "close"):
            keys_ok(v["confidence"])
        else:
            assert "confidence" not in v
    assert any("confidence" in v for v in vs)
