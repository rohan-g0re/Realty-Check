// Built-in sample run (invented). Same Event shapes as docs/CONTRACT.md section 2.6.
// Used when the local checker is not running: the content script plays these events in the drawer, and
// server/sample_report.py renders them into report/report.html (the full report, design A).
// Keep the block between the RC_SAMPLE_EVENTS markers plain JSON: Python reads it too. "{{address}}" stands
// for the address of the page the sample runs on.
globalThis.RC_SAMPLE = (() => {
  const EVENTS = /* RC_SAMPLE_EVENTS */ [
    {"type": "run_started", "run_id": "sample", "address": "{{address}}"},
    {"type": "step", "agent": "orchestrator", "text": "Reading the listing…"},
    {"type": "claims", "claims": [
      {"id": "c1", "kind": "bedrooms", "value": 2, "unit": null, "scope": "unit", "label": "Plan E", "quote": "2 Beds", "checkable": true},
      {"id": "c2", "kind": "stories", "value": 5, "unit": null, "scope": "building", "label": null, "quote": "5-story building", "checkable": true},
      {"id": "c3", "kind": "area_sqft", "value": 1100, "unit": "sqft", "scope": "unit", "label": "Plan E", "quote": "1,100 sq ft", "checkable": true},
      {"id": "c4", "kind": "year_built", "value": 2016, "unit": null, "scope": "building", "label": null, "quote": "Built in 2016", "checkable": true},
      {"id": "c5", "kind": "amenity", "value": "resort-style pool", "unit": null, "scope": "building", "label": null, "quote": "Resort-style pool", "checkable": true}
    ]},
    {"type": "step", "agent": "matcher", "text": "Finding the building record…"},
    {"type": "match", "match": {"status": "matched", "confidence": 0.95, "address": "{{address}}", "how": "sample"}},
    {"type": "evidence", "evidence": {"source": "record", "status": "ok", "summary": "The building record lists 5 stories, built in 2016.", "observed": {"stories": 5, "year_built": 2016, "unit_count": 100}, "images": [], "detail": "sample"}},
    {"type": "evidence", "evidence": {"source": "record", "status": "ok", "summary": "The building record's Plan E: 2 bed, 1,100 sq ft.", "observed": {"plan": "Plan E", "kit_plan": "Plan E", "bedrooms": 2, "bathrooms": 2, "area_sqft": 1100}, "images": [], "detail": "sample"}},
    {"type": "step", "agent": "inside", "text": "Walking through the 3D scan of Plan E…"},
    {"type": "evidence", "evidence": {"source": "scan", "status": "ok", "summary": "The 3D scan of Plan E shows one bedroom and a den, 850 sq ft.", "observed": {"plan": "Plan E", "model_name": "Plan E - 1 Bed + Den - 850 SF", "counts_from": "model_name", "bedrooms": 1, "area_sqft": 850, "room_labels": ["Bedroom", "Den", "Living", "Kitchen"]}, "images": [], "detail": "sample"}},
    {"type": "verdict", "verdict": {"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom and a den", "source": "scan", "reason": "The 3D scan of Plan E shows one bedroom and a den, not two bedrooms.", "image": null}},
    {"type": "verdict", "verdict": {"claim_id": "c3", "verdict": "contradicted", "claimed": "1,100 sq ft", "found": "850 sq ft", "source": "scan", "reason": "The 3D scan of Plan E is titled 850 sq ft, 23% less than listed.", "image": null}},
    {"type": "step", "agent": "outside", "text": "Measuring the roof from the aerial photo…"},
    {"type": "evidence", "evidence": {"source": "aerial", "status": "ok", "summary": "Roof is 23.1 m above ground: about 6 floors.", "observed": {"height_m": 23.1, "stories": 6, "stories_range": [6, 7], "capture_date": "2026-01-02"}, "images": [], "detail": "sample"}},
    {"type": "evidence", "evidence": {"source": "aerial", "status": "ok", "summary": "Parking seen in the aerial photo.", "observed": {"feature_check": true, "claim_ids": [], "feature": "parking", "presence": "seen", "presence_confidence": 0.9, "looked_at": "aerial photos"}, "images": [], "detail": "sample"}},
    {"type": "verdict", "verdict": {"claim_id": "c2", "verdict": "contradicted", "claimed": "5 stories", "found": "6 floors", "source": "aerial", "reason": "The roof height in the aerial photo matches a 6-floor building.", "image": null}},
    {"type": "step", "agent": "paper", "text": "Reading the county record…"},
    {"type": "evidence", "evidence": {"source": "county", "status": "ok", "summary": "The county record lists 2016 as the year built.", "observed": {"year_built": 2016, "building_sf": 112000, "unit_count": 100, "land_use": "Apartments", "owner_type": "company", "complaints_311": 4, "source_name": "Example County Appraisal District"}, "images": [], "detail": "sample"}},
    {"type": "verdict", "verdict": {"claim_id": "c4", "verdict": "confirmed", "claimed": "built in 2016", "found": "built in 2016", "source": "county", "reason": "The county record lists 2016 as the year built.", "image": null}},
    {"type": "step", "agent": "judge", "text": "Writing up the results…"},
    {"type": "verdict", "verdict": {"claim_id": "c5", "verdict": "cant_check", "claimed": "resort-style pool", "found": "no source covers this", "source": "record", "reason": "None of our sources show the pool, so we can't check it.", "image": null}},
    {"type": "fill_in", "facts": [
      {"id": "plan-a", "topic": "Homes & floor plans", "label": "Plan A", "value": "1 bed, 1 bath, 640–690 sq ft", "source": "record", "note": "From the building record, as of when it was last researched.", "image": null},
      {"id": "size-1bed", "topic": "Homes & floor plans", "label": "Typical size, 1 bed", "value": "640–760 sq ft across 3 plans", "source": "record", "note": "From the building record, as of when it was last researched.", "image": null},
      {"id": "manager", "topic": "Building", "label": "Managed by", "value": "Example Property Management", "source": "record", "note": "", "image": null},
      {"id": "county-311", "topic": "Records", "label": "311 service requests on record", "value": "4", "source": "county", "note": "A count from the county; what they say is not shown.", "image": null},
      {"id": "lot", "topic": "Records", "label": "Lot size", "value": "0.9 acres", "source": "county", "note": "From the county parcel record.", "image": null}
    ]},
    {"type": "done", "run_id": "sample", "counts": {"confirmed": 1, "contradicted": 3, "close": 0, "cant_check": 1}, "headline": "Listed as 2 bedrooms. The 3D scan shows 1 bedroom and a den.", "report_url": "/report/sample"}
  ] /* /RC_SAMPLE_EVENTS */;
  const events = (address) => JSON.parse(JSON.stringify(EVENTS), (k, v) => (v === "{{address}}" ? address : v));
  return { events, delayMs: 650 };
})();
