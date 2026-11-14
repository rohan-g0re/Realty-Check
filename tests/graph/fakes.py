"""Synthetic scenario for the graph tests and the --fake demo. No sponsor data.

Listing: "2 Beds" for Plan E, "5-story building", "1,100 sq ft", "Built in 2016", "Resort-style pool".
Record: 5 stories. Scan of Plan E: "1BR W Den", 1 bedroom, 968 sq ft.
Aerial: roof 23.1 m -> 6 stories. County: 6 stories, built 2016.
"""
from __future__ import annotations

from rc_common import evidence

ADDRESS = "100 Example St, Houston, TX 77002"
LAT, LON = 29.7500, -95.3700
IMG = "C:/rc_cache_fake"  # fake absolute paths; files do not exist and nothing reads them
CALLS: list[str] = []  # every fake call, in order


def _log(name):
    CALLS.append(name)


CLAIMS = [
    {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan E",
     "quote": "2 Beds", "checkable": True},
    {"id": "c2", "kind": "stories", "value": 5, "unit": None, "scope": "building", "label": None,
     "quote": "5-story building", "checkable": True},
    {"id": "c3", "kind": "area_sqft", "value": 1100, "unit": "sq ft", "scope": "unit", "label": "Plan E",
     "quote": "1,100 sq ft", "checkable": True},
    {"id": "c4", "kind": "year_built", "value": 2016, "unit": None, "scope": "building", "label": None,
     "quote": "Built in 2016", "checkable": True},
    {"id": "c5", "kind": "amenity", "value": "resort-style pool", "unit": None, "scope": "building",
     "label": None, "quote": "Resort-style pool", "checkable": False},
]

MATCH = {
    "status": "matched", "confidence": 0.97, "property_id": "SYN-0001", "name": "Synthetic Lofts",
    "address": ADDRESS, "city": "Houston", "state": "TX", "lat": LAT, "lon": LON,
    "record": {"stories": 5, "year_built": 2016, "unit_count": 100, "building_sf": None},
    "floorplans": [
        {"name": "Plan A", "beds": 1, "baths": 1, "sqft": 720, "matterport_model_id": "FAKE_A"},
        {"name": "Plan E", "beds": 2, "baths": 2, "sqft": 1100, "matterport_model_id": "FAKE_E"},
    ],
    "matterport_model_ids": ["FAKE_A", "FAKE_E"], "how": "street number + street name + zip",
}


def extract_claims(snapshot):
    _log("extract_claims")
    return [dict(c) for c in CLAIMS]


def match_address(address, lat=None, lon=None):
    _log("match_address")
    return dict(MATCH)


def geocode(address):
    _log("geocode")
    return {"lat": LAT, "lon": LON, "matched_address": ADDRESS}


def record_evidence(match):
    _log("record_evidence")
    return evidence("record", "ok", "The building record lists 5 stories, built 2016, 100 units.",
                    {"stories": 5, "year_built": 2016, "unit_count": 100}, detail="synthetic record")


def scan_summary(model_id):
    _log(f"scan_summary:{model_id}")
    if model_id == "FAKE_E":
        return evidence("scan", "ok", "The 3D scan titled '1BR W Den' shows 1 bedroom by its room labels, "
                        "about 968 sq ft of scanned floor.",
                        {"model_id": "FAKE_E", "model_name": "1BR W Den", "bedrooms": 1, "bathrooms": 1,
                         "area_sqft": 968, "room_labels": ["Bedroom", "Den", "Bathroom", "Kitchen", "Living Room"]},
                        detail="synthetic scan")
    if model_id == "FAKE_A":
        return evidence("scan", "ok", "The 3D scan titled '1BR' shows 1 bedroom, about 715 sq ft.",
                        {"model_id": "FAKE_A", "bedrooms": 1, "area_sqft": 715}, detail="synthetic scan")
    return evidence("scan", "no_evidence", f"No scan {model_id}.", detail="synthetic")


def scan_stills(model_id, max_n=6):
    _log(f"scan_stills:{model_id}")
    imgs = [{"path": f"{IMG}/scan/{model_id}/still_{i}.jpg", "caption": f"Scan still {i}"} for i in (1, 2, 3)]
    return evidence("scan", "ok", f"3 still photos saved from scan {model_id}.", {"stills": 3}, imgs,
                    detail="synthetic stills")


def aerial_images(lat, lon, half_m=60):
    _log("aerial_images")
    return evidence("aerial", "ok", "One top-down photo and one angled photo of the block.",
                    {"angled_views": 1},
                    [{"path": f"{IMG}/aerial/top.jpg", "caption": "Top-down aerial photo"},
                     {"path": f"{IMG}/aerial/oblique_north.jpg", "caption": "Angled aerial view, camera looking north"}],
                    detail="synthetic aerial")


def building_height(lat, lon):
    _log("building_height")
    return evidence("aerial", "ok", "Roof is 23.1 m above ground: about 6 floors.",
                    {"height_m": 23.1, "stories": 6}, detail="synthetic elevation model")


def county_record(address, lat=None, lon=None, city=None):
    _log("county_record")
    return evidence("county", "ok", "County appraisal lists 6 stories, built 2016.",
                    {"stories": 6, "year_built": 2016}, detail="synthetic county")


def count_floors(image_paths):
    _log("count_floors")
    return {"status": "ok", "answer": {"stories": 6, "how_counted": "six rows of windows on the north face"},
            "confidence": 0.8, "notes": "synthetic"}


def read_floorplan(image_paths):
    _log("read_floorplan")
    return {"status": "ok", "answer": {"bedrooms": 1, "bathrooms": 1, "has_den": True,
                                       "room_labels": ["Bedroom", "Den", "Bath"]},
            "confidence": 0.7, "notes": "synthetic"}


def check_room(image_paths, claim_text):
    _log("check_room")
    return {"status": "ok", "answer": {"room_type": "den", "has_window": False, "supports_claim": "no"},
            "confidence": 0.7, "notes": "synthetic"}


def find_features(image_paths, features, setting="aerial"):
    _log(f"find_features:{setting}")
    canned = {"pool": ("seen", 0.9, 1), "rooftop_deck": ("not_seen", 0.85, 1), "parking": ("unclear", 0.4, None),
              "washer_dryer": ("seen", 0.8, 2), "stone_counters": ("unclear", 0.5, None)}
    return {"status": "ok", "confidence": 0.8, "notes": "synthetic",
            "answer": {k: dict(zip(("presence", "confidence", "image"), canned.get(k, ("unclear", 0.3, None))))
                       for k in features}}


FAKE_FUNCTIONS = {
    "extract_claims": extract_claims, "match_address": match_address, "geocode": geocode,
    "record_evidence": record_evidence, "scan_summary": scan_summary, "scan_stills": scan_stills,
    "aerial_images": aerial_images, "building_height": building_height, "county_record": county_record,
    "count_floors": count_floors, "read_floorplan": read_floorplan, "check_room": check_room,
    "find_features": find_features,
}
