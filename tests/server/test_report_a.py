"""Report page, design A (server/report_a.py). Invented runs only."""
import re

import pytest
from fastapi.testclient import TestClient

import server.app as srv
from server.report_a import render_report

IMG = ["/img/" + f"{i:016x}" for i in range(1, 9)]


def _claim(cid, kind, value, quote, label=None, scope="building"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote, "checkable": True}


def _verdict(cid, verdict, claimed, found, source, reason, image=None):
    return {"claim_id": cid, "verdict": verdict, "claimed": claimed, "found": found, "source": source,
            "reason": reason, "image": image}


def _run(claims, verdicts, evidence=(), facts=None, run_id="20990101-000000-aaaaaa"):
    ev = [{"type": "run_started", "run_id": run_id, "address": "12 Invented Row, Inventville, ZZ 00000"},
          {"type": "step", "agent": "orchestrator", "text": "Starting."},
          {"type": "claims", "claims": claims},
          {"type": "match", "match": {"status": "matched", "confidence": 0.9, "name": "The Invented",
                                      "record": {"stories": 4, "year_built": 2001, "unit_count": 40},
                                      "floorplans": []}}]
    ev += [{"type": "evidence", "evidence": e} for e in evidence]
    ev += [{"type": "verdict", "verdict": v} for v in verdicts]
    if facts is not None:
        ev.append({"type": "fill_in", "facts": facts})
    ev.append({"type": "done", "run_id": run_id, "headline": "x", "counts": {}})
    run = {"run_id": run_id, "snapshot": {"address_text": "12 Invented Row, Inventville, ZZ 00000",
                                          "url": "https://listing.invalid/1", "title": "Invented listing"},
           "started_at": 1.0, "finished_at": 3.0, "finished": True, "events": ev, "images": {}}
    if facts is not None:
        run["fill_in"] = facts
    return run


def _rich():
    claims = [_claim("c1", "stories", 9, "9-story tower"),
              _claim("c2", "year_built", 2010, "Built in 2010"),
              _claim("c3", "bedrooms", 2, "2 Beds", "Plan Q", "unit"),
              _claim("c4", "area_sqft", 900, "900 sq ft", "Plan Q", "unit"),
              _claim("c5", "amenity", "pool", "Pool"),
              _claim("c6", "amenity", "concierge", "Concierge"),
              _claim("c7", "unit_count", 40, "40 homes")]
    evidence = [
        {"source": "aerial", "status": "ok", "summary": "", "detail": "",
         "observed": {"height_m": 18.0, "stories": 6},
         "images": [{"path": IMG[0], "caption": "Top-down aerial photo"}]},
        {"source": "county", "status": "ok", "summary": "", "detail": "", "images": [],
         "observed": {"stories": 6, "year_built": 2008, "unit_count": 40}},
        {"source": "record", "status": "ok", "summary": "", "detail": "", "images": [],
         "observed": {"stories": 4, "year_built": 2010, "unit_count": 40}},
        {"source": "scan", "status": "ok", "summary": "", "detail": "", "images": [
            {"path": IMG[1], "caption": "Schematic floor plan 1 of the scan"},
            {"path": IMG[2], "caption": "Panorama 1 (bedroom): x"}],
         "observed": {"plan": "Plan Q", "bedrooms": 1, "area_sqft": 700, "model_name": "1BR - 700 SF"}},
    ]
    verdicts = [
        _verdict("c1", "contradicted", "9 floors", "6 floors", "aerial",
                 "Listing says 9 floors. The measured roof height (18.0 m) means about 6 floors.", IMG[0]),
        _verdict("c2", "close", "built in 2010", "built in 2008", "county", "County records say built in 2008."),
        _verdict("c3", "contradicted", "2 bedrooms", "1 bedroom", "scan", "The 3D scan shows 1 bedroom."),
        _verdict("c4", "contradicted", "900 sq ft", "700 sq ft", "scan", "The 3D scan measures 700 sq ft."),
        _verdict("c5", "close", "pool", "not visible", "aerial", "Not visible in the aerial photo."),
        _verdict("c6", "cant_check", "concierge", "no data", None, "Nothing in our data measures this."),
        _verdict("c7", "confirmed", "40 apartments", "40 apartments", "county", "County records say 40."),
    ]
    facts = [{"id": f"p{i}", "topic": "Homes & floor plans", "label": f"Plan {chr(65 + i)}",
              "value": f"{1 + i % 3} bed, 1 bath, {600 + 80 * i} sq ft", "source": "record", "note": "", "image": None}
             for i in range(6)]
    facts += [{"id": f"f{i}", "topic": ["Building", "Rent", "Rules", "Around it", "Records"][i % 5],
               "label": f"Invented fact {i}", "value": f"value {i}", "source": ["record", "county"][i % 2],
               "note": "As of when it was last researched." if i % 2 == 0 else "", "image": None}
              for i in range(40)]
    return _run(claims, verdicts, evidence, facts)


def test_many_findings():
    html = render_report(_rich())
    assert "5 things" in html and "don&#x27;t match." in html  # counted per verdict; c3 and c4 share one module
    # one finding module per item: building claims alone, plan claims grouped
    assert len(re.findall(r"<article class='find", html)) == 4
    assert html.count("class='find lead") == 1
    # struck listing words and the truth
    assert "“9-story tower”" in html and "6 floors." in html and "≈ Built in 2008." in html
    # photo-based 'close' is soft: no strike, softer word
    finds = html[html.index("id=mismatch"):html.index("id=sources")]
    pool = finds[finds.index("“Pool”") - 200:finds.index("“Pool”") + 600]
    assert "strike" not in pool.split("“Pool”")[0][-80:] and "Not seen in photos" in pool
    # proof slots picked from evidence: pictures for floors, record line for year built
    assert re.search(r"data-proof='pictures'", html) and re.search(r"data-proof='record'", html)
    # only /img/<token> pictures, none repeated across findings
    srcs = re.findall(r"<img src='([^']+)'", html)
    assert srcs and all(re.fullmatch(r"/img/[a-f0-9]{16}", s) for s in srcs)
    finds = html[html.index("id=mismatch"):html.index("id=sources")]
    fsrcs = re.findall(r"<img src='([^']+)'", finds)
    assert len(fsrcs) == len(set(fsrcs))
    # sections and anchors the extension and old links use
    for anchor in ("id=mismatch", "id=match", "id=cant-verify", "id=extra", "id=sources", "id=questions"):
        assert anchor in html, anchor
    for word in ("CoStar", "Matterport", "Aerial", "County"):
        assert word in html
    # questions: deterministic, neutral, each copyable
    qs = re.findall(r"<button type=button class='btn ghost copy' data-text='([^']+)'", html)
    assert 1 <= len(qs) <= 5 and "Copy all" in html
    assert all("fake" not in q.lower() and "lie" not in q.lower().split() for q in qs)
    # long fact list collapsed behind a count button; plans drawn
    assert "Show all 46 facts" in html and "class='fact more'" in html and "class='fp rv" in html
    for bad in ("C:/", "C:\\", "rc_cache", "scam", "Fake", " lie"):
        assert bad not in html, bad


def test_zero_findings_is_calm():
    run = _rich()
    run["events"] = [e for e in run["events"] if not (e["type"] == "verdict" and e["verdict"]["verdict"] in
                                                       ("contradicted", "close"))]
    html = render_report(run)
    assert "Everything we could" in html and "check matches." in html
    assert "<article class='find" not in html and "Show me what" not in html and "See what we checked" in html


def test_one_finding_headline_and_no_scan_no_fill_in():
    claims = [_claim("c1", "year_built", 2010, "Built in 2010")]
    verdicts = [_verdict("c1", "contradicted", "built in 2010", "built in 2001", "record",
                         "The building record says built in 2001.")]
    run = _run(claims, verdicts, [{"source": "record", "status": "ok", "summary": "", "detail": "", "images": [],
                                   "observed": {"year_built": 2001}}])
    html = render_report(run)
    assert "1 thing" in html and "doesn&#x27;t match." in html
    assert "data-proof='record'" in html
    src = html[html.index("id=sources"):html.index("id=extra")]
    assert src.count("Not available for this address.") == 3  # scan, aerial, county
    assert "Our sources have nothing to add beyond the listing." in html
    assert "<iframe" not in html and ">3D walk<" not in html


def test_no_picture_finding_and_empty_run():
    claims = [_claim("c1", "amenity", "rooftop", "Rooftop deck")]
    verdicts = [_verdict("c1", "contradicted", "rooftop deck", "no rooftop deck", "record", "The record says none.")]
    html = render_report(_run(claims, verdicts))
    assert "data-proof='none'" in html or "data-proof='record'" in html
    empty = render_report({"snapshot": {}, "events": []})
    assert "No claims" in empty and "<article" not in empty


def test_route_serves_new_and_old(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    run = _rich()
    srv.save_run(run)
    client = TestClient(srv.app)
    new = client.get(f"/report/{run['run_id']}").text
    old = client.get(f"/report/{run['run_id']}?v=old").text
    assert "Four sources." in new and "<nav class=tabs>" not in new
    assert "<nav class=tabs>" in old
    # no outside requests other than the font
    hosts = set(re.findall(r"(?:src|href)='(https?://[^/']+)", new))
    assert hosts <= {"https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://listing.invalid"}
    assert "<script src" not in new


@pytest.mark.parametrize("n", [0, 5, 60])
def test_claim_counts_render(n):
    claims = [_claim(f"c{i}", "amenity", "x", f"Thing {i}") for i in range(n)]
    verdicts = [_verdict(f"c{i}", ["confirmed", "cant_check", "contradicted"][i % 3], f"thing {i}", f"other {i}",
                         "record" if i % 3 != 1 else None, "One line.") for i in range(n)]
    html = render_report(_run(claims, verdicts, facts=[]))
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    if n == 60:
        assert "Show the other" in html


def test_mismatch_has_claim_anchor():
    # the drawer links a finding as #claim-<id>
    claims = [_claim("c1", "stories", 5, "5-story building")]
    verdicts = [_verdict("c1", "contradicted", "5 floors", "4 floors", "record", "The building record says 4 floors.")]
    html = render_report(_run(claims, verdicts))
    assert "id='claim-c1'" in html
