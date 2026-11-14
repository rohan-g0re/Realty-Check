# CONTRACT — data shapes, tool signatures, events

Everything is a plain JSON-serializable dict. Names here are exact. Shared helpers live in `rc_common.py` (`private_dir`, `cache_dir`, `secret`, `RateLimit`, `evidence`, `cached_json`) and `harness/model/ask.py` (`ask_text`, `ask_json`, `ask_images`).

## 1. Flow
1. Extension sends a `PageSnapshot` to the local server: `POST /check`.
2. Server starts a run and streams `Event`s (server-sent events).
3. Harness: extract claims → match address to a property → gather evidence (record, scan, aerial, county) → judge each claim → "Fill me in" facts → finish.

## 2. Shapes

### 2.1 PageSnapshot (extension → server)
```json
{"url": "https://…", "title": "…", "address_text": "2111 Austin St, Houston, TX 77002",
 "text": "visible page text, at most 60000 chars", "jsonld": [ {"@type": "ApartmentComplex"} ]}
```

### 2.2 Claim
```json
{"id": "c1", "kind": "bedrooms", "value": 2, "unit": null, "scope": "unit",
 "label": "Plan E", "quote": "2 Beds", "checkable": true}
```
- `kind`: `bedrooms | bathrooms | area_sqft | stories | year_built | unit_count | amenity | parking | laundry | view | other`
- `value`: number for numeric kinds, string otherwise. `scope`: `unit | building`. `label`: floor plan or unit name when the claim is about one, else null.
- `quote`: the exact words on the page (at most 120 chars). `checkable`: false for marketing words with nothing to measure.

### 2.3 Match
```json
{"status": "matched", "confidence": 0.97, "property_id": "10122224", "name": "…",
 "address": "2111 Austin St, Houston, TX 77002", "city": "Houston", "state": "TX",
 "lat": 29.75, "lon": -95.37,
 "record": {"stories": 5, "year_built": 2016, "unit_count": 100, "building_sf": 123456},
 "floorplans": [{"name": "Plan E", "beds": 2, "baths": 2, "sqft": 1100, "matterport_model_id": "abc"}],
 "matterport_model_ids": ["abc"], "how": "street number + street name + zip", "kind": "building"}
```
- `status`: `matched | no_match`. On `no_match` only `status`, `confidence`, `how` are required; `lat`/`lon` may still be filled from geocoding.
- Missing record fields are `null`, never guessed.

### 2.4 Evidence (every tool returns one; build with `rc_common.evidence`)
```json
{"source": "aerial", "status": "ok", "summary": "Roof is 23.1 m above ground: about 6 floors.",
 "observed": {"height_m": 23.1, "stories": 6},
 "images": [{"path": "C:/…/rc_cache/aerial/x.jpg", "caption": "Oblique view from the north"}],
 "detail": "method, source name, URL without any key"}
```
- `source`: `record | scan | aerial | county`. `status`: `ok | no_evidence | error`.
- `observed` keys reuse Claim kinds where they apply: `bedrooms`, `bathrooms`, `area_sqft`, `stories`, `year_built`, `unit_count`; plus free extras (`height_m`, `room_labels`, …).
- `images[].path` is an absolute path under `rc_cache`. Never a signed URL. `detail` never contains a key or token.

### 2.5 Verdict
```json
{"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom + den",
 "source": "scan", "reason": "The 3D scan labels one bedroom and a den.", "image": null}
```
- `verdict`: `confirmed | contradicted | close | cant_check`. `image`: absolute cache path of the best proof image, or null.

### 2.6 Event (server → extension, one JSON object per SSE `data:` line)
| `type` | other fields |
|---|---|
| `run_started` | `run_id`, `address` |
| `step` | `agent` (`orchestrator`, `claims`, `matcher`, `inside`, `outside`, `paper`, `judge`), `text` (one plain sentence) |
| `claims` | `claims`: list of Claim |
| `match` | `match`: Match |
| `evidence` | `evidence`: Evidence |
| `verdict` | `verdict`: Verdict |
| `fill_in` | `facts`: list of Fact (2.7), in display order. Sent once, after the last `verdict` and before `done`; may be empty |
| `done` | `run_id`, `counts` `{confirmed, contradicted, close, cant_check}`, `headline` (one sentence), `report_url` |
| `error` | `message` |

The server rewrites every image path in outgoing events (evidence images, verdict `image`, fact `image`) to `/img/<token>` URLs it serves itself. The saved run keeps the `fill_in` event in `events` and its facts also at top level as `fill_in`; replays (`RC_REPLAY=1`) resend it, and the fake runner (`RC_FAKE=1`) sends invented facts.

### 2.7 Fact ("Fill me in": what our sources know that the listing does not say)
```json
{"id": "plan-b2", "topic": "Homes & floor plans", "label": "Plan B2",
 "value": "2 bed, 2 bath, 1,050–1,120 sq ft, 24 homes of this plan", "source": "record",
 "note": "A floor plan the listing does not show. From the building record, as of when it was last researched.",
 "image": null}
```
- Not a fact-check: nothing is right or wrong. Built by `harness.fill_in.build_fill_in(match, evidences, claims, kit=None, page_text="")` from the match, the run's Evidence and the kit's extra facts; no model call, no network call.
- `id`: stable within a run (`year-built`, `plan-b2`, `rent-<plan>-<id>`, `scan-rooms-<plan>`, `county-parcel`, …). `topic`: `Building | Homes & floor plans | Rent | Rules | Around it | Records`; facts come sorted by that order. `label`: short plain words. `value`: human-readable string. `source`: `record | scan | aerial | county`. `note`: "" or one plain sentence (record values say "as of when it was last researched"; asking rents say "as of 2026-11-12", the kit's listing date). `image`: cache path (an `/img/<token>` URL once sent) or null.
- Left out: anything the page already says (claim kinds and plan labels/sizes, plus keyword checks of the page text for rent, pets, parking, schools, management, availability, vacancy, lease terms, utilities, style, …); blank, 0 or unknown values (never stated as "none"); people's names, phones, emails; free text; complaint text.

### 2.8 Claim field (report only; `server.claim_field.claim_field(run)`)
```
{sources: [{key: record|scan|aerial|county, name, count}],
 claims:  [{id, kind, label, quote, claimed, verdict, verdict_words, found, reason, highlight,
            cells: {<source key>: {state: match|contra|close|data|none, verdict, found, reason,
                                   summary, summaries, facts, observed, raw, image, images, tour?}}}],
 counts}
```
A cell is the judge run on that one source's evidence alone. `claim_field_section(run)` returns the ready HTML section (`cf-` prefixed CSS and JS).

## 3. Tool signatures (plain Python functions)

| module | function | returns |
|---|---|---|
| `tools.kit` | `match_address(address: str, lat: float or None = None, lon: float or None = None) -> dict` | Match |
| `tools.kit` | `record_evidence(match: dict) -> dict` | Evidence, `source="record"` |
| `tools.kit` | `kit_facts(property_id: str) -> dict or None` | Extra kit facts of a building for "Fill me in" (class, rating, style, pets, parking, schools, management company, every floor plan, asking rent per plan as of the listing date), read from `rc_cache/kit/facts/<id>.json`; built offline once by `python -m tools.kit.facts`; None when not built. Never a network call |
| `tools.scan` | `scan_summary(model_id: str) -> dict` | Evidence, `source="scan"`: room labels, bedroom/bathroom counts from labels, floor area, floors |
| `tools.scan` | `scan_stills(model_id: str, max_n: int = 6) -> dict` | Evidence with `images` saved to cache |
| `tools.aerial` | `aerial_images(lat: float, lon: float, half_m: int = 60) -> dict` | Evidence with top-down and oblique `images` |
| `tools.aerial` | `building_height(lat: float, lon: float) -> dict` | Evidence: `observed.height_m`, `observed.stories` (estimate), method |
| `tools.paper` | `county_record(address: str, lat: float or None, lon: float or None, city: str or None) -> dict` | Evidence, `source="county"`: stories, year_built, unit_count, building_sf where the county publishes them |
| `tools.paper` | `geocode(address: str) -> dict or None` | `{"lat", "lon", ...}` for an address the kit did not match, else None |
| `tools.vision` | `look(image_paths: list[str], question: str, fields: dict[str, str]) -> dict` | `{"status": "ok" or "error", "answer": {field: value}, "confidence": 0-1, "notes": str}`; cached under `rc_cache/vision` |
| `tools.vision` | `count_floors(image_paths: list[str]) -> dict` | look() result, `answer`: `stories`, `how_counted`. Illustrative only: never decides a stories verdict |
| `tools.vision` | `read_floorplan(image_paths: list[str]) -> dict` | look() result, `answer`: `bedrooms`, `bathrooms`, `has_den`, `room_labels` |
| `tools.vision` | `check_room(image_paths: list[str], claim_text: str) -> dict` | look() result, `answer`: `room_type`, `has_window`, `supports_claim` |
| `tools.vision` | `find_features(image_paths: list[str], features: dict[str, str], setting: str = "aerial") -> dict` | look() result for several features in ONE call; `answer[key]` = `{"presence": "seen" or "not_seen" or "unclear", "confidence": 0-1, "image": 1-based index or null}`. `setting`: `aerial` or `scan`. Presence only, never a count |
| `harness.claims` | `extract_claims(snapshot: dict, use_model: bool = True) -> list[dict]` | list of Claim; `use_model=False` = rules and JSON-LD only |
| `harness.graph` | `judge(claim: dict, evidences: list[dict]) -> dict` | Verdict |
| `harness.graph` | `run_check(snapshot: dict, emit, toolbox=None) -> dict` | emits Events through `emit(event: dict)`; returns the `done` event. `toolbox=None` means `Toolbox.real()`; `Toolbox.fake()` is the synthetic one |

- Floor plans (`harness/graph/plans.py`), per listing plan label, first that applies:
  a. kit plan whose name equals the label, else starts with it at a word boundary, else contains it (case-insensitive): `"Plan E"` → `"Plan E 1 Bedroom + Den"`;
  b. no name match: kit plan with the listing's beds and baths and the nearest sq ft within 8% ("Matched to the building's plan A2s by size.");
  c. that kit plan has no scan id (or its scan's title gives a size more than 8% off the plan's): a property-level scan (`matterport_model_ids`) whose title has the plan's beds/baths and a sq ft within 3% (or names the plan). At most 8 uncached `scan_summary` fetches per run; two equal fits = no pick;
  d. otherwise the kit plan's own numbers as `source="record"` evidence; no kit plan at all = `cant_check`, "This floor plan is not in the building's records."
  - no label at all (unit claims that name no plan): the kit's full plan list as one `source="record"` Evidence, `observed.floorplans` = `[{name, beds, baths, sqft}]`, so the judge can confirm "some plan has this"; a property scan is used only when the building has exactly one.
- Plan label field: every floor plan Evidence (scan summary, scan stills, and the record's floor plan numbers) carries the LISTING's plan label in `observed.plan`; group pictures by it. Record floor plan Evidence also has `observed.kit_plan` (the kit name) and, when matched by size or via another scan, `observed.plan_match` (one plain sentence the judge repeats). A listing plan with no kit plan gets `status="no_evidence"` record Evidence with `observed.plan_missing: true`.
- Picture checks (amenity / parking / laundry / view / other claims): Evidence with `observed.feature_check: true`, `claim_ids` (claims it answers), `feature` (key such as `pool`, `washer_dryer`), `presence` (`seen | not_seen | unclear | skipped`), `presence_confidence`, `looked_at`, and for scans `model_id`, `scan_title`. A property-level scan whose title names the amenity ("Fitness Center") gives `presence: "seen"`, `how: "scan_title"`. These are never proof images for floor plan claims.
- Stories: decided only by measured roof height (aerial evidence with `observed.height_m`) and paper records. A vision floor count is stored as `observed.photo_floor_count` with `observed.illustrative: true` and is ignored by the judge.

Each module re-exports its functions from its package `__init__.py` (e.g. `from tools.kit import match_address`).

## 4. Judge rules (deterministic first, model only for wording)
- Count kinds (`bedrooms`, `bathrooms`, `stories`, `unit_count`, `year_built`): equal → `confirmed`; different → `contradicted`, except near misses: year built within 1 year, or unit count within 2% (at least 1 unit), → `close` ("County records say 216 apartments, which is close.").
- Bed/bath counts read from a scan's room labels (not its title; also a photo reading) are not trusted alone. They may contradict the listing only when the scan's own title or the building record for that plan gives the same count. When the title or record agrees with the listing and only the labels differ → `confirmed` (the title or record is the source, and the reason says the labels were not relied on). Labels the only evidence and different → `cant_check` ("room labels alone are not reliable"). A label count of 0 bathrooms is never used.
- Unit claims (`bedrooms`, `bathrooms`, `area_sqft`) with no plan label ("1 Bed", "645 sq ft"): evidence about one particular plan or one scan may only confirm them, never make them `close` or `contradicted`. `confirmed` when some plan in the building record (the per-plan record Evidence, or the full plan list a run adds as record Evidence with `observed.floorplans`) or some scan title has those numbers ("Listing says 1 bedroom without naming a floor plan; the building record's plan A has 1 bedroom."); otherwise `cant_check`. Room labels never confirm such a claim. Building-wide evidence (county, building record without a plan) is still judged the normal way.
- A labelled unit claim ignores scan evidence that carries no `observed.plan`.
- `area_sqft` against the record (kit) or county: within 5% → `confirmed`; 5–10% → `close`; beyond 10% → `contradicted`.
- `area_sqft` against a scan whose title states a size (`observed.model_name` such as "1BR W Den 1BA - 855 SF"): the title's sq ft is compared with the same 5% / 10% rule; `found` is "855 sq ft (scan title)", reason "The 3D scan of this plan is titled '1BR W Den 1BA - 855 SF', which gives 855 sq ft."
- `area_sqft` against a scan's measured floor area (no size in the title): a scan measures interior floor and runs a median 11% under the advertised size (89 floor plans), so the band is one-sided: up to 12% smaller or 5% larger → `confirmed`; 12–20% smaller or 5–10% larger → `close`; beyond → `contradicted`. Reason: "The 3D scan measures 640 sq ft of floor, 10% smaller; scans usually measure about a tenth less than the advertised size."
- No evidence with `status="ok"` that observes the claim's kind → `cant_check`.
- When sources disagree, priority is scan and aerial (measured), then county, then record. Name the winning source in `source`.
- Stories from aerial: reason reads "The measured roof height (23.1 m) means about 6 floors."
- Floor plan numbers from the record: "According to the building record, its plan A2s has 750 sq ft." plus `observed.plan_match` when present.
- Amenity, parking, laundry, view and other claims (soft, presence only): seen with confidence ≥ 0.7 → `confirmed` ("Visible in the aerial photo" / "Visible in the 3D scan photos", that image as proof), or a scan title match → `confirmed` ("3D scan titled 'Fitness Center'"); not seen with confidence ≥ 0.8 → `close` ("Not visible in the aerial photo"; a photo cannot prove absence), never `contradicted`; otherwise `cant_check` ("The photos don't show this clearly"); things no picture shows (24-hour access, pet wash, concierge, co-working, utilities) → `cant_check`, "Nothing in our data measures this", with no model call; `checkable=false` → marketing phrase.

## 5. Server
- `POST /check` body PageSnapshot → `text/event-stream` of Events. `GET /health` → `{"ok": true}`.
- `GET /img/<token>` serves a cached image. `GET /report/<run_id>` returns the full report page; `GET /runs/<run_id>.json` the saved events.
- `RC_REPLAY=1`: a snapshot whose address was run before replays its saved events with short delays instead of re-running.
- Binds `127.0.0.1` only, port `RC_PORT` (default `8787`). CORS allows extension origins.
- `RC_FAKE=1` uses the canned fake runner instead of the harness.

- `GET /report/<run_id>` is design A (`server/report_a.py`); `?v=old` gives the earlier tabbed page. Anchors the drawer uses: `#claim-<claim_id>` (mismatches only), `#mismatch`, `#match`, `#cant-verify`, `#extra`. `GET /tour/<run_id>?claim=<claim_id>` opens the 3D walk for one finding.

## 6. Environment
- `RC_PRIVATE_DIR` = private driver folder. Keys through `rc_common.secret(NAME)`: `MATTERPORT_API_KEY`, `MATTERPORT_API_TOKEN`, `VEXCEL_API_KEY`, `COSTAR_ZIP_PASSWORD`, `COSTAR_KIT_ZIP`.
- Cache root: `rc_common.cache_dir("<module>")`.
