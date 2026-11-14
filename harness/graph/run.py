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

from . import amenities, plans
from .judge import SOURCE_NAME, VERB, judge
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
        self._stills_fetched = 0
        self._aerial: dict | None = None
        self._aerial_lock = threading.Lock()
        self._spans: dict[str, list[tuple[float, float]]] = {}
        self._late: dict[str, dict] = {}       # evidence that arrived after judging started (not used)

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
        if not label:
            r = {"label": label, "kit": None, "kit_plans": [], "how": None, "ambiguous": False, "scan_id": None,
                 "scan_how": None, "scan_title": None, "note": ""}
            ids = self.match.get("matterport_model_ids") or []
            if len(ids) == 1:
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

    def scan_rooms(self, plan: str = "", agent: str = "inside") -> str:
        plan = self.canon_plan(plan)
        name = plan or "this unit"

        def body():
            r = self.resolve(plan)
            out = []
            example = bool(r.get("ambiguous"))  # type-level: several record plans fit, none was picked
            if plan and example:
                out.append(self.line(self.add(_type_parts()[1](r, self.match), "record")))
            elif plan and r["kit"]:
                out.append(self.line(self.add(self._plan_record(plan, r), "record")))
            elif plan and self.match.get("status") == "matched":
                out.append(self.line(self.add(make_evidence(
                    "record", "no_evidence", f"{plan} is not in the building's floor plan records.",
                    {"plan": plan, "plan_missing": True},
                    detail="No kit floor plan has this name, or the same beds and baths within 8% of its size."),
                    "record")))
            elif not plan and self.match.get("status") == "matched" and self.match.get("floorplans"):
                out.append(self.line(self.add(self._all_plans(), "record")))
            if not r["scan_id"]:
                out.append(self.line(self.add(make_evidence("scan", "no_evidence", f"No 3D scan was found for {name}."),
                                              "scan", plan)))
                return "\n".join(out)
            ev = self._safe("scan", self.tb.scan_summary, r["scan_id"])
            if r["note"] and isinstance(ev, dict) and ev.get("status") == "ok":
                ev = {**ev, "observed": {**(ev.get("observed") or {}), "plan_match": r["note"]}}
            if example and isinstance(ev, dict):
                # an example of the kind of home only: not tied to the listing's plan, so it never decides its claims
                ev = {**ev, "observed": {**(ev.get("observed") or {}), "example_for": plan}}
                out.append(self.line(self.add(ev, "scan")))
            else:
                out.append(self.line(self.add(ev, "scan", plan)))
            return "\n".join(out)
        return self._check(f"scan_rooms:{plan.lower()}", agent, f"Checking the floor plan {name}: its scan or record.",
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

    def aerial_ev(self) -> dict:
        """The aerial photos of this location, fetched once per run (roof check and amenity check share them)."""
        with self._aerial_lock:
            if self._aerial is None:
                self._aerial = self._safe("aerial", self.tb.aerial_images, self.lat, self.lon)
            return self._aerial

    def aerial_photos(self, agent: str = "outside") -> str:
        def body():
            if self.lat is None or self.lon is None:
                return self._no_location()
            return self.line(self.add(self.aerial_ev(), "aerial"))
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
        self.needed = helpers_needed(self.claims, self.match)
        with self._order:
            self.claims_ready = True
            self._flush()
        return self.claims_text()

    def claims_text(self) -> str:
        checkable = [c for c in self.claims if c.get("checkable")]
        rows = [f"{c['id']} {c.get('kind')}={json.dumps(c.get('value'))}" + (f" [{c['label']}]" if c.get("label") else "")
                for c in checkable]
        need = "; ".join(f"{h} ({why})" for h, why in self.needed.items()) or "none (nothing measurable)"
        return (f"{len(self.claims)} claims, {len(checkable)} checkable:\n" + "\n".join(rows)
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
            if self.match_ready or getattr(self, "_matching", False):
                return self.match_text() + " (already matched)"
            self._matching = True
        self.step("matcher", f"Finding the building at {self.address or 'this address'}.")
        try:
            with self.phase("match"):
                self.do_match()
        except Exception as e:
            self.error(f"address match failed: {type(e).__name__}: {e}")
        if self.claims_ready:
            self.needed = helpers_needed(self.claims, self.match)
        with self._order:
            self.match_ready = True
            self._flush()  # sent right after the claims event, never before it
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
            return (f"Not judged yet: these helpers are needed and were not asked: {', '.join(missing)}. "
                    "Ask them now with the task tool (all in one message), then call judge_claims again.")
        verdicts = self.judge_all(by="agent")
        counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("confirmed", "contradicted", "close", "cant_check")}
        return "Judged: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()) + "."

    def fill_in_tool(self) -> str:
        """Orchestrator tool: "Fill me in" facts, sent after the verdicts."""
        if not self._judged.wait(timeout=max(0.0, min(15.0, self.time_left()))):
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
                           + ("the aerial photos." if which == "aerial" else "the 3D scan photos."), body)

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
            verdicts = []
            for c in self.claims:
                try:
                    v = self.type_verdict(c) or judge(c, evs)
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
            self.emit({"type": "fill_in", "facts": facts})
            self.fill_in_by = by
            return len(facts)

    def close(self, by: str) -> dict:
        """Verdicts (if the agent did not judge), fill in (if not sent), done. Never raises."""
        verdicts = self.judge_all(by)
        self.send_fill_in(by)
        counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("confirmed", "contradicted", "close", "cant_check")}
        try:
            headline = _headline(verdicts, {c["id"]: c for c in self.claims})
        except Exception:
            headline = "The check finished."
        stopped = (self.agent_stats or {}).get("stopped")
        done = {"type": "done", "run_id": self.run_id, "counts": counts, "headline": headline,
                "report_url": f"/report/{self.run_id}",
                "harness": {"path": self.path, "judged_by": self.judged_by,
                            "fill_in_by": getattr(self, "fill_in_by", None), "stopped": stopped,
                            "helpers": sorted(self.helpers_used), "needed": sorted(self.needed)},
                "timing": self.timing()}
        LAST_AGENT_STATS.clear()
        LAST_AGENT_STATS.update(self.agent_stats)
        LAST_AGENT_STATS["vision_calls"] = self.vision_calls
        LAST_AGENT_STATS["done"] = done
        self.emit(done)
        with self._lock:
            self.finished = True
        return done

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
            return [make_evidence("aerial", "no_evidence", "We have no map location, so no aerial photos.",
                                  {"feature_check": True, "claim_ids": ids, "presence": "unclear"})]
        ev = self.aerial_ev()
        images = [im for im in ev.get("images") or [] if im.get("path")][:3]
        if ev.get("status") != "ok" or not images:
            return [make_evidence("aerial", "no_evidence", "No aerial photos are available here.",
                                  {"feature_check": True, "claim_ids": ids, "presence": "unclear"},
                                  detail=str(ev.get("summary") or "")[:200])]
        res = self._look([im["path"] for im in images], {k: d for _, (k, d, _) in items}, "aerial")
        return self._feature_evs("aerial", items, res, images, {"looked_at": "aerial photos"}, "the aerial photos")

    def _unit_features(self, items: list) -> list[dict]:
        """Labelled claims use their plan's scan; unlabelled ones any unit scan (plans' scans first). Cap 2 scans."""
        out, wanted = [], []
        for c, f in items:
            if c.get("label") and not self.resolve(c["label"])["scan_id"]:
                out.append(make_evidence("scan", "no_evidence", f"No 3D scan of {c['label']} to look at.",
                                         {"feature_check": True, "claim_ids": [c["id"]], "presence": "unclear"}))
        order = [self.resolve(c["label"])["scan_id"] for c, _ in items if c.get("label")]
        if any(not c.get("label") for c, _ in items):
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
        return (Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "scan" / model_id / "summary.json").exists()
    except Exception:
        return False


def _stills_cached(model_id: str | None) -> bool:
    try:
        return bool(model_id) and (Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "scan" / model_id
                                   / "stills.json").exists()
    except Exception:
        return False


def helpers_needed(claims: list[dict], match: dict | None = None) -> dict[str, str]:
    """Which helpers the claim kinds call for, and why. Every source that applies is listed, so the
    orchestrator does not stop at one (stories: the roof height AND the paper records)."""
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
    if outside:
        need["outside"] = ", ".join(outside)
    paper = sorted(kinds & {"stories", "year_built", "unit_count"})
    if paper:
        need["paper"] = "county and building records for " + ", ".join(k.replace("_", " ") for k in paper)
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


def _headline(verdicts: list[dict], claims: dict[str, dict]) -> str:
    bad = [v for v in verdicts if v["verdict"] == "contradicted"]
    if bad:
        bad.sort(key=lambda v: (SEVERITY.index(claims[v["claim_id"]]["kind"])
                                if claims[v["claim_id"]]["kind"] in SEVERITY else 99,
                                0 if _overstated(v, claims[v["claim_id"]]) else 1))
        v = bad[0]
        src = v.get("source")
        verb = "measures" if claims[v["claim_id"]]["kind"] == "area_sqft" else VERB.get(src, "shows")
        # Stories come from the measured roof height, not from reading the photo: say so.
        who = "the measured roof height" if src == "aerial" else SOURCE_NAME.get(src, "our check")
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
