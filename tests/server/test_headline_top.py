"""One headline for the drawer and the report: done.top (server.report_a.top_finding) and the 3D Esc path.
Invented runs only (418 Linden Grove Ave, run ids 900001/900002)."""
import html as htmllib
import json
import re

import pytest
from fastapi.testclient import TestClient

import server.app as srv
import server.fake_run as fake
from server import tour
from server.report import render_report as render_old
from server.report_a import render_report, top_finding

ADDR = "418 Linden Grove Ave, Inventville, ZZ 00000"


def _claim(cid, kind, value, quote, label=None, scope="building"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote, "checkable": True}


def _v(cid, verdict, claimed, found, source):
    return {"claim_id": cid, "verdict": verdict, "claimed": claimed, "found": found, "source": source,
            "reason": "Invented reason.", "image": None}


def _run(verdicts, run_id="900001", done=True):
    claims = [_claim("c1", "stories", 5, "5-story building"),
              _claim("c2", "year_built", 2016, "Built in 2016"),
              _claim("c3", "bedrooms", 2, "2 Beds", "Plan E", "unit"),
              _claim("c4", "amenity", "pool", "Pool")]
    ev = [{"type": "run_started", "run_id": run_id, "address": ADDR},
          {"type": "claims", "claims": claims},
          {"type": "match", "match": {"status": "matched", "confidence": 0.9, "address": ADDR, "how": "invented"}}]
    ev += [{"type": "verdict", "verdict": v} for v in verdicts]
    if done:
        ev.append({"type": "done", "run_id": run_id, "headline": "Harness words.", "counts": {}})
    return {"run_id": run_id, "snapshot": {"address_text": ADDR, "url": "https://listing.invalid/1", "title": "x"},
            "finished": True, "events": ev, "images": {}}


ONE_HARD_ONE_CLOSE = [_v("c1", "contradicted", "5 floors", "6 floors", "aerial"),
                      _v("c2", "close", "built in 2016", "built in 2015", "county"),
                      _v("c3", "confirmed", "2 bedrooms", "2 bedrooms", "record"),
                      _v("c4", "cant_check", "pool", "no data", None)]
ONLY_CLOSE = [_v("c2", "close", "built in 2016", "built in 2015", "county"),
              _v("c3", "confirmed", "2 bedrooms", "2 bedrooms", "record")]
ALL_OK = [_v("c1", "confirmed", "5 floors", "5 floors", "aerial"),
          _v("c3", "confirmed", "2 bedrooms", "2 bedrooms", "record")]


def _hero(html: str) -> tuple[str, str]:
    """(h1 text, top line text) of a design A page."""
    h1 = re.search(r"<section class=hero.*?<h1>(.*?)</h1>", html, re.S).group(1)
    h1 = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", h1)).split())
    m = re.search(r"<p class=top-line>(.*?)</p>", html, re.S)
    return h1, htmllib.unescape(m.group(1)) if m else ""


@pytest.mark.parametrize("verdicts,headline,claim", [
    (ONE_HARD_ONE_CLOSE, "2 things don't match.", "c1"),  # a close one counts as a mismatch
    (ONLY_CLOSE, "1 thing doesn't match.", "c2"),  # and leads when it is the only one
    (ALL_OK, "Everything we could check matches.", None),
])
def test_top_matches_the_report_hero(verdicts, headline, claim):
    run = _run(verdicts)
    top = top_finding(run)
    assert top["headline"] == headline and " ".join(top["lines"]) == headline
    assert top["claim_id"] == claim
    assert top["calm"] is (claim is None)
    h1, line = _hero(render_report(run))
    assert h1 == headline and top["line"] == line
    assert bool(line) is (claim is not None)
    json.dumps(top)  # plain JSON for the SSE line


def test_top_uses_the_report_ranking_not_page_order():
    # floors come first on the page; bedrooms cost the renter more, so they lead (report's renter-impact tiers)
    run = _run([_v("c1", "contradicted", "5 floors", "6 floors", "aerial"),
                _v("c2", "close", "built in 2016", "built in 2015", "county"),
                _v("c3", "contradicted", "2 bedrooms", "1 bedroom", "scan")])
    top = top_finding(run)
    assert top["headline"] == "3 things don't match." and top["hard"] == 3
    # no score sent: a contradicted one before a close one, then the page's order (strictly by score otherwise)
    assert top["claim_id"] == "c1" and top["order"] == ["c1", "c3", "c2"]
    assert "floors" in top["line"] and top["line"] == _hero(render_report(run))[1]


def test_order_is_strictly_by_score_and_the_pick_never_moves_a_row():
    vs = [_v("c1", "contradicted", "5 floors", "6 floors", "aerial"),
          _v("c2", "close", "built in 2016", "built in 2015", "county"),
          _v("c3", "contradicted", "2 bedrooms", "1 bedroom", "scan"),
          _v("c4", "contradicted", "900 sq ft", "700 sq ft", "scan")]
    vs[0]["confidence"] = {"score": 40, "band": "low"}
    vs[1]["confidence"] = {"score": 80, "band": "high"}
    vs[2]["confidence"] = {"band": "high"}  # no score: after every scored one
    vs[3]["confidence"] = {"score": 82, "band": "high"}
    run = _run(vs)
    run["events"][-1]["harness"] = {"top_mismatch": {"claim_id": "c1", "by": "model"}}
    top = top_finding(run)
    assert top["order"] == ["c4", "c2", "c1", "c3"] and top["claim_id"] == "c4"
    assert top["line"] == _hero(render_report(run))[1]


def test_top_of_an_empty_run():
    top = top_finding({"snapshot": {}, "events": []})
    assert top == {"headline": "No claims to check.", "lines": ["No claims", "to check."], "calm": True, "hard": 0,
                   "claim_id": None, "line": "", "order": [], "why": "", "by": None}


# ---------- the server puts it on done ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setenv("RC_FAKE", "1")
    monkeypatch.delenv("RC_REPLAY", raising=False)
    monkeypatch.setattr(fake, "GAP", (0.005, 0.01))
    monkeypatch.setattr(srv, "REPLAY_GAP", 0.01)
    return TestClient(srv.app)


def _post(client, snap):
    with client.stream("POST", "/check", json=snap) as r:
        raw = "".join(r.iter_text())
    return [json.loads(l[6:]) for l in raw.splitlines() if l.startswith("data: ")]


SNAP = {"url": "https://listing.invalid/1", "title": "Invented listing", "address_text": ADDR, "text": "2 Beds",
        "jsonld": []}


def test_done_carries_top_from_the_report(client):
    events = _post(client, SNAP)
    done = events[-1]
    assert done["type"] == "done" and "headline" in done and "counts" in done  # old keys stay
    run = client.get(f"/runs/{done['run_id']}.json").json()
    assert done["top"] == top_finding(run)
    report = client.get(f"/report/{done['run_id']}").text
    assert _hero(report) == (done["top"]["headline"], done["top"]["line"])


def test_runner_top_is_replaced_by_the_servers(client, monkeypatch):
    def runner(snapshot, emit):
        for e in _run(ONLY_CLOSE, done=False)["events"][1:]:
            emit(e)
        emit({"type": "done", "headline": "1 thing doesn't match.", "counts": {},
              "top": {"headline": "Made up.", "lines": ["Made", "up."]}})
    monkeypatch.setattr(srv, "pick_runner", lambda: ("fake", runner))
    done = _post(client, SNAP)[-1]
    assert done["top"]["headline"] == "1 thing doesn't match." and done["top"]["calm"] is False


def test_replay_of_an_older_run_adds_top(client, monkeypatch):
    run = _run(ONE_HARD_ONE_CLOSE, run_id="900002")
    run["events"][-1]["top"] = {"headline": "1 thing doesn't match.", "lines": ["1 thing", "doesn't match."],
                                "calm": False, "hard": 1, "claim_id": "c1", "line": "", "order": ["c1", "c2"]}
    run["address_key"] = srv.norm_address(ADDR)
    srv.save_run(run)
    monkeypatch.setenv("RC_REPLAY", "1")
    done = _post(client, SNAP)[-1]
    assert done["run_id"] == "900002" and done["top"]["claim_id"] == "c1"
    assert done["top"]["headline"] == "2 things don't match."  # today's count, never the copy saved with the run


# ---------- Esc from inside a 3D viewer ----------

def test_embed_pages_forward_esc_to_the_report():
    page = tour.message_page("900001", "No 3D scan for this claim.", embed=True)
    assert tour.ESC_FORWARD in page and "key:'Escape'" in tour.ESC_FORWARD
    assert tour.ESC_FORWARD not in tour.message_page("900001", "No 3D scan for this claim.")
    run = _run(ONE_HARD_ONE_CLOSE)
    html = render_report(run)
    # the report hears it only from its own frames, from its own origin or the localhost twin
    assert "e.data.key==='Escape'" in html and "ours(e.source)" in html
    assert "e.origin!==location.origin&&e.origin!==twin" in html


def test_old_report_never_opens_a_new_tab_for_3d():
    html = render_old(_run(ONE_HARD_ONE_CLOSE))
    assert "Open in new tab" not in html
