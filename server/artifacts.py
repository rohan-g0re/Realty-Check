"""Artifact library: GET /artifacts/<run_id>, one page per run that shows what each source actually gave us.

The report stays simple; this page shows the agent's depth, laid out like the report (Newsreader, hairlines, one blue):
  - hero: big staggered title, the address, one summary line;
  - a dark band, one tile per source (CoStar, Matterport, Vexcel, County): artifacts held, how often it decided /
    agreed / differed; a tile is also the source filter;
  - "The exhibits": a master-detail browser. The left rail lists the claims any source spoke to: mismatches first,
    the most certain on top with the confidence score shown large (fainter blue as it drops), then matches, then
    couldn't-check collapsed. Arrow keys move along it. The right pane shows the selected claim as an exhibit: the
    listing's words vs. what was found, the confidence figure, then one PLATE per source in source order, each with
    the anchor `a-<claim_id>-<source slug>` (costar | matterport | vexcel | county): a tag "Decided this" (the
    report's own truthful-credit rule, `deciding_source`) / "Agrees" / "Differs" / "Had nothing", the value read in
    large type, a key-value table (long ones behind "Show all N"), where / when (the verdict's `citations` when the
    run has them, else read from the evidence), a picture gallery with an in-page lightbox, "See it in 3D", and a
    grey trace line (agent -> tool, timing). A source with nothing for the claim gets a quiet compact plate.
    A deep link selects its claim, opens its group, and scrolls to and marks the plate.

Other pages link here with `artifact_href(run_id, claim_id, source)`. Privacy (same rules as the report): no people's
names or contacts (owner type only), no parcel / property / scan ids, 311 as a count only, no file paths, query URLs
or signed URLs (pictures only as /img/<token>), no keys, never unkind words; absence is never a mismatch.

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
    if not usable or st == "none" or alone not in ("confirmed", "contradicted", "close"):
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
        d = re.sub(r"\s*\b(?:for |of )?(?:model|scan)\s+(?=[\w-]*[A-Z])(?=[\w-]*[a-z])(?=[\w-]*\d|[\w-]{10,})[\w-]{8,24}\b",
                   "", d)
        d = re.sub(r"\b(?=[\w-]*[A-Z])(?=[\w-]*[a-z])(?=[\w-]*\d)[\w-]{9,24}\b", "", d)
        d = re.sub(r"\s*\((?=[\w-]*[A-Z])(?=[\w-]*[a-z])[\w-]{8,24}\)", "", d).strip(" ,:")
        d = _safe(d, 130)
        if d and not re.search(r"\d{5,}", d):
            method = d
            break
    out = " · ".join(bits + ms)
    if method:
        out += (" · " if out else "") + method
    return out


R_SOFT = {"amenity", "parking", "laundry", "view", "other"}
CIT_KEY = {"costar": "record", "matterport": "scan", "vexcel": "aerial", "county": "county",
           "record": "record", "scan": "scan", "aerial": "aerial"}
ROLE_TAG = {"decided": "Decided this", "agrees": "Agrees", "differs": "Differs"}
KV_SHOW = 6       # facts shown before "Show all N"
PLANS_SHOW = 5    # floor plans shown before "Show all N"
SRC_GLYPH = {  # a small line drawing per source, for the source strip when it holds no picture
    "record": "<svg viewBox='0 0 80 60' aria-hidden=true><rect x=14 y=6 width=52 height=48 fill=none stroke=currentColor "
              "stroke-width=1.4/><path d='M22 18h36M22 26h36M22 34h24M22 42h30' stroke=currentColor stroke-width=1.4/></svg>",
    "scan": "<svg viewBox='0 0 80 60' aria-hidden=true><path d='M12 46 40 54 68 46V18L40 10 12 18Z M12 18 40 26 68 18 M40 26V54' "
            "fill=none stroke=currentColor stroke-width=1.4 stroke-linejoin=round/></svg>",
    "aerial": "<svg viewBox='0 0 80 60' aria-hidden=true><rect x=8 y=6 width=64 height=48 fill=none stroke=currentColor "
              "stroke-width=1.4/><rect x=24 y=18 width=22 height=18 fill=none stroke=currentColor stroke-width=1.4/>"
              "<path d='M52 40h12v8H52Z' fill=none stroke=currentColor stroke-width=1.4/></svg>",
    "county": "<svg viewBox='0 0 80 60' aria-hidden=true><path d='M10 24 40 8 70 24Z M16 28v20M28 28v20M40 28v20M52 28v20M64 28v20"
              " M10 52h60' fill=none stroke=currentColor stroke-width=1.4 stroke-linejoin=round/></svg>",
}


def _where(s) -> str:
    """A citation's 'where', fit to show: no parcel / property / scan ids, no link, no path; '' otherwise."""
    s = str(s or "")
    s = re.sub(r"(?i),?\s*\bparcels?\s*(?:no\.?|number|#)?\s*[\w-]*\d[\w-]*", "", s)
    s = re.sub(r"(?i)\b(property|record|account|acct|model|scan id)\s*#?\s*\d[\w-]*", r"\1 record", s)
    s = re.sub(r"(?i)\brecord record\b|\bproperty record record\b", "record", s)
    s = re.sub(r"\s*\((?=[\w-]*[A-Z])(?=[\w-]*[a-z])[\w-]{8,24}\)", "", s)  # a scan's id in brackets
    s = re.sub(r"[\w-]*\d{5,}[\w-]*", "", s)
    s = re.sub(r"\s+,", ",", re.sub(r"\s{2,}", " ", s)).strip(" ,;")
    return _safe(s, 160)


def _cites(v: dict) -> dict:
    """{source key: [citation, ...]} from the verdict's `citations` (new runs); {} for old runs."""
    out: dict = {}
    for ct in v.get("citations") or []:
        if not isinstance(ct, dict):
            continue
        k = CIT_KEY.get(str(ct.get("source") or "").strip().lower())
        if k:
            out.setdefault(k, []).append(ct)
        elif str(ct.get("source") or "").strip().lower() == "listing":
            out.setdefault("listing", []).append(ct)
    return out


def _pictures(run, raw_run: dict, v: dict, c: dict, key: str, evs: list, decider, cits: list) -> list:
    """[(url, caption)] for one plate: the verdict's own picture (deciding source only), the citation's, this source's
    relevant evidence, then same-plan stills / the building's aerial photos. /img/<token> only."""
    images = raw_run.get("images") or {}
    pool = []
    if key == decider and v.get("image"):
        pool.append((v.get("image"), ""))
    for ct in cits:
        if ct.get("image"):
            pool.append((ct.get("image"), ""))
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
                u = _img_url(im["path"], images)
                if u:
                    caps.setdefault(u, im.get("caption") or "")
    pics, seen = [], set()
    for p, cap in pool:
        url = _img_url(p, images)
        if url and url not in seen:
            seen.add(url)
            pics.append((url, _short_caption(cap or caps.get(p) or caps.get(url) or "")))
    return pics[:MAX_IMGS]


def _gallery(pics: list, name: str) -> str:
    if not pics:
        return ""
    out = []
    for i, (u, cp) in enumerate(pics):
        alt = cp or f"Picture from {name}"
        plan = " plan" if re.search(r"(?i)floor ?plan|schematic", cp or "") else ""
        out.append(f"<figure class=g><a class='pic{plan}' href='{_e(u)}' data-cap='{_e(alt)}'>"
                   f"<img src='{_e(u)}' alt='{_e(alt)}' loading=lazy decoding=async></a>"
                   + (f"<figcaption>{_e(cp)}</figcaption>" if cp else "") + "</figure>")
    n = len(pics)
    return (f"<div class='gal n{min(n, 3)}' aria-label='{n} picture{'s' if n != 1 else ''} from {_e(name)}'>"
            + "".join(out) + "</div>")


def _kv(rows: list, uid: str) -> str:
    if not rows:
        return ""
    trs = "".join(f"<div{' class=x' if i >= KV_SHOW else ''}><dt>{_e(a)}</dt><dd>{_e(b)}</dd></div>"
                  for i, (a, b) in enumerate(rows))
    more = ""
    if len(rows) > KV_SHOW + 1:
        more = (f"<button type=button class=allbtn aria-controls='{_e(uid)}' aria-expanded=false "
                f"data-more='Show all {len(rows)} facts' data-less='Show fewer'>Show all {len(rows)} facts</button>")
    else:
        trs = trs.replace("<div class=x>", "<div>")
    return f"<dl class=kv id='{_e(uid)}'>{trs}</dl>{more}"


def _plans_table(plans: list, uid: str) -> str:
    rows = [p for p in plans[:MAX_PLANS * 3] if isinstance(p, dict)]
    if not rows:
        return ""
    trs = "".join(f"<tr{' class=x' if i >= PLANS_SHOW and len(rows) > PLANS_SHOW + 1 else ''}>"
                  f"<td>{_e(_safe(p.get('name'), 40))}</td><td>{_e(_n(p.get('beds')))}</td>"
                  f"<td>{_e(_n(p.get('baths')))}</td><td>{_e(_n(p.get('sqft')))}</td></tr>" for i, p in enumerate(rows))
    more = ""
    if len(rows) > PLANS_SHOW + 1:
        more = (f"<button type=button class=allbtn aria-controls='{_e(uid)}' aria-expanded=false "
                f"data-more='Show all {len(rows)} floor plans' data-less='Show fewer'>Show all {len(rows)} floor plans</button>")
    extra = f"<p class=pnote>+ {len(plans) - len(rows)} more floor plans on record</p>" if len(plans) > len(rows) else ""
    return (f"<div class=ptw><p class=k>Floor plans on record</p><table class=plans id='{_e(uid)}'><thead><tr><th>Plan</th>"
            f"<th>Beds</th><th>Baths</th><th>Sq ft</th></tr></thead><tbody>{trs}</tbody></table>{more}{extra}</div>")


def _plate(R, run, raw_run, v: dict, c: dict, key: str, evs: list, nothing: list, cell: dict, decider,
           credited: list, info: dict, steps: list, cits: list) -> tuple[str, str, bool]:
    """(html, tag) of one source's plate for one claim. A full plate when the source gave something for the claim;
    a quiet compact one when it had nothing."""
    cid = c.get("id") or v.get("claim_id")
    aid = f"a-{_cid(cid)}-{SRC_SLUG[key]}"
    name = SRC_NAME[key]
    cit = next((ct for ct in cits if ct.get("role") in ROLE_TAG), cits[0] if cits else {})
    cit_val = _safe(cit.get("value"), 120) if cit else ""
    usable = bool(evs) or bool(cit_val)
    tag = _tag(key, decider, v, cell, credited, bool(evs))
    if cit and cit.get("role") in ("agrees", "differs") and key != decider and usable:
        tag = ROLE_TAG[cit["role"]]  # the run's own word for this source, when it gave one
    if tag == "Had nothing" and cit_val:
        tag = "Agrees" if cit.get("role") == "agrees" else "Differs" if cit.get("role") == "differs" else tag
    if not usable or tag == "Had nothing" and not evs:
        why = ""
        for ev in nothing:
            why = _safe(ev.get("summary"), 140)
            if why:
                break
        return (f"<article class='plate nil' id='{_e(aid)}' data-src={key}><p class=psrc><b>{name}</b>"
                f"<span>{_e(SRC_WHAT[key])}</span></p><p class=pnil>{_e(why or 'Had nothing for this claim.')}</p>"
                "</article>"), "Had nothing", False
    plans = []
    if key == "record":
        rows, plans, asof = _rows_record(evs or nothing)
    elif key == "county":
        rows, asof = _rows_county(evs or nothing)
    elif key == "aerial":
        rows, asof = _rows_aerial(evs or nothing)
    else:
        rows, asof = _rows_scan(evs or nothing)
    pics = _pictures(run, raw_run, v, c, key, evs, decider, cits)
    value = (_read_value(cell, rows) if evs else "") or cit_val
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
    if evs and alone in ALONE_WORD:
        cmp = f"On its own, {name} {ALONE_WORD[alone]} ({said})"
    elif evs and c.get("kind") in R_SOFT and str(cell.get("found") or "").lower().startswith("not seen"):
        cmp = "Not seen; a photo can't prove something is absent, so this is not a mismatch"
    elif evs:
        cmp = "Nothing here could be compared with the listing"
    else:
        cmp = ""
    # where / when: the run's citation when it has one, else what the evidence itself tells us
    where = _where(cit.get("where")) if cit else ""
    read = _safe(cit.get("read"), 60) if cit else ""
    date = str(cit.get("date") or "").strip() if cit else ""
    if re.fullmatch(r"\d{4}", date):
        asof = f"As of the {date} county roll" if key == "county" else f"Dated {date}"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        asof = f"Photos of {date}" if key == "aerial" else f"Dated {date}"
    if key == "aerial" and asof.startswith("Photos of") and any(a == "Photos taken" and asof.endswith(b)
                                                               for a, b in rows):
        rows = [r for r in rows if r[0] != "Photos taken"]
    tour = ""
    if key == "scan" and evs:
        try:
            from server.tour import tour_href, tour_target
            if RUN_ID_RE.match(str(raw_run.get("run_id") or "")) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(cid)) \
                    and tour_target(raw_run, cid):
                tour = (f"<a class=tour href='{_e(tour_href(raw_run, cid))}'>See it in 3D "
                        "<span aria-hidden=true>→</span></a>")
        except Exception:  # noqa: BLE001
            tour = ""
    tcls = {"Decided this": "t-dec", "Agrees": "t-agr", "Differs": "t-dif"}.get(tag, "t-nil")
    h = [f"<article class='plate full {tcls}' id='{_e(aid)}' data-src={key}>"
         f"<header class=ph><p class=psrc><b>{name}</b><span>{_e(SRC_WHAT[key])}</span></p>"
         f"<span class='tag {tcls}'>{_e(tag)}</span></header><div class=pb><div class=pmain>"]
    if value:
        h.append(f"<div class=pv><p class=k>{_e(read) if read else 'Value read'}</p>"
                 f"<p class=pval>{_e(_cap(value))}</p></div>")
    if cmp:
        h.append(f"<p class=pcmp>{_e(_cap(cmp))}.</p>")
    if summary:
        h.append(f"<p class=psum>{_e(summary)}</p>")
    h.append(_gallery(pics, name))
    h.append("</div><div class=pside>")
    h.append(_kv(rows, f"kv-{aid}"))
    if plans:
        h.append(_plans_table(plans, f"pl-{aid}"))
    meta = []
    if where:
        meta.append(f"<p><span class=k>Where</span>{_e(_cap(where))}</p>")
    if asof and (rows or pics or where):
        meta.append(f"<p><span class=k>When</span>{_e(asof)}</p>")
    if meta:
        h.append("<div class=pmeta>" + "".join(meta) + "</div>")
    if tour:
        h.append(tour)
    h.append("</div></div>")
    tr = _trace(evs or nothing, info, steps, run.done.get("harness") or {}, run.done.get("timing") or {})
    if tr:
        h.append(f"<footer class=trace>{_e(tr)}</footer>")
    h.append("</article>")
    return "".join(h), tag, True


def _exhibit(R, run, raw_run, v: dict, cells: dict, info: dict, steps: list, top_id, no: int) -> dict | None:
    """One claim as an exhibit: its head (listed vs. found, confidence) and the four sources' plates in source
    order (full plates first, then the quiet ones). None when no source spoke to the claim."""
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
    cites = _cites(v)
    full, quiet, tags, spoke = [], [], {}, []
    for k in SRC_ORDER:
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
        html, tag, is_full = _plate(R, run, raw_run, v, c, k, rel, nothing, cells.get(k) or {}, decider, credited, info,
                           steps, cites.get(k) or [])
        tags[k] = tag
        if not is_full:
            quiet.append(html)
        else:
            full.append(html)
            spoke.append(k)
    if not full:
        return None
    kind = c.get("kind") or ""
    what = KIND_LABEL.get(kind) or _cap(str(kind).replace("_", " ")) or "Claim"
    if kind == "amenity" and isinstance(c.get("value"), str) and 0 < len(c["value"]) <= 28:
        what = _cap(c["value"])
    name = " · ".join(x for x in (what, c.get("label")) if x)
    said, is_q = _listing_words(R, run, v, c)
    said_t = f"“{said}”" if is_q else _cap(said)
    verdict = v.get("verdict") if v.get("verdict") in VERDICT_WORD else "cant_check"
    off = verdict in ("contradicted", "close")
    try:
        score, band, cwhy = R._conf(v)
    except Exception:  # noqa: BLE001
        score, band, cwhy = None, "", ""
    try:
        shade = R._shade(v) if off else ""
    except Exception:  # noqa: BLE001
        shade = "high" if verdict == "contradicted" else "low"
    try:
        reason = R._kind_text(R._first_sentence(v.get("reason") or ""))
    except Exception:  # noqa: BLE001
        reason = _safe(v.get("reason"), 160)
    found = _safe(v.get("found"), 100)
    if found.lower() in ("no data", "not checked", "") or found.lower() in JUNK_FOUND:
        found = ""
    found = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", found)
    lst = next((ct for ct in cites.get("listing") or []), {})
    lst_where = _where(lst.get("where")) if lst else ""
    word = "Doesn't match" if off else VERDICT_WORD[verdict]
    conf_txt = ""
    if score is not None:
        conf_txt = f"Confidence {score} / 100" + (f" · {band}" if band else "")
    elif band:
        conf_txt = f"{_cap(band)} confidence"
    # the head: listed | found | confidence
    strike = " class=strike" if verdict == "contradicted" else ""
    listed = (f"<div class=xl><p class=k>Listed</p><p class=said><span{strike}>{_e(said_t)}</span></p>"
              + (f"<p class=xsrc>From the {_e(lst_where[0].lower() + lst_where[1:])}</p>" if lst_where else "")
              + "</div>")
    if off:
        rhs = found or "Sources differ"
        right = f"<div class=xr><p class=k>Found</p><p class=truth>{_e(_cap(rhs))}</p></div>"
    elif verdict == "confirmed":
        right = (f"<div class=xr><p class=k>Found</p><p class='truth ok'>{_e(_cap(found) if found else 'The same')}"
                 "</p></div>")
    else:
        right = (f"<div class=xr><p class=k>Found</p><p class='truth nil'>{_e(_cap(found) if found else 'Nothing to compare')}"
                 "</p></div>")
    if score is not None:
        meter = (f"<div class=xc><p class=k>Confidence</p><p class=cfn>{score}<small>/100</small></p>"
                 f"<span class=meter aria-hidden=true><i style='--w:{score}%'></i></span>"
                 f"<p class=cfb>{_e(_cap(band) + ' confidence' if band else conf_txt)}</p></div>")
    elif band:
        meter = f"<div class=xc><p class=k>Confidence</p><p class='cfn word'>{_e(_cap(band))}</p></div>"
    elif verdict == "confirmed":
        meter = (f"<div class=xc><p class=k>Result</p><p class='cfn word ok'>Matches</p></div>")
    elif verdict == "cant_check":
        meter = (f"<div class=xc><p class=k>Result</p><p class='cfn word nil'>Couldn't check</p></div>")
    else:
        meter = ""
    pick = "<span class=pick>Top finding</span>" if cid == top_id else ""
    srcs_sq = "".join(f"<i class='{'hit' if tags[k] == 'Decided this' else 'on' if k in spoke else ''}' "
                      f"title='{SRC_NAME[k]}: {_e(tags[k])}'></i>" for k in SRC_ORDER)
    head = (f"<header class=xh><p class=xk><span class=xno>No. {no:02d}</span><span class=xn>{_e(name)}</span>{pick}"
            f"<span class=xt>{_e(word)}</span></p>"
            f"<div class='xg{' three' if meter else ''}'>{listed}{right}{meter}</div>"
            + (f"<p class=why>{_e(reason)}</p>" if reason else "")
            + (f"<p class=cwhy>{_e(cwhy)}</p>" if cwhy and cwhy != reason else "") + "</header>")
    n_full = len(full)
    sub = (f"<div class=plh><p class=k>The sources</p><p class=plsum><span class=sq aria-hidden=true>{srcs_sq}</span>"
           f"{n_full} of 4 gave something for this claim</p></div>")
    body = (f"{sub}<div class='plates n{min(n_full, 3)}'>{''.join(full)}</div>"
            + (f"<div class=quiet>{''.join(quiet)}</div>" if quiet else ""))
    cls = f"claim x-{verdict}" + (f" s-{shade}" if shade else "")
    sec = (f"<section class='{cls}' id='c-{_e(_cid(cid))}' data-verdict={verdict} data-srcs='{' '.join(spoke)}' "
           f"role=tabpanel aria-labelledby='t-{_e(_cid(cid))}' tabindex=-1>{head}{body}</section>")
    # the rail entry
    if off and score is not None:
        mark = f"<span class=ri-sc>{score}</span>"
    elif off:
        mark = f"<span class='ri-sc word'>{_e(band or 'off')}</span>"
    elif verdict == "confirmed":
        mark = ("<span class='ri-sc ok' aria-label=Matches><svg viewBox='0 0 20 20' width=16 height=16 aria-hidden=true>"
                "<path d='M4 10.5 8.2 14.5 16 6' fill=none stroke=currentColor stroke-width=1.8/></svg></span>")
    else:
        mark = "<span class='ri-sc nil'>–</span>"
    short = said_t if len(said_t) <= 60 else said_t[:57].rstrip() + "…"
    tip = f" title='{_e(conf_txt)}'" if conf_txt else ""
    tab = (f"<button type=button role=tab class='ri{' s-' + shade if shade else ''}' id='t-{_e(_cid(cid))}' "
           f"aria-controls='c-{_e(_cid(cid))}' aria-selected=false tabindex=-1 data-srcs='{' '.join(spoke)}'{tip}>"
           f"<span class=ri-n>{no:02d}</span><span class=ri-t>{_e(name)}</span>{mark}"
           f"<span class=ri-s>{_e(short)}</span><span class=sq aria-hidden=true>{srcs_sq}</span></button>")
    decided = [k for k in SRC_ORDER if tags[k] == "Decided this"]
    return {"section": sec, "tab": tab, "n": n_full, "spoke": spoke, "decided": decided, "verdict": verdict,
            "tags": tags}


# ---------- page ----------

CSS = """
:root{--ink:#111;--blue:#1a4be0;--mute:#6b6b6b;--ghost:#858585;--soft:#f4f4f4;--line:#d9d9d9;--hair:#ececec;--bg:#fff;
--gut:clamp(16px,5vw,72px)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.5 Newsreader,Georgia,'Times New Roman',serif;
-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:var(--ink)}a:hover{color:var(--blue)}
a:focus-visible,button:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
img{max-width:100%;display:block}
p{margin:0}
.wrap{max-width:1520px;margin:0 auto;padding:0 var(--gut)}
.k{display:block;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);line-height:1.3}
.hid{display:none!important}
/* header + hero */
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;min-height:88px;gap:8px 16px}
.brand{font-size:19px;font-weight:500;letter-spacing:-.01em;text-decoration:none}
.back{display:inline-flex;align-items:center;gap:8px;min-height:44px;font-size:15px;text-decoration:none}
.hero{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:24px 6%;align-items:end;
padding:clamp(0px,1vw,16px) 0 clamp(48px,6vw,96px)}
h1{margin:0;font-weight:300;font-size:clamp(56px,10vw,164px);line-height:.9;letter-spacing:-.05em;display:flex;flex-direction:column}
h1 .l2{padding-left:clamp(0px,14vw - 30px,220px)}
.hero-r{display:flex;flex-direction:column;gap:12px;padding-bottom:.6em}
.hero-r .where{font-size:clamp(21px,1.9vw,26px);line-height:1.25;letter-spacing:-.01em}
.hero-r .sum{font-size:18px;line-height:1.45}
.hero-r .meta{font-size:14px;color:var(--mute)}
.btn{display:inline-flex;align-items:center;gap:10px;min-height:48px;padding:0 22px;background:var(--ink);color:#fff;
border:0;font:inherit;font-size:16px;text-decoration:none;cursor:pointer;align-self:flex-start;transition:background-color .18s}
.btn:hover{background:#333;color:#fff}
/* the four sources: a dark band, one tile per source (also the source filter) */
.dark{background:#111;color:#f2f2f2}
.band{padding:clamp(48px,6vw,96px) 0 clamp(44px,5vw,80px)}
.band h2{margin:0;font-weight:300;font-size:clamp(40px,6vw,96px);line-height:.92;letter-spacing:-.05em;display:flex;flex-direction:column}
.band h2 .l2{padding-left:clamp(0px,12vw,180px)}
.band .lede{margin:clamp(14px,2vw,24px) 0 0;max-width:52ch;font-size:16px;color:#bdbdbd}
.srcs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:clamp(14px,2vw,36px);margin-top:clamp(28px,4vw,56px)}
.src{display:flex;flex-direction:column;gap:10px;min-width:0;text-align:left;background:none;border:0;padding:0;color:inherit;
font:inherit;cursor:pointer}
.src .tile{position:relative;background:#1d1d1d;aspect-ratio:4/3;overflow:hidden;display:flex;align-items:center;
justify-content:center;color:#8d8d8d;transition:transform .3s ease}
.src .tile img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .6s}
.src .tile svg{width:46%;height:auto}
.src:hover .tile{transform:translateY(-4px)}.src:hover .tile img{transform:scale(1.03)}
.src .tile .what{position:absolute;left:12px;top:12px;background:#111;color:#e8e8e8;font-size:13px;padding:3px 9px}
.src .row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;border-bottom:1px solid #333;padding-bottom:8px}
.src .h3{margin:0;font-weight:400;font-size:clamp(20px,1.8vw,26px)}
.src .n{font-weight:300;font-size:clamp(44px,5vw,84px);line-height:.95;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.src .n small{font-size:15px;letter-spacing:0;color:#a9a9a9;margin-left:8px;font-weight:400}
.src .roles{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px;color:#bdbdbd}
.src .roles b{font-weight:400;color:#fff;font-variant-numeric:tabular-nums}
.src .roles .dec b{color:#8eaaff}
.src .go{font-size:14px;color:#a9a9a9}
.src:hover .go,.src[aria-pressed=true] .go{color:#fff}
.src[aria-pressed=true] .tile{outline:2px solid #fff;outline-offset:4px}
.src.off{cursor:default}.src.off .tile,.src.off .n{opacity:.35}.src.off:hover .tile{transform:none}
/* the exhibits: a rail of claims, one exhibit shown */
.lib{padding-top:clamp(48px,6vw,96px);padding-bottom:clamp(48px,6vw,96px);scroll-margin-top:0}
.lib-top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px 24px;
padding-bottom:14px;border-bottom:1px solid var(--ink)}
.sh{margin:0;font-weight:300;font-size:clamp(40px,5vw,76px);line-height:.95;letter-spacing:-.045em}
.lib-n{font-size:16px;color:var(--mute)}
.fl{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.fl>span{font-size:13px;color:var(--mute);margin-right:4px}
.chip{font:inherit;font-size:14px;min-height:36px;padding:0 12px;background:var(--bg);color:var(--ink);
border:1px solid var(--line);cursor:pointer;border-radius:0;transition:border-color .18s,background-color .18s}
.chip:hover{border-color:var(--ink)}
.chip[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.chip b{font-weight:400;color:var(--ghost);margin-left:6px;font-variant-numeric:tabular-nums}.chip[aria-pressed=true] b{color:#bbb}
.md{display:grid;grid-template-columns:minmax(260px,330px) minmax(0,1fr);column-gap:clamp(24px,3vw,52px);align-items:start}
.rail{position:sticky;top:0;max-height:100vh;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;
border-right:1px solid var(--line);padding:6px 0 24px}
.rg-h{display:flex;align-items:baseline;justify-content:space-between;gap:10px;width:100%;margin:0;padding:18px 16px 8px 0;
font:inherit;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);background:none;border:0;
border-bottom:1px solid var(--ink);text-align:left}
.rg-h b{font-weight:400;font-variant-numeric:tabular-nums;letter-spacing:0;font-size:14px}
button.rg-h{cursor:pointer;min-height:44px}
button.rg-h::after{content:'+';font-size:18px;letter-spacing:0;color:var(--ink);margin-left:auto}
button.rg-h[aria-expanded=true]::after{content:'\\2212'}
button.rg-h b{margin-left:8px}
.rg.shut .ri{display:none}
.ri{position:relative;display:grid;grid-template-columns:28px minmax(0,1fr) auto;column-gap:10px;row-gap:2px;
align-items:baseline;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--hair);
padding:12px 16px 12px 12px;font:inherit;color:var(--ink);cursor:pointer;transition:background-color .18s}
.ri:hover{background:var(--soft)}
.ri:focus-visible{outline:2px solid var(--blue);outline-offset:-2px}
.ri[aria-selected=true]{background:var(--acc-bg,#f2f2f2)}
.ri::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--acc,var(--ink));transform:scaleY(0);
transition:transform .22s cubic-bezier(.2,.7,.2,1)}
.ri[aria-selected=true]::before{transform:none}
.ri-n{font-size:12px;color:var(--ghost);font-variant-numeric:tabular-nums}
.ri-t{font-size:18px;line-height:1.25;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ri-sc{grid-row:1/3;grid-column:3;align-self:center;font-weight:300;font-size:30px;line-height:1;letter-spacing:-.03em;
color:var(--acc-t,var(--blue));font-variant-numeric:tabular-nums;min-width:36px;text-align:right}
.ri-sc.word{font-size:14px;font-weight:400;letter-spacing:.02em}
.ri-sc.ok{color:var(--ink);display:flex;justify-content:flex-end}.ri-sc.nil{color:var(--ghost);font-size:22px}
.ri-s{grid-column:2;font-size:14px;line-height:1.35;color:var(--mute);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ri .sq{grid-column:2;margin-top:4px}
.sq{display:inline-flex;gap:3px;flex:none;vertical-align:middle}.sq i{width:8px;height:8px;background:var(--hair);display:block}
.sq i.on{background:var(--ink)}.sq i.hit{background:var(--acc,var(--blue))}
/* one blue accent, fainter as the confidence drops */
.s-high{--acc:#1a4be0;--acc-t:#1a4be0;--acc-bg:#eef2fd}
.s-medium{--acc:#6585ea;--acc-t:#4a6fe6;--acc-bg:#f1f4fd}
.s-low{--acc:#b3c2f4;--acc-t:#8199ec;--acc-bg:#f6f8fe}
.pane{min-width:0;padding-top:clamp(18px,2vw,28px)}
html.js .claim:not(.on){display:none}
.claim{scroll-margin-top:12px;outline:0}
.claim+.claim{margin-top:56px}html.js .claim+.claim{margin-top:0}
.claim.in{animation:xin .38s cubic-bezier(.2,.7,.2,1)}
@keyframes xin{from{opacity:0;transform:translateY(10px)}}
.xk{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;font-size:15px;color:var(--mute)}
.xno{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink);border:1px solid var(--ink);padding:1px 7px}
.xn{color:var(--ink);font-size:17px}
.xt{color:var(--acc-t,var(--mute));font-variant-numeric:tabular-nums}
.x-confirmed .xt{color:var(--ink)}
.pick{font-size:12px;color:#fff;background:var(--blue);padding:1px 8px;letter-spacing:.02em}
.xg{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:clamp(20px,3vw,48px);margin-top:14px;
border-top:1px solid var(--line);padding-top:16px}
.xg.three{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(150px,.62fr)}
.said,.truth{font-weight:300;font-size:clamp(32px,3.4vw,58px);line-height:1.02;letter-spacing:-.04em;overflow-wrap:anywhere;margin-top:4px}
.said{color:var(--ghost)}
.truth{color:var(--acc-t,var(--blue))}.truth.ok{color:var(--ink)}.truth.nil{color:var(--ghost)}
.x-confirmed .said{color:var(--ink)}
.xsrc{margin-top:6px;font-size:14px;color:var(--mute)}
.xc{border-left:1px solid var(--line);padding-left:clamp(14px,1.6vw,24px);display:flex;flex-direction:column}
.cfn{font-weight:300;font-size:clamp(44px,4.6vw,76px);line-height:.95;letter-spacing:-.04em;color:var(--acc-t,var(--ink));
font-variant-numeric:tabular-nums;margin-top:4px}
.cfn small{font-size:.3em;letter-spacing:0;color:var(--mute);margin-left:4px}
.cfn.word{font-size:clamp(26px,2.4vw,38px);line-height:1.05}.cfn.ok{color:var(--ink)}.cfn.nil{color:var(--ghost)}
.meter{display:block;height:4px;background:var(--hair);margin:10px 0 6px;position:relative;overflow:hidden}
.meter i{position:absolute;left:0;top:0;bottom:0;width:var(--w);background:var(--acc,var(--ink));transform-origin:left}
.claim.in .meter i{animation:grow .9s cubic-bezier(.2,.7,.2,1) .1s backwards}
@keyframes grow{from{transform:scaleX(0)}}
.cfb{font-size:14px;color:var(--mute)}
.strike{background:linear-gradient(var(--acc,var(--blue)),var(--acc,var(--blue))) no-repeat 0 54%/100% .07em;
-webkit-box-decoration-break:clone;box-decoration-break:clone}
.claim.in .strike{animation:strk .8s cubic-bezier(.7,0,.2,1) .15s backwards}
@keyframes strk{from{background-size:0 .07em}}
.why{margin-top:16px;font-size:18px;line-height:1.45;max-width:68ch}
.cwhy{margin-top:4px;font-size:15px;color:var(--mute);max-width:80ch}
/* the plates */
.plh{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 20px;margin-top:clamp(24px,3vw,40px);
padding-bottom:10px;border-bottom:1px solid var(--ink)}
.plsum{display:flex;align-items:center;gap:10px;font-size:15px;color:var(--mute)}
.plates{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:clamp(14px,1.6vw,24px);margin-top:16px}
.plate{min-width:0;scroll-margin-top:90px;transition:box-shadow .3s ease,border-color .3s ease}
.plate.full{border:1px solid var(--line);display:flex;flex-direction:column;container-type:inline-size;background:var(--bg)}
.plate.full.t-dec{border-color:var(--ink);box-shadow:inset 0 3px 0 var(--acc,var(--blue))}
.x-confirmed .plate.full.t-dec{box-shadow:inset 0 3px 0 var(--ink)}
.plate.hit{border-color:var(--blue)!important;box-shadow:0 0 0 3px var(--blue)!important;animation:hit 1.1s ease 2}
@keyframes hit{50%{box-shadow:0 0 0 7px rgba(26,75,224,.25)}}
.plate.dim{opacity:.32}
.ph{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 16px 12px;border-bottom:1px solid var(--hair)}
.psrc{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;min-width:0}
.psrc b{font-weight:500;font-size:21px;letter-spacing:-.01em}
.psrc span{font-size:14px;color:var(--mute)}
.tag{font-size:12px;letter-spacing:.04em;padding:3px 9px;border:1px solid var(--line);white-space:nowrap;color:var(--mute)}
.tag.t-dec{background:var(--acc,var(--blue));border-color:var(--acc,var(--blue));color:#fff}
.x-confirmed .tag.t-dec{background:var(--ink);border-color:var(--ink)}
.tag.t-agr{border-color:var(--ink);color:var(--ink)}
.tag.t-dif{border-color:var(--blue);color:var(--blue)}
.pb{display:grid;grid-template-columns:minmax(0,1fr);align-content:start;align-items:start;gap:14px;padding:14px 16px 16px;flex:1}
.pmain,.pside{display:flex;flex-direction:column;gap:12px;min-width:0}
@container (min-width:640px){.pb{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);column-gap:28px}}
.pval{font-weight:300;font-size:clamp(28px,2.4vw,38px);line-height:1.08;letter-spacing:-.03em;margin-top:2px;overflow-wrap:anywhere}
.t-dec .pval{color:var(--acc-t,var(--blue))}.x-confirmed .t-dec .pval{color:var(--ink)}
.pcmp{font-size:15px;line-height:1.4}
.psum{font-size:15px;line-height:1.45;color:var(--mute)}
.gal{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.gal.n1{grid-template-columns:minmax(0,1fr)}.gal.n2{grid-template-columns:repeat(2,minmax(0,1fr))}
.gal figure{margin:0;min-width:0;display:flex;flex-direction:column;gap:4px}
.gal figcaption{font-size:12.5px;line-height:1.3;color:var(--mute);display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden}
a.pic{display:block;overflow:hidden;background:var(--soft);cursor:zoom-in}
a.pic img{width:100%;aspect-ratio:4/3;object-fit:cover;transition:transform .6s ease}
a.pic.plan img{object-fit:contain;background:#fff;padding:4px}
a.pic.plan{border:1px solid var(--hair)}
a.pic:hover img{transform:scale(1.04)}
.gal.n1 a.pic img{aspect-ratio:16/10}
.kv{margin:0;border-top:1px solid var(--ink)}
.kv div{display:grid;grid-template-columns:minmax(96px,42%) minmax(0,1fr);gap:12px;padding:6px 0;border-bottom:1px solid var(--hair);
font-size:14.5px;line-height:1.38}
.kv dt{color:var(--mute)}.kv dd{margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
html.js .kv:not(.open) .x,html.js .plans:not(.open) tr.x{display:none}
.allbtn{align-self:flex-start;font:inherit;font-size:14px;background:none;border:0;padding:4px 0;min-height:32px;color:var(--ink);
text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px;cursor:pointer}
.allbtn:hover{color:var(--blue);text-decoration-color:var(--blue)}
.ptw{display:flex;flex-direction:column;gap:6px}
.plans{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;border-top:1px solid var(--ink)}
.plans th{text-align:left;font-weight:400;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);
border-bottom:1px solid var(--line);padding:6px 6px 5px 0}
.plans td{border-bottom:1px solid var(--hair);padding:5px 6px 5px 0;overflow-wrap:anywhere}
.plans td+td,.plans th+th{text-align:right}
.pnote{font-size:13px;color:var(--mute)}
.pmeta{display:flex;flex-direction:column;gap:6px;font-size:14px;line-height:1.4}
.pmeta p{display:grid;grid-template-columns:52px minmax(0,1fr);gap:8px;align-items:baseline}
.pmeta .k{font-size:11px}
.tour{align-self:flex-start;display:inline-flex;align-items:center;gap:8px;min-height:40px;font-size:15px;color:#fff;
background:var(--ink);text-decoration:none;padding:0 16px}
.tour:hover{background:var(--blue);color:#fff}
.trace{margin-top:auto;padding:8px 16px 10px;border-top:1px dashed #e2e2e2;background:#fafafa;
font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--ghost);overflow-wrap:anywhere}
.quiet{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr));gap:10px;margin-top:14px}
.plate.nil{border:1px dashed var(--line);padding:10px 14px;display:flex;flex-direction:column;gap:2px;color:var(--mute)}
.plate.nil .psrc b{font-size:16px;color:var(--mute);font-weight:500}.plate.nil .psrc span{font-size:13px}
.pnil{font-size:14px;line-height:1.35}
.none{color:var(--mute);font-size:15px;margin-top:28px}
/* footer */
.foot{padding:clamp(48px,6vw,88px) 0 40px}
.mark{margin:0;font-weight:300;font-size:clamp(48px,13vw,196px);line-height:.95;letter-spacing:-.05em;white-space:nowrap}
.foot-end{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;margin-top:40px;font-size:15px;color:#a9a9a9}
.foot a{color:#fff}
/* lightbox */
.lb{position:fixed;inset:0;z-index:90;background:rgba(17,17,17,.94);display:flex;flex-direction:column;align-items:center;
justify-content:center;gap:12px;padding:64px 16px 24px}
.lb[hidden]{display:none}
.lb img{max-width:min(100%,1400px);max-height:80vh;object-fit:contain;background:#fff}
.lb p{color:#eee;font-size:16px;text-align:center;max-width:70ch}
.lb .lbx,.lb .lbp,.lb .lbn{position:absolute;background:#fff;color:var(--ink);border:0;font:inherit;font-size:14px;
min-height:44px;min-width:44px;padding:0 14px;cursor:pointer}
.lb .lbx{top:12px;right:12px}.lb .lbp{left:12px;top:50%}.lb .lbn{right:12px;top:50%}
.lb .lbp[hidden],.lb .lbn[hidden]{display:none}
/* motion */
.js .rv{opacity:0;transform:translateY(24px);transition:opacity .8s ease var(--d,0ms),transform .8s cubic-bezier(.2,.7,.2,1) var(--d,0ms)}
.js .rv.in{opacity:1;transform:none}
@media (max-width:1100px){.srcs{grid-template-columns:repeat(2,minmax(0,1fr))}.xg.three{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.xg.three .xc{grid-column:1/-1;border-left:0;border-top:1px solid var(--hair);padding:12px 0 0;margin-top:14px;display:grid;
grid-template-columns:auto auto minmax(80px,1fr) auto;align-items:center;column-gap:16px}
.xg.three .xc .meter{margin:0}}
@media (max-width:860px){
.hero{grid-template-columns:minmax(0,1fr)}
.md{display:block}
.rail{position:sticky;top:0;z-index:6;max-height:none;overflow-x:auto;overflow-y:hidden;display:flex;gap:8px;border-right:0;
background:var(--bg);padding:10px 0;margin:0 calc(-1 * var(--gut));padding-left:var(--gut);padding-right:var(--gut);
border-bottom:1px solid var(--line);scrollbar-width:none;scroll-snap-type:x proximity}
.rail::-webkit-scrollbar{display:none}
.rg{display:flex;gap:8px;flex:none}
.rg-h{flex:none;width:auto;border:0;padding:0 4px 0 0;flex-direction:column;justify-content:center;align-items:flex-start;gap:0;
font-size:11px;max-width:84px;white-space:normal;line-height:1.2}
button.rg-h{min-height:0;border:1px dashed var(--line);padding:0 10px}
button.rg-h::after{margin-left:0}
.ri{flex:none;width:auto;min-width:150px;max-width:62vw;border:1px solid var(--line);padding:8px 12px 8px 10px;
grid-template-columns:minmax(0,1fr) auto;scroll-snap-align:start}
.ri-n,.ri .sq{display:none}
.ri-t{font-size:16px}.ri-s{grid-column:1;font-size:13px}
.ri-sc{grid-column:2;font-size:24px}
.pane{padding-top:16px}
.plate{scroll-margin-top:110px}.claim{scroll-margin-top:96px}
}
@media (max-width:560px){
body{font-size:17px}
.top{min-height:68px}
.srcs{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px 12px}
.src .row{flex-wrap:wrap;gap:0 8px}.src .n{font-size:44px}.src .n small{display:block;margin:2px 0 0}.src .h3{font-size:19px}.src .roles{font-size:13px}
.xg,.xg.three{grid-template-columns:minmax(0,1fr);row-gap:14px}
.xg.three .xc{grid-column:auto;margin-top:0;grid-template-columns:auto auto;row-gap:4px}
.xg.three .xc .meter{grid-column:1/-1}
.said,.truth{font-size:36px}
.gal{grid-template-columns:repeat(2,minmax(0,1fr))}.gal.n1{grid-template-columns:minmax(0,1fr)}
.lb .lbp,.lb .lbn{top:auto;bottom:16px}
}
@media (prefers-reduced-motion:reduce){
.js .rv,.js .rv.in{opacity:1;transform:none;transition:none}
.claim.in,.claim.in .strike,.claim.in .meter i,.plate.hit{animation:none}
.ri::before,.src .tile,.src .tile img,a.pic img{transition:none}
.src:hover .tile,a.pic:hover img,.src:hover .tile img{transform:none}
}
"""

SCRIPT = r"""
(function(){
var d=document.documentElement;d.classList.add('js');
var still=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
/* fade-ins and count-ups */
var rv=[].slice.call(document.querySelectorAll('.rv'));
function count(el){var n=+el.getAttribute('data-n');if(still||!n){el.firstChild.nodeValue=n;return;}var t0=null;
function f(t){if(!t0)t0=t;var p=Math.min(1,(t-t0)/900);el.firstChild.nodeValue=Math.round(n*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(f);}
requestAnimationFrame(f);}
function show(el){el.classList.add('in');[].forEach.call(el.querySelectorAll('[data-n]'),count);}
if(still||!('IntersectionObserver' in window)){rv.forEach(show);}
else{var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){show(e.target);io.unobserve(e.target);}});},
{rootMargin:'0px 0px -6% 0px',threshold:0.05});rv.forEach(function(el){io.observe(el);});}
/* a picture whose file is gone: hide its slot */
document.addEventListener('error',function(e){var t=e.target;if(!t||t.tagName!=='IMG'||!t.closest||t.closest('.lb'))return;
var f=t.closest('figure.g')||t.closest('.tile')&&t;if(f)f.classList.add('hid');
var g=t.closest('.gal');if(g&&!g.querySelector('figure.g:not(.hid)'))g.classList.add('hid');},true);

var rail=document.getElementById('rail');
var tabs=rail?[].slice.call(rail.querySelectorAll('[role=tab]')):[];
var cant=document.getElementById('g-cant'),cantB=cant&&cant.querySelector('button.rg-h');
var st={src:'all'};
function sec(t){return document.getElementById(t.getAttribute('aria-controls'));}
function tabOf(s){return document.getElementById('t-'+s.id.slice(2));}
function vis(t){return !t.classList.contains('hid')&&!(t.closest('.rg.shut'));}
function keep(t){var L=rail;if(!L)return;
if(L.scrollHeight>L.clientHeight+2){var top=t.offsetTop-L.offsetTop;if(top<L.scrollTop+40)L.scrollTop=Math.max(0,top-40);
else if(top+t.offsetHeight>L.scrollTop+L.clientHeight)L.scrollTop=top+t.offsetHeight-L.clientHeight+8;}
if(L.scrollWidth>L.clientWidth+2){var lf=t.offsetLeft-L.offsetLeft;if(lf<L.scrollLeft+16)L.scrollLeft=Math.max(0,lf-16);
else if(lf+t.offsetWidth>L.scrollLeft+L.clientWidth)L.scrollLeft=lf+t.offsetWidth-L.clientWidth+16;}}
function openCant(o){if(!cant)return;cant.classList.toggle('shut',!o);if(cantB)cantB.setAttribute('aria-expanded',o?'true':'false');}
function select(t,o){if(!t)return;o=o||{};var s=sec(t);
tabs.forEach(function(x){var on=x===t,p=sec(x);x.setAttribute('aria-selected',on?'true':'false');x.tabIndex=on?0:-1;
if(p){if(on&&!p.classList.contains('on')){p.classList.remove('in');p.classList.add('on');void p.offsetWidth;if(!still)p.classList.add('in');}
else if(!on)p.classList.remove('on','in');}});
if(s)[].forEach.call(s.querySelectorAll('img[loading=lazy]'),function(i){i.loading='eager';});
if(o.focus)t.focus({preventScroll:true});keep(t);dim();
if(o.scroll&&s){var r=s.getBoundingClientRect();if(r.top<0||r.top>innerHeight*0.6)s.scrollIntoView({block:'start'});}
if(o.hash&&s&&history.replaceState)history.replaceState(null,'','#'+s.id);}
function dim(){[].forEach.call(document.querySelectorAll('.plate'),function(p){
p.classList.toggle('dim',st.src!=='all'&&p.getAttribute('data-src')!==st.src);});}
var chips=[].slice.call(document.querySelectorAll('[data-f]'));
function apply(){chips.forEach(function(b){b.setAttribute('aria-pressed',String(b.getAttribute('data-f')==='src:'+st.src));});
tabs.forEach(function(t){var ok=st.src==='all'||(' '+t.getAttribute('data-srcs')+' ').indexOf(' '+st.src+' ')>=0;t.classList.toggle('hid',!ok);});
[].forEach.call(document.querySelectorAll('.rg'),function(g){g.classList.toggle('hid',!g.querySelector('.ri:not(.hid)'));});
var cur=tabs.filter(function(t){return t.getAttribute('aria-selected')==='true';})[0];
if(!cur||cur.classList.contains('hid')){var f=tabs.filter(function(t){return !t.classList.contains('hid');})[0];if(f){
if(cant&&cant.contains(f))openCant(true);select(f,{});}}
dim();}
chips.forEach(function(b){b.addEventListener('click',function(){if(b.classList.contains('off'))return;
var v=b.getAttribute('data-f').split(':')[1];st.src=(st.src===v&&b.classList.contains('src'))?'all':v;apply();
if(b.classList.contains('src')){var l=document.getElementById('lib');if(l)l.scrollIntoView({behavior:still?'auto':'smooth',block:'start'});}});});
if(rail){rail.addEventListener('click',function(e){var t=e.target.closest('[role=tab]');if(t){select(t,{focus:true,scroll:true,hash:true});return;}
var h=e.target.closest('button.rg-h');if(h)openCant(h.getAttribute('aria-expanded')!=='true');});
rail.addEventListener('keydown',function(e){var t=e.target.closest('[role=tab]');if(!t)return;var v=tabs.filter(vis),i=v.indexOf(t),j=null;
if(e.key==='ArrowDown'||e.key==='ArrowRight')j=Math.min(v.length-1,i+1);
else if(e.key==='ArrowUp'||e.key==='ArrowLeft')j=Math.max(0,i-1);
else if(e.key==='Home')j=0;else if(e.key==='End')j=v.length-1;
if(j===null||j<0)return;e.preventDefault();select(v[j],{focus:true,hash:true});});}
/* "Show all N" */
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('button.allbtn');if(!b)return;
var w=document.getElementById(b.getAttribute('aria-controls'));if(!w)return;var o=w.classList.toggle('open');
b.setAttribute('aria-expanded',o?'true':'false');b.textContent=o?b.getAttribute('data-less'):b.getAttribute('data-more');});
/* deep links: #a-<claim>-<source> selects the claim, opens its group, shows and marks that source's plate */
function go(){var id='';try{id=decodeURIComponent(location.hash.slice(1));}catch(e){return;}
if(!/^(a|c)-/.test(id))return;var el=document.getElementById(id);if(!el)return;
var s=el.closest('.claim');if(!s)return;var t=tabOf(s);if(!t)return;
if(t.classList.contains('hid')){st.src='all';apply();}
if(cant&&cant.contains(t))openCant(true);select(t,{});
requestAnimationFrame(function(){requestAnimationFrame(function(){
el.scrollIntoView({block:el.classList.contains('plate')?'center':'start'});
if(el.classList.contains('plate')){el.classList.remove('hit');void el.offsetWidth;el.classList.add('hit');
clearTimeout(el._h);el._h=setTimeout(function(){el.classList.remove('hit');},3200);}});});}
if(tabs.length){var first=tabs.filter(function(t){return t.getAttribute('data-first');})[0]||tabs[0];
if(!/^#(a|c)-/.test(location.hash))select(first,{});go();window.addEventListener('hashchange',go);}
/* lightbox: one plate's pictures, arrows to move, Esc to close */
var lb=document.getElementById('lb');if(!lb)return;var li=lb.querySelector('img'),lc=lb.querySelector('p'),set=[],at=0,from=null;
var bp=lb.querySelector('.lbp'),bn=lb.querySelector('.lbn');
function paint(){var a=set[at];li.src=a.getAttribute('href');var c=a.getAttribute('data-cap')||'';li.alt=c;
lc.textContent=c+(set.length>1?'  ·  '+(at+1)+' of '+set.length:'');bp.hidden=bn.hidden=set.length<2;}
function shut(){lb.hidden=true;li.removeAttribute('src');if(from)from.focus({preventScroll:true});}
function step(k){at=(at+k+set.length)%set.length;paint();}
document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a.pic');if(!a)return;e.preventDefault();
var g=a.closest('.gal');set=[].slice.call((g||a.parentNode).querySelectorAll('a.pic')).filter(function(x){return !x.closest('.hid');});
at=Math.max(0,set.indexOf(a));from=a;paint();lb.hidden=false;lb.querySelector('.lbx').focus();});
lb.addEventListener('click',function(e){if(e.target===bp){step(-1);return;}if(e.target===bn){step(1);return;}if(e.target!==li)shut();});
document.addEventListener('keydown',function(e){if(lb.hidden)return;if(e.key==='Escape')shut();
else if(e.key==='ArrowRight'&&set.length>1){e.preventDefault();step(1);}else if(e.key==='ArrowLeft'&&set.length>1){e.preventDefault();step(-1);}});
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
                 "<h1>Not found.</h1><p class=meta>No such check.</p></div>")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _src_strip(run, raw: dict, held: dict, decided: dict, agrees: dict, differs: dict) -> str:
    """The dark band: one tile per source, in source order, with what it holds for this check; a tile is also the
    source filter."""
    images = raw.get("images") or {}
    tiles = []
    for i, k in enumerate(SRC_ORDER):
        pic = ""
        if k in ("scan", "aerial"):
            for ev in run.evidence:
                if ev.get("source") != k or ev.get("status") != "ok":
                    continue
                for im in ev.get("images") or []:
                    u = _img_url(im.get("path") if isinstance(im, dict) else None, images)
                    cap = str((im or {}).get("caption") or "") if isinstance(im, dict) else ""
                    if u and not re.search(r"(?i)floor ?plan|schematic", cap):
                        pic = u
                        break
                if pic:
                    break
        n = held.get(k, 0)
        off = "" if n else " off"
        tile = ("<span class=tile>" + (f"<img src='{_e(pic)}' alt='' loading=lazy>" if pic else SRC_GLYPH[k])
                + f"<span class=what>{_e(SRC_WHAT[k])}</span></span>")
        roles = (f"<span class=roles><span class=dec>decided <b>{decided.get(k, 0)}</b></span>"
                 f"<span>agrees <b>{agrees.get(k, 0)}</b></span><span>differs <b>{differs.get(k, 0)}</b></span></span>"
                 if n else "<span class=roles>Nothing held for this check</span>")
        tiles.append(f"<button type=button class='src{off}' data-f='src:{k}' aria-pressed=false style='--d:{i * 90}ms'"
                     + (" disabled" if not n else "") + f">{tile}<span class=row><span class=h3>{SRC_NAME[k]}</span>"
                     f"<span class=n data-n={n}>{n}<small>{'artifact' if n == 1 else 'artifacts'}</small></span></span>"
                     f"{roles}" + (f"<span class=go>Show only {SRC_NAME[k]} →</span>" if n else "") + "</button>")
    return ("<section class='dark band' aria-label='The four sources'><div class=wrap>"
            "<h2 class=rv><span>Four sources.</span><span class=l2>Every artifact.</span></h2>"
            "<p class='lede rv'>What each source holds for this address, and how often its evidence decided a claim. "
            "Pick one to see only its artifacts.</p>"
            f"<div class='srcs rv'>{''.join(tiles)}</div></div></section>")


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

    def strength(v):
        try:
            return float(R._strength(v))
        except Exception:  # noqa: BLE001
            s = (v.get("confidence") or {}).get("score") if isinstance(v.get("confidence"), dict) else None
            return float(s) if isinstance(s, (int, float)) else 0.0
    off = sorted(off, key=lambda v: -strength(v))  # the most certain first (stable: the report's order on ties)
    ok = [v for v in run.verdicts if v.get("verdict") == "confirmed"]
    cant = [v for v in run.verdicts if v.get("verdict") not in ("contradicted", "close", "confirmed")]

    held = {k: 0 for k in SRC_ORDER}
    decided = {k: 0 for k in SRC_ORDER}
    agrees = {k: 0 for k in SRC_ORDER}
    differs = {k: 0 for k in SRC_ORDER}
    groups, sections, empty, no, n_claims = [], [], 0, 0, {"off": 0, "ok": 0, "cant": 0}
    first_tab = None
    for gid, title, vs in (("off", "Doesn't match", off), ("ok", "Matches", ok), ("cant", "Couldn't check", cant)):
        tabs = []
        for v in vs:
            try:
                x = _exhibit(R, run, raw, v, cells_by.get(v.get("claim_id")) or {}, info, steps, top_id, no + 1)
            except Exception:  # noqa: BLE001  one odd claim never breaks the page
                x = None
            if not x:
                empty += 1
                continue
            no += 1
            tab = x["tab"]
            if first_tab is None:
                tab = tab.replace("aria-selected=false tabindex=-1", "aria-selected=true tabindex=0 data-first=1", 1)
                x["section"] = x["section"].replace("<section class='claim", "<section class='on claim", 1)
                first_tab = tab
            tabs.append(tab)
            sections.append(x["section"])
            for k in x["spoke"]:
                held[k] += 1
            for k, tg in x["tags"].items():
                decided[k] += tg == "Decided this"
                agrees[k] += tg == "Agrees"
                differs[k] += tg == "Differs"
            n_claims[gid] += 1
        if not tabs:
            continue
        if gid == "cant":
            head = (f"<button type=button class=rg-h aria-expanded=false aria-controls=g-cant>{_e(title)}"
                    f"<b>{len(tabs)}</b></button>")
            groups.append(f"<div class='rg shut' id=g-cant>{head}{''.join(tabs)}</div>")
        else:
            groups.append(f"<div class=rg id=g-{gid}><p class=rg-h>{_e(title)}<b>{len(tabs)}</b></p>{''.join(tabs)}</div>")
    if first_tab and "id=g-cant" in "".join(groups) and n_claims["off"] + n_claims["ok"] == 0:
        groups = [g.replace("class='rg shut' id=g-cant", "class=rg id=g-cant").replace("aria-expanded=false", "aria-expanded=true", 1)
                  if "id=g-cant" in g else g for g in groups]

    total = sum(held.values())
    src_n = sum(1 for k in SRC_ORDER if held[k])
    n_ex = no
    back = f"/report/{rid}" if RUN_ID_RE.match(rid) else "/"
    if total:
        summary = (f"{_plural(total, 'artifact')} from {_plural(src_n, 'source')}, behind {_plural(n_ex, 'claim')}"
                   + (f"; {n_claims['off']} {'doesn’t' if n_claims['off'] == 1 else 'don’t'} match the listing."
                      if n_claims["off"] else "; everything checked matches the listing."))
    else:
        summary = "No source gave us anything to show for this check."
    hero = ("<section class=hero><h1 class=rv><span>Artifact</span><span class=l2>library.</span></h1>"
            f"<div class='hero-r rv' style='--d:120ms'><p class=where>{_e(address)}</p><p class=sum>{_e(summary)}</p>"
            "<p class=meta>Each exhibit is one claim from the listing, with what every source gave us for it.</p>"
            + ("<a class=btn href='#lib'>Open the exhibits <span aria-hidden=true>↓</span></a>" if total else "")
            + "</div></section>")
    strip = _src_strip(run, raw, held, decided, agrees, differs) if total else ""
    chips = "".join(f"<button type=button class=chip data-f='src:{k}' aria-pressed=false>{SRC_NAME[k]}<b>{held[k]}</b>"
                    "</button>" for k in SRC_ORDER if held[k])
    none = (f"<p class=none>{_plural(empty, 'claim')} had nothing from any source; "
            f"<a href='{_e(back)}#checked'>see the report</a>.</p>") if empty else ""
    lib = ""
    if sections:
        sub = (f"{_plural(n_ex, 'exhibit')}. Mismatches first, the most certain on top.")
        lib = ("<section class='wrap lib' id=lib aria-labelledby=lib-h>"
               f"<div class=lib-top><div><h2 class=sh id=lib-h>The exhibits</h2><p class=lib-n>{_e(sub)}</p></div>"
               "<div class=fl role=group aria-label='Filter by source'><span>Source</span>"
               f"<button type=button class=chip data-f='src:all' aria-pressed=true>All</button>{chips}</div></div>"
               f"<div class=md><nav class=rail id=rail role=tablist aria-orientation=vertical aria-label=Claims>"
               f"{''.join(groups)}</nav><div class=pane id=pane>{''.join(sections)}</div></div>{none}</section>")
    elif none:
        lib = f"<section class='wrap lib' id=lib>{none}</section>"
    body = ("<div class=wrap>"
            f"<header class=top><a class=brand href='{_e(back)}'>Realty Check</a>"
            f"<a class=back href='{_e(back)}'><span aria-hidden=true>←</span> Back to the report</a></header>"
            f"{hero}</div>{strip}{lib}"
            "<footer class='dark foot'><div class=wrap><p class=mark>Realty Check</p>"
            f"<div class=foot-end><span>Check {_e(rid)}</span><a href='{_e(back)}'>Back to the report</a></div></div></footer>"
            "<div class=lb id=lb hidden role=dialog aria-modal=true aria-label='Picture'>"
            "<button type=button class=lbx>Close</button><button type=button class=lbp aria-label='Previous picture'>←</button>"
            "<img alt=''><p></p><button type=button class=lbn aria-label='Next picture'>→</button></div>"
            f"<script>{SCRIPT}</script>")
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
