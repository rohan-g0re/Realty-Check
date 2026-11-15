"""The drawer on its own: every mismatch with its why, source and score, "Not in the listing", the questions to ask,
what matched and what nobody could check, all as text; rows while the run goes; never on the checker's own pages.

Invented Event runs (drawer_events.py) through a stub checker, local fixture page only. Skips without Playwright.
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_drawer_full.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawer_events as D  # noqa: E402
import rc_harness as H  # noqa: E402

ADDRESS = "418 Linden Grove Ave, Houston, TX 77006"
QS = ["The listing says “2 Beds” for Plan E, but the 3D scan shows 1 bedroom and a den. Which is right?",
      "The listing says “5-story building”, but a roof height measurement suggests 6 floors. Which is right?"]


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


def with_report(address, pause=0.0, name=None):
    """Invented events whose `done` carries the server's text report (done.report, as server/app.py builds it)."""
    evs = D.run_events(address, bad=("c1", "c2"), n=8, cant=1, facts=0)
    for e in evs:
        if e["type"] == "match" and name:
            e["match"]["name"] = name
        if e["type"] == "verdict" and e["verdict"]["claim_id"] == "c1":
            e["verdict"]["confidence"] = {"score": 82, "band": "high", "decided_by": "scan", "why": "Invented.",
                                          "sources": [{"source": "scan", "says": "1 bedroom + den", "agrees_with_listing": False},
                                                      {"source": "record", "says": "2 bedrooms", "agrees_with_listing": True}]}
        if e["type"] == "verdict" and e["verdict"]["claim_id"] == "c2":
            e["verdict"]["confidence"] = {"band": "medium", "decided_by": "aerial"}  # no number: the word only
    verdicts = [e["verdict"] for e in evs if e["type"] == "verdict"]
    done = evs[-1]
    done["harness"] = {"top_mismatch": {"claim_id": "c1", "why": "Invented.", "by": "rule"}}
    done["report"] = {
        "rows": [
            {"id": "c1", "verdict": "contradicted", "kind": "bedrooms", "label": "Bedrooms · Plan E", "said": "2 Beds",
             "quoted": True, "strike": True, "found": "1 bedroom and a den", "why": "The 3D scan of the same plan shows one bedroom.",
             "credit": ["scan"], "score": 82, "band": "high", "proof": "/report/r1#claim-c1", "walk": "/tour/r1?claim=c1"},
            {"id": "c2", "verdict": "contradicted", "kind": "stories", "label": "Floors", "said": "5-story building",
             "quoted": True, "strike": True, "found": "6 floors", "why": "The roof is about 23 m up.", "credit": ["aerial"],
             "score": None, "band": "medium", "proof": "/report/r1#claim-c2", "walk": None},
        ],
        "facts": [{"label": "Elevators", "value": "2 elevators", "why": "Moving in is easier.", "sources": ["CoStar"]}],
        "questions": QS,
        "ok": [{"id": v["claim_id"], "label": "", "text": v["claimed"]} for v in verdicts if v["verdict"] == "confirmed"],
        "cant": [{"id": v["claim_id"], "label": "", "text": v["claimed"], "why": "Nothing in our data measures this."}
                 for v in verdicts if v["verdict"] == "cant_check"],
    }
    if pause:
        evs.insert(len(evs) - 1, {"_pause": pause})
    return evs


class P:
    def __init__(self, env, events, wait_done=True):
        self.stub = H.StubChecker(delay=0.02, events=events)
        H.set_server(env["sw"], self.stub.url)
        self.page = env["ctx"].new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"{env['base']}/listing.html")
        self.page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)
        self.page.locator("#rc-host .addr-mark").first.click()
        self.page.locator("#rc-host .pill").click()
        if wait_done:
            self.R("#headline").wait_for(timeout=20000)
            self.page.wait_for_timeout(300)

    def R(self, sel):
        return self.page.locator("#rc-host " + sel)

    def close(self):
        self.page.close()
        self.stub.close()


@pytest.fixture
def make(env):
    made = []

    def mk(events, **kw):
        p = P(env, events, **kw)
        made.append(p)
        return p
    yield mk
    for p in made:
        p.close()


def test_rows_say_what_the_report_says(env, make):
    p = make(lambda a: with_report(a))
    assert p.R(".find").evaluate_all("els => els.map(e => e.dataset.id)") == ["c1", "c2"]
    row = p.R('.fr[data-id="c1"]')
    assert row.locator(".fk").inner_text().lower() == "bedrooms · plan e"
    assert row.locator("s.wt").inner_text() == "“2 Beds”"
    assert row.locator(".is").inner_text() == "1 bedroom and a den"
    assert row.locator(".fy").inner_text() == "The 3D scan of the same plan shows one bedroom."
    # citations as the report shows them: "Source", the deciding one emphasised, then "also checked" ones, all links
    names = lambda sel: row.locator(sel).evaluate_all("els => els.map(e => e.firstChild.textContent)")  # noqa: E731
    assert row.locator(".citl").evaluate_all("els => els.map(e => e.textContent)") == ["Source", "also checked"]
    assert names(".cite.dec") == ["Matterport"] and names(".cite:not(.dec)") == ["CoStar"]
    assert row.locator("a.cite").evaluate_all("els => els.map(e => [e.target, e.rel])") == [["_blank", "noopener"]] * 2
    # the score is the row's right-hand element, a big serif numeral; no number sent: the level word only
    assert row.locator(".ftag .scn").inner_text() == "82" and row.locator(".ftag .scu").inner_text() == "/ 100 · high"
    assert float(row.locator(".scn").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")) >= 26
    assert "Doesn't match" not in row.inner_text()
    c2 = p.R('.fr[data-id="c2"]')
    assert c2.locator(".ftag").inner_text() == "Medium" and c2.locator(".scn").count() == 0
    assert " ".join(row.locator(".cfb").inner_text().split()) == "Why this score"
    # links only: proof in the report, the source's evidence page, the 3D walk where there is one
    assert p.R('.fr[data-id="c2"] .l3d').count() == 0
    for sel, want in ((".lproof", "/report/r1#claim-c1"), (".cite.dec", "/artifacts/r1#a-c1-matterport"),
                      (".l3d", "/tour/r1?claim=c1")):
        with env["ctx"].expect_page() as newp:
            row.locator(sel).click()
        tab = newp.value
        tab.wait_for_load_state()
        assert tab.url == p.stub.url + want, (sel, tab.url)
        tab.close()
    assert p.R("#body img, #body iframe, #body canvas").count() == 0  # text only
    assert not p.errors, p.errors


def test_questions_copy_and_checked_lines(env, make):
    env["ctx"].grant_permissions(["clipboard-read", "clipboard-write"], origin=env["base"])
    p = make(lambda a: with_report(a))
    assert p.R("#askH").inner_text().lower() == "ask before you tour"
    assert p.R(".q .qt").all_inner_texts() == QS
    p.R(".qcopy").first.click()
    assert p.R(".qcopy").first.inner_text() == "Copied"
    assert p.page.evaluate("navigator.clipboard.readText()") == QS[0]
    p.R("#qAll").click()
    assert p.R("#qAll").inner_text() == "Copied"
    assert p.page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n") == "\n".join(QS)  # Windows: CRLF
    p.page.wait_for_timeout(1600)
    assert p.R("#qAll").inner_text() == "Copy all 2"
    # "N confirmed" and "N couldn't check": shut, then one plain line each
    ok, cant = p.R("#okToggle"), p.R("#cantToggle")
    assert ok.inner_text().strip() == "5 confirmed" and cant.inner_text().strip() == "1 couldn't check"
    assert ok.get_attribute("aria-expanded") == "false" and p.R(".ckr").count() == 0
    ok.click()
    assert p.R("#okToggle").get_attribute("aria-expanded") == "true"
    assert p.R("#okList .ckr").count() == 5
    p.R("#cantToggle").click()
    assert p.R("#cantList .ckw").inner_text() == "Nothing in our data measures this."
    assert p.R("#fiList .fact .fv").all_inner_texts() == ["2 elevators"]
    assert p.R("#btnFull").is_visible()  # pinned below the scrolling list
    assert not p.errors, p.errors


def test_rows_appear_while_the_run_goes(make):
    p = make(lambda a: with_report(a, pause=3.0), wait_done=False)
    p.R("#liveFinds .fr").nth(1).wait_for(timeout=15000)
    assert p.R("#headline").count() == 0  # still running
    assert p.R("#liveFinds .find").evaluate_all("els => els.map(e => e.dataset.id)") == ["c1", "c2"]
    assert p.R("#liveFinds .lproof, #liveFinds .srcl").count() == 0  # links once the report exists
    assert p.R("#steps li").count() >= 1
    p.R("#headline").wait_for(timeout=15000)
    assert p.R("#finds .fr").count() == 2
    assert not p.errors, p.errors


def test_sample_style_fallback_cuts_manager(make):
    def evs(a):
        out = D.run_events(a, bad=("c2",), n=8, cant=1, facts=0)
        out[-1]["harness"] = {"top_facts": [
            {"label": "Elevators", "value": "2 elevators; managed by Example Co", "why": "Invented.", "sources": ["CoStar"]},
            {"label": "Property manager", "value": "Example Co", "sources": ["CoStar"]},
            {"label": "Lot size", "value": "1 acre", "sources": ["County"]}]}
        return out
    p = make(evs)
    assert p.R(".fact .fl").all_inner_texts() == ["Elevators"]
    assert p.R(".fact .fv").inner_text() == "2 elevators"
    assert "Example Co" not in p.R("#body").inner_text()
    # no server report: the drawer drafts the questions with the report's own template
    assert p.R(".q .qt").first.inner_text() == \
        "The listing says “5-story building”, but a roof height measurement suggests 6 floors. Which is right?"
    assert not p.errors, p.errors


def test_address_shown_once(make):
    p = make(lambda a: with_report(a, name="418 Linden Grove"))
    assert " ".join(p.R("#chkLine").inner_text().split()) == f"Checking: {ADDRESS}"
    q = make(lambda a: with_report(a, name="Larkspur Example Commons"))
    assert " ".join(q.R("#chkLine").inner_text().split()) == f"Checking: Larkspur Example Commons · {ADDRESS}"


def test_not_on_the_checkers_own_pages(env):
    stub = H.StubChecker()
    try:
        for path in ("/report/r1", "/tour/r1?claim=c1"):
            page = env["ctx"].new_page()
            page.goto(stub.url + path)
            page.wait_for_timeout(1200)
            assert page.locator("#rc-host").count() == 0, path  # no floating button over the report
            page.close()
        page = env["ctx"].new_page()  # a listing still gets it
        page.goto(f"{env['base']}/listing.html")
        page.wait_for_selector("#rc-host", state="attached", timeout=10000)
        page.close()
    finally:
        stub.close()


def with_previews(address):
    """with_report, the first row carrying the report's preview cards (invented); the second an old run's {}."""
    evs = with_report(address)
    rows = evs[-1]["report"]["rows"]
    rows[0]["previews"] = {
        "scan": {"source": "Matterport", "role": "decided", "tag": "Decided this", "value": "1 bedroom + den",
                 "read": "One closed bedroom; the second room has no door.", "where": "3D scan of Plan E",
                 "date": "Mar 2026", "href": "/artifacts/r1#a-c1-matterport"},
        "record": {"source": "CoStar", "role": "agrees", "tag": "Agrees", "value": "2 bedrooms",
                   "read": "The building record lists Plan E as 2 bedrooms.", "where": "Building record", "date": "",
                   "href": None},
    }
    rows[1]["previews"] = {}
    return evs


def test_source_preview_cards(env, make):
    p = make(lambda a: with_previews(a))
    row = p.R('.fr[data-id="c1"]')
    dec = row.locator(".cite.dec")
    assert dec.get_attribute("aria-expanded") == "false" and dec.get_attribute("aria-controls") == "rcCard"
    card = p.R("#rcCard")
    assert card.is_hidden()
    dec.click()  # a click (or tap) opens it pinned
    card.wait_for(state="visible")
    assert dec.get_attribute("aria-expanded") == "true"
    assert "r-decided" in card.get_attribute("class") and card.get_attribute("role") == "dialog"
    assert card.locator(".cc-h b").inner_text().lower() == "matterport" and card.locator(".cc-h em").inner_text().lower() == "decided this"
    assert card.locator(".cc-v").inner_text() == "1 bedroom + den"
    assert card.locator(".cc-read").inner_text() == "One closed bedroom; the second room has no door."
    assert card.locator(".cc-w").inner_text() == "3D scan of Plan E · Mar 2026"
    assert card.locator(".cc-v").evaluate("e => getComputedStyle(e).color") == "rgb(26, 75, 224)"
    go = card.locator(".cc-go")
    assert go.get_attribute("data-href") == "/artifacts/r1#a-c1-matterport" and go.get_attribute("target") == "_blank"
    # inside the drawer, never past its edges or the viewport
    box, dbox = card.bounding_box(), p.R("#drawer").bounding_box()
    vw = p.page.viewport_size["width"]
    assert box["x"] >= max(dbox["x"], 0) and box["x"] + box["width"] <= min(dbox["x"] + dbox["width"], vw) + 0.5, (box, dbox)
    with env["ctx"].expect_page() as newp:
        go.click()
    tab = newp.value
    tab.wait_for_load_state()
    assert tab.url == p.stub.url + "/artifacts/r1#a-c1-matterport"
    tab.close()
    # Esc closes the card (not the drawer) and gives focus back to the chip without reopening it
    p.page.bring_to_front()
    dec.focus()
    p.page.keyboard.press("Escape")
    assert card.is_hidden() or "on" not in (card.get_attribute("class") or "")
    p.page.wait_for_timeout(250)
    assert card.is_hidden() and p.R("#drawer.open").count() == 1
    # keyboard: Space pins it and moves focus into the card; Esc closes and returns focus
    dec.press(" ")
    card.wait_for(state="visible")
    assert p.page.evaluate("() => document.getElementById('rc-host').shadowRoot.activeElement.className") == "cc-go"
    p.page.keyboard.press("Escape")
    p.page.wait_for_timeout(250)
    assert card.is_hidden() and dec.get_attribute("aria-expanded") == "false"
    assert p.page.evaluate("() => document.getElementById('rc-host').shadowRoot.activeElement.getAttribute('data-cc')") == "scan"
    # hover opens the other source's card; no library link when the server sent none; an outside click closes it
    also = row.locator(".cite:not(.dec)")
    p.page.mouse.move(0, 0)
    also.hover()
    card.wait_for(state="visible")
    assert card.locator(".cc-h b").inner_text().lower() == "costar" and "r-agrees" in card.get_attribute("class")
    assert card.locator(".cc-go").count() == 0 and card.locator(".cc-w").inner_text() == "Building record"
    p.R("#mmH").click()
    p.page.wait_for_timeout(250)
    assert card.is_hidden()
    # an old run's row ({} previews): plain chips, as before
    c2 = p.R('.fr[data-id="c2"]')
    assert c2.locator("[data-cc]").count() == 0 and c2.locator(".cw").count() == 0
    assert c2.locator("a.cite.dec").get_attribute("data-href") == "/artifacts/r1#a-c2-vexcel"
    assert not p.errors, p.errors
