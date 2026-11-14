"""run_check() without the live model: fixed plan, agent failure, broken tools, and the deep agent driven
by scripted CLI replies (real deepagents graph + real adapter parsing, no CLI process)."""
import re

from graph_events import check_run
from harness.graph import Toolbox, run_check
from harness.graph import agent as agent_mod
from harness.graph import run as run_mod
from harness.model import cli_chat_model as ccm

SNAP = {"url": "https://example.test/listing", "title": "Synthetic Lofts",
        "address_text": "100 Example St, Houston, TX 77002", "text": "2 Beds 1,100 sq ft", "jsonld": []}
EXPECTED = {"c1": "contradicted", "c2": "contradicted", "c3": "confirmed", "c4": "confirmed", "c5": "cant_check"}


def go(tb):
    events = []
    done = run_check(SNAP, events.append, tb)
    assert events[-1] == done
    return events, check_run(events)


def verdicts(events):
    return {e["verdict"]["claim_id"]: e["verdict"]["verdict"] for e in events if e["type"] == "verdict"}


def fail(*a, **k):
    raise RuntimeError("down")


def test_fixed_plan_with_fake_toolbox(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED
    assert done["counts"] == {"confirmed": 2, "contradicted": 2, "close": 0, "cant_check": 1}
    assert done["headline"].startswith("Listing says 2 bedrooms, but the 3D scan shows 1 bedroom + den")
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and "evidence" in types
    assert any(e["type"] == "step" and "standard checks" in e["text"] for e in events)


def test_agent_exception_falls_back_to_fixed_plan(monkeypatch):
    def boom(run):
        raise RuntimeError("CLI exploded")
    monkeypatch.setattr(agent_mod, "run_agent", boom)
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED
    assert any(e["type"] == "error" and "CLI exploded" in e["message"] for e in events)


def test_every_tool_raises_still_done_with_cant_check(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    tb = Toolbox.fake(**{f: fail for f in Toolbox.__dataclass_fields__ if f != "extract_claims"})
    events, done = go(tb)
    assert set(verdicts(events).values()) == {"cant_check"}
    assert done["counts"]["cant_check"] == 5
    assert all(e["evidence"]["status"] != "ok" for e in events if e["type"] == "evidence")


def test_every_tool_raises_including_claims(monkeypatch):
    monkeypatch.setattr(run_mod, "AGENT_ENABLED", False)
    events, done = go(Toolbox.fake(**{f: fail for f in Toolbox.__dataclass_fields__}))
    assert done["counts"] == {"confirmed": 0, "contradicted": 0, "close": 0, "cant_check": 0}
    assert any(e["type"] == "error" for e in events)


def test_real_toolbox_never_crashes_on_import():
    tb = Toolbox.real()
    assert all(callable(getattr(tb, f)) for f in Toolbox.__dataclass_fields__)


def _scripted_cli(seen):
    def fake(self, prompt):
        block = re.search(r"# Tools\n(.*?)\n\n# Transcript", prompt, re.S).group(1)
        tools = re.findall(r'"name": "([^"]+)"', block)
        who = ("inside" if "inside of apartment" in prompt else "outside" if "from the air" in prompt
               else "paper" if "read paper records" in prompt else "orchestrator")
        seen.append((who, tools))
        n = prompt.count("<tool_result")
        if who == "orchestrator":
            if n == 0:
                r = ('Checking scan, aerial and records.\n<tool_calls>[{"name":"task","args":{"subagent_type":"inside",'
                     '"description":"Check Plan E scan."}},{"name":"task","args":{"subagent_type":"outside",'
                     '"description":"Measure floors."}},{"name":"task","args":{"subagent_type":"paper",'
                     '"description":"Read records."}}]</tool_calls>')
            elif n == 3:
                r = 'Floors disagree.\n<tool_calls>[{"name":"look_floors_on_photo","args":{}}]</tool_calls>'
            else:
                r = "Gathered scan, aerial and record evidence."
        else:
            first = {"inside": '[{"name":"scan_rooms","args":{"plan":"Plan E"}}]',
                     "outside": '[{"name":"measure_building","args":{}}]',
                     "paper": '[{"name":"county_lookup","args":{}},{"name":"building_record","args":{}}]'}[who]
            r = f"<tool_calls>{first}</tool_calls>" if n == 0 else "E1 and E2 found."
        return {"result": r, "usage": {}}, 0.0
    return fake


def test_deep_agent_path_with_scripted_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(ccm.ClaudeCLIChatModel, "_run_cli", _scripted_cli(seen))
    events, done = go(Toolbox.fake())
    assert verdicts(events) == EXPECTED
    whos = [w for w, _ in seen]
    assert whos.count("orchestrator") == 3 and {"inside", "outside", "paper"} <= set(whos) and len(seen) == 9
    # deepagents defaults stripped: no file tools, no general-purpose subagent
    orch_tools = next(t for w, t in seen if w == "orchestrator")
    assert sorted(orch_tools) == ["look_at_scan_photos", "look_floors_on_photo", "task"]
    assert all(not ({"ls", "read_file", "write_file", "write_todos"} & set(t)) for _, t in seen)
    steps = [e for e in events if e["type"] == "step"]
    assert any(s["agent"] == "orchestrator" and s["text"].startswith("Asking the inside checker") for s in steps)
    assert any(s["agent"] == "inside" for s in steps) and any(s["agent"] == "paper" for s in steps)
    assert not any("standard checks" in s["text"] for s in steps)  # no fallback needed


def test_model_call_cap_triggers_fallback(monkeypatch):
    seen = []
    monkeypatch.setattr(ccm.ClaudeCLIChatModel, "_run_cli", _scripted_cli(seen))
    real_init = agent_mod.BudgetedCLIModel.__init__

    def small_cap(self, **kw):
        real_init(self, **{**kw, "max_calls": 2})
    monkeypatch.setattr(agent_mod.BudgetedCLIModel, "__init__", small_cap)
    events, done = go(Toolbox.fake())
    assert len(seen) == 2
    assert verdicts(events) == EXPECTED
    assert any(e["type"] == "step" and "turn budget" in e["text"] for e in events)
