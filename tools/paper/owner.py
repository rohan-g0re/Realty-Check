"""Owner TYPE from a county owner-name field: "company", "individual" or None. The name itself is never kept.

Callers read the name, call owner_type() and drop the name in the same breath; only the word goes into Evidence.
None when the name is blank, withheld, or does not clearly read as either (a tool never guesses).
"""
from __future__ import annotations

import re

from tools.kit.facts import is_company

# Public bodies and other non-person owners the kit's company words do not cover.
ORG_RE = re.compile(
    r"\b(trust|trustees?|assn|association|church|ministr(y|ies)|city of|county|state of|united states|federal|"
    r"authority|district|isd|school|college|universit(y|ies)|hospital|bank|foundation|society|council|"
    r"cooperative|condominiums?|hoa|venture|operating|lp|llc|lllp|plc|pllc)\b", re.I)
WITHHELD_RE = re.compile(r"confidential|withheld|redacted|unknown|not available|\bn/?a\b", re.I)
PERSON_CHARS = re.compile(r"[A-Za-z.'\-&,/ ]+")


def owner_type(name, withheld: bool = False) -> str | None:
    """'company' | 'individual' | None (unknown). Never returns or stores the name."""
    s = " ".join(str(name or "").split())
    if withheld or not s or WITHHELD_RE.search(s):
        return None
    if is_company(s) or ORG_RE.search(s):
        return "company"
    words = re.findall(r"[A-Za-z][A-Za-z.'\-]*", s)
    if 2 <= len(words) <= 7 and PERSON_CHARS.fullmatch(s):
        return "individual"  # letters only, no company or public-body word: a person's name
    return None
