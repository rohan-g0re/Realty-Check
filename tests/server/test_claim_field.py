"""Claim field: the claim x source mapping and its HTML fragment. Invented data only."""
import json
import re

from server.claim_field import SOURCES, claim_field, claim_field_section, clean_observed, compare_table

IMG = "/img/0123456789abcdef"


def ev(source, observed, status="ok", summary="", images=None):
    return {"type": "evidence", "evidence": {"source": source, "status": status, "summary": summary,
                                             "observed": observed, "images": images or [], "detail": ""}}


def claim(cid, kind, value, quote, scope="building", label=None, checkable=True):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label,
            "quote": quote, "checkable": checkable}


def verdict(cid, v, claimed="", found="", source=None, reason=""):
    return {"type": "verdict", "verdict": {"claim_id": cid, "verdict": v, "claimed": claimed, "found": found,
                                           "source": source, "reason": reason, "image": None}}


def run_of(claims, evidence, verdicts):
    return {"run_id": "test-run", "events": [{"type": "claims", "claims": claims}, *evidence, *verdicts]}


def state(field, cid, src):
    c = next(c for c in field["claims"] if c["id"] == cid)
    return c["cells"][src]["state"]


BASE = run_of(
    [claim("c1", "stories", 5, "5-story building"),
     claim("c2", "year_built", 2001, "Built in 2001"),
     claim("c3", "bedrooms", 2, "2 Beds", scope="unit", label="Plan Q"),
     claim("c4", "amenity", "pool", "Sparkling pool"),
     claim("c5", "other", "vibes", "Great vibes", checkable=False)],
    [ev("record", {"stories": 5, "year_built": 2001, "unit_count": 40}, summary="Record: 5 floors."),
     ev("aerial", {"height_m": 23.0, "stories": 6}, summary="Roof is 23 m: about 6 floors."),
     ev("aerial", {"photo_floor_count": 5, "stories": 5, "illustrative": True}, images=[{"path": IMG, "caption": "x"}]),
     ev("county", {"stories": 6, "year_built": 2000}, summary="County: 6 floors, built 2000."),
     ev("scan", {"plan": "Plan Q", "bedrooms": 1, "room_labels": ["Bedroom", "Den"]},
        images=[{"path": IMG, "caption": "Floor plan from the 3D scan"}]),
     ev("aerial", {"feature_check": True, "claim_ids": ["c4"], "feature": "pool", "presence": "seen",
                   "presence_confidence": 0.95}, images=[{"path": IMG, "caption": "Top-down"}]),
     ev("scan", {"stories": 9}, status="error")],
    [verdict("c1", "contradicted", "5 floors", "6 floors", "aerial", "The measured roof height means about 6."),
     verdict("c2", "close", "built in 2001", "built in 2000", "county"),
     verdict("c3", "cant_check", "2 bedrooms", "room labels only"),
     verdict("c4", "confirmed", "pool", "Visible in the aerial photo", "aerial"),
     verdict("c5", "cant_check", "", "nothing to measure")])


def test_rows_in_order_and_counts():
    f = claim_field(BASE)
    assert [s["name"] for s in f["sources"]] == ["CoStar", "Matterport", "Vexcel", "County"]
    counts = {s["key"]: s["count"] for s in f["sources"]}
    assert counts == {"record": 2, "scan": 1, "aerial": 2, "county": 2}


def test_each_source_judged_on_its_own():
    f = claim_field(BASE)
    # overall contradicted, but the building record agrees with the listing
    assert state(f, "c1", "record") == "match"
    assert state(f, "c1", "aerial") == "contra"
    assert state(f, "c1", "county") == "contra"
    assert state(f, "c2", "record") == "match"
    assert state(f, "c2", "county") == "close"  # a year off: close, kept apart from contra in the data


def test_illustrative_and_error_evidence_never_fill_a_cell():
    f = claim_field(BASE)
    assert state(f, "c1", "scan") == "none"  # scan error + building claim
    run = run_of([claim("c1", "stories", 5, "5 floors")],
                 [ev("aerial", {"stories": 5, "illustrative": True, "photo_floor_count": 5})], [])
    assert state(claim_field(run), "c1", "aerial") == "none"


def test_room_labels_alone_are_hollow_not_agreement():
    f = claim_field(BASE)
    c3 = f["claims"][2]["cells"]["scan"]
    assert c3["state"] == "data"
    assert c3["facts"][0] == ["Bedrooms", "1"]
    assert c3["image"] == IMG


def test_picture_checks_and_marketing_words():
    f = claim_field(BASE)
    assert state(f, "c4", "aerial") == "match"
    assert all(state(f, "c5", k) == "none" for k, _ in SOURCES)


def test_highlight_follows_overall_verdict():
    f = claim_field(BASE)
    assert [c["highlight"] for c in f["claims"]] == [True, True, False, False, False]


def test_missing_source_row_stays():
    run = run_of([claim("c1", "stories", 5, "5 floors")], [ev("record", {"stories": 5})],
                 [verdict("c1", "confirmed")])
    f = claim_field(run)
    assert [s["count"] for s in f["sources"]] == [1, 0, 0, 0]
    assert len(f["sources"]) == 4


def test_no_people_contacts_free_text_or_paths():
    obs = {"stories": 5, "leasing_contact": "Jane Example", "phone": "555-010-0199", "note": "call me",
           "owner_name": "Pat Example", "management": "Example Homes LLC", "x": "write to a@example.com",
           "photo": "C:/somewhere/rc_cache/a.jpg", "source_url": "https://example.org/q?key=SECRET",
           "complaints_311": [{"text": "neighbour Sam at 555 010 0100"}], "model_name": "2 BR - 900 SF"}
    out = clean_observed(obs)
    blob = json.dumps(out)
    for bad in ("Jane", "555", "call me", "Pat", "@example", "rc_cache", "SECRET", "Sam"):
        assert bad not in blob
    assert out["complaints_311_count"] == 1
    assert out["management"] == "Example Homes LLC"
    assert out["source_url"] == "https://example.org/q"
    assert out["model_name"] == "2 BR - 900 SF"


def test_images_only_img_tokens():
    run = run_of([claim("c1", "stories", 5, "5 floors")],
                 [ev("aerial", {"height_m": 15.0, "stories": 5},
                     images=[{"path": "C:/x/rc_cache/a.jpg", "caption": "a"}, {"path": IMG, "caption": "b"}])],
                 [verdict("c1", "confirmed")])
    cell = claim_field(run)["claims"][0]["cells"]["aerial"]
    assert [i["url"] for i in cell["images"]] == [IMG]


def test_section_renders_grid_and_is_self_contained():
    html = claim_field_section(BASE)
    assert html.startswith("<style>") and "<section id=cf-field class='cf-field" in html
    assert html.count("class='cf-cell ") == 5 * 4
    assert "cf-cell cf-match" in html and "cf-cell cf-contra" in html and "cf-cell cf-close" in html
    assert "cf-cell cf-data" in html and "cf-cell cf-none" in html
    for word in ("CoStar", "Matterport", "Vexcel", "County", "matches the listing", "doesn't match",
                 "nothing to say", "One column per claim."):
        assert word in html
    assert "\u201c5-story building\u201d" in html and "\u201cBuilt in 2001\u201d" in html  # highlighted columns
    assert html.count("cf-mark cf-q") == 2  # c3 and c5 could not be checked
    data = re.search(r"<script type=application/json id=cf-data>(.*?)</script>", html, re.S).group(1)
    assert json.loads(data)["claims"][0]["id"] == "c1"
    assert "fake" not in html.lower() and " lie" not in html.lower()
    # no page-level rules
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert not re.search(r"(^|[}\s])(body|html|:root)\s*[{,]", css)


def test_sixty_claims_and_script_safety():
    claims = [claim(f"c{i}", "stories", 5, f"</script><b>{i} floors") for i in range(60)]
    run = run_of(claims, [ev("record", {"stories": 5})], [verdict(f"c{i}", "confirmed") for i in range(60)])
    html = claim_field_section(run)
    assert html.count("class='cf-cell ") == 240
    assert "--cf-n:60" in html
    assert "</script><b>" not in html


def test_empty_run():
    html = claim_field_section({"events": []})
    assert "cf-field" in html and claim_field({})["claims"] == []


def test_panel_facts_are_plain_words_never_raw_keys():
    run = run_of([claim("c1", "bedrooms", 2, "2 Beds", scope="unit", label="Plan Q")],
                 [ev("scan", {"plan": "Plan Q", "bedrooms": 1, "model_name": "Plan Q - 1 Bed", "counts_from": "model_name",
                              "model_id": "zzInvented1", "panoramas": 12, "image_count": 4, "how": "scan_title"})],
                 [verdict("c1", "contradicted", "2 bedrooms", "1 bedroom", "scan")])
    cell = claim_field(run)["claims"][0]["cells"]["scan"]
    facts = dict(cell["facts"])
    assert facts["Counted from"] == "The title of the 3D scan of Plan Q"
    blob = json.dumps(cell["facts"])
    for raw in ("model_name", "counts_from", "zzInvented1", "scan_title", "_"):
        assert raw not in blob, raw
    html = claim_field_section(run)
    assert "Raw data" not in html and "JSON.stringify(cell.raw" not in html


def test_source_rows_aerial_and_county_only_what_the_run_has():
    run = run_of([claim("c1", "stories", 5, "5 floors")],
                 [ev("aerial", {"height_m": 23.1, "stories": 6, "stories_range": [6, 7], "footprint_m2": 1000}),
                  ev("aerial", {"photo_floor_count": 9, "illustrative": True}),
                  ev("aerial", {"feature_check": True, "claim_ids": [], "feature": "pool", "presence": "seen"}),
                  ev("aerial", {"feature_check": True, "claim_ids": [], "feature": "parking", "presence": "unclear"}),
                  ev("county", {"year_built": 2001, "land_use": "APARTMENTS", "owner_name": "Pat Example",
                                "complaints_311": [{"text": "Sam called"}, {"text": "x"}]})],
                 [verdict("c1", "contradicted", "5 floors", "6 floors", "aerial")])
    rows = claim_field(run)["source_rows"]
    aerial, county = dict(rows["aerial"]), dict(rows["county"])
    assert aerial["Roof height"] == "23.1 m" and aerial["Floors, measured from above"].startswith("About 6")
    assert "Pool" not in aerial and "Parking" not in aerial and "9" not in json.dumps(aerial)  # cells, not chips
    assert county == {"Year built": "2001", "311 requests": "2"}
    blob = json.dumps(rows)
    assert "Pat" not in blob and "Sam" not in blob
    assert "record" not in rows and "scan" not in rows
    assert claim_field(run_of([], [], []))["source_rows"] == {}


def test_dead_pictures_are_left_out(tmp_path):
    live = tmp_path / "a.jpg"
    live.write_bytes(b"x")
    dead_tok, live_tok = "0" * 15 + "1", "0" * 15 + "2"
    run = run_of([claim("c1", "stories", 5, "5 floors")],
                 [ev("aerial", {"height_m": 15.0, "stories": 5},
                     images=[{"path": "/img/" + dead_tok, "caption": "a"}, {"path": "/img/" + live_tok, "caption": "b"}])],
                 [verdict("c1", "confirmed")])
    run["images"] = {dead_tok: str(tmp_path / "gone.jpg"), live_tok: str(live)}
    cell = claim_field(run)["claims"][0]["cells"]["aerial"]
    assert [i["url"] for i in cell["images"]] == ["/img/" + live_tok]


def test_source_rows_311_zero_is_left_out():
    run = run_of([], [ev("county", {"year_built": 2001, "houston_311_count": 0, "complaints": []})], [])
    county = dict(claim_field(run)["source_rows"]["county"])
    assert "311 requests" not in county and "Complaints on record" not in county and county["Year built"] == "2001"


def _one_home_run(done_harness=None):
    c = claim("c1", "bedrooms", 2, "2 Beds", scope="unit")
    record = ev("record", {"floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 700}]})
    events = [{"type": "claims", "claims": [c]},
              {"type": "match", "match": {"status": "matched", "kind": "home", "floorplans": []}}, record,
              verdict("c1", "cant_check")]
    if done_harness is not None:
        events.append({"type": "done", "run_id": "t", "harness": done_harness})
    return {"run_id": "t", "events": events}


def test_judge_gets_single_unit_like_the_run(monkeypatch):
    import importlib
    judge_mod = importlib.import_module("harness.graph.judge")
    seen = []
    real = judge_mod.judge

    def spy(claim, evs, single_unit=False):
        seen.append(single_unit)
        return real(claim, evs, single_unit=single_unit)

    monkeypatch.setattr(judge_mod, "judge", spy)
    claim_field(_one_home_run())  # no done yet: the run's own rule (a single home match)
    assert seen and all(seen)
    seen.clear()
    claim_field(_one_home_run({"single_unit": False}))  # the run's recorded answer wins
    assert seen and not any(seen)


# ---------- "Listing vs record" table ----------

CONF = {"score": 82, "band": "high", "decided_by": "aerial",
        "why": "Vexcel measures the roof height directly; County agrees.",
        "sources": [{"source": "aerial", "says": "about 6 floors", "weight": 0.8, "agrees_with_listing": False},
                    {"source": "record", "says": "5 floors", "weight": 0.6, "agrees_with_listing": True}]}


def _with_conf(run, cid, conf):
    out = json.loads(json.dumps(run))
    for e in out["events"]:
        if e["type"] == "verdict" and e["verdict"]["claim_id"] == cid:
            e["verdict"]["confidence"] = conf
    return out


def _rows(html):
    body = html[html.index("<tbody>"):html.index("</tbody>")]
    return re.findall(r"<tr class='lt-r-(\w+)' data-claim='(\w+)'>(.*?)</tr>", body)


def test_confidence_passes_through_claim_field():
    f = claim_field(_with_conf(BASE, "c1", CONF))
    c1 = next(c for c in f["claims"] if c["id"] == "c1")
    assert c1["confidence"]["decided_by"] == "aerial" and c1["confidence"]["score"] == 82
    assert all(c["confidence"] is None for c in f["claims"] if c["id"] != "c1")


def test_compare_table_one_row_per_claim_in_order():
    html = compare_table(BASE)
    assert "<table>" in html and "Listing vs record" in html
    heads = re.findall(r"<th scope=col[^>]*>([^<]+)</th>", html)
    assert heads == ["Claim", "Listing says", "CoStar", "Matterport", "Vexcel", "County", "Result", "Confidence"]
    rows = _rows(html)
    assert len(rows) == 5 and {cid for _, cid, _ in rows} == {"c1", "c2", "c3", "c4", "c5"}
    # mismatches first (doesn't match, then close), then matches, then couldn't check (page order)
    assert [cid for _, cid, _ in rows] == ["c1", "c2", "c4", "c3", "c5"]
    assert "position:sticky" in html and "overflow:auto" in html


def test_compare_table_cells():
    rows = {cid: body for _, cid, body in _rows(compare_table(BASE))}
    cells = lambda body: re.findall(r"<td class='lt-c lt-(\w+)", body)
    # c1 (5 floors): CoStar matches, no scan, Vexcel and County don't match
    assert cells(rows["c1"]) == ["match", "none", "contra", "contra"]
    assert "6 floors" in rows["c1"] and "5 floors" in rows["c1"] and "Doesn&#x27;t match" in rows["c1"]
    assert "—" in rows["c1"] and "Nothing from Matterport" in rows["c1"]
    # c5 has no source at all: four dashes, and the quote stands in for the claimed value
    assert rows["c5"].count(">—<") == 4 and "Great vibes" in rows["c5"] and "Couldn&#x27;t check" in rows["c5"]
    # listing quote as secondary text when it differs from the claimed value
    assert "<b>5 floors</b><small>“5-story building”</small>" in rows["c1"]


def test_compare_table_confidence_only_on_mismatch_and_decides_mark():
    run = _with_conf(BASE, "c1", CONF)
    run = _with_conf(run, "c4", dict(CONF, decided_by="aerial"))  # a match carrying one: never shown
    rows = {cid: body for _, cid, body in _rows(compare_table(run))}
    assert "<b>High · 82</b>" in rows["c1"] and "Vexcel measures the roof height directly" in rows["c1"]
    assert rows["c1"].count("lt-dec") == 1 and rows["c1"].count(">decides<") == 1
    vex = re.findall(r"<td class='lt-c [^']*'[^>]*>.*?</td>", rows["c1"])[2]
    assert "lt-dec" in vex and "decides" in vex
    for cid in ("c2", "c3", "c4", "c5"):
        assert "<td class=lt-conf></td>" in rows[cid] and "decides" not in rows[cid]


def test_compare_table_odd_or_absent_confidence_never_breaks():
    for conf in (None, {}, "high", {"band": "weird"}, {"score": "x"}, {"score": 140, "decided_by": "nowhere"}):
        html = compare_table(_with_conf(BASE, "c1", conf))
        assert len(_rows(html)) == 5
    html = compare_table(_with_conf(BASE, "c1", {"score": 140, "decided_by": "nowhere"}))
    assert "<b>100</b>" in html and "decides" not in html
    assert compare_table(run_of([], [], [])) == ""


def test_compare_table_cell_is_a_short_fact_never_a_cut_sentence():
    from server.claim_field import _td_source
    long = "The building record lists an unusually long description of floors that goes on and on and on."
    td = _td_source({"state": "data", "found": "", "summary": long}, "CoStar", False)  # no short fact: a dash
    assert "lt-data" in td and f"title='{long}'" in td and "…" not in td and ">—<" in td
    assert long not in td.split("title=")[0] and long[:20] not in td.split("'>", 1)[1]
    td = _td_source({"state": "match", "found": "6 floors", "short": "6 floors", "summary": long}, "County", True)
    assert "6 floors</span>" in td and "title=" not in td and "lt-dec" in td and long[:20] not in td
    td = _td_source({"state": "contra", "found": "1 bedroom and a den", "short": "1 bed + den"}, "Matterport", False)
    assert "1 bed + den</span>" in td and "title='1 bedroom and a den'" in td
    assert ">—<" in _td_source({"state": "none"}, "Vexcel", False)
    body = compare_table(BASE)
    body = body[body.index("<tbody>"):]
    assert "…" not in body
    rows = {cid: b for _, cid, b in _rows(compare_table(BASE))}
    assert "Pool seen" in rows["c4"] and "Built 2000" in rows["c2"] and "Built 2001" in rows["c2"]


def test_compact_short_facts():
    from server.claim_field import compact
    assert compact("1 bedroom and a den") == "1 bed + den"
    assert compact("1 bedroom + den") == "1 bed + den"
    assert compact("2 bathrooms") == "2 baths"
    assert compact("built in 2016") == "Built 2016"
    assert compact("6 stories") == "6 floors"
    assert compact("about 6 floors (6 to 7)") == "About 6 floors (6 to 7)"
    assert compact("1,184 sq ft.") == "1,184 sq ft"
    for sentence in ("The 3D scan shows 1 bedroom and a den, about 968 sq ft.", "Seen. Twice", "Rooms: Bedroom", ""):
        assert compact(sentence) == ""


def test_compare_table_prefers_the_confidence_says():
    rows = {cid: b for _, cid, b in _rows(compare_table(_with_conf(BASE, "c1", CONF)))}
    assert "About 6 floors" in rows["c1"]  # the Vexcel row's own words
    # a match carrying a confidence block shows the cell's own fact, not the block's words
    rows = {cid: b for _, cid, b in _rows(compare_table(_with_conf(BASE, "c4", CONF)))}
    assert "Pool seen" in rows["c4"] and "About 6 floors" not in rows["c4"]


# ---------- Vexcel property data in the table (invented: 418 Linden Grove Ave, ids 900001 / 900002) ----------

def prop_data(**over):
    base = {"captured": "2025-12-19", "source": "Vexcel property data",
            "pool": {"present": True, "confidence": 0.95, "area_sqft": 1900, "kind": "in-ground", "stable": True},
            "hot_tub": {"present": False, "stable": True}, "vehicles": 12, "tree_cover_pct": 18.5, "buildings": 2}
    base.update(over)
    return ev("aerial", {"property": base}, summary="Vexcel property data (survey of 2025-12-19) shows a pool.")


def look(cid, presence, feat="pool"):
    return ev("aerial", {"feature_check": True, "claim_ids": [cid], "feature": feat, "presence": presence,
                         "presence_confidence": 0.9}, images=[{"path": IMG, "caption": "Top-down"}])


def judged_run(claims, evidence):
    """A run whose verdicts come from the real judge on all the evidence (confidence blocks included)."""
    from harness.graph.judge import judge
    evs = [e["evidence"] for e in evidence]
    return run_of(claims, evidence, [{"type": "verdict", "verdict": judge(c, evs)} for c in claims])


def _vexcel(row):
    return re.findall(r"<td class='lt-c [^']*'[^>]*>.*?</td>", row)[2]


def test_property_data_confirmation_in_the_vexcel_cell():
    c = claim("p1", "amenity", "pool", "Resort-style pool")
    run = judged_run([c], [prop_data()])
    f = claim_field(run)
    assert f["claims"][0]["verdict"] == "confirmed" and state(f, "p1", "aerial") == "match"
    vex = _vexcel(_rows(compare_table(run))[0][2])
    assert "Pool, ~1,900 sq ft (2025 survey)" in vex and "lt-match" in vex and "…" not in vex
    assert "title='In Vexcel property data'" in vex
    run = judged_run([c], [prop_data(captured=None)])
    assert "Pool, ~1,900 sq ft</span>" in _vexcel(_rows(compare_table(run))[0][2])
    # a "no" alone: couldn't check, said softly
    run = judged_run([c], [prop_data(pool={"present": False, "stable": True})])
    row = _rows(compare_table(run))[0]
    assert row[0] == "cant_check" and "Not in property data" in _vexcel(row[2])
    # a feature property data does not cover never picks it up
    run = judged_run([claim("r1", "amenity", "rooftop deck", "Rooftop deck")], [prop_data()])
    assert state(claim_field(run), "r1", "aerial") == "none"


def test_not_seen_and_not_in_data_is_couldnt_check():
    # neither "not seen in the photo" nor "not in property data" proves absence
    c = claim("p1", "amenity", "pool", "Pool")
    rec = ev("record", {"property_id": "900001", "amenities": ["Fitness center"]}, summary="Amenities.")
    run = judged_run([c], [rec, look("p1", "not_seen"), prop_data(pool={"present": False, "stable": True})])
    assert claim_field(run)["claims"][0]["verdict"] == "cant_check"
    assert _rows(compare_table(run))[0][0] == "cant_check"
    html = compare_table(run).lower()
    for bad in ("fake", " lie", "scam", "fraud"):
        assert bad not in html


def test_says_with_odd_rows_never_breaks():
    from server.claim_field import _says
    assert _says(None, "aerial") == "" and _says({"sources": "x"}, "aerial") == ""
    assert _says({"sources": [{"source": "aerial", "says": ""}, "x"]}, "aerial") == ""
    two = {"sources": [{"source": "aerial", "says": "seen"}, {"source": "aerial", "says": "in property data"}]}
    assert _says(two, "aerial") == "Photo: seen · Data: shown"
    long = {"sources": [{"source": "county", "says": "a very long reading that would never fit in the cell"}]}
    assert _says(long, "county") == ""


def test_compare_table_soft_words_only():
    html = compare_table(_with_conf(BASE, "c1", CONF)).lower()
    for bad in ("fake", " lie", "scam", "fraud"):
        assert bad not in html


def test_compare_table_listing_says_is_never_cut():
    """'Listing says' shows the whole claimed text (the cell wraps), never a '…' cut; a very long page quote with
    no claimed form is cut at a word boundary, never mid-word."""
    long_claim = "Largest rooftop deck in North America spanning the whole top floor of 418 Linden Grove Ave"
    long_quote = ("Washer and dryer options on select floors plus a resident lounge, a quiet library and a bike room "
                  "with repair stand and storage lockers")
    run = run_of([claim("c1", "amenity", "deck", long_claim), claim("c2", "amenity", "laundry", long_quote)], [],
                 [verdict("c1", "cant_check", claimed=long_claim), verdict("c2", "cant_check")])
    html = compare_table(run)
    rows = {cid: b for _, cid, b in _rows(html)}
    assert f"<td class=lt-said><b>{long_claim}</b>" in rows["c1"]
    assert "…" not in html[html.index("<tbody>"):]
    said = re.search(r"<td class=lt-said><b>([^<]*)</b>", rows["c2"]).group(1)
    assert len(long_quote) > 120 and len(said) <= 120 and long_quote.startswith(said)
    assert long_quote[len(said)] == " "  # cut between words
    assert "overflow-wrap:anywhere" in html and ".lt-scroll{overflow:auto" in html


# ---------- one count, verdict-consistent colours, few chips, phone width (invented: 418 Linden Grove Ave) ----------

def test_record_amenity_names_fill_the_costar_cell_and_counts_come_from_one_function():
    from server.claim_field import source_counts
    run = run_of([claim("c1", "amenity", "pool", "Sparkling pool"), claim("c2", "stories", 5, "5 floors")],
                 [ev("record", {"property_id": "900001", "amenities": ["Pool", "Fitness center"]}),
                  ev("record", {"stories": 5}),
                  ev("aerial", {"feature_check": True, "claim_ids": ["c1"], "feature": "pool", "presence": "seen",
                                "presence_confidence": 0.9})],
                 [verdict("c1", "confirmed", "pool", "Named in the building record", "record"),
                  verdict("c2", "confirmed", "5 floors", "5 floors", "record")])
    f = claim_field(run)
    assert state(f, "c1", "record") == "match"  # the record's amenity list spoke to it: a filled CoStar cell
    per = source_counts(f)
    assert per["record"] == {"total": 2, "settled": 2, "weighed": 0}
    assert per["aerial"] == {"total": 1, "settled": 0, "weighed": 1}
    for s in f["sources"]:  # the row label is the same number as the filled cells in the row
        filled = sum(1 for c in f["claims"] if c["cells"][s["key"]]["state"] != "none")
        assert s["count"] == per[s["key"]]["total"] == filled
        assert (s["settled"], s["weighed"]) == (per[s["key"]]["settled"], per[s["key"]]["weighed"])
    html = claim_field_section(run)
    assert "<span><b>CoStar</b><i>2</i></span>" in html


def _floors_run(v, src=None, found="", conf=None):
    run = run_of([claim("c1", "stories", 5, "5-story building at 418 Linden Grove Ave")],
                 [ev("record", {"property_id": "900002", "stories": 5}),
                  ev("aerial", {"height_m": 14.0, "stories": 4}),
                  ev("county", {"parcel_id": "900003", "stories": 6})],
                 [verdict("c1", v, "5 floors", found, src)])
    if conf is not None:
        run["events"][-1]["verdict"]["confidence"] = conf
    return run


def cell(field, cid, src):
    return next(c for c in field["claims"] if c["id"] == cid)["cells"][src]


def test_couldnt_check_never_shows_blue_or_an_outline():
    f = claim_field(_floors_run("cant_check", found="sources differ"))
    cells = f["claims"][0]["cells"]
    assert cells["record"]["state"] == "match"  # agrees with the listing: black
    assert cells["aerial"]["state"] == cells["county"]["state"] == "data"  # off the listing: light grey
    assert all("shade" not in c and "note" not in c for c in cells.values())
    assert f["counts"]["contra"] == f["counts"]["close"] == 0 and "differ" not in f["counts"]
    html = claim_field_section(_floors_run("cant_check", found="sources differ"))
    assert "cf-cell cf-contra" not in html and "cf-cell cf-close" not in html
    assert "differ" not in re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "dashed" not in html and "class=cf-d>" not in html and "cf-differ" not in html


def test_couldnt_check_with_one_reading_is_has_data():
    run = run_of([claim("c1", "stories", 5, "5 floors")], [ev("aerial", {"height_m": 14.0, "stories": 4})],
                 [verdict("c1", "cant_check", "5 floors", "about 4 floors")])
    assert state(claim_field(run), "c1", "aerial") == "data"


def test_every_off_listing_source_is_blue_on_a_mismatch_shaded_by_confidence():
    f = claim_field(_floors_run("contradicted", "county", "6 floors",
                                {"score": 55, "band": "medium", "decided_by": "county"}))
    assert (state(f, "c1", "county"), cell(f, "c1", "county")["shade"]) == ("contra", "medium")  # same shade as the list
    assert (state(f, "c1", "aerial"), cell(f, "c1", "aerial")["shade"]) == ("contra", "medium")  # says 4: blue too
    assert state(f, "c1", "record") == "match" and "shade" not in cell(f, "c1", "record")  # agrees: black
    f = claim_field(_floors_run("contradicted", "county", "6 floors", {"score": 30, "decided_by": "county"}))
    assert cell(f, "c1", "aerial")["shade"] == "low" and cell(f, "c1", "county")["shade"] == "low"
    f = claim_field(_floors_run("contradicted", "county", "6 floors", {"score": 90, "decided_by": "county"}))
    assert cell(f, "c1", "aerial")["shade"] == cell(f, "c1", "county")["shade"] == "high"
    f = claim_field(_floors_run("contradicted", "county", "6 floors"))  # no confidence: medium
    assert cell(f, "c1", "aerial")["shade"] == "medium" and cell(f, "c1", "county")["shade"] == "medium"
    f = claim_field(_floors_run("close", "county", "6 floors", {"score": 95}))  # close: always faint
    assert {(cell(f, "c1", k)["state"], cell(f, "c1", k)["shade"]) for k in ("aerial", "county")} == {("close", "low")}
    html = claim_field_section(_floors_run("contradicted", "county", "6 floors", {"score": 30, "decided_by": "county"}))
    assert "cf-cell cf-contra cf-s-low" in html
    assert ".cf-cell.cf-s-high{background:var(--cf-blue)}" in html and "--cf-blue:#1a4be0" in html


def test_holds_up_credited_source_is_black_and_other_disagreeing_sources_light_grey():
    f = claim_field(_floors_run("confirmed", "record", "5 floors"))
    assert state(f, "c1", "record") == "match"
    assert state(f, "c1", "aerial") == state(f, "c1", "county") == "data"
    # the credited source's own re-judging disagrees (it matched another plan): still black, never blue
    f = claim_field(_floors_run("confirmed", "county", "5 floors"))
    assert state(f, "c1", "county") == "match"
    assert f["counts"]["contra"] == f["counts"]["close"] == 0
    table = compare_table(_floors_run("confirmed", "record", "5 floors"))
    assert "lt-differ" not in table and "sources differ" not in table


def test_a_photo_that_did_not_show_it_is_never_blue():
    from server.claim_field import _agree_with_verdict
    cells = {k: {"state": "none"} for k, _ in SOURCES}
    cells["aerial"] = {"state": "contra", "found": "Not seen in the Vexcel aerial photo",
                       "observed": {"presence": "not_seen"}}
    cells["record"] = {"state": "contra", "found": "No pool"}
    _agree_with_verdict(cells, "contradicted", "record", "No pool", {"score": 80})
    assert cells["aerial"]["state"] == "data" and "shade" not in cells["aerial"]
    assert (cells["record"]["state"], cells["record"]["shade"]) == ("contra", "high")


def test_legend_is_three_entries():
    html = claim_field_section(_floors_run("contradicted", "county", "6 floors"))
    legend = re.search(r"<p class=cf-legend>(.*?)</p>", html).group(1)
    assert re.findall(r"</i>([^<]+)</span>", legend) == ["matches the listing", "doesn't match (darker = more sure)",
                                                         "nothing to say"]


def test_source_rows_never_owner_land_use_or_publisher():
    run = run_of([], [ev("county", {"parcel_id": "900003", "year_built": 2001, "unit_count": 40, "stories": 5,
                                    "land_use": "APARTMENTS", "owner_type": "company", "building_sf": 50000,
                                    "source_name": "Example County Appraisal District", "city_311_count": 7}),
                      ev("aerial", {"height_m": 14.0, "stories": 4, "capture_date": "2025-03-01",
                                    "footprint_m2": 900})], [])
    rows = claim_field(run)["source_rows"]
    assert dict(rows["county"]) == {"Year built": "2001", "Homes": "40", "311 requests": "7"}
    assert dict(rows["aerial"]) == {"Roof height": "14 m", "Floors, measured from above": "About 4",
                                    "Photos taken": "2025-03-01"}
    html = claim_field_section(run)
    for bad in ("Owner", "Land use", "Published by", "A company", "Appraisal"):
        assert bad not in html


def test_phone_width_grid_fits_or_scrolls_with_a_hint():
    html = claim_field_section(_floors_run("contradicted", "county", "6 floors"))
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    phone = css[css.index("@media(max-width:640px)"):]
    assert "--cf-min:8px" in phone and "--cf-gap:2px" in phone  # 24 columns fit in a 390 px screen
    assert "text-overflow:ellipsis" in css[css.index(".cf-rows b{"):]  # row names end cleanly, never mid-word
    assert ".cf-mt{" in css and "<span class=cf-mt>" in html  # column labels too
    assert "<p class=cf-more aria-hidden=true>Scroll sideways to see every claim →</p>" in html
    assert ".cf-over .cf-more{display:block}" in css and "classList.toggle('cf-over'" in html
