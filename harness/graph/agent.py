"""The deepagents part of a run: an orchestrator that plans the checks and delegates them to three
subagents (inside, outside, paper) through deepagents' `task` tool, then may order one follow-up look.

Model: ClaudeCLIChatModel (Claude Code CLI, headless Sonnet, no API key), wrapped with a hard per-run
model-call and time budget. Tools are coarse and return short text; full Evidence goes to the run store.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from harness.model import cli_chat_model as ccm

PROVIDER = "reality_check_cli"
MAX_MODEL_CALLS = 12      # hard cap across orchestrator + subagents
ORCH_MAX_CALLS = 4        # orchestrator's own turns (langchain ModelCallLimitMiddleware)
AGENT_BUDGET_S = 35.0     # after this, every model call returns a stop message
STOP_TEXT = "Stopped: the turn budget for this check is used up. Report what you have."

# Strip what deepagents adds by default and we do not need: file tools and the general-purpose subagent.
register_harness_profile(PROVIDER, HarnessProfile(
    excluded_tools=frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "delete",
                              "write_todos"}),
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
))


class BudgetedCLIModel(ccm.ClaudeCLIChatModel):
    """ClaudeCLIChatModel with a per-run call cap and deadline; past either it answers STOP_TEXT without a CLI call."""

    max_calls: int = MAX_MODEL_CALLS
    budget_s: float = AGENT_BUDGET_S
    _calls: int = PrivateAttr(default=0)
    _t0: float = PrivateAttr(default_factory=time.monotonic)
    _hit: str | None = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)

    def _get_ls_params(self, stop=None, **kwargs):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = PROVIDER  # selects the harness profile registered above
        return params

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        with self._lock:
            over = True
            if self._calls >= self.max_calls:
                self._hit = self._hit or f"model-call cap {self.max_calls}"
            elif time.monotonic() - self._t0 > self.budget_s:
                self._hit = self._hit or f"time budget {self.budget_s:.0f} s"
            else:
                self._calls += 1
                over = False
        if over:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=STOP_TEXT))])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def budget_hit(self) -> str | None:
        return self._hit


ORCHESTRATOR_PROMPT = """You are the lead fact-checker of Reality Check. A rental listing makes claims about an apartment and its building. You decide which checks are worth running for THESE claims, delegate them, and stop. You never judge claims yourself: a separate judge compares the stored evidence afterwards.

Helpers (call them with the task tool):
- inside: checks floor plans (bedrooms, bathrooms, area): matches each listing plan to the building's plans by name or size, then reads its 3D scan or the building record. Give it ALL floor plan names in ONE task.
- outside: measures the building from aerial imagery (roof height -> floors). Only useful for building stories.
- paper: county records and the building record (stories, year built, unit count).
Amenity, parking, laundry and finish claims (pool, rooftop, garage, washer/dryer, counters...) are checked on photos automatically in the background; do not delegate them.

Rules:
1. Skip claims with checkable=false. If no stories, year, unit-count or floor plan claim is checkable, answer at once without tools.
2. Send ALL independent tasks in ONE message as parallel task calls, at most one task per helper. Each task description: one sentence naming exactly what to check (for inside: every floor plan name) and asking for evidence ids and numbers.
3. When the reports are back, usually stop. Only if the scan gave no room counts for a room claim, make ONE follow-up look_at_scan_photos call. Floors are decided by the measured roof height and the county record, never by a photo; call look_floors_on_photo only if there is no roof height at all, and only as an illustration. A building record that disagrees with measured sources is expected and needs no follow-up.
4. Every message that calls tools starts with one short plain sentence (at most 20 words) saying what you check and why.
5. Final answer: one plain sentence on what was gathered. Hard budget: at most 3 of your own turns."""

SUB_RULES = ("Never guess numbers. Make your tool calls in one message where you can. "
             "Then reply in at most 3 short lines: the evidence ids and the numbers they found.")

INSIDE_PROMPT = ("You check the inside of apartment floor plans. In ONE message, call scan_rooms once for every floor "
                 "plan name in your task, plus unit_feature_photos once. Only if a plan has a scan but no "
                 "bedroom/bathroom counts and the task asks about rooms, call scan_photos once. " + SUB_RULES)
OUTSIDE_PROMPT = ("You measure the building from the air. In ONE message, call measure_building and "
                  "outdoor_feature_photos. Only if there is no roof height, call count_floors_on_photo once "
                  "(illustration only). " + SUB_RULES)
PAPER_PROMPT = "You read paper records. Call county_lookup and building_record together in one message. " + SUB_RULES


def build_agent(run, model: BudgetedCLIModel):
    """Tools are closures over the run; each returns short text and stores full Evidence in run.evidence."""

    def scan_rooms(plan: str) -> str:
        """Check one listing floor plan: match it to the building's plans (by name, else by beds, baths and size),
        then read its 3D scan (room labels, bedroom/bathroom counts, floor area) and the building record's numbers."""
        return run.scan_rooms(plan, agent="inside")

    def unit_feature_photos() -> str:
        """Results so far of the background photo check for in-unit features (washer/dryer, counters, floors...)."""
        return run.feature_status("unit")

    def outdoor_feature_photos() -> str:
        """Results so far of the background aerial-photo check for outdoor amenities (pool, rooftop, parking...)."""
        return run.feature_status("aerial")

    def scan_photos(plan: str, claim: str = "") -> str:
        """Have the vision model look at still photos from a floor plan's 3D scan. Without claim: count bedrooms,
        bathrooms and a den. With claim (listing text about one room): check whether the photos support it."""
        return run.scan_photos(plan, claim, agent="inside")

    def measure_building() -> str:
        """Measure the roof height above ground from aerial elevation data, estimate floors, and fetch aerial photos."""
        return run.measure_building(agent="outside")

    def count_floors_on_photo() -> str:
        """Have the vision model count the floors of the building on the angled aerial photo."""
        return run.count_floors_on_photo(agent="outside")

    def county_lookup() -> str:
        """Look up the county property record for the address: stories, year built, unit count, building size."""
        return run.county_lookup(agent="paper")

    def building_record() -> str:
        """Read the building record matched to the address: stories, year built, unit count."""
        return run.building_record(agent="paper")

    def look_floors_on_photo() -> str:
        """Follow-up: count floors on the angled aerial photo with the vision model (tie-breaker for stories)."""
        return run.count_floors_on_photo(agent="orchestrator")

    def look_at_scan_photos(plan: str, claim: str = "") -> str:
        """Follow-up: look at the 3D scan's photos of a floor plan with the vision model (when the scan had no room
        counts, or to check one room claim)."""
        return run.scan_photos(plan, claim, agent="orchestrator")

    subagents = [
        {"name": "inside", "description": "Checks bedroom, bathroom and floor-area claims of the listing's floor "
         "plans against the building's plans, 3D scans and record. Give it every floor plan name in one task.", "system_prompt": INSIDE_PROMPT,
         "tools": [scan_rooms, scan_photos, unit_feature_photos], "model": model},
        {"name": "outside", "description": "Measures the building's height and number of floors from aerial imagery.",
         "system_prompt": OUTSIDE_PROMPT, "tools": [measure_building, outdoor_feature_photos, count_floors_on_photo],
         "model": model},
        {"name": "paper", "description": "Reads county records and the building record: stories, year built, "
         "unit count.", "system_prompt": PAPER_PROMPT, "tools": [county_lookup, building_record], "model": model},
    ]
    return create_deep_agent(
        model=model,
        tools=[look_floors_on_photo, look_at_scan_photos],
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=subagents,
        middleware=[ModelCallLimitMiddleware(run_limit=ORCH_MAX_CALLS, exit_behavior="end")],
        name="orchestrator",
    )


def task_message(run) -> str:
    claims = [{k: c.get(k) for k in ("id", "kind", "value", "scope", "label", "quote", "checkable")}
              for c in run.claims]
    m = run.match
    match = {"status": m.get("status"), "name": m.get("name"), "address": m.get("address"),
             "has_location": run.lat is not None,
             "record": m.get("record"),
             "floorplans": [{"name": fp.get("name"), "has_scan": bool(fp.get("matterport_model_id"))}
                            for fp in (m.get("floorplans") or [])[:30]],
             "listing_plans": sorted({c.get("label") for c in run.claims if c.get("label")})}
    return ("Listing claims:\n" + json.dumps(claims) + "\n\nAddress match:\n" + json.dumps(match)
            + "\n\nPlan and run the checks worth doing for these claims.")


_TEAM = {"inside": "the inside checker", "outside": "the outside checker", "paper": "the records checker"}


def run_agent(run) -> dict:
    """Run the deep agent for one check. Returns stats: model_calls, delegations, budget_hit, wall_s, trace."""
    model = BudgetedCLIModel(model="sonnet", effort="low")
    agent = build_agent(run, model)
    log_start = len(ccm.CALL_LOG)
    trace: list[dict] = []
    t0 = time.perf_counter()
    for ns, update in agent.stream({"messages": [HumanMessage(task_message(run))]},
                                   stream_mode="updates", subgraphs=True):
        for node, val in (update or {}).items():
            msgs = val.get("messages", []) if isinstance(val, dict) else []
            for msg in msgs or []:
                if not hasattr(msg, "type"):
                    continue
                calls = [{"name": c["name"], "args": c["args"]} for c in getattr(msg, "tool_calls", None) or []]
                trace.append({"ns": list(ns), "node": node, "type": msg.type, "tool_calls": calls,
                              "content": ccm._text(msg.content)[:400]})
                if ns or msg.type != "ai":
                    continue
                text = ccm._text(msg.content).strip()
                if text and text != STOP_TEXT and "<tool_calls>" not in text:
                    first = re.split(r"(?<=[.!?])\s", text.splitlines()[0], maxsplit=1)[0]
                    run.step("orchestrator", first[:240])
                for c in calls:
                    if c["name"] == "task":
                        who = c["args"].get("subagent_type", "")
                        what = str(c["args"].get("description", "")).strip().split("\n")[0]
                        what = re.split(r"[:;.]\s", what, maxsplit=1)[0][:110]  # the task, not its instructions
                        run.step("orchestrator", f"Asking {_TEAM.get(who, who)}: {what}")
    log = ccm.CALL_LOG[log_start:]
    return {
        "model_calls": model.calls, "cli_calls": len(log), "budget_hit": model.budget_hit,
        "delegations": sum(1 for t in trace if not t["ns"] for c in t["tool_calls"] if c["name"] == "task"),
        "parse_failures": sum(1 for x in log if x.get("parse_error")),
        "wall_s": round(time.perf_counter() - t0, 2),
        "turn_wall_s": [x.get("wall_s") for x in log], "trace": trace,
    }
