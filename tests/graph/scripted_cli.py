"""Scripted stand-in for the Claude Code CLI: plays the orchestrator and the three helpers by the book, so the
REAL deepagents graph and the real adapter parsing run without a CLI process. Invented data only.

install(monkeypatch, **opts) patches ClaudeCLIChatModel._run_cli and returns the list of (role, tools) seen.
opts: skip = helpers the orchestrator leaves out of its first delegation; delay = {role_turn: seconds}.
"""
from __future__ import annotations

import json
import re
import time

from harness.model import cli_chat_model as ccm

ROLES = (("lead fact-checker", "orchestrator"), ("inside of the apartments", "inside"),
         ("from the air", "outside"), ("read paper records", "paper"))
SUB_CALLS = {"inside": [("check_floor_plans", {}), ("unit_feature_photos", {})],
             "outside": [("measure_building", {}), ("outdoor_feature_photos", {})],
             "paper": [("county_lookup", {}), ("building_record", {})]}


def _block(calls, say="Checking."):
    return say + "\n<tool_calls>" + json.dumps([{"name": n, "args": a} for n, a in calls]) + "</tool_calls>"


def role_of(prompt: str) -> str:
    return next((r for marker, r in ROLES if marker in prompt), "other")


def _needed(prompt: str) -> list[str]:
    m = re.search(r"Helpers needed: ([^\n<]*)", prompt)
    if not m or m.group(1).startswith("none"):
        return []
    return [h for h in ("inside", "outside", "paper") if re.search(rf"\b{h} \(", m.group(1))]


def orchestrator_reply(prompt: str, skip=frozenset()) -> str:
    n = prompt.count("<tool_result")
    transcript = prompt.split("# Transcript", 1)[-1]
    if n == 0:
        return _block([("read_claims", {}), ("match_listing", {})], "Reading the claims and finding the building.")
    asked = set(re.findall(r'"subagent_type": "(\w+)"', transcript))
    need = _needed(transcript)
    if "Not judged yet" in transcript.rsplit("<assistant>", 1)[-1]:
        todo = [h for h in need if h not in asked]
        return _block([("task", {"subagent_type": h, "description": f"Check the {h} claims."}) for h in todo],
                      "Asking the helpers I skipped.")
    if not asked:
        todo = [h for h in need if h not in skip]
        if todo:
            return _block([("task", {"subagent_type": h, "description": f"Check the {h} claims."}) for h in todo],
                          "Checking every source the claims need.")
    return _block([("judge_claims", {}), ("fill_in", {})], "Judging the claims.")


def install(monkeypatch, skip=(), delay=None):
    seen: list[tuple[str, list[str]]] = []
    delay = delay or {}

    def fake(self, prompt):
        block = re.search(r"# Tools\n(.*?)\n\n# Transcript", prompt, re.S).group(1)
        tools = re.findall(r'"name": "([^"]+)"', block)
        who = role_of(prompt)
        turn = sum(1 for w, _ in seen if w == who)
        seen.append((who, tools))
        if f"{who}{turn}" in delay:
            time.sleep(delay[f"{who}{turn}"])
        if who == "orchestrator":
            r = orchestrator_reply(prompt, frozenset(skip))
        elif who in SUB_CALLS:
            r = _block(SUB_CALLS[who]) if prompt.count("<tool_result") == 0 else "Done."
        else:
            r = "[]"
        return {"result": r, "usage": {}}, 0.01
    monkeypatch.setattr(ccm.ClaudeCLIChatModel, "_run_cli", fake)
    return seen
