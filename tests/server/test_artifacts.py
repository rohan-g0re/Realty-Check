"""Artifact library (server/artifacts.py). Invented data only."""
import hashlib
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.app as srv
from server.artifacts import artifact_href, build_artifacts, deciding_source, register

RID = "20990101-000000-aaaaaa"
ADDR = "418 Linden Grove Ave, Houston, TX 77006"
RAW_KNOWN = "C:/invented/rc_cache/scan/plan_q_still.jpg"     # in the run's image map: gets its token
RAW_UNKNOWN = "C:/invented/rc_cache/scan/not_registered.jpg"  # not in the map: left out
TOKEN = hashlib.sha1(RAW_KNOWN.encode("utf-8")).hexdigest()[:16]
IMG = ["/img/" + f"{i:016x}" for i in range(1, 6)]


def _claim(cid, kind, value, quote, label=None, scope="building"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote, "checkable": True}


def _run():
    claims = [_claim("c1", "unit_count", 120, "120 apartments"),
              _claim("c2", "bedrooms", 2, "2 Beds", "Plan Q", "unit"),
              _claim("c3", "amenity", "pool", "Resort pool"),
              _claim("c4", "amenity", "concierge", "Concierge")]
    evidence = [
        {"source": "county", "status": "ok", "summary": "Contact the owner at jane@example.invalid.",
         "detail": "Invented county parcel query (tax_year 2026).", "images": [],
         "observed": {"unit_count": 150, "year_built": 2001, "owner_type": "individual",
                      "owner_name": "Jane Q Example", "houston_311_count": 12,
                      "city_calls": {"total": 12, "by_type": {"Barking dog next door": 12}},
                      "source_name": "Invented County Appraisal District",
                      "source_url": "https://records.invalid/query?token=SECRETTOKEN"}},
        {"source": "record", "status": "ok", "summary": "Building record for 418 Linden Grove: 90 units.",
         "detail": "Invented record 900001.", "images": [],
         "observed": {"unit_count": 90, "stories": 4, "year_built": 2001}},
        {"source": "scan", "status": "ok", "summary": "The 3D scan titled 'Plan Q - 1 Bed + Den' shows 1 bedroom.",
         "detail": "Invented scan SAMPLEscan01.",
         "images": [{"path": RAW_KNOWN, "caption": "Schematic floor plan 1 of the scan"},
                    {"path": RAW_UNKNOWN, "caption": "Panorama 2 (Kitchen): four views"}],
         "observed": {"plan": "Plan Q", "model_id": "SAMPLEscan01", "model_name": "Plan Q - 1 Bed + Den",
                      "bedrooms": 1, "bathrooms": 1, "counts_from": "model_name",
                      "room_labels": ["Bedroom", "Den", "Bathroom", "Kitchen"], "area_sqft": 700}},
        {"source": "record", "status": "ok", "summary": "The building record lists plan Q: 1 bed, 1 bath.",
         "detail": "", "images": [],
         "observed": {"plan": "Plan Q", "kit_plan": "Q", "bedrooms": 1, "bathrooms": 1, "area_sqft": 760}},
        {"source": "aerial", "status": "ok", "summary": "Looking at the aerial photos for pool: not seen.",
         "detail": "vision model presence check (not a measurement).",
         "images": [{"path": IMG[0], "caption": "Top-down aerial photo (2026-01-02)"}],
         "observed": {"feature_check": True, "claim_ids": ["c3"], "feature": "pool", "presence": "not_seen",
                      "presence_confidence": 0.8, "looked_at": "aerial photos"}},
    ]
    verdicts = [
        {"claim_id": "c1", "verdict": "contradicted", "claimed": "120 apartments", "found": "150 apartments",
         "source": "county", "reason": "Listing says 120 apartments. County lists 150 apartments; CoStar lists 90.",
         "image": None,
         "confidence": {"score": 60, "band": "medium", "why": "The records differ.", "decided_by": "county",
                        "sources": [{"source": "county", "says": "150 apartments", "agrees_with_listing": False},
                                    {"source": "record", "says": "90 apartments", "agrees_with_listing": False}]}},
        {"claim_id": "c2", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom and a den",
         "source": "scan", "reason": "Listing says 2 bedrooms. The 3D scan shows 1 bedroom and a den.",
         "image": f"/img/{TOKEN}"},
        {"claim_id": "c3", "verdict": "cant_check", "claimed": "Resort pool", "found": "Not seen in the aerial photo",
         "source": None, "reason": "Listing says “Resort pool”. Not seen in the aerial photo.", "image": None},
        {"claim_id": "c4", "verdict": "cant_check", "claimed": "Concierge", "found": "no data", "source": None,
         "reason": "Nothing in our data measures this.", "image": None},
    ]
    ev = [{"type": "run_started", "run_id": RID, "address": ADDR},
          {"type": "claims", "claims": claims},
          {"type": "match", "match": {"status": "matched", "confidence": 0.9, "property_id": "900001",
                                      "name": "The Linden", "record": {"unit_count": 90}, "floorplans": [],
                                      "matterport_model_ids": ["SAMPLEscan01"]}},
          {"type": "step", "agent": "paper", "text": "Looking up the county property record."}]
    ev += [{"type": "evidence", "evidence": e} for e in evidence]
    ev += [{"type": "verdict", "verdict": v} for v in verdicts]
    ev.append({"type": "done", "run_id": RID, "headline": "x", "counts": {},
               "timing": {"total_s": 20.0, "model_s": {"paper": 2.0}}, "harness": {"path": "agent", "direct": []}})
    times = [100.0 + i for i in range(len(ev))]
    return {"run_id": RID, "snapshot": {"address_text": ADDR, "url": "https://listing.invalid/1"},
            "started_at": 100.0, "finished_at": 130.0, "finished": True, "events": ev, "event_times": times,
            "images": {TOKEN: RAW_KNOWN}}


def _card(html, aid):
    m = re.search(rf"<article[^>]*id='{aid}'.*?</article>", html, re.S)
    assert m, aid
    return m.group(0)


def test_link_helpers():
    assert artifact_href(RID, "c1", "county") == f"/artifacts/{RID}#a-c1-county"
    assert artifact_href(RID, "c1", "record") == artifact_href(RID, "c1", "CoStar") == f"/artifacts/{RID}#a-c1-costar"
    assert artifact_href(RID, "c2", "scan").endswith("#a-c2-matterport")
    assert artifact_href(RID, "c2", "Vexcel").endswith("#a-c2-vexcel")


def test_deciding_source():
    run = _run()
    vs = {e["verdict"]["claim_id"]: e["verdict"] for e in run["events"] if e["type"] == "verdict"}
    assert deciding_source(vs["c1"]) == "county"
    assert deciding_source({**vs["c1"], "source": "record"}) == "county"  # decided_by wins
    assert deciding_source(vs["c2"]) == "scan"
    assert deciding_source(vs["c3"]) is None  # couldn't check: nobody decided
    assert deciding_source({"verdict": "confirmed", "source": "record"}) == "record"
    assert deciding_source(None) is None


def test_two_sources_disagree_tags_the_decider():
    html = build_artifacts(_run())
    county = _card(html, "a-c1-county")
    costar = _card(html, "a-c1-costar")
    assert "Decided this" in county and "150 apartments" in county
    assert "Differs" in costar and "Decided this" not in costar and "90" in costar
    # the deciding card comes first in its claim
    claim = re.search(r"<section class=claim id='c-c1'.*?</section>", html, re.S).group(0)
    assert claim.index("a-c1-county") < claim.index("a-c1-costar")
    assert "Confidence 60" in claim and "medium" in claim


def test_anchors_and_artifacts():
    html = build_artifacts(_run())
    ids = re.findall(r"id='(a-[^']+)'", html)
    assert len(ids) == len(set(ids))
    assert {"a-c1-county", "a-c1-costar", "a-c2-matterport", "a-c2-costar", "a-c3-vexcel"} <= set(ids)
    scan = _card(html, "a-c2-matterport")
    assert "Decided this" in scan and "Plan Q - 1 Bed + Den" in scan and "Den" in scan
    assert f"/img/{TOKEN}" in scan  # the raw cache path shown only as its server token
    assert "Artifact library" in html and ADDR in html and "artifacts from" in html
    assert f"href='/report/{RID}'" in html  # back link
    assert "Records checker" in _card(html, "a-c1-county")  # tool trace
    assert "county_lookup()" in _card(html, "a-c1-county")


def test_no_paths_names_contacts_or_free_text():
    html = build_artifacts(_run())
    for bad in ("C:/", "rc_cache", "not_registered", "Jane", "jane@", "SECRETTOKEN", "records.invalid",
                "Barking dog", "owner_name"):
        assert bad not in html, bad
    assert not re.search(r"(?i)\b(fake|lie|lies|scam|fraud)\b", html)
    for src in re.findall(r"<img src='([^']+)'", html):
        assert re.fullmatch(r"/img/[a-f0-9]{16}", src), src
    county = _card(html, "a-c1-county")
    assert "An individual" in county and "12" in county and "Invented County Appraisal District" in county
    assert "2026 county roll" in county


def test_cant_check_collapsed_and_absence_is_not_a_mismatch():
    html = build_artifacts(_run())
    m = re.search(r"<details class=grp id=g-cant>(.*?)</details>", html, re.S)
    assert m and "id='c-c3'" in m.group(1)
    assert "<details class=grp id=g-cant open" not in html
    vex = _card(html, "a-c3-vexcel")
    assert "doesn't match" not in vex.lower()
    # a claim no source spoke to has no card, only the count
    assert "a-c4-" not in html and "1 claim had nothing from any source" in html
    # mismatches come before the collapsed group
    assert html.index("id=g-off") < html.index("id=g-cant")


def test_route_and_404(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    srv.save_run(_run())
    app = FastAPI()
    register(app)
    client = TestClient(app)
    r = client.get(f"/artifacts/{RID}")
    assert r.status_code == 200 and "Artifact library" in r.text and "a-c1-county" in r.text
    assert client.get("/artifacts/20990101-000000-ffffff").status_code == 404
    assert client.get("/artifacts/NOT..valid").status_code == 404
