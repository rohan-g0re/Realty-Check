"""End-to-end tests for the Realty Check extension (headless bundled Chromium, local fixture pages only).

Run: .venv/Scripts/python.exe -m pytest tests/extension -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402

playwright_sync = pytest.importorskip("playwright.sync_api")
DRAWER_W = 420
SAMPLE_HEADLINE = "4 things don't match."  # the sample report page (design A) headline: every mismatch, close too
COLORS = {"contradicted": "#1a4be0", "close": "#1a4be0", "confirmed": "#111111", "cant_check": "#b5b5b5"}


def headline(page):
    return " ".join(page.locator("#rc-host #headline").inner_text().split())


@pytest.fixture(scope="module")
def env():
    httpd, base = H.serve_fixtures()
    with playwright_sync.sync_playwright() as pw:
        ctx, sw, udd = H.launch(pw, headless=True)
        try:
            yield {"ctx": ctx, "sw": sw, "base": base}
        finally:
            ctx.close()
            httpd.shutdown()
            H.cleanup(udd)


def open_page(env, name):
    page = env["ctx"].new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{env['base']}/{name}")
    page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)
    return page, errors


def body_width(page):
    return page.evaluate("document.body.getBoundingClientRect().width")


def test_addresses_highlighted_on_both_fixtures(env):
    for name, street in (("listing.html", "418 Linden Grove Ave"), ("listing_dark.html", "6120 Sunset Crest Blvd")):
        page, errors = open_page(env, name)
        assert int(page.locator("#rc-host").get_attribute("data-count")) >= 1
        assert page.locator("#rc-host .addr-mark").count() >= 1
        # the drawer's address list shows the full address (street joined with the city line / JSON-LD)
        page.locator("#rc-host #tabFace").click()
        picks = page.locator("#rc-host .pick .pa").all_inner_texts()
        assert any(p.startswith(street) and ", " in p and any(ch.isdigit() for ch in p[-5:]) for p in picks), picks
        assert not any("Hidden Rd" in p for p in picks)  # address inside <script> is skipped
        assert not errors, errors
        page.close()


def test_tab_hover_and_drawer_push(env):
    page, errors = open_page(env, "listing.html")
    tab = page.locator("#rc-host #tab")
    assert tab.is_visible()
    tab.hover()
    page.wait_for_timeout(350)
    op = lambda sel: float(page.locator(f"#rc-host {sel}").evaluate("e => getComputedStyle(e).opacity"))  # noqa: E731
    assert op(".tab-handle") > 0.9 and op(".tab-close") > 0.9
    w0 = body_width(page)
    page.locator("#rc-host #tabFace").click()
    page.wait_for_timeout(450)
    assert page.locator("#rc-host #drawer").is_visible()
    assert abs((w0 - body_width(page)) - DRAWER_W) < 2
    page.locator("#rc-host #btnCollapse").click()
    page.wait_for_timeout(450)
    assert abs(body_width(page) - w0) < 1
    assert not errors, errors
    page.close()


def test_sample_flow_marks_and_report(env):
    H.set_server(env["sw"], f"http://127.0.0.1:{H.free_port()}")  # nothing listening: checker "not running"
    page, errors = open_page(env, "listing.html")
    page.locator("#rc-host .addr-mark").first.click()
    pill = page.locator("#rc-host .pill")
    assert pill.inner_text() == "Inspect location"
    pill.click()
    page.locator("#rc-host .notice", has_text="Local checker is not running").wait_for(timeout=10000)
    page.locator("#rc-host #useSample").click()
    page.locator("#rc-host #cols .col").nth(4).wait_for(timeout=10000)  # the claim field, one column per claim
    page.locator("#rc-host #headline").wait_for(timeout=15000)
    # concise verdict: the report's headline and lead line (done.top), quiet counts line, findings in the report's order
    assert headline(page) == "4 things don't match."  # three contradicted and one close: every one counts
    assert page.locator("#rc-host #topLine").inner_text().startswith("Biggest difference: the listing says “2 Beds”")
    meta = page.locator("#rc-host #counts").inner_text()
    assert "Sample result" in meta and "2 true" in meta and "4 don't match" in meta and "1 couldn't check" in meta, meta
    assert "close" not in meta, meta
    finds = page.locator("#rc-host .find")
    # the run's pick first, then the most certain first (82, 70, 58)
    assert finds.evaluate_all("els => els.map(e => e.dataset.id)") == ["c1", "c2", "c7", "c3"]
    assert page.locator("#rc-host .fr").evaluate_all("els => els.map(e => e.dataset.shade)") == [
        "medium", "high", "medium", "medium"]
    assert page.locator("#rc-host .ftag").evaluate_all("els => els.map(e => e.textContent)") == [
        "Doesn't match · 64 medium", "Doesn't match · 82 high", "Doesn't match · 70 medium", "Doesn't match · 58 medium"]
    assert page.locator("#rc-host #moreF").count() == 0
    assert finds.first.locator(".is").inner_text() == "1 bedroom and a den"
    assert page.locator("#rc-host .fr .bys").first.inner_text() == "Source: Matterport"
    # which home was checked, above the headline
    assert " ".join(page.locator("#rc-host #who").inner_text().split()) == \
        "Checked: Unit 1204 (this home) 3D scan is of the same floor plan"
    # how sure: one chip per finding (all three carry a confidence block), the why and the sources on demand
    chips = page.locator("#rc-host .cf .cfb")
    assert chips.count() == 4
    # the judge's own 0-100 score next to the word
    assert chips.evaluate_all("els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())") == [
        "64 · medium confidence", "82 · high confidence", "70 · medium confidence", "58 · medium confidence"]
    assert page.locator("#rc-host .cfx:visible").count() == 0
    chips.first.click()
    x = page.locator("#rc-host #cfx0")
    assert x.is_visible() and chips.first.get_attribute("aria-expanded") == "true"
    assert "CoStar matches the listing" in x.locator(".cfw").inner_text()
    rows = x.locator(".cfl li").evaluate_all("els => els.map(e => [...e.children].map(c => c.textContent).join(' | '))")
    assert rows[0] == "Matterport | 1 bedroom + den | decides", rows
    assert rows[1] == "CoStar | 2 bedrooms | matches the listing", rows
    chips.first.click()
    assert not x.is_visible()
    assert page.locator("#rc-host #body").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    assert page.locator("#rc-host .claim").count() == 0  # no per-claim list in the drawer any more

    marks = page.locator("#rc-host .claim-mark")
    assert marks.count() >= 3
    found = marks.evaluate_all("els => els.map(e => [e.dataset.verdict, e.style.getPropertyValue('--vc')])")
    assert len({v for v, _ in found}) >= 3
    for v, c in found:
        assert c == COLORS[v]
    red = page.locator('#rc-host .claim-mark[data-verdict="contradicted"]').first
    red.scroll_into_view_if_needed()
    red.hover()
    tip = page.locator("#rc-host .tip.show")
    tip.wait_for(timeout=3000)
    assert tip.inner_text().startswith("Listing says ")

    # click a mark: the drawer points at that finding
    red.click()
    page.locator("#rc-host .find.ping").wait_for(timeout=3000)

    with env["ctx"].expect_page() as newp:
        page.locator("#rc-host #btnFull").click()
    rep = newp.value
    rep_errors = watch_errors(rep)
    rep.wait_for_load_state("load")  # report.js (the address swap) runs at the end of the body
    # the design A page (server/sample_report.py), with this listing's address in place of the sample one
    assert "/report/report.html?sample=1" in rep.url
    assert " ".join(rep.locator("h1").inner_text().split()) == SAMPLE_HEADLINE
    assert rep.locator(".hero .where").inner_text().startswith("418 Linden Grove Ave")
    assert "Sample address" not in rep.locator("body").inner_text()
    assert rep.locator("#cf-field .cf-cell").count() == 7 * 4  # seven claims, four sources
    assert rep.locator("[id^='claim-']").count() == 4  # every mismatch, the close one too
    rep.close()
    assert not rep_errors, rep_errors
    assert not errors, errors
    page.close()


def watch_errors(page):
    """Console errors and page errors, minus the web font (it needs the network; the page falls back to Georgia)."""
    errors = []
    font = lambda m: "fonts.g" in ((m.location or {}).get("url") or "")  # noqa: E731
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not font(m) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def at_top(page, el_id):
    """The page scrolled (smoothly) to put this element near the top."""
    js = f"Math.abs(document.getElementById('{el_id}').getBoundingClientRect().top) < 40"
    for _ in range(50):  # wait_for_function would eval a string, which the extension page's CSP forbids
        if page.evaluate(js):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"#{el_id} never reached the top")


def test_sample_report_page_anchors_and_panel(env):
    ext_id = env["sw"].url.split("/")[2]
    base = f"chrome-extension://{ext_id}/report/report.html?sample=1&address=" + "77%20Example%20Lane%2C%20Testville%2C%20ZZ%2000000"
    rep = env["ctx"].new_page()
    errors = watch_errors(rep)
    rep.goto(base + "#extra")
    assert rep.title() == "Realty Check: 77 Example Lane, Testville, ZZ 00000"
    assert rep.locator(".hero .where").inner_text().splitlines() ==["77 Example Lane", "Testville, ZZ 00000"]
    at_top(rep, "extra")
    # hover a cell: preview; click it: the evidence panel slides in
    cell = rep.locator("#cf-field .cf-cell.cf-contra").first
    cell.hover()
    rep.locator("#cf-tip:not([hidden])").wait_for(timeout=3000)
    cell.click()
    rep.locator("#cf-panel:not([hidden])").wait_for(timeout=3000)
    assert "claim 1 of 7" in rep.locator("#cf-panel .cf-where").inner_text()  # 2 Beds: Matterport contradicts it
    rep.locator("#cf-panel .cf-x").click()
    # a claim with no finding block: #claim-<id> falls back to the claim field
    rep.goto(base + "#claim-c4")
    at_top(rep, "cf-field")
    rep.close()
    assert not errors, errors


def test_host_css_does_not_leak(env):
    page, errors = open_page(env, "listing_dark.html")
    page.locator("#rc-host #tabFace").click()
    page.wait_for_timeout(300)
    style = page.locator("#rc-host #drawer").evaluate(
        "e => { const s = getComputedStyle(e); return [s.fontFamily, s.backgroundColor, s.letterSpacing, getComputedStyle(e.querySelector('.brand')).textTransform]; }")
    font, bg, spacing, tt = style
    assert "Newsreader" in font and "Georgia" in font and "Courier" not in font, font
    assert bg == "rgb(255, 255, 255)", bg
    assert spacing == "normal" and tt == "none", style
    assert not errors, errors
    page.close()


def test_stub_server_through_service_worker(env):
    stub = H.StubChecker()
    try:
        H.set_server(env["sw"], "http://127.0.0.1:8787")
        page, errors = open_page(env, "listing.html")
        # point the extension at the stub through the gear field
        page.locator("#rc-host #tabFace").click()
        page.locator("#rc-host #btnGear").click()
        page.locator("#rc-host #srv").fill(stub.url)
        page.locator("#rc-host #srvSave").click()
        page.locator("#rc-host #srvHint", has_text="Saved.").wait_for(timeout=3000)
        page.locator("#rc-host #btnCollapse").click()
        page.wait_for_timeout(300)

        page.locator("#rc-host .addr-mark").first.click()
        page.locator("#rc-host .pill").click()
        page.locator("#rc-host #headline").wait_for(timeout=15000)
        assert headline(page) == "2 things don't match."  # one not true, one close

        snap = stub.requests[-1]
        assert set(snap) == {"url", "title", "address_text", "text", "jsonld", "address_candidates", "address_locked"}
        assert snap["address_text"] == "418 Linden Grove Ave, Houston, TX 77006"
        assert snap["address_locked"] is True  # clicked on the page: the person chose it
        cand = snap["address_candidates"]
        assert 1 <= len(cand) <= 5 and set(cand[0]) == {"text", "score", "where", "why"}
        assert cand[0]["text"] == snap["address_text"]
        assert snap["url"].endswith("/listing.html") and 0 < len(snap["text"]) <= 60000
        assert snap["jsonld"][0]["@type"] == "ApartmentComplex"
        assert page.locator("#rc-host .claim-mark").count() >= 3

        # a finding scrolls to and flashes its words on the page
        page.locator('#rc-host .find[data-id="c1"]').click()
        page.locator("#rc-host .claim-mark.flash").first.wait_for(state="attached", timeout=3000)

        with env["ctx"].expect_page() as newp:
            page.locator("#rc-host #btnFull").click()
        rep = newp.value
        rep.wait_for_selector("#headline")
        assert rep.url == stub.url + "/report/r1"
        rep.close()
        assert not errors, errors
        page.close()
    finally:
        stub.close()


DEMO = {  # page -> (full address, claim quotes the stub returns, phrases the snapshot text must contain)
    "demo_houston.html": ("1820 Marrow Bend St, Houston, TX 77002", ["5-story building", "Built in 2018", "184 apartments"],
                          ["5-story building", "Built in 2018", "184 apartments", "Resort-style pool", "Garage parking"]),
    "demo_la.html": ("2750 Corbel Ave, Los Angeles, CA 90064", ["2 Beds · 1 Bath · 815 sq ft", "Plan E", "Saltwater pool"],
                     ["Plan E", "2 Beds · 1 Bath · 815 sq ft", "Saltwater pool"]),
}


def test_demo_pages_detect_full_address(env):
    for name, (full, _, _) in DEMO.items():
        page, errors = open_page(env, name)
        assert page.locator("#rc-host").get_attribute("data-count") == "1"
        page.locator("#rc-host #tabFace").click()
        assert page.locator("#rc-host .pick .pa").all_inner_texts() == [full]
        assert not errors, errors
        page.close()


def test_demo_pages_snapshot_and_marks(env):
    for name, (full, quotes, phrases) in DEMO.items():
        stub = H.StubChecker(delay=0.05, quotes=quotes)
        try:
            H.set_server(env["sw"], stub.url)
            page, errors = open_page(env, name)
            page.locator("#rc-host .addr-mark").first.click()
            page.locator("#rc-host .pill").click()
            page.locator("#rc-host #headline").wait_for(timeout=15000)
            snap = stub.requests[-1]
            assert snap["address_text"] == full
            for ph in phrases:
                assert ph in snap["text"], ph
            verdicts = set(page.locator("#rc-host .claim-mark").evaluate_all("els => els.map(e => e.dataset.verdict)"))
            assert verdicts == {"contradicted", "close", "confirmed"}, verdicts  # every stub quote got a mark on the page
            assert not errors, errors
            page.close()
        finally:
            stub.close()
