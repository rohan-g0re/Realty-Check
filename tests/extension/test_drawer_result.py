"""The drawer's two states (Running, Result) driven by invented Event runs (drawer_events.py) through a stub checker.

Headless bundled Chromium with the unpacked extension, local fixture page only. Skips without Playwright.
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_drawer_result.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawer_events as D  # noqa: E402
import rc_harness as H  # noqa: E402


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


class Run:
    def __init__(self, env, wait_done=True, reduced=False, delay=0.02, **kw):
        self.stub = H.StubChecker(delay=delay, events=lambda a: D.run_events(a, **kw))
        H.set_server(env["sw"], self.stub.url)
        self.page = env["ctx"].new_page()
        if reduced:
            self.page.emulate_media(reduced_motion="reduce")
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"{env['base']}/listing.html")
        self.page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)
        self.page.locator("#rc-host .addr-mark").first.click()
        self.page.locator("#rc-host .pill").click()
        if wait_done:
            self.R("#headline").wait_for(timeout=20000)

    def R(self, sel):
        return self.page.locator("#rc-host " + sel)

    def headline(self):
        return " ".join(self.R("#headline").inner_text().split())

    def cell(self, cid, src):
        return self.R(f'.col[data-id="{cid}"] .cell[data-src="{src}"]').get_attribute("data-s")

    def close(self):
        self.page.close()
        self.stub.close()


@pytest.fixture
def run(env):
    made = []

    def make(**kw):
        r = Run(env, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


# ---------- headline variants ----------

def test_many_findings_every_row(run):
    r = run()  # 34 claims: 4 not true, 1 close, 4 couldn't check
    assert r.headline() == "5 things don't match."
    assert r.R("#headline").get_attribute("data-n") == "5"
    meta = r.R("#counts").inner_text()
    for part in ("25 true", "5 don't match", "4 couldn't check"):  # a close one counts as a mismatch
        assert part in meta, meta
    assert "close" not in meta, meta
    finds = r.R(".find")
    assert finds.count() == 5  # every mismatch, no cap: the list scrolls inside the drawer
    assert r.R("#moreF").count() == 0
    # the most certain first (no confidence sent: a contradicted one counts as sure, a close one as unsure)
    assert finds.evaluate_all("els => els.map(e => [e.dataset.id, e.dataset.verdict])") == [
        ["c1", "contradicted"], ["c2", "contradicted"], ["c4", "contradicted"], ["c6", "contradicted"], ["c3", "close"]]
    assert finds.first.locator(".fk").inner_text().lower() == "bedrooms · plan e"
    assert finds.first.locator(".wt").inner_text() == "“2 Beds”"
    assert finds.first.locator("s.wt").count() == 1  # struck through: it doesn't match
    assert r.R('.find[data-id="c3"] s').count() == 0  # close: never struck
    # one blue, fainter when less sure: the close one faint, the tag the same words for both
    assert r.R('.fr[data-id="c3"]').get_attribute("data-shade") == "low"
    assert r.R('.fr[data-id="c1"]').get_attribute("data-shade") == "high"
    assert r.R('.fr[data-id="c3"] .ftag').inner_text() == r.R('.fr[data-id="c1"] .ftag').inner_text() == "Doesn't match"
    blue = lambda sel: r.R(sel).evaluate("e => getComputedStyle(e).color")  # noqa: E731
    assert blue('.fr[data-id="c1"] .is') == "rgb(26, 75, 224)" and blue('.fr[data-id="c3"] .is') != "rgb(26, 75, 224)"
    assert "Close" not in r.R('.fr[data-id="c3"]').inner_text()
    assert finds.first.locator(".is").inner_text() == "1 bedroom and a den"
    assert r.R('.fr[data-id="c1"] .fy').inner_text() == "Invented reason."  # one short why line
    assert r.R("#mmH").inner_text().lower() == "what doesn't match"
    with r.page.context.expect_page() as newp:
        r.R('.fr[data-id="c2"] .lproof').click()
    rep = newp.value
    rep.wait_for_load_state()
    assert rep.url == r.stub.url + "/report/r1#claim-c2"
    rep.close()
    assert r.R("#body").evaluate("e => e.scrollHeight > e.clientHeight")  # it scrolls; the button stays put
    assert r.R("#btnFull").is_visible()
    assert r.R("#btnFull").inner_text() == "Open the full report"
    assert not r.errors, r.errors


def test_one_finding(run):
    r = run(bad=("c2",), n=8, cant=1, facts=3)
    assert r.headline() == "1 thing doesn't match."
    assert r.R(".find").count() == 1
    assert r.R("#moreF").count() == 0
    f = r.R(".find").first
    assert f.locator(".wt").inner_text() == "“5-story building”"
    assert f.locator(".is").inner_text() == "6 floors"
    assert r.R(".fr .bys").first.inner_text() == "Source: Vexcel"  # CoStar and County say 5 stories: they back the listing
    # the headline stays on two lines whatever the font
    box = r.R("#headline").bounding_box()
    size = float(r.R("#headline").evaluate("e => parseFloat(getComputedStyle(e).fontSize)"))
    assert box["height"] < size * 2.3, (box, size)
    assert not r.errors, r.errors


def test_everything_matches_is_calm(run):
    r = run(bad=(), n=12, cant=3, facts=4)
    assert r.headline() == "Everything we could check matches."
    assert "calm" in r.R("#headline").get_attribute("class")
    assert r.R(".find").count() == 0 and r.R("#moreF").count() == 0
    meta = r.R("#counts").inner_text()
    assert "9 true" in meta and "3 couldn't check" in meta and "close" not in meta, meta
    assert r.R('.cell[data-s="contra"]').count() == 0  # no blue anywhere
    assert not r.errors, r.errors


def test_no_match(run):
    r = run(bad=(), n=5, no_match=True, facts=0)
    assert r.headline() == "We couldn't find this building."
    assert r.R("#fi").count() == 0
    assert r.R('.cell:not([data-s="none"])').count() == 0
    assert not r.errors, r.errors


# ---------- claim field ----------

def test_field_cells_before_and_after_verdicts(run):
    r = run(wait_done=False, pause_before_verdicts=2.5)
    r.R('.col[data-id="c2"] .cell[data-src="record"][data-s="data"]').wait_for(state="attached", timeout=15000)
    assert r.R("#cols .col").count() == 34
    assert r.R("#elapsed").inner_text().count(":") == 1
    r.R('.col[data-id="c2"] .cell[data-src="county"][data-s="data"]').wait_for(state="attached", timeout=10000)
    # before any verdict: sources with data are hollow, nothing is coloured
    assert r.R('.cell[data-s="agree"], .cell[data-s="contra"]').count() == 0
    assert r.cell("c2", "scan") == "wait"
    steps = r.R("#steps li")
    assert 1 <= steps.count() <= 5
    assert steps.first.locator(".sk").inner_text() == "County"  # latest on top
    r.R("#headline").wait_for(timeout=20000)
    # stories: aerial says 6 (the verdict's source), CoStar and County say 5 on their own record, no scan data
    assert [r.cell("c2", s) for s in ("record", "scan", "aerial", "county")] == ["agree", "none", "contra", "agree"]
    # year built: County says 2015 (close), CoStar says 2016
    assert [r.cell("c3", s) for s in ("record", "scan", "aerial", "county")] == ["agree", "none", "none", "contra"]
    # bedrooms of Plan E: the scan settles it; CoStar has the plan but per-plan rules are the judge's, so hollow
    assert [r.cell("c1", s) for s in ("record", "scan", "aerial", "county")] == ["data", "contra", "none", "none"]
    # couldn't check, though the aerial photo was looked at for it: hollow, never a match
    assert [r.cell("c7", s) for s in ("record", "scan", "aerial", "county")] == ["none", "none", "data", "none"]
    assert r.R(".fr .bys").first.inner_text() == "Source: Matterport"
    assert not r.errors, r.errors


def test_field_fits_forty_claims(run):
    r = run(n=40, long_quote=True, bad=("c6", "c1", "c2"))
    cols = r.R("#cols")
    assert r.R("#cols .col").count() == 40
    assert cols.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    widths = r.R("#cols .col").evaluate_all("els => els.map(e => e.getBoundingClientRect().width)")
    assert min(widths) >= 3, widths
    # a long listing phrase wraps in full (text is all there is), the drawer never scrolls sideways
    body = r.R("#body")
    assert body.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    wt = r.R('.find[data-id="c6"] .wt')
    assert "soaking tubs and dual vanities" in wt.inner_text()
    assert wt.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    assert not r.errors, r.errors


# ---------- findings ----------

def test_finding_flashes_its_mark_or_opens_report(run):
    r = run(bad=("c1", "c6"), n=8, cant=1, long_quote=True)
    r.R('.find[data-id="c1"]').click()  # "2 Beds" is on the page
    r.R(".claim-mark.flash").first.wait_for(state="attached", timeout=3000)
    with r.page.context.expect_page() as newp:
        r.R('.find[data-id="c6"]').click()  # its words are not on the page: open the report at it
    rep = newp.value
    rep.wait_for_load_state()
    assert rep.url == r.stub.url + "/report/r1#claim-c6"
    rep.close()
    assert not r.errors, r.errors


def test_walkthrough_link_for_scan_finding(run):
    r = run(bad=("c2", "c1"), n=8, cant=1)
    walk = r.R("#btnWalk")
    assert walk.get_attribute("data-id") == "c1"  # the worst finding a 3D scan settled
    with r.page.context.expect_page() as newp:
        walk.click()
    rep = newp.value
    rep.wait_for_load_state()
    assert rep.url == r.stub.url + "/report/r1#claim-c1"  # the report at this finding, its 3D viewer embedded there
    rep.close()


def test_keyboard_reaches_findings_and_button(run):
    r = run(bad=("c2",), n=8, cant=1)
    r.R('.find[data-id="c2"]').focus()
    assert r.page.evaluate("document.getElementById('rc-host').shadowRoot.activeElement.dataset.id") == "c2"
    sizes = r.R(".find, #btnFull, #btnCollapse, #okToggle").evaluate_all(
        "els => els.map(e => Math.min(e.getBoundingClientRect().height, e.getBoundingClientRect().width))")
    assert min(sizes) >= 44, sizes
    r.page.keyboard.press("Escape")
    r.page.wait_for_timeout(500)
    assert not r.R("#drawer").is_visible()


def test_reduced_motion_turns_animation_off(run):
    r = run(reduced=True, n=8, bad=("c1",), cant=1)
    names = r.R(".fr, .hn, .field .cell, .find").evaluate_all("els => els.map(e => getComputedStyle(e).animationName)")
    assert set(names) == {"none"}, names


# ---------- not in the listing ----------

def test_fill_in_shows_in_place(run):
    r = run()
    assert r.R("#fiH").inner_text().lower() == "not in the listing"
    # shown as text right away; never a lot size or a building class
    assert r.R(".fact .fl").all_inner_texts() == ["Plan A", "Pets", "Rooftop", "Asking rent"]
    assert r.R(".fact").first.locator(".fs").inner_text() == "CoStar"
    assert r.R("#fiAll").inner_text() == "More in the full report"
    assert r.R(".find").count() == 5  # the rest of the result stays as it was
    assert not r.errors, r.errors
