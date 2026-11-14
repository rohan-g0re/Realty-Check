"""LEGACY run path (RC_LEGACY=1): the earlier flow, kept so a run can switch back in one step.

Claims and the address match run as plain code before the agent; picture checks start in a background
thread; the old deep agent (legacy_agent.py, 35 s) gathers evidence; then a fixed plan runs every relevant
check once as a completion pass, proof stills are fetched, and the judge and "Fill me in" close the run.
The default path is harness/graph/agent.py (everything inside deepagents).
"""
from __future__ import annotations

import traceback

from . import run as run_mod

RUN_BUDGET_S = 60.0


def run_check_legacy(run: "run_mod.Run") -> dict:
    """Run the legacy flow on an already started Run (run_started emitted). Returns the done event."""
    run.budget_s = RUN_BUDGET_S
    run.path = "legacy"
    with run.phase("claims"):
        try:
            run.step("claims", "Reading what the listing claims.")
            claims = run.tb.extract_claims(run.snapshot) or []
            run.claims = [c for c in claims if isinstance(c, dict) and c.get("id")]
        except Exception as e:
            run.error(f"reading claims failed: {type(e).__name__}: {e}")
    run.emit({"type": "claims", "claims": run.claims})
    run.claims_ready = run.claims_sent = True
    with run.phase("match"):
        try:
            run.step("matcher", f"Finding the building at {run.address or 'this address'}.")
            run.do_match()
        except Exception as e:
            run.error(f"address match failed: {type(e).__name__}: {e}")
    run.emit({"type": "match", "match": run.match})
    run.match_ready = run.match_sent = True
    try:
        run.start_pictures()  # amenity / in-unit picture checks run alongside the agent
    except Exception as e:
        run.error(f"picture checks failed to start: {type(e).__name__}: {e}")

    if any(c.get("checkable") for c in run.claims):
        reason = None
        with run.phase("agent"):
            if run_mod.AGENT_ENABLED:
                try:
                    from .legacy_agent import run_agent
                    run.agent_stats = run_agent(run)
                    if run.agent_stats.get("budget_hit"):
                        reason = "The planner ran out of its turn budget"
                    elif not run.evidence:
                        reason = "The planner gathered no evidence"
                except Exception as e:
                    run.error(f"agent failed: {type(e).__name__}: {str(e)[:300]}")
                    run.agent_stats = {"error": traceback.format_exc()[-1500:]}
                    reason = "The planner hit an error"
            else:
                reason = "The planner is switched off"
        if reason:
            run.step("orchestrator", f"{reason}, so we run the standard checks for each claim.")
        with run.phase("standard_checks"):
            try:
                run.fixed_plan()  # completion pass: every claim gets its checks; ones already done are not repeated
            except Exception as e:
                run.error(f"standard checks failed: {type(e).__name__}: {e}")
            try:
                run.proof_images()
            except Exception as e:
                run.error(f"proof images failed: {type(e).__name__}: {e}")
    else:
        run.step("orchestrator", "Nothing on this page can be measured, so no checks are needed.")

    with run.phase("pictures_wait"):
        try:
            run.collect_pictures()
        except Exception as e:
            run.error(f"picture checks failed: {type(e).__name__}: {e}")
    return run.close(by="legacy")


