"""Amenity, parking, laundry and view claims: which picture (if any) can show them, and what a look means.

Pure functions, no I/O. The run decides when to look; the judge turns the stored presence answers into
verdicts. Presence only: a picture never decides a count.
"""
from __future__ import annotations

import re

FEATURE_KINDS = ("amenity", "parking", "laundry", "view", "other")
SEEN_MIN = 0.7       # seen with at least this confidence -> confirmed
NOT_SEEN_MIN = 0.8   # clearly not there with at least this confidence -> close (never contradicted)

# (pattern, key, plain description for the vision model, where to look: "aerial" | "scan")
FEATURES = [
    (r"\bpools?\b(?!\s*tables?)", "pool", "an outdoor swimming pool", "aerial"),  # "2 pools" too (trust.py)
    (r"roof\s*-?top|\broof (?:deck|terrace|lounge|garden|patio)|sky ?(?:deck|terrace)", "rooftop_deck",
     "a rooftop deck, terrace or lounge: seating, furniture, shade structures or planters on a roof", "aerial"),
    (r"dog (?:run|park)|bark park|pet (?:park|run)", "dog_run",
     "a fenced dog run or dog park (a small fenced patch of turf or gravel)", "aerial"),
    (r"(?:tennis|pickleball|basketball|sport|sports|volleyball|bocce) courts?", "sports_court",
     "an outdoor sports court (tennis, pickleball or basketball)", "aerial"),
    (r"courtyard", "courtyard", "a courtyard: an open-air space enclosed by the building", "aerial"),
    (r"playground", "playground", "a children's playground", "aerial"),
    (r"garage|parking|carport", "parking",
     "parking for this property: a parking garage or structure, a garage entrance or ramp, or a parking lot", "aerial"),
    (r"washer|dryer|in[- ]?unit laundry|in[- ]home laundry|laundry:\s*in unit", "washer_dryer",
     "a washer and dryer (or a stacked washer-dryer) inside the apartment", "scan"),
    (r"stainless", "stainless_appliances", "stainless steel kitchen appliances", "scan"),
    (r"quartz", "quartz_counters", "quartz kitchen countertops: smooth engineered stone with an even or "
     "marble-like pattern (speckled natural granite and laminate do not count)", "scan"),
    (r"granite", "granite_counters", "granite kitchen countertops: speckled or veined natural stone", "scan"),
    (r"stone count|marble count", "stone_counters", "stone kitchen countertops (not laminate)", "scan"),
    (r"plank|hardwood|wood(?:[- ](?:style|look))? floor", "plank_flooring",
     "wood or wood-look plank flooring", "scan"),
    (r"balcon|\bpatio\b", "balcony", "a private balcony or patio belonging to the apartment", "scan"),
    (r"walk-?in closet", "walk_in_closet", "a walk-in closet", "scan"),
    (r"fireplace", "fireplace", "a fireplace", "scan"),
    (r"dishwasher", "dishwasher", "a dishwasher in the kitchen", "scan"),
    (r"kitchen island|island kitchen", "kitchen_island", "a kitchen island", "scan"),
]
_FEATURES = [(re.compile(p, re.I), k, d, w) for p, k, d, w in FEATURES]
# Things no picture shows, checked before the patterns above ("pet spa" is not a pool, a laundry room is not in-unit).
NOT_PICTURED = re.compile(r"24[- ]hour|concierge|co-?working|business center|package|utilities|pet (?:wash|spa)|"
                          r"\bspas?\b(?!.*\bpools?\b)|wifi|wi-fi|internet|smart home|on-site|onsite|management|"
                          r"maintenance|valet|storage|bike|ev charg|electric vehicle|cats|dogs welcome|pets? "
                          r"(?:friendly|welcome)|laundry (?:room|facilit|center)", re.I)
NICE = {k: k.replace("_", " ") for _, k, _, _ in FEATURES}
NICE.update({"rooftop_deck": "rooftop deck or lounge", "washer_dryer": "washer and dryer",
             "stone_counters": "stone counters", "quartz_counters": "quartz counters",
             "granite_counters": "granite counters", "sports_court": "sports court"})

# Amenity rooms a property-level 3D scan title can name: (pattern on the claim, pattern on the scan title).
TITLE_ROOMS = [
    (r"fitness|gym|workout", r"fitness|\bgym\b"),
    (r"club ?room|clubhouse|club house", r"club"),
    (r"lounge", r"lounge"),
    (r"\bpools?\b(?!\s*tables?)", r"\bpools?\b"),
    (r"co-?working|business center|library", r"co-?work|business|library"),
    (r"theater|theatre|cinema|screening", r"theat|cinema|screening"),
    (r"game room", r"game"),
]


def text_of(claim: dict) -> str:
    """The claim's own words: its value ('garage parking'), else its quote. A quote can list several
    amenities ('pool, spa, fitness center'), so it is used only when there is no value."""
    v = claim.get("value")
    return str(v).strip() if isinstance(v, str) and v.strip() else str(claim.get("quote") or "")


def feature_for(claim: dict) -> tuple[str, str, str] | None:
    """(key, description, where) of the first picturable feature the claim names, else None.
    Building laundry rooms, pet spas and services return None: nothing in our data shows them."""
    if claim.get("kind") not in FEATURE_KINDS or not claim.get("checkable", True):
        return None
    words = text_of(claim)
    hits = [(rx.search(words).start(), k, d, w) for rx, k, d, w in _FEATURES if rx.search(words)]
    if not hits:
        return None
    pos, key, desc, where = min(hits)
    blocker = NOT_PICTURED.search(words)
    if blocker and blocker.start() <= pos and not re.search(r"\band\b|,", words[blocker.end():pos]):
        return None  # e.g. "24-hour fitness center", "pet spa", "laundry room on every floor"
    if key == "washer_dryer" and re.search(r"laundry (?:room|facilit|center)|shared|on each floor", words, re.I):
        return None
    return key, desc, where


def title_rooms(claim: dict) -> list[str]:
    """Title patterns of amenity rooms this claim names ('24-hour fitness center' -> fitness)."""
    if claim.get("kind") not in FEATURE_KINDS or not claim.get("checkable", True):
        return []
    words = text_of(claim)
    return [t for c, t in TITLE_ROOMS if re.search(c, words, re.I)]


def is_feature_claim(claim: dict) -> bool:
    return claim.get("kind") in FEATURE_KINDS


def outcome(observed: dict) -> str:
    """confirmed | close | unclear | skipped, from a presence answer stored in Evidence.observed."""
    p = observed.get("presence")
    try:
        conf = float(observed.get("presence_confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if p == "seen" and conf >= SEEN_MIN:
        return "confirmed"
    if p == "not_seen" and conf >= NOT_SEEN_MIN:
        return "close"
    if p == "skipped":
        return "skipped"
    return "unclear"
