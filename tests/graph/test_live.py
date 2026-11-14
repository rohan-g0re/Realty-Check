"""Live: fake toolbox + the REAL deep agent on the Claude Code CLI model. Runs only with RC_LIVE=1 (uses quota)."""
import json
import os
import time

import pytest

from graph_events import check_run
from harness.graph import Toolbox, run_check
from harness.graph import agent as agent_mod
from harness.graph import run as run_mod

pytestmark = pytest.mark.skipif(os.environ.get("RC_LIVE") != "1", reason="live CLI run; set RC_LIVE=1")

SNAP = {"url": "https://example.test/listing", "title": "Synthetic Lofts",
        "address_text": "100 Example St, Houston, TX 77002", "text": "2 Beds 1,100 sq ft", "jsonld": []}


def test_live_deep_agent_on_fake_toolbox():
    events = []
    t0 = time.perf_counter()
    done = run_check(SNAP, events.append, Toolbox.fake())
    wall = time.perf_counter() - t0
    check_run(events)
    stats = run_mod.LAST_AGENT_STATS
    got = {e["verdict"]["claim_id"]: e["verdict"]["verdict"] for e in events if e["type"] == "verdict"}
    summary = {k: stats.get(k) for k in ("model_calls", "cli_calls", "delegations", "parse_failures",
                                         "budget_hit", "wall_s", "turn_wall_s")}
    summary["run_wall_s"] = round(wall, 2)
    summary["steps"] = [f"{e['agent']}: {e['text']}" for e in events if e["type"] == "step"]
    summary["headline"] = done["headline"]
    print("\nLIVE " + json.dumps(summary, indent=1))
    assert "error" not in stats, stats.get("error")
    assert got["c1"] == "contradicted" and got["c2"] == "contradicted"
    assert got["c3"] == "confirmed" and got["c4"] == "confirmed"  # 968 vs 1,100: usual scan shortfall
    assert stats["delegations"] >= 1
    assert any(t["ns"] for t in stats["trace"])  # messages really came from inside a subagent graph
    assert stats["model_calls"] <= agent_mod.MAX_MODEL_CALLS
    assert wall <= 90
