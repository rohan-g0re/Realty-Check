"""The few things worth knowing that the listing did not say: one small model step with a deterministic fallback.

pick_top_facts(facts, verdicts=..., claims=..., llm=..., timeout=...) -> list of at most 5 items:
    {"label", "value", "why", "sources": ["CoStar" | "Matterport" | "Vexcel" | "County"], "image": path or None,
     "topic", "by": "model" | "rule", "fact_ids": [ids of the fill_in facts it rests on]}

`facts` is harness.fill_in.build_fill_in's list as-is ({"id", "topic", "label", "value", "source", "note", "image"}).
The report's "Not on the listing" list can hold ~25 facts; this keeps the handful a renter or buyer acts on. An item
rests on one fact or on several (e.g. "CoStar: 3 floors; Vexcel roof height: about 2 floors").
  - Never picked: parcel number, building class, ratings, lot size, owner, manager / management company / contact.
    Facts the listing already speaks to (its claims' kinds and quotes, the verdicts' kinds) are left out too.
    311 / complaints: the count only. A model item that names who manages or owns the place is rejected.
  - 0 candidates: []. Otherwise the model (Claude Code CLI login through harness.model, Sonnet, low effort, no API
    key) gets the candidate facts and answers {"items": [{"fact_ids", "label", "value", "why"}]}. It may only choose
    and phrase: every id must be a candidate, every number in the label and value must appear in those facts, the
    words must be soft (never fake / lie / scam / fraud) and carry no links, emails or phone numbers. A rejected item
    is replaced by the next rule item. An item resting only on building size (two records differing) is low value
    for a renter: a rule item a renter acts on takes its place first, and it only fills a slot left over.
    `sources`, `image` and `topic` always come from the facts, never the model.
  - Any failure (no CLI, timeout after `timeout` s, bad JSON, nothing valid): the rule below, by "rule".
Rule (what a renter or buyer acts on): no elevator / walk-up; homes available + vacancy; utility metering; 311 /
complaint counts nearby; records that disagree on floors or homes; aerial observations with the top-down photo
(parked vehicles, tree cover); then records that disagree on building size; then pets, parking, lease, floor plans,
amenities, rent and a few more. The result is cached per candidate set, so one run asks the model at most once.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any, Callable

from harness.top_mismatch import _UNKIND, SRC_NAME, _ask, _parse, soft_why

TIMEOUT_S = 8.0
MAX_ITEMS = 5
LABEL_MAX = 40
VALUE_MAX = 140
WHY_MAX = 120
SRC_ORDER = ["CoStar", "Matterport", "Vexcel", "County"]

# Never picked, whatever the model says (ids from harness.fill_in, plus label words for anything new).
NEVER_IDS = {"county-parcel", "class", "rating", "lot", "owner", "county-owner", "manager", "county-manager",
             "contact", "phone", "email", "website"}
NEVER_LABEL = re.compile(r"parcel|building class|rating|lot size|\bowners?\b|\bmanag\w*|\blandlord|\bcontact|"
                         r"\bphone\b|\be-?mail\b|\bwebsite\b|\bleasing office\b", re.I)
# Words a model item may never use: who manages or owns the place is not something to pass along.
NEVER_SAY = re.compile(r"\bmanag\w*|\bowners?\b|\bowned\s+by\b|\blandlord|\bleasing\s+office\b|\bcontact\b",
                       re.I)
# Low value for a renter on their own (two records giving different building sizes): only ever fills a slot.
LOW_IDS = {"building-size", "county-size"}
COUNT_ONLY = {"county-311", "county-complaints"}  # 311 / complaints: the count only, never categories or text

# Fact ids (a trailing "-" is a prefix) the listing speaks to when its claims or verdicts have this kind, or its
# quotes hit harness.fill_in.PAGE_SAYS for this topic.
KIND_FACTS = {
    "stories": ("floors", "county-floors", "roof-height"),
    "unit_count": ("homes", "county-homes"),
    "year_built": ("year-built", "county-year-built"),
    "parking": ("parking", "parking-", "aerial-vehicles"),
    "area_sqft": ("scan-size-",),
    "rent": ("rent-",),
    "pets": ("pets", "pets-"),
    "schools": ("school-",),
    "available": ("available",),
    "vacancy": ("vacancy",),
    "lease": ("lease", "short-term"),
    "utilities": ("metering",),
    "rent_type": ("rent-type",),
    "lifestyle": ("lifestyle",),
    "elevators": ("elevators",),
    "condo": ("condo",),
    "renovated": ("renovated",),
    "manager": ("manager",),
}

# Rule tiers after the special items (walk-up, availability, metering, 311, disagreements, aerial):
# (id or prefix, why). Anything not named here is never picked by the rule.
LATER = [
    ("elevators", "How many elevators serve the building; matters for moving in and day-to-day access."),
    ("pets", "Pet rules decide whether your pet can come and what it costs."),
    ("pets-", "Pet rules decide whether your pet can come and what it costs."),
    ("parking", "Parking can add to the monthly cost or decide whether a car works here."),
    ("parking-", "Parking can add to the monthly cost or decide whether a car works here."),
    ("lease", "Lease lengths on record; check they fit your plans."),
    ("plan-count", "More floor plans are on record than the listing shows; worth asking which are open."),
    ("amenities", "Amenities on record that the listing does not mention; worth asking about."),
    ("rent-range", "Asking rents on record give a sense of the price range here."),
    ("rent-type", "Some homes may come with income rules or other limits."),
    ("lifestyle", "The community may be meant for a specific group of residents."),
    ("condo", "Homes owned one by one can mean different landlords and rules."),
    ("aerial-features", "Outdoor features seen from above that the listing does not mention."),
    ("year-built", "The building's age hints at upkeep and how it is built."),
    ("scan-size-", "A measured floor area from a 3D scan of a home here."),
    ("short-term", "Short leases may be possible here."),
]

PROMPT = """A renter or buyer is reading a check of a home listing. Below are facts our sources (CoStar, Matterport, \
Vexcel, County) know about this place that the listing does not say. Pick at most {n} that matter most to someone \
deciding whether to rent or buy here (what they pay, how they live day to day, what to ask about). Skip trivia.
Prefer what they act on: walk-up / no elevator, homes available (and vacancy), how utilities are metered, nearby \
311 counts, floor or home counts that differ between records, things seen from above that come with a photo. \
Two records giving different building sizes matters little to a renter: pick it only when nothing better is left.
An item may combine 2-3 facts on the same subject when together they say more (e.g. two sources giving different \
floor counts). Never mention who manages, owns or runs the place, or how to contact anyone.

Facts (JSON):
{items}

Rules: use only the facts above; never add a number, name or detail that is not in them; keep numbers exactly as \
given. "label": 2-5 words. "value": short, plain, under 100 characters. "why": one plain sentence under 100 \
characters on why it matters to a renter or buyer, neutral and kind: never say fake, lie, scam or fraud.
Reply with JSON only: {{"items": [{{"fact_ids": ["<id>", ...], "label": "...", "value": "...", "why": "..."}}]}}"""

_CACHE: dict[str, list] = {}
_LOCK = threading.Lock()
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12, "hundred": 100, "thousand": 1000, "half": 0.5,
             "twice": 2, "double": 2, "triple": 3}
_CONTACT = re.compile(r"@|https?:|www\.|<|\(\d{3}\)|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b", re.I)


# ---------- helpers ----------

def _numbers(text: Any) -> set[float]:
    s = str(text or "")
    out = set()
    for m in _NUM.findall(s):
        try:
            out.add(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    for w in re.findall(r"[a-z]+", s.lower()):
        if w in _WORD_NUM:
            out.add(float(_WORD_NUM[w]))
    return out


def _first_num(text: Any) -> float | None:
    m = _NUM.search(str(text or ""))
    try:
        return float(m.group(0).replace(",", "")) if m else None
    except ValueError:
        return None


def _hits(fid: str, keys) -> bool:
    return any(fid == k or (k.endswith("-") and fid.startswith(k)) for k in keys)


def _one_line(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _fact_value(f: dict) -> str:
    """The fact's value as this module may show it: 311 / complaints keep the count part only."""
    v = _one_line(f.get("value"))
    if f.get("id") in COUNT_ONLY:
        v = v.split(";")[0].strip()
    return v


def _said_ids(claims: list[dict], verdicts: list[dict]) -> set[str]:
    """Fact-id keys the listing already speaks to (by claim / verdict kind or quote keywords)."""
    from harness.fill_in import PAGE_SAYS
    kinds = {str(c.get("kind") or "") for c in claims} | {str(v.get("kind") or "") for v in verdicts}
    text = " \n ".join(f"{c.get('quote') or ''} {c.get('value') or ''}" for c in claims)
    text += " \n " + " \n ".join(f"{v.get('claimed') or ''}" for v in verdicts)
    topics = {k for k in kinds if k in KIND_FACTS}
    topics |= {k for k, rx in PAGE_SAYS.items() if k in KIND_FACTS and re.search(rx, text, re.I)}
    return {fid for t in topics for fid in KIND_FACTS[t]}


def candidates(facts: Any, claims: Any = None, verdicts: Any = None) -> list[dict]:
    """The facts that may be picked: well-formed, not banned, not already said by the listing."""
    claims = [c for c in (claims.values() if isinstance(claims, dict) else claims or []) if isinstance(c, dict)]
    verdicts = [v for v in verdicts or [] if isinstance(v, dict)]
    said = _said_ids(claims, verdicts)
    out, seen = [], set()
    for f in facts or []:
        if not isinstance(f, dict):
            continue
        fid, label, value = str(f.get("id") or ""), _one_line(f.get("label")), _fact_value(f)
        if not fid or fid in seen or not label or not value or fid in NEVER_IDS or NEVER_LABEL.search(label):
            continue
        if _hits(fid, said):
            continue
        seen.add(fid)
        out.append({**f, "id": fid, "label": label, "value": value})
    return out


def _sources(fs: list[dict]) -> list[str]:
    names = {SRC_NAME.get(str(f.get("source") or ""), "") for f in fs}
    return [s for s in SRC_ORDER if s in names]


def _image(fs: list[dict], by_id: dict[str, dict]) -> str | None:
    for f in fs:
        if f.get("image"):
            return f["image"]
    if any(f.get("source") == "aerial" for f in fs):  # an aerial observation: the top-down photo of the block
        return (by_id.get("aerial-view") or {}).get("image") or None
    return None


def _item(fs: list[dict], by_id: dict, label: str, value: str, why: str, by: str) -> dict:
    return {"label": label, "value": value, "why": why, "sources": _sources(fs), "image": _image(fs, by_id),
            "topic": str(fs[0].get("topic") or ""), "by": by, "fact_ids": [f["id"] for f in fs]}


# ---------- rule ----------

def _disagreements(by_id: dict[str, dict], size: bool = False) -> list[tuple[list[dict], str, str, str]]:
    """Records that disagree with each other: (facts, label, value, why) for floors and homes, or (size=True)
    building size only."""
    out = []
    if size:
        a, b = by_id.get("building-size"), by_id.get("county-size")
        x, y = (_first_num(a["value"]), _first_num(b["value"])) if a and b else (None, None)
        if x and y and abs(x - y) > max(1.0, 0.10 * max(x, y)):
            out.append(([a, b], "Building size: sources differ", f"CoStar: {x:,.0f} sq ft; County: {y:,.0f} sq ft",
                        "Sources give different sizes for the building; worth asking."))
        return out
    rows = []  # floors
    if "floors" in by_id and _first_num(by_id["floors"]["value"]) is not None:
        rows.append((by_id["floors"], "CoStar", _first_num(by_id["floors"]["value"]), ""))
    if "roof-height" in by_id:
        m = re.search(r"about\s+(\d+(?:\.\d+)?)\s+floors?", by_id["roof-height"]["value"])
        if m:
            rows.append((by_id["roof-height"], "Vexcel roof height", float(m.group(1)), "about "))
    if "county-floors" in by_id and _first_num(by_id["county-floors"]["value"]) is not None:
        rows.append((by_id["county-floors"], "County", _first_num(by_id["county-floors"]["value"]), ""))
    if len({round(r[2]) for r in rows}) > 1:
        value = "; ".join(f"{who}: {pre}{n:g} floor{'s' if n != 1 else ''}" for _, who, n, pre in rows)
        out.append(([r[0] for r in rows], "Floors: sources differ", value,
                    "Sources do not agree on the floor count; worth asking how many floors there are."))
    for a, b, unit, label, why, tol in (
            ("homes", "county-homes", "homes", "Homes: sources differ",
             "Sources give different home counts for the building; worth asking.", 0.05),):
        if a in by_id and b in by_id:
            x, y = _first_num(by_id[a]["value"]), _first_num(by_id[b]["value"])
            if x and y and abs(x - y) > max(1.0, tol * max(x, y)):
                out.append(([by_id[a], by_id[b]], label, f"CoStar: {x:,.0f} {unit}; County: {y:,.0f} {unit}", why))
    return out


def by_rule(cands: list[dict]) -> list[dict]:
    """The deterministic pick (at most MAX_ITEMS), from candidate facts."""
    by_id = {f["id"]: f for f in cands}
    out: list[dict] = []
    used: set[str] = set()

    def add(fs, label, value, why):
        fs = [f for f in fs if f]
        if fs and not any(f["id"] in used for f in fs) and len(out) < MAX_ITEMS:
            out.append(_item(fs, by_id, label, value, why, "rule"))
            used.update(f["id"] for f in fs)

    el = by_id.get("elevators")
    if el and re.search(r"walk-?up|no elevator", el["value"], re.I):
        add([el], "No elevator", el["value"], "Every trip up is by stairs; matters for moving in and day-to-day access.")
    av, va = by_id.get("available"), by_id.get("vacancy")
    if av or va:
        value = "; ".join(x for x in (f"{av['value']} available to rent" if av else "", va["value"] if va else "") if x)
        add([av, va], "Homes available", value, "Shows how much choice you have, and maybe room to negotiate.")
    if "metering" in by_id:
        add([by_id["metering"]], "Utility meters", by_id["metering"]["value"],
            "Tells you how utilities are billed, which can add to the monthly cost.")
    for fid in ("county-311", "county-complaints"):
        if fid in by_id:
            add([by_id[fid]], "311 requests nearby" if fid == "county-311" else "Complaints on record",
                by_id[fid]["value"], "A count of city service requests near the address; worth asking about.")
    for fs, label, value, why in _disagreements(by_id):
        add(fs, label, value, why)
    if "aerial-vehicles" in by_id:
        add([by_id["aerial-vehicles"]], "Parking seen from above", by_id["aerial-vehicles"]["value"],
            "Shows how busy parking on site was when the photo was taken.")
    if "aerial-trees" in by_id:
        add([by_id["aerial-trees"]], "Tree cover", by_id["aerial-trees"]["value"],
            "Trees mean shade and greenery around the home.")
    for fs, label, value, why in _disagreements(by_id, size=True):  # low value for a renter: after the above
        add(fs, label, value, why)
    for key, why in LATER:
        for f in cands:
            if _hits(f["id"], (key,)):
                add([f], f["label"][:LABEL_MAX], f["value"], why)
    return out


# ---------- model ----------

def _valid(raw: Any, by_id: dict[str, dict], used: set[str]) -> dict | None:
    """One model item checked against the facts it names, as an output item; None when it does not hold up."""
    if not isinstance(raw, dict):
        return None
    ids = raw.get("fact_ids")
    ids = [ids] if isinstance(ids, str) else ids
    if not isinstance(ids, list) or not ids or len(ids) > 4:
        return None
    ids = [str(i).strip() for i in ids]
    if len(set(ids)) != len(ids) or any(i not in by_id or i in used for i in ids):
        return None
    fs = [by_id[i] for i in ids]
    label, value = _one_line(raw.get("label")), _one_line(raw.get("value"))
    if not (0 < len(label) <= LABEL_MAX and 0 < len(value) <= VALUE_MAX):
        return None
    if NEVER_LABEL.search(label) or any(_UNKIND.search(s) or _CONTACT.search(s) or NEVER_SAY.search(s)
                                        for s in (label, value)):
        return None
    known = set().union(*(_numbers(f"{f['label']} {f['value']} {f.get('note') or ''}") for f in fs))
    if not _numbers(f"{label} {value}") <= known:
        return None  # a number the facts do not give: invented
    why = soft_why(raw.get("why"))
    if why and (_CONTACT.search(why) or NEVER_SAY.search(why) or not _numbers(why) <= known):
        why = ""
    return _item(fs, by_id, label, value, why, "model")


def _low(item: dict) -> bool:
    """Rests only on building size: low value for a renter."""
    return all(i in LOW_IDS for i in item.get("fact_ids") or [])


def _acted_on(item: dict) -> bool:
    """A rule item from the tiers a renter acts on (walk-up .. aerial): not building size, not the later tiers."""
    return not _low(item) and not any(_hits(i, [k for k, _ in LATER]) for i in item.get("fact_ids") or [])


def _for_model(cands: list[dict]) -> list[dict]:
    by_id = {f["id"]: f for f in cands}
    return [{"id": f["id"], "topic": str(f.get("topic") or ""), "label": f["label"][:60], "value": f["value"][:160],
             "source": SRC_NAME.get(str(f.get("source") or ""), ""), "has_photo": bool(_image([f], by_id))}
            for f in cands if f["id"] != "aerial-view"]


def _default_llm(prompt: str, timeout: float) -> str:
    """One fresh CLI call (Sonnet, low effort), killed after `timeout` s."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from harness.model.ask import PLAIN_SYSTEM
    from harness.model.cli_chat_model import ClaudeCLIChatModel, _text
    m = ClaudeCLIChatModel(model="sonnet", timeout_s=max(1, int(timeout + 0.999)), effort="low", max_parse_retries=0)
    return _text(m.invoke([SystemMessage(PLAIN_SYSTEM), HumanMessage(prompt)], role="top_facts").content)


def pick_top_facts(facts: Any, *, verdicts: Any = None, claims: Any = None, llm: Callable[[str], str] | None = None,
                   timeout: float = TIMEOUT_S, cache: bool = True) -> list[dict]:
    """At most MAX_ITEMS things worth knowing that the listing did not say (shape in the module doc). Never raises.
    `llm(prompt) -> text`: the model call (default: the Claude Code CLI); tests pass a stub."""
    try:
        cands = candidates(facts, claims, verdicts)
        rule = by_rule(cands)
    except Exception:  # noqa: BLE001
        return []
    shown = _for_model(cands)
    if not shown:
        return rule
    key = hashlib.sha256(json.dumps(shown, sort_keys=True, default=str).encode()).hexdigest()
    if cache:
        with _LOCK:
            if key in _CACHE:
                return [dict(x) for x in _CACHE[key]]
    out = rule
    try:
        text = _ask(llm or (lambda p: _default_llm(p, timeout)),
                    PROMPT.format(n=MAX_ITEMS, items=json.dumps(shown, indent=1)), timeout)
        got = _parse(text)
        raws = got.get("items") if isinstance(got.get("items"), list) else []
        by_id = {f["id"]: f for f in cands}
        main: list[dict] = []
        low: list[dict] = []
        used: set[str] = set()
        rejected = 0
        for raw in raws[:MAX_ITEMS]:
            it = _valid(raw, by_id, used)
            if it is None:
                rejected += 1
                continue
            (low if _low(it) else main).append(it)
            used.update(it["fact_ids"])
        if main or low:
            # The model's items keep their places, except building size: rule items a renter acts on come first,
            # then the model's building-size item, then (for rejected items) the next rule items on other facts.
            budget = min(MAX_ITEMS, len(main) + len(low) + rejected)
            picked = list(main)
            taken = {i for x in main for i in x["fact_ids"]}
            low_ids = {i for x in low for i in x["fact_ids"]}
            for r in [r for r in rule if _acted_on(r)] + low + [r for r in rule if not _acted_on(r)]:
                if len(picked) >= budget:
                    break
                ids = set(r["fact_ids"])
                if ids & taken or (r["by"] == "rule" and ids & low_ids):
                    continue
                picked.append(r)
                taken |= ids
            out = picked
    except Exception:  # noqa: BLE001  no CLI, timeout, bad JSON: the rule stands
        out = rule
    if cache:
        with _LOCK:
            _CACHE[key] = [dict(x) for x in out]
    return out
