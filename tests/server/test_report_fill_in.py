"""Report tab 'Not on the listing' built from fill_in facts. Invented data only."""
import re

from server.report import render_report

FACTS = [
    {"id": "f1", "topic": "Rules", "label": "Pets", "value": "Cats allowed", "source": "record",
     "note": "", "image": None},
    {"id": "f2", "topic": "Homes & floor plans", "label": "Plan Q7", "value": "2 bed, 1 bath, 900 sq ft",
     "source": "record", "note": "From the building record, as of when it was last researched.", "image": None},
    {"id": "f3", "topic": "Homes & floor plans", "label": "Rooms in the 3D scan of Plan Q7",
     "value": "Bedroom, Kitchen", "source": "scan", "note": "Labelled automatically by the scan; labels can be wrong.",
     "image": "/img/00112233aabbccdd"},
    {"id": "f4", "topic": "Building", "label": "Year built", "value": "<script>alert(1)</script>",
     "source": "county"},
    {"id": "f5", "topic": "Odd topic", "label": "Roof", "value": "Flat", "source": "aerial"},
    "not a dict",
    {"id": "f6", "topic": "Building", "label": "", "value": "no label"},
    {"id": "f7", "topic": "Building", "label": "No value"},
]


def base_events():
    return [
        {"type": "claims", "claims": [{"id": "c1", "kind": "amenity", "value": "gym", "scope": "building",
                                       "label": None, "quote": "Gym"}]},
        {"type": "verdict", "verdict": {"claim_id": "c1", "verdict": "cant_check", "claimed": "gym",
                                        "found": "no data", "source": None, "reason": "None of our sources measured this.",
                                        "image": None}},
        {"type": "done", "counts": {}, "headline": "Nothing to compare."}]


def extra_panel(html):
    return html[html.index("<section class=panel id='extra'>"):html.index("</section>", html.index("id='extra'"))]


def test_fill_in_event_renders_tab():
    ev = base_events()
    ev.insert(2, {"type": "fill_in", "facts": [{"id": "old", "topic": "Rent", "label": "Stale", "value": "x"}]})
    ev.insert(3, {"type": "fill_in", "facts": FACTS})  # last event wins
    html = render_report({"snapshot": {"address_text": "9 Test Ave"}, "events": ev})
    assert re.findall(r"<a href='#([a-z-]+)'", html) == ["mismatch", "match", "cant-verify", "extra"]
    assert "id='tab-extra'" in html and "Not on the listing (5)" in html
    assert "'extra'" in html  # tab id known to the tab script
    p = extra_panel(html)
    assert "Not a fact-check." in p and "Stale" not in p
    heads = re.findall(r"<p class=rowh>([^<]+)</p>", p)
    assert heads == ["Building", "Homes &amp; floor plans", "Rules", "Odd topic"]
    assert p.index("Plan Q7</span>") < p.index("Rooms in the 3D scan of Plan Q7")  # order kept within topic
    for text in ("2 bed, 1 bath, 900 sq ft", "<small>Labelled automatically by the scan; labels can be wrong.</small>",
                 "Cats allowed", "From the building record", "From the 3D scan", "From county records",
                 "From Vexcel aerial photos"):
        assert text in p, text
    assert "<small></small>" not in p
    assert "<a class=pic href='/img/00112233aabbccdd'" in p and "<img src='/img/00112233aabbccdd'" in p
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in p
    assert "no label" not in p and "No value" not in p


def test_top_level_fill_in_without_event():
    run = {"snapshot": {"address_text": "9 Test Ave"}, "events": base_events(),
           "fill_in": [{"id": "a", "topic": "Around it", "label": "Nearest park", "value": "0.3 mi",
                        "source": "county", "image": "C:/bad/path.png"}]}
    p = extra_panel(render_report(run))
    assert "Nearest park" in p and "0.3 mi" in p and "Around it" in p
    assert "class=pic" not in p and "C:/" not in p  # only /img/<token> pictures


def test_no_fill_in_shows_empty_message():
    html = render_report({"snapshot": {"address_text": "1 Main St"}, "events": base_events()})
    assert "Not on the listing (0)" in html
    assert "Our sources have nothing to add beyond the listing." in extra_panel(html)
    assert html.count("Nothing here.") == 2  # other empty tabs unchanged
    assert "Can't verify (1)" in html.replace("&#x27;", "'")
