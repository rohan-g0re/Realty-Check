"""End-to-end tests for the Reality Check extension (headless bundled Chromium, local fixture pages only).

Run: .venv/Scripts/python.exe -m pytest tests/extension -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402

playwright_sync = pytest.importorskip("playwright.sync_api")
DRAWER_W = 380
HEADLINE = "Listed as 2 bedrooms. The 3D scan shows 1 bedroom and a den."
COLORS = {"contradicted": "#dc2626", "close": "#d97706", "confirmed": "#16a34a", "cant_check": "#a3a3a3"}


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
    page.locator("#rc-host .claim").nth(3).wait_for(timeout=10000)
    page.locator('#rc-host .claim[data-verdict="contradicted"]').first.wait_for(timeout=10000)
    page.locator("#rc-host #headline").wait_for(timeout=15000)
    # mismatches first, then matches; the one "can't verify" row folds under a single line
    order = page.locator("#rc-host .claim").evaluate_all("els => els.map(e => e.dataset.verdict)")
    assert order == ["contradicted", "contradicted", "contradicted", "confirmed"], order
    assert page.locator("#rc-host #ccToggle").inner_text().strip() == "Can't verify (1)"
    page.locator("#rc-host #ccToggle").click()
    assert page.locator("#rc-host .claim").count() == 5
    assert page.locator("#rc-host #headline").inner_text() == HEADLINE
    counts = page.locator("#rc-host .cnt b").all_inner_texts()
    assert counts == ["3", "1", "1"]  # Mismatch, Match, Can't verify
    verdicts = page.locator("#rc-host .claim").evaluate_all("els => els.map(e => e.dataset.verdict)")
    assert sorted(verdicts) == ["cant_check", "confirmed", "contradicted", "contradicted", "contradicted"]

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

    # click a mark: drawer shows that claim expanded
    red.click()
    assert page.locator("#rc-host .claim.open").count() >= 1

    with env["ctx"].expect_page() as newp:
        page.locator("#rc-host #btnFull").click()
    rep = newp.value
    rep.wait_for_selector("#headline")
    assert rep.locator("#headline").inner_text() == HEADLINE
    rep.close()
    assert not errors, errors
    page.close()


def test_host_css_does_not_leak(env):
    page, errors = open_page(env, "listing_dark.html")
    page.locator("#rc-host #tabFace").click()
    page.wait_for_timeout(300)
    style = page.locator("#rc-host #drawer").evaluate(
        "e => { const s = getComputedStyle(e); return [s.fontFamily, s.backgroundColor, s.letterSpacing, getComputedStyle(e.querySelector('.brand')).textTransform]; }")
    font, bg, spacing, tt = style
    assert "Segoe UI" in font and "Courier" not in font, font
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
        assert page.locator("#rc-host #headline").inner_text() == H.STUB_HEADLINE

        snap = stub.requests[-1]
        assert set(snap) == {"url", "title", "address_text", "text", "jsonld"}
        assert snap["address_text"] == "418 Linden Grove Ave, Houston, TX 77006"
        assert snap["url"].endswith("/listing.html") and 0 < len(snap["text"]) <= 60000
        assert snap["jsonld"][0]["@type"] == "ApartmentComplex"
        assert page.locator("#rc-host .claim-mark").count() >= 3

        # proof image comes back through the service worker as a data URL
        page.locator('#rc-host .claim[data-id="c1"] .row1').click()
        img = page.locator('#rc-host .claim[data-id="c1"] img.proof')
        img.wait_for(timeout=5000)
        assert img.evaluate("i => i.complete && i.naturalWidth") == 64

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
    "houston_austin.html": ("2111 Austin St, Houston, TX 77002", ["5-story building", "Built in 2019", "216 apartments"],
                            ["5-story building", "Built in 2019", "216 apartments", "Resort-style pool", "Garage parking"]),
    "la_sepulveda.html": ("3700 Sepulveda Blvd, Los Angeles, CA 90034", ["2 Beds · 1 Bath · 855 sq ft", "Plan E", "Saltwater pool"],
                          ["Plan E", "2 Beds · 1 Bath · 855 sq ft", "Saltwater pool"]),
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
