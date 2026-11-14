# Decisions

- 2026-11-14: harness model = Claude Code CLI headless Sonnet via own adapter (probe: 2/2 runs, 12–15 s). No API keys.
- 2026-11-14: UI = in-page edge tab + push-left drawer (Simplify pattern), not Chrome side panel (cannot be opened by automation).
- 2026-11-14: page reader is site-agnostic: regex + JSON-LD + one model call; no per-site selectors.
- 2026-11-14: single working tree, disjoint folder ownership, orchestrator commits.

## Wiring (2026-11-14)
- Floors are decided by measured roof height (aerial `height_m`) and county/building records only. The vision model miscounted the real demo building (7 vs 6), so a photo floor count is stored as `photo_floor_count` with `illustrative: true`, the judge ignores it, and it is not run at all once a roof height exists.
- Claim labels resolve to kit floor plans by exact, then word-boundary prefix, then substring match ("Plan E" → "Plan E 1 Bedroom + Den"). Whatever plan name the agent passes is mapped back to the claim label, so scan evidence and claims share one plan tag.
- "+ den" in the bedrooms verdict also comes from the scan model's title (e.g. "1BR W Den").
- Time budget: run target 55 s, agent budget 35 s; vision calls are skipped when under 15 s remain. Vision is not run on scan photos when the scan already gave room counts; scan stills are still fetched (cached) as proof images, after the agent too.
- Server: `RC_PORT` overrides the port (default 8787). Replays are capped at about 6 s and rewrite `report_url` to the serving server.
- Demo snapshots are invented listing text plus the public street address, in `tests/fixtures/public/`; `python -m server.demo_post <name>` posts one.

## Wider checks (2026-11-14)
- Floor plans: name match, else beds + baths + nearest size within 8% against the kit's plans; a kit plan without a scan borrows a property-level scan only when its title fits (beds, baths, title sq ft within 3%); otherwise the kit's own plan numbers are compared as `source="record"`, said in the reason ("according to the building record", "matched to the building's plan A2s by size"). A listing plan matching no kit plan is "not in the building's records". A kit plan's own scan whose title gives a size >8% off is not used (LA Plan F's scan is titled 1060 SF).
- Amenities are soft picture checks, presence only: one batched vision call on the aerial photos (top-down + 2 angled) for all outdoor amenities, one per scan (max 2 scans, room panoramas only, no schematic) for in-unit features. Seen ≥0.7 → confirmed; not seen ≥0.8 → close (a photo cannot prove absence), never contradicted; else can't check. No model call for things no picture shows. Answers cached by images + question (`rc_cache/vision`).
- Quartz and granite are asked separately: the model saw granite in the Houston B2 scan, and "stone counters" would have confirmed a quartz claim.
- A property-level scan titled after an amenity room (Fitness Center, Sky Lounge) confirms that amenity by name; when the aerial photo did not show a rooftop, the reason says it may be indoors.
- Picture checks start right after the address match in a background thread, in parallel with the agent; whatever is unfinished one and a half seconds before the 60 s budget is "not checked". Agent tools only report their status, so they never block the agent.
- After the agent, the fixed plan always runs as a completion pass (already-run checks are not repeated), so every claim gets its best verdict even if the planner skipped it. The inside checker now gets every floor plan in one task.
- Stories reason: "The measured roof height (23.1 m) means about 6 floors."
- Claim cap raised from 25 to 40 (the fixture pages have ~28); the claim model's parsed reply is cached by prompt hash under `rc_cache/claims`.
- LA aerial (3700 Sepulveda): roof 11.7 m → about 3 floors (kit record also 3); top-down + 2 angled photos available.
- Scan area vs listed size: a precheck over 89 floor plans found the scan's measured floor a median 11% smaller than the advertised size, so the old 5% / 10% rule gave false contradictions. A size in the scan's own title ("… - 855 SF") is now compared with the 5% / 10% rule; otherwise measured area is confirmed from 20% smaller to 5% larger, close to 30% smaller or 10% larger, contradicted beyond. Record and county sizes keep 5% / 10%. The fake scenario's 968 vs 1,100 sq ft is now confirmed.
