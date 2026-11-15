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
    for word, n in (("Mismatch", 4), ("Match", 1), ("Can't verify", 1)):  # 3 hard + 1 close (fake run)
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
    # design A shows at most five picked facts under "Not in the listing", not every fill_in fact
    nil = re.search(r"<section class='wrap nil' id=extra.*?</section>", html, re.S).group(0)
    assert "Not in the listing" in nil and 1 <= nil.count("class='nil-row") <= 5
    labels = {f["label"] for f in facts}
    assert any(lab in labels for lab in re.findall(r"<p class=nil-k><b>\d</b>(.*?)</p>", nil))
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
        assert set(g) == {"id", "cells", "shade", "image", "credit"}
        # the blue's strength, only for blue cells that carry one
        assert all(x in ("high", "medium", "low") and g["cells"][k] in ("contra", "close") for k, x in g["shade"].items())
        if not w["highlight"]:
            assert g["image"] is None
    imgs = [c["image"] for c in got if c["image"]]
    assert imgs and all(client.get(u).status_code == 200 for u in imgs)  # proof pictures for the findings
    assert client.get(f"/field/{run_id}").json() == done["field"]
    assert client.get("/field/nope").status_code == 404


# ---------- the drawer's text report (done.report), credit and favicon (invented runs only) ----------

def _drawer_run():
    """An invented run: the listing says 184 homes, County says 216, CoStar says 149 (a third figure)."""
    claims = [
        {"id": "c1", "kind": "unit_count", "value": 184, "scope": "building", "label": None,
         "quote": "The community has 184 apartments", "checkable": True},
        {"id": "c2", "kind": "year_built", "value": 2018, "scope": "building", "label": None, "quote": "Built in 2018",
         "checkable": True},
        {"id": "c3", "kind": "stories", "value": 5, "scope": "building", "label": None, "quote": "5-story building",
         "checkable": True},
        {"id": "c4", "kind": "amenity", "value": "pool", "scope": "building", "label": None, "quote": "Rooftop pool",
         "checkable": True},
    ]
    v1 = {"claim_id": "c1", "verdict": "contradicted", "claimed": "184 apartments", "found": "216 apartments",
          "source": "county", "reason": "County lists 216 homes on the parcel.", "image": None,
          "confidence": {"score": 60, "band": "medium", "decided_by": "county", "why": "Invented.",
                         "sources": [{"source": "county", "says": "216 apartments", "agrees_with_listing": False},
                                     {"source": "record", "says": "149 apartments", "agrees_with_listing": False}]}}
    v2 = {"claim_id": "c2", "verdict": "close", "claimed": "built in 2018", "found": "built in 2019", "source": "county",
          "reason": "County lists 2019.", "image": None, "confidence": {"band": "low", "decided_by": "county"}}
    v3 = {"claim_id": "c3", "verdict": "confirmed", "claimed": "5 stories", "found": "5 floors", "source": "record",
          "reason": "Invented.", "image": None}
    v4 = {"claim_id": "c4", "verdict": "cant_check", "claimed": "rooftop pool", "found": "no source covers this",
          "source": "aerial", "reason": "Nothing in our data measures this.", "image": None}
    facts = [{"id": "elevators", "topic": "Building", "label": "Elevators", "value": "2 elevators; managed by Example Co",
              "source": "record", "note": "", "image": None},
             {"id": "manager", "topic": "Building", "label": "Property manager", "value": "Example Co", "source": "record",
              "note": "", "image": None}]
    events = [{"type": "run_started", "run_id": "t-drawer", "address": "418 Linden Grove Ave, Faketown, ZZ 00001"},
              {"type": "claims", "claims": claims}]
    events += [{"type": "verdict", "verdict": v} for v in (v1, v2, v3, v4)]
    events += [{"type": "fill_in", "facts": facts},
               {"type": "done", "run_id": "t-drawer", "counts": {"contradicted": 1, "close": 1, "confirmed": 1,
                                                                 "cant_check": 1},
                "harness": {"top_facts": [{"label": "Elevators", "value": "2 elevators; managed by Example Co",
                                           "why": "Moving in is easier.", "sources": ["CoStar"]},
                                          {"label": "Property manager", "value": "Example Co", "sources": ["CoStar"]}]}}]
    return {"run_id": "t-drawer", "snapshot": {"address_text": "418 Linden Grove Ave, Faketown, ZZ 00001"},
            "events": events}


def test_drawer_report_says_what_the_report_says():
    run = _drawer_run()
    rep = srv.drawer_report(run)
    rows = rep["rows"]
    assert [r["id"] for r in rows] == ["c1", "c2"]  # the most certain first; never a match or a couldn't check
    hard, close = rows
    assert hard["credit"] == ["county"]  # CoStar says 149, not 216: never credited with the found value
    assert hard["strike"] is True and close["strike"] is False
    assert hard["score"] == 60 and hard["band"] == "medium"
    assert close["score"] is None and close["band"] == "low"  # no number sent: none made up
    assert hard["shade"] == "medium" and close["shade"] == "low"  # the blue's strength by confidence
    assert hard["tag"] == "60 / 100 · medium" and close["tag"] == "Low"  # the score; the level word alone, never a guess
    assert hard["also"] == ["record"] and "county" not in hard["also"]  # the report's "also checked", same rule
    assert hard["label"] == "Homes" and hard["found"] == "216 apartments" and hard["quoted"] is True
    assert hard["proof"] == "/report/t-drawer#claim-c1" and hard["walk"] is None
    # facts: no manager row, no "; managed by" clause
    assert [f["label"] for f in rep["facts"]] == ["Elevators"]
    assert rep["facts"][0]["value"] == "2 elevators"
    assert "manag" not in json.dumps(rep["facts"]).lower()
    qs = rep["questions"]
    assert qs[0].startswith("The listing says “The community has 184 apartments”, but county records say 216")
    assert 2 <= len(qs) <= 5 and len(set(qs)) == len(qs)
    assert any("2019" in q for q in qs)  # the close one gets its question too
    assert [x["id"] for x in rep["ok"]] == ["c3"] and [x["id"] for x in rep["cant"]] == ["c4"]
    assert rep["cant"][0]["why"]
    text = json.dumps(rep).lower()
    for word in ("fake", "lie", "scam", "fraud", "/img/", "<"):
        assert word not in text
    done = srv.with_drawer_keys(run["events"][-1], run)
    assert done["report"] == rep
    assert [c["credit"] for c in done["field"]["claims"] if c["id"] == "c1"] == [["county"]]


def test_done_carries_the_drawer_report(client):
    _, events = post(client)
    done = events[-1]
    rep = done["report"]
    assert set(rep) == {"rows", "facts", "questions", "ok", "cant"}
    off = [e["verdict"]["claim_id"] for e in events if e["type"] == "verdict"
           and e["verdict"]["verdict"] in ("contradicted", "close")]
    assert sorted(r["id"] for r in rep["rows"]) == sorted(off)
    assert [r["id"] for r in rep["rows"]] == done["top"]["order"]  # the report's order
    assert len(rep["facts"]) <= 5 and len(rep["questions"]) <= max(5, len(off))


def test_favicon_is_not_a_404(client):
    r = client.get("/favicon.ico")
    assert r.status_code in (200, 204)
    if r.status_code == 200:
        assert r.headers["content-type"] == "image/png"
