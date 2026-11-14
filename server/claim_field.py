"""The claim field: every claim a column, every source a row (CoStar, Matterport, Vexcel, County).

`claim_field(run)` is the pure mapping (no I/O): for each claim and source it asks the judge's own rules
(`harness.graph.judge.judge` on that source's evidence alone) whether the source agrees with the listing.
`claim_field_section(run)` renders one self-contained HTML fragment (grid, hover preview, slide-in panel,
own <style> and <script>); every class/id/global is prefixed `cf-` / `cf_`.

The run dict is a saved run as `server.app.load_run` returns it: image paths are already `/img/<token>` URLs.
"""
from __future__ import annotations

import json
import re
from html import escape

SOURCES = [("record", "CoStar"), ("scan", "Matterport"), ("aerial", "Vexcel"), ("county", "County")]
SOURCE_NAME = dict(SOURCES)
STATE_WORDS = {"match": "Matches the listing", "contra": "Doesn't match the listing", "close": "Close, not exact",
               "data": "Has data, not compared", "none": "Nothing from this source"}
VERDICT_WORDS = {"confirmed": "Holds up", "contradicted": "Doesn't match", "close": "Close, not exact",
                 "cant_check": "Couldn't check"}
IMG_RE = re.compile(r"^/img/[a-f0-9]{16}$")

# privacy: never people, contacts or free text (AGENTS.md; same rule as harness/fill_in.py)
_KEEP_KEYS = {"model_name", "scan_title", "source_name", "parcel_address", "kit_plan", "plan"}
_DROP_KEY = re.compile(r"name|contact|phone|email|owner|agent|manager|leasing|person|tel|fax|comment|descr|note"
                       r"|text|narrative|remark|caller|claim_ids|path", re.I)
_COUNT_KEY = re.compile(r"311|complaint|request|violation|incident", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
_PATHLIKE = re.compile(r"^[A-Za-z]:[\\/]|rc_cache|^/(?!img/)|^\\\\")
_LIST_KEEP = {"floorplans": ("name", "beds", "baths", "sqft"),
              "rooms": ("label", "name", "area_sqft", "area_m2")}
PRIORITY = ["plan", "kit_plan", "model_name", "scan_title", "height_m", "stories", "bedrooms", "bathrooms",
            "area_sqft", "year_built", "unit_count", "building_sf", "presence", "feature",
            "counts_from", "room_labels", "floorplans", "parcel_id", "source_name"]


# ---------- sanitising ----------

def _clean_str(s: str) -> str | None:
    s = str(s).strip()
    if not s or _EMAIL.search(s) or _PHONE.search(s) or _PATHLIKE.search(s):
        return None
    if s.startswith(("http://", "https://")):
        s = s.split("?", 1)[0].split("#", 1)[0]  # never a query string (keys, signatures)
    return s[:160]


def clean_observed(obs, depth: int = 0) -> dict:
    """Observed data safe to show: no people, contacts, free text, file paths, signed URLs; complaint-like
    records reduced to counts."""
    out: dict = {}
    if not isinstance(obs, dict):
        return out
    for k, v in obs.items():
        k = str(k)
        if _COUNT_KEY.search(k):
            if isinstance(v, (list, dict)):
                out[k + "_count"] = len(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
            continue
        if k not in _KEEP_KEYS and _DROP_KEY.search(k):
            continue
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            s = _clean_str(v)
            if s is not None:
                out[k] = s
        elif isinstance(v, list):
            if k in _LIST_KEEP:
                keep = _LIST_KEEP[k]
                rows = [{f: x[f] for f in keep if f in x and isinstance(x[f], (str, int, float))
                         and not isinstance(x[f], bool) and (not isinstance(x[f], str) or _clean_str(x[f]))}
                        for x in v[:60] if isinstance(x, dict)]
                rows += [{"label": s} for s in (_clean_str(x) for x in v[:60] if isinstance(x, str)) if s]
                out[k] = [r for r in rows if r]
            elif all(isinstance(x, str) for x in v):
                vals = [s for s in (_clean_str(x) for x in v[:40]) if s and len(s) <= 60]
                if vals:
                    out[k] = vals
            elif all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                out[k] = v[:40]
            else:
                out[k + "_count"] = len(v)
        elif isinstance(v, dict) and depth < 1:
            sub = clean_observed(v, depth + 1)
            if sub:
                out[k] = sub
    return out


def _clean_summary(s) -> str:
    s = str(s or "").strip()
    if not s or _EMAIL.search(s) or _PHONE.search(s) or _PATHLIKE.search(s):
        return ""
    return s[:240]


# ---------- the mapping ----------

def _parts(run: dict):
    claims, order, verdicts, evidence = {}, [], {}, []
    for e in (run or {}).get("events") or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") == "claims":
            for c in e.get("claims") or []:
                if isinstance(c, dict) and c.get("id") and c["id"] not in claims:
                    claims[c["id"]] = c
                    order.append(c["id"])
        elif e.get("type") == "verdict" and isinstance(e.get("verdict"), dict):
            verdicts[e["verdict"].get("claim_id")] = e["verdict"]
        elif e.get("type") == "evidence" and isinstance(e.get("evidence"), dict):
            evidence.append(e["evidence"])
    return claims, order, verdicts, evidence


def _relevant(claim: dict, evs: list) -> list:
    """This source's evidence that bears on the claim, by the judge's own relevance rule (`_usable`),
    plus picture checks naming the claim and the record's full plan list for unnamed unit claims."""
    from harness.graph import amenities
    from harness.graph.judge import UNIT_KINDS, _usable
    if amenities.is_feature_claim(claim):
        return [e for e in evs if e.get("status") == "ok" and claim.get("id") in
                ((e.get("observed") or {}).get("claim_ids") or [])
                and (e.get("observed") or {}).get("presence") not in (None, "skipped")]
    out = _usable(claim, evs, any_plan=True)
    if claim.get("kind") in UNIT_KINDS and not claim.get("label") and claim.get("scope") != "building":
        out += [e for e in evs if e.get("status") == "ok" and e not in out
                and isinstance((e.get("observed") or {}).get("floorplans"), list)]
    return out


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _primary(rel: list, kind: str, found: str, image) -> dict:
    if image:
        for e in rel:
            if any(isinstance(im, dict) and im.get("path") == image for im in e.get("images") or []):
                return e
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(found or ""))
    if m:
        f = _num(m.group(0))
        for e in rel:
            if f is not None and _num((e.get("observed") or {}).get(kind)) == f:
                return e
    return rel[0]


def _images(claim: dict, primary: dict, rel: list, evs: list, vimg) -> list:
    """(url, caption) for this cell: the per-source proof image, then the relevant evidence's pictures, then the
    same plan's (or the building's) other pictures from this source. Only /img/<token> URLs."""
    label = claim.get("label")
    pool = [vimg] if vimg else []
    for e in [primary, *rel]:
        pool += [im.get("path") for im in e.get("images") or [] if isinstance(im, dict)]
    for e in evs:
        obs = e.get("observed") or {}
        if e.get("status") != "ok" or obs.get("feature_check"):
            continue
        plan = obs.get("plan")
        if (label and plan and _same_plan(plan, label)) or (not label and not plan and claim.get("scope") == "building"):
            pool += [im.get("path") for im in e.get("images") or [] if isinstance(im, dict)]
    caps = {}
    for e in evs:
        for im in e.get("images") or []:
            if isinstance(im, dict) and im.get("path"):
                caps.setdefault(im["path"], str(im.get("caption") or ""))
    out, seen = [], set()
    for p in pool:
        if isinstance(p, str) and IMG_RE.match(p) and p not in seen:
            seen.add(p)
            out.append({"url": p, "caption": _clean_summary(caps.get(p, ""))[:120]})
    return out[:6]


def _same_plan(a, b) -> bool:
    from harness.graph.judge import _same_plan as same
    return bool(a and b) and same(str(a), str(b))


FEATURE_WORDS = {"washer_dryer": "washer and dryer", "walk_in_closet": "walk-in closet", "ev_charging": "EV charging",
                 "rooftop_deck": "rooftop deck", "bbq": "grills", "dog_park": "dog park"}
PRESENCE_WORDS = {"seen": "Seen", "not_seen": "Not seen", "unclear": "Not clear in the photos"}
NOISE_ROOMS = {"void", "other", "unknown", ""}


def feature_words(key) -> str:
    """'walk_in_closet' -> 'walk-in closet': a plain name for a picture-check key."""
    k = str(key or "").strip().lower()
    return FEATURE_WORDS.get(k, k.replace("_", " "))


def _n_words(v, unit: str = "") -> str:
    n = _num(v)
    if n is None:
        return ""
    s = f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"
    return f"{s} {unit}".strip()


def _rooms_words(labels) -> str:
    counts: dict = {}
    for x in labels or []:
        name = str((x.get("label") or x.get("name")) if isinstance(x, dict) else x).strip()
        if name.lower() in NOISE_ROOMS:
            continue
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in counts.items())


def _facts(obs: dict, kind: str, source: str = "", plan: str = "") -> list:
    """Up to 6 (label, value) pairs in plain words, the claim's own kind first. Internal keys and values (scan ids,
    method names, confidences, counts of pictures) are never shown; a key with no plain label is left out."""
    obs = obs or {}
    scan_of = f"the 3D scan of {plan}" if plan else "the 3D scan"
    rows: dict = {}

    def put(key, label, value):
        if value not in (None, "") and key not in rows:
            rows[key] = [label, str(value)]

    stories_label = "Floors, measured from above" if source == "aerial" else "Floors"
    for k in [kind] + [x for x in PRIORITY if x != kind]:
        v = obs.get(k)
        if v in (None, "", []):
            continue
        if k == "bedrooms":
            put(k, "Bedrooms", _n_words(v))
        elif k == "bathrooms":
            put(k, "Bathrooms", _n_words(v))
        elif k == "area_sqft":
            put(k, "Floor area", _n_words(v, "sq ft"))
        elif k == "stories":
            rng = obs.get("stories_range")
            extra = ""
            if isinstance(rng, list) and len(rng) == 2 and rng[0] != rng[1]:
                extra = f" (between {_n_words(rng[0])} and {_n_words(rng[1])})"
            put(k, stories_label, ("About " if source == "aerial" else "") + _n_words(v) + extra)
        elif k == "height_m":
            put(k, "Roof height", _n_words(v, "m"))
        elif k == "year_built":
            put(k, "Year built", str(int(_num(v))) if _num(v) else "")
        elif k == "unit_count":
            put(k, "Homes in the building", _n_words(v))
        elif k == "building_sf":
            put(k, "Building size", _n_words(v, "sq ft"))
        elif k in ("model_name", "scan_title"):
            put("title", "3D scan title", f"“{v}”")
        elif k == "plan":
            put(k, "Floor plan", v)
        elif k == "kit_plan" and str(v) != str(obs.get("plan") or ""):
            put(k, "Plan in the CoStar record", v)
        elif k == "counts_from":
            put(k, "Counted from", f"The title of {scan_of}" if v == "model_name" else f"The room labels in {scan_of}")
        elif k == "presence":
            put(k, "In the photos", PRESENCE_WORDS.get(str(v), ""))
        elif k == "feature":
            put(k, "Looked for", _cap(feature_words(v)))
        elif k == "room_labels":
            put(k, "Rooms the scan labels", _rooms_words(v))
        elif k == "floorplans" and isinstance(v, list):
            put(k, "Floor plans on record", str(len(v)))
        elif k == "parcel_id":
            put(k, "County parcel", v)
        elif k == "source_name":
            put(k, "Published by", v)
    if "area_sqft" not in rows and _num(obs.get("area_m2")):
        put("area_m2", "Floor area", _n_words(round(_num(obs["area_m2"]) * 10.7639), "sq ft"))
    if source == "scan" and _num(obs.get("floors")) and _num(obs["floors"]) > 1:
        put("floors", "Levels in the scan", _n_words(obs["floors"]))
    if obs.get("looked_at"):
        put("looked_at", "Looked at", _cap(str(obs["looked_at"])))
    if obs.get("plan_match"):
        put("plan_match", "How the plan was linked", obs["plan_match"])
    if obs.get("capture_date"):
        put("capture_date", "Photo taken", obs["capture_date"])
    return list(rows.values())[:6]


def _cap(s: str) -> str:
    s = str(s or "")
    return s[:1].upper() + s[1:]


def _cell(claim: dict, evs: list) -> dict:
    from harness.graph.judge import judge
    rel = _relevant(claim, evs)
    if not rel:
        return {"state": "none"}
    v = judge(claim, evs)  # the judge's own rules, this source only
    state = {"confirmed": "match", "contradicted": "contra", "close": "close"}.get(v.get("verdict"), "data")
    kind = str(claim.get("kind") or "")
    prim = _primary(rel, kind, v.get("found"), v.get("image"))
    obs = clean_observed(prim.get("observed"))
    plan = str((prim.get("observed") or {}).get("plan") or claim.get("label") or "")
    summaries = list(dict.fromkeys(s for s in (_clean_summary(e.get("summary")) for e in rel) if s))[:3]
    imgs = _images(claim, prim, rel, evs, v.get("image"))
    return {"state": state, "verdict": v.get("verdict"), "found": _clean_summary(v.get("found")),
            "reason": _clean_summary(v.get("reason")), "summary": summaries[0] if summaries else "",
            "summaries": summaries, "facts": _facts(obs, kind, str(prim.get("source") or ""), plan), "observed": obs,
            "raw": [clean_observed(e.get("observed")) for e in rel[:4]],
            "image": imgs[0]["url"] if imgs else None, "images": imgs}


def claim_field(run: dict) -> dict:
    """{"sources": [{key, name, count}], "claims": [{id, kind, quote, label, verdict, found, reason, highlight,
    cells: {record|scan|aerial|county: {state, ...}}}], "counts": {state: n}}.

    state: match | contra | close | data (source has relevant evidence but the judge could not compare) | none.
    """
    from server.report import drop_dead_images
    run = drop_dead_images(run or {})
    claims, order, verdicts, evidence = _parts(run)
    by_src = {k: [e for e in evidence if e.get("source") == k] for k, _ in SOURCES}
    out_claims, counts = [], {s: 0 for s in STATE_WORDS}
    for cid in order:
        c = claims[cid]
        v = verdicts.get(cid) or {}
        cells = {}
        for k, _ in SOURCES:
            try:
                cells[k] = _cell(c, by_src[k])
            except Exception:  # noqa: BLE001  one odd evidence must not break the report
                cells[k] = {"state": "none"}
            counts[cells[k]["state"]] += 1
        verdict = v.get("verdict") or "cant_check"
        out_claims.append({"id": cid, "kind": c.get("kind"), "label": c.get("label"),
                           "quote": str(c.get("quote") or v.get("claimed") or "")[:120],
                           "claimed": _clean_summary(v.get("claimed"))[:80],
                           "verdict": verdict, "verdict_words": VERDICT_WORDS.get(verdict, "Couldn't check"),
                           "found": _clean_summary(v.get("found")), "reason": _clean_summary(v.get("reason")),
                           "highlight": verdict in ("contradicted", "close"), "cells": cells})
    sources = [{"key": k, "name": n, "count": sum(1 for c in out_claims if c["cells"][k]["state"] != "none")}
               for k, n in SOURCES]
    return {"sources": sources, "claims": out_claims, "counts": counts, "source_rows": source_rows(evidence)}


_LAND_USE = re.compile(r"land_?use|property_?use|use_desc|zoning_desc", re.I)
_FOOTPRINT = re.compile(r"footprint", re.I)
_COMPANY = re.compile(r"\b(llc|l\.l\.c|inc|corp|corporation|company|lp|l\.p|llp|ltd|trust|partners|partnership|"
                      r"holdings|properties|property|realty|residential|apartments|management|group|fund|reit|"
                      r"association|housing|authority|city of|county|state of)\b", re.I)


def owner_type(value) -> str:
    """'company' or 'individual' from an owner field; never the name itself. '' when unknown."""
    v = str(value or "").strip()
    if not v:
        return ""
    if v.lower() in ("company", "individual"):
        return v.lower()
    try:
        from tools.kit.facts import is_company
        if is_company(v):
            return "company"
    except Exception:  # noqa: BLE001
        pass
    return "company" if _COMPANY.search(v) else "individual"


def _count_of(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, (list, dict)):
        return len(v)
    return None


def source_rows(evidence: list) -> dict:
    """What aerial imagery and county records saw in this run, as plain (label, value) rows for the grid:
    {"aerial": [[label, value]], "county": [[label, value]]}; a source with nothing is left out.
    Aerial: roof height, floors measured from above, footprint, things seen or not seen in the photos (never a photo
    floor count). County: year built, building size, homes, floors, land use, owner type (company / individual, never
    a name), 311 requests as a count only (never their text). Only what the evidence really carries."""
    rows: dict = {"aerial": {}, "county": {}}

    def put(src, key, label, value):
        if value not in (None, "") and key not in rows[src]:
            rows[src][key] = [label, str(value)]

    for e in evidence or []:
        if not isinstance(e, dict) or e.get("status") != "ok" or e.get("source") not in rows:
            continue
        src, obs = e["source"], e.get("observed") or {}
        if not isinstance(obs, dict):
            continue
        if src == "aerial":
            if obs.get("feature_check"):
                pres, feat = obs.get("presence"), obs.get("feature")
                if feat and pres in ("seen", "not_seen"):
                    key = "feature:" + str(feat)
                    if pres == "seen":
                        rows[src].pop(key, None)  # seen in one photo set beats not seen in another
                    put(src, key, _cap(feature_words(feat)),
                        "Seen from above" if pres == "seen" else "Not seen from above")
                continue
            if obs.get("illustrative"):
                continue  # a photo floor count never decides floors
            if _num(obs.get("height_m")):
                put(src, "height", "Roof height", _n_words(obs["height_m"], "m"))
            if _num(obs.get("stories")):
                rng = obs.get("stories_range")
                extra = ""
                if isinstance(rng, list) and len(rng) == 2 and rng[0] != rng[1]:
                    extra = f" (between {_n_words(rng[0])} and {_n_words(rng[1])})"
                put(src, "stories", "Floors, measured from above", f"About {_n_words(obs['stories'])}{extra}")
            for k, v in obs.items():
                if _FOOTPRINT.search(str(k)) and _num(v):
                    low = str(k).lower()
                    sq = _num(v) * (10.7639 if ("m2" in low or "sqm" in low) else 1)
                    put(src, "footprint", "Building footprint", _n_words(round(sq), "sq ft"))
            if obs.get("capture_date"):
                put(src, "date", "Photos taken", _clean_str(obs["capture_date"]))
        else:
            if _num(obs.get("year_built")):
                put(src, "year", "Year built", str(int(_num(obs["year_built"]))))
            if _num(obs.get("building_sf")):
                put(src, "size", "Building size", _n_words(obs["building_sf"], "sq ft"))
            if _num(obs.get("unit_count")):
                put(src, "units", "Homes", _n_words(obs["unit_count"]))
            if _num(obs.get("stories")):
                put(src, "stories", "Floors", _n_words(obs["stories"]))
            for k, v in obs.items():
                k = str(k)
                if _COUNT_KEY.search(k):
                    n = _count_of(v)
                    if n is not None and n >= 0:
                        label = "311 requests" if "311" in k else "Complaints on record"
                        put(src, "count:" + label, label, f"{n:,}")
                elif _LAND_USE.search(k) and isinstance(v, str):
                    lu = _clean_str(v)
                    if lu and len(lu) <= 60:
                        put(src, "land", "Land use", _cap(lu.lower()) if lu.isupper() else lu)
                elif re.search(r"owner", k, re.I) and isinstance(v, str):
                    t = owner_type(v)
                    if t:
                        put(src, "owner", "Owner", "A company" if t == "company" else "An individual")
            if obs.get("source_name"):
                put(src, "pub", "Published by", _clean_str(obs["source_name"]))
    return {k: list(v.values()) for k, v in rows.items() if v}


def _tour_links(run: dict, field: dict) -> None:
    """Give a Matterport cell its 3D view when the claim has one: `tour` = the inline viewer's embed URL
    (/tour/...&embed=1, shown inside the panel or at the finding's own viewer), `tour_page` = the standalone page
    (same tab, only on pages without the report's viewer script)."""
    try:
        from server.tour import tour_href, tour_target
    except Exception:  # noqa: BLE001
        return
    if not re.fullmatch(r"[a-z0-9-]{1,64}", str(run.get("run_id") or "")):
        return
    for c in field["claims"]:
        cell = c["cells"]["scan"]
        if cell["state"] == "none" or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(c["id"])):
            continue
        try:
            if tour_target(run, c["id"]):
                cell["tour"] = tour_href(run, c["id"], embed=True)
                cell["tour_page"] = tour_href(run, c["id"])
        except Exception:  # noqa: BLE001
            pass


# ---------- rendering ----------

CSS = """
.cf-field{--cf-ink:#111;--cf-blue:#1a4be0;--cf-grey:#ebebeb;--cf-muted:#6b6b6b;--cf-line:#e2e2e2;
--cf-ease:cubic-bezier(.22,1,.36,1);--cf-h:60px;--cf-gap:5px;--cf-min:12px;
font-family:Newsreader,Georgia,"Times New Roman",serif;color:var(--cf-ink);margin:0 0 48px;width:100%}
.cf-field *{box-sizing:border-box}
.cf-wrap{display:flex;gap:clamp(12px,1.6vw,24px);align-items:flex-start}
.cf-rows{flex:none;width:clamp(104px,13%,150px);display:grid;grid-template-rows:repeat(4,var(--cf-h));gap:var(--cf-gap);
font-size:17px;line-height:1.1}
.cf-rows span{display:flex;align-items:center;justify-content:space-between;gap:8px}
.cf-rows b{font-weight:400}.cf-rows i{font-style:normal;color:var(--cf-muted);font-variant-numeric:tabular-nums}
.cf-scroll{flex:1 1 auto;min-width:0;overflow-x:auto;overflow-y:visible;padding-bottom:4px}
.cf-inner{position:relative;width:100%;max-width:calc(var(--cf-n) * 56px);
min-width:calc(var(--cf-n) * (var(--cf-min) + var(--cf-gap)))}
.cf-grid{display:grid;grid-template-rows:repeat(4,var(--cf-h));grid-auto-flow:column;
grid-auto-columns:minmax(var(--cf-min),1fr);gap:var(--cf-gap)}
.cf-cell{display:block;width:100%;height:100%;min-width:0;padding:0;margin:0;border:0;border-radius:0;cursor:pointer;
background:var(--cf-grey);transition:transform .22s var(--cf-ease),box-shadow .22s var(--cf-ease),opacity .2s;
animation:cf-in .5s var(--cf-ease) both;animation-delay:calc(var(--cf-i) * 9ms + 60ms)}
.cf-cell.cf-match{background:var(--cf-ink)}
.cf-cell.cf-contra{background:var(--cf-blue)}.cf-cell.cf-close{background:#a9baf2}
.cf-cell.cf-data{background:#fff;box-shadow:inset 0 0 0 2px var(--cf-ink)}
.cf-cell.cf-none{cursor:default}
.cf-cell:not(.cf-none):hover,.cf-cell:not(.cf-none):focus-visible{transform:translateY(-3px)}
.cf-cell:not(.cf-none):hover{box-shadow:0 8px 18px rgba(17,17,17,.18)}
.cf-cell.cf-data:hover{box-shadow:inset 0 0 0 2px var(--cf-ink),0 8px 18px rgba(17,17,17,.18)}
.cf-cell:focus{outline:none}.cf-cell:focus-visible{outline:2px solid var(--cf-blue);outline-offset:2px}
.cf-cell.cf-on{outline:2px solid var(--cf-blue);outline-offset:2px}
@keyframes cf-in{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
.cf-sweep{position:absolute;top:-6px;height:calc(4 * var(--cf-h) + 3 * var(--cf-gap) + 12px);left:0;width:0;
background:rgba(26,75,224,.13);pointer-events:none;opacity:0;transition:opacity .15s,left .18s var(--cf-ease)}
.cf-sweep.cf-show{opacity:1}
.cf-marks{position:relative;height:var(--cf-mh,44px);margin-top:12px;font-size:16px;line-height:1.2}
.cf-mark{position:absolute;top:0;display:flex;flex-direction:column;gap:5px;white-space:nowrap;color:var(--cf-blue);
transition:color .15s}
.cf-mark::before{content:"";width:3px;height:12px;background:var(--cf-blue)}
.cf-mark.cf-flip{align-items:flex-end}
.cf-mark.cf-tick{font-size:0;gap:0}
.cf-mark.cf-near{color:#5f78c9}.cf-mark.cf-near::before{background:#a9baf2}
.cf-mark.cf-q{color:var(--cf-muted)}.cf-mark.cf-q::before{background:#c4c4c4}
.cf-nojs .cf-mark{position:static;display:inline-flex;margin-right:16px}
.cf-legend{display:flex;flex-wrap:wrap;gap:8px 28px;font-size:15px;color:var(--cf-muted);margin:14px 0 0}
.cf-legend span{display:inline-flex;align-items:center;gap:8px}
.cf-legend i{width:12px;height:12px;background:var(--cf-grey);flex:none}
.cf-legend i.cf-k{background:var(--cf-ink)}.cf-legend i.cf-n{background:#a9baf2}.cf-legend i.cf-b{background:var(--cf-blue)}
.cf-legend i.cf-o{background:#fff;box-shadow:inset 0 0 0 2px var(--cf-ink)}
.cf-tip{position:fixed;z-index:60;width:260px;height:250px;overflow:hidden;background:#fff;color:var(--cf-ink);
border:1px solid var(--cf-line);box-shadow:0 14px 36px rgba(17,17,17,.14);padding:12px 14px;pointer-events:none;
font-family:Newsreader,Georgia,serif;opacity:0;transform:translateY(4px);
transition:opacity .12s ease,transform .16s var(--cf-ease)}
.cf-tip.cf-show{opacity:1;transform:none}
.cf-tip[hidden]{display:none}
.cf-tip.cf-quiet{height:auto;width:auto;max-width:260px;padding:7px 11px;font-size:14px;color:var(--cf-muted)}
.cf-tip img{display:block;width:100%;height:96px;object-fit:cover;background:var(--cf-grey);margin:0 0 9px}
.cf-tip .cf-src{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--cf-muted);margin:0 0 3px}
.cf-tip .cf-st{color:var(--cf-ink)}.cf-tip .cf-st.cf-b{color:var(--cf-blue)}
.cf-tip .cf-qt{font-size:17px;line-height:1.2;margin:0 0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cf-tip .cf-cl{font-size:14px;color:var(--cf-muted);margin:0 0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cf-tip dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:1px 10px;font-size:13px;line-height:1.35}
.cf-tip dt{color:var(--cf-muted)}.cf-tip dd{margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf-tip .cf-fd{font-size:14px;margin:0 0 5px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.cf-back{position:fixed;inset:0;z-index:70;background:rgba(17,17,17,.28);opacity:0;transition:opacity .32s var(--cf-ease)}
.cf-back.cf-show{opacity:1}.cf-back[hidden],.cf-panel[hidden]{display:none}
.cf-panel{position:fixed;top:0;right:0;bottom:0;z-index:71;width:min(520px,100vw);background:#fff;color:var(--cf-ink);
font-family:Newsreader,Georgia,serif;box-shadow:-18px 0 48px rgba(17,17,17,.12);display:flex;flex-direction:column;
transform:translateX(100%);transition:transform .32s var(--cf-ease)}
.cf-panel.cf-show{transform:none}
.cf-bar{display:flex;align-items:center;gap:6px;padding:14px 18px;border-bottom:1px solid var(--cf-line);font-size:15px}
.cf-bar .cf-where{flex:1;min-width:0;color:var(--cf-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cf-bar button{font:inherit;font-size:15px;min-width:40px;min-height:40px;padding:0 10px;border:1px solid var(--cf-line);
background:#fff;color:var(--cf-ink);cursor:pointer;transition:background .15s,transform .15s var(--cf-ease)}
.cf-bar button:hover{background:#f4f4f4}.cf-bar button:disabled{opacity:.35;cursor:default}
.cf-bar .cf-x{background:var(--cf-ink);color:#fff;border-color:var(--cf-ink)}.cf-bar .cf-x:hover{background:#333}
.cf-body{flex:1;overflow-y:auto;padding:22px 24px 40px}
.cf-body h3{font-weight:400;font-size:32px;line-height:1.1;letter-spacing:-.02em;margin:0 0 6px}
.cf-body .cf-lbl{font-size:13px;color:var(--cf-muted);margin:22px 0 4px;text-transform:uppercase;letter-spacing:.05em;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.cf-body p{margin:0 0 6px;font-size:17px;line-height:1.45}
.cf-body .cf-state{font-size:19px}.cf-body .cf-state.cf-b{color:var(--cf-blue)}
.cf-body .cf-dim{color:var(--cf-muted);font-size:15px}
.cf-pics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0 4px}
.cf-pics.cf-one{grid-template-columns:minmax(0,1fr)}.cf-pics[hidden],.cf-pics figure[hidden]{display:none}
.cf-pics a{display:block;cursor:zoom-in}.cf-pics img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#f6f6f4;
border:1px solid var(--cf-line)}
.cf-pics.cf-one img{aspect-ratio:16/10}
.cf-pics figure{margin:0}.cf-pics figcaption{font-size:13px;color:var(--cf-muted);margin-top:3px;line-height:1.3}
.cf-saw{display:grid;gap:10px;margin:22px 0 0;padding-top:16px;border-top:1px solid var(--cf-line)}
.cf-saw-row{display:flex;align-items:baseline;gap:6px 8px;font-size:15px;line-height:1.3}
.cf-chips{flex:1;min-width:0;display:flex;flex-wrap:wrap;gap:6px 8px}
.cf-saw-row>b{font-weight:400;flex:none;width:clamp(104px,13%,150px);font-size:17px}
.cf-chip{display:inline-flex;gap:6px;align-items:baseline;background:#f4f4f2;padding:5px 10px;
animation:cf-in .5s var(--cf-ease) both;animation-delay:calc(var(--cf-i) * 40ms + 200ms)}
.cf-chip i{font-style:normal;color:var(--cf-muted)}
.cf-saw .cf-credit{font-size:13px;color:var(--cf-muted);margin:2px 0 0}
.cf-kv{display:grid;grid-template-columns:minmax(110px,auto) 1fr;gap:0;margin:0;font-size:15px}
.cf-kv dt,.cf-kv dd{margin:0;padding:6px 0;border-bottom:1px solid var(--cf-line)}
.cf-kv dt{color:var(--cf-muted);padding-right:16px}.cf-kv dd{overflow-wrap:anywhere}
.cf-body details{margin-top:18px}.cf-body summary{cursor:pointer;font-size:15px}
.cf-body pre{font:12px/1.45 ui-monospace,Consolas,monospace;background:#f6f6f4;padding:10px;overflow:auto;max-height:300px;
white-space:pre-wrap;overflow-wrap:anywhere}
.cf-body .cf-go{display:inline-block;margin-top:10px;background:var(--cf-ink);color:#fff;padding:9px 16px;
text-decoration:none;font-size:15px}
.cf-body .cf-go:hover{background:#333}
.cf-body button.cf-go{border:0;font:inherit;cursor:pointer}.cf-3dhost .see3d-box{margin-top:12px}
@media(max-width:640px){.cf-field{--cf-h:44px;--cf-min:18px;--cf-gap:4px}
.cf-rows{width:96px;font-size:15px}.cf-saw-row{flex-wrap:wrap}.cf-saw-row>b{width:100%;font-size:16px}.cf-legend{font-size:14px;gap:6px 18px}.cf-body{padding:18px 16px 32px}
.cf-body h3{font-size:26px}}
@media(prefers-reduced-motion:reduce){.cf-field *,.cf-tip,.cf-panel,.cf-back{animation:none!important;transition:none!important}
.cf-cell:not(.cf-none):hover,.cf-cell:not(.cf-none):focus-visible{transform:none}}
"""

SCRIPT = r"""
(function(){var root=document.getElementById('cf-field');if(!root||root.getAttribute('data-cf-ready'))return;
root.setAttribute('data-cf-ready','1');root.classList.remove('cf-nojs');
var D=JSON.parse(document.getElementById('cf-data').textContent),S=D.sources,C=D.claims;
var NAME={},WORD=%s;S.forEach(function(s){NAME[s.key]=s.name;});
var grid=root.querySelector('.cf-grid'),sweep=root.querySelector('.cf-sweep'),tip=document.getElementById('cf-tip');
var back=document.getElementById('cf-back'),panel=document.getElementById('cf-panel'),body=panel.querySelector('.cf-body');
var cells=Array.prototype.slice.call(grid.querySelectorAll('.cf-cell'));
var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function at(c,r){return grid.querySelector('.cf-cell[data-c="'+c+'"][data-r="'+r+'"]');}
function data(b){var c=C[+b.getAttribute('data-c')],s=S[+b.getAttribute('data-r')];return{c:c,s:s,cell:c.cells[s.key]};}
function blue(st){return st==='contra'||st==='close';}
/* a soft caption: the part before any colon, at most 60 characters */
function soft(c){c=String(c||'').split(':')[0].trim();return c.length>60?c.slice(0,58)+'…':c;}
/* a picture whose file is gone (older runs) is hidden, never a broken-image icon */
document.addEventListener('error',function(e){var t=e.target;if(!t||t.tagName!=='IMG')return;
 var f=t.closest&&t.closest('.cf-pics figure');if(f){f.hidden=true;var p=f.parentNode;if(p&&!p.querySelector('figure:not([hidden])'))p.hidden=true;}
 else if(t.closest&&t.closest('.cf-tip'))t.style.display='none';},true);
/* under-column marks: stack labels into lanes so none overlap */
function lanes(){var box=root.querySelector('.cf-marks'),marks=Array.prototype.slice.call(box.querySelectorAll('.cf-mark')),ends=[],lh=-1;
 var base=grid.getBoundingClientRect().left,W=grid.getBoundingClientRect().width,MAX=3;
 marks.sort(function(a,b){return(a.classList.contains('cf-q')?1:0)-(b.classList.contains('cf-q')?1:0)||a.getAttribute('data-c')-b.getAttribute('data-c');});
 function free(lane,x0,x1){return(ends[lane]||[]).every(function(r){return x1<r[0]-8||x0>r[1]+8;});}
 marks.forEach(function(m){var b=at(m.getAttribute('data-c'),0);if(!b)return;m.classList.remove('cf-tick','cf-flip');
  var r=b.getBoundingClientRect(),x=r.left-base+r.width/2-1,w=m.offsetWidth,x0=x,x1=x+w;
  if(x1>W){m.classList.add('cf-flip');x0=x-w+3;x1=x+3;}
  var lanesTry=m.classList.contains('cf-q')?1:MAX,lane=-1;for(var i=0;i<lanesTry;i++){if(free(i,x0,x1)){lane=i;break;}}
  if(lane<0){m.classList.add('cf-tick');m.classList.remove('cf-flip');lane=0;x0=x;x1=x+3;}
  (ends[lane]=ends[lane]||[]).push([x0,x1]);
  m.style.left=(m.classList.contains('cf-flip')?x0:x)+'px';m.style.top=(lane*40)+'px';lh=Math.max(lh,lane);});
 box.style.setProperty('--cf-mh',(lh<0?0:lh*40+42)+'px');}
lanes();window.addEventListener('resize',lanes);
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(lanes);
/* hover preview */
var hideT;
function place(b){var r=b.getBoundingClientRect(),w=tip.offsetWidth,h=tip.offsetHeight,vw=document.documentElement.clientWidth,
 vh=window.innerHeight,x=r.left+r.width/2-w/2,y=r.bottom+10;
 if(y+h>vh-8)y=r.top-h-10;if(y<8)y=8;x=Math.max(8,Math.min(x,vw-w-8));tip.style.left=x+'px';tip.style.top=y+'px';}
function show(b){clearTimeout(hideT);var d=data(b),st=d.cell.state;colOn(b);
 if(st==='none'){tip.className='cf-tip cf-quiet';tip.innerHTML='Nothing from '+esc(d.s.name)+'.';}
 else{tip.className='cf-tip';var h='';if(d.cell.image)h+='<img alt="" src="'+esc(d.cell.image)+'">';
  h+='<p class="cf-src"><span>'+esc(d.s.name)+'</span><span class="cf-st'+(blue(st)?' cf-b':'')+'">'+esc(WORD[st])+'</span></p>';
  h+='<p class="cf-qt">“'+esc(d.c.quote)+'”</p>';
  if(d.c.claimed)h+='<p class="cf-cl">Listing: '+esc(d.c.claimed)+'</p>';
  if(d.cell.found)h+='<p class="cf-fd">'+esc(d.s.name)+': '+esc(d.cell.found)+'</p>';
  var f=(d.cell.facts||[]).slice(0,d.cell.image?2:5);
  if(f.length)h+='<dl>'+f.map(function(p){return'<dt>'+esc(p[0])+'</dt><dd>'+esc(p[1])+'</dd>';}).join('')+'</dl>';
  tip.innerHTML=h;}
 tip.hidden=false;place(b);requestAnimationFrame(function(){tip.classList.add('cf-show');});}
function hide(){tip.classList.remove('cf-show');colOff();hideT=setTimeout(function(){tip.hidden=true;},reduce?0:130);}
function colOn(b){var r=b.getBoundingClientRect(),g=grid.getBoundingClientRect();sweep.style.width=(r.width+8)+'px';
 sweep.style.left=(r.left-g.left-4)+'px';sweep.classList.add('cf-show');}
function colOff(){sweep.classList.remove('cf-show');}
grid.addEventListener('mouseover',function(e){var b=e.target.closest('.cf-cell');if(b)show(b);});
grid.addEventListener('mouseleave',hide);
grid.addEventListener('focusin',function(e){var b=e.target.closest('.cf-cell');if(b)show(b);});
grid.addEventListener('focusout',function(e){if(!grid.contains(e.relatedTarget))hide();});
root.querySelector('.cf-scroll').addEventListener('scroll',function(){tip.hidden=true;colOff();lanes();},{passive:true});
/* roving focus inside the grid */
function rove(b){cells.forEach(function(x){x.tabIndex=-1;});b.tabIndex=0;b.focus();}
function step(c,r,dc,dr,skip){var n=C.length;for(var i=0;i<n*4;i++){c+=dc;r+=dr;if(c<0||c>=n||r<0||r>3)return null;
 var b=at(c,r);if(!skip||!b.classList.contains('cf-none'))return b;}return null;}
grid.addEventListener('keydown',function(e){var b=e.target.closest('.cf-cell');if(!b)return;
 var c=+b.getAttribute('data-c'),r=+b.getAttribute('data-r'),m={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
 if(m){var t=step(c,r,m[0],m[1],false);if(t)rove(t);e.preventDefault();}
 else if(e.key==='Home'){rove(at(0,r));e.preventDefault();}else if(e.key==='End'){rove(at(C.length-1,r));e.preventDefault();}});
/* slide-in panel */
var cur=null,opener=null,closeT;
function drop3d(){var w=body.querySelector('.walk');if(w&&window.RC3D)window.RC3D.unmount(w);}
function fill(b){var d=data(b),st=d.cell.state,c=d.c,cell=d.cell,h='';drop3d();cur=b;
 cells.forEach(function(x){x.classList.toggle('cf-on',x===b);});
 panel.querySelector('.cf-where').textContent=d.s.name+' · claim '+(+b.getAttribute('data-c')+1)+' of '+C.length;
 panel.querySelector('.cf-prev').disabled=!step(+b.getAttribute('data-c'),+b.getAttribute('data-r'),-1,0,true);
 panel.querySelector('.cf-next').disabled=!step(+b.getAttribute('data-c'),+b.getAttribute('data-r'),1,0,true);
 h+='<p class="cf-lbl">The listing says</p><h3>“'+esc(c.quote)+'”</h3>';
 var sub=[c.claimed,c.label?'floor plan '+c.label:''].filter(Boolean).join(' · ');if(sub)h+='<p class="cf-dim">'+esc(sub)+'</p>';
 h+='<p class="cf-lbl">Overall</p><p class="cf-state'+(c.highlight?' cf-b':'')+'">'+esc(c.verdict_words)+(c.found?': '+esc(c.found):'')+'</p>';
 if(c.reason)h+='<p class="cf-dim">'+esc(c.reason)+'</p>';
 h+='<p class="cf-lbl">'+esc(d.s.name)+'</p><p class="cf-state'+(blue(st)?' cf-b':'')+'">'+esc(WORD[st])+(cell.found&&st!=='none'?': '+esc(cell.found):'')+'</p>';
 if(st==='none'){h+='<p class="cf-dim">'+esc(d.s.name)+' had nothing usable about this claim.</p>';}
 else{if(cell.reason&&st!=='data'&&(c.reason||'').indexOf(cell.reason.split('. ').slice(0,2).join('. '))<0)h+='<p class="cf-dim">'+esc(cell.reason)+'</p>';
  (cell.summaries||[]).forEach(function(s){h+='<p>'+esc(s)+'</p>';});
  if(cell.tour)h+='<button type="button" class="cf-go cf-3d" data-claim="'+esc(c.id)+'">See it in 3D</button><div class="cf-3dhost"></div>';
  if(cell.images&&cell.images.length){h+='<div class="cf-pics'+(cell.images.length===1?' cf-one':'')+'">'+cell.images.map(function(im){var cap=soft(im.caption);return'<figure><a class="pic" href="'+esc(im.url)+'" data-cap="'+esc(cap)+'"><img loading="lazy" decoding="async" alt="'+esc(cap||'Proof picture')+'" src="'+esc(im.url)+'"></a>'+(cap?'<figcaption>'+esc(cap)+'</figcaption>':'')+'</figure>';}).join('')+'</div>';}
  if(cell.facts&&cell.facts.length)h+='<p class="cf-lbl">What we pulled</p><dl class="cf-kv">'+cell.facts.map(function(p){return'<dt>'+esc(p[0])+'</dt><dd>'+esc(p[1])+'</dd>';}).join('')+'</dl>';
  }
 body.innerHTML=h;body.scrollTop=0;}
function open(b){if(b.classList.contains('cf-none'))return;opener=b;clearTimeout(closeT);hide();fill(b);
 back.hidden=false;panel.hidden=false;void panel.offsetWidth;back.classList.add('cf-show');panel.classList.add('cf-show');
 panel.querySelector('.cf-x').focus({preventScroll:true});}
function close(){if(panel.hidden)return;drop3d();back.classList.remove('cf-show');panel.classList.remove('cf-show');
 cells.forEach(function(x){x.classList.remove('cf-on');});
 closeT=setTimeout(function(){panel.hidden=true;back.hidden=true;},reduce?0:330);
 if(opener){rove(opener);}}
function go(dc,dr){if(!cur)return;var t=step(+cur.getAttribute('data-c'),+cur.getAttribute('data-r'),dc,dr,true);
 if(t){opener=t;fill(t);t.scrollIntoView({block:'nearest',inline:'nearest'});}}
grid.addEventListener('click',function(e){var b=e.target.closest('.cf-cell');if(b)open(b);});
back.addEventListener('click',close);panel.querySelector('.cf-x').addEventListener('click',close);
/* "See it in 3D": the finding's own inline viewer when it has one (panel closes, page scrolls there), else a live
   viewer inside this panel; never a new tab */
body.addEventListener('click',function(e){var g=e.target.closest('.cf-3d');if(!g||!cur)return;var cell=data(cur).cell,cid=g.getAttribute('data-claim');
 var R=window.RC3D;if(!R){if(cell.tour_page)location.href=cell.tour_page;return;}
 var a=document.getElementById('claim-'+cid),f=a&&a.closest('.find');
 if(f&&f.querySelector('.walk[data-src]')){close();R.see(cid);return;}
 var w=R.see(cid,cell.tour,body.querySelector('.cf-3dhost'));g.hidden=true;if(w)w.scrollIntoView({block:'nearest'});});
panel.querySelector('.cf-prev').addEventListener('click',function(){go(-1,0);});
panel.querySelector('.cf-next').addEventListener('click',function(){go(1,0);});
document.addEventListener('keydown',function(e){if(panel.hidden)return;
 if(e.key==='Escape'){close();e.preventDefault();return;}
 var t=e.target;if(t&&(t.tagName==='SUMMARY'||t.tagName==='INPUT'))return;
 var m={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];if(m){go(m[0],m[1]);e.preventDefault();}
 if(e.key==='Tab'){var f=panel.querySelectorAll('button:not([disabled]),a[href],summary');if(!f.length)return;
  var a=f[0],z=f[f.length-1];if(e.shiftKey&&document.activeElement===a){z.focus();e.preventDefault();}
  else if(!e.shiftKey&&document.activeElement===z){a.focus();e.preventDefault();}}});
})();
"""


def _saw_html(rows: dict) -> str:
    """'What aerial and county records saw': one line of short facts per source that has any, plus the plain-text
    source credit."""
    lines = []
    for key, name in SOURCES:
        items = rows.get(key) or []
        if not items:
            continue
        chips = "".join(f"<span class=cf-chip style='--cf-i:{i}'><i>{escape(lab)}</i>{escape(val)}</span>"
                        for i, (lab, val) in enumerate(items[:8]))
        lines.append(f"<div class=cf-saw-row><b>{escape(name)}</b><span class=cf-chips>{chips}</span></div>")
    credit = ("<p class=cf-credit>Rows: CoStar building record, Matterport 3D scans, Vexcel aerial imagery, county public "
              "records.</p>")
    if not lines:
        return f"<div class=cf-saw>{credit}</div>"
    return "<div class=cf-saw aria-label='What Vexcel aerial imagery and county records saw'>" + "".join(lines) + credit + "</div>"


def _json_script(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def claim_field_section(run: dict, tours: bool = True) -> str:
    """One self-contained HTML fragment: <section id="cf-field" class="cf-field"> with the grid, legend, hover
    preview, slide-in panel, and its own <style> and <script>. tours=False (a copy with no server) leaves out the
    3D views."""
    field = claim_field(run)
    if tours:
        _tour_links(run, field)
    claims, sources = field["claims"], field["sources"]
    n = max(1, len(claims))
    h = [f"<style>{CSS}</style>",
         f"<section id=cf-field class='cf-field cf-nojs' style='--cf-n:{n}' aria-label='Claims and sources'>",
         "<div class=cf-wrap><div class=cf-rows aria-hidden=true>"]
    for s in sources:
        h.append(f"<span><b>{escape(s['name'])}</b><i>{s['count']}</i></span>")
    h.append("</div><div class=cf-scroll><div class=cf-inner><div class=cf-grid role=group "
             "aria-label='One column per claim, one row per source. Arrow keys move; Enter opens.'>")
    first = True
    for ci, c in enumerate(claims):
        for ri, s in enumerate(sources):
            st = c["cells"][s["key"]]["state"]
            label = f"{s['name']}, “{c['quote']}”: {STATE_WORDS[st].lower()}"
            tab = "0" if first else "-1"
            first = False
            h.append(f"<button type=button class='cf-cell cf-{st}' data-c={ci} data-r={ri} tabindex={tab} "
                     f"style='--cf-i:{ci}' aria-label='{escape(label, True)}'></button>")
    h.append("</div><div class=cf-sweep aria-hidden=true></div><div class=cf-marks>")
    for ci, c in enumerate(claims):
        if c["highlight"]:
            near = " cf-near" if c["verdict"] == "close" else ""
            h.append(f"<span class='cf-mark{near}' data-c={ci}>“{escape(c['quote'])}”</span>")
        elif c["verdict"] == "cant_check":
            h.append(f"<span class='cf-mark cf-q' data-c={ci} title='{escape(c['quote'], True)}'>?</span>")
    h.append("</div></div></div></div>")
    h.append("<p class=cf-legend><span><i class=cf-k></i>matches the listing</span>"
             "<span><i class=cf-b></i>doesn't match</span><span><i class=cf-n></i>close, not exact</span><span><i></i>nothing to say</span>")
    if field["counts"]["data"]:
        h.append("<span><i class=cf-o></i>has data, not compared</span>")
    h.append("<span>One column per claim.</span></p>")
    h.append(_saw_html(field.get("source_rows") or {}))
    h.append("<div class=cf-tip id=cf-tip hidden aria-hidden=true></div>"
             "<div class=cf-back id=cf-back hidden></div>"
             "<aside class=cf-panel id=cf-panel hidden role=dialog aria-modal=true aria-label='Evidence'>"
             "<div class=cf-bar><span class=cf-where></span>"
             "<button type=button class=cf-prev aria-label='Previous claim'>←</button>"
             "<button type=button class=cf-next aria-label='Next claim'>→</button>"
             "<button type=button class=cf-x>Close</button></div><div class=cf-body></div></aside>")
    h.append(f"<script type=application/json id=cf-data>{_json_script(field)}</script>")
    h.append(f"<script>{SCRIPT % json.dumps(STATE_WORDS)}</script></section>")
    return "".join(h)
