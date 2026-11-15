"""Citations: the exact proof behind one verdict, built from what the run already keeps (docs/CONTRACT.md 2.5.1).

Pure functions, no I/O. `build_citations(verdict, claim, evidences)` lists, per source, what was read, where
(parcel number, scan name + spot, photo, record field), the date when the source keeps one, the value it gave, the
raw cache image path (same convention as a verdict's `image`), a public query link when the source kept one, and a
role: "decided" (the source whose value the judge used; exactly one on a confirmed / contradicted / close
verdict), "agrees" / "differs" (against the decided value, or the listing when nothing decided), "context".

`apply_proof_rule(verdict, citations)`: a confirmed / contradicted / close verdict with no citation from a source
other than the Listing becomes "couldn't check" (soft wording). `cite()` does both and returns the verdict with
`citations` added; nothing else on a verdict that has proof changes.

Privacy: never people's names or contacts (owner, manager), never Houston 311 text (311 is not cited at all), never
a signed URL, credential or Matterport SDK key, never a local path except in `image`.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from harness.graph import amenities, trust
from harness.graph.judge import (UNIT_KINDS, _num, _same_plan, _usable, phrase, property_reading,
                                 record_names)
from harness.graph.plans import title_sqft

SOURCE = {"record": "CoStar", "scan": "Matterport", "aerial": "Vexcel", "county": "County"}
RESULT = ("confirmed", "contradicted", "close")
ROLES = ("decided", "agrees", "differs", "context")
FIELD = {"bedrooms": "Bedrooms", "bathrooms": "Bathrooms", "area_sqft": "Floor area", "stories": "Number of floors",
         "year_built": "Year built", "unit_count": "Number of units"}
NO_PROOF_FOUND = "no openable proof"
NO_PROOF_REASON = "We found no openable proof for this, so we could not check it."

_URL = re.compile(r"https?://\S+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
_PATH = re.compile(r"[A-Za-z]:[\\/]\S*|\\\\\S+|(?<![\w/])/(?:[\w.-]+/)+[\w.-]*")
_SECRET_PARAM = re.compile(r"(?i)key|token|sig|secret|password|passwd|auth|credential|session|x-amz|expires|policy")
_PANO = re.compile(r"^Panorama\b", re.I)


# ---------- small helpers ----------

def _plain(s: Any, limit: int = 160) -> str:
    """One short plain line: no URL, email, phone number or local path; '' when nothing is left."""
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    s = _URL.sub("", s)
    s = _EMAIL.sub("", s)
    s = _PHONE.sub("", s)
    s = _PATH.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;:")
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _link(url: Any) -> str | None:
    """A public http(s) query link, or None when it is not one or carries anything like a key, token or
    signature."""
    u = str(url or "").strip()
    if not u or len(u) > 2000 or any(c.isspace() for c in u):
        return None
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return None
    if any(_SECRET_PARAM.search(k) for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return None
    return u


def _ok(e: Any) -> bool:
    return isinstance(e, dict) and e.get("status") == "ok" and e.get("source") in SOURCE


def _obs(e: dict) -> dict:
    o = e.get("observed")
    return o if isinstance(o, dict) else {}


def _images(e: dict) -> list[dict]:
    return [im for im in e.get("images") or [] if isinstance(im, dict) and im.get("path")]


def _first_image(e: dict) -> str | None:
    ims = _images(e)
    return str(ims[0]["path"]) if ims else None


def _caption_of(path: str | None, evidences: list[dict]) -> str:
    if not path:
        return ""
    for e in evidences:
        for im in _images(e) if isinstance(e, dict) else []:
            if str(im["path"]) == str(path):
                return _plain(im.get("caption"), 120)
    return ""


def _date(e: dict) -> str | None:
    """As-of / capture date the source keeps: Vexcel capture or survey date, the county roll year; else None."""
    o, src = _obs(e), e.get("source")
    if src == "aerial":
        prop = o.get("property") if isinstance(o.get("property"), dict) else {}
        for v in (o.get("capture_date"), prop.get("captured")):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v or "")[:10]):
                return str(v)[:10]
        for im in _images(e):
            m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", str(im.get("caption") or ""))
            if m:
                return m.group(1)
    if src == "county":
        m = re.search(r"(?i)(?:tax_year|tax year|roll year|roll_year)\s*(\d{4})", str(e.get("detail") or ""))
        if m:
            return m.group(1)
    return None


def _record_id(e: dict, match: dict | None) -> str:
    """The CoStar property / home listing id the record evidence came from (its detail names it), else the
    match's."""
    m = re.search(r"(?:home listings?|property)\s+([A-Za-z0-9][\w:,-]*[A-Za-z0-9])", str(e.get("detail") or ""))
    if m:
        return m.group(1)
    return str((match or {}).get("property_id") or "")


def _county_name(o: dict) -> str:
    name = str(o.get("source_name") or "").split(" + City of Houston 311")[0]
    return _plain(name, 90) or "County record"


# ---------- where / read / value ----------

def _where(e: dict, kind: str | None, match: dict | None, image: str | None, evidences: list[dict],
           plan_name: str | None = None) -> str:
    o, src = _obs(e), e.get("source")
    bits: list[str] = []
    if src == "record":
        pid = _record_id(e, match)
        bits.append(f"CoStar property {pid}" if pid else "CoStar record")
        plan = plan_name or o.get("kit_plan") or o.get("plan")
        if plan:
            bits.append(f"floor plan {_plain(plan, 60)}")
        if o.get("unit"):
            bits.append(f"unit {_plain(o['unit'], 30)}")
        if kind in FIELD:
            bits.append(f"field {FIELD[kind]}")
        elif isinstance(o.get("amenities"), list):
            bits.append("amenity list")
    elif src == "county":
        bits.append(_county_name(o))
        if o.get("parcel_id"):
            bits.append(f"parcel {_plain(o['parcel_id'], 40)}")
        if kind in FIELD:
            bits.append(f"field {FIELD[kind]}")
    elif src == "scan":
        title = _plain(o.get("model_name") or o.get("scan_title"), 90)
        mid = _plain(o.get("model_id"), 40)
        bits.append(f"scan '{title}'" + (f" ({mid})" if mid else "") if title else (f"scan {mid}" if mid else "3D scan"))
        if o.get("unit"):
            bits.append(f"unit {_plain(o['unit'], 30)}")
        cap = _caption_of(image, evidences)
        if cap and _PANO.match(cap):
            bits.append("spot: " + cap.split(":")[0].strip())
    elif src == "aerial":
        cap = _caption_of(image, evidences)
        if kind == "stories" and _num(o.get("height_m")) is not None:
            bits.append("Vexcel roof height measured at the address")
            if cap:
                bits.append(f"photo: {cap}")
        elif cap:
            bits.append(cap)
        elif isinstance(o.get("property"), dict):
            bits.append("Vexcel property data")
        elif _num(o.get("height_m")) is not None:
            bits.append("Vexcel roof height at the address")
        else:
            bits.append("Vexcel aerial photo")
    return ", ".join(b for b in bits if b)


def _read(e: dict, kind: str | None, feature: bool, found_from: str | None = None) -> str:
    o, src = _obs(e), e.get("source")
    if feature:
        if src == "record":
            return "Amenity list"
        if src == "aerial" and isinstance(o.get("property"), dict) and not o.get("feature_check"):
            return "Property data"
        if o.get("how") == "scan_title":
            return "Scan titles"
        if o.get("outlined"):
            return "Outlined top-down photo"
        return "Photo look"
    if src == "scan":
        if found_from == "scan_title" or o.get("counts_from") == "model_name" or o.get("count_from_title"):
            return "Scan title"
        if kind in ("bedrooms", "bathrooms") and o.get("room_labels"):
            return "Room labels"
        if kind == "area_sqft":
            return "Measured floor area"
        return FIELD.get(str(kind), "3D scan")
    if src == "aerial" and kind == "stories" and _num(o.get("height_m")) is not None:
        return "Measured roof height"
    field = FIELD.get(str(kind))
    if not field:
        return "Record"
    if src == "county" and kind == "unit_count" and o.get("units_estimated"):
        return f"{field} field (added up over several parcels)"
    return f"{field} field"


def _num_value(e: dict, kind: str) -> str | None:
    o = _obs(e)
    v = _num(o.get(kind))
    if v is None:
        return None
    out = phrase(kind, v)
    if e.get("source") == "aerial" and kind == "stories" and _num(o.get("height_m")) is not None:
        out += f" (roof {_num(o['height_m']):g} m)"
    return out


def _entry(e: dict, role: str, read: str, where: str, value: str | None, image: str | None) -> dict:
    return {"source": SOURCE[e["source"]], "read": read, "where": where, "date": _date(e),
            "value": _plain(value, 120) or None if value is not None else None, "image": image,
            "link": _link(_obs(e).get("source_url")) if e.get("source") == "county" else None, "role": role}


def _listing(claim: dict) -> dict:
    label = _plain(claim.get("label"), 60)
    return {"source": "Listing", "read": "Listing text", "where": "listing page" + (f", {label}" if label else ""),
            "date": None, "value": _plain(claim.get("quote") or claim.get("value"), 120) or None, "image": None,
            "link": None, "role": "context"}


# ---------- numbers ----------

def _same_value(kind: str, a: float, b: float) -> bool:
    if kind == "area_sqft":
        return abs(a - b) <= 0.05 * max(abs(b), 1.0)
    return round(a) == round(b)


def _found_number(verdict: dict) -> float | None:
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(verdict.get("found") or ""))
    return _num(m.group(0)) if m else None


def _ev_number(e: dict, kind: str, found_from: str | None) -> float | None:
    o = _obs(e)
    if e.get("source") == "scan" and kind == "area_sqft":
        t = title_sqft(str(o.get("model_name") or "").strip()) if o.get("model_name") else None
        if t and found_from == "scan_title":
            return t
    return _num(o.get(kind))


def _plan_hit(e: dict, kind: str, value: float | None) -> str | None:
    """Name of the floor plan in a record's plan list (observed.floorplans) that has this value."""
    key = {"bedrooms": "beds", "bathrooms": "baths", "area_sqft": "sqft"}.get(kind)
    if not key or value is None:
        return None
    for fp in _obs(e).get("floorplans") or []:
        if isinstance(fp, dict) and _num(fp.get(key)) is not None and _same_value(kind, _num(fp[key]), value):
            return str(fp.get("name") or "").strip() or None
    return None


def _numeric(verdict: dict, claim: dict, evs: list[dict], match: dict | None, decider: str | None) -> list[dict]:
    kind = str(claim.get("kind"))
    found_from = verdict.get("found_from")
    usable = _usable(claim, evs) or _usable(claim, evs, any_plan=True)
    found = _found_number(verdict)
    out: list[dict] = []
    dec = None
    if decider:
        mine = [e for e in evs if _ok(e) and e["source"] == decider]
        ranked = ([e for e in usable if e["source"] == decider]
                  + [e for e in mine if _num(_obs(e).get(kind)) is not None and not any(e is u for u in usable)])
        hit = [e for e in ranked if found is not None and _ev_number(e, kind, found_from) is not None
               and _same_value(kind, _ev_number(e, kind, found_from), found)]
        plan_lists = [e for e in mine if _plan_hit(e, kind, found)]
        label = str(claim.get("label") or "")
        same_plan = [e for e in mine if label and _obs(e).get("plan") and _same_plan(str(_obs(e)["plan"]), label)]
        pick = (hit or ranked or plan_lists or same_plan or [None])[0]
        if pick is None and mine:
            pick = mine[0]
        if pick is not None:
            dec = pick
            img = verdict.get("image") if verdict.get("source") == decider and verdict.get("image") \
                else _first_image(pick)
            plan_name = _plan_hit(pick, kind, found) if _num(_obs(pick).get(kind)) is None else None
            value = verdict.get("found") or _num_value(pick, kind)
            out.append(_entry(pick, "decided", _read(pick, kind, False, found_from),
                              _where(pick, kind, match, img, evs, plan_name), value, img))
    ref = _ev_number(dec, kind, found_from) if dec is not None else None
    if ref is None and dec is not None:
        ref = found
    claimed = _num(claim.get("value"))
    seen: set = set()
    for e in usable:
        if e is dec:
            continue
        v = _ev_number(e, kind, None)
        if v is None:
            continue
        if ref is not None:
            role = "agrees" if _same_value(kind, v, ref) else "differs"
        elif claimed is not None:
            role = "agrees" if _same_value(kind, v, claimed) else "differs"
        else:
            role = "context"
        img = _first_image(e)
        ent = _entry(e, role, _read(e, kind, False), _where(e, kind, match, img, evs), _num_value(e, kind), img)
        key = (ent["source"], ent["where"], ent["value"])
        if key in seen or (dec is not None and key == (out[0]["source"], out[0]["where"], out[0]["value"])):
            continue
        seen.add(key)
        out.append(ent)
    return out


# ---------- features ----------

def _feature_value(e: dict, claim: dict, evs: list[dict]) -> tuple[str | None, str]:
    """(value, role vs the listing) for one feature evidence. Absence is never "differs": it is context."""
    o, src = _obs(e), e.get("source")
    if src == "record" and isinstance(o.get("amenities"), list) and not o.get("feature_check"):
        named = record_names(claim, evs)
        return (f"Named: {named}", "agrees") if named else (None, "context")
    if src == "aerial" and isinstance(o.get("property"), dict) and not o.get("feature_check"):
        key = trust.property_key(claim)
        entry = o["property"].get(key) if key else None
        if isinstance(entry, dict) and isinstance(entry.get("present"), bool):
            thing = key.replace("_", " ")
            if entry["present"]:
                n = entry.get("count")
                cnt = f"{int(n)} × " if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 1 else ""
                return f"Shows {cnt}{thing}", "agrees"
            return f"Does not show {thing}", "context"
        return None, "context"
    if o.get("outlined"):
        return "Outlined: " + ", ".join(str(x).replace("_", " ") for x in o.get("outlined") or []), "context"
    out = amenities.outcome(o)
    nice = amenities.NICE.get(str(o.get("feature")), str(o.get("feature") or "it"))
    if o.get("how") == "scan_title" and out == "confirmed":
        return f"Scan titled '{_plain(o.get('scan_title'), 80)}'", "agrees"
    if out == "confirmed":
        return f"Saw {nice}", "agrees"
    if out == "close":
        return f"Did not see {nice}", "context"
    return None, "context"


def _feature(verdict: dict, claim: dict, evs: list[dict], match: dict | None, decider: str | None) -> list[dict]:
    cid = claim.get("id")
    key = trust.property_key(claim)
    mine = [e for e in evs if _ok(e) and cid is not None and cid in (_obs(e).get("claim_ids") or [])]
    related: list[dict] = list(mine)
    if record_names(claim, evs):
        related += [e for e in evs if _ok(e) and e["source"] == "record"
                    and isinstance(_obs(e).get("amenities"), list) and not _obs(e).get("feature_check")][:1]
    pr = property_reading(claim, evs)
    if pr:
        related.append(pr[2])
    if key:
        related += [e for e in evs if _ok(e) and e["source"] == "aerial" and key in (_obs(e).get("outlined") or [])][:1]
    uniq: list[dict] = []
    for e in related:
        if not any(e is u for u in uniq):
            uniq.append(e)
    found = str(verdict.get("found") or "")
    dec = None
    if decider:
        cands = [e for e in uniq if e["source"] == decider]
        if decider == "record":
            pref = [e for e in cands if isinstance(_obs(e).get("amenities"), list)]
        elif decider == "aerial" and "Vexcel property data" in found:
            pref = [e for e in cands if isinstance(_obs(e).get("property"), dict) and not _obs(e).get("feature_check")]
        else:
            pref = [e for e in cands if _feature_value(e, claim, evs)[1] == "agrees"]
        pool = pref or cands or [e for e in evs if _ok(e) and e["source"] == decider]
        dec = pool[0] if pool else None
    out: list[dict] = []
    if dec is not None:
        img = verdict.get("image") if verdict.get("source") == decider and verdict.get("image") else _first_image(dec)
        value = _feature_value(dec, claim, evs)[0] or _plain(found, 120) or None
        out.append(_entry(dec, "decided", _read(dec, None, True), _where(dec, None, match, img, evs), value, img))
    for e in uniq:
        if e is dec:
            continue
        value, role = _feature_value(e, claim, evs)
        img = _first_image(e)
        if dec is not None and img and img == out[0]["image"] and _obs(e).get("outlined"):
            img = None  # the decided entry already shows this outlined photo
        out.append(_entry(e, role, _read(e, None, True), _where(e, None, match, img, evs), value, img))
    return out


# ---------- public ----------

def deciding_source(verdict: dict) -> str | None:
    """The evidence source key whose value the judge used: confidence.decided_by, else the verdict's source; only
    for a confirmed / contradicted / close verdict."""
    if not isinstance(verdict, dict) or verdict.get("verdict") not in RESULT:
        return None
    conf = verdict.get("confidence") if isinstance(verdict.get("confidence"), dict) else {}
    for k in (conf.get("decided_by"), verdict.get("source")):
        if k in SOURCE:
            return k
    return None


def build_citations(verdict: dict, claim: dict | None, evidences: list[dict] | None,
                    match: dict | None = None) -> list[dict]:
    """Every proof behind one verdict, decided first, the Listing's own words last. Pure: reads only its inputs."""
    claim = claim if isinstance(claim, dict) else {}
    evs = [e for e in evidences or [] if _ok(e)]
    decider = deciding_source(verdict)
    if not claim.get("checkable", True) and verdict.get("verdict") not in RESULT:
        out: list[dict] = []
    elif amenities.is_feature_claim(claim):
        out = _feature(verdict, claim, evs, match, decider)
    elif claim.get("kind") in FIELD:
        out = _numeric(verdict, claim, evs, match, decider)
    else:
        out = []
    if decider and not any(c["role"] == "decided" for c in out) and verdict.get("image"):
        # the judge's own proof picture, with no evidence record left that names it
        out.insert(0, {"source": SOURCE[decider], "read": "Proof picture", "where": SOURCE[decider] + " proof picture",
                       "date": None, "value": _plain(verdict.get("found"), 120) or None, "image": verdict["image"],
                       "link": None, "role": "decided"})
    order = {r: i for i, r in enumerate(ROLES)}
    out.sort(key=lambda c: order[c["role"]])
    if claim.get("quote") or claim.get("value") is not None:
        out.append(_listing(claim))
    return out


def has_proof(citations: list[dict]) -> bool:
    return any(isinstance(c, dict) and c.get("source") not in (None, "Listing") for c in citations or [])


def apply_proof_rule(verdict: dict, citations: list[dict]) -> dict:
    """A confirmed / contradicted / close verdict that cites no source but the Listing becomes "couldn't check"
    (`downgraded_from` keeps what it was). Any other verdict comes back unchanged, with `citations` added."""
    v = {**verdict, "citations": citations}
    if verdict.get("verdict") in RESULT and not has_proof(citations):
        v.update({"verdict": "cant_check", "found": NO_PROOF_FOUND, "source": None, "image": None,
                  "reason": NO_PROOF_REASON, "downgraded_from": verdict.get("verdict")})
    return v


def cite(verdict: dict, claim: dict | None, evidences: list[dict] | None, match: dict | None = None) -> dict:
    """The verdict with `citations`, after the proof rule."""
    return apply_proof_rule(verdict, build_citations(verdict, claim, evidences, match))
