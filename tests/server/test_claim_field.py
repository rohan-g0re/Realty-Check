"""Claim field: the claim x source mapping and its HTML fragment. Invented data only."""
import json
import re

from server.claim_field import SOURCES, claim_field, claim_field_section, clean_observed

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
    assert [s["name"] for s in f["sources"]] == ["CoStar", "Matterport", "Aerial", "County"]
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
    for word in ("CoStar", "Matterport", "Aerial", "County", "matches the listing", "contradicts it",
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
