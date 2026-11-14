"""Tests for harness.claims. Fast tests use the deterministic layer only.
Live tests call the model once per fixture and run only with RC_LIVE=1."""
import json
import os
import re
import time
from pathlib import Path

import pytest

from harness.claims import extract_claims, validate_claim
from harness.claims.extract import JSONLD_PREFIX, NUMERIC_KINDS, to_number

FIX = Path(__file__).parent / "fixtures"
LIVE = os.environ.get("RC_LIVE") == "1"


def live(f):
    return pytest.mark.live(pytest.mark.skipif(not LIVE, reason="set RC_LIVE=1 to call the model")(f))

# numbers that appear only in the "similar homes" carousels of fixture b
B_CAROUSEL_SQFT = {980, 2210, 760, 1875, 3320, 1010, 1690, 1050, 2640}
B_CAROUSEL_BEDS = {1, 2, 4, 5}
B_CAROUSEL_BATHS = {1, 1.5, 2.5, 3, 3.5}


def load(name: str) -> dict:
    snap = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    snap["text"] = (FIX / f"{name}.txt").read_text(encoding="utf-8")
    return snap


def find(claims, kind, value=None, label=None):
    return [c for c in claims if c["kind"] == kind and (value is None or c["value"] == value)
            and (label is None or (c["label"] and label in c["label"]))]


def norm(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


# ------------------------------------------------------------------ shared expectations

def check_a(claims):
    assert find(claims, "stories", 5), claims
    assert find(claims, "year_built", 2016), claims
    assert find(claims, "unit_count", 100), claims
    assert find(claims, "bedrooms", 2, label="E"), claims
    assert not find(claims, "year_built", 2008) and not find(claims, "stories", 12), "carousel leaked"
    assert not find(claims, "unit_count", 240), "carousel leaked"


def check_b(claims):
    assert find(claims, "bedrooms", 3), claims
    assert find(claims, "bathrooms", 2), claims
    assert find(claims, "area_sqft", 1450), claims
    assert not [c for c in claims if c["kind"] == "area_sqft" and c["value"] in B_CAROUSEL_SQFT], claims
    assert not [c for c in claims if c["kind"] == "bedrooms" and c["value"] in B_CAROUSEL_BEDS], claims
    assert not [c for c in claims if c["kind"] == "bathrooms" and c["value"] in B_CAROUSEL_BATHS], claims
    assert not find(claims, "year_built", 2005) and not find(claims, "year_built", 2011), claims


def check_shape(claims, page):
    assert len(claims) <= 40
    assert [c["id"] for c in claims] == [f"c{i}" for i in range(1, len(claims) + 1)]
    for c in claims:
        assert validate_claim(c) == [], (c, validate_claim(c))
        if not c["quote"].startswith(JSONLD_PREFIX):
            assert norm(c["quote"]) in norm(page), c


# ------------------------------------------------------------------ fixtures sanity

@pytest.mark.parametrize("name", ["a_complex", "b_marketplace", "c_sparse"])
def test_fixture_size(name):
    assert len(load(name)["text"]) >= 8000


# ------------------------------------------------------------------ deterministic layer

def test_a_deterministic():
    snap = load("a_complex")
    claims = extract_claims(snap, use_model=False)
    check_a(claims)
    check_shape(claims, snap["text"])
    assert find(claims, "bedrooms", 0, label="A"), "Plan A studio -> 0 bedrooms"
    assert find(claims, "area_sqft", 1100, label="E")


def test_b_deterministic():
    snap = load("b_marketplace")
    claims = extract_claims(snap, use_model=False)
    check_b(claims)
    check_shape(claims, snap["text"])


def test_c_deterministic_has_no_numbers():
    snap = load("c_sparse")
    claims = extract_claims(snap, use_model=False)
    check_shape(claims, snap["text"])
    assert not [c for c in claims if c["kind"] in NUMERIC_KINDS], claims


def test_jsonld_only():
    snap = {"address_text": "12 Oak St, Town, TX", "text": "",
            "jsonld": [{"@graph": [{"@type": "Apartment", "address": {"streetAddress": "12 Oak St"},
                                     "numberOfBedrooms": "2", "numberOfBathroomsTotal": 1,
                                     "floorSize": {"value": 70, "unitCode": "MTK"}, "yearBuilt": "1990"},
                                    {"@type": "Apartment", "address": {"streetAddress": "99 Elm St"},
                                     "numberOfBedrooms": 5}]}]}
    claims = extract_claims(snap, use_model=False)
    assert find(claims, "bedrooms", 2) and find(claims, "bathrooms", 1) and find(claims, "year_built", 1990)
    assert find(claims, "area_sqft", 753)
    assert not find(claims, "bedrooms", 5)
    check_shape(claims, "")


def test_floor_plans_in_jsonld():
    snap = {"address_text": "", "text": "",
            "jsonld": [{"@type": "ApartmentComplex", "numberOfAccommodationUnits": {"value": 48},
                        "containsPlace": [{"@type": "FloorPlan", "name": "The Birch", "numberOfBedrooms": 1},
                                          {"@type": "FloorPlan", "name": "The Cedar", "numberOfBedrooms": 2}]}]}
    claims = extract_claims(snap, use_model=False)
    assert find(claims, "unit_count", 48)
    assert find(claims, "bedrooms", 1, label="Birch") and find(claims, "bedrooms", 2, label="Cedar")


@pytest.mark.parametrize("raw,want", [("Studio", 0), ("1,100 sq ft", 1100), ("5-story", 5), (2.0, 2),
                                      ("1.5 Baths", 1.5), ("two", 2), ("luxury", None)])
def test_to_number(raw, want):
    assert to_number(raw) == want


def test_validate_claim():
    good = {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit",
            "label": "Plan E", "quote": "2 Beds", "checkable": True}
    assert validate_claim(good) == []
    assert validate_claim({**good, "value": "2"})
    assert validate_claim({**good, "kind": "garage"})
    assert validate_claim({**good, "scope": "room"})
    assert validate_claim({k: v for k, v in good.items() if k != "quote"})


def test_model_failure_falls_back(monkeypatch):
    import harness.claims.extract as ex

    def boom(*a, **k):
        raise RuntimeError("cli down")
    monkeypatch.setattr(ex, "_model_claims", boom)
    snap = load("b_marketplace")
    claims = extract_claims(snap, use_model=True)
    check_b(claims)


def test_post_check_drops_unquoted_and_coerces(monkeypatch):
    import harness.claims.extract as ex
    page = "Plan E\t2 Beds\t2 Baths\t1,100 sq ft\nA 5-story building\nLuxury living"
    reply = [{"kind": "bedrooms", "value": "2 Beds", "scope": "unit", "label": "Plan E", "quote": "2  beds"},
             {"kind": "area_sqft", "value": "1,100 sq ft", "scope": "unit", "label": "Plan E", "quote": "1,100 sq ft"},
             {"kind": "stories", "value": "5-story", "scope": "building", "label": None, "quote": "5-story building"},
             {"kind": "bedrooms", "value": 3, "scope": "unit", "label": None, "quote": "3 bedrooms invented"},
             {"kind": "other", "value": "luxury", "scope": "building", "label": None, "quote": "Luxury living",
              "checkable": False}]
    got = ex._post_check(reply, ex._norm(page))
    vals = {(c["kind"], c["value"]) for c in got}
    assert vals == {("bedrooms", 2), ("area_sqft", 1100), ("stories", 5), ("other", "luxury")}
    assert [c for c in got if c["kind"] == "other"][0]["checkable"] is False


# ------------------------------------------------------------------ live model (RC_LIVE=1)

_live_cache = {}


def live_claims(name):
    """One model call per fixture; records whether the model answered (so a silent fallback fails the test)."""
    if name not in _live_cache:
        import harness.claims.extract as ex
        snap = load(name)
        real, info = ex._model_claims, {}

        def spy(address, excerpt, page_norm):
            info["prompt_chars"] = len(ex.PROMPT.format(address=address, excerpt=excerpt))
            got = real(address, excerpt, page_norm)
            info["model_claims"] = len(got)
            return got
        ex._model_claims = spy
        try:
            t = time.time()
            claims = ex.extract_claims(snap, use_model=True)
            dt = time.time() - t
        finally:
            ex._model_claims = real
        print(f"\n[live] {name}: {dt:.1f}s wall, prompt {info.get('prompt_chars')} chars, "
              f"model claims kept {info.get('model_claims')}, final {len(claims)}")
        for c in claims:
            print("   ", c["id"], c["kind"], c["value"], c["scope"], c["label"], repr(c["quote"]), c["checkable"])
        _live_cache[name] = (snap, claims, info)
    return _live_cache[name]


@live
def test_live_a():
    snap, claims, info = live_claims("a_complex")
    assert "model_claims" in info, "model did not answer"
    check_a(claims)
    check_shape(claims, snap["text"])


@live
def test_live_b():
    snap, claims, info = live_claims("b_marketplace")
    assert "model_claims" in info, "model did not answer"
    check_b(claims)
    check_shape(claims, snap["text"])


@live
def test_live_c():
    snap, claims, info = live_claims("c_sparse")
    assert "model_claims" in info, "model did not answer"
    check_shape(claims, snap["text"])
    assert not [c for c in claims if c["kind"] in NUMERIC_KINDS and c["checkable"]], claims
