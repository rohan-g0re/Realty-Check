"""run_check() without the live model. The deep agent runs for real (deepagents graph + adapter parsing) on
scripted CLI replies (scripted_cli.py); failures, timeouts and the RC_LEGACY switch end cleanly. Invented data."""
import threading
import time

import scripted_cli
from graph_events import check_run
from harness.graph import Toolbox, run_check
from harness.graph import agent as agent_mod
from harness.graph import run as run_mod
from harness.graph.run import helpers_needed

SNAP = {"url": "https://example.test/listing", "title": "Synthetic Lofts",
        "address_text": "100 Example St, Houston, TX 77002", "text": "2 Beds 1,100 sq ft", "jsonld": []}
EXPECTED = {"c1": "contradicted", "c2": "contradicted", "c3": "confirmed", "c4": "confirmed", "c5": "cant_check"}
ALL_CANT = {k: "cant_check" for k in EXPECTED}


def go(tb):
    events = []
    done = run_check(SNAP, events.append, tb)
    assert events[-1] == done
    return events, check_run(events)


def verdicts(events):
    return {e["verdict"]["claim_id"]: e["verdict"]["verdict"] for e in events if e["type"] == "verdict"}


def steps(events):
    return [e for e in events if e["type"] == "step"]


def fail(*a, **k):
    raise RuntimeError("down")


def fake_with_rules_claims(**over):
    """Fake toolbox whose claim reader also answers use_model=False (the close's model-free reading)."""
    fake = Toolbox.fake()
    return Toolbox.fake(extract_claims=lambda s, use_model=True: fake.extract_claims(s), **over)


def test_whole_check_runs_inside_the_agent(monkeypatch):
    seen = scripted_cli.install(monkeypatch)
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED
    whos = [w for w, _ in seen]
    assert whos.count("orchestrator") == 3 and sorted(w for w in whos if w != "orchestrator") == ["inside", "outside", "paper"]
    orch_tools = next(t for w, t in seen if w == "orchestrator")
    assert sorted(orch_tools) == ["fill_in", "judge_claims", "look_at_scan_photos", "match_listing", "read_claims", "task"]
    assert all(not ({"ls", "read_file", "write_file", "write_todos"} & set(t)) for _, t in seen)
    s = steps(events)
    assert any(x["agent"] == "orchestrator" and x["text"].startswith("Asking the inside checker") for x in s)
    assert {"claims", "matcher", "inside", "outside", "paper", "judge"} <= {x["agent"] for x in s}
    assert not any("standard checks" in x["text"] for x in s)
    h = done["harness"]
    assert h["path"] == "agent" and h["judged_by"] == "agent" and h["fill_in_by"] == "agent" and h["stopped"] is None
    assert h["helpers"] == ["inside", "outside", "paper"] == h["needed"]
    t = done["timing"]
    assert t["total_s"] >= 0 and {"agent", "claims", "match", "inside", "outside", "paper", "judge", "fill_in"} <= set(t["phases"])
    assert t["model_calls"] == 6 and set(t["model_s"]) == {"orchestrator", "inside", "outside", "paper"}


def test_judge_refuses_once_until_every_needed_helper_was_asked(monkeypatch):
    seen = scripted_cli.install(monkeypatch, skip={"paper"})
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED  # county year built and stories only arrive after the second delegation
    assert [w for w, _ in seen].count("paper") == 1
    assert done["harness"]["helpers"] == ["inside", "outside", "paper"]


def test_helpers_needed_lists_every_source_for_the_claim_kinds():
    cl = [{"id": "c1", "kind": "stories", "value": 5, "checkable": True},
          {"id": "c2", "kind": "amenity", "value": "Resort-style pool", "checkable": True},
          {"id": "c3", "kind": "laundry", "value": "In-unit washer and dryer", "checkable": True}]
    need = helpers_needed(cl)
    assert set(need) == {"inside", "outside", "paper"}
    assert "roof height" in need["outside"] and "outdoor amenities" in need["outside"]
    assert helpers_needed([{"id": "c1", "kind": "year_built", "value": 2016, "checkable": True}]).keys() == {"paper"}
    assert helpers_needed([{"id": "c1", "kind": "bedrooms", "value": 2, "checkable": False}]) == {}


def test_agent_exception_ends_with_honest_cant_check(monkeypatch):
    def boom(run, hard_s=None):
        raise RuntimeError("CLI exploded")
    monkeypatch.setattr(agent_mod, "run_agent", boom)
    events, done = go(fake_with_rules_claims())
    assert verdicts(events) == ALL_CANT  # nothing was gathered, nothing is invented
    assert any(e["type"] == "error" and "CLI exploded" in e["message"] for e in events)
    assert done["harness"]["judged_by"] == "close" and done["harness"]["stopped"] == "error"
    assert not any(e["type"] == "evidence" for e in events)
    assert next(e for e in events if e["type"] == "match")["match"]["status"] == "no_match"


def test_agent_timeout_never_hangs_and_nothing_follows_done(monkeypatch):
    monkeypatch.setattr(run_mod, "RUN_BUDGET_S", 3.0)
    monkeypatch.setattr(run_mod, "CLOSE_RESERVE_S", 0.5)
    seen = scripted_cli.install(monkeypatch, delay={"orchestrator1": 4.0})  # turn 2 hangs past the hard limit
    events = []
    t = time.perf_counter()
    done = run_check(SNAP, events.append, Toolbox.fake())
    assert time.perf_counter() - t < 4.0
    time.sleep(4.5)  # let the abandoned agent thread wake up and try to carry on
    assert events[-1] == done and events.count(done) == 1
    check_run(events)
    assert done["harness"]["stopped"] == "timed out" and done["harness"]["judged_by"] == "close"
    assert verdicts(events) == ALL_CANT
    assert [e["type"] for e in events].index("claims") < [e["type"] for e in events].index("match")


def test_model_call_cap_stops_and_judges_what_exists(monkeypatch):
    seen = scripted_cli.install(monkeypatch)
    real_init = agent_mod.BudgetedCLIModel.__init__

    def small_cap(self, **kw):
        real_init(self, **{**kw, "max_calls": 2})
    monkeypatch.setattr(agent_mod.BudgetedCLIModel, "__init__", small_cap)
    events, done = go(Toolbox.fake())
    assert len(seen) == 2
    assert verdicts(events) == ALL_CANT
    assert done["harness"]["stopped"] == "model-call cap 2"
    assert any(x["agent"] == "orchestrator" and "stopped before judging" in x["text"] for x in steps(events))


def test_every_tool_raises_still_done_with_cant_check(monkeypatch):
    scripted_cli.install(monkeypatch)
    tb = Toolbox.fake(**{f: fail for f in Toolbox.__dataclass_fields__ if f != "extract_claims"})
    events, done = go(tb)
    assert set(verdicts(events).values()) == {"cant_check"}
    assert done["counts"]["cant_check"] == 5
    assert all(e["evidence"]["status"] != "ok" for e in events if e["type"] == "evidence")


def test_every_tool_raises_including_claims(monkeypatch):
    scripted_cli.install(monkeypatch)
    events, done = go(Toolbox.fake(**{f: fail for f in Toolbox.__dataclass_fields__}))
    assert done["counts"] == {"confirmed": 0, "contradicted": 0, "close": 0, "cant_check": 0}
    assert any(e["type"] == "error" for e in events)


def test_agent_switched_off_reads_nothing_and_checks_nothing(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    events, done = go(fake_with_rules_claims())
    assert verdicts(events) == ALL_CANT and done["harness"]["stopped"] == "agent switched off"


def test_legacy_switch_runs_the_old_path(monkeypatch):
    monkeypatch.setenv("RC_LEGACY", "1")
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED
    assert done["headline"].startswith("Listing says 2 bedrooms, but the 3D scan shows 1 bedroom + den")
    assert any(x["text"].startswith("The planner is switched off, so we run the standard checks") for x in steps(events))
    assert done["harness"]["path"] == "legacy" and "standard_checks" in done["timing"]["phases"]


def test_match_called_first_still_sends_claims_first():
    run = run_mod.Run(SNAP, (events := []).append, Toolbox.fake())
    ths = [threading.Thread(target=run.match_listing), threading.Thread(target=run.read_claims)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    types = [e["type"] for e in events]
    assert types.index("claims") < types.index("match")
    run.check_floor_plans()
    assert all(i > types.index("match") for i, e in enumerate(events) if e["type"] == "evidence")


def test_late_evidence_after_judging_is_not_sent(monkeypatch):
    run = run_mod.Run(SNAP, (events := []).append, Toolbox.fake())
    run.read_claims()
    run.match_listing()
    run.judge_all(by="agent")
    n = len(events)
    run.tool("paper", run.county_lookup)
    assert len(events) == n + 1 and events[-1]["type"] == "step"  # the step, but no evidence after the verdicts
