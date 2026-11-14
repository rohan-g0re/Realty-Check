"""Pick the listing's own building address from the page's address candidates.

`pick_address(snapshot, index=None, matcher=None)` cross-checks every candidate the extension found
(`snapshot["address_candidates"]`, best first, plus `address_text`) against the kit index and returns
`{chosen, property, confidence, why, alternatives}`. Pure: no network, no model call, no state.

A candidate wins by matching a residential kit property, best one whose name agrees with the page
(title, web address, page text). A candidate whose only support is a footer, a company/office label or a
"nearby" section is set aside when another candidate matches a property named like the page. When nothing
matches, the extension's best candidate is kept with low confidence, and `why` says so.
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse

from tools.kit.match import units_building, units_words
from tools.kit.normalize import parse, street_key

MAX_CANDIDATES = 6
RESIDENTIAL_KINDS = {"building", "home"}
# Words in property names that say nothing about which property it is.
GENERIC = {"the", "at", "on", "of", "and", "in", "by", "a", "an", "apartments", "apartment", "apts", "apt", "homes",
           "home", "residences", "residence", "residential", "living", "community", "communities", "lofts", "loft",
           "flats", "rentals", "rental", "luxury", "new", "street", "st", "ave", "avenue", "road", "rd", "drive",
           "dr", "blvd", "lane", "ln", "way", "place", "pl", "tower", "towers", "north", "south", "east", "west",
           "www", "com", "net", "org", "html", "htm", "php", "rent", "for", "near", "unit", "units", "listing"}


def _words(s: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split() if len(w) > 2 and w not in GENERIC
            and not w.isdigit()]


def _page_signals(snapshot: dict) -> dict:
    """Word sets for the page's own name: title, web address (host and path), visible text near the top."""
    url = urlparse(str(snapshot.get("url") or ""))
    host = (url.hostname or "").lower()
    host = re.sub(r"^www\.", "", host)
    return {"title": set(_words(snapshot.get("title"))),
            "domain": host.rsplit(".", 1)[0].replace(".", "").replace("-", ""),
            "path": set(_words(url.path.replace("-", " ").replace("_", " "))),
            "page": set(_words(" ".join([str(snapshot.get("heading") or ""), str(snapshot.get("text") or "")[:600]])))}


def name_agreement(name: str | None, signals: dict) -> tuple[float, str | None]:
    """Share of the property name's distinctive words found on the page, and where ('title', 'domain', 'page')."""
    # "St. Andrie at Buffalo Heights": pages often use only the part before "at" / "on" / a dash.
    head = re.split(r"\s+(?:at|on)\s+|\s+[-|–]\s+", str(name or ""), maxsplit=1, flags=re.I)[0]
    best, where = 0.0, None
    for toks in (_words(name or ""), _words(head)):
        if not toks:
            continue
        for key in ("title", "domain", "path", "page"):
            sig = signals[key]
            hit = [t for t in toks if (t in sig if isinstance(sig, set) else len(t) >= 4 and t in sig)]
            share = len(hit) / len(toks)
            if share > best:
                best, where = share, ("domain" if key == "path" else key)
    return best, where


def _where_set(c: dict) -> set[str]:
    w = c.get("where") or []
    if isinstance(w, str):
        w = re.split(r"[\s,+]+", w)
    return {str(x) for x in w if x}


def _strong_place(w: set[str]) -> bool:
    """Support beyond a footer position: heading, top of listing, residential page data, web address, title."""
    return bool(w & {"h1", "hero", "url", "meta", "title-words", "label:located"}) or any(
        t.startswith("jsonld:") and t[7:] in RES_LD for t in w)


RES_LD = {"ApartmentComplex", "Apartment", "Residence", "SingleFamilyResidence", "House", "Accommodation",
          "GatedResidenceCommunity", "Place", "RealEstateListing", "Room", "Suite", "LodgingBusiness"}
ORG_LD = {"Organization", "Corporation", "RealEstateAgent", "Person", "ProfessionalService", "LocalBusiness"}


def candidates_of(snapshot: dict) -> list[dict]:
    """Snapshot candidates, best first, de-duplicated; `address_text` added when missing (older extensions)."""
    out, seen = [], set()
    for c in snapshot.get("address_candidates") or []:
        if isinstance(c, dict) and str(c.get("text") or "").strip():
            out.append({"text": str(c["text"]).strip(), "score": float(c.get("score") or 0),
                        "where": _where_set(c), "why": str(c.get("why") or "")})
    at = str(snapshot.get("address_text") or "").strip()
    if at and not any(c["text"].lower() == at.lower() for c in out):
        out.insert(0, {"text": at, "score": out[0]["score"] if out else 0.0, "where": {"picked"}, "why": ""})
    for c in out:
        k = re.sub(r"\W+", " ", c["text"].lower()).strip()
        if k not in seen:
            seen.add(k)
            c["key"] = k
    return [c for c in out if "key" in c][:MAX_CANDIDATES]


def index_matcher(index) -> Callable[[str], dict]:
    """A small offline matcher over an index-shaped dict ({"properties": [...]}) or a property list.

    Same address rules as tools.kit.match_address in short: street number in range + street name, then zip,
    else city, else a unique street hit; buildings first. Used for tests and offline checks; the server passes
    tools.kit.match_address itself.
    """
    props = index.get("properties", []) if isinstance(index, dict) else list(index or [])
    by_street: dict[str, list] = {}
    cities = set()
    for p in props:
        for k in p.get("keys") or []:
            by_street.setdefault(street_key(k["street_name"]), []).append((p, k))
            if k.get("city"):
                cities.add(k["city"])
    cities_l = sorted(cities)

    def match(address: str) -> dict:
        q = parse(address or "", cities_l)
        if q["num_lo"] is None or not q["streets"]:
            return {"status": "no_match", "confidence": 0.0, "how": "no street number and name"}
        same = [(p, k) for st in q["streets"] for p, k in by_street.get(st, [])
                if k.get("num_lo") is not None and k["num_lo"] <= q["num_lo"] <= k["num_hi"]]
        tiers = [("street number + street name + zip", 0.97, [h for h in same if q["zip"] and h[1].get("zip") == q["zip"]]),
                 ("street number + street name + city", 0.9, [h for h in same if q["city"] and h[1].get("city") == q["city"]]),
                 ("street number + street name (unique)", 0.75,
                  same if not q["zip"] and not q["city"] and len({p["property_id"] for p, _ in same}) == 1 else [])]
        for how, conf, hits in tiers:
            if hits:
                ps = {p["property_id"]: p for p, _ in hits}
                cands = [p for p in ps.values() if p.get("kind") == "building"] or list(ps.values())
                units = units_building(hits, q, conf, how)  # only condo units on record: the same rule as the kit
                if units:
                    return units
                if len(cands) > 1 and q["unit"] and all(p.get("kind") == "home" for p in cands):
                    cands = [p for p in cands if str(p.get("unit") or "").lower() == q["unit"]][:1]
                if len(cands) != 1:
                    return {"status": "no_match", "confidence": 0.0, "how": f"{len(cands)} properties share this address"}
                p = cands[0]
                return {"status": "matched", "confidence": conf, "how": how, **{k: p.get(k) for k in (
                    "property_id", "kind", "name", "address", "city", "state", "lat", "lon", "record",
                    "floorplans", "matterport_model_ids")}}
        return {"status": "no_match", "confidence": 0.0, "how": "no property at this street number, street and zip/city"}
    return match


def _default_matcher() -> Callable[[str], dict]:
    from tools.kit import match_address
    return match_address


def _residential(m: dict) -> bool:
    return (m.get("kind") or "building") in RESIDENTIAL_KINDS


def _multifamily(m: dict) -> bool:
    rec = m.get("record") or {}
    return bool(m.get("floorplans")) or (rec.get("unit_count") or 0) > 1


def pick_address(snapshot: dict, index=None, matcher: Callable[[str], dict] | None = None) -> dict:
    """Choose the listing's building address. Returns
    {chosen, property, confidence, why, alternatives: [{text, name, why}]}.

    `property` is the Match (CONTRACT 2.3) of the chosen candidate when it matched, else None.
    `index` (index-shaped dict or property list) builds an offline matcher; else `matcher`, else
    tools.kit.match_address. `snapshot["address_locked"]` true keeps `address_text` (the person picked it).
    """
    snapshot = snapshot or {}
    cands = candidates_of(snapshot)
    if not cands:
        return {"chosen": None, "property": None, "confidence": 0.0,
                "why": "We couldn't find an address on this page.", "alternatives": []}
    match = index_matcher(index) if index is not None else (matcher or _default_matcher())
    signals = _page_signals(snapshot)

    for i, c in enumerate(cands):
        try:
            m = match(c["text"])
        except Exception:  # noqa: BLE001 - a broken lookup is a no-match for this candidate, never a crash
            m = {"status": "no_match", "confidence": 0.0, "how": "lookup failed"}
        c["match"] = m if isinstance(m, dict) else {"status": "no_match", "confidence": 0.0}
        ok = c["match"].get("status") == "matched"
        w = c["where"]
        c["agree"], c["agree_where"] = name_agreement(c["match"].get("name"), signals) if ok else (0.0, None)
        c["weak_place"] = (not _strong_place(w)) and bool(
            ("footer" in w) or ("nearby" in w) or ("label:corporate" in w)
            or any(t.startswith("jsonld:") and t[7:] in ORG_LD for t in w))
        s = (1.0 if i == 0 else 0.5 if i == 1 else 0.0) + min(max(c["score"], 0.0), 10.0) * 0.1
        if ok:
            s += 3 * float(c["match"].get("confidence") or 0)
            s += 0.5 if _multifamily(c["match"]) else 0.0
            s += 4 * c["agree"]
            if not _residential(c["match"]):
                s -= 4
        if "label:corporate" in w:
            s -= 2.5
        if "nearby" in w and not _strong_place(w):
            s -= 2.5
        if "footer" in w and not _strong_place(w):
            s -= 1.5
        c["pick_score"] = s

    named = [c for c in cands if c["match"].get("status") == "matched" and _residential(c["match"]) and c["agree"] >= 0.5]
    if named:  # a property named like the page exists: weak-place candidates without that support step aside
        for c in cands:
            if c["weak_place"] and c["agree"] < 0.5:
                c["pick_score"] -= 10

    locked = bool(snapshot.get("address_locked")) and str(snapshot.get("address_text") or "").strip()
    if locked:
        best = next((c for c in cands if c["text"].lower() == locked.lower()), cands[0])
    else:
        best = max(cands, key=lambda c: (c["pick_score"], -cands.index(c)))
    m = best["match"]
    ok = m.get("status") == "matched"
    conf_m = float(m.get("confidence") or 0)
    n_ok = sum(c["match"].get("status") == "matched" for c in cands)

    if locked:
        conf, why = (round(0.9 * conf_m, 2) if ok else 0.3), "You picked this address."
    elif ok and not _residential(m):
        conf, why = 0.3, "The closest building record is not a home or apartment building, so this may not be the listing."
    elif ok and best["agree"] >= 0.5:
        conf = round(min(0.97, 0.95 * conf_m + 0.03 * best["agree"]), 2)
        why = {"title": "Matches the building named in the page title.",
               "domain": "Matches the building named in the site's web address.",
               "page": "Matches the building named on the page."}[best["agree_where"]]
    elif ok and best["weak_place"]:
        conf = round(0.55 * conf_m, 2)
        why = "Matches a building record, but it appears only in the page footer or an office line, so it may be a company address."
    elif ok:
        conf = round(0.8 * conf_m, 2)
        why = ("Matches a building record at this address." if len(cands) == 1
               else "The only address on the page that matches a building record." if n_ok == 1
               else "Matches a building record at the page's main address.")
    else:
        conf = 0.2
        why = ("No building record matches the addresses on this page; this one looks most like the listing's own."
               if len(cands) > 1 else "No building record matches this address, so we may not have its building.")

    if ok and m.get("units_only") and not locked and _residential(m) and not best["weak_place"]:
        units = m.get("units_on_file") or []
        why = (f"There is no building record here, but {units_words(units)} of this building "
               f"{'is' if len(units) == 1 else 'are'} on record.")
        if m.get("unit_not_on_record"):
            why += f" Unit {m['unit_not_on_record']} is not."

    alts = []
    for c in sorted((c for c in cands if c is not best), key=lambda c: -c["pick_score"]):
        cm = c["match"]
        alts.append({"text": c["text"], "name": cm.get("name") if cm.get("status") == "matched" else None,
                     "property_id": cm.get("property_id") if cm.get("status") == "matched" else None,
                     "why": c["why"] or ("Matches a building record." if cm.get("status") == "matched" else "Also on the page.")})
    return {"chosen": best["text"], "property": m if ok else None, "confidence": conf, "why": why, "alternatives": alts[:4]}
