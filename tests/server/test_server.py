"""Server tests: fake runner, isolated cache under tmp_path."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import server.app as srv
import server.fake_run as fake

TYPES = {"run_started", "step", "claims", "match", "evidence", "verdict", "fill_in", "done", "error"}
SNAP = {"url": "https://example.com/l/1", "title": "Test listing",
        "address_text": "418 Linden Grove Ave, Houston, TX 77006", "text": "2 Beds", "jsonld": []}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setenv("RC_FAKE", "1")
    monkeypatch.delenv("RC_REPLAY", raising=False)
    monkeypatch.setattr(fake, "GAP", (0.005, 0.01))
    monkeypatch.setattr(srv, "REPLAY_GAP", 0.01)
    srv._IMAGES.clear()


@pytest.fixture
def client():
    return TestClient(srv.app)


def post(client, snap=SNAP):
    with client.stream("POST", "/check", json=snap) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    events = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith("data: ")]
    return raw, events


def test_health(client):
    assert client.get("/health").json() == {"ok": True, "harness": "fake"}


def test_stream_order_and_no_paths(client):
    raw, events = post(client)
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "done"
    assert all(e["type"] in TYPES for e in events)
    for bad in ("C:/", "C:\\", "rc_cache"):
        assert bad not in raw
    assert events[-1]["report_url"].endswith("/report/" + events[0]["run_id"])
    assert events[-1]["run_id"] == events[0]["run_id"]


def test_images(client):
    _, events = post(client)
    urls = [im["path"] for e in events if e["type"] == "evidence" for im in e["evidence"]["images"]]
    urls += [e["verdict"]["image"] for e in events if e["type"] == "verdict" and e["verdict"]["image"]]
    assert urls and all(u.startswith("/img/") for u in urls)
    r = client.get(urls[0])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get("/img/0123456789abcdef").status_code == 404
    assert client.get("/img/../server/app.py").status_code == 404
    assert client.get("/img/..%2F..%2Fpyproject.toml").status_code == 404
    assert client.get(r"/img/..\..\pyproject.toml").status_code == 404
    # survives a restart: token found from the saved run
    srv._IMAGES.clear()
    assert client.get(urls[0]).status_code == 200


def test_runs_and_report(client):
    _, events = post(client)
    rid = events[0]["run_id"]
    run = client.get(f"/runs/{rid}.json").json()
    assert run["events"] == events
    assert run["snapshot"]["address_text"] == SNAP["address_text"]
    assert len(run["event_times"]) == len(events) and "images" not in run
    html = client.get(f"/report/{rid}?v=old").text
    assert events[-1]["headline"] in html
    claims = next(e for e in events if e["type"] == "claims")["claims"]
    for c in claims:
        assert c["quote"] in html
    for external in ("<link", "<script src", "src='http", "src=\"http", "@import", "monospace"):
        assert external not in html  # self-contained, no external assets
    assert client.get("/report/nope").status_code == 404
    assert client.get("/runs/..%2Fx.json").status_code == 404


def test_replay(client, monkeypatch):
    calls = []

    def counting(snapshot, emit):
        calls.append(1)
        return fake.run_fake(snapshot, emit)

    monkeypatch.setattr(srv, "pick_runner", lambda: ("fake", counting))
    monkeypatch.setenv("RC_REPLAY", "1")
    _, first = post(client)
    snap2 = {**SNAP, "address_text": "418  linden grove ave., Houston TX 77006"}
    _, second = post(client, snap2)
    assert len(calls) == 1
    assert second == first


def test_runner_error(client, monkeypatch):
    def boom(snapshot, emit):
        emit({"type": "step", "agent": "orchestrator", "text": "Starting."})
        raise RuntimeError("kaput")

    monkeypatch.setattr(srv, "pick_runner", lambda: ("fake", boom))
    _, events = post(client)
    assert [e["type"] for e in events] == ["run_started", "step", "error"]
    assert "kaput" in events[-1]["message"]
    monkeypatch.undo()
    assert client.get("/health").status_code == 200


def test_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(fake, "GAP", (0.15, 0.15))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        t0 = time.monotonic()
        times = []
        with httpx.stream("POST", f"http://127.0.0.1:{port}/check", json=SNAP, timeout=30) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    times.append(time.monotonic() - t0)
        total = times[-1]
        assert total > 2.0
        assert times[0] < total / 2  # run_started arrives long before the end
        assert times[2] < total - 1.5  # later events also arrive before the run ends
    finally:
        server.should_exit = True
        t.join(5)


def test_report_tabs(client):
    import re

    _, events = post(client)
    html = client.get(f"/report/{events[0]['run_id']}?v=old").text
    # three verdict tabs with counts (every verdict lands in one), then "Not on the listing"
    assert re.findall(r"<a href='#([a-z-]+)'", html) == ["mismatch", "match", "cant-verify", "extra"]
    for word, n in (("Mismatch", 3), ("Match", 1), ("Can't verify", 1)):
        assert f"{word} ({n})" in html.replace("&#x27;", "'")
    for pid in ("mismatch", "match", "cant-verify"):
        assert f"<section class=panel id='{pid}'>" in html
    # pictures only through /img/<token>, never repeated across verdict items
    assert all(re.fullmatch(r"/img/[a-f0-9]{16}", s) for s in re.findall(r"<img src='([^']+)'", html))
    srcs = re.findall(r"<img src='([^']+)'", html.split("<section class=panel id='extra'>")[0])
    assert srcs and all(re.fullmatch(r"/img/[a-f0-9]{16}", s) for s in srcs)
    assert len(srcs) == len(set(srcs))
    # plan claims grouped under one item, headline claim first, visuals for area and floors
    assert html.count("<h3>Plan E</h3>") == 1
    assert html.index("<h3>Plan E</h3>") < html.index("<h3>5-story building</h3>")
    assert "class='viz bars'" in html and "class='viz floors'" in html
    assert "County records: 6 floors." in html and "Building record: 5 floors." in html
    assert "How we checked" in html and "<span class=ag>Lead</span>" in html
    for bad in ("C:/", "C:\\", "rc_cache", "Users", "token=", "key=", "Signature", "Contradicted", "Source:"):
        assert bad not in html, bad


def test_report_pictures_open_in_page_lightbox(client):
    import re

    _, events = post(client)
    html = client.get(f"/report/{events[0]['run_id']}?v=old").text
    links = re.findall(r"<a class=pic href='(/img/[a-f0-9]{16})'[^>]*>", html)
    imgs = re.findall(r"<img src='(/img/[a-f0-9]{16})'", html)
    assert links and sorted(links) == sorted(imgs)  # every picture is a same-tab link (no-JS fallback)
    for a in re.findall(r"<a class=pic [^>]*>", html):
        assert "target=" not in a
    assert "<div class=lb id=lb hidden role=dialog" in html and "<button type=button>Close</button>" in html
    assert "closest('a.pic')" in html and "Escape" in html


def test_report_empty_tab():
    from server.report import render_report

    run = {"snapshot": {"address_text": "1 Main St"}, "events": [
        {"type": "claims", "claims": [{"id": "c1", "kind": "amenity", "value": "pool", "scope": "building",
                                       "label": None, "quote": "Pool"}]},
        {"type": "verdict", "verdict": {"claim_id": "c1", "verdict": "cant_check", "claimed": "pool",
                                        "found": "no data", "source": None, "reason": "None of our sources measured this.",
                                        "image": None}},
        {"type": "done", "counts": {}, "headline": "Nothing to compare."}]}
    html = render_report(run)
    assert html.count("Nothing here.") == 2
    assert "Nothing in our data measures this" in html and "Pool" in html


def test_fill_in_event_saved_and_replayed(client, monkeypatch):
    """Fake mode sends a fill_in event (invented facts) before done; its images become /img/ tokens; the saved
    run keeps it in events and at top level; a replay sends it again; the report shows the tab."""
    import re

    raw, events = post(client)
    types = [e["type"] for e in events]
    assert types.count("fill_in") == 1 and types.index("fill_in") == len(types) - 2
    facts = events[types.index("fill_in")]["facts"]
    assert facts and all({"id", "topic", "label", "value", "source", "note", "image"} <= set(f) for f in facts)
    imgs = [f["image"] for f in facts if f["image"]]
    assert imgs and all(re.fullmatch(r"/img/[a-f0-9]{16}", u) for u in imgs)
    assert all(client.get(u).status_code == 200 for u in imgs)
    run_id = events[0]["run_id"]
    saved = client.get(f"/runs/{run_id}.json").json()
    assert saved["fill_in"] == facts
    assert [e for e in saved["events"] if e["type"] == "fill_in"][0]["facts"] == facts
    html = client.get(f"/report/{run_id}").text
    assert "Not on the listing" in html and facts[0]["label"].split()[0] in html
    monkeypatch.setenv("RC_REPLAY", "1")
    _, again = post(client)
    assert [e for e in again if e["type"] == "fill_in"][0]["facts"] == facts


# ---------- address pick, cache switch, drawer field (invented addresses and buildings) ----------

HERO = "418 Linden Grove Ave, Faketown, ZZ 00001"
FOOT = "900 Placeholder Pkwy, Faketown, ZZ 00001"


def _fake_kit(monkeypatch):
    """An invented kit: only HERO matches, as a residential building named like the page title."""
    import server.address_pick as ap

    def match(address):
        if address.lower().startswith("418 linden grove"):
            return {"status": "matched", "confidence": 0.95, "property_id": "990001", "name": "Larkspur Example Commons",
                    "address": HERO, "kind": "building", "record": {"unit_count": 100}, "floorplans": [{"name": "A1"}],
                    "how": "test"}
        return {"status": "no_match", "confidence": 0.0, "how": "test"}
    monkeypatch.setattr(ap, "_default_matcher", lambda: match)


def _snap(**kw):
    return {**SNAP, "title": "Larkspur Example Commons | Apartments", "address_text": FOOT,
            "address_candidates": [{"text": FOOT, "score": 6, "where": ["footer", "label:corporate"], "why": "In the footer."},
                                   {"text": HERO, "score": 4, "where": ["main"], "why": "In the page text."}], **kw}


def test_address_pick_on_run_started(client, monkeypatch):
    _fake_kit(monkeypatch)
    _, events = post(client, _snap())
    rs = events[0]
    assert rs["type"] == "run_started" and rs["address"] == HERO  # the footer office address stepped aside
    pick = rs["address_pick"]
    assert set(pick) == {"chosen", "why", "confidence", "alternatives"} and pick["chosen"] == HERO
    assert pick["confidence"] >= 0.5 and pick["why"]
    assert [a["text"] for a in pick["alternatives"]] == [FOOT]
    assert all(set(a) == {"text", "name", "why"} for a in pick["alternatives"])
    assert rs["cache"] == "on"
    assert "990001" not in json.dumps(rs) and "property_id" not in json.dumps(rs)  # no property ids to the browser
    saved = client.get(f"/runs/{rs['run_id']}.json").json()["snapshot"]
    assert saved["address_text"] == HERO and saved["address_candidates"][1]["text"] == HERO
    assert saved["address_pick"] == pick  # saved without the Match


def test_locked_address_is_kept(client, monkeypatch):
    _fake_kit(monkeypatch)
    _, events = post(client, _snap(address_locked=True))
    assert events[0]["address"] == FOOT
    assert events[0]["address_pick"]["why"] == "You picked this address."
    assert client.get(f"/runs/{events[0]['run_id']}.json").json()["snapshot"]["address_locked"] is True


def test_pick_failure_keeps_snapshot(client, monkeypatch):
    import server.app as app_mod

    def boom(snapshot):
        raise RuntimeError("kit missing")
    monkeypatch.setattr(app_mod, "pick_address", boom)
    _, events = post(client, _snap())
    assert events[0]["address"] == FOOT and events[0]["address_pick"] is None
    assert events[-1]["type"] == "done"


def test_no_cache_turns_replay_off(client, monkeypatch):
    calls = []

    def counting(snapshot, emit):
        calls.append(1)
        return fake.run_fake(snapshot, emit)

    monkeypatch.setattr(srv, "pick_runner", lambda: ("fake", counting))
    monkeypatch.setenv("RC_REPLAY", "1")
    _, first = post(client)
    monkeypatch.setenv("RC_NO_CACHE", "1")
    _, second = post(client)
    assert len(calls) == 2 and second[0]["cache"] == "off" and first[0]["cache"] == "on"
    assert second[0]["run_id"] != first[0]["run_id"]
    monkeypatch.delenv("RC_NO_CACHE")
    _, third = post(client)
    assert len(calls) == 2  # replayed again


def test_done_field_is_the_report_claim_field(client):
    from server.claim_field import claim_field

    _, events = post(client)
    done = events[-1]
    run_id = events[0]["run_id"]
    run = client.get(f"/runs/{run_id}.json").json()
    want = claim_field(run)
    got = done["field"]["claims"]
    assert [c["id"] for c in got] == [c["id"] for c in want["claims"]]
    for g, w in zip(got, want["claims"]):
        assert g["cells"] == {k: w["cells"][k]["state"] for k in ("record", "scan", "aerial", "county")}
        assert set(g) == {"id", "cells", "image"}
        if not w["highlight"]:
            assert g["image"] is None
    imgs = [c["image"] for c in got if c["image"]]
    assert imgs and all(client.get(u).status_code == 200 for u in imgs)  # proof pictures for the findings
    assert client.get(f"/field/{run_id}").json() == done["field"]
    assert client.get("/field/nope").status_code == 404
