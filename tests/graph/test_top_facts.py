"""harness.top_facts.pick_top_facts: the few things the listing did not say. Stubbed model only (no CLI call).
Invented facts (418 Linden Grove Ave, property 900001, scan SAMPLEscan01)."""
import json
import time

import pytest

from harness import top_facts as tf
from harness.fill_in import build_fill_in
from harness.top_facts import by_rule, candidates, pick_top_facts

AERIAL_IMG = "/tmp/rc_test/aerial/900001-top.jpg"


def _f(fid, topic, label, value, source="record", image=None, note=""):
    return {"id": fid, "topic": topic, "label": label, "value": value, "source": source, "note": note,
            "image": image}


FACTS = [
    _f("year-built", "Building", "Year built", "2016, about 10 years old"),
    _f("floors", "Building", "Floors", "3 floors above ground"),
    _f("homes", "Building", "Number of homes", "120"),
    _f("class", "Building", "Building class", "Class B"),
    _f("rating", "Building", "CoStar building rating", "3 of 5 stars"),
    _f("elevators", "Building", "Elevator", "Walk-up building (no elevator on record)"),
    _f("owner", "Building", "Owner", "A company"),
    _f("lot", "Building", "Lot size", "1.2 acres"),
    _f("available", "Rent", "Homes available to rent", "6 of 120"),
    _f("vacancy", "Rent", "Vacancy", "5% of homes empty"),
    _f("metering", "Rent", "Utility meters", "Individually metered"),
    _f("pets-dog", "Rules", "Dog", "Allowed, up to 2, $300 deposit"),
    _f("aerial-view", "Around it", "The block from above", "Top-down Vexcel aerial photo", "aerial", AERIAL_IMG),
    _f("roof-height", "Building", "Measured roof height", "7.1 m, about 2 floors", "aerial"),
    _f("aerial-trees", "Around it", "Tree cover", "About 18% tree cover on the lot", "aerial"),
    _f("aerial-vehicles", "Around it", "Parked vehicles", "About 40 vehicles parked on site at survey time", "aerial"),
    _f("county-floors", "Records", "Floors (county)", "3", "county"),
    _f("county-homes", "Records", "Homes (county)", "120", "county"),
    _f("county-311", "Records", "311 service requests nearby",
       "42 since 2026-01-01 within 100 m; most often water leak (10), noise (6)", "county"),
    _f("county-parcel", "Records", "County parcel number", "0000000000001", "county"),
]


@pytest.fixture(autouse=True)
def _clear():
    tf._CACHE.clear()
    yield
    tf._CACHE.clear()


def _no(prompt):
    raise RuntimeError("no model")


def test_rule_ranks_what_a_renter_acts_on():
    out = pick_top_facts(FACTS, llm=_no)
    assert [x["label"] for x in out] == ["No elevator", "Homes available", "Utility meters", "311 requests nearby",
                                         "Floors: sources differ"]
    assert all(x["by"] == "rule" for x in out)
    avail = out[1]
    assert avail["value"] == "6 of 120 available to rent; 5% of homes empty" and avail["fact_ids"] == ["available",
                                                                                                         "vacancy"]
    assert out[3]["value"] == "42 since 2026-01-01 within 100 m"  # the count only: no categories
    floors = out[4]
    assert floors["value"] == "CoStar: 3 floors; Vexcel roof height: about 2 floors; County: 3 floors"
    assert floors["sources"] == ["CoStar", "Vexcel", "County"] and floors["image"] == AERIAL_IMG
    for x in out:
        assert set(x) == {"label", "value", "why", "sources", "image", "topic", "by", "fact_ids"}
        assert 0 < len(x["why"]) <= 120 and "\n" not in x["why"]


def test_aerial_items_carry_the_top_down_photo():
    facts = [f for f in FACTS if f["id"] in ("aerial-view", "aerial-trees", "aerial-vehicles", "class")]
    out = by_rule(candidates(facts))
    assert [x["label"] for x in out] == ["Parking seen from above", "Tree cover"]
    assert all(x["image"] == AERIAL_IMG and x["sources"] == ["Vexcel"] for x in out)


def test_never_picks_banned_topics_even_when_the_model_asks():
    def llm(prompt):
        assert "0000000000001" not in prompt and "Class B" not in prompt and "A company" not in prompt
        return json.dumps({"items": [{"fact_ids": ["county-parcel"], "label": "Parcel", "value": "0000000000001"},
                                     {"fact_ids": ["class"], "label": "Building class", "value": "Class B"},
                                     {"fact_ids": ["metering"], "label": "Utilities", "value": "Individually metered",
                                      "why": "Utilities may be billed on top of rent."}]})
    out = pick_top_facts(FACTS, llm=llm)
    ids = {i for x in out for i in x["fact_ids"]}
    assert not ids & {"county-parcel", "class", "rating", "lot", "owner", "county-owner"}
    assert out[0] == {"label": "Utilities", "value": "Individually metered",
                      "why": "Utilities may be billed on top of rent.", "sources": ["CoStar"], "image": None,
                      "topic": "Rent", "by": "model", "fact_ids": ["metering"]}
    # the two rejected items are replaced by rule items on other facts
    assert [x["by"] for x in out] == ["model", "rule", "rule"]


def test_validation_rejects_invented_values_and_unknown_ids():
    def llm(prompt):
        return json.dumps({"items": [
            {"fact_ids": ["floors"], "label": "Floors", "value": "4 floors"},                   # number not in fact
            {"fact_ids": ["nope"], "label": "Gym", "value": "Gym on site"},                      # unknown id
            {"fact_ids": ["elevators"], "label": "No elevator", "value": "A scam: no lift"},     # unkind
            {"fact_ids": ["metering"], "label": "Meters", "value": "Call (555) 010-0000"},       # contact
            {"fact_ids": ["floors", "roof-height"], "label": "Floors",
             "value": "CoStar says 3 floors, Vexcel roof height suggests about 2",
             "why": "Worth asking about the 9 floors."}]})                                       # why number invented
    out = pick_top_facts(FACTS, llm=llm)
    model = [x for x in out if x["by"] == "model"]
    assert len(model) == 1
    assert model[0]["value"] == "CoStar says 3 floors, Vexcel roof height suggests about 2"
    assert model[0]["why"] == "" and model[0]["sources"] == ["CoStar", "Vexcel"] and model[0]["image"] == AERIAL_IMG
    assert len(out) == 5 and all(x["by"] == "rule" for x in out[1:])


def test_nothing_valid_from_the_model_is_the_rule():
    out = pick_top_facts(FACTS, llm=lambda p: json.dumps({"items": [{"fact_ids": ["homes"], "value": "999"}]}))
    assert out == pick_top_facts(FACTS, llm=_no, cache=False)
    assert all(x["by"] == "rule" for x in out)


def test_dedupes_against_claims_and_verdicts():
    claims = [{"id": "c1", "kind": "stories", "quote": "3-story building"},
              {"id": "c2", "kind": "amenity", "quote": "Elevator, utilities included"}]
    verdicts = [{"claim_id": "c3", "kind": "unit_count", "verdict": "confirmed", "claimed": "120 homes"}]
    cands = {f["id"] for f in candidates(FACTS, claims, verdicts)}
    assert not cands & {"floors", "county-floors", "roof-height", "elevators", "metering", "homes", "county-homes"}
    out = pick_top_facts(FACTS, claims=claims, verdicts=verdicts, llm=_no)
    labels = [x["label"] for x in out]
    assert "No elevator" not in labels and "Utility meters" not in labels and "Floors: sources differ" not in labels
    assert labels[:3] == ["Homes available", "311 requests nearby", "Parking seen from above"]


def test_at_most_five_even_when_the_model_sends_more():
    many = [{"fact_ids": [fid], "label": "x", "value": "y"} for fid in
            ("metering", "pets-dog", "elevators", "available", "vacancy", "aerial-trees", "year-built")]
    out = pick_top_facts(FACTS, llm=lambda p: json.dumps({"items": many}))
    assert len(out) == 5 and all(x["by"] == "model" for x in out)
    assert len(pick_top_facts(FACTS, llm=_no, cache=False)) == 5


def test_llm_timeout_falls_back_to_the_rule_in_time():
    def slow(prompt):
        time.sleep(3)
        return json.dumps({"items": [{"fact_ids": ["metering"], "label": "Meters", "value": "Individually metered"}]})
    t = time.monotonic()
    out = pick_top_facts(FACTS, llm=slow, timeout=0.2)
    assert time.monotonic() - t < 1.5
    assert out == pick_top_facts(FACTS, llm=_no, cache=False) and out[0]["by"] == "rule"


def test_cached_per_fact_set():
    calls = []

    def llm(prompt):
        calls.append(1)
        return json.dumps({"items": [{"fact_ids": ["metering"], "label": "Meters", "value": "Individually metered"}]})
    a = pick_top_facts(FACTS, llm=llm)
    b = pick_top_facts(FACTS, llm=llm)
    assert a == b and len(calls) == 1


def test_empty_and_junk_never_raise():
    assert pick_top_facts([], llm=_no) == []
    assert pick_top_facts(None, llm=_no) == []
    assert pick_top_facts([1, "x", {"id": "class", "label": "Building class", "value": "Class A"}], llm=_no) == []
    assert pick_top_facts(FACTS, llm=lambda p: "not json") == pick_top_facts(FACTS, llm=_no, cache=False)


def test_accepts_fill_in_output_as_is():
    match = {"status": "matched", "property_id": 900001, "record": {"stories": 3, "unit_count": 120}}
    kit = {"walkup": True, "building_class": "B", "lot": {"size": 1.2, "unit": "acres"}, "metering": "Individually metered",
           "listing": {"available": 6, "units": 120, "as_of": "2026-10-01"}, "vacancy_percent": 5}
    county = {"source": "county", "status": "ok", "observed": {"stories": 2, "parcel_id": "0000000000001",
                                                                "houston_311_count": 42}}
    facts = build_fill_in(match, [county], [], kit, "418 Linden Grove Ave")
    out = pick_top_facts(facts, llm=_no)
    assert [x["label"] for x in out] == ["No elevator", "Homes available", "Utility meters", "311 requests nearby",
                                         "Floors: sources differ"]
    assert out[4]["value"] == "CoStar: 3 floors; County: 2 floors"


# ---- done.harness.top_facts ---------------------------------------------------------------------------------------

SNAP = {"url": "https://example.test/listing/418", "title": "418 Linden Grove Ave",
        "address_text": "418 Linden Grove Ave, Houston, TX 77002", "text": "2 Beds 1,100 sq ft", "jsonld": []}


def _run(monkeypatch, llm=None):
    import scripted_cli
    from harness.graph import Toolbox, run_check
    from harness.graph import run as run_mod
    scripted_cli.install(monkeypatch)
    if llm is not None:
        monkeypatch.setattr(run_mod, "TOP_FACTS_LLM", llm)
    events = []
    done = run_check(SNAP, events.append, Toolbox.fake())
    facts = next(e["facts"] for e in events if e["type"] == "fill_in")
    return done, facts


def test_done_carries_top_facts_from_the_stubbed_model(monkeypatch):
    def llm(prompt):
        return json.dumps({"items": [{"fact_ids": ["vacancy"], "label": "Vacancy", "value": "x",
                                      "why": "Room to negotiate."}]})
    done, facts = _run(monkeypatch, llm)
    top = done["harness"]["top_facts"]
    assert isinstance(top, list) and 1 <= len(top) <= 5
    assert top[0]["by"] == "model" and top[0]["fact_ids"] == ["vacancy"]
    ids = {f["id"] for f in facts}
    assert all(set(x["fact_ids"]) <= ids for x in top)


def test_top_facts_failure_never_breaks_a_run(monkeypatch):
    from harness import top_facts as mod
    monkeypatch.setattr(mod, "pick_top_facts", lambda *a, **k: 1 / 0)
    done, _ = _run(monkeypatch)
    assert done["type"] == "done" and done["harness"]["top_facts"] == []


def test_slow_model_adds_at_most_the_timeout(monkeypatch):
    from harness.graph import run as run_mod
    monkeypatch.setattr(tf, "TIMEOUT_S", 0.5)

    def slow(prompt):
        time.sleep(5)
        return "{}"
    t = time.monotonic()
    done, facts = _run(monkeypatch, slow)
    assert time.monotonic() - t < 4.5
    top = done["harness"]["top_facts"]
    assert top and all(x["by"] == "rule" for x in top)
    assert run_mod.TOP_FACTS_LLM is slow
