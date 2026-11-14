"""Tour page (GET /tour/<run_id>?claim=<id>): invented run, isolated private folder under tmp_path."""
import json

import pytest
from fastapi.testclient import TestClient

import server.app as srv

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


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("MATTERPORT_SDK_KEY", raising=False)
    (tmp_path / ".env").write_text("MATTERPORT_API_KEY=other-secret-value-xyz\n", encoding="utf-8")
    srv.save_run(_run())


@pytest.fixture
def client():
    return TestClient(srv.app)


def _cfg(html):
    start = html.index("window.TOUR_CFG=") + len("window.TOUR_CFG=")
    return json.loads(html[start:html.index(";</script>", start)])


def test_tour_served_with_key(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    r = client.get(f"/tour/{RUN_ID}?claim=c1")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    html = r.text
    assert "Back to the report" in html and f"/report/{RUN_ID}" in html
    assert "<s>2 bedrooms</s>" in html and "1 bedroom + den" in html and "Plan Z" in html
    cfg = _cfg(html)
    assert cfg["model"] == MODEL and cfg["key"] == FAKE_KEY
    assert "1 bedroom + den" in cfg["tag"]["label"]
    assert "other-secret-value-xyz" not in html


def test_tour_key_from_private_env(client, tmp_path):
    (tmp_path / ".env").write_text(f"MATTERPORT_SDK_KEY={FAKE_KEY}\n", encoding="utf-8")
    assert _cfg(client.get(f"/tour/{RUN_ID}?claim=c1").text)["key"] == FAKE_KEY


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
    assert "<s>2 bedrooms</s>" in client.get(f"/tour/{RUN_ID}").text


def test_room_picked_only_when_one_room_carries_the_word(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _summary(tmp_path, [{"id": "roomA", "label": None, "tags": ["bathroom"], "classifications": []},
                        {"id": "roomB", "label": None, "tags": ["unfurnished"], "classifications": []},
                        {"id": "roomC", "label": None, "tags": ["bedroom"], "classifications": []}])
    bath = _cfg(client.get(f"/tour/{RUN_ID}?claim=c2").text)["room"]
    assert bath["room_id"] == "roomA" and bath["sweeps"] == ["pano0000abcd"]
    den = _cfg(client.get(f"/tour/{RUN_ID}?claim=c1").text)["room"]  # no room is tagged den: start view
    assert "room_id" not in den and den["word"] == "den"


def test_room_not_picked_when_ambiguous_or_shared(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    _summary(tmp_path, [{"id": "roomA", "tags": ["bathroom"]}, {"id": "roomB", "tags": ["bathroom"]}])
    assert "room_id" not in _cfg(client.get(f"/tour/{RUN_ID}?claim=c2").text)["room"]
    _summary(tmp_path, [{"id": "roomA", "tags": ["bathroom", "kitchen"]}])
    assert "room_id" not in _cfg(client.get(f"/tour/{RUN_ID}?claim=c2").text)["room"]


def test_report_links_scan_mismatch_only(client, monkeypatch):
    monkeypatch.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    html = client.get(f"/report/{RUN_ID}").text
    assert html.count("Walk into this room") == 1  # Plan Z item; the aerial floors item has none
    assert f"/tour/{RUN_ID}?claim=c1" in html
    assert FAKE_KEY not in html
