"""Tour page (GET /tour/<run_id>?claim=<id>): invented run, isolated private folder under tmp_path.
Spot picking uses a fake Matterport client and a fake picture check: no network, no model calls."""
import json
import re
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server.app as srv
from server import tour
from tools.scan import spots

RUN_ID = "20990101-000000-abc123"
MODEL = "TestModel123"
FAKE_KEY = "fake-sdk-key-for-tests-only"


def _run():
    claims = [
        {"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan Z",
         "quote": "2 Beds", "checkable": True},
        {"id": "c2", "kind": "bathrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan Z",
         "quote": "2 Baths", "checkable": True},
        {"id": "c3", "kind": "stories", "value": 9, "unit": None, "scope": "building", "label": None,
         "quote": "9 floors", "checkable": True},
    ]
    scan = {"source": "scan", "status": "ok", "summary": "s", "images": [], "detail": "",
            "observed": {"model_id": MODEL, "model_name": "1BR W Den 1BA - 700 SF", "bedrooms": 1, "plan": "Plan Z"}}
    aerial = {"source": "aerial", "status": "ok", "summary": "a", "images": [], "detail": "",
              "observed": {"height_m": 20.0, "stories": 6}}
    verdicts = [
        {"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom + den",
         "source": "scan", "reason": "Listing says 2 bedrooms. The 3D scan shows 1 bedroom + den.", "image": None},
        {"claim_id": "c2", "verdict": "contradicted", "claimed": "2 bathrooms", "found": "1 bathroom",
         "source": "scan", "reason": "Listing says 2 bathrooms. The 3D scan shows 1 bathroom.", "image": None},
        {"claim_id": "c3", "verdict": "contradicted", "claimed": "9 floors", "found": "6 floors",
         "source": "aerial", "reason": "The measured roof height (20.0 m) means about 6 floors.", "image": None},
    ]
    events = [{"type": "run_started", "run_id": RUN_ID, "address": "1 Invented Way, Nowhere"},
              {"type": "claims", "claims": claims},
              {"type": "evidence", "evidence": scan}, {"type": "evidence", "evidence": aerial}]
    events += [{"type": "verdict", "verdict": v} for v in verdicts]
    events.append({"type": "done", "run_id": RUN_ID, "headline": "3 of 3 claims did not hold up.",
                   "counts": {"confirmed": 0, "contradicted": 3, "close": 0, "cant_check": 0}})
    return {"run_id": RUN_ID, "harness": "fake", "snapshot": {"address_text": "1 Invented Way, Nowhere"},
            "address_key": "1 invented way nowhere", "started_at": 1.0, "finished_at": 2.0, "finished": True,
            "events": events, "event_times": [], "images": {}}


def _summary(tmp_path, rooms):
    d = tmp_path / "rc_cache" / "scan" / MODEL
    d.mkdir(parents=True, exist_ok=True)
    panos = [{"id": f"pano{i:04d}abcd", "label": str(i), "anchor": {"room": {"id": r["id"]}}} for i, r in enumerate(rooms)]
    (d / "summary.json").write_text(json.dumps({"data": {"model": {"id": MODEL, "rooms": rooms,
                                                                   "panoLocations": panos}}}), encoding="utf-8")


class FakeClient:
    """Stands in for tools.scan.matterport: invented spots whose face URLs look signed."""

    def __init__(self):
        self.queries = self.downloads = 0
        self.spot_ids = [f"spot{i:04d}abcd" for i in range(6)]

    def spot_query(self, model_id):
        self.queries += 1
        url = "https://signed.invalid/face.jpg?Signature=abc123&Expires=1"
        rooms = [{"id": "liv", "tags": ["living"]}, {"id": "nook", "tags": ["unfurnished"]}]
        panos = [{"id": sid, "label": str(i), "position": {"x": float(i), "y": float(i % 2), "z": 1.4},
                  "anchor": {"room": {"id": "nook" if i == 2 else "liv"}}, "skybox": {"children": [url] * 6}}
                 for i, sid in enumerate(self.spot_ids)]
        return {"id": model_id, "name": "invented", "rooms": rooms, "panoLocations": panos}

    def download_image(self, url):
        self.downloads += 1
        return Image.new("RGB", (64, 64), (200, 190, 180))


class FakeLook:
    def __init__(self):
        self.calls, self.answer, self.confidence, self.images, self.question = 0, None, 0.9, [], ""

    def __call__(self, paths, question, fields):
        self.calls += 1
        self.images, self.question = list(paths), question
        assert set(fields) == {"spot", "view", "seen"}
        return {"status": "ok", "answer": dict(self.answer or {}), "confidence": self.confidence, "notes": ""}


FAKE_CLIENT, FAKE_LOOK = FakeClient(), FakeLook()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    global FAKE_CLIENT, FAKE_LOOK
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("MATTERPORT_SDK_KEY", raising=False)
    (tmp_path / ".env").write_text("MATTERPORT_API_KEY=other-secret-value-xyz\n", encoding="utf-8")
    srv.save_run(_run())
    FAKE_CLIENT, FAKE_LOOK = FakeClient(), FakeLook()
    monkeypatch.setattr(spots, "_default_client", lambda: FAKE_CLIENT)
    monkeypatch.setattr(spots, "_default_look", lambda: FAKE_LOOK)
    tour.reset_spots()
    yield
    tour.reset_spots()


def _add_claim(claim, verdict):
    run = _run()
    run["events"][1]["claims"].append(claim)
    run["events"].insert(-1, {"type": "verdict", "verdict": verdict})
    srv.save_run(run)


@pytest.fixture
def client():
    return TestClient(srv.app)


def _warm(client, url):
    """The viewer never waits for a pick: ask once (starts it), let it finish, ask again (as a later visit would)."""
    client.get(url)
    for th in list(tour._JOBS.values()):
        th.join(5)
    return client.get(url).text


def _cfg(html):
    start = html.index("window.TOUR_CFG=") + len("window.TOUR_CFG=")
    return json.loads(html[start:html.index(";</script>", start)])


def test_tour_served_with_key(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    r = client.get(f"/tour/{RUN_ID}?claim=c1")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    html = r.text
    assert "Back to the report" in html and f"/report/{RUN_ID}" in html
    assert "<span class=strike>2 bedrooms</span>" in html and "1 bedroom + den" in html and "Plan Z" in html
    cfg = _cfg(html)
    assert cfg["model"] == MODEL and cfg["key"] == FAKE_KEY
    assert cfg["tag"]["label"] == "2 bedrooms"
    assert "other-secret-value-xyz" not in html


def test_tour_key_from_private_env(client, tmp_path):
    (tmp_path / ".env").write_text(f"MATTERPORT_SDK_KEY={FAKE_KEY}\n", encoding="utf-8")
    assert _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c1"))["key"] == FAKE_KEY


def test_tour_key_absent(client):
    r = client.get(f"/tour/{RUN_ID}?claim=c1")
    assert r.status_code == 200
    html = r.text
    assert "3D view not available" in html and "Back to the report" in html
    for bad in ("applicationKey", "TOUR_CFG", "other-secret-value-xyz", MODEL, "<iframe"):
        assert bad not in html


def test_tour_not_found(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    assert client.get(f"/tour/{RUN_ID}?claim=c3").status_code == 404  # aerial claim: no scan
    assert client.get(f"/tour/{RUN_ID}?claim=c99").status_code == 404
    assert client.get(f"/tour/{RUN_ID}?claim=%3Cscript%3E").status_code == 404
    r = client.get("/tour/20990101-000000-nope00?claim=c1")
    assert r.status_code == 404 and FAKE_KEY not in r.text


def test_tour_default_claim_is_first_scan_mismatch(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    assert "<span class=strike>2 bedrooms</span>" in client.get(f"/tour/{RUN_ID}").text


def test_room_picked_only_when_one_room_carries_the_word(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _summary(tmp_path, [{"id": "roomA", "label": None, "tags": ["bathroom"], "classifications": []},
                        {"id": "roomB", "label": None, "tags": ["unfurnished"], "classifications": []},
                        {"id": "roomC", "label": None, "tags": ["bedroom"], "classifications": []}])
    html = _warm(client, f"/tour/{RUN_ID}?claim=c2")
    bath = _cfg(html)["spot"]
    assert bath["status"] == "found" and bath["sweeps"] == ["pano0000abcd"] and bath["sweep"] is None
    assert "only room the scan tags as a bathroom" in html
    assert FAKE_CLIENT.queries == 0 and FAKE_LOOK.calls == 0  # the run's saved room tags settled it


def test_room_not_picked_when_ambiguous_or_shared(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "none", "view": None, "seen": None}
    _summary(tmp_path, [{"id": "roomA", "tags": ["bathroom"]}, {"id": "roomB", "tags": ["bathroom"]}])
    spot = _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c2"))["spot"]
    assert spot["sweep"] is None and spot["sweeps"] == []  # two bathrooms: picture check said none
    assert FAKE_LOOK.calls == 1


# ---------- spot picking (tools/scan/spots.py) with a fake Matterport client and a fake picture check ----------

def test_den_spot_chosen_by_picture_check(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "3", "view": "B", "seen": "Small room off the living room, no closet, no door"}
    html = _warm(client, f"/tour/{RUN_ID}?claim=c1")
    spot = _cfg(html)["spot"]
    assert spot["status"] == "found" and spot["sweep"] == FAKE_CLIENT.spot_ids[2] and spot["face"] == 1
    # the callout at the marker says what is there; the card says how the spot was picked
    assert "<p class=meta>This spot</p>Small room off the living room, no closet, no door.</div>" in html
    assert "Picked by a picture check of 6 spots in the scan." in html
    cfg = _cfg(html)
    assert cfg["tag"]["callout"].startswith("Small room") and cfg["pull"] < 1
    assert FAKE_LOOK.calls == 1 and len(FAKE_LOOK.images) <= 5  # two spots per sheet, at most 10 spots
    assert "spots are numbered 1 to" in FAKE_LOOK.question and "den" in FAKE_LOOK.question


def test_none_keeps_start_view_and_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "none", "view": None, "seen": None}
    html = _warm(client, f"/tour/{RUN_ID}?claim=c1")
    spot = _cfg(html)["spot"]
    assert spot["status"] == "none" and spot["sweep"] is None and spot["sweeps"] == []
    # the verdict says the scan shows a den, so the card never says no spot shows one
    card = html[html.index("<aside class=card"):html.index("</aside>")]
    assert "none clearly shows" not in card and "No single spot was picked" in card and "start position" in card


def test_low_confidence_or_bad_spot_is_none(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "99", "view": "A", "seen": "a room"}
    assert _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c1"))["spot"]["sweep"] is None
    (tmp_path / "rc_cache" / "spots" / MODEL / "bedrooms_2.json").unlink()
    tour.reset_spots()
    FAKE_LOOK.answer, FAKE_LOOK.confidence = {"spot": "2", "view": "A", "seen": "maybe a den"}, 0.3
    assert _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c1"))["spot"]["sweep"] is None


def test_pick_cached_and_reused(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "1", "view": "C", "seen": "Small nook by the kitchen"}
    first = _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c1"))["spot"]
    assert (tmp_path / "rc_cache" / "spots" / MODEL / "bedrooms_2.json").is_file()
    q, d = FAKE_CLIENT.queries, FAKE_CLIENT.downloads
    tour.reset_spots()  # as after a server restart: only the disk cache is left
    again = _cfg(_warm(client, f"/tour/{RUN_ID}?claim=c1"))["spot"]
    assert again == first and FAKE_LOOK.calls == 1
    assert (FAKE_CLIENT.queries, FAKE_CLIENT.downloads) == (q, d)


def test_no_signed_url_written_to_cache(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    FAKE_LOOK.answer = {"spot": "2", "view": "D", "seen": "Small room with a window"}
    html = _warm(client, f"/tour/{RUN_ID}?claim=c1")
    assert _cfg(html)["spot"]["status"] == "found"
    files = [f for f in (tmp_path / "rc_cache" / "spots").rglob("*") if f.is_file()]
    assert any(f.suffix == ".json" for f in files) and any(f.suffix == ".jpg" for f in files)
    for f in files:
        data = f.read_bytes()
        assert b"signed.invalid" not in data and b"Signature=" not in data and b"http" not in data.lower()
    assert "signed.invalid" not in html and "Signature=" not in html


def test_viewer_never_waits_for_a_pick(client, monkeypatch):
    """A pick still running (or never started) opens the scan's start view at once, no waiting page, no reload."""
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    gate = threading.Event()

    def slow(model_id, claim, verdict):
        gate.wait(5)
        return {"status": "found", "how": "geometry", "sweep": None, "sweeps": ["pano0000abcd"], "why": "Only one."}
    monkeypatch.setattr(tour, "PICKER", slow)
    for url in (f"/tour/{RUN_ID}?claim=c1", f"/tour/{RUN_ID}?claim=c1&embed=1"):
        r = client.get(url)
        assert r.status_code == 200 and "TOUR_CFG" in r.text and "http-equiv=refresh" not in r.text
        assert "Finding the spot" not in r.text
        cfg = _cfg(r.text)
        assert cfg["spot"]["status"] == "pending" and cfg["spot"]["sweeps"] == [] and cfg["ss"] is None
        assert "start position" in cfg["note"]
    gate.set()
    for th in list(tour._JOBS.values()):
        th.join(5)
    assert _cfg(client.get(f"/tour/{RUN_ID}?claim=c1").text)["spot"]["sweeps"] == ["pano0000abcd"]  # next visit


def test_whole_unit_claim_is_not_picked(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _add_claim({"id": "c4", "kind": "area_sqft", "value": 900, "unit": "sqft", "scope": "unit", "label": "Plan Z",
                "quote": "900 sq ft", "checkable": True},
               {"claim_id": "c4", "verdict": "contradicted", "claimed": "900 sq ft", "found": "700 sq ft",
                "source": "scan", "reason": "x", "image": None})
    html = client.get(f"/tour/{RUN_ID}?claim=c4").text
    assert _cfg(html)["spot"]["status"] == "skip" and "whole unit" in html
    assert FAKE_CLIENT.queries == 0 and FAKE_LOOK.calls == 0


def test_more_bedrooms_than_listed_stands_in_the_smallest(tmp_path):
    _summary(tmp_path, [{"id": "big", "tags": ["bedroom"], "dimensions": {"areaFloor": 15.0}},
                        {"id": "small", "tags": ["bedroom"], "dimensions": {"areaFloor": 9.0}},
                        {"id": "liv", "tags": ["living"], "dimensions": {"areaFloor": 30.0}}])
    claim = {"id": "c9", "kind": "bedrooms", "value": 1, "label": "Plan Z"}
    verdict = {"claimed": "1 bedroom", "found": "2 bedrooms"}
    pick = spots.pick_spot(MODEL, claim, verdict, client=FAKE_CLIENT, look=FAKE_LOOK)
    assert pick["status"] == "found" and pick["how"] == "geometry" and pick["room_id"] == "small"
    assert "smallest" in pick["why"] and FAKE_LOOK.calls == 0
    fewer = spots.pick_spot(MODEL, {**claim, "value": 3}, {"claimed": "3 bedrooms", "found": "2 bedrooms"},
                            client=FAKE_CLIENT, look=FAKE_LOOK)
    assert fewer["status"] == "skip" and FAKE_LOOK.calls == 0


def test_spread_spots_one_per_room_then_far_apart():
    panos = [{"id": f"p{i}", "position": {"x": float(i), "y": 0.0, "z": 1.4}, "anchor": {"room": {"id": "r1"}}}
             for i in range(20)]
    panos.append({"id": "lone", "position": {"x": 50.0, "y": 0.0, "z": 1.4}, "anchor": {"room": {"id": "r2"}}})
    picked = spots.spread_spots(panos, 4)
    ids = [p["id"] for p in picked]
    assert len(ids) == 4 and "lone" in ids and "p0" in ids  # r2's only spot, and the far end of r1


def test_report_embeds_scan_mismatch_only(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    html = client.get(f"/report/{RUN_ID}?v=old").text
    assert html.count("Walk into this room") == 1  # Plan Z item; the aerial floors item has none
    # inline viewer: lazy iframe (no src until clicked) fed by the embed URL; the plain link stays as fallback
    assert f"<div class=walk data-src='/tour/{RUN_ID}?claim=c1&amp;embed=1'>" in html
    assert f"href='/tour/{RUN_ID}?claim=c1'" in html and ">Full screen</button>" in html and ">Close</button>" in html
    walks = re.findall(r"<div class=walk .*?</iframe>", html, re.S)
    assert walks and not any("target=" in w or "new tab" in w for w in walks)  # never a new tab
    frame = html[html.index("<iframe"):html.index("</iframe>")]
    assert " src=" not in frame and "allow='fullscreen; xr-spatial-tracking'" in frame
    assert html.count("<iframe") == 1
    assert FAKE_KEY not in html


def test_tour_embed_mode(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    r = client.get(f"/tour/{RUN_ID}?claim=c1&embed=1")
    assert r.status_code == 200 and "TOUR_CFG" in r.text
    assert "Back to the report" not in r.text and "<body class=embed>" in r.text
    assert "x-frame-options" not in r.headers and "frame-ancestors" not in r.headers.get("content-security-policy", "")
    plain = client.get(f"/tour/{RUN_ID}?claim=c1").text
    assert "Back to the report" in plain and "<body class=embed>" not in plain
    assert "Back to the report" not in client.get(f"/tour/{RUN_ID}?claim=c3&embed=1").text  # 404 page too


def test_match_row_gets_3d_viewer_and_checks_out_wording(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _add_claim({"id": "c5", "kind": "bathrooms", "value": 1, "unit": None, "scope": "unit", "label": "Plan Z",
                "quote": "1 Bath", "checkable": True},
               {"claim_id": "c5", "verdict": "confirmed", "claimed": "1 bathroom", "found": "1 bathroom",
                "source": "scan", "reason": "The 3D scan shows 1 bathroom.", "image": None})
    report = client.get(f"/report/{RUN_ID}?v=old").text
    match = report[report.index("<section class=panel id='match'>"):report.index("<section class=panel id='cant-verify'>")]
    assert "See it in 3D" in match and f"data-src='/tour/{RUN_ID}?claim=c5&amp;embed=1'" in match
    assert "Walk into this room" in report  # the mismatch item keeps its own button
    html = client.get(f"/tour/{RUN_ID}?claim=c5&embed=1").text
    assert "Listing says 1 bathroom" in html and "Checks out in the 3D scan" in html and "class=strike" not in html
    assert _cfg(html)["tag"]["label"] == "1 bathroom"
    assert "<span class=strike>2 bedrooms</span>" in client.get(f"/tour/{RUN_ID}?claim=c1").text  # mismatch unchanged


# ---------- report design A: the live viewer is embedded inline, opens at the spot, never a new tab ----------

def _report_a(client):
    return client.get(f"/report/{RUN_ID}").text


def test_report_a_embeds_live_viewer_without_click(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    html = _report_a(client)
    assert f"<div class=walk data-src='/tour/{RUN_ID}?claim=c1&amp;embed=1' data-claim='c1'>" in html
    assert "Opening at the spot that shows this" in html and "Live 3D · Matterport" in html
    # mounted by the page script (lead viewer at once, the rest near the screen); nothing opens a new tab
    assert "window.RC3D" in html and "IntersectionObserver" in html and "MAX=2" in html
    assert "Walk into this room" not in html and "Open in new tab" not in html
    assert not re.search(r"href='/tour/[^']*'[^>]*target=_blank", html)
    assert "<link rel=preconnect href='https://my.matterport.com' crossorigin>" in html
    assert FAKE_KEY not in html and "applicationKey" not in html
    assert "<a class=btn href='#f1'>3D walk</a>" in html  # top nav goes to the finding's own viewer


def test_report_a_match_row_opens_viewer_in_place(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _add_claim({"id": "c5", "kind": "bathrooms", "value": 1, "unit": None, "scope": "unit", "label": "Plan Z",
                "quote": "1 Bath", "checkable": True},
               {"claim_id": "c5", "verdict": "confirmed", "claimed": "1 bathroom", "found": "1 bathroom",
                "source": "scan", "reason": "The 3D scan shows 1 bathroom.", "image": None})
    html = _report_a(client)
    assert (f"<button type=button class=see3d data-src='/tour/{RUN_ID}?claim=c5&amp;embed=1' data-claim='c5'>"
            "See it in 3D</button>") in html


def test_report_a_static_copy_has_no_viewer():
    from server.report_a import render_report
    html = render_report(_run(), live_3d=False)
    assert "data-src=" not in html and "/tour/" not in html and "my.matterport.com" not in html.split("<style>")[0]
    assert "The live 3D view opens in the report from the local checker." in html


def test_claim_field_panel_shows_viewer_not_a_tab():
    from server.claim_field import claim_field_section
    html = claim_field_section(_run())
    assert f'"tour":"/tour/{RUN_ID}?claim=c1\\u0026embed=1"' in html and f'"tour_page":"/tour/{RUN_ID}?claim=c1"' in html
    assert "/tour/" not in claim_field_section(_run(), tours=False)
    assert "cf-3d" in html and "cf-3dhost" in html and "_blank\" rel=\"noopener\" href=\"'+esc(cell.tour)" not in html


def test_embed_page_reports_progress_and_opens_at_spot_label(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _summary(tmp_path, [{"id": "roomA", "tags": ["bathroom"]}, {"id": "roomB", "tags": ["living"]}])
    html = _warm(client, f"/tour/{RUN_ID}?claim=c2&embed=1")
    cfg = _cfg(html)
    assert cfg["embed"] is True and cfg["spot"]["sweeps"] == ["pano0000abcd"]
    assert cfg["ss"] == "1"  # label 0 = the first sweep; ss counts from 1: the showcase opens there, not at the start
    assert "postMessage" in html and "tell('ready'" in html and "Transition.INSTANT" in html
    assert "&amp;ss=1&amp;" in html and "location.origin" in html  # in the showcase URL itself
    assert "rel=preconnect href='https://my.matterport.com'" in html
    plain = _cfg(client.get(f"/tour/{RUN_ID}?claim=c2").text)
    assert plain["embed"] is False


def test_embed_message_pages_tell_the_report(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    off = client.get(f"/tour/{RUN_ID}?claim=c3&embed=1").text  # aerial claim: no scan
    assert '"state": "off"' in off and "postMessage" in off
    assert "postMessage" not in client.get(f"/tour/{RUN_ID}?claim=c3").text  # standalone page: nobody to tell


def test_prewarm_starts_every_viewer_pick_mismatches_first(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    seen = []

    def stub(model_id, claim, verdict):
        seen.append(claim["id"])
        return {"status": "none", "why": "x"}
    monkeypatch.setattr(tour, "PICKER", stub)
    monkeypatch.setattr(tour, "PREWARM", True)
    assert tour.prewarm(_run()) == 0  # the fake runner's scan ids are invented
    run = {**_run(), "harness": "real"}
    run["events"][1]["claims"] += [
        {"id": "c6", "kind": "laundry", "value": "in-unit", "label": "Plan Z", "quote": "In-unit washer and dryer"},
        {"id": "c7", "kind": "area_sqft", "value": 700, "label": "Plan Z", "quote": "700 sq ft"}]
    run["events"].insert(4, {"type": "verdict", "verdict": {"claim_id": "c6", "verdict": "cant_check",
                                                            "claimed": "In-unit washer and dryer", "found": "unclear",
                                                            "source": "scan", "reason": "x", "image": None}})
    run["events"].insert(4, {"type": "verdict", "verdict": {"claim_id": "c7", "verdict": "confirmed",
                                                            "claimed": "700 sq ft", "found": "700 sq ft",
                                                            "source": "scan", "reason": "x", "image": None}})
    # c1, c2 (mismatches) first, then the couldn't-check c6; c3 is aerial, c7 a whole-unit claim (nothing to pick)
    assert tour.prewarm(run) == 3
    tour._WARMERS[-1].join(5)
    assert seen == ["c1", "c2", "c6"]
    monkeypatch.delenv("MATTERPORT_SDK_KEY")
    tour.reset_spots()
    assert tour.prewarm(run) == 0  # no viewer key: nothing to warm


def test_report_route_prewarms(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tour, "prewarm", lambda run: calls.append(run.get("run_id")) or 0)
    assert client.get(f"/report/{RUN_ID}").status_code == 200 and calls == [RUN_ID]


# ---------- wording and look: report design, strike-through only for a contradicted claim ----------

def _cant_claim():
    _add_claim({"id": "c6", "kind": "laundry", "value": "in-unit", "unit": None, "scope": "unit", "label": "Plan Z",
                "quote": "In-unit washer and dryer", "checkable": True},
               {"claim_id": "c6", "verdict": "cant_check", "claimed": "In-unit washer and dryer", "found": "unclear",
                "source": "scan", "reason": "The photos don't show this clearly.", "image": None})


def test_couldnt_check_is_not_struck_and_says_so_softly(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _cant_claim()
    FAKE_LOOK.answer = {"spot": "none", "view": None, "seen": None}
    html = _warm(client, f"/tour/{RUN_ID}?claim=c6")
    card = html[html.index("<aside class=card"):html.index("</aside>")]
    assert "Couldn’t see it in this scan" in card and "In-unit washer and dryer" in card
    assert "We looked at 6 spots and none clearly shows it." in card
    assert "strike" not in card and "<s>" not in card and "unclear" not in card.lower() and "Really" not in html
    for bad in ("fake", "lie", "scam", "fraud"):
        assert not re.search(rf"\b{bad}\b", card.lower())


def test_close_is_not_struck(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _add_claim({"id": "c8", "kind": "bathrooms", "value": 1, "label": "Plan Z", "quote": "1 Bath"},
               {"claim_id": "c8", "verdict": "close", "claimed": "1 bathroom", "found": "1 bathroom + powder room",
                "source": "scan", "reason": "x", "image": None})
    card = client.get(f"/tour/{RUN_ID}?claim=c8").text
    card = card[card.index("<aside class=card"):card.index("</aside>")]
    assert "strike" not in card and "Close: the 3D scan shows 1 bathroom + powder room" in card


def test_page_uses_report_look_and_no_matterport_popup(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    html = client.get(f"/tour/{RUN_ID}?claim=c1").text
    style = html[html.index("<style>"):html.index("</style>")]
    assert "Newsreader" in style and "--blue:#1a4be0" in style
    assert "#B3261E" not in html and "box-shadow:0 1px" not in style and "border-radius:12px" not in style
    assert "border-radius:999px" not in style and "class=pill" not in html  # no dark title pill
    assert "Tag.allowAction" in html and "Tag.open" not in html  # our callout, never Matterport's popup
    assert "26 / 255" in html  # marker in the report's blue
    # the showcase loads while the page parses: quick start, no intro / help / title; SDK preloaded
    frame = html[html.index("<iframe"):html.index("</iframe>")]
    assert "&amp;qs=1" in frame and "play=1" in frame and "help=0" in frame and "title=0" in frame
    assert "rel=modulepreload" in html


def test_still_picture_covers_standalone_page_only(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    run = _run()
    run["events"][2]["evidence"]["images"] = [{"path": "/img/0123456789abcdef", "caption": "c"}]
    srv.save_run(run)
    html = client.get(f"/tour/{RUN_ID}?claim=c1").text
    assert "class=still id=still style='background-image:url(/img/0123456789abcdef)'" in html
    assert "class=still" not in client.get(f"/tour/{RUN_ID}?claim=c1&embed=1").text  # the report covers its frame


def test_studio_listing_keeps_its_own_word_and_note_never_contradicts(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _add_claim({"id": "c9", "kind": "bedrooms", "value": 0, "unit": None, "scope": "unit", "label": "Plan Z",
                "quote": "Studio · 1 Bath · 400 sq ft", "checkable": True},
               {"claim_id": "c9", "verdict": "contradicted", "claimed": "0 bedrooms", "found": "1 bedroom",
                "source": "scan", "reason": "x", "image": None})
    t = tour.tour_target(srv.load_run(RUN_ID), "c9")
    none = {"status": "none", "how": "picture", "looked_at": 10,
            "why": "We looked at 10 spots in the scan and none clearly shows a bedroom."}
    html = tour.render_tour(srv.load_run(RUN_ID), t, FAKE_KEY, spot=none, embed=True)
    card = html[html.index("<aside class=card"):html.index("</aside>")]
    assert "Listing says <span class=strike>Studio</span>" in card and "0 bedrooms" not in card
    assert "The 3D scan shows 1 bedroom" in card and "none clearly shows" not in card
    assert _cfg(html)["tag"]["label"] == "Studio" and "none clearly shows" not in _cfg(html)["note"]
    # a note that agrees with the verdict stays
    v0 = dict(t["verdict"], found="0 bedrooms (studio)")
    assert "none clearly shows a bedroom" in tour._spot_note(none, False, v0)
    # a number the listing really said is kept as is
    assert tour._claimed_text({"kind": "bedrooms", "value": 2, "quote": "2 Beds"}, {"claimed": "2 bedrooms"}) == "2 bedrooms"


def test_overlays_fit_small_frames(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    html = client.get(f"/tour/{RUN_ID}?claim=c1&embed=1").text
    style = html[html.index("<style>"):html.index("</style>")]
    # small frame: the card is one line with a More toggle; the callout flips left / shrinks / hides its words
    assert "@media (max-width:480px),(max-height:300px)" in style and ".embed .card:not(.open) .note{display:none}" in style
    assert "<span class=more aria-hidden=true>More</span>" in html and "aria-expanded=false" in html
    assert ".call.flip div{" in style and ".call.dot div{display:none}" in style
    assert "keepOut" in html and "sz.w - 168" in html  # clear of the report's Full screen button
    assert ".card,.call{opacity:1;transition:none}" in style  # no half-faded caption
    # standalone: Back sits top right, clear of Matterport's logo (top left)
    style = client.get(f"/tour/{RUN_ID}?claim=c1").text
    assert ".back{position:fixed;top:16px;right:16px;" in style
