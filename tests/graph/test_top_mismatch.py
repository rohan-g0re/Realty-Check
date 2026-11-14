"""harness.top_mismatch.pick_top: which mismatch leads the report hero. Stubbed model only (no CLI call).
Invented verdicts (418 Linden Grove Ave)."""
import json
import time

import pytest

from harness import top_mismatch as tm
from harness.top_mismatch import by_rule, pick_top, soft_why

CLAIMS = [{"id": "c1", "kind": "bedrooms", "quote": "2 Beds"},
          {"id": "c2", "kind": "stories", "quote": "5-story building"},
          {"id": "c3", "kind": "area_sqft", "quote": "1,100 sq ft"},
          {"id": "c4", "kind": "amenity", "quote": "Pool"},
          {"id": "c5", "kind": "year_built", "quote": "Built in 2016"}]


def _v(cid, verdict, claimed, found, band=None, score=None, source="scan"):
    v = {"claim_id": cid, "verdict": verdict, "claimed": claimed, "found": found, "source": source}
    if band:
        v["confidence"] = {"band": band, "score": score, "decided_by": source,
                           "sources": [{"source": source, "says": found}]}
    return v


THREE = [_v("c1", "contradicted", "2 bedrooms", "1 bedroom + den", "medium", 64),
         _v("c2", "contradicted", "5 stories", "6 stories", "high", 82, "aerial"),
         _v("c3", "contradicted", "1,100 sq ft", "968 sq ft", "medium", 58),
         _v("c5", "close", "built in 2016", "built in 2015", "high", 90, "county"),
         _v("c4", "confirmed", "pool", "pool", source="aerial")]


@pytest.fixture(autouse=True)
def _clear():
    tm._CACHE.clear()
    yield
    tm._CACHE.clear()


def _never(prompt):
    raise AssertionError("the model must not be asked")


def test_none_or_one_mismatch_skips_the_model():
    assert pick_top([], llm=_never) is None
    assert pick_top([_v("c4", "confirmed", "pool", "pool")], llm=_never) is None
    one = [_v("c1", "contradicted", "2", "1"), _v("c5", "close", "2016", "2015")]
    assert pick_top(one, claims=CLAIMS, llm=_never) == {"claim_id": "c1", "why": "", "by": "rule"}


def test_close_leads_only_when_nothing_is_contradicted():
    only_close = [_v("c5", "close", "built in 2016", "built in 2015", "low", 30),
                  _v("c3", "close", "1,100 sq ft", "1,080 sq ft", "medium", 50)]
    assert by_rule(only_close, {c["id"]: c for c in CLAIMS})["claim_id"] == "c3"  # band first
    seen = {}

    def llm(prompt):
        seen["p"] = prompt
        return json.dumps({"claim_id": "c1", "why": "Bedrooms change the home you get."})
    out = pick_top(THREE, claims=CLAIMS, llm=llm)
    assert out == {"claim_id": "c1", "why": "Bedrooms change the home you get.", "by": "model"}
    # the model only sees the hard ones, with listed / found values, kind, confidence and sources
    assert '"c5"' not in seen["p"] and '"c4"' not in seen["p"]
    assert "1 bedroom + den" in seen["p"] and '"kind": "bedrooms"' in seen["p"] and "Matterport" in seen["p"]


def test_rule_order():
    claims = {c["id"]: c for c in CLAIMS}
    # high band beats medium even for floors vs bedrooms
    assert by_rule(THREE, claims)["claim_id"] == "c2"
    same_band = [_v("c4", "contradicted", "pool", "no pool", "medium", 70, "aerial"),
                 _v("c3", "contradicted", "1,100 sq ft", "968 sq ft", "medium", 50),
                 _v("c1", "contradicted", "2 bedrooms", "1 bedroom", "medium", 40)]
    assert by_rule(same_band, claims)["claim_id"] == "c1"  # then kind: beds > size > amenity
    gaps = [_v("c3", "contradicted", "1,100 sq ft", "1,050 sq ft"), _v("c3b", "contradicted", "900 sq ft", "700 sq ft")]
    assert by_rule(gaps, {"c3": CLAIMS[2], "c3b": {"kind": "area_sqft"}})["claim_id"] == "c3b"  # bigger gap
    assert by_rule([{"claim_id": "x", "verdict": "contradicted"}, {"claim_id": "y", "verdict": "contradicted"}]) \
        == {"claim_id": "x", "why": "", "by": "rule"}  # nothing to tell apart: page order


@pytest.mark.parametrize("reply", [
    '{"claim_id": "c9", "why": "x"}',            # not one of the inputs
    '{"claim_id": "c5", "why": "x"}',            # a close one while hard ones exist
    "I think the bedrooms.",                       # no JSON
    '["c1"]',                                     # not an object
])
def test_bad_answers_fall_back_to_the_rule(reply):
    out = pick_top(THREE, claims=CLAIMS, llm=lambda p: reply)
    assert out == {"claim_id": "c2", "why": "", "by": "rule"}


def test_errors_and_timeout_fall_back_fast():
    def boom(p):
        raise RuntimeError("no CLI")
    assert pick_top(THREE, claims=CLAIMS, llm=boom)["by"] == "rule"
    tm._CACHE.clear()

    def slow(p):
        time.sleep(3)
        return '{"claim_id": "c1", "why": "late"}'
    t0 = time.perf_counter()
    out = pick_top(THREE, claims=CLAIMS, llm=slow, timeout=0.3)
    assert time.perf_counter() - t0 < 1.5 and out["by"] == "rule" and out["claim_id"] == "c2"


@pytest.mark.parametrize("why,kept", [
    ("Bedrooms change the home you get.", True),
    ("The listing is a scam about the bedrooms.", False),
    ("They lie about the floors.", False),
    ("Looks fake.", False),
    ("x" * 121, False),
    ("", False),
])
def test_reason_must_be_short_and_soft(why, kept):
    out = pick_top(THREE, claims=CLAIMS, llm=lambda p: json.dumps({"claim_id": "c3", "why": why}))
    assert out["claim_id"] == "c3" and out["by"] == "model"
    assert out["why"] == (why if kept else "")
    assert soft_why(why) == (why if kept else "")


def test_cached_per_run():
    calls = []

    def llm(p):
        calls.append(p)
        return '{"claim_id": "c3", "why": "Size sets the rent."}'
    a = pick_top(THREE, claims=CLAIMS, llm=llm)
    b = pick_top(THREE, claims=CLAIMS, llm=llm)
    assert a == b and len(calls) == 1
    b["claim_id"] = "zz"  # callers cannot change the cached answer
    assert pick_top(THREE, claims=CLAIMS, llm=llm)["claim_id"] == "c3"
