""""Fill me in" section in the drawer (fill_in event, docs/CONTRACT.md section 2.6).

Static checks always run; browser checks use headless bundled Chromium like test_extension.py and skip without Playwright.
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_fill_in_ui.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402

EXT = H.EXT
FACTS = [  # invented
    {"id": "f1", "topic": "Homes & floor plans", "label": "Plan Z", "value": "3 bed, 2 bath, 1,400 sq ft", "source": "record",
     "note": "From the building record.", "image": None},
    {"id": "f2", "topic": "Rules", "label": "Pets <b>", "value": "No pets", "source": "record", "note": "", "image": None},
    {"id": "f3", "topic": "Records", "label": "Lot size", "value": "1.2 acres", "source": "county", "note": "", "image": None},
    {"id": "f4", "topic": "Around it", "label": "Roof", "value": "Flat roof", "source": "aerial", "note": "", "image": None},
    {"id": "f5", "topic": "Building", "label": "Fifth fact", "value": "Hidden until the report", "source": "scan", "note": "", "image": None},
]


# ---------- static ----------

def test_sample_events_has_fill_in_before_done():
    from server.sample_report import load_events  # reads the JSON block in extension/sample_events.js

    kinds = [e["type"] for e in load_events()]
    i_fill, i_done = kinds.index("fill_in"), kinds.index("done")
    i_last_verdict = len(kinds) - 1 - kinds[::-1].index("verdict")
    assert 0 < i_last_verdict < i_fill < i_done
    facts = load_events()[i_fill]["facts"]
    assert 3 <= len(facts) <= 12  # the drawer lists them all; the report keeps only done.harness.top_facts
    assert any(f["id"] == "aerial-features" and f["topic"] == "Around it" for f in facts)
    assert any(f["value"] == "Example Property Management" for f in facts)
    top = load_events()[i_done]["harness"]["top_facts"]
    ids = {f["id"] for f in facts}
    assert 1 <= len(top) <= 5 and all(t["fact_ids"] and set(t["fact_ids"]) <= ids for t in top)


def load_events_facts():
    from server.sample_report import load_events

    return next(e for e in load_events() if e["type"] == "fill_in")["facts"]


def test_ui_handles_fill_in_neutrally():
    src = (EXT / "content" / "ui.js").read_text(encoding="utf-8")
    assert 'case "fill_in"' in src
    m = re.search(r"function fillInHTML\(facts\) \{(.*?)\n  \}\n", src, re.S)
    assert m, "fillInHTML not found"
    fn = m.group(1)
    assert "VC[" not in fn and "#dc2626" not in fn and "#16a34a" not in fn  # no verdict colours
    for field in ("f.label", "f.value", "f.note", "f.id"):
        assert f"esc({field})" in fn, field
    assert '"#extra"' in src


# ---------- browser ----------

@pytest.fixture(scope="module")
def env():
    ps = pytest.importorskip("playwright.sync_api")
    httpd, base = H.serve_fixtures()
    with ps.sync_playwright() as pw:
        try:
            ctx, sw, udd = H.launch(pw, headless=True)
        except Exception as e:  # browser not installed
            httpd.shutdown()
            pytest.skip(f"Chromium unavailable: {e}")
        try:
            yield {"ctx": ctx, "sw": sw, "base": base}
        finally:
            ctx.close()
            httpd.shutdown()
            H.cleanup(udd)


def open_page(env, name="listing.html"):
    page = env["ctx"].new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{env['base']}/{name}")
    page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)
    return page, errors


def test_sample_flow_shows_fill_in(env):
    H.set_server(env["sw"], f"http://127.0.0.1:{H.free_port()}")  # nothing listening
    page, errors = open_page(env)
    page.locator("#rc-host .addr-mark").first.click()
    page.locator("#rc-host .pill").click()
    page.locator("#rc-host #useSample").wait_for(timeout=10000)
    page.locator("#rc-host #useSample").click()
    page.locator("#rc-host #headline").wait_for(timeout=15000)
    tog = page.locator("#rc-host #fiToggle")
    tog.wait_for(timeout=3000)
    assert tog.inner_text().strip() == f"Not in the listing ({len(load_events_facts())})"
    assert tog.get_attribute("aria-expanded") == "false"
    assert page.locator("#rc-host .fact").count() == 0  # collapsed by default
    tog.click()
    facts = page.locator("#rc-host .fact")
    assert facts.count() == 4  # top 4 only
    # renter order: home sizes, then year built / renovated, then what the aerial survey saw; the rest in the report
    assert facts.locator(".fl").all_inner_texts() == ["Plan A", "Typical size, 1 bed", "Last renovated", "Seen from above"]
    assert tog.get_attribute("aria-expanded") == "true"
    assert facts.first.locator(".fl").inner_text() == "Plan A"
    assert facts.first.locator(".fs").inner_text() == "CoStar"
    assert "From the building record" in facts.first.locator(".fn").inner_text()
    assert page.locator("#rc-host #fiAll").inner_text() == "See all in the full report"
    with env["ctx"].expect_page() as newp:
        page.locator("#rc-host #fiAll").click()
    rep = newp.value
    rep.wait_for_selector("#extra")
    assert rep.url.endswith("#extra")  # the sample report (design A) opens at its "Not in the listing" section
    extra = rep.locator("#extra").inner_text()  # the report lists the sample's top picks, not every fact
    assert "Seen from above" in extra and "Sport court" in extra
    rep.close()
    page.locator("#rc-host #fiToggle").click()
    assert page.locator("#rc-host .fact").count() == 0
    assert not errors, errors
    page.close()


def _run_stub(env, monkeypatch, facts):
    base_events = H.stub_events

    def events(address, quotes=None):
        evs = base_events(address, quotes)
        if facts is not None:
            evs.insert(len(evs) - 1, {"type": "fill_in", "facts": facts})
        return evs

    monkeypatch.setattr(H, "stub_events", events)
    stub = H.StubChecker(delay=0.03)
    H.set_server(env["sw"], stub.url)
    page, errors = open_page(env)
    page.locator("#rc-host .addr-mark").first.click()
    page.locator("#rc-host .pill").click()
    page.locator("#rc-host #headline").wait_for(timeout=15000)
    page.wait_for_timeout(200)
    return stub, page, errors


def test_server_fill_in_escaped_and_links_to_extra(env, monkeypatch):
    stub, page, errors = _run_stub(env, monkeypatch, FACTS)
    try:
        page.locator("#rc-host #fiToggle").click()
        assert page.locator("#rc-host .fact").count() == 4
        assert page.locator('#rc-host .fact[data-id="f2"] .fl').inner_text() == "Pets <b>"  # escaped, not markup
        assert page.locator('#rc-host .fact[data-id="f5"]').count() == 0
        assert page.locator('#rc-host .fact[data-id="f4"] .fs').inner_text() == "Vexcel"
        with env["ctx"].expect_page() as newp:
            page.locator("#rc-host #fiAll").click()
        rep = newp.value
        rep.wait_for_load_state()
        assert rep.url == stub.url + "/report/r1#extra"
        rep.close()
        assert not errors, errors
    finally:
        page.close()
        stub.close()


def test_no_fill_in_event_shows_nothing(env, monkeypatch):
    stub, page, errors = _run_stub(env, monkeypatch, None)
    try:
        assert page.locator("#rc-host #fiToggle").count() == 0
        assert not errors, errors
    finally:
        page.close()
        stub.close()


def test_one_fact_singular(env, monkeypatch):
    stub, page, errors = _run_stub(env, monkeypatch, json.loads(json.dumps(FACTS[:1])))
    try:
        assert page.locator("#rc-host #fiToggle").inner_text().strip() == "Not in the listing (1)"
        assert not errors, errors
    finally:
        page.close()
        stub.close()
