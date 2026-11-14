""""Fill me in" rows from Vexcel property data ("Seen from above", harness/fill_in.py::_from_above).
Invented data only (418 Linden Grove Ave, ids 900001 / 900002); no network."""
from harness.fill_in import TOPICS, build_fill_in

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
MATCH = {"status": "matched", "confidence": 0.97, "property_id": "900001", "kind": "building",
         "name": "Linden Grove", "address": ADDRESS, "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
         "record": {"stories": 4, "year_built": 2012, "unit_count": 80}, "floorplans": [],
         "matterport_model_ids": [], "how": "test"}
SOFT_BAD = ("fake", "lie", "scam", "no pool", "not seen", "none")


def claim(cid, value, kind="amenity"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "building", "label": None,
            "quote": value, "checkable": True}


def prop(**over):
    base = {"captured": "2025-12-19", "source": "Vexcel property data",
            "pool": {"present": True, "confidence": 0.95, "area_sqft": 1900, "kind": "in-ground", "stable": True},
            "hot_tub": {"present": False, "stable": True}, "sport_court": {"present": True, "stable": True},
            "playground": {"present": False, "stable": True}, "solar": {"present": True, "stable": True},
            "vehicles": 40, "tree_cover_pct": 28.4, "buildings": 2}
    base.update(over)
    return base


def prop_ev(p):
    return {"source": "aerial", "status": "ok", "summary": "Vexcel property data.", "detail": "",
            "observed": {"property": p}, "images": []}


def facts(p=None, claims=(), text="", evs=None):
    evs = [prop_ev(p if p is not None else prop())] if evs is None else evs
    out = build_fill_in(MATCH, evs, list(claims), None, text)
    for f in out:
        assert set(f) == {"id", "topic", "label", "value", "source", "note", "image"}
        assert f["topic"] in TOPICS and isinstance(f["value"], str) and f["value"]
    return {f["id"]: f for f in out if f["id"].startswith("aerial-")}


def test_features_tree_cover_and_vehicles_listed():
    by = facts()
    feat = by["aerial-features"]
    assert feat == {"id": "aerial-features", "topic": "Around it", "label": "Seen from above",
                    "value": "Pool (about 1,900 sq ft), sport court, solar panels", "source": "aerial",
                    "note": "From Vexcel's aerial survey of December 2025.", "image": None}
    assert by["aerial-trees"]["value"] == "About 28% tree cover on the lot"
    assert by["aerial-vehicles"]["value"] == "About 40 vehicles parked on site at survey time"
    assert all(f["source"] == "aerial" for f in by.values())
    for f in by.values():
        low = (f["value"] + " " + f["note"]).lower()
        assert not any(w in low for w in SOFT_BAD)
        assert "building" not in low and "stor" not in low and "footprint" not in low


def test_claimed_feature_is_skipped():
    by = facts(claims=[claim("c1", "Resort-style pool")])
    assert by["aerial-features"]["value"] == "Sport court, solar panels"
    by = facts(text="Lighted tennis courts and a sparkling pool.")
    assert by["aerial-features"]["value"] == "Solar panels"
    by = facts(claims=[claim("c1", "Pool"), claim("c2", "Tennis court"), claim("c3", "Solar panels")])
    assert "aerial-features" not in by and "aerial-trees" in by


def test_absent_feature_never_shown():
    p = prop(pool={"present": False, "stable": True}, sport_court={"present": False, "stable": False},
             solar={"present": None, "stable": False})
    by = facts(p)
    assert "aerial-features" not in by
    assert not any("pool" in f["value"].lower() or "court" in f["value"].lower() for f in by.values())


def test_unstable_feature_softened():
    p = prop(hot_tub={"present": True, "stable": False}, playground={"present": True, "stable": False})
    assert facts(p)["aerial-features"]["value"] == (
        "Pool (about 1,900 sq ft), sport court, solar panels; may also have a hot tub, a playground "
        "(seen in one survey)")
    p = {"captured": "2025-12-19", "pool": {"present": True, "area_sqft": None, "stable": False}}
    assert facts(p)["aerial-features"]["value"] == "May have a pool (seen in one survey)"


def test_vehicles_threshold_and_blank_values():
    assert "aerial-vehicles" not in facts(prop(vehicles=4))
    assert "aerial-vehicles" not in facts(prop(vehicles=0))
    assert facts(prop(vehicles=5))["aerial-vehicles"]["value"] == "About 5 vehicles parked on site at survey time"
    assert "aerial-trees" not in facts(prop(tree_cover_pct=0.0))
    by = facts({k: v for k, v in prop().items() if k not in ("tree_cover_pct", "vehicles")})
    assert set(by) == {"aerial-features"}


def test_missing_evidence_adds_nothing():
    assert facts(evs=[]) == {}
    bad = [{"source": "aerial", "status": "error", "summary": "x", "observed": {"property": prop()}, "images": []},
           {"source": "aerial", "status": "ok", "summary": "x", "observed": {"height_m": 14}, "images": []},
           {"source": "aerial", "status": "ok", "summary": "x", "observed": {"property": None}, "images": []},
           {"source": "aerial", "status": "ok", "summary": "x", "observed": {"property": "junk"}, "images": []}]
    assert facts(evs=bad) == {}
    out = build_fill_in(None, [prop_ev({"captured": "bad-date", "pool": "yes", "vehicles": True})], None)
    assert out == []


def test_no_survey_date_still_says_vexcel():
    p = prop()
    del p["captured"]
    assert facts(p)["aerial-features"]["note"] == "From Vexcel's aerial survey."


def test_counts_used_when_present():
    p = prop(pool={"present": True, "area_sqft": 2400, "stable": True, "count": 2},
             hot_tub={"present": True, "stable": True, "count": 3})
    assert facts(p)["aerial-features"]["value"] == (
        "2 pools (about 2,400 sq ft in all), 3 hot tubs, sport court, solar panels")
    p = prop(counts={"sport_court": 2, "pool": 1})
    assert facts(p)["aerial-features"]["value"] == "Pool (about 1,900 sq ft), 2 sport courts, solar panels"
    p = prop(pool={"present": True, "area_sqft": 1900, "stable": True, "count": True})  # not a count
    assert facts(p)["aerial-features"]["value"].startswith("Pool (about 1,900 sq ft)")


def test_works_without_a_building_match():
    out = build_fill_in({"status": "no_match"}, [prop_ev(prop())], [], None, "")
    assert [f["id"] for f in out] == ["aerial-features", "aerial-trees", "aerial-vehicles"]
