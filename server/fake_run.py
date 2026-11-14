"""Built-in fake runner: a canned event sequence for one listing, used when the harness is missing or RC_FAKE=1.

Same signature as harness.graph.run_check(snapshot, emit) -> done event.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from rc_common import cache_dir, evidence

GAP = (0.3, 0.8)  # seconds between events; tests may patch this


def _placeholder(name: str, draw_fn) -> str:
    """Small neutral JPEG under rc_cache/fake/ (never in the repo)."""
    from PIL import Image, ImageDraw

    path = cache_dir("fake") / name
    if not path.exists():
        img = Image.new("RGB", (480, 320), (236, 236, 233))
        draw_fn(ImageDraw.Draw(img))
        img.save(path, "JPEG", quality=85)
    return str(path)


def _scan_img(d) -> None:
    # simple floor plan: one bedroom, a den, living area
    line = (120, 120, 118)
    d.rectangle([30, 30, 450, 290], outline=line, width=3)
    d.line([250, 30, 250, 190], fill=line, width=3)
    d.line([30, 190, 450, 190], fill=line, width=3)
    d.line([340, 190, 340, 290], fill=line, width=3)
    d.text((110, 100), "BEDROOM", fill=line)
    d.text((330, 100), "LIVING", fill=line)
    d.text((370, 235), "DEN", fill=line)
    d.text((150, 235), "KITCHEN", fill=line)


def _aerial_img(d) -> None:
    # oblique-ish block with six floor bands
    d.rectangle([0, 250, 480, 320], fill=(205, 205, 200))
    for i in range(6):
        y = 70 + i * 30
        d.rectangle([140, y, 340, y + 28], fill=(190, 190, 186), outline=(150, 150, 146))
    d.text((150, 50), "about 23 m", fill=(110, 110, 108))


def _images() -> tuple[str, str]:
    return _placeholder("scan_plan.jpg", _scan_img), _placeholder("aerial_oblique.jpg", _aerial_img)


def _fake_facts(scan_path: str, aerial_path: str) -> list[dict]:
    """Invented "Fill me in" facts (docs/CONTRACT.md 2.7); every value is made up."""
    rec = "From the building record, as of when it was last researched."
    rent = "Asking rent on record as of 2026-11-12; today's prices may differ."

    def f(fid, topic, label, value, source, note="", image=None):
        return {"id": fid, "topic": topic, "label": label, "value": value, "source": source, "note": note,
                "image": image}
    return [
        f("units", "Building", "Number of homes", "100", "record", rec),
        f("class", "Building", "Building class", "Class B", "record",
          "CoStar's quality grade within the local market (A is the top). As of when it was last researched."),
        f("manager", "Building", "Management company", "Example Property Management LLC", "record",
          "Company named in the building record or its rental listing."),
        f("plan-a", "Homes & floor plans", "Plan A", "1 bed, 1 bath, 720 sq ft, 40 homes of this plan", "record",
          "A floor plan the listing does not show. " + rec),
        f("scan-rooms-plan-e", "Homes & floor plans", "Rooms in the 3D scan of Plan E",
          "Bedroom, Den, Living, Kitchen", "scan", "Labelled automatically by the scan; labels can be wrong.",
          scan_path),
        f("rent-range", "Rent", "Asking rent across the building", "$1,400–$2,600 a month", "record", rent),
        f("vacancy", "Rent", "Vacancy", "6% of homes empty", "record",
          "Share of homes empty when the building was last researched."),
        f("pets-dogs", "Rules", "Dogs", "Allowed, up to 2, $300 deposit, $25 a month pet rent", "record", rec),
        f("parking-garage", "Rules", "Parking: garage", "$75 a month, assigned spaces available", "record", rec),
        f("aerial-view", "Around it", "The block from above",
          "Top-down Vexcel aerial photo of the building and what sits next to it", "aerial", "", aerial_path),
        f("county-parcel", "Records", "County parcel number", "0000000000000", "county",
          "Look it up with the county appraisal district."),
    ]


def run_fake(snapshot: dict, emit) -> dict:
    scan_path, aerial_path = _images()
    address = snapshot.get("address_text") or "418 Linden Grove Ave, Houston, TX 77006"
    run_id = snapshot.get("run_id", "fake")

    def say(ev: dict) -> None:
        time.sleep(random.uniform(*GAP))
        emit(ev)

    claims = [
        {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan E",
         "quote": "2 Beds", "checkable": True},
        {"id": "c2", "kind": "stories", "value": 5, "unit": None, "scope": "building", "label": None,
         "quote": "5-story building", "checkable": True},
        {"id": "c3", "kind": "area_sqft", "value": 1100, "unit": "sqft", "scope": "unit", "label": "Plan E",
         "quote": "1,100 sq ft", "checkable": True},
        {"id": "c4", "kind": "amenity", "value": "pool", "unit": None, "scope": "building", "label": None,
         "quote": "Resort-style pool", "checkable": True},
        {"id": "c5", "kind": "amenity", "value": "rooftop deck", "unit": None, "scope": "building", "label": None,
         "quote": "Rooftop deck", "checkable": True},
        {"id": "c6", "kind": "year_built", "value": 2015, "unit": None, "scope": "building", "label": None,
         "quote": "Built in 2015", "checkable": True},
    ]
    match = {"status": "matched", "confidence": 0.97, "property_id": "900001", "name": "Linden Grove Lofts",
             "address": address, "city": "Houston", "state": "TX", "lat": 29.7520, "lon": -95.3700,
             "record": {"stories": 5, "year_built": 2016, "unit_count": 100, "building_sf": 123456},
             "floorplans": [{"name": "Plan E", "beds": 2, "baths": 2, "sqft": 1100, "matterport_model_id": "fake01"}],
             "matterport_model_ids": ["fake01"], "how": "street number + street name + zip"}

    say({"type": "step", "agent": "orchestrator", "text": "Reading the listing page."})
    say({"type": "step", "agent": "claims", "text": "Pulling out what the page says about the home and building."})
    say({"type": "claims", "claims": claims})
    say({"type": "step", "agent": "matcher", "text": "Looking up the building at this address."})
    say({"type": "match", "match": match})
    say({"type": "evidence", "evidence": evidence(
        "record", "ok", "The building record lists 5 stories, built in 2016.",
        {"stories": 5, "year_built": 2016, "unit_count": 100}, detail="Building record")})
    say({"type": "evidence", "evidence": evidence(  # invented plan numbers, for the claim field
        "record", "ok", "The building record lists Plan E: 2 beds, 2 baths, 1,100 sq ft.",
        {"plan": "Plan E", "kit_plan": "Plan E", "bedrooms": 2, "bathrooms": 2, "area_sqft": 1100},
        detail="Building record floor plan")})
    say({"type": "step", "agent": "inside", "text": "Walking through the 3D scan of Plan E."})
    say({"type": "evidence", "evidence": evidence(
        "scan", "ok", "The 3D scan shows 1 bedroom and a den, about 968 sq ft.",
        {"bedrooms": 1, "bathrooms": 1, "area_sqft": 968, "room_labels": ["Bedroom", "Den", "Living", "Kitchen"],
         "label": "1BR W Den", "plan": "Plan E"},
        [{"path": scan_path, "caption": "Floor plan from the 3D scan"}], detail="3D scan room labels")})
    say({"type": "step", "agent": "outside", "text": "Measuring the roof height from Vexcel aerial photos."})
    say({"type": "evidence", "evidence": evidence(
        "aerial", "ok", "Roof is 23.1 m above ground: about 6 floors.", {"height_m": 23.1, "stories": 6},
        [{"path": aerial_path, "caption": "Oblique view from the north"}], detail="Aerial height estimate")})
    say({"type": "evidence", "evidence": evidence(  # invented picture check: the photo could not tell
        "aerial", "ok", "Looking at the Vexcel aerial photos for a pool: the vision model could not tell.",
        {"feature_check": True, "claim_ids": ["c4"], "feature": "pool", "looked_at": "Vexcel aerial photos",
         "presence": "unclear", "presence_confidence": 0.5},
        [{"path": aerial_path, "caption": "Oblique view from the north"}], detail="Aerial picture check")})
    say({"type": "evidence", "evidence": evidence(  # invented Vexcel property data for the parcel
        "aerial", "ok", "Vexcel property data (survey of 2025-12-19) shows a pool (about 1,900 sq ft); it shows no "
        "hot tub.", {"property": {"captured": "2025-12-19", "source": "Vexcel property data",
                                  "pool": {"present": True, "confidence": 0.93, "area_sqft": 1900, "kind": "in-ground",
                                           "stable": True},
                                  "hot_tub": {"present": False, "stable": True}, "vehicles": 14, "buildings": 1}},
        detail="Vexcel property data: outdoor features marked on the parcel from the aerial surveys")})
    say({"type": "step", "agent": "paper", "text": "Checking the county property record."})
    say({"type": "evidence", "evidence": evidence(
        "county", "ok", "The county lists 6 stories, built in 2016.", {"stories": 6, "year_built": 2016},
        detail="County appraisal record")})
    say({"type": "step", "agent": "judge", "text": "Comparing each claim with what we found."})
    verdicts = [
        {"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom + den",
         "source": "scan", "reason": "The 3D scan labels this plan as one bedroom with a den.", "image": scan_path,
         "confidence": {  # invented, in the judge's shape (docs/CONTRACT.md 2.9)
             "score": 64, "band": "medium", "decided_by": "scan",
             "why": "Matterport walks through the home and its title states the room count; CoStar matches the "
                    "listing, so we are less sure.",
             "sources": [{"source": "scan", "says": "1 bedroom", "weight": 0.9, "agrees_with_listing": False},
                         {"source": "record", "says": "2 bedrooms", "weight": 0.81, "agrees_with_listing": True}]}},
        {"claim_id": "c2", "verdict": "contradicted", "claimed": "5 stories", "found": "6 stories",
         "source": "aerial", "reason": "The roof is 23.1 m high, about 6 floors, and the county also lists 6.",
         "image": aerial_path,
         "confidence": {  # invented, in the judge's shape (docs/CONTRACT.md 2.9)
             "score": 82, "band": "high", "decided_by": "aerial",
             "why": "Vexcel measures the roof height directly; County agrees; CoStar matches the listing, so we are "
                    "less sure.",
             "sources": [{"source": "aerial", "says": "about 6 floors", "weight": 0.8, "agrees_with_listing": False},
                         {"source": "county", "says": "6 floors", "weight": 0.7, "agrees_with_listing": False},
                         {"source": "record", "says": "5 floors", "weight": 0.6, "agrees_with_listing": True}]}},
        {"claim_id": "c3", "verdict": "contradicted", "claimed": "1,100 sq ft", "found": "968 sq ft",
         "source": "scan", "reason": "The 3D scan measures about 968 sq ft, 12% smaller than listed.", "image": None,
         "confidence": {  # invented, in the judge's shape
             "score": 58, "band": "medium", "decided_by": "scan",
             "why": "Matterport measures the floor of the scanned home; CoStar matches the listing, so we are less "
                    "sure.",
             "sources": [{"source": "scan", "says": "968 sq ft", "weight": 0.85, "agrees_with_listing": False},
                         {"source": "record", "says": "1,100 sq ft", "weight": 0.77, "agrees_with_listing": True}]}},
        {"claim_id": "c4", "verdict": "confirmed", "claimed": "resort-style pool", "found": "In Vexcel property data",
         "source": "aerial", "reason": "Listing says “Resort-style pool”. Vexcel property data shows a pool, "
                                      "about 1,900 sq ft.", "image": None},
        {"claim_id": "c5", "verdict": "cant_check", "claimed": "rooftop deck", "found": "no data",
         "source": None, "reason": "Listing says “Rooftop deck”. We had no photos that could show this.",
         "image": None},
        {"claim_id": "c6", "verdict": "close", "claimed": "built in 2015", "found": "built in 2016",
         "source": "county", "reason": "County records say built in 2016, one year off.", "image": None,
         "confidence": {  # invented, in the judge's shape
             "score": 70, "band": "medium", "decided_by": "county",
             "why": "The county and CoStar both list 2016; a year off is often when building finished.",
             "sources": [{"source": "county", "says": "built in 2016", "weight": 0.7, "agrees_with_listing": False},
                         {"source": "record", "says": "built in 2016", "weight": 0.6, "agrees_with_listing": False}]}},
    ]
    for v in verdicts:
        say({"type": "verdict", "verdict": v})
    say({"type": "fill_in", "facts": _fake_facts(scan_path, aerial_path)})
    done = {"type": "done", "run_id": run_id,
            "counts": {"confirmed": 1, "contradicted": 3, "close": 1, "cant_check": 1},
            "headline": "Listed as 2 bedrooms. The 3D scan shows 1 bedroom and a den.",
            "report_url": f"/report/{run_id}"}
    say(done)
    return done
