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
