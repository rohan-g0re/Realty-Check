"""Fixes from a live check of five runs: a scan that does not fit the record never outvotes a record that agrees with
the listing; a unit page shows its own record row; "spa" is a hot tub only when used as one; plural pools; plain step
wording; roof-height floors are a rough guide on low buildings; no Vexcel coverage is said out loud; a scan photo
read the record already answers is not made. Invented data only (418 Linden Grove Ave, ids 900001 / 900002, unit
1204, scan SAMPLEscan01); no network."""
import pytest

from harness.graph import Toolbox, amenities, trust
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
SNAP = {"url": "https://example.test/listing", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
        "text": "", "jsonld": []}
FORBIDDEN = ("fake", "lie", "scam", "fraud")


def claim(kind, value, label=None, scope="unit", cid="c1"):
    return {"id": cid, "kind": kind, "value": value, "unit": "sqft" if kind == "area_sqft" else None,
            "scope": scope, "label": label, "quote": str(value), "checkable": True}


def amenity(text, cid="a1"):
    return {"id": cid, "kind": "amenity", "value": text, "unit": None, "scope": "building", "label": None,
            "quote": text, "checkable": True}


def ev(source, observed, status="ok", images=None):
    return evidence(source, status, "s", observed, images)


def plan_record(plan, beds, baths, sqft, **extra):
    return ev("record", {"property_id": "900001", "plan": plan, "kit_plan": plan, "bedrooms": beds,
                         "bathrooms": baths, "area_sqft": sqft, **extra})


def plan_scan(plan, sqft, beds, title, **extra):
    return ev("scan", {"model_id": "SAMPLEscan01", "model_name": title, "plan": plan, "bedrooms": beds,
                       "bathrooms": 2, "area_sqft": sqft, "room_labels": ["Bedroom"] * int(beds), **extra},
              images=[{"path": "C:/x/still.jpg", "caption": "Scan still"}])


def soft(v):
    text = (v["reason"] + " " + (v.get("confidence") or {}).get("why", "")).lower()
    for word in FORBIDDEN:
        assert word not in text


# ---- 1. a scan that does not fit the record never outvotes a record that agrees with the listing -----------

def test_record_agreeing_with_listing_beats_an_implausible_scan():
    # listing and record 723 sq ft; the plan's scan measures 1,184 and its labels count 5 bedrooms in a 2-bed plan
    rec = plan_record("Plan B", 2, 2, 723)
    scan = plan_scan("Plan B", 1184, 5, "Linden Grove - Plan B - 2 Bed / 2 Bath")
    v = judge(claim("area_sqft", 723, "Plan B"), [rec, scan])
    assert v["verdict"] == "confirmed" and v["source"] == "record" and v["found"] == "723 sq ft"
    assert "confidence" not in v
    assert "The 3D scan measured 1,184 sq ft of floor" in v["reason"]
    assert "includes more than the listed living area" in v["reason"] and "building record decides" in v["reason"]
    assert v["reason"].count("1,184") == 1  # the scan is not mentioned a second time
    soft(v)


def test_record_agreeing_with_listing_beats_a_smaller_scan_too():
    rec = plan_record("Plan C", 2, 2, 1079)
    scan = plan_scan("Plan C", 845, 3, "Linden Grove - Plan C - 2 Bed / 2 Bath")
    v = judge(claim("area_sqft", 1055, "Plan C"), [rec, scan])
    assert v["verdict"] == "confirmed" and v["source"] == "record" and v["found"] == "1,079 sq ft"
    assert "scans usually measure less than the advertised size" in v["reason"]


def test_listing_and_record_disagreeing_is_still_flagged():
    rec = plan_record("Plan B", 2, 2, 723)
    scan = plan_scan("Plan B", 1184, 5, "Linden Grove - Plan B - 2 Bed / 2 Bath")
    v = judge(claim("area_sqft", 900, "Plan B"), [rec, scan])
    assert v["verdict"] in ("contradicted", "close") and v["source"] == "record" and v["found"] == "723 sq ft"
    assert v["confidence"]["decided_by"] == "record"
    soft(v)
    # a scan that fits the record still decides as before when the record also differs from the listing
    v = judge(claim("area_sqft", 723, "Plan B"), [plan_record("Plan B", 2, 2, 900),
                                                  plan_scan("Plan B", 1000, 2, "Linden Grove - Plan B")])
    assert v["verdict"] == "contradicted" and v["source"] == "scan"


def test_record_paired_by_size_does_not_count_as_agreeing():
    # a plan paired with the listing by its size agrees with the listing's size by construction
    rec = plan_record("Plan B", 2, 2, 723, plan_match="Matched to the building's plan B by size.")
    scan = plan_scan("Plan B", 840, 2, "Linden Grove - Plan B")
    v = judge(claim("area_sqft", 723, "Plan B"), [rec, scan])
    assert v["source"] == "scan" and "building record decides" not in v["reason"]


def test_implausible_scan_gets_a_strong_trust_penalty():
    ref = {"bedrooms": 2, "area_sqft": 723}
    assert trust.scan_implausible({"bedrooms": 5, "area_sqft": 800}, ref)  # labels: 3 bedrooms too many
    assert trust.scan_implausible({"bedrooms": 2, "area_sqft": 1184}, ref)  # 64% larger
    assert trust.scan_implausible({"bedrooms": 2, "area_sqft": 400}, ref)  # 45% smaller
    assert trust.scan_implausible({"bedrooms": 3, "area_sqft": 950}, ref) is None  # one label off, 31% off
    assert trust.scan_implausible({"bedrooms": 5, "counts_from": "model_name"}, ref) is None  # a title count
    assert trust.QUALITY["implausible"][0] <= 0.3
    flagged = plan_scan("Plan B", 1184, 5, "Linden Grove - Plan B")
    flagged["observed"]["implausible"] = "it gives 1,184 sq ft where the building record has 723"
    c = claim("area_sqft", 723, "Plan B")
    assert "implausible" in trust.quality(flagged, c)
    assert trust.weight(flagged, c) < trust.weight(plan_record("Plan B", 2, 2, 723), c) / 2
    # a bed count the scan's own title gives is not penalised (the title is not the labels)
    titled = {**flagged, "observed": {**flagged["observed"], "count_from_title": True}}
    assert "implausible" not in trust.quality(titled, claim("bedrooms", 2, "Plan B"))


def test_implausible_scan_cannot_decide_alone_against_the_record_on_a_one_home_page():
    row = ev("record", {"unit": "1204", "unit_row": True, "bedrooms": 2, "bathrooms": 2, "area_sqft": 723,
                        "unit_says": "2 beds, 2 baths, 723 sq ft"})
    scan = ev("scan", {"model_id": "SAMPLEscan01", "area_sqft": 1184, "scan_of": "unit", "unit": "1204"})
    v = judge(claim("area_sqft", 723), [row, scan], single_unit=True)
    assert v["verdict"] == "confirmed" and v["source"] == "record"


# ---- 2. a unit page shows the unit's own record row, a plan scan only supports ------------------------------

ROW = ev("record", {"unit": "1204", "unit_row": True, "bedrooms": 0, "bathrooms": 1, "area_sqft": 575,
                    "sqft_min": True, "unit_plan": "Plan S", "unit_says": "a studio, 1 bath, 575 sq ft"})


@pytest.mark.parametrize("scan_of", ["plan", "unit"])
def test_unit_row_decides_the_size_on_a_unit_page(scan_of):
    scan = ev("scan", {"model_id": "SAMPLEscan01", "model_name": "Linden Grove - Plan S - Studio / 1 Bath",
                       "bedrooms": 0, "bathrooms": 1, "area_sqft": 840, "unit": "1204", "scan_of": scan_of,
                       "unit_plan": "Plan S"})
    v = judge(claim("area_sqft", 1100), [ROW, scan], single_unit=True)
    assert v["verdict"] == "contradicted" and v["source"] == "record" and v["found"] == "575 sq ft"
    assert v["confidence"]["decided_by"] == "record"
    assert "lists unit 1204 as a studio, 1 bath, 575 sq ft" in v["reason"]
    soft(v)
    b = judge(claim("bedrooms", 2), [ROW, scan], single_unit=True)
    assert b["verdict"] == "contradicted" and b["source"] == "record" and b["found"] == "0 bedrooms"


def _unit_run(title):
    def scan_summary(mid):
        return evidence("scan", "ok", f"The 3D scan titled '{title}'.",
                        {"model_id": mid, "model_name": title, "bedrooms": 0, "bathrooms": 1,
                         "counts_from": "model_name", "area_sqft": 840})
    events = []
    run = run_mod.Run(SNAP, events.append, Toolbox.fake(scan_summary=scan_summary))
    run.match = {"status": "matched", "property_id": "900001", "kind": "building", "floorplans": []}
    run.unit, run.unit_row, run.unit_scan_id = "1204", {"plan": "Plan S", "beds": 0, "baths": 1, "sqft": 575}, \
        "SAMPLEscan01"
    run.scan_rooms("")
    scan = next(e for e in run.evidence.values() if e["source"] == "scan" and e["status"] == "ok")
    return run, events, scan


def test_scan_filed_under_the_unit_but_titled_with_its_plan_is_a_plan_scan():
    _, events, scan = _unit_run("Linden Grove - Plan S - Studio / 1 Bath")
    assert scan["observed"]["scan_of"] == "plan" and scan["observed"]["unit_plan"] == "Plan S"
    _, _, own = _unit_run("Linden Grove - Unit 1204 - Plan S")  # names the unit itself: its own scan
    assert own["observed"]["scan_of"] == "unit"
    _, _, plain = _unit_run("Studio, 1BA - 575SF")  # names no plan
    assert plain["observed"]["scan_of"] == "unit"


# ---- 3. "spa" is a hot tub only when used as one -------------------------------------------------------------

@pytest.mark.parametrize("text,key", [
    ("Access to the Serenity Wellness Spa", None), ("Day spa", None), ("Wellness spa", None),
    ("Spa-like bathrooms", None), ("Spa-inspired bathroom", None), ("Spa services on request", None),
    ("Linden Grove Spa", None), ("Med spa on site", None), ("Spa tub in the primary bath", None),
    ("Two day spas", None), ("Pet spa", None),
    ("Hot tub", "hot_tub"), ("Jacuzzi", "hot_tub"), ("Spa/hot tub", "hot_tub"), ("Heated spa", "hot_tub"),
    ("Outdoor spa and cabanas", "hot_tub"), ("Spa", "hot_tub"), ("Two spas", "hot_tub"),
    ("2 heated spas", "hot_tub"), ("Resort-style pool and spa", "pool"), ("Pool & spa", "pool"),
    ("2 pools and 2 spas", "pool"),
])
def test_spa_is_a_hot_tub_only_when_used_as_one(text, key):
    assert trust.property_key(amenity(text)) == key


def test_named_wellness_spa_is_not_checked_against_the_hot_tub_flag():
    prop = {"captured": "2025-12-19", "hot_tub": {"present": False, "stable": True}}
    v = judge(amenity("Access to the Serenity Wellness Spa"), [ev("aerial", {"property": prop})])
    assert v["verdict"] == "cant_check" and "Vexcel property data" not in v["reason"]
    assert v["found"] != "Not in Vexcel property data"


# ---- 4. plural pools are recognised everywhere -----------------------------------------------------------------

@pytest.mark.parametrize("text", ["2 pools", "Pools", "Two resort-style pools", "Pool"])
def test_plural_pools_are_a_picture_feature(text):
    feat = amenities.feature_for(amenity(text))
    assert feat and feat[0] == "pool" and feat[2] == "aerial"
    assert trust.property_key(amenity(text)) == "pool"
    assert r"\bpools?\b" in amenities.title_rooms(amenity(text))


def test_pool_tables_are_still_not_pools():
    assert amenities.feature_for(amenity("2 pool tables")) is None
    assert trust.property_key(amenity("Pool tables")) is None


# ---- 5. step wording ----------------------------------------------------------------------------------------------

def test_scan_rooms_step_is_a_plain_sentence():
    events = []
    run = run_mod.Run(SNAP, events.append, Toolbox.fake())
    run.scan_rooms("Plan B")
    run.scan_rooms("")
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert "Checking floor plan Plan B against its record and 3D scan." in steps
    assert "Checking this unit against its record and 3D scan." in steps
    assert not [s for s in steps if "its scan or record" in s]
    _, unit_events, _ = _unit_run("Studio, 1BA - 575SF")
    assert "Checking unit 1204 against its record and 3D scan." in [e["text"] for e in unit_events
                                                                     if e["type"] == "step"]


# ---- 6. floors from roof height: rough on low buildings, and called roof height -----------------------------

LOW_ROOF = ev("aerial", {"height_m": 11.1, "stories": 3, "stories_range": [3, 3], "capture_date": "2026-02-07"})


def test_low_roof_estimate_never_contradicts_county():
    v = judge(claim("stories", 1, scope="building"), [LOW_ROOF, ev("county", {"stories": 1})])
    assert v["verdict"] == "confirmed" and v["source"] == "county" and v["found"] == "1 floor"
    assert "aerial photo" not in v["reason"]
    assert "Vexcel roof height suggests about 3 floors" in v["reason"]
    # county says 2, the roof height agrees with the listing's 1: county still decides
    v = judge(claim("stories", 1, scope="building"),
              [ev("aerial", {"height_m": 5.0, "stories": 1}), ev("county", {"stories": 2})])
    assert v["source"] == "county" and v["found"] == "2 floors"


def test_low_roof_estimate_alone_is_only_a_near_miss():
    v = judge(claim("stories", 1, scope="building"), [LOW_ROOF])
    assert v["verdict"] == "close" and v["source"] == "aerial"
    assert "Vexcel roof height (11.1 m) suggests about 3 floors" in v["reason"]
    assert "rough guide" in v["reason"] and "aerial photo" not in v["reason"]
    soft(v)


def test_tall_roof_estimate_unchanged():
    v = judge(claim("stories", 5, scope="building"), [ev("aerial", {"stories": 6, "height_m": 23.1})])
    assert v["verdict"] == "contradicted" and v["source"] == "aerial"
    v = judge(claim("stories", 6, scope="building"),
              [ev("aerial", {"stories": 6, "height_m": 23.1}), ev("county", {"stories": 6})])
    assert "aerial photo" not in v["reason"]


# ---- 7. no Vexcel coverage is said out loud ------------------------------------------------------------------------

def _located_run(**tools):
    events = []
    tb = Toolbox.fake(**{k: v for k, v in tools.items() if k != "property_attributes"})
    if "property_attributes" in tools:
        tb.property_attributes = tools["property_attributes"]
    run = run_mod.Run(SNAP, events.append, tb)
    run.lat, run.lon = 29.75, -95.37
    return run, events


class _NoCoverage(Exception):  # the name tools.aerial gives its HTTP 403 / 404 exception
    pass


@pytest.mark.parametrize("answer", ["dict", "raise"])
def test_property_data_with_no_coverage_gives_a_step_and_evidence(answer):
    def fetch(lat, lon):
        if answer == "raise":
            raise _NoCoverage("property/extract answered 404")
        return {"no_coverage": True}
    run, events = _located_run(property_attributes=fetch)
    out = run.property_data()
    assert "Vexcel has no aerial coverage here." in out
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert steps.count("Vexcel has no aerial coverage here.") == 1
    got = [e for e in run.evidence.values() if e["observed"].get("no_coverage")]
    assert len(got) == 1 and got[0]["source"] == "aerial" and got[0]["status"] == "no_evidence"
    assert got[0]["summary"] == "Vexcel has no aerial coverage here."


def test_property_data_failure_stays_silent_as_before():
    run, events = _located_run(property_attributes=lambda lat, lon: None)
    assert run.property_data() == "No Vexcel property data for this location."
    assert not run.evidence


def test_no_coverage_photos_and_height_say_so_once():
    nothing = lambda *a, **k: evidence("aerial", "no_evidence", "Vexcel has no aerial coverage at this point "
                                       "(confirmed coverage: Houston and Los Angeles).")
    run, events = _located_run(aerial_images=nothing, building_height=nothing)
    run.measure_building()
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert steps.count("Vexcel has no aerial coverage here.") == 1


# ---- 8. a scan photo read the record already answers is not made; a late one is not an error -------------------

def _photo_run(log):
    def read_floorplan(paths):
        log.append("read_floorplan")
        return {"status": "ok", "answer": {"bedrooms": 2, "bathrooms": 2}, "confidence": 0.7, "notes": "synthetic"}
    events = []
    run = run_mod.Run(SNAP, events.append, Toolbox.fake(read_floorplan=read_floorplan))
    run._plans["plan b"] = {"label": "Plan B", "kit": None, "kit_plans": [], "how": "name", "ambiguous": False,
                            "scan_id": "SAMPLEscan01", "scan_how": "name", "scan_title": None, "note": ""}
    return run


def test_scan_photo_read_skipped_when_the_record_gives_the_room_counts():
    log = []
    run = _photo_run(log)
    run.add(plan_record("Plan B", 2, 2, 723), "record")
    out = run.scan_photos("Plan B")
    assert log == [] and "kept as proof only" in out
    assert not [e for e in run.evidence.values() if e["status"] == "error"]


def test_scan_photo_read_out_of_time_is_not_an_error():
    log = []
    run = _photo_run(log)
    run.budget_s = 5.0  # less than VISION_MIN_LEFT_S left
    run.scan_photos("Plan B")
    assert log == []
    late = [e for e in run.evidence.values() if e["source"] == "scan" and e["status"] != "ok"]
    assert len(late) == 1 and late[0]["status"] == "no_evidence"
    assert late[0]["summary"] == "We ran out of time before the photos were read."


def test_scan_photo_read_still_runs_without_record_counts():
    log = []
    run = _photo_run(log)
    run.scan_photos("Plan B")
    assert log == ["read_floorplan"]
