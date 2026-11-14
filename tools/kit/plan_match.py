"""Link the floor plans a listing page names to the building record's plans and to 3D scans, by structure.

Page plan names are often generic ("1x1", "2x2", "Efficiency", "Maple Court - 2 Bedrooms") or carry a size
("1BR 774 sq ft"), while the record names plans by code ("A1", "Plan 3K + Den") and scan titles mix both
("Plan E - 1 Bed + Den - 850 SF"). Order, first that applies (docs/DECISIONS.md, 2026-11-14 plan matching):
 1. name: exact record name, then word-boundary prefix / containment, then the same plan code ("Plan 3K + Den"
    and "3K"; "B6+Den" and "B6");
 2. size: (beds, baths[, den]) from the name and the page's claims for it, nearest record sq ft within SIZE_TOL;
    (a generic name with an exact page size needs TITLE_TOL; "from 650 sq ft" or "559-603" is not a tie-break);
 3. type: a generic name (no code, no size) that several record plans fit stays a TYPE-LEVEL match: no plan is
    picked, claims are checked against the range across the fitting plans (check_type_claim), and a scan is
    attached only when it is the only one of that type or the only one labelled as an example.
A type-level match never produces a mismatch verdict: outside the range is "can't check", never "contradicted".
Pure functions; no network, no model.
"""
from __future__ import annotations

import re

import rc_common

SIZE_TOL = 0.08    # page sq ft vs record plan sq ft for a size match
TITLE_TOL = 0.03   # scan title sq ft vs record plan sq ft for a scan pick
RANGE_TOL = 0.05   # a type-level size claim within 5% of the fitting plans' range is confirmed
UNIT_KINDS = ("bedrooms", "bathrooms", "area_sqft")

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_N = r"(\d+(?:\.\d)?|zero|one|two|three|four|five|six)"
_BEDS = re.compile(_N + r"\s*[- ]?\s*(?:bed(?:room)?s?|bdrms?|bdr|br|bd)(?![a-z])", re.I)
_BATHS = re.compile(_N + r"(?:\s+(?:and\s+(?:a\s+)?)?(?:1/2|half))?\s*[- ]?\s*(?:bath(?:room)?s?|ba)(?![a-z])", re.I)
_XBY = re.compile(r"(?<![\w.])(\d)\s*[x×]\s*(\d(?:\.5)?)(?![\d.])", re.I)
_SLASH = re.compile(r"^\s*(\d)\s*/\s*(\d(?:\.5)?)\s*$")
_STUDIO = re.compile(r"\b(?:studio|efficiency|eff|micro)\b", re.I)
_DEN = re.compile(r"\b(?:den|study)\b", re.I)
_SQFT = re.compile(r"(?<![\d.,])(\d{1,2},\d{3}|\d{3,4})\s*(?:sf|sq\.?\s*f(?:ee)?t\.?|sqft|square\s*f(?:ee|oo)t)(?![a-z])",
                   re.I)
_CODE_PLAN = re.compile(r"\bplan\s+([a-z]{0,3}\d{0,3}[a-z]{0,2}(?:[.-]\d|-[a-z])?)(?![\w])", re.I)
_CODE_FLOOR = re.compile(r"(?<![\w])([A-Z]{1,3}\d{0,3})\s+[Ff]loor\s*[Pp]lan\b")
_CODE_TOKEN = re.compile(r"(?<![\w.])([A-Z]{1,3}\d{1,3}[A-Za-z]?(?:[.-]\d|-[A-Z])?|\d{1,2}[A-Z])(?![\w])")
_EXAMPLE = re.compile(r"\b(?:example|typical|representative|model\s+(?:unit|home|apartment)|sample|"
                      r"features\s+(?:will\s+|may\s+)?vary)\b", re.I)
_FILLER = re.compile(r"\b(?:floor\s*plans?|plans?|the|apartments?|apts?|units?|homes?|layout|style|type|with|w|"
                     r"and|plus|from|starting|at|sq|ft|sf|feet|square|bed(?:room)?s?|bath(?:room)?s?|br|bd|ba|"
                     r"studio|efficiency|eff|micro|den|study|one|two|three|four|five|six|zero|half)\b", re.I)


def _num(v) -> float | None:
    if isinstance(v, str) and v.strip().lower() in _WORDS:
        return float(_WORDS[v.strip().lower()])
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


_SEP = re.compile(r"\s+[-–—:|]\s+")


def core_label(label: str, building: str = "") -> str:
    """The plan part of a page name: the building's name taken out ('Maple Court - 1 Bedroom' -> '1 Bedroom');
    when the name is not known, the part after the last ' - ' if that part alone states a kind of home."""
    t = str(label or "").strip()
    if len(building.strip()) >= 3 and building.strip().lower() in t.lower():
        t = re.sub(re.escape(building.strip()), " ", t, flags=re.I)
        return " ".join(t.split()).strip(" -–—:|,")
    parts = _SEP.split(t)
    if len(parts) > 1 and is_generic(parts[-1]) and not parse_plan(" - ".join(parts[:-1]))["code"]:
        return parts[-1].strip()
    return t


def parse_plan(text: str, building: str = "") -> dict:
    """Structure in a plan name or scan title: {beds, baths, den, studio, sqft, code}; None where it says nothing.
    'Studio'/'Efficiency' -> 0 beds; '1x1' -> 1 bed 1 bath; 'Plan E - 1 Bed + Den - 850 SF' -> code 'E'."""
    t = str(text or "")
    if building and len(building.strip()) >= 3:
        t = re.sub(re.escape(building.strip()), " ", t, flags=re.I)
    out = {"beds": None, "baths": None, "den": bool(_DEN.search(t)), "studio": bool(_STUDIO.search(t)),
           "sqft": None, "code": None}
    m = _SQFT.search(t)
    if m:
        out["sqft"] = _num(m.group(1))
    rest = _SQFT.sub(" ", t)
    m = _XBY.search(rest) or _SLASH.match(rest)
    if m:
        out["beds"], out["baths"] = _num(m.group(1)), _num(m.group(2))
    m = _BEDS.search(rest)
    if m and out["beds"] is None:
        out["beds"] = _num(m.group(1))
    m = _BATHS.search(rest)
    if m and out["baths"] is None:
        b = _num(m.group(1))
        if b is not None and re.search(r"(?:1/2|half)", m.group(0), re.I):
            b += 0.5
        out["baths"] = b
    if out["studio"] and out["beds"] is None:
        out["beds"] = 0.0
    out["code"] = _code(rest)
    return out


def _code(t: str) -> str | None:
    """A plan code: 'Plan 13' -> '13', 'C Floor Plan' -> 'C', 'B6+Den' -> 'B6', a bare short name 'E2' -> 'E2'."""
    t = _XBY.sub(" ", t)
    for rx in (_CODE_PLAN, _CODE_FLOOR):
        m = rx.search(t)
        if m and m.group(1).strip():
            return m.group(1).strip().upper()
    bare = re.sub(r"[\s+&/,-]*\b(?:den|loft|live/work|study)\b.*$", "", t.strip(), flags=re.I).strip(" -,")
    if re.fullmatch(r"[A-Za-z]{1,3}\d{0,3}[A-Za-z]?(?:[.-]\d|-[A-Za-z])?", bare) and not _FILLER.fullmatch(bare):
        return bare.upper()
    m = _CODE_TOKEN.search(t)
    if m and not re.fullmatch(r"\d+(?:BR|BD|BA)", m.group(1), re.I):
        return m.group(1).upper()
    return None


def is_generic(label: str, building: str = "") -> bool:
    """A name that only states a type ('1x1', 'Efficiency', 'Maple Court - 2 Bedrooms'): no code, no size."""
    p = parse_plan(label, building)
    if p["code"] or p["sqft"] or (p["beds"] is None and not p["studio"]):
        return False
    t = re.sub(re.escape(building.strip()), " ", label or "", flags=re.I) if len(building.strip()) >= 3 else label
    t = _FILLER.sub(" ", _XBY.sub(" ", _SLASH.sub(" ", t or "")))
    return not re.sub(r"[\d\W_]+", "", t)


def _norm(name: str, building: str = "") -> str:
    t = str(name or "").lower()
    if len(building.strip()) >= 3:
        t = t.replace(building.strip().lower(), " ")
    t = re.sub(r"\b(?:floor\s*plan|plan|the)\b", " ", t)
    return " ".join(re.sub(r"[^\w.+]+", " ", t).split())


def _kit_shape(fp: dict) -> dict:
    """Record plan structure: its numbers win over its name (a record plan named '1 Bedroom + Den' may have 2 beds)."""
    p = parse_plan(str(fp.get("name") or ""))
    sq = _num(fp.get("sqft"))
    return {"beds": _num(fp.get("beds")), "baths": _num(fp.get("baths")), "den": p["den"],
            "sqft": sq if sq else None, "code": p["code"]}


def type_words(beds, baths=None, den: bool = False) -> str:
    """'studio', '1-bed, 1-bath', '1-bed + den, 2-bath'."""
    if beds is None:
        return "floor plan"
    b = "studio" if beds == 0 else f"{_n(beds)}-bed"
    if den:
        b += " + den"
    return b + (f", {_n(baths)}-bath" if baths is not None else "")


def _n(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:g}"


def listing_facts(label: str, claims: list[dict]) -> dict:
    """{bedrooms, bathrooms, area_sqft} the page's claims give for one plan label (None when absent)."""
    out = {k: None for k in UNIT_KINDS}
    for c in claims or []:
        if (c.get("label") or "").strip().lower() == (label or "").strip().lower() and c.get("kind") in UNIT_KINDS:
            if out[c["kind"]] is None:
                out[c["kind"]] = _num(c.get("value"))
    return out


def label_values(label: str, claims: list[dict]) -> dict:
    """Every distinct value the page's claims give for one plan label, per kind: {bedrooms: [..], bathrooms: [..],
    area_sqft: [..]}, in page order. A size given as a minimum or range ('from 650 sq ft') is left out."""
    lab = (label or "").strip().lower()
    out: dict[str, list[float]] = {k: [] for k in UNIT_KINDS}
    for c in claims or []:
        if (c.get("label") or "").strip().lower() != lab or c.get("kind") not in UNIT_KINDS:
            continue
        if c["kind"] == "area_sqft" and _LOOSE.search(str(c.get("quote") or "")):
            continue
        v = _num(c.get("value"))
        if v is not None and v not in out[c["kind"]]:
            out[c["kind"]].append(v)
    return out


def _one_plan(cands: list[dict], sqft: float | None) -> dict | None:
    """The single record plan nearest to sqft within SIZE_TOL (None when none fits, or on a tie)."""
    near = _nearest(cands, sqft, SIZE_TOL) if sqft else []
    return near[0] if len(near) == 1 else None


def _shared(label: str, claims: list[dict], pool: list[dict], p: dict) -> tuple[bool, list[dict]]:
    """Is this label a line several of the page's plans share ('1/1 with Wood Floors' over eleven plans of
    568-882 sq ft) rather than one plan? It is when its claims give more than one bed or bath count, or sizes no
    one record plan fits (spread beyond SIZE_TOL, or nearest to different plans). Returns (shared, the record plans
    of `pool` it may stand for: those whose beds and baths are among the ones the label gives)."""
    vals = label_values(label, claims)
    beds = [p["beds"]] if p["beds"] is not None else vals["bedrooms"]
    baths = [p["baths"]] if p["baths"] is not None else vals["bathrooms"]
    cands = [fp for fp in pool
             if all(not want or (_kit_shape(fp)[k] is not None and _kit_shape(fp)[k] in want)
                    for k, want in (("beds", beds), ("baths", baths)))]
    if p["den"]:
        cands = _den_filter(cands, True)
    sizes = vals["area_sqft"]
    if len(vals["bedrooms"]) > 1 or len(vals["bathrooms"]) > 1:
        return True, cands
    if len(sizes) < 2:
        return False, cands
    if (max(sizes) - min(sizes)) / min(sizes) > SIZE_TOL:
        return True, cands
    picks = {id(fp) if fp else None for fp in (_one_plan(cands, s) for s in sizes)}
    return len(picks) > 1, cands


def plan_for_claim(claim: dict, link: dict) -> dict | None:
    """Per-claim resolution on a shared label (link['shared']): the one record plan among the label's plans whose
    size is nearest the claim's, within SIZE_TOL. None when no plan fits, on a tie, or for a size given as a minimum
    or range. A caller may use it to tie one size claim to one record plan."""
    if claim.get("kind") != "area_sqft" or _LOOSE.search(str(claim.get("quote") or "")):
        return None
    return _one_plan(link.get("kit_plans") or [], _num(claim.get("value")))


def _by_name(label: str, fps: list[dict], building: str, want: dict | None = None) -> list[dict]:
    """Record plans the name points at: exact; word-boundary prefix; containment; then, looser, the normalized name
    or the plan code ('Plan 3K + Den' / '3K'). A looser hit must not disagree with the page's beds and baths
    (`want`): a coincidence of codes is not enough to call the page's numbers wrong."""
    p = (label or "").strip().lower()
    if not p:
        return []
    names = [(str(fp.get("name") or "").strip().lower(), fp) for fp in fps]
    for test in (lambda n: n == p,
                 lambda n: re.match(r"^" + re.escape(p) + r"(?![\w.])", n) is not None,
                 lambda n: len(p) >= 2 and re.search(r"(?<![\w.])" + re.escape(p) + r"(?![\w.])", n) is not None):
        hits = [fp for n, fp in names if n and test(n)]
        if hits:
            return hits
    def agrees(fp):
        k = _kit_shape(fp)
        return all((want or {}).get(x) is None or k[x] is None or k[x] == want[x] for x in ("beds", "baths"))

    nl = _norm(label, building)
    hits = [fp for _, fp in names if nl and _norm(str(fp.get("name") or "")) == nl and agrees(fp)]
    if hits:
        return hits
    code = parse_plan(label, building)["code"]
    if code:
        return [fp for fp in fps if _kit_shape(fp)["code"] == code and agrees(fp)]
    return []


def _fits(shape: dict, want: dict) -> bool:
    """Record plan shape agrees with the wanted beds / baths (unknown = no constraint)."""
    for k in ("beds", "baths"):
        if want.get(k) is not None and shape.get(k) is not None and shape[k] != want[k]:
            return False
        if want.get(k) is not None and shape.get(k) is None:
            return False
    return True


def _den_filter(cands: list[dict], den: bool) -> list[dict]:
    """Prefer plans with the same den flag; '1x1' leaves den plans out when plain ones exist."""
    same = [fp for fp in cands if _kit_shape(fp)["den"] == den]
    return same or cands


def _nearest(cands: list[dict], sqft: float, tol: float = SIZE_TOL) -> list[dict]:
    """Plans nearest to sqft within tol (several only on an exact tie)."""
    scored = []
    for fp in cands:
        ks = _kit_shape(fp)["sqft"]
        if ks:
            d = abs(ks - sqft) / sqft
            if d <= tol:
                scored.append((d, fp))
    if not scored:
        return []
    best = min(d for d, _ in scored)
    return [fp for d, fp in scored if d == best]


_LOOSE = re.compile(r"\b(?:from|starting|starts|up\s+to|min(?:imum)?|max(?:imum)?|avg|average)\b"
                    r"|\d\s*[-–]\s*\d|\d\s*\+", re.I)


def _loose_size(label: str, claims: list[dict]) -> bool:
    """The page's size for this plan is a minimum, maximum or range ('from 650 sq ft', '559-603')."""
    lab = (label or "").strip().lower()
    return any((c.get("label") or "").strip().lower() == lab and c.get("kind") == "area_sqft"
               and _LOOSE.search(str(c.get("quote") or "")) for c in claims)


def _title(s) -> str:
    if isinstance(s, dict):
        return str((s.get("observed") or {}).get("model_name") or "") if s.get("status", "ok") == "ok" else ""
    return str(s or "")


def _scan_fit(fp: dict, title: str, building: str) -> float | None:
    """How well a scan title fits one record plan: sq ft gap (smaller is better), None = no fit."""
    t, k = parse_plan(title, building), _kit_shape(fp)
    named = bool(t["code"] and k["code"] and t["code"] == k["code"])
    name = str(fp.get("name") or "").strip()
    if not named and len(name) >= 3:
        named = re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", title, re.I) is not None
    if t["beds"] is None or k["beds"] is None or t["beds"] != k["beds"]:
        return None
    if t["baths"] is not None and k["baths"] is not None and t["baths"] != k["baths"]:
        return None
    if t["sqft"] and k["sqft"]:
        gap = abs(t["sqft"] - k["sqft"]) / k["sqft"]
        return gap if gap <= TITLE_TOL or (named and gap <= SIZE_TOL) else None
    return 0.05 if named else None


def _pick_for_plan(fp: dict, titles: dict[str, str], taken: set[str], building: str) -> tuple[str, str] | None:
    fits = sorted((mid in taken, gap, mid, t) for mid, t in titles.items()
                  if (gap := _scan_fit(fp, t, building)) is not None)
    if not fits or (len(fits) > 1 and fits[0][:2] == fits[1][:2]):
        return None  # none, or two scans fit equally well: not guessing
    return fits[0][2], fits[0][3]


def _type_fit(want: dict, title: str, building: str) -> bool:
    t = parse_plan(title, building)
    if t["beds"] is None or t["beds"] != want.get("beds"):
        return False
    if want.get("baths") is not None and t["baths"] is not None and t["baths"] != want["baths"]:
        return False
    return t["den"] == bool(want.get("den"))


def link_plan(label: str, match: dict, claims: list[dict] | None = None, scans: dict | None = None) -> dict:
    """One page plan name -> its record plan(s) and scan.

    `scans`: {model_id: scan title, or scan_summary Evidence}; property-level scans whose titles are known.
    Returns {label, how, kit, kit_plans, ambiguous, beds, baths, den, sqft_min, sqft_max, scan_id, scan_how,
    scan_title, note, generic, shared[, claim_plans]}. `how`: "name" | "size" | "type" | None; `kit` is the one
    record plan (None when ambiguous or missing); `kit_plans` every plan that fits. `shared`: the label is a line
    several of the page's plans share (its claims give sizes or counts no one plan has): a type-level link, never
    one plan; `claim_plans` maps each of its size claims to the record plan of that size (plan_for_claim), and
    check_type_claim compares each size with that plan. `scan_how`: "kit" (the plan's own scan), "title" (a property
    scan whose title fits the plan), "type_only" (the one scan of that type), "type_example" (the one scan of
    that type labelled as an example), else None."""
    label = (label or "").strip()
    building = str((match or {}).get("name") or "")
    fps = [fp for fp in ((match or {}).get("floorplans") or []) if isinstance(fp, dict)] \
        if (match or {}).get("status") == "matched" else []
    titles = {mid: _title(s) for mid, s in (scans or {}).items() if _title(s)}
    r = {"label": label, "how": None, "kit": None, "kit_plans": [], "ambiguous": False, "beds": None,
         "baths": None, "den": False, "sqft_min": None, "sqft_max": None, "scan_id": None, "scan_how": None,
         "scan_title": None, "note": "", "generic": False, "shared": False}
    if not label:
        return r
    core = core_label(label, building)
    p = parse_plan(core)
    facts = listing_facts(label, claims or [])
    want = {"beds": p["beds"] if p["beds"] is not None else facts["bedrooms"],
            "baths": p["baths"] if p["baths"] is not None else facts["bathrooms"], "den": p["den"]}
    if (p["beds"] is not None and facts["bedrooms"] is not None and p["beds"] != facts["bedrooms"]) or \
            (p["baths"] is not None and facts["bathrooms"] is not None and p["baths"] != facts["bathrooms"]):
        r["note"] = f"The page's numbers for {label} do not agree with its name, so no plan was matched."
        return r
    sqft = p["sqft"] or facts["area_sqft"]
    r["den"] = p["den"]

    # 1. name
    hits = _by_name(label, fps, building, want) or (_by_name(core, fps, building, want) if core != label else [])
    how, size_only = "name", False
    generic = is_generic(core)
    # a line several of the page's plans share (sizes or counts no one plan has): never one plan for the whole
    # label; each of its size claims is compared with its own plan (plan_for_claim, check_type_claim)
    shared, shared_plans = (False, []) if len(hits) == 1 else _shared(label, claims or [], hits or fps, p)
    if shared:
        hits, how = shared_plans, "type"
    elif len(hits) > 1:  # several plans share the name: narrow by the page's numbers
        narrowed = _den_filter([fp for fp in hits if _fits(_kit_shape(fp), want)] or hits, p["den"])
        if sqft and len(narrowed) > 1:
            narrowed = _nearest(narrowed, sqft) or narrowed
        hits = narrowed
    # 2. size / structure
    if not hits and not shared and want["beds"] is not None:
        fit = [fp for fp in fps if _fits(_kit_shape(fp), want)]
        if p["den"]:
            fit = _den_filter(fit, True)
        if sqft and generic and not p["sqft"] and _loose_size(label, claims or []):
            hits, how = _den_filter(fit, False), "type"  # "1x1 from 650 sq ft": a minimum or range, not one plan
        elif sqft:
            hits = _nearest(fit, sqft, TITLE_TOL if generic and not p["sqft"] else SIZE_TOL)
            hits, how = (_den_filter(hits, p["den"]) if len(hits) > 1 else hits), "size"
            if not hits and generic and not p["sqft"]:
                hits, how = _den_filter(fit, False), "type"
        elif generic:  # no size: the name states the kind of home; one plan of that kind, or a type-level match
            fit = _den_filter(fit, p["den"])
            hits, how = fit, ("size" if len(fit) == 1 else "type")
    elif not hits and not shared and want["beds"] is None and sqft and not _loose_size(label, claims or []):
        # size alone (no bed count on the page; baths, when given, must agree): only when exactly one record plan
        # is within SIZE_TOL of it, else no plan
        fit = [fp for fp in fps if _fits(_kit_shape(fp), want)]
        if p["den"]:
            fit = _den_filter(fit, True)
        within = [fp for fp in fit if _kit_shape(fp)["sqft"]
                  and abs(_kit_shape(fp)["sqft"] - sqft) / sqft <= SIZE_TOL]
        if len(within) == 1:
            hits, how, size_only = within, "size", True
    if not hits:
        if shared:
            r.update(shared=True, note=f"{label} is a line several of the page's plans share, and none of the "
                                       "building's plans fits it, so no plan was matched.")
        if titles and p["code"]:  # no record plan, but a scan titled with this plan's code
            named = [(mid, t) for mid, t in titles.items() if parse_plan(t, building)["code"] == p["code"]]
            if len(named) == 1:
                r.update(scan_id=named[0][0], scan_how="title", scan_title=named[0][1])
        return r

    sizes = [s for s in (_kit_shape(fp)["sqft"] for fp in hits) if s]
    beds = {_kit_shape(fp)["beds"] for fp in hits}
    baths = {_kit_shape(fp)["baths"] for fp in hits}
    r.update(kit_plans=hits, beds=beds.pop() if len(beds) == 1 else None, baths=baths.pop() if len(baths) == 1 else None,
             sqft_min=min(sizes) if sizes else None, sqft_max=max(sizes) if sizes else None)
    if len(hits) == 1 and not shared:
        fp = hits[0]
        name = str(fp.get("name") or "").strip()
        r.update(kit=fp, how=how)
        if how == "size" and not sqft:
            r["note"] = (f"Matched to the building's plan {name}, its only "
                         f"{type_words(r['beds'], r['baths'], _kit_shape(fp)['den'])} plan.")
        elif how == "size" and size_only:
            r["note"] = (f"Matched to the building's plan {name} by size alone: the page does not give its bedrooms, "
                         f"and it is the only plan of that size.")
        elif how == "size":
            r["note"] = f"Matched to the building's plan {name} by size."
        elif label.strip().lower() not in name.lower():
            r["note"] = f"Matched to the building's plan {name} by its plan name."
        own = fp.get("matterport_model_id")
        ts, ks = parse_plan(titles.get(own, ""), building)["sqft"], _kit_shape(fp)["sqft"]
        if own and not (ts and ks and abs(ts - ks) / ks > SIZE_TOL):
            r.update(scan_id=own, scan_how="kit", scan_title=titles.get(own))
        else:
            taken = {f.get("matterport_model_id") for f in fps if f.get("matterport_model_id")}
            got = _pick_for_plan(fp, {m: t for m, t in titles.items() if m != own}, taken, building)
            if got:
                r.update(scan_id=got[0], scan_how="title", scan_title=got[1])
                r["note"] = (r["note"] + " " if r["note"] else "") + (
                             f"{label} has no 3D scan of its own in the building record; the building's scan titled "
                             f"'{got[1]}' fits its plan {name}.")
        return r

    # 3. type-level: several plans fit; none is picked
    tw = type_words(r["beds"], r["baths"], p["den"])
    rng = _range_words(r["sqft_min"], r["sqft_max"])
    r.update(how=how, ambiguous=True, generic=generic, shared=shared)
    if shared:
        said = label_values(label, claims or [])["area_sqft"]
        said_w = _range_words(min(said), max(said)) if said else ""
        r["claim_plans"] = {c.get("id"): str(got.get("name") or "").strip() for c in claims or []
                            if (c.get("label") or "").strip().lower() == label.lower()
                            and (got := plan_for_claim(c, r))}
        r["note"] = (f"{label} is a line several of the page's plans share{' (' + said_w + ')' if said_w else ''}, "
                     f"not one floor plan, so no single plan was picked; each size is compared with the building's "
                     f"{tw} plan nearest to it ({len(hits)} plan{'s' if len(hits) != 1 else ''}"
                     f"{', ' + rng if rng else ''}).")
    elif generic:
        r["note"] = (f"{label} names a kind of home, not one floor plan: the building record has {len(hits)} {tw} "
                     f"plans{', ' + rng if rng else ''}, so no single plan was picked.")
    else:
        names = ", ".join(str(fp.get("name") or "").strip() for fp in hits[:6]) + (", …" if len(hits) > 6 else "")
        r["note"] = (f"{label} fits {len(hits)} of the building's plans equally well ({names}"
                     f"{'; ' + rng if rng else ''}), so no single plan was picked.")
    want_t = {"beds": r["beds"], "baths": r["baths"], "den": p["den"]}
    codes = {_kit_shape(fp)["code"] for fp in hits} - {None}
    own = {fp.get("matterport_model_id") for fp in hits if fp.get("matterport_model_id")}
    if generic:  # any scan of this kind of home may stand in as an example
        fitting = own | {mid for mid, t in titles.items() if r["beds"] is not None and _type_fit(want_t, t, building)}
    else:  # a named plan: only scans filed under, or titled with, that plan
        fitting = own | {mid for mid, t in titles.items() if parse_plan(t, building)["code"] in codes}
    fitting = sorted(fitting)
    example = [mid for mid in fitting if _EXAMPLE.search(titles.get(mid, ""))]
    pick, why = None, None
    if len(fitting) == 1:
        pick, why = fitting[0], "type_only"
    elif len(example) == 1:
        pick, why = example[0], "type_example"
    if pick:
        title = titles.get(pick)
        what = f" ('{title}')" if title else ""
        r.update(scan_id=pick, scan_how=why, scan_title=title)
        if not generic:
            r["note"] += (f" The building's only 3D scan filed under {label}{what} is shown as an example; the home "
                          "offered may be a different one of these plans.")
        else:
            lead = ("The building's only 3D scan of a" if why == "type_only"
                    else "The building's 3D scan labelled as an example of a")
            r["note"] += (f" {lead} {tw} home{what} is shown as an example; the home offered may be a different "
                          f"{tw} plan.")
    elif fitting:
        r["note"] += f" {len(fitting)} 3D scans fit this kind of home, so none was picked for it."
    return r


def link_plans(match: dict, claims: list[dict], scans: dict | None = None) -> dict[str, dict]:
    """link_plan for every plan label the page's claims name: {label: link}."""
    labels = []
    for c in claims or []:
        lab = (c.get("label") or "").strip()
        if lab and lab not in labels:
            labels.append(lab)
    return {lab: link_plan(lab, match, claims, scans) for lab in labels}


def _range_words(lo, hi) -> str:
    if not lo or not hi:
        return ""
    return f"{lo:,.0f} sq ft" if lo == hi else f"{lo:,.0f}–{hi:,.0f} sq ft"


def plan_types(floorplans: list[dict]) -> list[dict]:
    """Record plans grouped by kind of home: [{type, beds, baths, den, plans, sqft_min, sqft_max, scan_ids}]."""
    groups: dict[tuple, dict] = {}
    for fp in floorplans or []:
        if not isinstance(fp, dict):
            continue
        k = _kit_shape(fp)
        if k["beds"] is None:
            continue
        g = groups.setdefault((k["beds"], k["baths"] if k["baths"] is not None else -1, k["den"]),
                              {"type": type_words(k["beds"], k["baths"], k["den"]), "beds": fp.get("beds"),
                               "baths": fp.get("baths"), "den": k["den"], "plans": [], "sqft_min": None,
                               "sqft_max": None, "scan_ids": []})
        g["plans"].append(str(fp.get("name") or "").strip())
        if k["sqft"]:
            g["sqft_min"] = k["sqft"] if g["sqft_min"] is None else min(g["sqft_min"], k["sqft"])
            g["sqft_max"] = k["sqft"] if g["sqft_max"] is None else max(g["sqft_max"], k["sqft"])
        mid = fp.get("matterport_model_id")
        if mid and mid not in g["scan_ids"]:
            g["scan_ids"].append(mid)
    return [groups[k] for k in sorted(groups)]


def type_evidence(link: dict, match: dict) -> dict:
    """Record Evidence for a type-level link. Counts only when every fitting plan shares them; the size only as a
    range (`area_sqft_min/max`), never as `area_sqft`, so a judge cannot read one plan's size as a mismatch."""
    fps = link.get("kit_plans") or []
    obs = {"plan": link.get("label"), "plan_type": True,
           "floorplans": [{"name": str(fp.get("name") or "").strip(), "beds": fp.get("beds"), "baths": fp.get("baths"),
                           "sqft": fp.get("sqft")} for fp in fps]}
    if link.get("beds") is not None:
        obs["bedrooms"] = link["beds"]
    if link.get("baths") is not None:
        obs["bathrooms"] = link["baths"]
    if link.get("sqft_min"):
        obs["area_sqft_min"], obs["area_sqft_max"] = link["sqft_min"], link["sqft_max"]
    if link.get("note"):
        obs["plan_match"] = link["note"]
    tw = type_words(link.get("beds"), link.get("baths"), link.get("den"))
    rng = _range_words(link.get("sqft_min"), link.get("sqft_max"))
    return rc_common.evidence("record", "ok" if fps else "no_evidence",
                              f"The building record has {len(fps)} {tw} plans{', ' + rng if rng else ''}.", obs,
                              detail=f"CoStar hackathon kit floor plan list of property {(match or {}).get('property_id')};"
                                     f" listing plan {link.get('label')} matched as a kind of home, not one plan.")


def check_type_claim(claim: dict, link: dict) -> dict | None:
    """Verdict for a unit claim against a type-level link: inside the fitting plans' range (sq ft within RANGE_TOL)
    or a count some fitting plan has -> confirmed; otherwise can't check. Never contradicted or close: which plan
    the page means is not known. None when the link is not type-level or the claim is not a unit claim."""
    kind = claim.get("kind")
    fps = link.get("kit_plans") or []
    if not link.get("ambiguous") or kind not in UNIT_KINDS or not fps:
        return None
    v = _num(claim.get("value"))
    label = link.get("label") or claim.get("label")
    if link.get("generic"):
        whose = (f"{label} is a kind of home, not one plan; the building record's {len(fps)} "
                 f"{type_words(link.get('beds'), link.get('baths'), link.get('den'))} plans")
    else:
        whose = f"{len(fps)} of the building's plans fit {label}; they"
    base = {"claim_id": claim.get("id"), "source": "record", "image": None}
    if kind == "area_sqft" and link.get("shared") and not _LOOSE.search(str(claim.get("quote") or "")):
        # a line several plans share: this size against the one plan of that size, never against another plan
        claimed = f"{v:,.0f} sq ft" if v else str(claim.get("value"))
        fp = plan_for_claim(claim, link)
        ks = _kit_shape(fp)["sqft"] if fp else None
        if not v or not ks:
            return {**base, "verdict": "cant_check", "claimed": claimed, "found": None,
                    "reason": f"Listing says {claimed} for {label}, a line several of the page's plans share. None "
                              "of the building's plans that fit it has this size, so it could not be tied to one plan."}
        k = _kit_shape(fp)
        found, ok = f"{ks:,.0f} sq ft", abs(ks - v) / v <= RANGE_TOL
        return {**base, "verdict": "confirmed" if ok else "cant_check", "claimed": claimed, "found": found,
                "reason": f"Listing says {claimed} for {label}, a line several of the page's plans share. The "
                          f"building's {type_words(k['beds'], k['baths'], k['den'])} plan nearest that size is "
                          f"{str(fp.get('name') or '').strip()}, with {found}"
                          + (", so this size fits." if ok else ", so this size could not be tied to one plan.")}
    if kind == "area_sqft":
        lo, hi = link.get("sqft_min"), link.get("sqft_max")
        claimed = f"{v:,.0f} sq ft" if v else str(claim.get("value"))
        found = _range_words(lo, hi) or None
        if not v or not lo:
            return {**base, "verdict": "cant_check", "claimed": claimed, "found": found,
                    "reason": f"Listing says {claimed} for {label}. The building record gives no sizes for these plans."}
        ok = lo * (1 - RANGE_TOL) <= v <= hi * (1 + RANGE_TOL)
        return {**base, "verdict": "confirmed" if ok else "cant_check", "claimed": claimed, "found": found,
                "reason": f"Listing says {claimed} for {label}. {_cap(whose)} {'are' if lo != hi else 'have'} {found}"
                          + (", so this size fits." if ok else ", so this size could not be tied to one of them.")}
    key, word = ("beds", "bedroom") if kind == "bedrooms" else ("baths", "bathroom")
    have = sorted({_num(fp.get(key)) for fp in fps if _num(fp.get(key)) is not None})
    claimed = _count(v, word) if v is not None else str(claim.get("value"))
    found = (" or ".join(_n(x) for x in have) + f" {word}{'' if have == [1.0] else 's'}") if have else None
    ok = v is not None and v in have
    return {**base, "verdict": "confirmed" if ok else "cant_check", "claimed": claimed, "found": found,
            "reason": (f"Listing says {claimed} for {label}. {_cap(whose)} have {found}." if ok else
                       f"Listing says {claimed} for {label}. It could not be tied to one of the building's plans.")}


def _count(v, word: str) -> str:
    return f"{_n(v)} {word}{'' if v == 1 else 's'}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]
