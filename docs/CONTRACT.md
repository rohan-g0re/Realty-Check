# CONTRACT — data shapes, tool signatures, events

Everything is a plain JSON-serializable dict. Names here are exact. Shared helpers live in `rc_common.py` (`private_dir`, `cache_dir`, `secret`, `RateLimit`, `evidence`, `cached_json`) and `harness/model/ask.py` (`ask_text`, `ask_json`, `ask_images`).

## 1. Flow
1. Extension sends a `PageSnapshot` to the local server: `POST /check`.
2. Server starts a run and streams `Event`s (server-sent events).
3. Harness: extract claims → match address to a property → gather evidence (record, scan, aerial, county) → judge each claim → finish.

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
 "matterport_model_ids": ["abc"], "how": "street number + street name + zip"}
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
| `done` | `run_id`, `counts` `{confirmed, contradicted, close, cant_check}`, `headline` (one sentence), `report_url` |
| `error` | `message` |

The server rewrites every image path in outgoing events to `/img/<token>` URLs it serves itself.

## 3. Tool signatures (plain Python functions)

| module | function | returns |
|---|---|---|
| `tools.kit` | `match_address(address: str, lat: float or None = None, lon: float or None = None) -> dict` | Match |
| `tools.kit` | `record_evidence(match: dict) -> dict` | Evidence, `source="record"` |
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
| `harness.claims` | `extract_claims(snapshot: dict, use_model: bool = True) -> list[dict]` | list of Claim; `use_model=False` = rules and JSON-LD only |
| `harness.graph` | `judge(claim: dict, evidences: list[dict]) -> dict` | Verdict |
| `harness.graph` | `run_check(snapshot: dict, emit, toolbox=None) -> dict` | emits Events through `emit(event: dict)`; returns the `done` event. `toolbox=None` means `Toolbox.real()`; `Toolbox.fake()` is the synthetic one |

- Floor plans: a claim label resolves to the kit floor plan whose name equals it, else starts with it at a word boundary (case-insensitive), else contains it: `"Plan E"` → `"Plan E 1 Bedroom + Den"`. Scan evidence is tagged with the claim's label in `observed.plan`.
- Stories: decided only by measured roof height (aerial evidence with `observed.height_m`) and paper records. A vision floor count is stored as `observed.photo_floor_count` with `observed.illustrative: true` and is ignored by the judge.

Each module re-exports its functions from its package `__init__.py` (e.g. `from tools.kit import match_address`).

## 4. Judge rules (deterministic first, model only for wording)
- Count kinds (`bedrooms`, `bathrooms`, `stories`, `unit_count`, `year_built`): equal → `confirmed`; different → `contradicted`.
- `area_sqft`: within 5% → `confirmed`; 5–10% → `close`; beyond 10% → `contradicted`.
- No evidence with `status="ok"` that observes the claim's kind → `cant_check`.
- When sources disagree, priority is scan and aerial (measured), then county, then record. Name the winning source in `source`.

## 5. Server
- `POST /check` body PageSnapshot → `text/event-stream` of Events. `GET /health` → `{"ok": true}`.
- `GET /img/<token>` serves a cached image. `GET /report/<run_id>` returns the full report page; `GET /runs/<run_id>.json` the saved events.
- `RC_REPLAY=1`: a snapshot whose address was run before replays its saved events with short delays instead of re-running.
- Binds `127.0.0.1` only, port `RC_PORT` (default `8787`). CORS allows extension origins.
- `RC_FAKE=1` uses the canned fake runner instead of the harness.

## 6. Environment
- `RC_PRIVATE_DIR` = private driver folder. Keys through `rc_common.secret(NAME)`: `MATTERPORT_API_KEY`, `MATTERPORT_API_TOKEN`, `VEXCEL_API_KEY`, `COSTAR_ZIP_PASSWORD`, `COSTAR_KIT_ZIP`.
- Cache root: `rc_common.cache_dir("<module>")`.
