"""Artifact library: GET /artifacts/<run_id>, one page per run that shows what each source actually gave us.

The report stays simple; this page shows the agent's depth. For every claim that any source spoke to (mismatches
first, the report's lead on top, then close, then confirmed, then couldn't-check collapsed), a claim header and one
ARTIFACT CARD per source, each with the anchor `a-<claim_id>-<source slug>` (costar | matterport | vexcel | county):
  - the source's name and a tag: "Decided this" (the source whose evidence backs the found value, `deciding_source`,
    the report's own truthful-credit rule `server.report_a._credit`), else "Agrees" / "Differs" / "Had nothing";
  - the artifact itself: CoStar record fields, County record fields with publisher and roll year, Vexcel pictures
    (/img/<token> only) and measured values, Matterport scan title, rooms, measured area, stills and "See it in 3D";
  - the value read from it and how it compares with the listing (that source alone, judged by the same judge the
    report's claim field uses, `server.claim_field.claim_field`);
  - one grey trace line: which agent and tool produced it, and when in the run.

Other pages link here with `artifact_href(run_id, claim_id, source)`. Privacy (same rules as the report): no people's
names or contacts (owner type only), 311 as a count only, no file paths or signed URLs (pictures only as
/img/<token>), no keys, never unkind words; absence is never a mismatch.

Wiring: `register(app)` adds the route; server/app.py calls it once.
"""
from __future__ import annotations

import hashlib
import re
from html import escape

SRC_ORDER = ["record", "scan", "aerial", "county"]
SRC_NAME = {"record": "CoStar", "scan": "Matterport", "aerial": "Vexcel", "county": "County"}
SRC_SLUG = {"record": "costar", "scan": "matterport", "aerial": "vexcel", "county": "county"}
SRC_WHAT = {"record": "Building record", "scan": "3D scan", "aerial": "Aerial imagery", "county": "Public record"}
_SLUG_OF = {**SRC_SLUG, **{v.lower(): v for v in SRC_SLUG.values()}}
VERDICT_WORD = {"contradicted": "Doesn't match", "close": "Close", "confirmed": "Matches", "cant_check": "Couldn't check"}
ALONE_WORD = {"confirmed": "matches the listing", "contradicted": "doesn't match the listing",
              "close": "is close to the listing"}
KIND_LABEL = {"year_built": "Year built", "stories": "Floors", "unit_count": "Homes", "area_sqft": "Size",
              "bedrooms": "Bedrooms", "bathrooms": "Bathrooms", "amenity": "Amenity", "parking": "Parking",
              "laundry": "Laundry", "view": "View", "other": "Feature"}
AGENT_NAME = {"inside": "Inside checker", "outside": "Outside checker", "paper": "Records checker"}
RUN_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
IMG_RE = re.compile(r"^/img/[a-f0-9]{16}$")
UNKIND = re.compile(r"(?i)\b(fake|faked|lie|lies|lied|lying|scam\w*|fraud\w*)\b")
_URL = re.compile(r"https?://\S+")
_PATHLIKE = re.compile(r"[A-Za-z]:[\\/]|rc_cache|\\\\|(?<![\w/])/(?!img/)[\w.-]+/")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
MAX_IMGS = 6
MAX_PLANS = 10


# ---------- small helpers ----------

def _e(s) -> str:
    return escape(str(s if s is not None else ""), True)


def _cap(s) -> str:
    s = str(s or "").strip()
    return s[:1].upper() + s[1:]


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    try:
        return float(m.group(0).replace(",", "")) if m else None
    except ValueError:
        return None


def _n(v, unit: str = "") -> str:
    n = _num(v)
    if n is None:
        return ""
    s = f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"
    return f"{s} {unit}".strip()


def _safe(s, limit: int = 220) -> str:
    """One plain line fit to show: no URL, path, email, phone, markup or unkind word; '' otherwise."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or len(s) > limit or "<" in s or UNKIND.search(s) or _URL.search(s) or _PATHLIKE.search(s) \
            or _EMAIL.search(s) or _PHONE.search(s):
        return ""
    return s


def _slug(source) -> str:
    return _SLUG_OF.get(str(source or "").lower(), re.sub(r"[^a-z0-9]+", "-", str(source or "").lower()).strip("-"))


def _cid(claim_id) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(claim_id or ""))[:32]


# ---------- public helpers ----------

def artifact_href(run_id, claim_id, source) -> str:
    """'/artifacts/<run_id>#a-<claim_id>-<source slug>'. `source` may be a key (record) or a name (CoStar)."""
    return f"/artifacts/{run_id}#a-{_cid(claim_id)}-{_slug(source)}"


def deciding_source(verdict) -> str | None:
    """The source key whose evidence backs the verdict's found value: on a mismatch the report's truthful-credit
    rule (server.report_a._credit: confidence.decided_by, else the verdict's source; never a source that says
    something else); on a match the verdict's source. None for couldn't-check or when no source is named."""
    if not isinstance(verdict, dict):
        return None
    kind = verdict.get("verdict")
    if kind in ("contradicted", "close"):
        try:
            from server.report_a import _credit
            keys = _credit(verdict)[0]
            if keys:
                return keys[0]
        except Exception:  # noqa: BLE001  the report module is being edited elsewhere: same rule, local copy
            conf = verdict.get("confidence") if isinstance(verdict.get("confidence"), dict) else {}
            for k in (conf.get("decided_by"), verdict.get("source")):
                if k in SRC_NAME:
                    return k
        return None
    if kind == "confirmed" and verdict.get("source") in SRC_NAME:
        return verdict["source"]
    return None


# ---------- run reading ----------

def _parts(run: dict):
    claims, order, verdicts, evidence, steps = {}, [], {}, [], []
    times = run.get("event_times") or []
    t0 = run.get("started_at")
    for i, e in enumerate(run.get("events") or []):
        if not isinstance(e, dict):
            continue
        t = None
        if i < len(times) and isinstance(times[i], (int, float)) and isinstance(t0, (int, float)):
            t = max(0.0, times[i] - t0)
        typ = e.get("type")
        if typ == "claims":
            for c in e.get("claims") or []:
                if isinstance(c, dict) and c.get("id") and c["id"] not in claims:
                    claims[c["id"]] = c
                    order.append(c["id"])
        elif typ == "verdict" and isinstance(e.get("verdict"), dict):
            verdicts[e["verdict"].get("claim_id")] = e["verdict"]
        elif typ == "evidence" and isinstance(e.get("evidence"), dict):
            evidence.append((e["evidence"], t))
        elif typ == "step":
            steps.append((e.get("agent"), str(e.get("text") or ""), t))
    return claims, order, verdicts, evidence, steps


def _tool_of(ev: dict) -> tuple[str, str]:
    """(agent, tool) that produces this kind of evidence (harness/graph/agent.py's tool list)."""
    src, obs = ev.get("source"), ev.get("observed") or {}
    if src == "county":
        return "paper", "county_lookup"
    if src == "record":
        if any(k in obs for k in ("plan", "floorplans", "unit_row", "unit_listed", "example_unit", "kit_plan",
                                  "plan_missing", "unit")):
            return "inside", "check_floor_plans"
        return "paper", "building_record"
    if src == "aerial":
        if obs.get("feature_check") and obs.get("claim_ids"):
            return "outside", "outdoor_feature_photos"
        return "outside", "measure_building"
    if src == "scan":
        if "room_type" in obs or "supports_claim" in obs:
            return "inside", "look_at_scan_photos"
        if obs.get("feature_check"):
            return "inside", "unit_feature_photos"
        return "inside", "check_floor_plans"
    return "", ""


def _img_url(p, images: dict) -> str | None:
    """An /img/<token> URL for a picture: as stored, or (a raw cache path) the token app.py would give it, only when
    the run's own image map names that token (so the server can serve it); else None. Never a path."""
    if not isinstance(p, str) or not p:
        return None
    if IMG_RE.match(p):
        return p
    tok = hashlib.sha1(p.encode("utf-8")).hexdigest()[:16]
    return f"/img/{tok}" if isinstance(images, dict) and images.get(tok) == p else None


def _short_caption(cap) -> str:
    try:
        from server.report import _short_caption as sc
        return _safe(sc(cap), 90)
    except Exception:  # noqa: BLE001
        return _safe(str(cap or "").split(":")[0], 90)


def _same_plan(a, b) -> bool:
    try:
        from server.report import _same_plan as sp
        return sp(a, b)
    except Exception:  # noqa: BLE001
        return bool(a and b) and str(a).strip().lower() == str(b).strip().lower()


def _relevant(claim: dict, evs: list) -> list:
    """This source's evidence that bears on the claim: the claim field's rule (the judge's own), else a simple
    fallback (the claim's kind observed, same plan; picture checks naming the claim)."""
    try:
        from server.claim_field import _relevant as rel
        return rel(claim, evs)
    except Exception:  # noqa: BLE001
        pass
    kind, cid, label = claim.get("kind"), claim.get("id"), claim.get("label")
    out = []
    for e in evs:
        obs = e.get("observed") or {}
        if e.get("status") != "ok":
            continue
        if obs.get("feature_check"):
            if cid in (obs.get("claim_ids") or []):
                out.append(e)
            continue
        if kind in obs and (not label or not obs.get("plan") or _same_plan(obs.get("plan"), label)):
            out.append(e)
    return out


def _field_cells(run: dict) -> dict:
    """{claim_id: cells} from the report's claim field (each source judged alone; colours reconciled with the
    verdict), or {} when it cannot be built."""
    try:
        from server.claim_field import claim_field
        return {c.get("id"): c.get("cells") or {} for c in claim_field(run).get("claims") or []}
    except Exception:  # noqa: BLE001
        return {}


# ---------- one source's artifact: rows ----------

def _clean(obs) -> dict:
    try:
        from server.claim_field import clean_observed
        return clean_observed(obs)
    except Exception:  # noqa: BLE001
        return {k: v for k, v in (obs or {}).items() if isinstance(v, (int, float, str)) and not isinstance(v, bool)
                and not re.search(r"(?i)name|owner|contact|phone|email|path|url|text|note", str(k))}


def _put(rows: list, label: str, value) -> None:
    value = str(value if value is not None else "").strip()
    if value and label not in {r[0] for r in rows}:
        rows.append((label, value))


def _rooms_words(labels) -> str:
    counts: dict = {}
    for x in labels or []:
        name = str((x.get("label") or x.get("name")) if isinstance(x, dict) else x).strip()
        if name.lower() in ("void", "other", "unknown", ""):
            continue
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in counts.items())


FEATURE_WORDS = {"washer_dryer": "washer and dryer", "walk_in_closet": "walk-in closet", "ev_charging": "EV charging",
                 "rooftop_deck": "rooftop deck", "bbq": "grills", "dog_park": "dog park", "hot_tub": "hot tub",
                 "sport_court": "sport court"}
PRESENCE_WORDS = {"seen": "Seen", "not_seen": "Not seen (a photo can't prove absence)",
                  "unclear": "Not clear in the photos", "skipped": "Not looked at"}


def _presence_rows(rows: list, obs: dict) -> None:
    if not obs.get("feature_check"):
        return
    if obs.get("feature"):
        _put(rows, "Looked for", _cap(FEATURE_WORDS.get(obs["feature"], str(obs["feature"]).replace("_", " "))))
    if obs.get("presence"):
        conf = _num(obs.get("presence_confidence"))
        word = PRESENCE_WORDS.get(str(obs["presence"]), "")
        if word and conf is not None and 0 <= conf <= 1:
            word += f", confidence {conf:.2f}"
        _put(rows, "In the photos", word)
    if obs.get("how") == "scan_title" and obs.get("scan_title"):
        _put(rows, "Found by", "the 3D scan's title")
    if obs.get("outlined"):
        _put(rows, "Detected and outlined", ", ".join(str(x).replace("_", " ") for x in obs["outlined"]))
    if obs.get("photo_look") == "not_needed":
        _put(rows, "Photo look", "Not needed: the property data answered")


def _rows_record(evs: list) -> tuple[list, list, str]:
    rows, plans = [], []
    for ev in evs:
        o = _clean(ev.get("observed"))
        _put(rows, "Floor plan on the listing", o.get("plan"))
        if o.get("kit_plan") and str(o.get("kit_plan")) != str(o.get("plan") or ""):
            _put(rows, "Plan in the CoStar record", o["kit_plan"])
        _put(rows, "Unit", o.get("unit"))
        _put(rows, "Bedrooms", _n(o.get("bedrooms")))
        _put(rows, "Bathrooms", _n(o.get("bathrooms")))
        _put(rows, "Floor area", _n(o.get("area_sqft"), "sq ft"))
        if _num(o.get("area_sqft_min")) and _num(o.get("area_sqft_max")):
            _put(rows, "Sizes", f"{_n(o['area_sqft_min'])}–{_n(o['area_sqft_max'], 'sq ft')}")
        _put(rows, "Floors", _n(o.get("stories")))
        _put(rows, "Year built", _n(o.get("year_built")).replace(",", ""))
        _put(rows, "Homes in the building", _n(o.get("unit_count")))
        _put(rows, "Building size", _n(o.get("building_sf"), "sq ft"))
        ams = o.get("amenities")
        if isinstance(ams, list) and ams:
            _put(rows, "Amenities on record", ", ".join(str(a) for a in ams[:14]) + (" …" if len(ams) > 14 else ""))
        if isinstance(o.get("floorplans"), list) and not plans:
            plans = [p for p in o["floorplans"] if isinstance(p, dict)]
        _put(rows, "How the plan was linked", _safe(o.get("plan_match"), 400))
    return rows, plans, "As of CoStar's last research of the building (the record gives no date)"


def _rows_county(evs: list) -> tuple[list, str]:
    rows, asof = [], ""
    for ev in evs:
        raw = ev.get("observed") or {}
        o = _clean(raw)
        _put(rows, "Floors", _n(o.get("stories")))
        _put(rows, "Year built", _n(o.get("year_built")).replace(",", ""))
        homes = _n(o.get("unit_count"))
        if homes and raw.get("units_estimated"):
            homes += " (added up over several parcels)"
        _put(rows, "Homes", homes)
        _put(rows, "Building size", _n(o.get("building_sf"), "sq ft"))
        _put(rows, "Land use", _safe(o.get("land_use"), 80))
        ot = str(raw.get("owner_type") or "").lower()
        _put(rows, "Owner", {"company": "A company", "individual": "An individual"}.get(ot, ""))
        _put(rows, "Parcel address", _safe(o.get("parcel_address"), 90))
        _put(rows, "Matched by", {"point": "the map point", "address": "the street address"}.get(
            str(raw.get("parcel_match") or ""), ""))
        n311 = None
        for k, v in raw.items():
            if "311" in str(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
                n311 = int(v)
        calls = raw.get("city_calls") if isinstance(raw.get("city_calls"), dict) else {}
        if n311 is None and isinstance(calls.get("total"), (int, float)):
            n311 = int(calls["total"])
        if n311:
            extra = []
            if _num(calls.get("radius_m")):
                extra.append(f"within {_n(calls['radius_m'])} m")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(calls.get("since") or "")):
                extra.append(f"since {calls['since']}")
            _put(rows, "311 requests nearby", f"{n311:,}" + (f" ({', '.join(extra)})" if extra else ""))
        _put(rows, "Published by", _safe(o.get("source_name"), 120))
        m = re.search(r"(?i)(?:tax_year|tax year|roll year|roll_year)\s*(\d{4})", str(ev.get("detail") or ""))
        if m and not asof:
            asof = f"As of the {m.group(1)} county roll"
    return rows, asof or "As of the county's current roll (the record gives no year)"


def _rows_aerial(evs: list) -> tuple[list, str]:
    rows, asof = [], ""
    for ev in evs:
        raw = ev.get("observed") or {}
        o = _clean(raw)
        _put(rows, "Roof height", _n(o.get("height_m"), "m above the ground"))
        if _num(o.get("stories")) and not raw.get("illustrative"):
            rng = raw.get("stories_range")
            extra = ""
            if isinstance(rng, list) and len(rng) == 2 and rng[0] != rng[1]:
                extra = f" (between {_n(rng[0])} and {_n(rng[1])})"
            _put(rows, "Floors, from the roof height", f"About {_n(o['stories'])}{extra}")
        prop = raw.get("property") if isinstance(raw.get("property"), dict) else None
        if prop:
            seen = []
            for k, val in prop.items():
                if isinstance(val, dict) and val.get("present") is True:
                    w = FEATURE_WORDS.get(k, k.replace("_", " "))
                    if _num(val.get("count")) and _num(val["count"]) > 1:
                        w = f"{_n(val['count'])} × {w}"
                    if _num(val.get("area_sqft")):
                        w += f" (about {_n(val['area_sqft'], 'sq ft')})"
                    if val.get("stable") is False:
                        w += ", seen in one survey"
                    seen.append(w)
            _put(rows, "Property data shows", _cap(", ".join(seen)) if seen else "None of the outdoor features it marks")
            if _num(prop.get("tree_cover_pct")):
                _put(rows, "Tree cover", f"{_n(prop['tree_cover_pct'])}%")
            if _num(prop.get("vehicles")):
                _put(rows, "Vehicles at survey time", _n(prop["vehicles"]))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(prop.get("captured") or "")):
                _put(rows, "Property survey", prop["captured"])
                asof = asof or f"Survey of {prop['captured']}"
        if raw.get("angled_views"):
            _put(rows, "Angled views", _n(raw["angled_views"]) if _num(raw["angled_views"]) else "Yes")
        _presence_rows(rows, raw)
        if o.get("looked_at") and isinstance(o.get("looked_at"), str):
            _put(rows, "Looked at", _safe(_cap(o["looked_at"]), 120))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw.get("capture_date") or "")):
            _put(rows, "Photos taken", raw["capture_date"])
            asof = asof or f"Photos of {raw['capture_date']}"
    return rows, asof


def _rows_scan(evs: list) -> tuple[list, str]:
    rows = []
    for ev in evs:
        raw = ev.get("observed") or {}
        o = _clean(raw)
        title = o.get("model_name") or o.get("scan_title")
        if title:
            _put(rows, "Scan title", f"“{_safe(title, 120)}”")
        _put(rows, "Floor plan on the listing", o.get("plan"))
        _put(rows, "Unit", o.get("unit"))
        bed, bath = _n(o.get("bedrooms")), _n(o.get("bathrooms"))
        frm = {"model_name": " (from the title)", "room_labels": " (from room labels)"}.get(str(o.get("counts_from") or ""), "")
        _put(rows, "Bedrooms", bed + frm if bed else "")
        _put(rows, "Bathrooms", bath + frm if bath else "")
        _put(rows, "Rooms the scan labels", _rooms_words(raw.get("room_labels")))
        area = _num(o.get("area_sqft")) or (_num(o.get("area_m2")) * 10.7639 if _num(o.get("area_m2")) else None)
        if area:
            _put(rows, "Floor area measured", f"{_n(round(area), 'sq ft')} of scanned floor")
        if _num(o.get("floors")):
            _put(rows, "Levels", _n(o["floors"]))
        if _num(o.get("panoramas")):
            _put(rows, "Panoramas", _n(o["panoramas"]))
        if _num(o.get("image_count")):
            _put(rows, "Stills saved", _n(o["image_count"]))
        _presence_rows(rows, raw)
        if o.get("looked_at") and isinstance(o.get("looked_at"), str):
            _put(rows, "Looked at", _safe(_cap(o["looked_at"]), 120))
        _put(rows, "How the plan was linked", _safe(o.get("plan_match"), 400))
    return rows, "As scanned (the scan gives no date)"


# ---------- one claim ----------

def _value_of(text) -> str:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text or ""))
    if m:
        n = _num(m.group(0))
        return f"{n:g}" if n is not None else m.group(0)
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


JUNK_FOUND = {"", "no data", "not checked", "nothing to measure", "no source covers this", "unclear", "sources differ"}


def _read_value(cell: dict, rows: list) -> str:
    for k in ("found", "short"):
        s = _safe(cell.get(k), 140)
        if s and s.lower() not in JUNK_FOUND:
            return s
    return ""


def _tag(key: str, decider, verdict: dict, cell: dict, credited: list, usable: bool) -> str:
    """'Decided this' | 'Agrees' | 'Differs' | 'Had nothing'. Agrees / Differs are against the found value when a
    source decided the claim, else against the listing (a couldn't-check)."""
    if key == decider:
        return "Decided this"
    st = cell.get("state") or "none"
    alone = cell.get("verdict")
    if not usable or st in ("none", "data") or alone not in ("confirmed", "contradicted", "close"):
        return "Had nothing"
    if decider:
        if key in credited:
            return "Agrees"
        if verdict.get("verdict") == "confirmed":
            return "Agrees" if alone == "confirmed" else "Differs"
        want = _value_of(verdict.get("found"))
        return "Agrees" if want and _value_of(cell.get("found")) == want else "Differs"
    return "Agrees" if alone == "confirmed" else "Differs"


def _listing_words(R, run, v: dict, c: dict) -> tuple[str, bool]:
    try:
        return R._strike_text(run, v, c)
    except Exception:  # noqa: BLE001
        q = c.get("quote") or v.get("claimed") or ""
        return str(q), bool(c.get("quote"))


def _trace(evs: list, info: dict, steps: list, harness: dict, timing: dict) -> str:
    bits, seen = [], set()
    total = _num(timing.get("total_s")) if isinstance(timing, dict) else None
    model_s = timing.get("model_s") if isinstance(timing, dict) and isinstance(timing.get("model_s"), dict) else {}
    direct = set(harness.get("direct") or []) if isinstance(harness, dict) else set()
    legacy = isinstance(harness, dict) and harness.get("path") == "legacy"
    for ev in evs:
        agent, tool = _tool_of(ev)
        t = info.get(id(ev))
        key = (agent, tool)
        if not tool or key in seen:
            continue
        seen.add(key)
        who = AGENT_NAME.get(agent, _cap(agent))
        if legacy:
            who += " (fixed plan)"
        elif agent in direct and tool in ("measure_building", "county_lookup", "building_record"):
            who += " (run called it directly)"
        s = f"{who} → {tool}()"
        if t is not None:
            s += f" · arrived at {t:.1f} s" + (f" of {total:.1f} s" if total else "")
        bits.append(s)
    agents = []
    for ev in evs:
        a = _tool_of(ev)[0]
        if a and a not in agents:
            agents.append(a)
    ms = [f"{AGENT_NAME.get(a, a).split()[0].lower()} model {float(model_s[a]):.1f} s" for a in agents
          if isinstance(model_s.get(a), (int, float))]
    method = ""
    for ev in evs:
        d = re.split(r"(?<=[.;])\s", str(ev.get("detail") or "").strip(), maxsplit=1)[0].rstrip(".;")
        if d.count("(") != d.count(")"):
            d = d[:d.rfind("(")].rstrip(" ,:")
        d = _safe(d, 130)
        if d and not re.search(r"\d{7,}", d):
            method = d
            break
    out = " · ".join(bits + ms)
    if method:
        out += (" · " if out else "") + method
    return out


def _card(R, run, raw_run: dict, v: dict, c: dict, key: str, evs: list, nothing: list, cell: dict, decider,
          credited: list, info: dict, steps: list) -> str:
    images = raw_run.get("images") or {}
    cid = c.get("id") or v.get("claim_id")
    usable = bool(evs)
    tag = _tag(key, decider, v, cell, credited, usable)
    plans = []
    if key == "record":
        rows, plans, asof = _rows_record(evs or nothing)
    elif key == "county":
        rows, asof = _rows_county(evs or nothing)
    elif key == "aerial":
        rows, asof = _rows_aerial(evs or nothing)
    else:
        rows, asof = _rows_scan(evs or nothing)
    # pictures: the verdict's own (deciding source only), this source's relevant evidence, then same-plan stills
    pool = []
    if key == decider and v.get("image"):
        pool.append((v.get("image"), ""))
    for ev in evs:
        pool += [(im.get("path"), im.get("caption")) for im in ev.get("images") or [] if isinstance(im, dict)]
    if key == "scan" and c.get("label"):
        for ev in run.evidence:
            o = ev.get("observed") or {}
            if ev.get("source") == "scan" and ev.get("status") == "ok" and not o.get("feature_check") \
                    and _same_plan(o.get("plan"), c.get("label")):
                pool += [(im.get("path"), im.get("caption")) for im in ev.get("images") or [] if isinstance(im, dict)]
    if key == "aerial" and evs and not c.get("label") and c.get("kind") not in R_SOFT:
        for ev in run.evidence:  # a building-wide claim: the aerial photos of the building are part of the artifact
            o = ev.get("observed") or {}
            if ev.get("source") == "aerial" and ev.get("status") == "ok" and not o.get("feature_check"):
                pool += [(im.get("path"), im.get("caption")) for im in ev.get("images") or [] if isinstance(im, dict)]
    caps = {}
    for ev in run.evidence:
        for im in ev.get("images") or []:
            if isinstance(im, dict) and im.get("path"):
                caps.setdefault(im["path"], im.get("caption") or "")
    pics, seen = [], set()
    for p, cap in pool:
        url = _img_url(p, images)
        if url and url not in seen:
            seen.add(url)
            pics.append((url, _short_caption(cap or caps.get(p) or "")))
    pics = pics[:MAX_IMGS]

    value = _read_value(cell, rows) if usable else ""
    summary = ""
    for ev in (evs or nothing):
        summary = _safe(ev.get("summary"), 240)
        if summary:
            break
    said = _safe(v.get("claimed"), 80)
    if not said:
        said, is_q = _listing_words(R, run, v, c)
        said = f"“{said}”" if is_q else said
    alone = cell.get("verdict")
    if usable and alone in ALONE_WORD:
        cmp = f"{SRC_NAME[key]} alone {ALONE_WORD[alone]} ({said})"
    elif usable and c.get("kind") in R_SOFT and str(cell.get("found") or "").lower().startswith("not seen"):
        cmp = "Not seen; a photo can't prove something is absent, so this is not a mismatch"
    elif usable:
        cmp = "Nothing here could be compared with the listing"
    else:
        cmp = "Nothing usable for this claim"
    tour = ""
    if key == "scan" and usable:
        try:
            from server.tour import tour_href, tour_target
            if RUN_ID_RE.match(str(raw_run.get("run_id") or "")) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(cid)) \
                    and tour_target(raw_run, cid):
                tour = f"<a class=tour href='{_e(tour_href(raw_run, cid))}'>See it in 3D →</a>"
        except Exception:  # noqa: BLE001
            tour = ""
    tcls = {"Decided this": "t-dec", "Agrees": "t-agr", "Differs": "t-dif"}.get(tag, "t-nil")
    h = [f"<article class='card{' dec' if tag == 'Decided this' else ''}' id='a-{_e(_cid(cid))}-{SRC_SLUG[key]}' "
         f"data-src={key}><header class=ch><p class=sn>{SRC_NAME[key]} <span class=sw>{_e(SRC_WHAT[key])}</span></p>"
         f"<span class='tag {tcls}'>{_e(tag)}</span></header>"]
    if value:
        h.append(f"<p class=val><span class=k>Value read</span> {_e(_cap(value))}</p>")
    h.append(f"<p class=cmp>{_e(_cap(cmp))}.</p>")
    if summary:
        h.append(f"<p class=sum>{_e(summary)}</p>")
    if pics:
        h.append("<div class=pics>" + "".join(
            f"<a href='{_e(u)}' target=_blank rel=noopener><img src='{_e(u)}' alt='{_e(cp or 'Picture from ' + SRC_NAME[key])}'"
            f" loading=lazy onerror=\"this.parentNode.remove()\">"
            + (f"<span>{_e(cp)}</span>" if cp else "") + "</a>" for u, cp in pics) + "</div>")
    if rows:
        h.append("<dl class=rows>" + "".join(f"<div><dt>{_e(a)}</dt><dd>{_e(b)}</dd></div>" for a, b in rows) + "</dl>")
    if plans:
        shown = plans[:MAX_PLANS]
        trs = "".join(f"<tr><td>{_e(_safe(p.get('name'), 40))}</td><td>{_e(_n(p.get('beds')))}</td>"
                      f"<td>{_e(_n(p.get('baths')))}</td><td>{_e(_n(p.get('sqft')))}</td></tr>" for p in shown)
        more = f"<p class=more>+ {len(plans) - len(shown)} more plans</p>" if len(plans) > len(shown) else ""
        h.append("<table class=plans><thead><tr><th>Plan</th><th>Beds</th><th>Baths</th><th>Sq ft</th></tr></thead>"
                 f"<tbody>{trs}</tbody></table>{more}")
    if key == "aerial" and asof.startswith("Photos of") and any(a == "Photos taken" for a, _ in rows):
        asof = ""  # the date is already a row
    if asof and (rows or pics):
        h.append(f"<p class=asof>{_e(asof)}</p>")
    if tour:
        h.append(tour)
    tr = _trace(evs or nothing, info, steps, run.done.get("harness") or {}, run.done.get("timing") or {})
    if tr:
        h.append(f"<p class=trace>{_e(tr)}</p>")
    h.append("</article>")
    return "".join(h)


R_SOFT = {"amenity", "parking", "laundry", "view", "other"}


def _claim_block(R, run, raw_run, v: dict, cells: dict, info: dict, steps: list, top_id) -> tuple[str, int, set]:
    c = run.claim(v)
    if not c:
        c = {"id": v.get("claim_id"), "kind": "", "label": None}
    cid = c.get("id") or v.get("claim_id")
    by_src = {k: [e for e in run.evidence if e.get("source") == k] for k in SRC_ORDER}
    decider = deciding_source(v)
    try:
        credited = list(R._credit(v)[0]) if v.get("verdict") in ("contradicted", "close") else []
    except Exception:  # noqa: BLE001
        credited = [decider] if decider else []
    cards, used = [], set()
    order = ([decider] if decider else []) + [k for k in SRC_ORDER if k != decider]
    for k in order:
        try:
            rel = _relevant(c, by_src[k])
        except Exception:  # noqa: BLE001
            rel = []
        nothing = []
        if not rel:
            lab = c.get("label")
            for e in by_src[k]:
                o = e.get("observed") or {}
                if e.get("status") == "ok":
                    continue
                if (lab and _same_plan(o.get("plan"), lab)) or cid in (o.get("claim_ids") or []):
                    nothing.append(e)
        if not rel and not nothing and k != decider:
            continue
        if not rel and not nothing:
            continue
        cards.append(_card(R, run, raw_run, v, c, k, rel, nothing, cells.get(k) or {}, decider, credited, info,
                           steps))
        used.add(k)
    if not cards:
        return "", 0, used
    kind = c.get("kind") or ""
    what = KIND_LABEL.get(kind) or _cap(str(kind).replace("_", " ")) or "Claim"
    if c.get("label"):
        what += f" · {c['label']}"
    said, is_q = _listing_words(R, run, v, c)
    said = f"“{said}”" if is_q else _cap(said)
    verdict = v.get("verdict") if v.get("verdict") in VERDICT_WORD else "cant_check"
    try:
        score, band, why = R._conf(v)
    except Exception:  # noqa: BLE001
        score, band, why = None, "", ""
    conf = ""
    if score is not None:
        conf = f"<span class=conf>Confidence {score}" + (f" · {_e(band)}" if band else "") + "</span>"
    elif band:
        conf = f"<span class=conf>{_e(_cap(band))} confidence</span>"
    reason = ""
    try:
        reason = R._kind_text(R._first_sentence(v.get("reason") or ""))
    except Exception:  # noqa: BLE001
        reason = _safe(v.get("reason"), 160)
    found = _safe(v.get("found"), 100)
    if found.lower() in ("no data", "not checked", ""):
        found = ""
    pick = "<span class=pick>Top finding</span>" if cid == top_id else ""
    off = verdict in ("contradicted", "close")
    head = (f"<header class=clh><p class=kind>{_e(what)}{pick}</p>"
            f"<p class=said>{_e(said)}</p>"
            f"<p class=res><span class='vw{' off' if off else ''}'>{_e(VERDICT_WORD[verdict])}</span>"
            + (f"<span class=fnd>{_e(_cap(found))}</span>" if found and verdict != "confirmed" else "")
            + conf + "</p>"
            + (f"<p class=why>{_e(reason)}</p>" if reason else "")
            + (f"<p class=why>{_e(why)}</p>" if why and why != reason else "") + "</header>")
    srcs = " ".join(sorted(used))
    return (f"<section class=claim id='c-{_e(_cid(cid))}' data-verdict={verdict} data-srcs='{srcs}'>{head}"
            f"<div class=cards>{''.join(cards)}</div></section>"), len(cards), used


# ---------- page ----------

CSS = """
:root{--ink:#111;--blue:#1a4be0;--mute:#6b6b6b;--ghost:#858585;--soft:#f4f4f4;--line:#d9d9d9;--bg:#fff;
--gut:clamp(16px,5vw,72px)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 Newsreader,Georgia,'Times New Roman',serif;
-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:var(--ink)}a:hover{color:var(--blue)}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
img{max-width:100%;display:block}
.wrap{max-width:1440px;margin:0 auto;padding:0 var(--gut)}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;min-height:80px;gap:8px 16px}
.brand{font-size:19px;font-weight:500;letter-spacing:-.01em;text-decoration:none}
.back{display:inline-flex;align-items:center;gap:6px;min-height:44px;font-size:15px;text-decoration:none}
h1{margin:clamp(8px,2vw,24px) 0 10px;font-weight:300;font-size:clamp(46px,7vw,104px);line-height:.95;letter-spacing:-.045em}
.where{margin:0;font-size:clamp(19px,2vw,24px);letter-spacing:-.01em}
.lede{margin:4px 0 0;color:var(--mute);font-size:16px}
.filters{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--ink);
margin-top:clamp(20px,3vw,36px);padding:10px 0;display:flex;flex-direction:column;gap:6px}
.fr{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.fr>span{font-size:13px;color:var(--mute);min-width:62px}
.chip{font:inherit;font-size:14px;min-height:34px;padding:0 12px;background:var(--bg);color:var(--ink);
border:1px solid var(--line);cursor:pointer;border-radius:0}
.chip:hover{border-color:var(--ink)}
.chip[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.chip b{font-weight:400;color:var(--ghost);margin-left:6px}.chip[aria-pressed=true] b{color:#bbb}
.grp{margin-top:clamp(28px,4vw,52px)}
.gh{margin:0 0 4px;font-weight:300;font-size:clamp(28px,3.2vw,44px);letter-spacing:-.035em;line-height:1.1}
details.grp>summary{cursor:pointer;list-style:none}
details.grp>summary::-webkit-details-marker{display:none}
details.grp>summary .gh::after{content:' +';color:var(--mute)}
details.grp[open]>summary .gh::after{content:' −'}
.claim{border-top:1px solid var(--line);padding:clamp(18px,2vw,28px) 0;scroll-margin-top:120px}
.clh{display:grid;grid-template-columns:minmax(0,1fr);gap:2px;margin-bottom:14px;max-width:900px}
.kind{margin:0;font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);display:flex;
flex-wrap:wrap;gap:8px;align-items:center}
.pick{text-transform:none;letter-spacing:0;font-size:12px;color:#fff;background:var(--blue);padding:1px 8px}
.said{margin:0;font-size:clamp(22px,2.4vw,30px);line-height:1.2;letter-spacing:-.02em;overflow-wrap:anywhere}
.res{margin:4px 0 0;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;font-size:16px}
.vw{font-weight:500}.vw.off{color:var(--blue)}
.fnd{color:var(--ink)}.conf{color:var(--mute);font-variant-numeric:tabular-nums}
.why{margin:2px 0 0;font-size:15px;color:var(--mute);max-width:80ch}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:12px}
.card{border:1px solid var(--line);padding:14px 16px 12px;display:flex;flex-direction:column;gap:8px;min-width:0;
scroll-margin-top:130px;transition:box-shadow .3s ease,border-color .3s ease}
.card.dec{border-color:var(--ink)}
.card.hit,.card:target{border-color:var(--blue);box-shadow:0 0 0 2px var(--blue)}
.ch{display:flex;justify-content:space-between;align-items:baseline;gap:10px;border-bottom:1px solid var(--line);
padding-bottom:8px}
.sn{margin:0;font-size:20px;font-weight:500;letter-spacing:-.01em}
.sw{font-size:13px;font-weight:400;color:var(--mute);margin-left:4px}
.tag{font-size:12px;letter-spacing:.03em;padding:2px 8px;border:1px solid var(--line);white-space:nowrap;color:var(--mute)}
.t-dec{background:var(--blue);border-color:var(--blue);color:#fff}
.t-agr{border-color:var(--ink);color:var(--ink)}
.t-dif{border-color:var(--blue);color:var(--blue)}
.val{margin:0;font-size:19px;line-height:1.3}
.k{display:block;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
.cmp{margin:0;font-size:15px}
.sum{margin:0;font-size:14px;color:var(--mute)}
.pics{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:6px}
.pics a{display:flex;flex-direction:column;gap:2px;text-decoration:none;font-size:12px;color:var(--mute);min-width:0}
.pics img{width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
.pics span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rows{margin:0;display:grid;grid-template-columns:minmax(0,1fr);border-top:1px solid #ececec}
.rows div{display:grid;grid-template-columns:minmax(90px,38%) minmax(0,1fr);gap:10px;padding:4px 0;
border-bottom:1px solid #ececec;font-size:14px}
.rows dt{color:var(--mute)}.rows dd{margin:0;overflow-wrap:anywhere}
.plans{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
.plans th{text-align:left;font-weight:400;color:var(--mute);border-bottom:1px solid var(--line);padding:3px 4px 3px 0}
.plans td{border-bottom:1px solid #ececec;padding:3px 4px 3px 0;overflow-wrap:anywhere}
.more{margin:0;font-size:13px;color:var(--mute)}
.asof{margin:0;font-size:13px;color:var(--mute)}
.tour{align-self:flex-start;font-size:15px;color:#fff;background:var(--ink);text-decoration:none;padding:6px 14px}
.tour:hover{background:#333;color:#fff}
.trace{margin:auto 0 0;padding-top:6px;border-top:1px dashed #e4e4e4;font:12px/1.45 ui-monospace,SFMono-Regular,
Menlo,Consolas,monospace;color:var(--ghost);overflow-wrap:anywhere}
.none{color:var(--mute);font-size:15px;margin:clamp(28px,4vw,48px) 0 0}
.hid{display:none!important}
.foot{margin-top:clamp(40px,6vw,80px);padding:20px 0 40px;border-top:1px solid var(--ink);font-size:14px;color:var(--mute);
display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 20px}
@media (max-width:600px){.filters{margin-left:calc(-1 * var(--gut));margin-right:calc(-1 * var(--gut));padding:8px var(--gut)}.fr{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}.fr::-webkit-scrollbar{display:none}.fr>span{min-width:auto;flex:none}.chip{flex:none;min-height:38px}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}.card{transition:none}}
"""

SCRIPT = r"""
(function(){
var st={src:'all',ver:'all'};
var chips=[].slice.call(document.querySelectorAll('.chip'));
var cant=document.getElementById('g-cant');
function apply(){
  chips.forEach(function(b){var f=b.dataset.f.split(':');b.setAttribute('aria-pressed',String(st[f[0]]===f[1]));});
  [].forEach.call(document.querySelectorAll('.card'),function(c){
    c.classList.toggle('hid',st.src!=='all'&&c.dataset.src!==st.src);});
  [].forEach.call(document.querySelectorAll('.claim'),function(s){
    var ok=(st.ver==='all'||s.dataset.verdict===st.ver)&&s.querySelector('.card:not(.hid)');
    s.classList.toggle('hid',!ok);});
  [].forEach.call(document.querySelectorAll('.grp'),function(g){
    g.classList.toggle('hid',!g.querySelector('.claim:not(.hid)'));});
  if(cant&&st.ver==='cant_check')cant.open=true;
}
chips.forEach(function(b){b.addEventListener('click',function(){
  var f=b.dataset.f.split(':');st[f[0]]=f[1];apply();});});
function go(){
  var id=decodeURIComponent(location.hash.slice(1));
  if(!/^(a|c)-/.test(id))return;
  var el=document.getElementById(id);if(!el)return;
  if(el.classList.contains('hid')||(el.closest('.claim')||el).classList.contains('hid')){st.src='all';st.ver='all';apply();}
  var d=el.closest('details');if(d)d.open=true;
  requestAnimationFrame(function(){el.scrollIntoView({block:'center',behavior:'instant'});
    if(el.classList.contains('card')){el.classList.add('hit');setTimeout(function(){el.classList.remove('hit');},2600);}});
}
apply();go();window.addEventListener('hashchange',go);
})();
"""


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title>"
            "<link rel=preconnect href='https://fonts.googleapis.com'>"
            "<link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
            "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;"
            "6..72,400;6..72,500&display=swap'>"
            f"<style>{CSS}</style></head><body id=top>{body}</body></html>")


def not_found_page() -> str:
    return _page("Artifact library: not found",
                 "<div class=wrap><header class=top><span class=brand>Realty Check</span></header>"
                 "<h1>Not found.</h1><p class=lede>No such check.</p></div>")


def build_artifacts(run_dict: dict) -> str:
    """The artifact library page for one saved run (full HTML)."""
    import server.report_a as R
    from server.report import Run, drop_dead_images
    raw = drop_dead_images(run_dict or {})
    run = Run(raw)
    rid = str(raw.get("run_id") or "")
    address = run.snap.get("address_text") or "Unknown address"
    _, _, _, evidence_t, steps = _parts(raw)
    info = {id(ev): t for ev, t in evidence_t}
    cells_by = _field_cells(raw)
    try:
        off = R._ranked_off(run, run.verdicts)
    except Exception:  # noqa: BLE001
        off = [v for v in run.verdicts if v.get("verdict") in ("contradicted", "close")]
    top_id = off[0].get("claim_id") if off and off[0].get("verdict") == "contradicted" else None
    ok = [v for v in run.verdicts if v.get("verdict") == "confirmed"]
    cant = [v for v in run.verdicts if v.get("verdict") not in ("contradicted", "close", "confirmed")]
    hard = [v for v in off if v.get("verdict") == "contradicted"]
    close = [v for v in off if v.get("verdict") == "close"]

    total, used_all, empty = 0, set(), 0
    vcount = {}
    groups = []
    for gid, title, vs, collapsed in (("g-off", "Doesn't match", hard, False), ("g-close", "Close", close, False),
                                      ("g-ok", "Matches", ok, False), ("g-cant", "Couldn't check", cant, True)):
        blocks = []
        for v in vs:
            try:
                html, n, used = _claim_block(R, run, raw, v, cells_by.get(v.get("claim_id")) or {}, info, steps, top_id)
            except Exception:  # noqa: BLE001  one odd claim never breaks the page
                html, n, used = "", 0, set()
            if not n:
                empty += 1
                continue
            blocks.append(html)
            total += n
            used_all |= used
            vk = v.get("verdict") if v.get("verdict") in VERDICT_WORD else "cant_check"
            vcount[vk] = vcount.get(vk, 0) + 1
        if not blocks:
            continue
        head = f"<h2 class=gh>{_e(title)} <span class=mute style='font-size:.5em;color:var(--mute)'>{len(blocks)}</span></h2>"
        if collapsed:
            groups.append(f"<details class=grp id={gid}><summary>{head}</summary>{''.join(blocks)}</details>")
        else:
            groups.append(f"<section class=grp id={gid}>{head}{''.join(blocks)}</section>")

    src_n = len(used_all)
    lede = (f"{total} artifact{'s' if total != 1 else ''} from {src_n} source{'s' if src_n != 1 else ''}"
            if total else "No source gave us anything to show for this check.")
    card_counts = {}
    for k in SRC_ORDER:
        card_counts[k] = 0
    for g in groups:
        for k in SRC_ORDER:
            card_counts[k] += g.count(f"data-src={k}>")
    chips_src = "".join(f"<button type=button class=chip data-f='src:{k}' aria-pressed=false>{SRC_NAME[k]}"
                        f"<b>{card_counts[k]}</b></button>" for k in SRC_ORDER if card_counts[k])
    chips_ver = "".join(f"<button type=button class=chip data-f='ver:{k}' aria-pressed=false>{VERDICT_WORD[k]}"
                        f"<b>{vcount[k]}</b></button>" for k in ("contradicted", "close", "confirmed", "cant_check")
                        if vcount.get(k))
    filters = ("<nav class=filters aria-label='Filter artifacts'>"
               f"<div class=fr><span>Source</span><button type=button class=chip data-f='src:all' aria-pressed=true>"
               f"All</button>{chips_src}</div>"
               f"<div class=fr><span>Result</span><button type=button class=chip data-f='ver:all' aria-pressed=true>"
               f"All</button>{chips_ver}</div></nav>") if total else ""
    back = f"/report/{rid}" if RUN_ID_RE.match(rid) else "/"
    none = (f"<p class=none>{empty} claim{'s' if empty != 1 else ''} had nothing from any source; "
            f"<a href='{_e(back)}#checked'>see the report</a>.</p>") if empty else ""
    body = ("<div class=wrap>"
            f"<header class=top><a class=brand href='{_e(back)}'>Realty Check</a>"
            f"<a class=back href='{_e(back)}'>← Back to the report</a></header>"
            "<h1>Artifact library</h1>"
            f"<p class=where>{_e(address)}</p><p class=lede>{_e(lede)}. Each card is what one source gave us for one "
            "claim; the source that decided it is marked.</p>"
            f"{filters}{''.join(groups)}{none}"
            f"<footer class=foot><span>Check {_e(rid)}</span><a href='{_e(back)}'>Back to the report</a></footer>"
            f"</div><script>{SCRIPT}</script>")
    return _page(f"Artifact library: {address}", body)


def register(app) -> None:
    """Add GET /artifacts/{run_id} to the FastAPI app (same run loading as the report route; 404 page when
    unknown)."""
    from fastapi.responses import HTMLResponse

    @app.get("/artifacts/{run_id}", response_class=HTMLResponse)
    def artifacts_page(run_id: str):
        from server.app import load_run
        run = load_run(run_id)
        if run is None:
            return HTMLResponse(not_found_page(), status_code=404)
        return HTMLResponse(build_artifacts(run), headers={"Cache-Control": "no-store"})
