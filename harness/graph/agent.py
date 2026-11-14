"""The whole check inside deepagents: one orchestrator, three subagents, all on the Claude Code CLI chat model.

Orchestrator (3 turns when all goes well):
  1. read_claims + match_listing (its own tools; the claim reading is one call through the same chat model)
  2. task -> inside / outside / paper, in parallel, every helper the claim kinds need
  3. judge_claims (fixed rules, no model) + fill_in; the run ends right after that tool round.
Subagents make one round of tool calls and end; their report is built from the tool results, with no extra
model turn. Photo checks (vision) are subagent tools and go through the same chat model (image path).

Model: BudgetedCLIModel (Claude Code CLI, headless Sonnet, no API key) with a per-run call cap and deadline;
past either it answers STOP_TEXT without a CLI call, and a CLI process is never allowed to outlive the
deadline by more than a few seconds. run_agent() runs the graph in a worker thread and gives up on it at a
hard limit, so a run never hangs; the caller then judges whatever evidence exists.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from harness.model import ask
from harness.model import cli_chat_model as ccm

PROVIDER = "reality_check_cli"
MAX_MODEL_CALLS = 14      # text calls across orchestrator, subagents and the claim reading (photo checks not counted)
ORCH_MAX_CALLS = 5        # orchestrator's own turns: 3 planned + one refused judge + one follow-up
AGENT_BUDGET_S = 75.0     # after this, every model call returns a stop message
CALL_GRACE_S = 8.0        # a CLI process started before the deadline may run this far past it
STOP_TEXT = "Stopped: the time budget for this check is used up. Report what you have."

# Strip what deepagents adds by default and we do not need: file tools, todos, the general-purpose subagent.
register_harness_profile(PROVIDER, HarnessProfile(
    excluded_tools=frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "delete",
                              "write_todos"}),
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
))


class BudgetedCLIModel(ccm.ClaudeCLIChatModel):
    """ClaudeCLIChatModel with a per-run text-call cap and a deadline; past either it answers STOP_TEXT
    without a CLI call. Photo (image) calls count against the deadline only."""

    max_calls: int = MAX_MODEL_CALLS
    budget_s: float = AGENT_BUDGET_S
    _calls: int = PrivateAttr(default=0)
    _t0: float = PrivateAttr(default_factory=time.monotonic)
    _hit: str | None = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)
    _log: list = PrivateAttr(default_factory=list)

    def _get_ls_params(self, stop=None, **kwargs):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = PROVIDER  # selects the harness profile registered above
        return params

    def left(self) -> float:
        return self.budget_s - (time.monotonic() - self._t0)

    def stop(self, why: str) -> None:
        with self._lock:
            self._hit = self._hit or why
            self.budget_s = min(self.budget_s, time.monotonic() - self._t0)

    def call_timeout(self) -> float:
        return max(1.0, min(float(self.timeout_s), self.left() + CALL_GRACE_S))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        images = ccm.has_images(messages)
        with self._lock:
            over = True
            if self.left() <= 0:
                self._hit = self._hit or f"time budget {self.budget_s:.0f} s"
            elif not images and self._calls >= self.max_calls:
                self._hit = self._hit or f"model-call cap {self.max_calls}"
            else:
                self._calls += 0 if images else 1
                over = False
        if over:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=STOP_TEXT))])
        n = len(ccm.CALL_LOG)
        try:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        finally:
            with self._lock:  # this call's log entries (other threads may append in between: keep only ours)
                tid = threading.get_ident()
                self._log.extend(x for x in ccm.CALL_LOG[n:] if x.get("_tid", tid) == tid)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def budget_hit(self) -> str | None:
        return self._hit

    @property
    def log(self) -> list[dict]:
        return list(self._log)


class EndAfterTools(AgentMiddleware):
    """Ends an agent after its tool round without another model turn, reporting the tool results as its
    final message. Subagents: after `rounds` tool rounds. Orchestrator: once `finished()` is true."""

    def __init__(self, rounds: int | None = None, finished=None):
        super().__init__()
        self.rounds, self.finished = rounds, finished

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime) -> dict[str, Any] | None:
        msgs = state.get("messages") or []
        if not msgs or not isinstance(msgs[-1], ToolMessage):
            return None
        start = max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)), default=-1)
        rounds = sum(1 for m in msgs[start + 1:] if isinstance(m, AIMessage) and m.tool_calls)
        done = (self.finished is not None and self.finished()) or (self.rounds is not None and rounds >= self.rounds)
        if not done:
            return None
        last_ai = max(i for i, m in enumerate(msgs) if isinstance(m, AIMessage))
        report = "\n".join(ccm._text(m.content) for m in msgs[last_ai + 1:] if isinstance(m, ToolMessage))
        return {"messages": [AIMessage(content=report.strip() or "Done.")], "jump_to": "end"}


ORCHESTRATOR_PROMPT = """You are the lead fact-checker of Realty Check. A rental listing page makes claims about an apartment and its building. You run the whole check with your tools and helpers in three turns. You never judge claims yourself: judge_claims applies fixed rules to the evidence the helpers store.

Turn 1: call read_claims and match_listing together in one message.
Turn 2: delegate with the task tool, ALL tasks in ONE message as parallel calls, one task per helper. Ask EVERY helper listed under "Helpers needed" in the read_claims result, never just one when several are listed. Each task description is one sentence naming what to check (claim ids and plan names). If no helper is needed, skip to turn 3.
Turn 3: call judge_claims and fill_in together in one message. That ends the check.

Optional, only between turns 2 and 3: if a floor plan's scan gave no bedroom/bathroom counts for a room claim, call look_at_scan_photos for that plan once. If judge_claims answers "Not judged yet", do what it says.
Every message that calls tools starts with one short plain sentence (at most 20 words) saying what you check and why."""

SUB_RULES = "Never guess numbers. Make all your tool calls in ONE message; their results are your report."
INSIDE_PROMPT = ("You check the inside of the apartments. Call check_floor_plans (with no plans it checks every "
                 "floor plan the listing names) and unit_feature_photos. " + SUB_RULES)
OUTSIDE_PROMPT = ("You measure the building from the air. Call measure_building and outdoor_feature_photos. "
                  + SUB_RULES)
PAPER_PROMPT = "You read paper records. Call county_lookup and building_record. " + SUB_RULES


def build_agent(run, model: BudgetedCLIModel):
    """Tools are closures over the run; each returns short text and stores full Evidence in run.evidence."""

    # orchestrator's own tools
    def read_claims() -> str:
        """Turn 1. Read the listing page: its claims (id, kind, value, floor plan) and which helpers they need."""
        token = ask.ROLE.set("claims")
        try:
            return run.read_claims()
        finally:
            ask.ROLE.reset(token)

    def match_listing() -> str:
        """Turn 1. Find the building record for the listing's address: floor plans, 3D scans, map location."""
        return run.match_listing()

    def judge_claims() -> str:
        """Turn 3. Compare every claim with the stored evidence by fixed rules and send the verdicts."""
        return run.judge_claims()

    def fill_in() -> str:
        """Turn 3, with judge_claims. Send what our sources know about the place that the listing does not say."""
        return run.fill_in_tool()

    def look_at_scan_photos(plan: str, claim: str = "") -> str:
        """Optional follow-up: read bedrooms/bathrooms off a floor plan's 3D scan photos (when its scan gave no
        room counts), or with claim (listing text about one room) check whether the photos support it."""
        token = ask.ROLE.set("vision")
        try:
            return run.tool("inside", run.scan_photos, plan, claim)
        finally:
            ask.ROLE.reset(token)

    # inside
    def check_floor_plans(plans: list[str] | None = None) -> str:
        """Check the listing's floor plans: match each to the building's plans (by name, else beds, baths and
        size), read its 3D scan or the building record, and keep scan photos as proof. No plans = all of them."""
        token = ask.ROLE.set("vision")
        try:
            return run.check_floor_plans(plans, agent="inside")
        finally:
            ask.ROLE.reset(token)

    def unit_feature_photos() -> str:
        """Look for the claimed in-unit features (washer/dryer, counters, floors...) in 3D scan photos, and for
        amenity rooms the building has scanned (fitness center, lounge...)."""
        token = ask.ROLE.set("vision")
        try:
            return run.features("unit", agent="inside")
        finally:
            ask.ROLE.reset(token)

    # outside
    def measure_building() -> str:
        """Measure the roof height above ground from aerial elevation data, estimate floors, fetch aerial photos."""
        return run.tool("outside", run.measure_building)

    def outdoor_feature_photos() -> str:
        """Look for the claimed outdoor amenities (pool, rooftop deck, parking...) on the aerial photos."""
        token = ask.ROLE.set("vision")
        try:
            return run.features("aerial", agent="outside")
        finally:
            ask.ROLE.reset(token)

    # paper
    def county_lookup() -> str:
        """Look up the county property record for the address: stories, year built, unit count, building size."""
        return run.tool("paper", run.county_lookup)

    def building_record() -> str:
        """Read the building record matched to the address: stories, year built, unit count."""
        return run.tool("paper", run.building_record)

    one_round = [EndAfterTools(rounds=1)]
    subagents = [
        {"name": "inside", "description": "Floor plan claims (bedrooms, bathrooms, area) and in-unit features: "
         "the building's plans, 3D scans, scan photos and record.", "system_prompt": INSIDE_PROMPT,
         "tools": [check_floor_plans, unit_feature_photos], "model": model, "middleware": one_round},
        {"name": "outside", "description": "Aerial imagery: roof height (floors) and outdoor amenities.",
         "system_prompt": OUTSIDE_PROMPT, "tools": [measure_building, outdoor_feature_photos], "model": model,
         "middleware": [EndAfterTools(rounds=1)]},
        {"name": "paper", "description": "County records and the building record: stories, year built, unit count.",
         "system_prompt": PAPER_PROMPT, "tools": [county_lookup, building_record], "model": model,
         "middleware": [EndAfterTools(rounds=1)]},
    ]
    return create_deep_agent(
        model=model,
        tools=[read_claims, match_listing, judge_claims, fill_in, look_at_scan_photos],
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=subagents,
        middleware=[ModelCallLimitMiddleware(run_limit=ORCH_MAX_CALLS, exit_behavior="end"),
                    EndAfterTools(finished=lambda: run.verdicts is not None)],
        name="orchestrator",
    )


def task_message(run) -> str:
    snap = run.snapshot
    return (f"Check this rental listing.\nAddress: {run.address or '(none given)'}\n"
            f"Page title: {str(snap.get('title') or '')[:120]}\nStart with turn 1.")


_TEAM = {"inside": "the inside checker", "outside": "the outside checker", "paper": "the records checker"}


def _narrate(run, ns, update, trace: list[dict]) -> None:
    for node, val in (update or {}).items():
        msgs = val.get("messages", []) if isinstance(val, dict) else []
        for msg in msgs or []:
            if not hasattr(msg, "type"):
                continue
            calls = [{"name": c["name"], "args": c["args"]} for c in getattr(msg, "tool_calls", None) or []]
            trace.append({"ns": list(ns), "node": node, "type": msg.type, "tool_calls": calls,
                          "content": ccm._text(msg.content)[:400]})
            if ns or msg.type != "ai" or not calls:
                continue  # narrate the orchestrator's own planning turns only
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


def run_agent(run, hard_s: float | None = None) -> dict:
    """Run the deep agent for one check, in a worker thread given up on after hard_s seconds (from the run's
    start). Returns stats: model_calls, delegations, budget_hit, stopped, wall_s, calls (per CLI call), trace."""
    model = BudgetedCLIModel(model="sonnet", effort="low", budget_s=AGENT_BUDGET_S)
    run.budget_s = AGENT_BUDGET_S  # tools skip photo checks when less than VISION_MIN_LEFT_S of this is left
    agent = build_agent(run, model)
    trace: list[dict] = []
    box: dict[str, Any] = {}
    t0 = time.perf_counter()

    def work():
        token = ask.CURRENT_MODEL.set(model)  # claim reading and photo checks go through this model
        try:
            for ns, update in agent.stream({"messages": [HumanMessage(task_message(run))]},
                                           stream_mode="updates", subgraphs=True,
                                           config={"recursion_limit": 40}):
                _narrate(run, ns, update, trace)
        except BaseException as e:  # reported by the caller; the run still closes
            box["error"] = e
        finally:
            ask.CURRENT_MODEL.reset(token)

    th = threading.Thread(target=work, daemon=True, name=f"rc-agent-{run.run_id}")
    th.start()
    limit = (hard_s if hard_s is not None else AGENT_BUDGET_S + 2 * CALL_GRACE_S) - (time.monotonic() - run.t0)
    th.join(timeout=max(1.0, limit))
    stopped = None
    if th.is_alive():
        model.stop("hard time limit")  # no new model calls; the thread is abandoned, the run is sealed by the close
        stopped = "timed out"
    elif "error" in box:
        stopped = "error"
    elif model.budget_hit:
        stopped = model.budget_hit
    elif run.verdicts is None:
        stopped = "ended before judging"
    log = model.log
    stats = {
        "model_calls": model.calls, "cli_calls": len(log), "budget_hit": model.budget_hit, "stopped": stopped,
        "delegations": sum(1 for t in trace if not t["ns"] for c in t["tool_calls"] if c["name"] == "task"),
        "orchestrator_turns": sum(1 for t in trace if not t["ns"] and t["type"] == "ai"),
        "parse_failures": sum(1 for x in log if x.get("parse_error")),
        "wall_s": round(time.perf_counter() - t0, 2),
        "turn_wall_s": [x.get("wall_s") for x in log],
        "calls": [{k: x.get(k) for k in ("role", "wall_s", "images", "prompt_chars", "output_tokens", "tool_calls")}
                  for x in log],
        "trace": trace,
    }
    if "error" in box:
        e = box["error"]
        stats["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        run.error(f"The check's planner stopped: {stats['error']}")
    return stats
