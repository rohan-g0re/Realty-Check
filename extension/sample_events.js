// Built-in sample run (synthetic). Same Event shapes as docs/CONTRACT.md section 2.6.
// Used when the local checker is not running, by the content script and by report/report.html.
globalThis.RC_SAMPLE = (() => {
  const claims = [
    { id: "c1", kind: "bedrooms", value: 2, unit: null, scope: "unit", label: "Plan E", quote: "2 Beds", checkable: true },
    { id: "c2", kind: "stories", value: 5, unit: null, scope: "building", label: null, quote: "5-story building", checkable: true },
    { id: "c3", kind: "area_sqft", value: 1100, unit: "sqft", scope: "unit", label: "Plan E", quote: "1,100 sq ft", checkable: true },
    { id: "c4", kind: "year_built", value: 2016, unit: null, scope: "building", label: null, quote: "Built in 2016", checkable: true },
    { id: "c5", kind: "amenity", value: "resort-style pool", unit: null, scope: "building", label: null, quote: "Resort-style pool", checkable: true },
  ];
  const v = (claim_id, verdict, claimed, found, source, reason) => ({ claim_id, verdict, claimed, found, source, reason, image: null });
  const events = (address) => [
    { type: "run_started", run_id: "sample", address },
    { type: "step", agent: "orchestrator", text: "Reading the listing…" },
    { type: "claims", claims },
    { type: "step", agent: "matcher", text: "Finding the building record…" },
    { type: "match", match: { status: "matched", confidence: 0.95, address, how: "sample" } },
    { type: "step", agent: "inside", text: "Checking the 3D scan…" },
    { type: "verdict", verdict: v("c1", "contradicted", "2 bedrooms", "1 bedroom and a den", "scan", "The 3D scan labels one bedroom and a den, not two bedrooms.") },
    { type: "verdict", verdict: v("c3", "contradicted", "1,100 sq ft", "968 sq ft", "scan", "The 3D scan measures 968 sq ft, 12% less than listed.") },
    { type: "step", agent: "outside", text: "Checking the aerial photo…" },
    { type: "verdict", verdict: v("c2", "contradicted", "5 stories", "6 floors", "aerial", "The roof height in the aerial photo matches a 6-floor building.") },
    { type: "step", agent: "paper", text: "Checking the county record…" },
    { type: "verdict", verdict: v("c4", "confirmed", "built in 2016", "built in 2016", "county", "The county record lists 2016 as the year built.") },
    { type: "step", agent: "judge", text: "Writing up the results…" },
    { type: "verdict", verdict: v("c5", "cant_check", "resort-style pool", "no source covers this", "record", "None of our sources show the pool, so we can't check it.") },
    { type: "fill_in", facts: [
      { id: "plan-a", topic: "Homes & floor plans", label: "Plan A", value: "1 bed, 1 bath, 640–690 sq ft", source: "record", note: "From the building record, as of when it was last researched.", image: null },
      { id: "pets", topic: "Rules", label: "Pets", value: "Cats and small dogs allowed, up to 2", source: "record", note: "", image: null },
      { id: "manager", topic: "Building", label: "Managed by", value: "Example Property Management", source: "record", note: "", image: null },
      { id: "lot", topic: "Records", label: "Lot size", value: "0.9 acres", source: "county", note: "From the county parcel record.", image: null },
      { id: "roof", topic: "Around it", label: "Rooftop", value: "Shared roof deck on the top floor", source: "aerial", note: "Seen from above; not confirmed on site.", image: null },
    ] },
    { type: "done", run_id: "sample", counts: { confirmed: 1, contradicted: 3, close: 0, cant_check: 1 },
      headline: "Listed as 2 bedrooms. The 3D scan shows 1 bedroom and a den.", report_url: "/report/sample" },
  ];
  return { events, delayMs: 650 };
})();
