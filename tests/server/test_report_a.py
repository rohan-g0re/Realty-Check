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
    if n == 60:  # one list row and one panel per mismatch, all in the one view; only the first panel shown
        view = _section(html, "id=mismatch", "</section>")
        assert view.count("role=tab ") == 20 and view.count("role=tabpanel") == 20
        assert view.count(" hidden data-claim=") == 19


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
    """{claim id: that mismatch's panel html} in the 'Listed vs. in reality' view."""
    view = _section(html, "id=mismatch", "</section>")
    parts = re.split(r"(?=<div class='lvr-row md-p)", view)[1:]
    return {re.search(r"data-claim='(\w+)'", p).group(1): p for p in parts}


def _ids(html: str) -> list:
    return re.findall(r"<div class='lvr-row md-p[^']*'[^>]*data-claim='(\w+)'", _section(html, "id=mismatch", "</section>"))


def _tabs(html: str) -> list:
    """(title, tag, small line) of each list row, in order."""
    view = _section(html, "id=mismatch", "</section>")
    return re.findall(r"<span class=md-t>([^<]*)</span><span class=md-g[^>]*>([^<]*)</span>"
                      r"<span class=md-s>([^<]*)</span>", view)


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
    # every contradicted or close claim counts: 3 that don't match plus 2 close ones
    assert "<h1><span>5 things</span><span class=l2>don&#x27;t match.</span></h1>" in hero
    assert "12 Invented Row<br>Inventville, ZZ 00000" in hero
    # no score sent: a contradicted one first, in the page's order
    assert ("<p class=top-line>Biggest difference: the listing says “9-story tower”; the measured roof height "
            "suggests 6 floors.</p>") in hero
    assert "<p>7 claims, held up to CoStar, Matterport, Vexcel and County.</p>" in hero and "close" not in hero
    assert "href='#mismatch'>Show me what" in hero
    assert "strike" not in hero and "class=pair" not in hero and "<img" not in hero


def test_each_mismatch_once_lead_first_and_selected():
    html = render_report(_rich())
    # no score sent: a contradicted one before a close one, then the page's order
    assert _ids(html) == ["c1", "c3", "c4", "c2", "c5"]
    for cid in ("c1", "c2", "c3", "c4", "c5"):
        assert html.count(f"id='claim-{cid}'") == 1
        assert html.count(f"data-claim='{cid}'") == 1
    body = re.sub(r"<style>.*?</style>|<script.*?</script>", "", html, flags=re.S)
    body = body[:body.index("id=cf-field")]  # the heatmap names claims by design
    assert body.count("“9-story tower”") == 6  # the hero line, its list row, its panel, its question (text, Copy, Copy all)
    assert "Biggest difference" not in body[body.index("id=mismatch"):]
    rows = _rows(html)
    # the first panel is the selected one; every other panel is hidden until its list row is picked
    assert re.search(r"<div class='lvr-row md-p lay-v s-high in' id=pan-c1-0 role=tabpanel [^>]*data-claim='c1'", html)
    assert all(" hidden " in rows[c].split(">")[0] for c in ("c3", "c4", "c2", "c5"))
    assert "id=tab-c1-0 aria-controls=pan-c1-0 aria-selected=true tabindex=0" in html
    assert _section(html, "id=mismatch", "</section>").count("aria-selected=true") == 1
    # no confidence at all: no score and no class word ("Doesn't match") on the right, never a made-up number
    assert _tabs(html) == [("Floors", "", "“9-story tower”"),
                           ("Bedrooms · Plan Q", "", "“2 Beds”"),
                           ("Size · Plan Q", "", "“900 sq ft”"),
                           ("Year built", "", "“Built in 2010”"),
                           ("Pool", "", "“Pool”")]
    assert "5 differences, highest confidence first." in html and "Doesn&#x27;t match ·" not in html
    assert "<span class='strike'>“2 Beds”</span>" in rows["c3"] and "lvr-truth>1 bedroom<" in rows["c3"]
    assert "<p class=lvr-why>The 3D scan shows 1 bedroom.</p>" in rows["c3"]
    assert ("<p class=lvr-cite><span class=cite-l>Source</span><a class='cite dec' "
            "href='/artifacts/20990101-000000-aaaaaa#a-c3-matterport' target=_blank rel=noopener>Matterport"
            "<i aria-hidden=true>↗</i></a></p>") in rows["c3"]
    # layout by proof: pictures fill a vertical panel; a figure from a record or a scan title is columnar
    assert "data-proof='pictures'" in rows["c3"] and "data-layout='v'" in rows["c3"]
    assert "<figure class='proof lvr-proof' id=f1 data-proof='pictures'" in rows["c1"] and "/img/0000000000000001" in rows["c1"]
    assert "data-layout='c'" in rows["c4"] and "<div class='pcols'>" in rows["c4"] and "class=vstack" not in rows["c4"]
    assert "<p class=rline>Matterport 3D scan <span>· titled “1BR - 700 SF”</span></p>" in rows["c4"]
    assert "<p class=rline>County records <span>· Year built: 2008</span></p>" in rows["c2"]
    srcs = re.findall(r"<img src='([^']+)'", _section(html, "id=mismatch", "</section>"))
    assert srcs and len(srcs) == len(set(srcs)) and all(re.fullmatch(r"/img/[a-f0-9]{16}", s) for s in srcs)


def test_close_rows_are_faint_never_struck():
    html = render_report(_rich())
    rows = _rows(html)
    # a close one is a mismatch like any other, only fainter: never struck, no separate "close" tag
    assert "strike" not in rows["c2"] and "close, not exact" not in rows["c2"] and "<em>" not in rows["c2"]
    assert "<span class=lvr-said><span class=''>“Built in 2010”</span></span>" in rows["c2"]
    assert "md-i s-low' id=tab-c2-3" in html and "lvr-row md-p lay-c s-low" in rows["c2"]
    assert "md-i s-high' id=tab-c1-0" in html and "lvr-close" not in html
    assert "<span class=lvr-tag></span>" in rows["c2"]  # no confidence sent: no figure, no class word
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
    # one mismatch: no list, the panel alone, full width
    view = _section(html, "id=mismatch", "</section>")
    assert "role=tablist" not in view and "<div class='md rv one" in view and " hidden" not in view
    assert "1 difference." in view


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


def test_only_close_counts_as_a_mismatch_and_leads():
    claims = [_claim("c1", "year_built", 2015, "Year Built 2015"), _claim("c2", "unit_count", 40, "40 homes")]
    verdicts = [_verdict("c1", "close", "built in 2015", "built in 2014", "county", "County records say built in 2014."),
                _verdict("c2", "confirmed", "40 apartments", "40 apartments", "county", "County records say 40.")]
    verdicts[0]["confidence"] = {"score": 40, "band": "low", "decided_by": "county"}
    html = render_report(_run(claims, verdicts))
    assert "<h1><span>1 thing</span><span class=l2>doesn&#x27;t match.</span></h1>" in html
    assert "Biggest difference: the listing says “Year Built 2015”; county records say built in 2014." in html
    assert "class='strike" not in html and "s-low" in html and "href='#mismatch'>Show me what" in html
    assert "Nothing is" not in html and "clearly off" not in html and "<span class=md-g" not in html  # one: no list
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
    assert _ids(html)[:3] == ["c1", "c3", "c4"] and "id=tab-c1-0 aria-controls=pan-c1-0 aria-selected=true" in html
    assert "<p class=lvr-why>Floors change the whole building.</p>" in _rows(html)["c1"]
    top = top_finding(run)
    assert top["claim_id"] == "c1" and top["by"] == "model" and top["why"] == "Floors change the whole building."
    # the pick never moves a row: the order is the score's (none sent here: contradicted first, page order)
    done["harness"] = {"top_mismatch": {"claim_id": "c7", "by": "model"}}
    assert _ids(render_report(run))[0] == "c1"
    done["harness"] = {"top_mismatch": {"claim_id": "c2", "by": "rule"}}
    assert _ids(render_report(run))[:2] == ["c1", "c3"] and top_finding(run)["claim_id"] == "c1"
    # an unkind reason is not shown
    done["harness"] = {"top_mismatch": {"claim_id": "c4", "why": "The size is a lie.", "by": "model"}}
    html = render_report(run)
    assert " lie" not in html.split("id=extra")[0]


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
    assert ("<span class=cite-l>Sources</span><span class='cite dec'>Vexcel</span><span class='cite'>County</span>"
            in rows["c2"]) and "<span class=sc-n>82</span><span class=sc-u>/ 100 · high</span>" in rows["c2"]
    assert ("<span class=cite-l>Source</span><span class='cite dec'>Matterport</span>" in rows["c1"]
            and "<span class=sc-n>64</span><span class=sc-u>/ 100 · medium</span>" in rows["c1"])
    assert "/artifacts/" not in html  # the static copy has no server: plain names, no library link
    assert "strike" not in rows["c6"] and " s-medium" in rows["c6"]  # the close year: a mismatch, mid blue
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
    cite = lambda c: re.search(r"<p class=lvr-cite>(.*?)</p>", rows[c]).group(1)  # noqa: E731
    assert cite("c1").startswith("<span class=cite-l>Source</span><a class='cite dec' "
                                 "href='/artifacts/20990101-000000-aaaaaa#a-c1-county' target=_blank rel=noopener>County")
    assert "CoStar" not in cite("c1").split("also checked")[0] and "#a-c1-costar" in cite("c1").split("also checked")[1]
    assert "#a-c2-county' target=_blank rel=noopener>County" in cite("c2")
    # Vexcel decides (first, emphasised), County backs it; CoStar says the listing's figure: only "also checked"
    assert re.match(r"<span class=cite-l>Sources</span><a class='cite dec' href='[^']*#a-c3-vexcel'[^>]*>Vexcel"
                    r"<i aria-hidden=true>↗</i></a><a class='cite' href='[^']*#a-c3-county'", cite("c3"))
    assert "CoStar" not in cite("c3").split("also checked")[0]


def test_credit_without_confidence_names_only_the_deciding_source():
    run = _credit_run()
    for e in run["events"]:
        if e.get("type") == "verdict":
            e["verdict"].pop("confidence", None)
    view = _section(render_report(run), "id=mismatch", "</section>")
    srcs = re.findall(r"<span class=cite-l>Sources?</span>(.*?)(?:<span class=cite-l>|</p>)", view)
    assert srcs and all(s.count("class='cite") == 1 and "CoStar" not in s for s in srcs)


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


def test_panel_viewer_slot_has_its_own_size():
    """A panel's 3D viewer holds only an absolutely placed frame and still picture, so its slot must stretch to the
    panel's width and fill its height; a shrink-to-fit flex parent left it 0x0 (nothing showed)."""
    from server.report_a import CSS
    assert "figure.lvr-proof{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;align-items:stretch;" in CSS
    assert "figure.lvr-proof .walk{aspect-ratio:auto;max-height:none;min-height:200px;width:100%}" in CSS
    assert "figure.lvr-proof .walk{aspect-ratio:4/3;min-height:260px}" in CSS  # phone: the panel grows instead


def _walk_run():
    """Invented: two scan mismatches whose scan can be walked in 3D, and one county figure."""
    claims = [_claim("c1", "bedrooms", 2, "2 Beds", "Plan Q", "unit"),
              _claim("c2", "area_sqft", 900, "900 sq ft", "Plan Q", "unit"),
              _claim("c3", "year_built", 2010, "Built in 2010")]
    ev = [{"source": "scan", "status": "ok", "summary": "", "detail": "", "images": [],
           "observed": {"plan": "Plan Q", "bedrooms": 1, "area_sqft": 700, "model_id": "SAMPLEscan01",
                        "model_name": "Plan Q - 1BR - 700 SF"}}]
    vs = [_verdict("c1", "contradicted", "2 bedrooms", "1 bedroom", "scan", "The 3D scan shows 1 bedroom."),
          _verdict("c2", "contradicted", "900 sq ft", "700 sq ft", "scan", "The 3D scan measures 700 sq ft."),
          _verdict("c3", "contradicted", "built in 2010", "built in 2001", "county", "County records say 2001.")]
    return _run(claims, vs, ev)


def test_3d_viewers_are_vertical_and_made_only_for_the_selected_panel(monkeypatch):
    import server.tour as tour
    monkeypatch.setattr(tour, "tour_target", lambda run, cid=None: {"model_id": "SAMPLEscan01"})
    html = render_report(_walk_run())
    rows = _rows(html)
    for cid in ("c1", "c2"):
        assert "data-layout='v'" in rows[cid] and "data-proof='walk'" in rows[cid]
        assert "<div class=walk data-src='/tour/" in rows[cid]
    assert "data-layout='c'" in rows["c3"]
    assert "<iframe" not in html  # frames are made by the page script, one at a time
    from server.report_a import SCRIPT
    assert "return !w.closest('.md-p[hidden]')" in SCRIPT  # never a viewer for a panel that is not selected
    assert "window.RC3D.unmount(w)" in SCRIPT and "ArrowDown" in SCRIPT and "hashchange" in SCRIPT
    static = render_report(_walk_run(), live_3d=False)
    assert "data-src=" not in _section(static, "id=mismatch", "</section>")


def test_confidence_score_in_list_and_panel_never_invented():
    run = _credit_run()
    for e in run["events"]:
        if e.get("type") == "verdict" and e["verdict"]["claim_id"] == "c3":
            e["verdict"]["confidence"]["score"] = 82
            e["verdict"]["confidence"]["why"] = "Vexcel measures the roof height directly."
        if e.get("type") == "verdict" and e["verdict"]["claim_id"] == "c2":
            e["verdict"]["confidence"].pop("score")
    html = render_report(run)
    rows = _rows(html)
    # the panel: the score as a big figure "82 / 100 · high", the why kept under the citations
    assert ("<span class=lvr-tag aria-label='Confidence 82 / 100 · high'><span class=sc-n>82</span>"
            "<span class=sc-u>/ 100 · high</span></span>") in rows["c3"]
    assert "<p class=lvr-cwhy>Vexcel measures the roof height directly.</p>" in rows["c3"]
    assert "<span class=sc-n>60</span><span class=sc-u>/ 100 · medium</span>" in rows["c1"]
    assert "<span class=sc-w>Low</span>" in rows["c2"] and "/ 100" not in rows["c2"]  # word only
    # the list: the score is the row's right-hand element, no class word; the level alone when no score is sent
    tabs = re.findall(r"<span class=md-t>([^<]*)</span><span class=md-g[^>]*>(.*?)</span><span class=md-s>",
                      _section(html, "id=mismatch", "</section>"))
    assert tabs == [("Floors", "<span class=sc-n>82</span><span class=sc-u>/ 100 · high</span>"),
                    ("Homes", "<span class=sc-n>60</span><span class=sc-u>/ 100 · medium</span>"),
                    ("Year built", "<span class=sc-w>Low</span>")]  # highest score first, no score last
    assert "s-high' id=tab-c3-0" in html and "s-medium' id=tab-c1-1" in html and "s-low' id=tab-c2-2" in html
    assert "Doesn&#x27;t match" not in _section(html, "id=mismatch", "</section>")
    plain = render_report(_rich())  # no confidence at all: no number anywhere in the view
    pv = _section(plain, "id=mismatch", "</section>")
    assert "sc-n" not in pv and "sc-w" not in pv and "/ 100" not in pv


def test_label_questions_read_as_sentences():
    claims = [_claim("c1", "bedrooms", 0, "Studio", "Plan A", "unit"),
              _claim("c2", "bathrooms", 2, "2 Baths", "Plan B", "unit")]
    vs = [_verdict("c1", "cant_check", "0 bedrooms", "no data", None, "Nothing in our data measures this."),
          _verdict("c2", "cant_check", "2 bathrooms", "no data", None, "Nothing in our data measures this.")]
    html = render_report(_run(claims, vs))
    qs = re.findall(r"class='btn ghost copy' data-text='([^']+)'", html)
    assert not any("Studio for Plan A" in q for q in qs), qs
    assert any(re.fullmatch(r"Can you confirm in writing that Plan A (is a studio|has .Studio.)\?", q) for q in qs), qs
    assert "Can you confirm in writing that Plan B has “2 Baths”?" in qs, qs


def test_not_in_listing_trims_instead_of_dropping():
    top = [{"label": "Availability and manager", "value": "24 of 215 homes available; managed by Invented Co",
            "why": "Several homes are open now.", "sources": ["CoStar"], "image": None, "fact_ids": []},
           {"label": "Nearby 311 requests", "value": "43 since 2026-01-01 within 100 m", "why": "A count only.",
            "sources": ["County"], "image": None, "fact_ids": []},
           {"label": "Elevator", "value": "Walk-up", "why": "", "sources": ["CoStar"], "image": None, "fact_ids": []},
           {"label": "Pets", "value": "Cats and dogs allowed", "why": "", "sources": ["CoStar"], "image": None,
            "fact_ids": []},
           {"label": "Seen from above", "value": "Sport court", "why": "", "sources": ["Vexcel"], "image": None,
            "fact_ids": []}]
    nil = _nil(render_report(_facts_run(FACTS, {"top_facts": top})))
    assert nil.count("class='nil-row") == 5
    assert "<p class=nil-k><b>1</b>Homes available</p><p class=nil-v>24 of 215 homes available</p>" in nil
    assert "Invented Co" not in nil and "managed" not in nil.lower()
    assert "43 since 2026-01-01 within 100 m" in nil


def test_long_cant_check_reasons_are_clamped():
    from server.report_a import CSS
    assert ".reasons p{margin:0 0 4px;font-size:15px;color:var(--mute);display:-webkit-box;-webkit-line-clamp:2;" in CSS


def test_deciding_source_links_to_the_artifact_library():
    run = _credit_run()
    html = render_report(run)
    rows = _rows(html)
    # the deciding source first, linked to its artifact; a source that said something else only as "also checked"
    assert ("<span class=cite-l>Source</span><a class='cite dec' href='/artifacts/20990101-000000-aaaaaa#a-c1-county' "
            "target=_blank rel=noopener>County<i aria-hidden=true>↗</i></a>") in rows["c1"]
    assert ("<span class=cite-l>also checked</span><a class='cite' href='/artifacts/20990101-000000-aaaaaa#a-c1-costar' "
            "target=_blank rel=noopener>CoStar<i aria-hidden=true>↗</i></a>") in rows["c1"]
    # every link into the library opens in a new tab
    view = _section(html, "id=mismatch", "</section>")
    assert view.count("href='/artifacts/") == view.count("target=_blank rel=noopener>")
    sources = _section(html, "id=sources", "</section>")
    assert "<a href='/artifacts/20990101-000000-aaaaaa' target=_blank rel=noopener>Open the artifact library" in sources
    static = render_report(run, live_3d=False)
    assert "/artifacts/" not in static
    assert "<span class=cite-l>Source</span><span class='cite dec'>County</span>" in _rows(static)["c1"]
    run["run_id"] = "../../x"  # an id that is not plain never makes a link
    assert "/artifacts/" not in render_report(run)


def test_every_mismatch_gets_a_question_even_past_five():
    claims = [_claim(f"c{i}", "year_built", 2000 + i, f"Built in {2000 + i}") for i in range(7)]
    claims.append(_claim("c9", "amenity", "concierge", "Concierge"))
    verdicts = [_verdict(f"c{i}", "close" if i % 2 else "contradicted", f"built in {2000 + i}", f"built in {1990 + i}",
                         "county", "County says otherwise.") for i in range(7)]
    verdicts.append(_verdict("c9", "cant_check", "concierge", "no data", None, "Nothing in our data measures this."))
    html = render_report(_run(claims, verdicts))
    qs = re.findall(r"class='btn ghost copy' data-text='([^']+)'", html)
    assert len(qs) == 7 and all(any(f"{1990 + i}" in q for q in qs) for i in range(7))  # no couldn't-check past five
    assert "<h1><span>7 things</span>" in html


# ---------- source preview cards (citations) ----------

def _cited_run():
    """Invented: _credit_run with harness citations on c1, ids and a query link in them."""
    run = _credit_run()
    v = next(e["verdict"] for e in run["events"] if e["type"] == "verdict" and e["verdict"]["claim_id"] == "c1")
    v["citations"] = [
        {"source": "County", "read": "Units field", "where": "county parcel no. 900001-0002, building 1",
         "date": "2026-03-01", "value": "52 apartments", "image": IMG[2],
         "link": "https://records.invalid/search?parcel=900001&token=abc", "role": "decided"},
        {"source": "CoStar", "read": "Unit count field", "where": "CoStar property 900002, field Units",
         "date": None, "value": "31 apartments", "image": "C:/private/rc_cache/x.png", "link": None, "role": "differs"},
        {"source": "Matterport", "read": "Scan title", "where": "scan 'Plan Q' (SAMPLEscan01)", "date": None,
         "value": "a scam listing", "image": None, "link": None, "role": "agrees"}]
    return run


def test_source_chip_opens_a_preview_card():
    rows = _rows(render_report(_cited_run()))
    cite = re.search(r"<p class=lvr-cite>(.*?)</p>", rows["c1"]).group(1)
    # the deciding chip: still a link to its artifact (works without script), now also controls a card
    assert re.search(r"<a href='/artifacts/20990101-000000-aaaaaa#a-c1-county' target=_blank rel=noopener "
                     r"class='cite dec' data-cc='(cc-c1-0-county)' aria-controls='\1' aria-expanded=false>County", cite)
    card = re.search(r"<span class='cc r-decided' id='cc-c1-0-county' role=dialog [^>]*hidden>(.*?)</a></span>", cite).group(1)
    assert "<b>County</b><em>Decided this</em>" in card
    assert "<span class=cc-v>52 apartments</span>" in card and "<span class=cc-read>Units field</span>" in card
    assert f"<img class=cc-img src='{IMG[2]}'" in card
    assert "2026-03-01" in card and "building 1" in card
    assert ("<a class=cc-go href='/artifacts/20990101-000000-aaaaaa#a-c1-county' target=_blank rel=noopener>"
            "Open in artifact library") in card
    # the also-checked CoStar chip: its own card, the role it had
    assert "<span class='cc r-differs' id='cc-c1-0-record'" in cite and "<em>Differs</em>" in cite
    assert "<span class=cc-v>31 apartments</span>" in cite


def test_preview_card_never_shows_ids_links_paths_or_unkind_words():
    html = render_report(_cited_run())
    view = _section(html, "id=mismatch", "</section>")
    for bad in ("900001", "900002", "records.invalid", "token=", "C:/private", "rc_cache", "SAMPLEscan01", "scam"):
        assert bad not in view, bad
    assert "CoStar property record" in view  # the id is gone, the words stay


def test_old_runs_without_citations_keep_plain_chips():
    html = render_report(_credit_run())
    view = _section(html, "id=mismatch", "</section>")
    assert "data-cc=" not in view and "class='cc " not in view
    static = _rows(render_report(_cited_run(), live_3d=False))["c1"]
    # the static copy: no library link anywhere, the card still opens from a button
    assert "/artifacts/" not in static
    assert "<button type=button class='cite dec' data-cc='cc-c1-0-county'" in static and "cc-go" not in static


def test_preview_card_script_and_style_are_inline():
    from server.report_a import CSS, SCRIPT
    assert "[data-cc]" in SCRIPT and "aria-expanded" in SCRIPT and "Escape" in SCRIPT
    assert ".cc{position:fixed" in CSS and "@media (max-width:520px){\n.cc{" in CSS
    assert ".cc.on .cc-v,.cc.on .cc-read,.cc.on .cc-w,.cc.on .cc-img{animation:none}" in CSS  # reduced motion
