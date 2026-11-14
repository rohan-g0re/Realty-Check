"""run_check(snapshot, emit, toolbox=None) -> done event (docs/CONTRACT.md 2.6).

Deterministic preamble (claims, match) -> deep agent plans and gathers evidence -> deterministic close
(judge every claim, done). If the agent fails, runs out of budget or gathers nothing, a fixed plan runs
every relevant check once, so the user always gets verdicts.
"""
from __future__ import annotations

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

AGENT_ENABLED = True  # tests switch the deep agent off to exercise the fixed plan
LAST_AGENT_STATS: dict = {}  # stats of the latest deep-agent run (model calls, delegations, trace)
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")
SEVERITY = ["bedrooms", "area_sqft", "bathrooms", "stories", "unit_count", "year_built"]
RUN_BUDGET_S = 60.0     # whole-run target; vision calls are skipped once less than VISION_MIN_LEFT_S remains
VISION_MIN_LEFT_S = 15.0
FINISH_RESERVE_S = 1.5  # picture checks still running this close to the budget are reported as not checked
MAX_SCAN_FETCH = 8      # property-level scan summaries fetched from Matterport per run (cached ones are free)
MAX_FEATURE_SCANS = 2   # scans whose photos are searched for in-unit features per run


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
        """Listing plan -> {kit, how, scan_id, scan_how, scan_title, note} (harness/graph/plans.py, steps a-d)."""
        label = (label or "").strip()
        key = label.lower()
        with self._plan_lock:
            if key in self._plans:
                return self._plans[key]
        r = {"label": label, "kit": None, "how": None, "scan_id": None, "scan_how": None, "scan_title": None,
             "note": ""}
        fps = self.match.get("floorplans") or [] if self.match.get("status") == "matched" else []
        if not label:
            ids = self.match.get("matterport_model_ids") or []
            if len(ids) == 1:
                r.update(scan_id=ids[0], scan_how="only")
        else:
            fp, how = plans.by_name(label, fps), "name"
            if not fp:
                fp, how = plans.by_size(plans.listing_facts(label, self.claims), fps), "size"
            if fp:
                name = plans.plan_label(fp)
                r.update(kit=fp, how=how)
                if how == "size":
                    r["note"] = f"Matched to the building's plan {name} by size."
                own = fp.get("matterport_model_id")
                bad = self._scan_misfit(fp, own) if own else None
                if own and not bad:
                    r.update(scan_id=own, scan_how="kit")
                else:
                    if bad:
                        r["note"] = (r["note"] + " " if r["note"] else "") + bad
                    taken = {f.get("matterport_model_id") for f in fps if f.get("matterport_model_id")}
                    got = plans.pick_scan(fp, self.property_scans(), taken)
                    if got:
                        r.update(scan_id=got[0], scan_how="title", scan_title=got[1])
                        r["note"] = (f"{label} has no 3D scan of its own in the building record; the building's "
                                     f"scan titled '{got[1]}' fits its plan {name}.")
        with self._plan_lock:
            return self._plans.setdefault(key, r)

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
        """Map whatever plan name the agent passes (e.g. the kit's 'Plan E 1 Bedroom + Den') back to the claim
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
            if plan and r["kit"]:
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
        ev = self._safe("aerial", self.tb.aerial_images, self.lat, self.lon)
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
            with ThreadPoolExecutor(len(wanted)) as ex:
                for evs in ex.map(one, wanted):
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


def _scan_cached(model_id: str) -> bool:
    try:
        return (Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "scan" / model_id / "summary.json").exists()
    except Exception:
        return False


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
    try:
        run.start_pictures()  # amenity / in-unit picture checks run alongside the agent
    except Exception as e:
        run.error(f"picture checks failed to start: {type(e).__name__}: {e}")

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
            run.fixed_plan()  # completion pass: every claim gets its checks; ones already done are not repeated
        except Exception as e:
            run.error(f"standard checks failed: {type(e).__name__}: {e}")
        try:
            run.proof_images()
        except Exception as e:
            run.error(f"proof images failed: {type(e).__name__}: {e}")
    else:
        run.step("orchestrator", "Nothing on this page can be measured, so no checks are needed.")

    try:
        run.collect_pictures()
    except Exception as e:
        run.error(f"picture checks failed: {type(e).__name__}: {e}")
    LAST_AGENT_STATS.clear()
    LAST_AGENT_STATS.update(run.agent_stats)
    LAST_AGENT_STATS["vision_calls"] = run.vision_calls

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
