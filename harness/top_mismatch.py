"""Which mismatch leads: one small model step with a deterministic fallback.

pick_top(verdicts, claims=..., llm=..., timeout=...) -> {"claim_id", "why", "by": "model" | "rule"} or None

The report's hero strikes through the listing's words for ONE mismatch; this picks which. Candidates are the
"contradicted" verdicts, or the "close" ones when nothing is contradicted (a close one never outranks a hard one).
  - 0 candidates: None.  1 candidate: that one, by "rule", no model call.
  - 2+: the model (Claude Code CLI login through harness.model, no API key) gets claim, listed value, found value,
    kind, confidence and sources, and answers {"claim_id", "why"}. The id must be one of the candidates; the reason
    is kept only when it is short (<= 120 chars), one line and soft-worded, else it is dropped (why = "").
  - Any failure (no CLI, timeout after `timeout` s, bad JSON, unknown id): the rule below, by "rule".
Rule: contradicted before close, then confidence band (high > medium > low > none), then kind (beds/baths > size >
year/floors/homes > amenities > other), then confidence score, then the relative size of the difference, then
input order. The result is cached per candidate set, so one run asks the model at most once.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any, Callable

TIMEOUT_S = 8.0
WHY_MAX = 120
_UNKIND = re.compile(r"\b(fake|faked|lie|lies|lied|lying|liar|scam|scams|scammer|fraud|fraudulent|deceiv\w*|"
                     r"mislead\w*|dishonest)\b", re.I)
KIND_TIER = {"bedrooms": 0, "bathrooms": 0, "area_sqft": 1, "year_built": 2, "stories": 2, "unit_count": 2,
             "amenity": 3, "parking": 3, "laundry": 3, "view": 3}
BAND_TIER = {"high": 0, "medium": 1, "low": 2}
SRC_NAME = {"record": "CoStar", "scan": "Matterport", "aerial": "Vexcel", "county": "County"}

_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()

PROMPT = """A renter or buyer is reading a check of a home listing. These listing claims did not match what \
independent sources found. Pick the ONE difference that matters most to the person deciding whether to rent or buy \
(think: what changes the home they get or what they pay; a well-supported finding counts more than a shaky one).

Differences (JSON):
{items}

Reply with JSON only: {{"claim_id": "<one id from the list>", "why": "<one short plain sentence, under 100 \
characters, neutral and kind: never say fake, lie or scam>"}}"""


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v or ""))
    try:
        return float(m.group(0).replace(",", "")) if m else None
    except ValueError:
        return None


def _conf(v: dict) -> dict:
    c = v.get("confidence")
    return c if isinstance(c, dict) else {}


def _kind(v: dict, claims: dict) -> str:
    return str(v.get("kind") or (claims.get(v.get("claim_id")) or {}).get("kind") or "other")


def _gap(v: dict, kind: str) -> float:
    a, b = _num(v.get("claimed")), _num(v.get("found"))
    if kind == "bedrooms" and str(v.get("claimed") or "").lower().startswith("studio"):
        a = 0.0
    if a is None or b is None:
        return 0.0
    if kind == "year_built":
        return abs(a - b) / 50.0
    return abs(a - b) / max(abs(a), 1.0)


def rule_key(v: dict, claims: dict | None = None, pos: int = 0) -> tuple:
    """Sort key of the deterministic rule (smaller leads)."""
    claims = claims or {}
    kind = _kind(v, claims)
    c = _conf(v)
    score = _num(c.get("score"))
    return (0 if v.get("verdict") == "contradicted" else 1,
            BAND_TIER.get(str(c.get("band") or "").lower(), 3),
            KIND_TIER.get(kind, 4),
            -(score if score is not None else -1.0),
            -_gap(v, kind),
            pos)


def candidates(verdicts: list) -> list:
    """The verdicts that may lead: every contradicted one, or every close one when none is contradicted."""
    vs = [v for v in verdicts or [] if isinstance(v, dict) and v.get("claim_id") is not None]
    hard = [v for v in vs if v.get("verdict") == "contradicted"]
    return hard or [v for v in vs if v.get("verdict") == "close"]


def by_rule(verdicts: list, claims: dict | None = None) -> dict | None:
    cands = candidates(verdicts)
    if not cands:
        return None
    best = min(enumerate(cands), key=lambda iv: rule_key(iv[1], claims, iv[0]))[1]
    return {"claim_id": best["claim_id"], "why": "", "by": "rule"}


def soft_why(text: Any) -> str:
    """The model's reason when it is fit to show (one short, kind line), else ''."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s or len(s) > WHY_MAX or _UNKIND.search(s) or "<" in s or "http" in s.lower():
        return ""
    return s


def _item(v: dict, claims: dict) -> dict:
    c = _conf(v)
    cl = claims.get(v.get("claim_id")) or {}
    srcs = [SRC_NAME.get(str(r.get("source")), "") for r in (c.get("sources") or []) if isinstance(r, dict)]
    return {"claim_id": str(v["claim_id"]), "claim": str(cl.get("quote") or v.get("claimed") or "")[:120],
            "kind": _kind(v, claims), "listed": str(v.get("claimed") or "")[:80], "found": str(v.get("found") or "")[:80],
            "result": "does not match" if v.get("verdict") == "contradicted" else "close, not exact",
            "confidence": {"band": c.get("band"), "score": c.get("score")} if c else None,
            "decided_by": SRC_NAME.get(str(c.get("decided_by") or v.get("source") or ""), ""),
            "sources": sorted({s for s in srcs if s})}


def _default_llm(prompt: str, timeout: float) -> str:
    """One fresh CLI call (Sonnet, low effort), killed after `timeout` s."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from harness.model.ask import PLAIN_SYSTEM
    from harness.model.cli_chat_model import ClaudeCLIChatModel, _text
    m = ClaudeCLIChatModel(model="sonnet", timeout_s=max(1, int(timeout + 0.999)), effort="low", max_parse_retries=0)
    return _text(m.invoke([SystemMessage(PLAIN_SYSTEM), HumanMessage(prompt)], role="top_mismatch").content)


def _ask(llm: Callable[[str], str], prompt: str, timeout: float) -> str:
    """llm(prompt) in a daemon thread with a hard deadline (a late answer is ignored)."""
    box: dict = {}

    def go():
        try:
            box["out"] = llm(prompt)
        except BaseException as e:  # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=go, daemon=True, name="rc-top-mismatch")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"top mismatch pick took over {timeout:g} s")
    if "err" in box:
        raise box["err"]
    return str(box.get("out") or "")


def _parse(text: str) -> dict:
    from harness.model.ask import parse_json
    got = parse_json(text)
    if not isinstance(got, dict):
        raise ValueError("not an object")
    return got


def pick_top(verdicts: list, *, claims: Any = None, llm: Callable[[str], str] | None = None,
             timeout: float = TIMEOUT_S, cache: bool = True) -> dict | None:
    """The mismatch that leads the report: {"claim_id", "why", "by"} or None when nothing is off. Never raises.
    `claims`: the run's claims (list or {id: claim}) for kinds and quotes. `llm(prompt) -> text`: the model call
    (default: the Claude Code CLI); tests pass a stub."""
    if isinstance(claims, list):
        claims = {c.get("id"): c for c in claims if isinstance(c, dict)}
    claims = claims if isinstance(claims, dict) else {}
    try:
        cands = candidates(verdicts)
        rule = by_rule(cands, claims)
    except Exception:  # noqa: BLE001
        return None
    if rule is None or len(cands) == 1:
        return rule
    items = [_item(v, claims) for v in cands]
    key = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()
    if cache:
        with _LOCK:
            if key in _CACHE:
                return dict(_CACHE[key])
    out = rule
    try:
        text = _ask(llm or (lambda p: _default_llm(p, timeout)), PROMPT.format(items=json.dumps(items, indent=1)),
                    timeout)
        got = _parse(text)
        cid = str(got.get("claim_id") or "").strip()
        ids = {str(v["claim_id"]): v["claim_id"] for v in cands}
        if cid in ids:
            out = {"claim_id": ids[cid], "why": soft_why(got.get("why")), "by": "model"}
    except Exception:  # noqa: BLE001  no CLI, timeout, bad JSON: the rule stands
        out = rule
    if cache:
        with _LOCK:
            _CACHE[key] = dict(out)
    return out
