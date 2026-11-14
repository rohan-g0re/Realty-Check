"""run_check(snapshot, emit, toolbox=None) -> done event (docs/CONTRACT.md 2.6).

Default path: the whole check runs inside the deepagents harness (harness/graph/agent.py). The
orchestrator reads the claims and matches the address with its own tools, delegates the checks to the
inside / outside / paper subagents, then calls the rule-based judge and "Fill me in" as its final tools.
If the agent fails, times out or stops early, the run still ends cleanly: whatever evidence was gathered is
judged by the same rules (claims without evidence get "couldn't check"), and done is sent. Nothing runs a
fixed plan behind the agent's back.

RC_LEGACY=1: the earlier flow (harness/graph/legacy.py): code preamble, old agent, fixed completion plan.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from rc_common import evidence as make_evidence
from tools import cachectl

from . import amenities, plans, trust
from .judge import SOURCE_NAME, VERB, _plan_code, judge
from .toolbox import Toolbox

AGENT_ENABLED = True  # False: no agent at all (claims and match only, every claim "couldn't check")
LAST_AGENT_STATS: dict = {}  # stats of the latest deep-agent run (model calls, delegations, trace)
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
SEVERITY = ["bedrooms", "area_sqft", "bathrooms", "stories", "unit_count", "year_built"]
RUN_BUDGET_S = 100.0    # hard end of a run: the agent is abandoned in time for the close to finish by then
CLOSE_RESERVE_S = 4.0   # kept for the close (judge, fill in, done: milliseconds) after an abandoned agent
VISION_MIN_LEFT_S = 15.0  # a vision call is skipped once less than this remains of the budget
FINISH_RESERVE_S = 1.5  # (legacy) picture checks still running this close to the budget are not checked
MAX_SCAN_FETCH = 8      # property-level scan summaries fetched from Matterport per run (cached ones are free)
MAX_FEATURE_SCANS = 2   # scans whose photos are searched for in-unit features per run
MAX_STILLS_FETCH = 3    # uncached scan stills (proof pictures) fetched per run; cached ones are free
HELPERS = ("inside", "outside", "paper")
ALWAYS_RESERVE_S = 1.5  # the close's direct aerial / county / record calls stop this far before the run's hard end
PROPERTY_WAIT_S = 10.0  # the amenity photo check waits at most this long for Vexcel property data after its look
MATCH_WAIT_S = 10.0     # read_claims waits this long for a match in progress, so "Helpers needed" knows the location
PROPERTY_DECIDE_WAIT_S = 3.0  # the amenity photo check waits at most this long for property data before deciding
                              # whether its vision look is needed (only when the data could answer every claim)
OUTLINE_WAIT_S = 4.0    # the aerial checks wait at most this long, after their own work, for the outlined photo
OUTLINE_KEYS = ("pool", "sport_court", "solar")  # what Vexcel elements/detect outlines (trust.property_key names)
MAX_UNIT_SCANS = 3      # unit scans (a listed unit's own, or an example unit's) opened per run, beyond the page unit's
MAX_EXAMPLE_UNITS = 3   # example unit rows shown on a building known only from its units (match.units_only)
TOP_MISMATCH_LLM = None  # harness.top_mismatch's model call (None: its default, the CLI login); tests stub it
TOP_MISMATCH_RESERVE_S = 1.0  # kept after the top-mismatch model call, for done
TOP_MISMATCH_MIN_S = 2.0      # less time than this left: the rule picks the top mismatch, no model call
TOP_FACTS_LLM = None  # harness.top_facts's model call (None: its default, the CLI login); tests stub it


class Run:
    """Run-local state: emitter, toolbox, evidence store and the coarse checks the agents call."""

    def __init__(self, snapshot: dict, emit: Callable[[dict], Any], tb: Toolbox):
        self.snapshot, self._emit, self.tb = snapshot or {}, emit, tb
        self.run_id = uuid.uuid4().hex[:12]
        self.address = (self.snapshot.get("address_text") or "").strip()
        self.claims: list[dict] = []
        self.match: dict = {"status": "no_match", "confidence": 0.0, "how": "not run"}
        self.lat = self.lon = None
        self.evidence: dict[str, dict] = {}  # evidence id -> Evidence
        self._done: dict[str, str] = {}      # check key -> short text already returned
        self._lock = threading.RLock()       # subagents run in parallel threads
        self.agent_stats: dict = {}
        self.t0 = time.monotonic()
        self._stills: dict[str, str] = {}  # scan model id -> evidence id of its stills
        self._plan_lock = threading.Lock()
        self._plans: dict[str, dict] = {}            # listing plan label (lower) -> resolution
        self._summaries: dict[str, dict] | None = None  # property-level scan id -> scan_summary Evidence
        self._pic_lock = threading.Lock()
        self._pic_thread: threading.Thread | None = None
        self._pic_evs: list[dict] = []               # finished picture-check Evidence, added after the agent
        self._pic_claims: dict[str, list[str]] = {}  # job name -> claim ids it answers
        self._pic_done: set[str] = set()
        self.vision_calls = 0
        self.budget_s = RUN_BUDGET_S  # what tools measure time_left() against (the agent path uses its own budget)
        self.path = "agent"
        # contract order (claims, match, evidence, verdicts, fill_in, done), whoever calls what when
        self._order = threading.RLock()
        self.claims_ready = self.match_ready = False
        self.claims_sent = self.match_sent = False
        self._held: list[dict] = []           # evidence events waiting for the match event
        self.sealed = False                   # judging started: later evidence is ignored
        self.finished = False                 # done sent: nothing more is emitted
        self.verdicts: list[dict] | None = None
        self.judged_by: str | None = None
        self.facts_sent = False
        self._judged = threading.Event()
        self.needed: dict[str, str] = {}      # helper -> why (from the claim kinds)
        self.helpers_used: set[str] = set()   # helpers whose tools ran
        self._judge_refused = False
        self._refused_at: float | None = None  # when judge_claims refused (fill_in called alongside stops waiting)
        self._stills_fetched = 0
        self._aerial: dict | None = None
        self._aerial_lock = threading.Lock()
        self._prop: dict | None = None                 # Vexcel property data of this run (once read)
        self._prop_ready = threading.Event()           # set once property_data() has finished, with or without data
        self._outline_th: threading.Thread | None = None  # the outlined-photo fetch, started at most once per run
        self.photo_look_skipped = False                # property data answered every aerial claim: no vision look
        self._outline_desc: dict | None = None         # the outlined photo's image descriptor, once made
        self._outline_feats: list[str] = []            # the property keys it boxed
        self._spans: dict[str, list[tuple[float, float]]] = {}
        self._late: dict[str, dict] = {}       # evidence that arrived after judging started (not used)
        self._match_evt = threading.Event()    # set once the match (and its map location) is known
        self.single_unit = False               # the page lists one home (set when judging; see single_unit_page())
        self.direct: list[str] = []            # always-run helpers the run called itself (the agent skipped them)
        # the page's own apartment, when the kit has exactly one unit row for it (settle_unit())
        self.unit: str | None = None           # unit text as the page names it ("1204")
        self.unit_row: dict | None = None      # tools.kit.find_unit row
        self.unit_scan_id: str | None = None   # that unit's own 3D scan
        self.unit_plan_scan: str | None = None  # else the scan of the unit's record plan
        self._unit_settled = False
        # other units the page lists ("Unit 1310" claim labels): label (lower) -> {"label", "unit", "row", "scan",
        # "capped"}; their claims are judged against that unit's own row and scan (settle_unit_labels())
        self.unit_links: dict[str, dict] = {}
        self._labels_settled = False
        self._unit_scan_ids: set[str] = set()  # unit scans kept for opening (at most MAX_UNIT_SCANS)

    def time_left(self) -> float:
        return self.budget_s - (time.monotonic() - self.t0)

    # ---- timing -------------------------------------------------------------------------------
    @contextlib.contextmanager
    def phase(self, name: str):
        a = time.monotonic() - self.t0
        try:
            yield
        finally:
            with self._lock:
                self._spans.setdefault(name, []).append((a, time.monotonic() - self.t0))

    def timing(self) -> dict:
        """Wall seconds of the run and of each phase (a phase's first start to its last end, so parallel
        checks of one helper are not added up), plus model seconds per role."""
        with self._lock:
            spans = {k: list(v) for k, v in self._spans.items()}
        phases = {k: round(max(b for _, b in v) - min(a for a, _ in v), 2) for k, v in spans.items() if v}
        out = {"total_s": round(time.monotonic() - self.t0, 2), "phases": phases}
        calls = (self.agent_stats or {}).get("calls") or []
        if calls:
            by: dict[str, list[float]] = {}
            for c in calls:
                by.setdefault(str(c.get("role") or "other"), []).append(float(c.get("wall_s") or 0))
            out["model_calls"] = len(calls)
            out["model_s"] = {k: round(sum(v), 2) for k, v in sorted(by.items())}
        return out

    # ---- events -------------------------------------------------------------------------------
    def emit(self, event: dict) -> None:
        with self._lock:
            if self.finished:
                return  # an abandoned agent thread may still be talking; done was already sent
            try:
                self._emit(event)
            except Exception:
                pass  # a broken listener must not kill the run

    def _flush(self) -> None:
        """Send claims, then match, then held evidence, as soon as each is ready (contract order)."""
        with self._order:
            if self.claims_ready and not self.claims_sent:
                self.claims_sent = True
                self.emit({"type": "claims", "claims": self.claims})
            if self.claims_sent and self.match_ready and not self.match_sent:
                self.match_sent = True
                self.emit({"type": "match", "match": self.match})
            if self.match_sent and self._held:
                held, self._held = self._held, []
                for ev in held:
                    self.emit(ev)

    def step(self, agent: str, text: str) -> None:
        self.emit({"type": "step", "agent": agent, "text": text})

    def error(self, message: str) -> None:
        self.emit({"type": "error", "message": message[:500]})

    # ---- evidence store -----------------------------------------------------------------------
    def add(self, ev: Any, source: str, plan: str | None = None) -> str:
        if not (isinstance(ev, dict) and ev.get("source") in SOURCE_NAME
                and ev.get("status") in ("ok", "no_evidence", "error")):
            ev = make_evidence(source, "error", "The check returned something unreadable.", detail=repr(ev)[:200])
        ev = {"source": ev["source"], "status": ev["status"], "summary": str(ev.get("summary") or ""),
              "observed": dict(ev.get("observed") or {}), "images": list(ev.get("images") or []),
              "detail": str(ev.get("detail") or "")}
        if plan and ev["source"] == "scan":
            ev["observed"].setdefault("plan", plan)
        with self._order:
            if self.sealed:  # judging has started: too late to count, and too late to show
                with self._lock:
                    eid = f"L{len(self._late) + 1}"
                    self._late[eid] = ev
                return eid
            with self._lock:
                eid = f"E{len(self.evidence) + 1}"
                self.evidence[eid] = ev
            if self.match_sent:
                self.emit({"type": "evidence", "evidence": ev})
            else:
                self._held.append({"type": "evidence", "evidence": ev})
        return eid

    def line(self, eid: str) -> str:
        """Short text an agent sees: id + status + one-line summary + small observed dict."""
        ev = self.evidence.get(eid) or self._late.get(eid)
        if eid in self._late:
            return f"{eid} arrived after judging started, so it was not used."
        obs = {k: v for k, v in ev["observed"].items()
               if not isinstance(v, (list, dict)) or len(json.dumps(v)) < 120}
        text = f"{eid} [{ev['source']} {ev['status']}] {ev['summary'][:160]} observed={json.dumps(obs)[:240]}"
        return text

    def _check(self, key: str, agent: str, say: str, body: Callable[[], str]) -> str:
        """Run one coarse check once per run; a repeat returns the earlier result without new calls."""
        with self._lock:
            if key in self._done:
                return self._done[key] + " (already checked)"
            self._done[key] = "(in progress)"
        self.step(agent, say)
        try:
            out = body()
        except Exception as e:
            out = f"check failed: {type(e).__name__}: {str(e)[:150]}"
        with self._lock:
            self._done[key] = out
        return out

    def _safe(self, source: str, fn: Callable, *args) -> dict:
        try:
            return fn(*args)
        except Exception as e:
            return make_evidence(source, "error", "This check failed.", detail=f"{type(e).__name__}: {str(e)[:200]}")

    # ---- the coarse checks (shared by the agent tools and the fixed plan) ---------------------
    def property_scans(self) -> dict[str, dict]:
        """scan_summary Evidence of the property-level scans: cached ones first, at most MAX_SCAN_FETCH fetched."""
        with self._plan_lock:
            if self._summaries is not None:
                return self._summaries
            ids = [i for i in self.match.get("matterport_model_ids") or [] if isinstance(i, str) and i]
            cached = [i for i in ids if _scan_cached(i)]
            fresh = [i for i in ids if i not in cached][:MAX_SCAN_FETCH]
            self._summaries = {mid: self._safe("scan", self.tb.scan_summary, mid) for mid in cached + fresh}
            return self._summaries

    def resolve(self, label: str | None) -> dict:
        """Listing plan -> link (tools.kit.plan_match.link_plan): {kit, kit_plans, how, ambiguous, scan_id,
        scan_how, scan_title, note, ...}. `ambiguous` = a type-level match: several record plans fit, none picked."""
        label = (label or "").strip()
        key = label.lower()
        with self._plan_lock:
            if key in self._plans:
                return self._plans[key]
        link = self.unit_links.get(key) if label else None
        if link:  # a unit the page lists, with its own row in the record: its own scan, never a floor plan's
            r = {"label": label, "kit": None, "kit_plans": [], "how": "unit", "ambiguous": False,
                 "scan_id": link["scan"], "scan_how": "unit_row" if link["scan"] else None, "scan_title": None,
                 "note": "", "unit": link["unit"]}
        elif not label:
            r = {"label": label, "kit": None, "kit_plans": [], "how": None, "ambiguous": False, "scan_id": None,
                 "scan_how": None, "scan_title": None, "note": ""}
            ids = self.match.get("matterport_model_ids") or []
            if self.unit_scan_id:  # the page's own unit has its own scan: it goes ahead of any plan or building scan
                r.update(scan_id=self.unit_scan_id, scan_how="unit")
            elif self.unit_plan_scan:
                r.update(scan_id=self.unit_plan_scan, scan_how="unit_plan")
            elif len(ids) == 1:
                r.update(scan_id=ids[0], scan_how="only")
        else:
            scans = self.property_scans() if self.match.get("status") == "matched" else {}
            try:
                r = _link_plan()(label, self.match, self.claims, scans)
            except Exception as e:  # the matcher must never break a run: an unlinked plan is "not in the records"
                r = {"label": label, "kit": None, "kit_plans": [], "how": None, "ambiguous": False, "scan_id": None,
                     "scan_how": None, "scan_title": None, "note": "", "error": f"{type(e).__name__}: {e}"[:200]}
        with self._plan_lock:
            return self._plans.setdefault(key, r)

    def type_link(self, label: str | None) -> dict | None:
        """The link of a listing plan when it is type-level (several record plans fit, none picked), else None."""
        if not label:
            return None
        r = self.resolve(label)
        return r if r.get("ambiguous") else None

    def _scan_misfit(self, fp: dict, mid: str) -> str | None:
        """A plan's own scan whose title gives a size more than 8% off the plan's is not used for that plan.
        (A bedroom count that differs is what the check is for, so it never disqualifies a scan.)"""
        sm = self.property_scans().get(mid)
        if not isinstance(sm, dict) or sm.get("status") != "ok":
            return None
        title = str((sm.get("observed") or {}).get("model_name") or "")
        ts, ks = plans.title_sqft(title), plans._num(fp.get("sqft"))
        if not ts or not ks or abs(ts - ks) / ks <= plans.SIZE_TOL:
            return None
        return (f"The 3D scan filed under the building's plan {plans.plan_label(fp)} is titled '{title}', which does "
                "not fit that plan, so it was not used.")

    def model_for(self, plan: str | None) -> str | None:
        return self.resolve(plan)["scan_id"]

    def canon_plan(self, plan: str) -> str:
        """Map whatever plan name the agent passes (e.g. the kit's 'Plan E 1 Bed + Den') back to the claim
        label that resolves to the same plan or scan ('Plan E'), so evidence and claims share one plan tag."""
        plan = (plan or "").strip()
        if not plan:
            return plan
        labels = sorted({c.get("label") for c in self.claims if c.get("label")})
        for label in labels:
            if label.strip().lower() == plan.lower():
                return label
        for label in labels:
            kit = self.resolve(label)["kit"]
            if kit and plans.plan_label(kit).lower() == plan.lower():
                return label
        mid = self.model_for(plan)
        for label in labels:
            if mid and self.model_for(label) == mid:
                return label
        return plan

    def _plan_record(self, plan: str, r: dict) -> dict:
        fp = r["kit"]
        name = plans.plan_label(fp)
        obs: dict[str, Any] = {"plan": plan, "kit_plan": name}
        bits = []
        for key, kind, word in (("beds", "bedrooms", "bed"), ("baths", "bathrooms", "bath"), ("sqft", "area_sqft", "sq ft")):
            v = fp.get(key)
            if v is not None:
                obs[kind] = v
                bits.append(f"{v:,} {word}" if key == "sqft" else f"{v} {word}{'s' if v != 1 else ''}")
        if r["note"] and r["scan_how"] != "title":
            obs["plan_match"] = r["note"]
        how = "its name" if r["how"] == "name" else "beds, baths and nearest size"
        return make_evidence("record", "ok", f"The building record lists plan {name}: {', '.join(bits)}.", obs,
                             detail=f"CoStar hackathon kit floor plan list of property {self.match.get('property_id')}; "
                                    f"listing plan {plan} matched to it by {how}.")

    def _all_plans(self) -> dict:
        """Every kit floor plan's numbers, for unit claims that name no plan: the judge may only confirm such a
        claim from them (some plan has these numbers), never contradict it with one particular plan."""
        fps = [{"name": plans.plan_label(fp), "beds": fp.get("beds"), "baths": fp.get("baths"), "sqft": fp.get("sqft")}
               for fp in self.match.get("floorplans") or [] if isinstance(fp, dict)]
        return make_evidence("record", "ok", f"The building record lists {len(fps)} floor plan"
                             f"{'s' if len(fps) != 1 else ''}.", {"floorplans": fps},
                             detail=f"CoStar hackathon kit floor plan list of property {self.match.get('property_id')}.")

    # ---- the page's own unit ----------------------------------------------------------------------
    def settle_unit(self) -> None:
        """Once the claims and the match are known: the apartment the page names (harness.claims.extract.page_unit)
        looked up in the kit's unit rows. With exactly one row, self.unit / unit_row / unit_scan_id are set, the
        claims' labels naming that unit are dropped (it is this home, not a floor plan) and the page counts as one
        home. A unit the kit does not have, a repeated number beds and baths cannot settle, a name that is also one
        of the building's floor plans, a unit store not built, or no lookup at all: nothing changes."""
        with self._lock:
            if self._unit_settled or not self.match_ready:
                return
            self._unit_settled = True
        m = self.match or {}
        if m.get("status") != "matched" or m.get("kind", "building") != "building":
            return
        fns = _unit_fns(self.tb)
        if not fns:
            return
        from harness.claims.extract import _unit_tools, page_unit  # lazy: the run imports without the kit
        tools = _unit_tools()
        unit = page_unit(self.snapshot, self.claims) if tools else None
        if not unit:
            return
        norm = tools[1]
        key = norm(unit)
        if key in {k for fp in m.get("floorplans") or [] if isinstance(fp, dict) for k in _plan_keys(fp.get("name"))}:
            return  # also a floor plan of this building: read as the plan, as before
        pid = m.get("property_id")
        try:
            row = fns["find_unit"](pid, unit, self._unit_value("bedrooms", key, norm),
                                   self._unit_value("bathrooms", key, norm))
        except Exception:
            row = None
        if not isinstance(row, dict) or row.get("ambiguous"):
            return
        try:
            scan = fns["unit_scan"](pid, unit)
        except Exception:
            scan = None
        own = [s for s in row.get("scan_ids") or [] if isinstance(s, str) and s]
        if not isinstance(scan, str) or not scan:
            scan = own[0] if len(own) == 1 else None
        plan_scan = None
        if not scan and row.get("plan"):
            plan_scan = next((fp.get("matterport_model_id") for fp in m.get("floorplans") or []
                              if isinstance(fp, dict) and fp.get("matterport_model_id")
                              and str(fp.get("name") or "").strip().lower() == str(row["plan"]).strip().lower()), None)
        claims = [{**c, "label": None} if c.get("label") and norm(c["label"]) == key else c for c in self.claims]
        with self._lock:
            self.claims, self.unit, self.unit_row = claims, unit, row
            self.unit_scan_id, self.unit_plan_scan = scan, plan_scan
        with self._plan_lock:
            self._plans.pop("", None)  # an unnamed resolution made before this knew nothing of the unit
        self.step("matcher", f"The page is about unit {unit}; the building record has a row for it.")

    def _unit_value(self, kind: str, key: str, norm: Callable) -> Any:
        """The one value the page gives for this unit kind (unnamed, or naming the unit), else None."""
        vals = {c.get("value") for c in self.claims if c.get("checkable") and c.get("kind") == kind
                and (not c.get("label") or norm(c["label"]) == key)}
        return vals.pop() if len(vals) == 1 else None

    # ---- other units the page lists ("Unit 1310"), and a building known only from its units ---------------
    def settle_unit_labels(self) -> None:
        """Once the claims and the match are known: each "Unit X" claim label (harness.claims.extract.unit_label)
        looked up in the record's unit rows, so that label's claims are judged against that unit's own row and scan
        instead of a floor plan (self.unit_links; resolve() and scan_rooms() use it). A normal building:
        find_unit / unit_scan (the page's beds and baths for that label choose among repeated numbers). A building
        known only from its units (match.units_only): the match's unit_plans / unit_scans. A unit the record has
        no row for, a repeated number beds and baths cannot settle, the page's own unit (settle_unit()), or a name
        that is also one of the building's floor plans: nothing changes. Local file reads only; the first
        MAX_UNIT_SCANS distinct unit scans (page order) are kept for opening, the rest are marked capped."""
        with self._lock:
            if self._labels_settled or not self.match_ready:
                return
            self._labels_settled = True
        m = self.match or {}
        if m.get("status") != "matched" or m.get("kind", "building") != "building":
            return
        if m.get("units_only") and m.get("unit_not_on_record"):
            self.step("matcher", f"Unit {m['unit_not_on_record']} is not in our records.")
        labels = list(dict.fromkeys(str(c["label"]).strip() for c in self.claims
                                    if c.get("label") and c.get("kind") in UNIT_KINDS and str(c["label"]).strip()))
        from harness.claims.extract import _unit_tools  # lazy: the run imports without the kit
        tools = _unit_tools() if labels else None
        fns = None if m.get("units_only") or not tools else _unit_fns(self.tb)
        if not tools or (not m.get("units_only") and not fns):
            return
        from_text, norm = tools
        plan_keys = {k for fp in m.get("floorplans") or [] if isinstance(fp, dict) for k in _plan_keys(fp.get("name"))}
        own = norm(self.unit) if self.unit else None
        homes = {norm(p.get("unit")): p for p in m.get("unit_plans") or [] if isinstance(p, dict) and p.get("unit")}
        links: dict[str, dict] = {}
        for label in labels:
            unit = from_text(label)
            key = norm(unit) if unit else None
            if not key or key == own or key in plan_keys:
                continue
            if m.get("units_only"):
                p = homes.get(key)
                if not p:
                    continue  # a unit not on record: the units on record are examples only (example_units())
                row = {"unit": str(p["unit"]), "beds": p.get("beds"), "baths": p.get("baths"), "sqft": p.get("sqft"),
                       "plan": None, "property_id": p.get("property_id")}
                scan = p.get("matterport_model_id") or _unit_scan_of(m, str(p["unit"]))
            else:
                row, scan = self._find_row(fns, m.get("property_id"), unit, label)
                if not row:
                    continue
            links[label.lower()] = {"label": label, "unit": str(row.get("unit") or unit), "row": row,
                                    "scan": scan if isinstance(scan, str) and scan else None, "capped": False}
        for link in links.values():
            if link["scan"] and not self._take_unit_scan(link["scan"]):
                link["scan"], link["capped"] = None, True
        with self._lock:
            self.unit_links = links
        with self._plan_lock:
            for k in links:
                self._plans.pop(k, None)  # a resolution made before this knew nothing of the unit
        if links:
            words = _units_words([x["unit"] for x in links.values()])
            self.step("matcher", f"The building record has its own row for {words}; those claims are checked against it.")

    def _find_row(self, fns: dict, pid: Any, unit: str, label: str) -> tuple[dict | None, str | None]:
        """(the one record row this listed unit names, its own scan) via the unit lookups, else (None, None)."""
        lab = label.strip().lower()
        one = {}
        for k in ("bedrooms", "bathrooms"):
            vals = {c.get("value") for c in self.claims if c.get("checkable") and c.get("kind") == k
                    and str(c.get("label") or "").strip().lower() == lab}
            one[k] = vals.pop() if len(vals) == 1 else None
        try:
            row = fns["find_unit"](pid, unit, one["bedrooms"], one["bathrooms"])
        except Exception:
            row = None
        if not isinstance(row, dict) or row.get("ambiguous"):
            return None, None
        try:
            scan = fns["unit_scan"](pid, unit)
        except Exception:
            scan = None
        own = [s for s in row.get("scan_ids") or [] if isinstance(s, str) and s]
        if not isinstance(scan, str) or not scan:
            scan = own[0] if len(own) == 1 else None
        return row, scan

    def _take_unit_scan(self, scan_id: str) -> bool:
        """True when this unit scan may be opened: one of the first MAX_UNIT_SCANS distinct unit scans of the run."""
        with self._lock:
            if scan_id in self._unit_scan_ids:
                return True
            if len(self._unit_scan_ids) >= MAX_UNIT_SCANS:
                return False
            self._unit_scan_ids.add(scan_id)
            return True

    def _unit_label_record(self, plan: str, link: dict) -> dict:
        """record Evidence of a listed unit's own row, tied to its claim label (observed.plan) so it decides that
        label's claims as a floor plan's record would: observed.unit, unit_listed: true, bedrooms / bathrooms /
        area_sqft (the unit's minimum size), unit_says, unit_plan, unit_scan."""
        row, unit = link["row"], link["unit"]
        nums, bits = _row_says(row)
        obs: dict[str, Any] = {"plan": plan, "unit": unit, "unit_listed": True, **nums}
        if row.get("plan"):
            obs["unit_plan"] = row["plan"]
        if link["scan"]:
            obs["unit_scan"] = link["scan"]
        if not bits:
            return make_evidence("record", "no_evidence", f"The building record has unit {unit} but no numbers for it.",
                                 obs)
        obs["unit_says"] = ", ".join(bits)
        if "area_sqft" in nums:
            obs["sqft_min"] = True
        where = (f"CoStar hackathon kit home listing {row.get('property_id')} (unit {unit})"
                 if self.match.get("units_only") else
                 f"CoStar hackathon kit unit row of property {self.match.get('property_id')}")
        fp = f" (floor plan {row['plan']})" if row.get("plan") else ""
        return make_evidence("record", "ok", f"The building record lists unit {unit} as {obs['unit_says']}{fp}.", obs,
                             detail=f"{where}; the size is the unit's listed minimum.")

    def example_units(self, agent: str = "paper") -> str:
        """A building known only from its units (match.units_only): up to MAX_EXAMPLE_UNITS of its units' rows
        ("Unit 1310 of this building (on record): 1 bed, 1 bath, 640 sq ft") and their scans (within MAX_UNIT_SCANS),
        as EXAMPLES of a home here, once per run. Their numbers sit under observed.example_unit only (no
        bedrooms / bathrooms / area_sqft of their own), so they never decide a claim: one unit says nothing about
        another. Units the page lists by label (unit_links) get their own evidence instead and are left out."""
        def body():
            linked = {str(x["unit"]) for x in self.unit_links.values()}
            homes = [p for p in self.match.get("unit_plans") or []
                     if isinstance(p, dict) and p.get("unit") and str(p["unit"]) not in linked]
            homes.sort(key=lambda p: not (p.get("matterport_model_id") or _unit_scan_of(self.match, str(p["unit"]))))
            out = []
            for p in homes[:MAX_EXAMPLE_UNITS]:
                unit = str(p["unit"])
                nums, bits = _row_says(p)
                ex = {"unit": unit, **nums}
                if bits:
                    ex["says"] = ", ".join(bits)
                ev = make_evidence("record", "ok" if bits else "no_evidence",
                                   f"Unit {unit} of this building (on record): {ex.get('says', 'no numbers on record')}.",
                                   {"example_unit": ex, "units_only": True},
                                   detail=f"CoStar hackathon kit home listing {p.get('property_id')}: one home in this "
                                          "building, shown as an example only; its numbers are not used to check "
                                          "another unit.")
                out.append(self.line(self.add(ev, "record")))
                sid = p.get("matterport_model_id") or _unit_scan_of(self.match, unit)
                if sid and self._take_unit_scan(sid):
                    sm = self._safe("scan", self.tb.scan_summary, sid)
                    out.append(self.line(self.add(_example_scan(sm, unit), "scan")))
            return "\n".join(out) or "No unit of this building is on record."
        return self._check("example_units", agent, "Reading the homes of this building on record (examples only).",
                           body)

    def _unit_scan_tags(self, how: str) -> dict:
        """observed keys on the scan the unit's run uses: its own scan (scan_of "unit"), or its record plan's."""
        tags = {"unit": self.unit, "scan_of": "unit" if how == "unit" else "plan"}
        if (self.unit_row or {}).get("plan"):
            tags["unit_plan"] = self.unit_row["plan"]
        return tags

    def _names_unit_plan(self, title: str) -> bool:
        """The scan title names the unit's record floor plan ('... Plan SA - Studio / 1 Bath' for a unit of plan
        'Plan SA') and not the unit itself: a scan of the plan, filed under the unit."""
        plan = str((self.unit_row or {}).get("plan") or "").strip()
        code = _plan_code(title)
        if not plan or not code or (self.unit and re.search(rf"\b{re.escape(str(self.unit))}\b", title)):
            return False
        return code in {(_plan_code(plan) or ""), plan.upper()}

    def unit_record(self, agent: str = "paper") -> str:
        """The building record's row for the page's unit, as record Evidence (once per run): observed.unit,
        unit_row: true, bedrooms / bathrooms / area_sqft (the unit's minimum size), unit_plan, unit_says."""
        def body():
            row, unit = self.unit_row or {}, self.unit
            nums, bits = _row_says(row)
            obs: dict[str, Any] = {"unit": unit, "unit_row": True, **nums}
            if "area_sqft" in nums:
                obs["sqft_min"] = True
            if row.get("plan"):
                obs["unit_plan"] = row["plan"]
            if self.unit_scan_id:
                obs["unit_scan"] = self.unit_scan_id
            if not bits:
                ev = make_evidence("record", "no_evidence", f"The building record has unit {unit} but no numbers for it.",
                                   obs)
                return self.line(self.add(ev, "record"))
            obs["unit_says"] = ", ".join(bits)
            plan = f" (floor plan {row['plan']})" if row.get("plan") else ""
            ev = make_evidence("record", "ok", f"The building record lists unit {unit} as {obs['unit_says']}{plan}.",
                               obs, detail=f"CoStar hackathon kit unit row of property {self.match.get('property_id')}; "
                                           "the size is the unit's listed minimum.")
            return self.line(self.add(ev, "record"))
        return self._check("unit_record", agent, f"Reading the building record's row for unit {self.unit}.", body)

    def scan_rooms(self, plan: str = "", agent: str = "inside") -> str:
        plan = self.canon_plan(plan)
        name = plan or (f"unit {self.unit}" if self.unit_row else "this unit")

        def body():
            r = self.resolve(plan)
            out = []
            example = bool(r.get("ambiguous"))  # type-level: several record plans fit, none was picked
            link = self.unit_links.get(plan.lower()) if plan else None  # a listed unit with its own record row
            units_only = bool(self.match.get("units_only")) and self.match.get("status") == "matched"
            if not plan and self.unit_row:
                out.append(self.unit_record(agent))
            if link:
                out.append(self.line(self.add(self._unit_label_record(plan, link), "record")))
            elif plan and example:
                out.append(self.line(self.add(_type_parts()[1](r, self.match), "record")))
            elif plan and r["kit"]:
                out.append(self.line(self.add(self._plan_record(plan, r), "record")))
            elif units_only and not self.unit_row:  # no building record: its units on record, as examples only
                out.append(self.example_units(agent))
            elif plan and self.match.get("status") == "matched":
                out.append(self.line(self.add(make_evidence(
                    "record", "no_evidence", f"{plan} is not in the building's floor plan records.",
                    {"plan": plan, "plan_missing": True},
                    detail="No kit floor plan has this name, or the same beds and baths within 8% of its size."),
                    "record")))
            elif not plan and self.match.get("status") == "matched" and self.match.get("floorplans") \
                    and not self.unit_row:
                out.append(self.line(self.add(self._all_plans(), "record")))
            if not r["scan_id"] and link and link.get("capped"):
                out.append(self.line(self.add(make_evidence(
                    "scan", "no_evidence", f"Unit {link['unit']} has a 3D scan on record; this check opens at most "
                    f"{MAX_UNIT_SCANS} unit scans, so it was not looked at.", {"unit": link["unit"], "scan_capped": True}),
                    "scan", plan)))
                return "\n".join(out)
            if not r["scan_id"]:
                out.append(self.line(self.add(make_evidence("scan", "no_evidence", f"No 3D scan was found for {name}."),
                                              "scan", plan)))
                return "\n".join(out)
            ev = self._safe("scan", self.tb.scan_summary, r["scan_id"])
            if r["note"] and isinstance(ev, dict) and ev.get("status") == "ok":
                ev = {**ev, "observed": {**(ev.get("observed") or {}), "plan_match": r["note"]}}
            if r.get("scan_how") in ("unit", "unit_plan") and isinstance(ev, dict) and ev.get("status") == "ok":
                how = r["scan_how"]
                if how == "unit" and self._names_unit_plan(str((ev.get("observed") or {}).get("model_name") or "")):
                    how = "unit_plan"  # filed under the unit, but its title names the unit's floor plan
                ev = {**ev, "observed": {**(ev.get("observed") or {}), **self._unit_scan_tags(how)}}
            if link and isinstance(ev, dict) and ev.get("status") == "ok":
                ev = {**ev, "observed": {**(ev.get("observed") or {}), "unit": link["unit"], "unit_listed": True}}
            if example and isinstance(ev, dict):
                # an example of the kind of home only: not tied to the listing's plan, so it never decides its claims
                ev = {**ev, "observed": {**(ev.get("observed") or {}), "example_for": plan}}
                out.append(self.line(self.add(ev, "scan")))
            else:
                out.append(self.line(self.add(ev, "scan", plan)))
            return "\n".join(out)
        what = (f"unit {self.unit_links[plan.lower()]['unit']}" if plan and plan.lower() in self.unit_links
                else f"floor plan {plan}" if plan else (f"unit {self.unit}" if self.unit_row else "this unit"))
        return self._check(f"scan_rooms:{plan.lower()}", agent, f"Checking {what} against its record and 3D scan.",
                           body)

    def scan_photos(self, plan: str = "", claim: str = "", agent: str = "inside") -> str:
        plan = self.canon_plan(plan)
        name = plan or "this unit"

        def body():
            mid = self.model_for(plan)
            if not mid:
                return f"No 3D scan for {name}, so no photos."
            with self._lock:
                sid = self._stills.get(mid)
            if sid is None:  # one stills fetch per scan, however many photo checks ask for it
                sid = self.add(self._safe("scan", self.tb.scan_stills, mid, 4), "scan", plan)
                with self._lock:
                    self._stills[mid] = sid
            paths = [im["path"] for im in self.evidence[sid]["images"] if im.get("path")][:4]
            if not paths:
                return self.line(sid)
            if (not claim or agent == "orchestrator") and any(self.has_ok(k, "scan", plan or None) for k in ("bedrooms", "bathrooms")):
                return self.line(sid) + "\nThe scan already gave room counts, so the photos are kept as proof only."
            if not claim and all(self.has_ok(k, "record", plan or None) for k in ("bedrooms", "bathrooms")):
                # a room count read off the photos is a label count: it never outweighs the record's own count for
                # this plan (judge._vet_labels, trust "room_labels"), so the slow vision call is not made
                return (self.line(sid) + "\nThe building record already gives this plan's room counts, so the photos "
                        "are kept as proof only.")
            if claim:
                res = self._vision(self.tb.check_room, paths, claim)
                a = res.get("answer") or {}
                summ = (f"Scan photos show a {a.get('room_type', 'room')}; window: "
                        f"{'yes' if a.get('has_window') else 'no'}; supports the claim: {a.get('supports_claim', 'unclear')}.")
                obs = {k: a[k] for k in ("room_type", "has_window", "supports_claim") if k in a}
            else:
                res = self._vision(self.tb.read_floorplan, paths)
                a = res.get("answer") or {}
                obs = {k: a[k] for k in ("bedrooms", "bathrooms", "has_den", "room_labels") if k in a}
                summ = f"Reading the scan photos: {a.get('bedrooms', '?')} bedroom(s), {a.get('bathrooms', '?')} bathroom(s)" \
                       + (" and a den." if a.get("has_den") else ".")
            ok = res.get("status") == "ok"
            late = not ok and bool(res.get("skipped"))  # not a failure: the run's time ran short
            ev = make_evidence("scan", "ok" if ok else "no_evidence" if late else "error",
                               summ if ok else "We ran out of time before the photos were read." if late
                               else "The photos could not be read.",
                               obs if ok else {}, self.evidence[sid]["images"][:2],
                               f"vision model on scan stills, confidence {res.get('confidence')}; {str(res.get('notes'))[:150]}")
            return self.line(sid) + "\n" + self.line(self.add(ev, "scan", plan))
        return self._check(f"scan_photos:{plan.lower()}:{claim.lower()}", agent,
                           f"Looking at photos from the 3D scan of {name}.", body)

    def _vision(self, fn, *args) -> dict:
        if self.time_left() < VISION_MIN_LEFT_S:
            return {"status": "error", "answer": {}, "confidence": 0.0, "skipped": True,
                    "notes": "skipped: the run's time budget is nearly used up"}
        try:
            res = fn(*args)
            return res if isinstance(res, dict) else {"status": "error", "notes": "bad vision reply"}
        except Exception as e:
            return {"status": "error", "answer": {}, "confidence": 0.0, "notes": f"{type(e).__name__}: {e}"[:200]}

    def _no_location(self) -> str:
        return self.line(self.add(make_evidence("aerial", "no_evidence", "We have no map location for this address."),
                                  "aerial"))

    def aerial_ev(self) -> dict:
        """The aerial photos of this location, fetched once per run (roof check and amenity check share them)."""
        with self._aerial_lock:
            if self._aerial is None:
                self._aerial = self._safe("aerial", self.tb.aerial_images, self.lat, self.lon)
                self._say_no_coverage(self._aerial)
            return self._aerial

    def aerial_photos(self, agent: str = "outside") -> str:
        def body():
            if self.lat is None or self.lon is None:
                return self._no_location()
            return self.line(self.add(self.aerial_ev(), "aerial"))
        return self._check("aerial_photos", agent, "Fetching Vexcel aerial photos of the building.", body)

    def measure_building(self, agent: str = "outside") -> str:
        def body():
            if self.lat is None or self.lon is None:
                return self._no_location()
            th = self._outlined_soon()  # the outlined photo, alongside (no-op unless a pool / court / solar claim)
            h, p = _ctx_map(lambda f: f(), [
                lambda: self.line(self.add(self._height(), "aerial")),
                lambda: self.property_data(agent)], 2)  # roof height and property data side by side
            out = h + "\n" + p + "\n" + self.aerial_photos(agent)
            if th is not None:
                th.join(timeout=OUTLINE_WAIT_S)  # it ran alongside the above, so this is usually no wait at all
            return out
        return self._check("measure_building", agent, "Measuring the roof height from Vexcel aerial imagery.", body)

    def _height(self) -> dict:
        ev = self._safe("aerial", self.tb.building_height, self.lat, self.lon)
        self._say_no_coverage(ev)
        return ev

    def property_data(self, agent: str = "outside") -> str:
        """Vexcel property data for the parcel (pool, hot tub, sport court, playground, solar, ...), fetched once per
        run and stored as its own aerial Evidence with the whole answer under observed.property. Nothing is stored
        when it fails or has no answer: the run goes on without it."""
        def body():
            try:
                fn = _property_fn(self.tb)
                if self.lat is None or self.lon is None or fn is None:
                    return "No Vexcel property data for this location."
                try:
                    prop = fn(self.lat, self.lon)
                except Exception as e:
                    prop = {"no_coverage": True} if _no_coverage_error(e) else None
                if isinstance(prop, dict) and prop.get("no_coverage"):  # Vexcel answered 403 / 404 here
                    return self._no_coverage("property data")
                if not isinstance(prop, dict) or not prop:
                    return "No Vexcel property data for this location."
                self._prop = prop
                ev = make_evidence("aerial", "ok", _property_summary(prop), {"property": prop},
                                   detail="Vexcel property data: outdoor features marked on the parcel from the "
                                          "aerial survey (newest year; 'stable' = the year before agrees).")
                return self.line(self.add(ev, "aerial"))
            finally:
                self._prop_ready.set()
        return self._check("property_data", agent, "Reading Vexcel property data for the parcel.", body)

    def _no_coverage(self, what: str) -> str:
        """Vexcel has no coverage at this point (it answered 403 / 404): a step, once per run, and an aerial
        Evidence with status no_evidence saying so (observed.no_coverage), so the gap is not silent."""
        with self._lock:
            first = not getattr(self, "_no_coverage_said", False)
            self._no_coverage_said = True
        if first:
            self.step("outside", NO_COVERAGE_TEXT)
        ev = make_evidence("aerial", "no_evidence", NO_COVERAGE_TEXT, {"no_coverage": True, "what": what},
                           detail=f"Vexcel {what}: the service has no data for this location (HTTP 403 / 404).")
        return self.line(self.add(ev, "aerial"))

    def _say_no_coverage(self, ev: Any) -> None:
        """A step (once per run) when a Vexcel photo or roof-height answer says there is no coverage here; the
        Evidence itself already says so."""
        if isinstance(ev, dict) and ev.get("source") == "aerial" and ev.get("status") == "no_evidence" \
                and re.search(r"no aerial coverage", str(ev.get("summary") or ""), re.I):
            with self._lock:
                first = not getattr(self, "_no_coverage_said", False)
                self._no_coverage_said = True
            if first:
                self.step("outside", NO_COVERAGE_TEXT)

    def _property_soon(self) -> threading.Thread:
        """property_data() in a background thread (same context), for the amenity photo check to run alongside."""
        th = threading.Thread(target=contextvars.copy_context().run, args=(self.property_data,), daemon=True,
                              name="rc-property")
        th.start()
        return th

    def _outline_wanted(self) -> bool:
        """A claim about a pool, sport court or solar panels: the only things the outlined photo can box."""
        return any(trust.property_key(c) in OUTLINE_KEYS for c in self.claims)

    def _outlined_soon(self) -> threading.Thread | None:
        """outlined_photo() in a background thread (same context), started at most once per run; later callers get
        the same thread to wait on. None when there is nothing to outline or no outlining tool."""
        with self._lock:
            if self._outline_th is None:
                if (self.lat is None or self.lon is None or _outlined_fn(self.tb) is None
                        or not self._outline_wanted()):
                    return None
                self._outline_th = threading.Thread(target=contextvars.copy_context().run,
                                                    args=(self.outlined_photo,), daemon=True, name="rc-outline")
                self._outline_th.start()
            return self._outline_th

    def outlined_photo(self) -> dict | None:
        """The top-down Vexcel aerial photo with detected pools, sport courts and solar panels boxed
        (tools.aerial.outlined_image), stored as one aerial Evidence: images [outlined photo, plain top-down photo],
        observed {"feature_check": true, "outlined": [property keys it boxed]}. It names no claim: the judge shows it
        only on a pool / sport court / solar claim that the property data or the vision look reports present AND the
        detection boxed. The detection square is 120 m wide, so the caption says "detected near the address".
        Failure or nothing detected: no Evidence and nothing else changes. Returns the image descriptor or None."""
        fn = _outlined_fn(self.tb)
        if fn is None or self.lat is None or self.lon is None:
            return None
        plain_ev = self.aerial_ev()  # the plain photo first (shared with the run), so the outlining reuses it
        try:
            im = fn(self.lat, self.lon)
        except Exception:
            im = None
        if not isinstance(im, dict) or not isinstance(im.get("path"), str) or not im["path"]:
            return None
        feats = _outlined_features(im)
        if not feats:
            return None
        names = _and([OUTLINE_NAMES[k] for k in feats])
        desc = {"path": im["path"], "caption": _outlined_caption(str(im.get("caption") or ""), names)}
        plain = next((x for x in (plain_ev or {}).get("images") or [] if isinstance(x, dict) and x.get("path")
                      and str(x.get("caption") or "").lower().startswith("top-down")), None)
        ev = make_evidence("aerial", "ok",
                           f"Top-down Vexcel aerial photo with the {names} detected near the address outlined.",
                           {"feature_check": True, "outlined": feats, "looked_at": "Vexcel aerial photos"},
                           [desc] + ([dict(plain)] if plain else []),
                           detail="Vexcel elements/detect boxes on the top-down photo of a 120 m square around the "
                                  "map point; a box can fall on a neighbouring lot. Shown only next to claims the "
                                  "property data or the photo look already report present.")
        self._outline_desc, self._outline_feats = desc, feats
        self.add(ev, "aerial")
        return desc

    def count_floors_on_photo(self, agent: str = "outside") -> str:
        """Illustrative only: a floor count read off a photo never decides the stories verdict (the vision model
        miscounted the real demo building). Stored as observed.photo_floor_count, never observed.stories, and
        skipped entirely once the roof height is measured."""
        def body():
            if self.has_ok("stories", "aerial"):
                return "The roof height is already measured; a photo count is not needed (it never decides floors)."
            self.aerial_photos(agent)
            imgs = [im for e in list(self.evidence.values()) if e["source"] == "aerial" and e["status"] == "ok"
                    for im in e["images"] if im.get("path")]
            angled = [im for im in imgs if any(w in str(im.get("caption", "")).lower() for w in ("angled", "oblique"))]
            use = (angled or imgs)[:2]
            if not use:
                return "No Vexcel aerial photo is available to count floors on."
            res = self._vision(self.tb.count_floors, [im["path"] for im in use])
            a = res.get("answer") or {}
            ok = res.get("status") == "ok" and isinstance(a.get("stories"), (int, float))
            ev = make_evidence("aerial", "ok" if ok else "error",
                               f"For illustration only: window rows on the angled Vexcel aerial photo suggest about "
                               f"{a.get('stories')} floors (a photo count never decides the verdict)." if ok
                               else "Floors could not be counted on the Vexcel aerial photo.",
                               {"photo_floor_count": a.get("stories"), "illustrative": True,
                                "how_counted": str(a.get("how_counted", ""))[:150]} if ok else {},
                               use, f"vision model floor count (illustrative), confidence {res.get('confidence')}")
            return self.line(self.add(ev, "aerial"))
        return self._check("count_floors", agent, "Counting floors on the angled Vexcel aerial photo.", body)

    def county_lookup(self, agent: str = "paper") -> str:
        def body():
            city = self.match.get("city")
            ev = self._safe("county", self.tb.county_record, self.address, self.lat, self.lon, city)
            return self.line(self.add(ev, "county"))
        return self._check("county", agent, "Looking up the county property record.", body)

    def building_record(self, agent: str = "paper") -> str:
        def body():
            if self.match.get("status") != "matched":
                ev = make_evidence("record", "no_evidence", "No building record matched this address.")
            else:
                ev = self._safe("record", self.tb.record_evidence, self.match)
            out = self.line(self.add(ev, "record"))
            if self.unit_row:
                out += "\n" + self.unit_record(agent)
            if self.match.get("units_only") and self.match.get("status") == "matched":
                out += "\n" + self.example_units(agent)
            extra = record_amenities(self)
            return f"{out}\n{extra}" if extra else out
        return self._check("record", agent, "Reading the building record.", body)

    # ---- agent path: the orchestrator's own tools ----------------------------------------------------
    def read_claims(self) -> str:
        """Orchestrator tool: read the listing's claims (rules + JSON-LD + one model call through the run's
        chat model), send the claims event, and say which helpers the claim kinds need."""
        with self._order:
            if self.claims_ready:
                return self.claims_text() + " (already read)"
            if getattr(self, "_reading", False):
                return "The claims are being read already."
            self._reading = True
        self.step("claims", "Reading what the listing claims.")
        try:
            with self.phase("claims"):
                claims = self.tb.extract_claims(self.snapshot) or []
            self.claims = [c for c in claims if isinstance(c, dict) and c.get("id")]
        except Exception as e:
            self.error(f"reading claims failed: {type(e).__name__}: {e}")
        # The location decides whether outside / paper are needed: match now if match_listing has not started (it
        # then answers "already matched"), or wait for the one running alongside.
        if not self.match_ready:
            self.match_listing()
            self._match_evt.wait(timeout=max(0.0, min(MATCH_WAIT_S, self.time_left() - VISION_MIN_LEFT_S)))
        self.settle_unit()  # the page's own unit, before the claims event (a unit label is not a plan)
        self.settle_unit_labels()  # other units the page lists, by label
        with self._order:
            self.needed = helpers_needed(self.claims, self.match)
            self.claims_ready = True
            self._flush()
        return self.claims_text()

    def claims_text(self) -> str:
        checkable = [c for c in self.claims if c.get("checkable")]
        rows = [f"{c['id']} {c.get('kind')}={json.dumps(c.get('value'))}" + (f" [{c['label']}]" if c.get("label") else "")
                for c in checkable]
        need = "; ".join(f"{h} ({why})" for h, why in self.needed.items()) or "none (nothing measurable)"
        unit = (f"\nThis page is about unit {self.unit}; the building record has its own row (beds, baths, size)."
                if self.unit_row else "")
        return (f"{len(self.claims)} claims, {len(checkable)} checkable:\n" + "\n".join(rows) + unit
                + f"\nHelpers needed: {need}")

    def do_match(self) -> None:
        """Address -> Match (the matcher's public function), plus a geocoded location when the match has none."""
        m = self.tb.match_address(self.address)
        if isinstance(m, dict) and m.get("status") in ("matched", "no_match"):
            self.match = m
        if self.match.get("lat") is None or self.match.get("lon") is None:
            g = self.tb.geocode(self.address) if self.address else None
            if isinstance(g, dict) and g.get("lat") is not None:
                self.match = {**self.match, "lat": g["lat"], "lon": g["lon"]}
        self.lat, self.lon = self.match.get("lat"), self.match.get("lon")

    def match_listing(self) -> str:
        """Orchestrator tool: match the listing's address to a building record (plans, scans, location)."""
        with self._order:
            busy = self.match_ready or getattr(self, "_matching", False)
            if not busy:
                self._matching = True
        if busy:  # matched already, or matching in another thread (read_claims): its result, once known
            self._match_evt.wait(timeout=max(0.0, min(MATCH_WAIT_S, self.time_left())))
            return self.match_text() + " (already matched)"
        self.step("matcher", f"Finding the building at {self.address or 'this address'}.")
        try:
            with self.phase("match"):
                self.do_match()
        except Exception as e:
            self.error(f"address match failed: {type(e).__name__}: {e}")
        with self._order:
            self.match_ready = True
            if self.claims_ready:  # the claims were read first (their wait for this match ran out)
                self.settle_unit()
                self.settle_unit_labels()
                self.needed = helpers_needed(self.claims, self.match)
            self._flush()  # sent right after the claims event, never before it
        self._match_evt.set()
        return self.match_text()

    def match_text(self) -> str:
        m = self.match
        if m.get("status") != "matched":
            loc = "has a map location" if self.lat is not None else "no map location"
            return f"No building record matched this address ({m.get('how')}); {loc}."
        fps = m.get("floorplans") or []
        scans = sum(1 for f in fps if f.get("matterport_model_id"))
        return (f"Matched {m.get('name')} ({m.get('kind', 'building')}), confidence {m.get('confidence')}: "
                f"{len(fps)} floor plans ({scans} with a 3D scan), {len(m.get('matterport_model_ids') or [])} scans "
                f"in all, record {json.dumps(m.get('record'))}, "
                f"{'has a map location' if self.lat is not None else 'no map location'}.")

    def judge_claims(self) -> str:
        """Orchestrator's final tool: the rule-based judge over the gathered evidence. Refuses once while a
        needed helper has not been asked, so a check does not stop at one source."""
        missing = [h for h in self.needed if h not in self.helpers_used]
        if missing and not self._judge_refused and self.claims_ready:
            self._judge_refused = True
            self._refused_at = time.monotonic()
            return (f"Not judged yet: these helpers are needed and were not asked: {', '.join(missing)}. "
                    "Ask them now with the task tool (all in one message), then call judge_claims again.")
        self.run_always()
        verdicts = self.judge_all(by="agent")
        counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("confirmed", "contradicted", "close", "cant_check")}
        return "Judged: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()) + "."

    def fill_in_tool(self) -> str:
        """Orchestrator tool: "Fill me in" facts, sent after the verdicts."""
        t = time.monotonic()
        end = t + max(0.0, min(15.0, self.time_left()))
        while not self._judged.wait(timeout=0.05):
            refused = self._refused_at is not None and self._refused_at >= t - 2.0
            if refused or time.monotonic() >= end:  # a refusal alongside: no point waiting for verdicts
                return "Not sent: call judge_claims first."
        n = self.send_fill_in(by="agent")
        return f"Sent {n} facts the listing does not mention." if n is not None else "Already sent."

    # ---- agent path: the subagents' tools -----------------------------------------------------------
    def check_floor_plans(self, names: list[str] | None = None, agent: str = "inside") -> str:
        """Every listing floor plan (or the given ones): its kit plan, 3D scan or record numbers, then cached or
        a few fresh scan stills as proof pictures (plans whose scan disagrees with the listing first)."""
        self.helpers_used.add(agent)
        listing = sorted({c.get("label") or "" for c in self.claims if c.get("checkable") and c.get("kind") in UNIT_KINDS})
        # the plans the agent names first, then any listing plan it left out (every claimed plan gets checked)
        todo = list(dict.fromkeys([self.canon_plan(str(n)) for n in names or [] if str(n).strip()] + listing))
        if not todo:
            return "The listing names no floor plan claims to check."
        with self.phase(agent):
            outs = _ctx_map(lambda p: self.scan_rooms(p, agent=agent), todo, 6)
            stills = [p for p in todo if p and self.model_for(p)]
            stills.sort(key=lambda p: (0 if self._plan_disagrees(p) else 1))
            fresh = []
            for p in stills:
                if _stills_cached(self.model_for(p)):
                    fresh.append(p)
                elif self._stills_fetched < MAX_STILLS_FETCH and self.time_left() > VISION_MIN_LEFT_S:
                    self._stills_fetched += 1
                    fresh.append(p)
            if fresh:  # proof pictures only: waited for while time allows, never past the reserve for judging
                got = self._bounded(lambda: _ctx_map(lambda p: self.scan_photos(p, agent=agent), fresh, 4))
                outs += got if got is not None else [
                    f"Scan photos of {', '.join(fresh)} are still downloading; they are not needed for the counts."]
        return "\n".join(outs)

    def _plan_disagrees(self, plan: str) -> bool:
        claimed = {c.get("kind"): c.get("value") for c in self.claims
                   if (c.get("label") or "") == plan and c.get("kind") in ("bedrooms", "bathrooms")}
        for e in list(self.evidence.values()):
            o = e["observed"]
            if e["source"] == "scan" and e["status"] == "ok" and str(o.get("plan", "")) == plan:
                if any(k in o and o[k] is not None and o[k] != v for k, v in claimed.items()):
                    return True
        return False

    def features(self, which: str, agent: str) -> str:
        """Picture checks of one kind, run now: 'aerial' (outdoor amenities on the aerial photos) or 'unit'
        (in-unit features on scan photos, plus amenity rooms the building has scanned)."""
        self.helpers_used.add(agent)
        feats = [(c, amenities.feature_for(c)) for c in self.claims]
        if which == "aerial":
            jobs = {"aerial": (self._aerial_features, [(c, f) for c, f in feats if f and f[2] == "aerial"])}
        else:
            titled = [c for c in self.claims if amenities.title_rooms(c)] if self.match.get("matterport_model_ids") else []
            jobs = {"unit": (self._unit_features, [(c, f) for c, f in feats if f and f[2] == "scan"]),
                    "titles": (self._title_rooms, titled)}
        jobs = {k: v for k, v in jobs.items() if v[1]}
        if not jobs:
            return "No claims of this kind to look for in pictures."

        def body():
            cancel = threading.Event()
            with self.phase(agent):
                res = self._bounded(lambda: _ctx_map(lambda kv: self._run_pictures(kv[0], *kv[1], cancel=cancel),
                                                     list(jobs.items()), len(jobs)))
            if res is None:  # out of time: these claims are honestly "not checked"; a late answer is dropped
                cancel.set()
                res = []
                for name, (_, items) in jobs.items():
                    ids = [(x[0] if isinstance(x, tuple) else x)["id"] for x in items]
                    src = "aerial" if name == "aerial" else "scan"
                    res.append([self.line(self.add(make_evidence(
                        src, "no_evidence", "We ran out of time before the photos were checked.",
                        {"feature_check": True, "claim_ids": ids, "presence": "skipped"}), src))])
            return "\n".join(line for r in res for line in r) or "Nothing found."
        return self._check(f"features:{which}", agent, "Looking for the claimed features in "
                           + ("the Vexcel aerial photos." if which == "aerial" else "the 3D scan photos."), body)

    def _bounded(self, fn: Callable[[], Any], reserve: float = 12.0) -> Any:
        """fn() in a worker thread (same context: the run's model), waited for until `reserve` seconds of the budget
        are left (time for the orchestrator to judge). None when it did not finish; the worker is left to end."""
        box: dict[str, Any] = {}
        ctx = contextvars.copy_context()

        def work():
            try:
                box["v"] = ctx.run(fn)
            except Exception as e:
                box["e"] = e
        th = threading.Thread(target=work, daemon=True, name="rc-bounded")
        th.start()
        th.join(timeout=max(0.0, self.time_left() - reserve))
        if th.is_alive():
            return None
        if "e" in box:
            raise box["e"]
        return box["v"]

    def _run_pictures(self, name: str, fn: Callable, items: list, cancel: threading.Event | None = None) -> list[str]:
        try:
            evs = fn(items)
        except Exception as e:
            ids = [(x[0] if isinstance(x, tuple) else x)["id"] for x in items]
            evs = [make_evidence("aerial" if name == "aerial" else "scan", "error", "The picture check failed.",
                                 {"feature_check": True, "claim_ids": ids, "presence": "unclear"},
                                 detail=f"{type(e).__name__}: {str(e)[:200]}")]
        if cancel is not None and cancel.is_set():
            return []
        return [self.line(self.add(ev, ev["source"])) for ev in evs]

    def tool(self, agent: str, fn: Callable[..., str], *args) -> str:
        """Run one subagent check, marking the helper as used and timing it under its name."""
        self.helpers_used.add(agent)
        with self.phase(agent):
            return fn(*args, agent=agent)

    # ---- the close: verdicts, fill in, done (also when the agent never got there) ---------------------
    def ensure_claims_and_match(self) -> None:
        """Before any verdict: a claims and a match event exist. Claims the agent never read come from the
        page's own words (rules and JSON-LD, no model); an address it never matched is said to be unmatched."""
        with self._order:
            if not self.claims_ready:
                try:
                    try:
                        claims = self.tb.extract_claims(self.snapshot, use_model=False)
                    except TypeError:
                        claims = []
                    self.claims = [c for c in claims or [] if isinstance(c, dict) and c.get("id")]
                except Exception:
                    self.claims = []
                self.claims_ready = True
            if not self.match_ready:
                if self.match.get("how") == "not run":
                    self.match = {**self.match, "how": "The check stopped before the address was matched."}
                self.match_ready = True
                self._match_evt.set()
            self.settle_unit()
            self.settle_unit_labels()
            self._flush()

    def judge_all(self, by: str) -> list[dict]:
        """Judge every claim once (rules, no model) and send the verdicts; later calls return the same list."""
        with self._order:
            if self.verdicts is not None:
                return self.verdicts
            self.ensure_claims_and_match()
            self.sealed = True
            self.step("judge", "Comparing every claim with the evidence.")
            t = time.monotonic()
            evs = list(self.evidence.values())
            try:
                self.single_unit = self.single_unit_page()
            except Exception:
                self.single_unit = False
            verdicts = []
            for c in self.claims:
                try:
                    v = self.type_verdict(c) or self.unit_verdict(c, evs) or judge(c, evs, single_unit=self.single_unit)
                except Exception as e:
                    v = {"claim_id": c.get("id"), "verdict": "cant_check", "claimed": str(c.get("quote") or ""),
                         "found": "no data", "source": None,
                         "reason": f"This claim could not be judged ({type(e).__name__}).", "image": None}
                verdicts.append(v)
                self.emit({"type": "verdict", "verdict": v})
            self.verdicts, self.judged_by = verdicts, by
            with self._lock:
                self._spans.setdefault("judge", []).append((t - self.t0, time.monotonic() - self.t0))
        self._judged.set()
        return verdicts

    def type_verdict(self, claim: dict) -> dict | None:
        """A unit claim on a type-level plan link (several record plans fit, none picked) is only ever confirmed
        or "couldn't check" (tools.kit.check_type_claim): an ambiguous pairing never gives a mismatch."""
        if claim.get("kind") not in UNIT_KINDS or not claim.get("label") or self.match.get("status") != "matched":
            return None
        with self._plan_lock:
            link = self._plans.get(str(claim["label"]).strip().lower())
        if not link or not link.get("ambiguous"):
            return None
        return _type_parts()[0](claim, link)

    def unit_verdict(self, claim: dict, evs: list[dict]) -> dict | None:
        """A unit claim labelled with a listed unit that has its own record row (unit_links): judged by the judge's
        single-unit rule (that unit's row first, its own scan supporting), on that unit's own evidence only. None
        when the label is no listed unit or nothing was gathered for it (then the claim is judged as before)."""
        label = str(claim.get("label") or "").strip()
        link = self.unit_links.get(label.lower()) if label and claim.get("kind") in UNIT_KINDS else None
        if not link:
            return None
        mine = []
        for e in evs:
            o = e.get("observed") or {}
            if (e.get("status") != "ok" or not o.get("unit_listed") or o.get("feature_check")
                    or str(o.get("plan") or "").strip().lower() != label.lower()):
                continue
            o = {k: v for k, v in o.items() if k != "plan"}
            if e.get("source") == "record":
                mine.append({**e, "observed": {**o, "unit_row": True}})
            elif e.get("source") == "scan":
                mine.append({**e, "observed": {**o, "scan_of": "unit"}})
        if not mine:
            return None
        v = judge({**claim, "label": None, "scope": "unit"}, mine, single_unit=True)
        v["reason"] = str(v.get("reason") or "").replace(" for this home;", f" for unit {link['unit']};")
        return v

    def send_fill_in(self, by: str) -> int | None:
        with self._order:
            if self.facts_sent:
                return None
            self.facts_sent = True
            with self.phase("fill_in"):
                try:
                    facts = self.fill_in()
                except Exception as e:  # extra information only: never turn the check into an error
                    facts = []
                    self.agent_stats["fill_in_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            self.facts = facts
            self.emit({"type": "fill_in", "facts": facts})
            self.fill_in_by = by
            return len(facts)

    def has_location(self) -> bool:
        """A matched building or a map location: the aerial and paper helpers always have something to say."""
        return self.match.get("status") == "matched" or (self.lat is not None and self.lon is not None)

    def run_always(self) -> list[str]:
        """Before judging: the aerial measurement + photos and the county + building record lookups, called here
        (no model) for whichever the agent skipped, in parallel, waited for until ALWAYS_RESERVE_S before the run's
        hard end. A lookup still running then is left out (its evidence would arrive after judging). Returns the
        helpers called."""
        if self.verdicts is not None or self.sealed or not self.has_location():
            return []
        jobs = []
        with self._lock:
            if "measure_building" not in self._done:
                jobs.append(("outside", self.measure_building))
            if "county" not in self._done:
                jobs.append(("paper", self.county_lookup))
            if "record" not in self._done:
                jobs.append(("paper", self.building_record))
        wait = RUN_BUDGET_S - (time.monotonic() - self.t0) - ALWAYS_RESERVE_S
        if not jobs or wait <= 0:
            return []
        names = list(dict.fromkeys(a for a, _ in jobs))
        self.step("orchestrator", "Adding the Vexcel aerial and county checks the planner skipped."
                  if "outside" in names else "Adding the county and building record checks the planner skipped.")
        with self.phase("always"):
            ctx = contextvars.copy_context()
            th = threading.Thread(target=ctx.run, args=(_ctx_map, lambda job: self.tool(job[0], job[1]), jobs,
                                                        len(jobs)), daemon=True, name="rc-always")
            th.start()
            th.join(timeout=wait)
        with self._lock:
            self.direct = list(dict.fromkeys(self.direct + names))
        return names

    def single_unit_page(self) -> bool:
        """True when the page is about one home: the match is a single home, or the page names no floor plan and
        states at most one value per unit kind (beds, baths, size) with at least a bed or bath count, and any
        matched building has at most one floor plan. When unsure: False. A page whose own unit has a row in the
        building record (settle_unit()) is one home."""
        if getattr(self, "unit_row", None):
            return True
        m = self.match or {}
        unit = [c for c in self.claims if c.get("checkable") and c.get("kind") in UNIT_KINDS]
        values: dict[str, set] = {}
        for c in unit:
            values.setdefault(c["kind"], set()).add(str(c.get("value")).replace(",", "").strip())
        if any(len(v) > 1 for v in values.values()):
            return False  # several homes on the page
        if m.get("status") == "matched" and m.get("kind") == "home":
            return True
        if any((c.get("label") or "").strip() for c in self.claims):
            return False  # the page lists floor plans or units
        if not ({"bedrooms", "bathrooms"} & set(values)):
            return False
        if m.get("status") == "matched" and len(m.get("floorplans") or []) > 1:
            return False  # a building with several plans: which one this home is, we cannot tell
        return True

    def close(self, by: str) -> dict:
        """Verdicts (if the agent did not judge), fill in (if not sent), done. Never raises."""
        if self.verdicts is None:
            try:
                self.ensure_claims_and_match()
                self.run_always()
            except Exception as e:
                self.agent_stats["always_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        verdicts = self.judge_all(by)
        self.send_fill_in(by)
        counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("confirmed", "contradicted", "close", "cant_check")}
        stopped = (self.agent_stats or {}).get("stopped")
        top_facts = self.top_facts_soon(verdicts)  # runs alongside the top-mismatch pick: no added wait
        top = self.top_mismatch(verdicts)
        top_facts = top_facts()
        try:  # the headline leads with the same mismatch the report leads with (top_mismatch's pick)
            headline = _headline(verdicts, {c["id"]: c for c in self.claims}, lead=(top or {}).get("claim_id"))
        except Exception:
            headline = "The check finished."
        done = {"type": "done", "run_id": self.run_id, "counts": counts, "headline": headline,
                "report_url": f"/report/{self.run_id}",
                "harness": {"path": self.path, "judged_by": self.judged_by,
                            "fill_in_by": getattr(self, "fill_in_by", None), "stopped": stopped,
                            "helpers": sorted(self.helpers_used), "needed": sorted(self.needed),
                            "direct": list(self.direct), "single_unit": self.single_unit, "unit": self.unit,
                            "top_mismatch": top, "top_facts": top_facts},
                "timing": self.timing()}
        LAST_AGENT_STATS.clear()
        LAST_AGENT_STATS.update(self.agent_stats)
        LAST_AGENT_STATS["vision_calls"] = self.vision_calls
        LAST_AGENT_STATS["done"] = done
        self.emit(done)
        with self._lock:
            self.finished = True
        return done

    def top_mismatch(self, verdicts: list[dict]) -> dict | None:
        """The mismatch that leads the report (harness.top_mismatch.pick_top: {"claim_id", "why", "by"}), or None
        when nothing is off. The model is asked only when the run has one (the agent path) and time is left for it
        (its own CLI login, no API key); else its rule decides. Never raises: a failure here is None."""
        try:
            from harness.top_mismatch import TIMEOUT_S, pick_top
            left = self.time_left() - TOP_MISMATCH_RESERVE_S
            if not AGENT_ENABLED or left < TOP_MISMATCH_MIN_S:
                return pick_top(verdicts, claims=self.claims, llm=_no_model, timeout=0.1, cache=False)
            return pick_top(verdicts, claims=self.claims, llm=TOP_MISMATCH_LLM, timeout=min(TIMEOUT_S, left))
        except Exception:
            return None

    def top_facts_soon(self, verdicts: list[dict]) -> Callable[[], list[dict]]:
        """Start harness.top_facts.pick_top_facts (at most 5 things the listing did not say) in a thread; the returned
        call waits for it (no longer than its own timeout) and gives the list. The model is asked only on the agent
        path with time left, like top_mismatch; a failure or a late answer gives the rule's pick, never an error."""
        facts = list(getattr(self, "facts", None) or [])
        box: dict = {}
        try:
            from harness.top_facts import TIMEOUT_S, pick_top_facts
        except Exception:
            return lambda: []

        def rule() -> list[dict]:
            try:
                return pick_top_facts(facts, verdicts=verdicts, claims=self.claims, llm=_no_model, timeout=0.1,
                                      cache=False)
            except Exception:
                return []
        left = self.time_left() - TOP_MISMATCH_RESERVE_S
        if not facts or not AGENT_ENABLED or left < TOP_MISMATCH_MIN_S:
            out = rule()
            return lambda: out
        budget = min(TIMEOUT_S, left)

        def go():
            try:
                box["out"] = pick_top_facts(facts, verdicts=verdicts, claims=self.claims, llm=TOP_FACTS_LLM,
                                            timeout=budget)
            except Exception:
                pass
        t = threading.Thread(target=contextvars.copy_context().run, args=(go,), daemon=True, name="rc-top-facts")
        t.start()

        def wait() -> list[dict]:
            t.join(budget + 0.5)
            out = box.get("out")
            return out if isinstance(out, list) else rule()
        return wait

    # ---- picture checks: amenities from the air, in-unit features in scan photos (soft, presence only) ----
    def start_pictures(self) -> None:
        """Start the picture checks in a background thread; collect_pictures() adds their Evidence later."""
        jobs = {}
        feats = [(c, amenities.feature_for(c)) for c in self.claims]
        outdoor = [(c, f) for c, f in feats if f and f[2] == "aerial"]
        indoor = [(c, f) for c, f in feats if f and f[2] == "scan"]
        titled = [c for c in self.claims if amenities.title_rooms(c)]
        if outdoor:
            jobs["aerial"] = (self._aerial_features, outdoor)
        if indoor:
            jobs["unit"] = (self._unit_features, indoor)
        if titled and self.match.get("matterport_model_ids"):
            jobs["titles"] = (self._title_rooms, titled)
        if not jobs:
            return
        for name, (_, items) in jobs.items():
            self._pic_claims[name] = [(x[0] if isinstance(x, tuple) else x)["id"] for x in items]

        def work():
            with ThreadPoolExecutor(len(jobs)) as ex:
                futs = {name: ex.submit(self._job, name, fn, items) for name, (fn, items) in jobs.items()}
                for f in futs.values():
                    f.result()
        self._pic_thread = threading.Thread(target=work, daemon=True, name="rc-pictures")
        self._pic_thread.start()

    def _job(self, name: str, fn: Callable, items: list) -> None:
        try:
            evs = fn(items)
        except Exception as e:
            ids = self._pic_claims.get(name, [])
            evs = [make_evidence("aerial" if name == "aerial" else "scan", "error", "The picture check failed.",
                                 {"feature_check": True, "claim_ids": ids, "presence": "unclear"},
                                 detail=f"{type(e).__name__}: {str(e)[:200]}")]
        with self._pic_lock:
            self._pic_evs.extend(evs)
            self._pic_done.add(name)

    def _look(self, paths: list[str], features: dict[str, str], setting: str) -> dict:
        if self.time_left() < VISION_MIN_LEFT_S:
            return {"status": "skipped", "answer": {}, "confidence": 0.0,
                    "notes": "There was no time left in this check to look at the photos."}
        with self._pic_lock:
            self.vision_calls += 1
        return self._vision(self.tb.find_features, paths, features, setting)

    def _feature_evs(self, source: str, items: list, res: dict, images: list[dict], extra: dict, where: str) -> list[dict]:
        """One Evidence per feature key, naming every claim it answers."""
        by_key: dict[str, list[dict]] = {}
        desc = {}
        for c, (key, d, _) in items:
            by_key.setdefault(key, []).append(c)
            desc[key] = d
        out = []
        for key, cs in by_key.items():
            ids = [c["id"] for c in cs]
            obs = {"feature_check": True, "claim_ids": ids, "feature": key, **extra}
            if res.get("status") == "skipped":
                out.append(make_evidence(source, "no_evidence", res.get("notes") or "Not checked.",
                                         {**obs, "presence": "skipped"}, detail="picture check skipped"))
                continue
            a = (res.get("answer") or {}).get(key)
            a = a if isinstance(a, dict) else {}
            presence = a.get("presence") if a.get("presence") in ("seen", "not_seen", "unclear") else "unclear"
            try:
                conf = round(float(a.get("confidence")), 2)
            except (TypeError, ValueError):
                conf = 0.0
            pick = a.get("image")
            best = images[int(pick) - 1] if isinstance(pick, (int, float)) and 1 <= int(pick) <= len(images) else images[0]
            imgs = [best] + [im for im in images[:1] if im is not best]
            ok = res.get("status") == "ok"
            word = {"seen": "saw it", "not_seen": "did not see it", "unclear": "could not tell"}[presence]
            summ = (f"Looking at {where} for {amenities.NICE.get(key, key)}: the vision model {word} "
                    f"(confidence {conf:.2f})." if ok else "The photos could not be read.")
            out.append(make_evidence(source, "ok" if ok else "error", summ,
                                     {**obs, "presence": presence if ok else "unclear", "presence_confidence": conf},
                                     imgs if ok else [],
                                     f"vision model presence check (not a measurement): {desc[key]}; "
                                     f"{str(res.get('notes') or '')[:160]}"))
        return out

    def _aerial_features(self, items: list) -> list[dict]:
        ids = [c["id"] for c, _ in items]
        if self.lat is None or self.lon is None:
            return [make_evidence("aerial", "no_evidence", "We have no map location, so no Vexcel aerial photos.",
                                  {"feature_check": True, "claim_ids": ids, "presence": "unclear"})]
        th = self._property_soon()  # the judge reads it next to the vision model's answer
        oth = self._outlined_soon()  # the outlined photo, alongside the look (no-op unless pool / court / solar)
        try:
            evs = self._aerial_look(items, ids)
        finally:
            th.join(timeout=PROPERTY_WAIT_S)
        if oth is not None:
            oth.join(timeout=OUTLINE_WAIT_S)
        return self._outline_onto(evs)

    def _data_answers(self, items: list) -> list[str] | None:
        """The property keys of these aerial claims when Vexcel property data already shows every one of them
        present in two survey years (stable), so the vision look would add nothing; else None. Waits for the data
        (at most PROPERTY_DECIDE_WAIT_S, while the photos were fetched) only when it could answer every claim:
        a rooftop deck, parking, courtyard or dog run claim always needs the look."""
        keys = [trust.property_key(c) for c, _ in items]
        if not keys or not all(keys) or not self._prop_ready.wait(PROPERTY_DECIDE_WAIT_S):
            return None
        prop = self._prop or {}
        ok = all(isinstance(prop.get(k), dict) and prop[k].get("present") is True and prop[k].get("stable") is True
                 for k in keys)
        return list(dict.fromkeys(keys)) if ok else None

    def _outline_onto(self, evs: list[dict]) -> list[dict]:
        """The outlined photo added, next to the plain photos, to each photo-look Evidence about a feature it boxed
        that the look saw or the property data shows present. Others are left as they are."""
        desc, feats = getattr(self, "_outline_desc", None), getattr(self, "_outline_feats", None) or []
        if not desc:
            return evs
        prop = self._prop or {}
        out = []
        for ev in evs:
            obs = ev.get("observed") or {}
            key = trust.VISION_TO_PROPERTY.get(str(obs.get("feature")))
            present = obs.get("presence") == "seen" or (isinstance(prop.get(key), dict)
                                                        and prop[key].get("present") is True)
            if (ev.get("status") == "ok" and obs.get("feature_check") and key in feats and present
                    and not any(isinstance(im, dict) and im.get("path") == desc["path"] for im in ev.get("images") or [])):
                ev = {**ev, "images": list(ev.get("images") or []) + [dict(desc)]}
            out.append(ev)
        return out

    def _aerial_look(self, items: list, ids: list[str]) -> list[dict]:
        ev = self.aerial_ev()
        answered = self._data_answers(items)
        if answered:
            words = _and([PROPERTY_WORDS.get(k, k) for k in answered])
            self.photo_look_skipped = True
            self.step("outside", f"Vexcel property data already shows the {words}, so the photo look was not needed.")
            return [make_evidence("aerial", "ok", f"Vexcel property data already shows the {words}, so the vision "
                                                  "look at the aerial photos was not needed.",
                                  {"feature_check": True, "photo_look": "not_needed", "answered_by": "property_data",
                                   "features": answered},
                                  detail="Every outdoor amenity claim the photo look would check is marked present in "
                                         "two Vexcel survey years; the vision model was not called.")]
        images = [im for im in ev.get("images") or [] if im.get("path")][:3]
        if ev.get("status") != "ok" or not images:
            return [make_evidence("aerial", "no_evidence", "No Vexcel aerial photos are available here.",
                                  {"feature_check": True, "claim_ids": ids, "presence": "unclear"},
                                  detail=str(ev.get("summary") or "")[:200])]
        res = self._look([im["path"] for im in images], {k: d for _, (k, d, _) in items}, "aerial")
        return self._feature_evs("aerial", items, res, images, {"looked_at": "Vexcel aerial photos"},
                                "the Vexcel aerial photos")

    def _unit_features(self, items: list) -> list[dict]:
        """Labelled claims use their plan's scan; unlabelled ones any unit scan (plans' scans first). Cap 2 scans."""
        out, wanted = [], []
        for c, f in items:
            if c.get("label") and not self.resolve(c["label"])["scan_id"]:
                out.append(make_evidence("scan", "no_evidence", f"No 3D scan of {c['label']} to look at.",
                                         {"feature_check": True, "claim_ids": [c["id"]], "presence": "unclear"}))
        order = [self.resolve(c["label"])["scan_id"] for c, _ in items if c.get("label")]
        if any(not c.get("label") for c, _ in items):
            order.insert(0, self.unit_scan_id)  # the page's own unit's scan first (None when it has none)
            labels = sorted({c.get("label") for c in self.claims if c.get("label")})
            order += [self.resolve(lab)["scan_id"] for lab in labels]
            order += [mid for mid, sm in self.property_scans().items()
                      if sm.get("status") == "ok" and (sm.get("observed") or {}).get("bedrooms") is not None]
        for mid in order:
            if mid and mid not in wanted:
                wanted.append(mid)
        wanted = wanted[:MAX_FEATURE_SCANS]
        unlabeled = [(c, f) for c, f in items if not c.get("label")]

        def one(mid: str) -> list[dict]:
            mine = [(c, f) for c, f in items if c.get("label") and self.resolve(c["label"])["scan_id"] == mid]
            mine += unlabeled
            if not mine:
                return []
            st = self._safe("scan", self.tb.scan_stills, mid, 6)
            images = [im for im in st.get("images") or [] if im.get("path")
                      and not str(im.get("caption", "")).lower().startswith("schematic")][:5]  # room photos only
            title = str((self.property_scans().get(mid) or {}).get("observed", {}).get("model_name") or "")
            if st.get("status") != "ok" or not images:
                return [make_evidence("scan", "no_evidence", "No photos could be taken from the 3D scan.",
                                      {"feature_check": True, "claim_ids": [c["id"] for c, _ in mine],
                                       "presence": "unclear"}, detail=str(st.get("summary") or "")[:200])]
            res = self._look([im["path"] for im in images], {k: d for _, (k, d, _) in mine}, "scan")
            extra = {"looked_at": "3D scan photos", "model_id": mid}
            if title:
                extra["scan_title"] = title
            return self._feature_evs("scan", mine, res, images, extra, "photos from the 3D scan")
        if not wanted and unlabeled:
            out.append(make_evidence("scan", "no_evidence", "This building has no apartment scan to look at.",
                                     {"feature_check": True, "claim_ids": [c["id"] for c, _ in unlabeled],
                                      "presence": "unclear"}))
        if wanted:
            for evs in _ctx_map(one, wanted, len(wanted)):
                out.extend(evs)
        return out

    def _title_rooms(self, claims: list[dict]) -> list[dict]:
        """An amenity room the building has scanned ('Fitness Center') confirms that amenity by name."""
        hits: dict[str, list[dict]] = {}
        titles = {}
        for mid, sm in self.property_scans().items():
            title = str((sm.get("observed") or {}).get("model_name") or "")
            if sm.get("status") != "ok" or not title:
                continue
            titles[mid] = title
            for c in claims:
                if any(re.search(t, title, re.I) for t in amenities.title_rooms(c)):
                    hits.setdefault(mid, []).append(c)
        out = []
        for n, (mid, cs) in enumerate(hits.items()):
            images = []
            if n < MAX_FEATURE_SCANS and self.time_left() > VISION_MIN_LEFT_S:
                st = self._safe("scan", self.tb.scan_stills, mid, 2)
                images = [im for im in st.get("images") or [] if im.get("path")][-1:]  # a panorama, not the plan
            out.append(make_evidence(
                "scan", "ok", f"The building has a 3D scan titled '{titles[mid]}'.",
                {"feature_check": True, "claim_ids": [c["id"] for c in cs], "presence": "seen",
                 "presence_confidence": 1.0, "how": "scan_title", "scan_title": titles[mid], "model_id": mid},
                images, f"Matterport scan title of property-level scan {mid}"))
        return out

    def feature_status(self, which: str) -> str:
        """Short text for the agent: picture-check results so far (they never block the agent)."""
        with self._pic_lock:
            if which not in self._pic_claims:
                return "No claims of this kind to look for in pictures."
            if which not in self._pic_done:
                return ("Picture checks for these claims are running in the background; their results go "
                        "straight to the judge.")
            evs = [e for e in self._pic_evs if set(e["observed"].get("claim_ids") or []) & set(self._pic_claims[which])]
        return "\n".join(f"[{e['source']} {e['status']}] {e['summary'][:160]}" for e in evs) or "Nothing found."

    def collect_pictures(self) -> None:
        """Wait for the picture checks until the budget ends; add their Evidence; unfinished ones = not checked."""
        t = self._pic_thread
        if t is None:
            return
        t.join(timeout=max(0.0, self.time_left() - FINISH_RESERVE_S))
        with self._pic_lock:
            evs, done = list(self._pic_evs), set(self._pic_done)
            self._pic_thread = None
        for ev in evs:
            self.add(ev, ev["source"])
        for name, ids in self._pic_claims.items():
            if name not in done:
                self.add(make_evidence("aerial" if name == "aerial" else "scan", "no_evidence",
                                       "We ran out of time before the photos were checked.",
                                       {"feature_check": True, "claim_ids": ids, "presence": "skipped"}),
                         "aerial" if name == "aerial" else "scan")

    def proof_images(self) -> None:
        """Cheap, cached stills for each claimed floor plan that has a scan, so verdicts carry a picture.
        Runs after the agent too; checks already done are not repeated."""
        plans = sorted({c.get("label") or "" for c in self.claims if c.get("checkable") and c.get("kind") in UNIT_KINDS})
        for plan in plans:
            if self.model_for(plan) and self.time_left() > 5:
                self.scan_photos(plan)

    # ---- "Fill me in": what our sources know that the listing does not say (no model, no network) ----
    def fill_in(self) -> list[dict]:
        from harness.fill_in import build_fill_in  # lazy: harness.fill_in imports harness.graph.plans
        kit = None
        if self.match.get("status") == "matched" and self.match.get("kind", "building") == "building":
            try:
                kit = self.tb.kit_facts(self.match.get("property_id"))
            except Exception:
                kit = None
        return build_fill_in(self.match, list(self.evidence.values()), self.claims, kit,
                             str(self.snapshot.get("text") or ""))

    # ---- fixed plan: every relevant check once, by claim kind -----------------------------------
    def has_ok(self, kind: str, source: str | None = None, plan: str | None = None) -> bool:
        for e in list(self.evidence.values()):
            if e["status"] == "ok" and e["observed"].get(kind) is not None and (source is None or e["source"] == source):
                if plan is None or str(e["observed"].get("plan", plan)).lower() == plan.lower():
                    return True
        return False

    def fixed_plan(self) -> None:
        checkable = [c for c in self.claims if c.get("checkable")]
        kinds = {c.get("kind") for c in checkable}
        plans = sorted({c.get("label") or "" for c in checkable if c.get("kind") in UNIT_KINDS})
        for plan in plans:
            self.scan_rooms(plan)
            if self.model_for(plan):
                self.scan_photos(plan)  # stills as proof images; vision only if the scan gave no room counts
        if "stories" in kinds:
            self.measure_building()
            if not self.has_ok("stories", "aerial"):
                self.count_floors_on_photo()
        if kinds & {"stories", "year_built", "unit_count"}:
            self.county_lookup()
            self.building_record()


def record_amenities(run) -> str:
    """Store the building record's amenities (kit facts `amenities`, named from the record's photo captions) as
    record Evidence with observed.amenities, once per run. '' when the record names none or the kit facts are not
    built. A local file read: no model, no network."""
    m = run.match or {}
    if m.get("status") != "matched" or m.get("kind", "building") != "building":
        return ""
    with run._lock:
        if getattr(run, "_amenities_done", False):
            return ""
        run._amenities_done = True
    if m.get("units_only"):
        return _unit_listing_amenities(run, m)
    try:
        kit = run.tb.kit_facts(m.get("property_id")) or {}
    except Exception:
        return ""
    names = [str(a).strip() for a in (kit.get("amenities") or []) if isinstance(a, str) and 0 < len(a.strip()) <= 40]
    if not names:
        return ""
    ev = make_evidence("record", "ok", "The building record's photos are captioned with these amenities: "
                       + ", ".join(names) + ".", {"amenities": names},
                       detail="Building record photo captions mapped to amenity names (the record's amenity ids "
                              "have no names).")
    return run.line(run.add(ev, "record"))


def _unit_listing_amenities(run, m: dict) -> str:
    """A building known only from its units: amenity names (tools.kit.facts.AMENITIES) that its units' home
    listings mention (match.unit_plans[*].listings / features / amenities, when they carry text such as "Community
    Pool"), as record Evidence with observed.amenities, so they may confirm a building amenity claim by name. A
    name no listing mentions decides nothing (judge.record_names). '' when no listing text names one."""
    try:
        from tools.kit.facts import AMENITIES  # lazy: the fixed amenity names and their patterns
    except Exception:
        return ""
    texts, units = [], []
    for p in m.get("unit_plans") or []:
        if not isinstance(p, dict):
            continue
        got = []
        for key in ("listings", "features", "amenities"):
            for x in p.get(key) if isinstance(p.get(key), list) else []:
                if isinstance(x, str):
                    got.append(x)
                elif isinstance(x, dict):
                    got += [y for k in ("features", "amenities") if isinstance(x.get(k), list)
                            for y in x[k] if isinstance(y, str)]
        got = [" ".join(t.split()) for t in got if 0 < len(t.strip()) <= 80]
        if got:
            texts += got
            units.append(str(p.get("unit")))
    names = [name for name, rx in AMENITIES if any(re.search(rx, t, re.I) for t in texts)]
    if not names:
        return ""
    ev = make_evidence("record", "ok", f"Listings of {_units_words(units)} of this building mention these amenities: "
                       + ", ".join(names) + ".", {"amenities": names, "units_only": True},
                       detail="CoStar hackathon kit home listings of this building's units; their listed features "
                              "mapped to amenity names. A name they do not mention decides nothing.")
    return run.line(run.add(ev, "record"))


def _property_fn(tb: Toolbox) -> Callable | None:
    """The toolbox's property_attributes(lat, lon); for the real toolbox (its aerial tools come from tools.aerial)
    the real one, loaded lazily. None for a toolbox without it (the fake one, unless a test adds it)."""
    fn = getattr(tb, "property_attributes", None)
    if callable(fn):
        return fn
    if getattr(getattr(tb, "aerial_images", None), "__module__", "") == "tools.aerial.aerial":
        try:
            from tools.aerial import property_attributes
            return property_attributes
        except Exception:
            return None
    return None


NO_COVERAGE_TEXT = "Vexcel has no aerial coverage here."


def _no_coverage_error(e: BaseException) -> bool:
    """An exception that means Vexcel has no data at this point (tools.aerial's _NoCoverage: HTTP 403 / 404)."""
    return type(e).__name__ == "_NoCoverage" or bool(re.search(r"answered (?:403|404)", str(e)))


OUTLINE_NAMES = {"pool": "pool", "sport_court": "sport court", "solar": "solar panels"}
PROPERTY_WORDS = {**OUTLINE_NAMES, "hot_tub": "hot tub", "playground": "playground"}
_OUTLINED_BACK = {v: k for k, v in OUTLINE_NAMES.items()}


def _outlined_fn(tb: Toolbox) -> Callable | None:
    """The toolbox's outlined_image(lat, lon); for the real toolbox (its aerial tools come from tools.aerial) the
    real one, loaded lazily. None for a toolbox without it (the fake one, unless a test adds it)."""
    fn = getattr(tb, "outlined_image", None)
    if callable(fn):
        return fn
    if getattr(getattr(tb, "aerial_images", None), "__module__", "") == "tools.aerial.aerial":
        try:
            from tools.aerial import outlined_image
            return outlined_image
        except Exception:
            return None
    return None


def _outlined_features(im: dict) -> list[str]:
    """Property keys (pool, sport_court, solar) the outlined photo boxed: its "features" list when it has one, else
    read from its caption ("..., detected pool, sport court outlined")."""
    raw = im.get("features")
    if isinstance(raw, list):
        found = {str(x) for x in raw}
    else:
        m = re.search(r"detected (.+?) outlined", str(im.get("caption") or ""))
        parts = re.split(r",\s*|\s+and\s+", m.group(1)) if m else []
        found = {_OUTLINED_BACK[p.strip().lower()] for p in parts if p.strip().lower() in _OUTLINED_BACK}
    return [k for k in OUTLINE_KEYS if k in found]


def _outlined_caption(caption: str, names: str) -> str:
    """'Top-down Vexcel aerial photo (2026-01-02): pool detected near the address, outlined (...)'."""
    m = re.search(r"\((\d{4}-\d\d-\d\d)\)", caption)
    return (f"Top-down Vexcel aerial photo{f' ({m.group(1)})' if m else ''}: {names} detected near the address, "
            f"outlined (the 120 m detection square can take in a neighbouring lot)")


def _unit_fns(tb: Toolbox) -> dict | None:
    """{"find_unit", "unit_scan"}: the toolbox's unit lookups; for the real toolbox (its match comes from tools.kit)
    the real ones from tools.kit.units, loaded lazily and only once the unit store is built. None for a toolbox
    without them (the fake one, unless a test adds them). Local file reads only: no network."""
    find = getattr(tb, "find_unit", None)
    if callable(find):
        scan = getattr(tb, "unit_scan", None)
        return {"find_unit": find, "unit_scan": scan if callable(scan) else (lambda *a, **k: None)}
    if getattr(getattr(tb, "match_address", None), "__module__", "") == "tools.kit.match":
        try:
            from tools.kit import units
            if units.store_ready():
                return {"find_unit": units.find_unit, "unit_scan": units.unit_scan}
        except Exception:
            return None
    return None


def _row_says(row: dict) -> tuple[dict, list[str]]:
    """A unit row's numbers ({"beds", "baths", "sqft"}) as observed keys (bedrooms / bathrooms / area_sqft) and
    words ("2 beds", "1 bath", "1,050 sq ft"); a missing, 0-bath or 0-size value is left out."""
    nums: dict[str, Any] = {}
    bits = []
    beds, baths, sqft = row.get("beds"), row.get("baths"), row.get("sqft")
    if isinstance(beds, (int, float)) and not isinstance(beds, bool):
        nums["bedrooms"] = beds
        bits.append("a studio" if beds == 0 else f"{beds:g} bed" + ("s" if beds != 1 else ""))
    if isinstance(baths, (int, float)) and not isinstance(baths, bool) and baths > 0:
        nums["bathrooms"] = baths
        bits.append(f"{baths:g} bath" + ("s" if baths != 1 else ""))
    if isinstance(sqft, (int, float)) and not isinstance(sqft, bool) and sqft > 0:
        nums["area_sqft"] = sqft
        bits.append(f"{int(sqft):,} sq ft" if float(sqft).is_integer() else f"{sqft:,.1f} sq ft")
    return nums, bits


def _units_words(units: list[str]) -> str:
    """['1310'] -> 'unit 1310'; ['1204', '1310'] -> 'units 1204 and 1310'."""
    units = [str(u) for u in units]
    return f"unit {units[0]}" if len(units) == 1 else "units " + _and(units)


def _unit_scan_of(match: dict, unit: str) -> str | None:
    """The scan match.unit_scans files under this unit (a building known only from its units), else None."""
    want = str(unit or "").strip().lower()
    return next((s for s, u in ((match or {}).get("unit_scans") or {}).items()
                 if isinstance(s, str) and s and str(u).strip().lower() == want), None)


def _example_scan(ev: Any, unit: str) -> Any:
    """A scan of another unit of the building, shown as an example only: its numbers move under
    observed.example_unit (with the unit) and observed.example_for is set, so the judge never uses it."""
    if not isinstance(ev, dict) or ev.get("status") != "ok":
        return ev
    obs = dict(ev.get("observed") or {})
    nums = {k: obs.pop(k) for k in UNIT_KINDS if k in obs}
    obs["example_unit"] = {"unit": unit, **{k: v for k, v in nums.items() if v is not None}}
    obs["example_for"] = "this building"
    return {**ev, "observed": obs,
            "summary": f"3D scan of unit {unit} of this building, an example home only: {ev.get('summary') or ''}".strip()}


def _plan_keys(name: Any) -> set[str]:
    """Unit-style keys a record plan name could be confused with: 'Plan A1' / 'A1 - 1 Bed' / 'B2  Plan' -> 'a1' /
    'b2'; the whole name too ('theaspen'). Names that are no code ('2 Bedrooms') give only the whole name."""
    s = str(name or "").strip().lower()
    if not s:
        return set()
    s = re.sub(r"^(?:floor\s?plan|plan|model|unit|residence)\s+", "", s)
    s = re.sub(r"\s+plan$", "", s)
    out = {re.sub(r"[^a-z0-9]", "", s)}
    code = re.split(r"\s+[-–(]|\s*\(", s)[0].strip()
    if code and " " not in code:
        out.add(re.sub(r"(?<!\d)0+(?=\d)", "", re.sub(r"[^a-z0-9]", "", code)))
    return {k for k in out if k}


_PROPERTY_SAY = (("pool", "a pool"), ("hot_tub", "a hot tub"), ("sport_court", "a sport court"),
                 ("playground", "a playground"), ("solar", "solar panels"))


def _property_summary(prop: dict) -> str:
    """'Vexcel property data (survey of 2025-12-19) shows a pool (about 1,900 sq ft); it shows no hot tub, ...'."""
    yes, no = [], []
    for key, say in _PROPERTY_SAY:
        f = prop.get(key)
        if not isinstance(f, dict) or not isinstance(f.get("present"), bool):
            continue
        if f["present"]:
            area = f.get("area_sqft") if key == "pool" else None
            yes.append(say + (f" (about {int(area):,} sq ft)" if isinstance(area, (int, float)) and area > 0 else ""))
        else:
            no.append(say.split(" ", 1)[1] if say.startswith("a ") else say)
    when = f" (survey of {prop['captured']})" if prop.get("captured") else ""
    parts = []
    if yes:
        parts.append("shows " + _and(yes))
    if no:
        parts.append(("it shows no " if yes else "shows no ") + _and(no, "or"))
    return (f"Vexcel property data{when} " + "; ".join(parts) + "." if parts
            else f"Vexcel property data{when} has no outdoor features marked for this parcel.")


def _and(xs: list[str], word: str = "and") -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + f" {word} " + xs[-1]


def _ctx_map(fn: Callable, items: list, workers: int) -> list:
    """ThreadPoolExecutor.map that carries the caller's context (the run's chat model) into each worker."""
    if not items:
        return []
    with ThreadPoolExecutor(max(1, min(workers, len(items)))) as ex:
        futs = [ex.submit(contextvars.copy_context().run, fn, x) for x in items]
        return [f.result() for f in futs]


def _link_plan():
    from tools.kit import link_plan  # lazy: the harness imports without the kit (fake toolbox tests)
    return link_plan


def _type_parts():
    from tools.kit import check_type_claim, type_evidence
    return check_type_claim, type_evidence


def _scan_cached(model_id: str) -> bool:
    try:
        return cachectl.hit(Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "scan" / model_id / "summary.json")
    except Exception:
        return False


def _stills_cached(model_id: str | None) -> bool:
    try:
        return bool(model_id) and cachectl.hit(Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "scan" / model_id
                                               / "stills.json")
    except Exception:
        return False


def helpers_needed(claims: list[dict], match: dict | None = None) -> dict[str, str]:
    """Which helpers the claim kinds call for, and why. Every source that applies is listed, so the
    orchestrator does not stop at one (stories: the roof height AND the paper records). With a usable location
    (a matched building, or a map location) outside and paper are always needed: the Vexcel roof height and
    photos, the county record and the building record go in every report, whatever the claims are."""
    checkable = [c for c in claims if c.get("checkable")]
    kinds = {c.get("kind") for c in checkable}
    feats = [amenities.feature_for(c) for c in checkable]
    need: dict[str, str] = {}
    inside = []
    if kinds & set(UNIT_KINDS):
        inside.append("floor plan bedrooms/bathrooms/area")
    if any(f and f[2] == "scan" for f in feats):
        inside.append("in-unit features on scan photos")
    if any(amenities.title_rooms(c) for c in checkable):
        inside.append("amenity rooms the building has scanned")
    if inside:
        need["inside"] = ", ".join(inside)
    outside = []
    if "stories" in kinds:
        outside.append("roof height for floors")
    if any(f and f[2] == "aerial" for f in feats):
        outside.append("outdoor amenities on aerial photos")
    m = match or {}
    located = m.get("status") == "matched" or (m.get("lat") is not None and m.get("lon") is not None)
    if located and not outside:
        outside.append("Vexcel roof height and aerial photos, always")
    if outside:
        need["outside"] = ", ".join(outside)
    paper = sorted(kinds & {"stories", "year_built", "unit_count"})
    if paper:
        need["paper"] = "county and building records for " + ", ".join(k.replace("_", " ") for k in paper)
    elif located:
        need["paper"] = "county and building records, always"
    return need


def _overstated(v: dict, claim: dict) -> bool:
    """True when the listing claims more than was found (2 bedrooms listed, 1 found): lead with those.
    A studio listed as 0 bedrooms that turns out to have 1 is a contradiction, but not the headline."""
    try:
        claimed = float(str(claim.get("value")).replace(",", ""))
    except (TypeError, ValueError):
        return False
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v.get("found") or ""))
    return bool(m) and claimed > float(m.group().replace(",", ""))


def _headline(verdicts: list[dict], claims: dict[str, dict], lead: Any = None) -> str:
    """lead: the claim id harness.top_mismatch picked; a contradicted verdict for it leads the headline. Without
    one, the old order (kind severity, then overstated first) picks."""
    bad = [v for v in verdicts if v["verdict"] == "contradicted"]
    if bad:
        bad.sort(key=lambda v: (0 if lead is not None and v["claim_id"] == lead else 1,
                                SEVERITY.index(claims[v["claim_id"]]["kind"])
                                if claims[v["claim_id"]]["kind"] in SEVERITY else 99,
                                0 if _overstated(v, claims[v["claim_id"]]) else 1))
        v = bad[0]
        src = v.get("source")
        verb = "measures" if claims[v["claim_id"]]["kind"] == "area_sqft" else VERB.get(src, "shows")
        # Stories come from the measured roof height, not from reading the photo: say so.
        who = ("the measured roof height from Vexcel aerial imagery" if src == "aerial"
               else SOURCE_NAME.get(src, "our check"))
        text = f"Listing says {v['claimed']}, but {who} {verb} {v['found']}"
        more = len(bad) - 1
        return text + (f"; {more} more claim{'s' if more != 1 else ''} did not match." if more else ".")
    checked = [v for v in verdicts if v["verdict"] != "cant_check"]
    if not checked:
        return "Nothing on this page could be checked against our sources."
    close = sum(v["verdict"] == "close" for v in verdicts)
    if close:
        return f"No claim was clearly wrong; {close} came close but not exact."
    return f"All {len(checked)} claims we could check matched our sources."


def _no_model(prompt: str) -> str:
    raise RuntimeError("no model for this run")


def _broken_toolbox() -> Toolbox:
    def fail(*a, **k):
        raise RuntimeError("tool unavailable")
    return Toolbox(**{f: fail for f in Toolbox.__dataclass_fields__})


def run_check(snapshot: dict, emit: Callable[[dict], Any], toolbox: Toolbox | None = None) -> dict:
    setup_error = None
    try:
        tb = toolbox or Toolbox.real()
    except Exception as e:  # cannot happen with Toolbox.real(), which catches import errors
        setup_error, tb = f"setup failed: {type(e).__name__}: {e}", _broken_toolbox()
    run = Run(snapshot, emit, tb)
    run.emit({"type": "run_started", "run_id": run.run_id, "address": run.address})
    if setup_error:
        run.error(setup_error)

    if os.environ.get("RC_LEGACY") == "1":
        from .legacy import run_check_legacy
        try:
            return run_check_legacy(run)
        except Exception as e:  # the close below still sends verdicts and done
            run.error(f"legacy run failed: {type(e).__name__}: {str(e)[:300]}")
            return run.close(by="close")

    # Everything runs inside the deep agent: claims, match, the three helpers, the judge, fill in.
    if AGENT_ENABLED:
        with run.phase("agent"):
            try:
                from .agent import run_agent
                run.agent_stats = run_agent(run, hard_s=RUN_BUDGET_S - CLOSE_RESERVE_S)
            except Exception as e:
                run.error(f"The check's planner stopped: {type(e).__name__}: {str(e)[:300]}")
                run.agent_stats = {"error": traceback.format_exc()[-1500:], "stopped": "error"}
    else:
        run.agent_stats = {"stopped": "agent switched off"}
    stopped = run.agent_stats.get("stopped")
    if run.verdicts is None:
        run.step("orchestrator", "The planner stopped before judging, so claims without evidence are marked "
                                 "as not checked." if stopped else "Judging what was gathered.")
    # The close: whatever the agent did not get to. No checks run here, only the judge's rules.
    try:
        return run.close(by="close")
    except Exception as e:  # last resort: still a done event
        run.error(f"closing the check failed: {type(e).__name__}: {e}")
        counts = {k: 0 for k in ("confirmed", "contradicted", "close", "cant_check")}
        done = {"type": "done", "run_id": run.run_id, "counts": counts, "headline": "The check finished.",
                "report_url": f"/report/{run.run_id}"}
        run.emit(done)
        return done
