"""run_check(snapshot, emit, toolbox=None) -> done event (docs/CONTRACT.md 2.6).

Deterministic preamble (claims, match) -> deep agent plans and gathers evidence -> deterministic close
(judge every claim, done). If the agent fails, runs out of budget or gathers nothing, a fixed plan runs
every relevant check once, so the user always gets verdicts.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from typing import Any, Callable

from rc_common import evidence as make_evidence

from .judge import SOURCE_NAME, VERB, judge
from .toolbox import Toolbox

AGENT_ENABLED = True  # tests switch the deep agent off to exercise the fixed plan
LAST_AGENT_STATS: dict = {}  # stats of the latest deep-agent run (model calls, delegations, trace)
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
SEVERITY = ["bedrooms", "area_sqft", "bathrooms", "stories", "unit_count", "year_built"]
RUN_BUDGET_S = 55.0     # whole-run target; vision calls are skipped once less than VISION_MIN_LEFT_S remains
VISION_MIN_LEFT_S = 15.0


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

    def time_left(self) -> float:
        return RUN_BUDGET_S - (time.monotonic() - self.t0)

    # ---- events -------------------------------------------------------------------------------
    def emit(self, event: dict) -> None:
        with self._lock:
            try:
                self._emit(event)
            except Exception:
                pass  # a broken listener must not kill the run

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
        with self._lock:
            eid = f"E{len(self.evidence) + 1}"
            self.evidence[eid] = ev
        self.emit({"type": "evidence", "evidence": ev})
        return eid

    def line(self, eid: str) -> str:
        """Short text an agent sees: id + status + one-line summary + small observed dict."""
        ev = self.evidence[eid]
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
    def model_for(self, plan: str | None) -> str | None:
        fps = self.match.get("floorplans") or []
        if plan:
            p = plan.strip().lower()
            for fp in fps:
                if str(fp.get("name") or "").strip().lower() == p and fp.get("matterport_model_id"):
                    return fp["matterport_model_id"]
            pre = re.compile(r"^" + re.escape(p) + r"(\b|\s|$)")
            for fp in fps:
                if pre.match(str(fp.get("name") or "").strip().lower()) and fp.get("matterport_model_id"):
                    return fp["matterport_model_id"]
            for fp in fps:
                if p in str(fp.get("name") or "").lower() and fp.get("matterport_model_id"):
                    return fp["matterport_model_id"]
            return None
        ids = self.match.get("matterport_model_ids") or []
        return ids[0] if len(ids) == 1 else None

    def canon_plan(self, plan: str) -> str:
        """Map whatever plan name the agent passes (e.g. the kit's 'Plan E 1 Bedroom + Den') back to the claim
        label that resolves to the same scan ('Plan E'), so evidence and claims share one plan tag."""
        plan = (plan or "").strip()
        mid = self.model_for(plan) if plan else None
        if not mid:
            return plan
        for label in sorted({c.get("label") for c in self.claims if c.get("label")}):
            if label.strip().lower() == plan.lower() or self.model_for(label) == mid:
                return label
        return plan

    def scan_rooms(self, plan: str = "", agent: str = "inside") -> str:
        plan = self.canon_plan(plan)
        name = plan or "this unit"

        def body():
            mid = self.model_for(plan)
            if not mid:
                return self.line(self.add(make_evidence("scan", "no_evidence", f"No 3D scan was found for {name}."),
                                          "scan", plan))
            return self.line(self.add(self._safe("scan", self.tb.scan_summary, mid), "scan", plan))
        return self._check(f"scan_rooms:{plan.lower()}", agent, f"Checking the 3D scan for {name}.", body)

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
            ev = make_evidence("scan", "ok" if ok else "error", summ if ok else "The photos could not be read.",
                               obs if ok else {}, self.evidence[sid]["images"][:2],
                               f"vision model on scan stills, confidence {res.get('confidence')}; {str(res.get('notes'))[:150]}")
            return self.line(sid) + "\n" + self.line(self.add(ev, "scan", plan))
        return self._check(f"scan_photos:{plan.lower()}:{claim.lower()}", agent,
                           f"Looking at photos from the 3D scan of {name}.", body)

    def _vision(self, fn, *args) -> dict:
        if self.time_left() < VISION_MIN_LEFT_S:
            return {"status": "error", "answer": {}, "confidence": 0.0,
                    "notes": "skipped: the run's time budget is nearly used up"}
        try:
            res = fn(*args)
            return res if isinstance(res, dict) else {"status": "error", "notes": "bad vision reply"}
        except Exception as e:
            return {"status": "error", "answer": {}, "confidence": 0.0, "notes": f"{type(e).__name__}: {e}"[:200]}

    def _no_location(self) -> str:
        return self.line(self.add(make_evidence("aerial", "no_evidence", "We have no map location for this address."),
                                  "aerial"))

    def aerial_photos(self, agent: str = "outside") -> str:
        def body():
            if self.lat is None or self.lon is None:
                return self._no_location()
            return self.line(self.add(self._safe("aerial", self.tb.aerial_images, self.lat, self.lon), "aerial"))
        return self._check("aerial_photos", agent, "Fetching aerial photos of the building.", body)

    def measure_building(self, agent: str = "outside") -> str:
        def body():
            if self.lat is None or self.lon is None:
                return self._no_location()
            h = self.line(self.add(self._safe("aerial", self.tb.building_height, self.lat, self.lon), "aerial"))
            return h + "\n" + self.aerial_photos(agent)
        return self._check("measure_building", agent, "Measuring the roof height from aerial imagery.", body)

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
                return "No aerial photo is available to count floors on."
            res = self._vision(self.tb.count_floors, [im["path"] for im in use])
            a = res.get("answer") or {}
            ok = res.get("status") == "ok" and isinstance(a.get("stories"), (int, float))
            ev = make_evidence("aerial", "ok" if ok else "error",
                               f"For illustration only: window rows on the angled aerial photo suggest about "
                               f"{a.get('stories')} floors (a photo count never decides the verdict)." if ok
                               else "Floors could not be counted on the aerial photo.",
                               {"photo_floor_count": a.get("stories"), "illustrative": True,
                                "how_counted": str(a.get("how_counted", ""))[:150]} if ok else {},
                               use, f"vision model floor count (illustrative), confidence {res.get('confidence')}")
            return self.line(self.add(ev, "aerial"))
        return self._check("count_floors", agent, "Counting floors on the angled aerial photo.", body)

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
            return self.line(self.add(ev, "record"))
        return self._check("record", agent, "Reading the building record.", body)

    def proof_images(self) -> None:
        """Cheap, cached stills for each claimed floor plan that has a scan, so verdicts carry a picture.
        Runs after the agent too; checks already done are not repeated."""
        plans = sorted({c.get("label") or "" for c in self.claims if c.get("checkable") and c.get("kind") in UNIT_KINDS})
        for plan in plans:
            if self.model_for(plan) and self.time_left() > 5:
                self.scan_photos(plan)

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


def _headline(verdicts: list[dict], claims: dict[str, dict]) -> str:
    bad = [v for v in verdicts if v["verdict"] == "contradicted"]
    if bad:
        bad.sort(key=lambda v: SEVERITY.index(claims[v["claim_id"]]["kind"])
                 if claims[v["claim_id"]]["kind"] in SEVERITY else 99)
        v = bad[0]
        src = v.get("source")
        verb = "measures" if claims[v["claim_id"]]["kind"] == "area_sqft" else VERB.get(src, "shows")
        text = f"Listing says {v['claimed']}, but {SOURCE_NAME.get(src, 'our check')} {verb} {v['found']}"
        more = len(bad) - 1
        return text + (f"; {more} more claim{'s' if more != 1 else ''} did not match." if more else ".")
    checked = [v for v in verdicts if v["verdict"] != "cant_check"]
    if not checked:
        return "Nothing on this page could be checked against our sources."
    close = sum(v["verdict"] == "close" for v in verdicts)
    if close:
        return f"No claim was clearly wrong; {close} came close but not exact."
    return f"All {len(checked)} claims we could check matched our sources."


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

    # 1. Deterministic preamble.
    try:
        run.step("claims", "Reading what the listing claims.")
        claims = run.tb.extract_claims(run.snapshot) or []
        run.claims = [c for c in claims if isinstance(c, dict) and c.get("id")]
    except Exception as e:
        run.error(f"reading claims failed: {type(e).__name__}: {e}")
    run.emit({"type": "claims", "claims": run.claims})
    try:
        run.step("matcher", f"Finding the building at {run.address or 'this address'}.")
        m = run.tb.match_address(run.address)
        if isinstance(m, dict) and m.get("status") in ("matched", "no_match"):
            run.match = m
        if run.match.get("lat") is None or run.match.get("lon") is None:
            g = run.tb.geocode(run.address) if run.address else None
            if isinstance(g, dict) and g.get("lat") is not None:
                run.match = {**run.match, "lat": g["lat"], "lon": g["lon"]}
        run.lat, run.lon = run.match.get("lat"), run.match.get("lon")
    except Exception as e:
        run.error(f"address match failed: {type(e).__name__}: {e}")
    run.emit({"type": "match", "match": run.match})

    # 2. The deep agent plans and gathers evidence; the fixed plan covers failure or an empty result.
    if any(c.get("checkable") for c in run.claims):
        reason = None
        if AGENT_ENABLED:
            try:
                from .agent import run_agent
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
            try:
                run.fixed_plan()
            except Exception as e:
                run.error(f"standard checks failed: {type(e).__name__}: {e}")
        try:
            run.proof_images()
        except Exception as e:
            run.error(f"proof images failed: {type(e).__name__}: {e}")
    else:
        run.step("orchestrator", "Nothing on this page can be measured, so no checks are needed.")

    LAST_AGENT_STATS.clear()
    LAST_AGENT_STATS.update(run.agent_stats)

    # 3. Deterministic close.
    run.step("judge", "Comparing every claim with the evidence.")
    evs = list(run.evidence.values())
    verdicts = []
    for c in run.claims:
        try:
            v = judge(c, evs)
        except Exception as e:
            v = {"claim_id": c.get("id"), "verdict": "cant_check", "claimed": str(c.get("quote") or ""),
                 "found": "no data", "source": None, "reason": f"This claim could not be judged ({type(e).__name__}).",
                 "image": None}
        verdicts.append(v)
        run.emit({"type": "verdict", "verdict": v})
    counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("confirmed", "contradicted", "close", "cant_check")}
    try:
        headline = _headline(verdicts, {c["id"]: c for c in run.claims})
    except Exception:
        headline = "The check finished."
    done = {"type": "done", "run_id": run.run_id, "counts": counts, "headline": headline,
            "report_url": f"/report/{run.run_id}"}
    run.emit(done)
    return done
