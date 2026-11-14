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


def test_no_picture_finding_and_empty_run():
    claims = [_claim("c1", "amenity", "rooftop", "Rooftop deck")]
    verdicts = [_verdict("c1", "contradicted", "rooftop deck", "no rooftop deck", "record", "The record says none.")]
    html = render_report(_run(claims, verdicts))
    assert "id='claim-c1'" in html and "data-proof='none'" not in html
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


def test_dead_pictures_leave_no_broken_slot(tmp_path):
    run = _rich()
    run["images"] = {p[5:]: str(tmp_path / "gone.jpg") for p in IMG}
    html = render_report(run)
    assert "<img src='/img/" not in html and "data-proof='pictures'" not in html
    assert "Realty Check" in html


def _section(html: str, sid: str, end: str) -> str:
    return html[html.index(sid):html.index(end, html.index(sid))]


SECTIONS = ("<section class=hero", "id=mismatch", "id=extra", "id=questions", "id=heatmap", "id=sources")


def _rows(html: str) -> dict:
    view = _section(html, "id=mismatch", "</section>")
    return dict(re.findall(r"data-claim='(\w+)'>(.*?)</li>", view))


def _ids(html: str) -> list:
    return re.findall(r"<li class='lvr-row[^']*'[^>]*data-claim='(\w+)'", _section(html, "id=mismatch", "</section>"))


def _hero_html(html: str) -> str:
    return _section(html, "<section class=hero", "</section>")


def test_five_sections_in_order_and_nothing_else():
    html = render_report(_rich())
    pos = [html.index(s) for s in SECTIONS]
    assert pos == sorted(pos)
    # the old blocks are gone: no strike-off hero, no finding modules, no topic dump, no separate close list
    for gone in ("id=top-mismatch", "<article", "class=topics", "class='fact", "id=near", "class=findings",
                 "Show all 46 facts", "the listing left out."):
        assert gone not in html, gone
    # matches and couldn't-check only behind one collapsed disclosure after the sources, before the footer
    assert html.index("id=sources") < html.index("<details class=allck>") < html.index("<footer")
    assert html.index("<details class=allck>") < html.index("id=match") < html.index("id=cant-verify") < html.index("<footer")
    for word in ("CoStar", "Matterport", "Vexcel", "County"):
        assert word in html
    for bad in ("C:/", "C:\\", "rc_cache", "scam", "Fake", " lie ", "Reality Check", "Not true"):
        assert bad not in html, bad


def test_hero_is_the_count_with_one_plain_line_and_no_strike():
    html = render_report(_rich())
    hero = _hero_html(html)
    assert "<h1><span>3 things</span><span class=l2>don&#x27;t match.</span></h1>" in hero
    assert "12 Invented Row<br>Inventville, ZZ 00000" in hero
    assert ("<p class=top-line>Biggest difference: the listing says “2 Beds” for Plan Q; the 3D scan shows "
            "1 bedroom.</p>") in hero
    assert "7 claims, held up to CoStar, Matterport, Vexcel and County. 2 more are close, not exact." in hero
    assert "href='#mismatch'>Show me what" in hero
    assert "strike" not in hero and "class=pair" not in hero and "<img" not in hero


def test_each_mismatch_once_lead_first_and_largest():
    html = render_report(_rich())
    assert _ids(html) == ["c3", "c4", "c1", "c2", "c5"]  # beds, size, floors; then the close ones
    for cid in ("c1", "c2", "c3", "c4", "c5"):
        assert html.count(f"id='claim-{cid}'") == 1
        assert html.count(f"data-claim='{cid}'") == 1
    body = re.sub(r"<style>.*?</style>|<script.*?</script>", "", html, flags=re.S)
    body = body[:body.index("id=cf-field")]  # the heatmap names claims by design
    assert body.count("“9-story tower”") == 4  # its row, and its question (text, Copy, Copy all)
    assert "Biggest difference" not in body[body.index("id=mismatch"):]
    rows = _rows(html)
    assert html.count("lvr-row rv lvr-lead") == 1 and "lvr-lead' style='--d:0ms' data-claim='c3'" in html
    assert "<span class='strike'>“2 Beds”</span>" in rows["c3"] and "lvr-truth>1 bedroom<" in rows["c3"]
    assert "<p class=lvr-why>The 3D scan shows 1 bedroom.</p>" in rows["c3"]
    assert "<span class=lvr-src>Matterport</span>" in rows["c3"]
    # proof inline: the lead row holds the full slot, others a picture or one record line
    assert "<figure class='proof lvr-proof' id=f1 data-proof='pictures'" in rows["c3"]
    assert "data-proof='pictures'" in rows["c1"] and "/img/0000000000000001" in rows["c1"]
    assert "<p class=rline>Matterport 3D scan <span>· titled “1BR - 700 SF”</span></p>" in rows["c4"]
    assert "<p class=rline>County records <span>· Year built: 2008</span></p>" in rows["c2"]
    srcs = re.findall(r"<img src='([^']+)'", _section(html, "id=mismatch", "</section>"))
    assert srcs and len(srcs) == len(set(srcs)) and all(re.fullmatch(r"/img/[a-f0-9]{16}", s) for s in srcs)


def test_close_rows_are_soft_never_struck():
    rows = _rows(render_report(_rich()))
    assert "strike" not in rows["c2"] and "<em>close, not exact</em>" in rows["c2"]
    assert "<span class=lvr-said><span class=''>“Built in 2010”</span></span>" in rows["c2"]
    assert "Not seen in the photos" in rows["c5"] and "strike" not in rows["c5"]  # a photo look: never struck


@pytest.mark.parametrize("claim,verdict,said,truth", [
    (_claim("c1", "bedrooms", 2, "2 Beds", "Plan A", "unit"),
     _verdict("c1", "contradicted", "2 bedrooms", "1 bedroom and a den", "scan", "The scan shows 1 bedroom."),
     "“2 Beds”", "1 bedroom and a den"),
    (_claim("c1", "bathrooms", 2, "2 Baths"), _verdict("c1", "contradicted", "2 bathrooms", "1 bathroom", "record", "x."),
     "“2 Baths”", "1 bathroom"),
    (_claim("c1", "area_sqft", 900, "900 sq ft"), _verdict("c1", "contradicted", "900 sq ft", "700 sq ft", "scan", "x."),
     "“900 sq ft”", "700 sq ft"),
    (_claim("c1", "year_built", 2010, "Built in 2010"),
     _verdict("c1", "contradicted", "built in 2010", "built in 1990", "county", "x."), "“Built in 2010”", "Built in 1990"),
    (_claim("c1", "amenity", "pool", "Pool"),
     _verdict("c1", "contradicted", "pool", "no pool in the building record", "record", "x."), "“Pool”",
     "No pool in the building record"),
])
def test_lead_row_strike_for_every_kind(claim, verdict, said, truth):
    html = render_report(_run([claim], [verdict]))
    row = _rows(html)["c1"]
    assert f"<span class='strike'>{said}</span>" in row and f"<span class=lvr-truth>{truth}</span>" in row
    assert "<h1><span>1 thing</span><span class=l2>doesn&#x27;t match.</span></h1>" in html


def test_one_finding_record_proof_and_no_fill_in():
    claims = [_claim("c1", "year_built", 2010, "Built in 2010")]
    verdicts = [_verdict("c1", "contradicted", "built in 2010", "built in 2001", "record",
                         "The building record says built in 2001.")]
    run = _run(claims, verdicts, [{"source": "record", "status": "ok", "summary": "", "detail": "", "images": [],
                                   "observed": {"year_built": 2001}}])
    html = render_report(run)
    assert "data-proof='record'" in _rows(html)["c1"]
    src = html[html.index("id=sources"):html.index("id=checked")]
    assert src.count("Not available for this address.") == 3  # scan, aerial, county
    assert "Our sources have nothing to add beyond the listing." in _section(html, "id=extra", "</section>")
    assert "<iframe" not in html and ">3D walk<" not in html


def test_zero_findings_is_calm():
    run = _rich()
    run["events"] = [e for e in run["events"] if not (e["type"] == "verdict" and e["verdict"]["verdict"] in
                                                       ("contradicted", "close"))]
    html = render_report(run)
    assert "Everything we could" in html and "check matches." in html
    assert "class='lvr-row" not in html and "Show me what" not in html and "href='#checked'>See what we checked" in html
    assert "<span id=mismatch></span>" in html and "class='strike" not in html


def test_only_close_never_sounds_like_a_mismatch():
    claims = [_claim("c1", "year_built", 2015, "Year Built 2015"), _claim("c2", "unit_count", 40, "40 homes")]
    verdicts = [_verdict("c1", "close", "built in 2015", "built in 2014", "county", "County records say built in 2014."),
                _verdict("c2", "confirmed", "40 apartments", "40 apartments", "county", "County records say 40.")]
    html = render_report(_run(claims, verdicts))
    assert "Nothing is" in html and "clearly off." in html
    assert "match." not in html.split("</h1>")[0] and "Biggest difference" not in html
    assert "class='strike" not in html and "lvr-close" in html and "href='#mismatch'>See what" in html
    assert "id='claim-c1'" in html
    for anchor in ("id=mismatch", "id=match", "id=cant-verify", "id=extra"):
        assert anchor in html


def test_recorded_pick_leads_hero_line_view_and_top():
    from server.report_a import top_finding
    run = _rich()
    done = run["events"][-1]
    done["harness"] = {"top_mismatch": {"claim_id": "c1", "why": "Floors change the whole building.", "by": "model"}}
    html = render_report(run)
    assert "Biggest difference: the listing says “9-story tower”" in _hero_html(html)
    assert _ids(html)[:3] == ["c1", "c3", "c4"]
    assert "<p class=lvr-why>Floors change the whole building.</p>" in _rows(html)["c1"]
    top = top_finding(run)
    assert top["claim_id"] == "c1" and top["by"] == "model" and top["why"] == "Floors change the whole building."
    # a pick that is not a mismatch, a close one over hard ones, or an unkind reason is not followed / not shown
    for pick in ({"claim_id": "c7", "by": "model"}, {"claim_id": "c2", "by": "model"}):
        done["harness"] = {"top_mismatch": pick}
        assert _ids(render_report(run))[0] == "c3"
    done["harness"] = {"top_mismatch": {"claim_id": "c4", "why": "The size is a lie.", "by": "model"}}
    html = render_report(run)
    assert _ids(html)[0] == "c4" and " lie" not in html.split("id=extra")[0]


def test_listed_vs_reality_on_the_invented_sample_run(monkeypatch, tmp_path):
    import server.fake_run as fake
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setattr(fake, "GAP", (0, 0))
    events = []
    fake.run_fake({"address_text": "418 Linden Grove Ave, Houston, TX 77006", "run_id": "sample"}, events.append)
    run = {"run_id": "sample", "finished": True, "events": events, "images": {},
           "snapshot": {"address_text": "418 Linden Grove Ave, Houston, TX 77006", "url": "", "title": "sample"}}
    html = render_report(run, live_3d=False)
    rows = _rows(html)
    assert set(rows) == {"c1", "c2", "c3", "c6"}  # the three hard ones and the close year; no match, no couldn't-check
    assert "Vexcel + County · high confidence" in rows["c2"] and "Matterport · medium confidence" in rows["c1"]
    assert "close, not exact" in rows["c6"]
    assert not re.search(r"\b(fake|lie|lies|scam)\b", re.sub(r"<style>.*?</style>", "", html), re.I)


def _credit_run():
    """Invented: the found value from the county; the building record gives its own, different figure."""
    claims = [_claim("c1", "unit_count", 40, "The community has 40 homes"), _claim("c2", "year_built", 2010, "Built in 2010"),
              _claim("c3", "stories", 9, "9-story tower")]
    def conf(by, band, *rows):
        return {"score": 60, "band": band, "decided_by": by, "why": "x",
                "sources": [{"source": s, "says": says, "weight": 0.7, "agrees_with_listing": ag} for s, says, ag in rows]}
    vs = [_verdict("c1", "contradicted", "40 apartments", "52 apartments", "county", "County records say 52."),
          _verdict("c2", "close", "built in 2010", "built in 2011", "county", "County records say built in 2011."),
          _verdict("c3", "contradicted", "9 floors", "6 floors", "aerial", "About 6 floors.")]
    vs[0]["confidence"] = conf("county", "medium", ("county", "52 apartments", False), ("record", "31 apartments", False))
    vs[1]["confidence"] = conf("county", "low", ("county", "built in 2011", False), ("record", "built in 2012", False))
    vs[2]["confidence"] = conf("aerial", "high", ("aerial", "about 6 floors", False), ("county", "6 floors", False),
                               ("record", "9 floors", True))
    return _run(claims, vs)


def test_credit_names_only_sources_that_support_the_found_value():
    html = render_report(_credit_run())
    rows = _rows(html)
    # the record says 31 / 2012 (something else): never credited; one that says the same figure is
    assert "<span class=lvr-src>County · medium confidence</span>" in rows["c1"] and "CoStar" not in rows["c1"]
    assert "<span class=lvr-src>County · low confidence</span>" in rows["c2"]
    assert "<span class=lvr-src>Vexcel + County · high confidence</span>" in rows["c3"] and "CoStar" not in rows["c3"]
    assert "<i class='hit'></i><i class='on'></i></span>" in rows["c3"]  # Vexcel decides, County backs it


def test_credit_without_confidence_names_only_the_deciding_source():
    run = _credit_run()
    for e in run["events"]:
        if e.get("type") == "verdict":
            e["verdict"].pop("confidence", None)
    view = _section(render_report(run), "id=mismatch", "</section>")
    srcs = re.findall(r"<span class=lvr-src>([^<]*)</span>", view)
    assert srcs and all("+" not in s and "CoStar" not in s for s in srcs)


# ---------- 3 "Not in the listing" ----------

def _facts_run(facts, harness=None, verdicts=None, claims=None):
    claims = claims or [_claim("c1", "bedrooms", 2, "2 Beds", None, "unit")]
    verdicts = verdicts or [_verdict("c1", "contradicted", "2 bedrooms", "1 bedroom", "record", "The record says 1.")]
    run = _run(claims, verdicts, facts=facts)
    if harness is not None:
        run["events"][-1]["harness"] = harness
    return run


def _fact(fid, topic, label, value, source="record", image=None):
    return {"id": fid, "topic": topic, "label": label, "value": value, "source": source, "note": "", "image": image}


FACTS = [_fact("class", "Building", "Building class", "B"), _fact("rating", "Building", "CoStar building rating", "4 stars"),
         _fact("lot", "Building", "Lot size", "1.2 acres"), _fact("county-parcel", "Records", "County parcel number", "123-456"),
         _fact("owner", "Building", "Owner", "Someone Invented"),
         _fact("manager", "Building", "Management company", "Invented Mgmt"),
         _fact("elevators", "Building", "Elevator", "Walk-up building (no elevator on record)"),
         _fact("available", "Rent", "Homes available to rent", "6 of 100"),
         _fact("vacancy", "Rent", "Vacancy", "5% of homes empty"),
         _fact("metering", "Rent", "Utility meters", "Each home has its own meters"),
         _fact("county-311", "Records", "311 service requests nearby", "12", "county"),
         _fact("aerial-view", "Around it", "The block from above", "Top-down photo", "aerial", IMG[4]),
         _fact("aerial-features", "Around it", "Seen from above", "Sport court", "aerial"),
         _fact("roof-height", "Building", "Measured roof height", "23.1 m, about 6 floors", "aerial"),
         _fact("pets", "Rules", "Pets", "Cats and dogs allowed")]


def _nil(html):
    return _section(html, "id=extra", "</section>")


def _no_picker(monkeypatch, result=None):
    import sys
    import types
    mod = types.ModuleType("harness.top_facts")

    def pick(*a, **k):
        if result is None:
            raise RuntimeError("not there")
        return result
    mod.pick_top_facts = pick
    monkeypatch.setitem(sys.modules, "harness.top_facts", mod)


def test_not_in_listing_uses_the_runs_top_facts():
    top = [{"label": "Elevator", "value": "Walk-up, no elevator", "why": "Every trip is by the stairs.",
            "sources": ["CoStar"], "image": "C:/private/raw.png", "topic": "Building", "by": "rule",
            "fact_ids": ["elevators"]},
           {"label": "Seen from above", "value": "Sport court", "why": "An amenity the listing skips.",
            "sources": ["Vexcel"], "image": "C:/private/raw.png", "topic": "Around it", "by": "rule",
            "fact_ids": ["aerial-features"]},
           {"label": "County parcel number", "value": "123", "why": "", "sources": ["County"], "image": None},
           {"label": "311 requests", "value": "Loud music at night", "why": "", "sources": ["County"], "image": None}]
    html = render_report(_facts_run(FACTS, {"top_facts": top}))
    nil = _nil(html)
    assert "Not in the listing." in nil and nil.count("class='nil-row") == 2
    assert "Walk-up, no elevator" in nil and "Every trip is by the stairs." in nil and "CoStar" in nil
    assert "Sport court" in nil and f"<img src='{IMG[4]}'" in nil  # the aerial photo, from the fill_in facts
    assert "C:/private" not in html and "parcel" not in nil and "Loud music" not in html


def test_not_in_listing_fallback_rule_for_old_runs(monkeypatch):
    _no_picker(monkeypatch)
    nil = _nil(render_report(_facts_run(FACTS)))
    labels = re.findall(r"<p class=nil-k><b>\d</b>([^<]*)</p>", nil)
    assert labels == ["Elevator", "Homes available", "Utility meters", "311 service requests nearby",
                      "Measured roof height"]
    assert "6 of 100 · 5% of homes empty" in nil and f"<img src='{IMG[4]}'" in nil
    for bad in ("Building class", "rating", "Lot size", "parcel", "Someone Invented", "Invented Mgmt"):
        assert bad not in nil, bad


def test_not_in_listing_never_repeats_a_mismatch(monkeypatch):
    _no_picker(monkeypatch, [])
    claims = [_claim("c1", "stories", 9, "9-story tower")]
    vs = [_verdict("c1", "contradicted", "9 floors", "6 floors", "aerial", "About 6 floors.")]
    nil = _nil(render_report(_facts_run(FACTS, verdicts=vs, claims=claims)))
    assert "Measured roof height" not in nil and nil.count("class='nil-row") == 5


def test_report_never_asks_a_model_for_top_facts(monkeypatch):
    import harness.top_facts as tf
    seen = {}
    real = tf.pick_top_facts

    def spy(*a, **k):
        seen["llm"] = k.get("llm")
        return real(*a, **k)
    monkeypatch.setattr(tf, "pick_top_facts", spy)
    monkeypatch.setattr(tf, "_default_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    html = render_report(_facts_run(FACTS))
    assert seen["llm"] is not None and "class='nil-row" in _nil(html)


def test_questions_and_checked_disclosure():
    html = render_report(_rich())
    qs = re.findall(r"<button type=button class='btn ghost copy' data-text='([^']+)'", html)
    assert 1 <= len(qs) <= 5 and "Copy all" in html
    assert all("fake" not in q.lower() and "lie" not in q.lower().split() for q in qs)
    ck = _section(html, "id=checked", "</section>")
    assert "Everything we checked" in ck and "“40 homes”" in ck and "Concierge" in ck
    assert "<details class=allck>" in ck


def test_no_img_without_a_src():
    for html in (render_report(_rich()), render_report({"snapshot": {}, "events": []})):
        assert not re.search(r"<img(?![^>]*\ssrc=)[^>]*>", html)


def test_lead_viewer_slot_has_its_own_size():
    """The lead row's 3D viewer holds only an absolutely placed frame and still picture, so its slot must take the
    proof column's full width and a height of its own; a shrink-to-fit flex parent left it 0x0 (nothing showed)."""
    from server.report_a import CSS
    assert "figure.lvr-proof{display:flex;flex-direction:column;align-items:stretch;" in CSS
    assert ".lvr-lead .walk{width:100%;aspect-ratio:16/9;min-height:360px}" in CSS
    assert ".walk,.lvr-lead .walk{min-height:280px}" in CSS  # the phone rule still reaches the lead viewer
