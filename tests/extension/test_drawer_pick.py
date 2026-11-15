"""The drawer's address line ("Checking: ..."), the "Not this?" picker, the server's claim field cells, proof pictures
and the fresh-run mark, driven by invented Event runs through a stub checker (local fixture page only).

Run: .venv/Scripts/python.exe -m pytest tests/extension/test_drawer_pick.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawer_events as D  # noqa: E402
import rc_harness as H  # noqa: E402

NAME = "Larkspur Example Commons"
ALT = "77 Example Lane, Testville, ZZ 00000"
ALT_NAME = "The Alder Example"
SRCS = ("record", "scan", "aerial", "county")
# server cell states (server/claim_field.py) -> the drawer's data-s
DRAWER = {"match": "agree", "contra": "contra", "close": "close", "data": "data", "none": "none",
          "differ": "none"}  # a state the drawer does not know draws as none


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


def field_for(claims):
    """An invented server field whose states differ from what the drawer's own running rules would show."""
    cycle = ["match", "contra", "close", "data", "none", "differ"]
    out = []
    for i, c in enumerate(claims):
        cells = {s: cycle[(i + j) % len(cycle)] for j, s in enumerate(SRCS)}
        # even columns send the blue's strength (medium); odd ones none: contra full, close faint
        shade = {s: "medium" for s, st in cells.items() if st in ("contra", "close")} if i % 2 == 0 else {}
        out.append({"id": c["id"], "cells": cells, "shade": shade, "image": None})
    return {"claims": out}


def events(address, conf=0.9, cache="on", with_field=True):
    evs = D.run_events(address, bad=("c1", "c2"), n=8, cant=1, facts=0)
    evs[0]["address_pick"] = {"chosen": address, "confidence": conf, "why": "Matches the building named in the page title.",
                              "alternatives": [{"text": ALT, "name": ALT_NAME, "why": "Also on the page."}]}
    evs[0]["cache"] = cache
    claims = next(e for e in evs if e["type"] == "claims")["claims"]
    for e in evs:
        if e["type"] == "match":
            e["match"]["name"] = NAME
        if e["type"] == "verdict" and e["verdict"]["claim_id"] == "c1":
            e["verdict"]["image"] = "/img/t1"  # the stub serves it
        if e["type"] == "verdict" and e["verdict"]["claim_id"] == "c2":
            e["verdict"]["image"] = "/img/0123456789abcdef"  # the stub answers 404: the picture slot goes away
        if e["type"] == "done" and with_field:
            e["field"] = field_for(claims)
    return evs


class Page:
    def __init__(self, env, **kw):
        self.kw = kw
        self.stub = H.StubChecker(delay=0.02, events=lambda a: events(a, **self.kw))
        H.set_server(env["sw"], self.stub.url)
        self.page = env["ctx"].new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"{env['base']}/listing.html")
        self.page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)

    def R(self, sel):
        return self.page.locator("#rc-host " + sel)

    def start_generic(self):
        self.R("#tabFace").click()
        self.R("#checkThis").click()
        self.R("#headline").wait_for(timeout=20000)

    def close(self):
        self.page.close()
        self.stub.close()


@pytest.fixture
def make(env):
    made = []

    def mk(**kw):
        p = Page(env, **kw)
        made.append(p)
        return p
    yield mk
    for p in made:
        p.close()


def test_generic_button_is_not_locked_and_line_shows(make):
    p = make()
    p.start_generic()
    snap = p.stub.requests[-1]
    assert "address_locked" not in snap
    assert snap["address_candidates"] and snap["address_candidates"][0]["text"] == snap["address_text"]
    line = " ".join(p.R("#chkLine").inner_text().split())
    assert line == f"Checking: {NAME} · {snap['address_text']} · Matches the building named in the page title."
    assert "not sure" not in line
    assert p.R("#alts").count() == 0  # sure enough: the picker stays shut
    assert p.R("#pickOther").get_attribute("aria-expanded") == "false"
    p.R("#pickOther").click()
    assert p.R("#alts .alt").count() == 1
    assert " ".join(p.R("#alts .alt").inner_text().split()) == f"{ALT} {ALT_NAME}"
    assert not p.errors, p.errors


def test_low_confidence_opens_picker_and_restart_locks(make):
    p = make(conf=0.3)
    p.start_generic()
    assert "We're not sure this is the right building." in p.R("#chkLine").inner_text()
    assert p.R("#pickOther").get_attribute("aria-expanded") == "true"
    n = len(p.stub.requests)
    p.R("#alts .alt").first.click()
    p.R("#headline").wait_for(timeout=20000)
    assert len(p.stub.requests) == n + 1
    snap = p.stub.requests[-1]
    assert snap["address_text"] == ALT and snap["address_locked"] is True
    assert ALT in p.R("#chkLine").inner_text()
    assert not p.errors, p.errors


def test_cells_are_the_servers(make):
    p = make()
    p.start_generic()
    claims = next(e for e in events("x") if e["type"] == "claims")["claims"]
    want = field_for(claims)["claims"]
    got = p.R("#cols .col").evaluate_all(
        "cols => cols.map(c => [c.dataset.id, [...c.querySelectorAll('.cell')].map(x => x.dataset.s)])")
    assert got == [[w["id"], [DRAWER[w["cells"][s]] for s in SRCS]] for w in want]
    p.page.wait_for_timeout(2500)  # the cells colour in one after another
    # blue cells: one blue, its strength by the server's shade (else contra full, close faint)
    sh = p.R("#cols .col").evaluate_all(
        "cols => cols.map(c => [...c.querySelectorAll('.cell')].map(x => x.dataset.sh || ''))")
    for i, (w, row) in enumerate(zip(want, sh)):
        for s, got_sh in zip(SRCS, row):
            st = w["cells"][s]
            exp = "" if st not in ("contra", "close") else "medium" if i % 2 == 0 else "high" if st == "contra" else "low"
            assert got_sh == exp, (w["id"], s, st, got_sh)
    look = lambda sel: p.R(sel).first.evaluate(  # noqa: E731
        "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).opacity]")
    # the report's own shades (server/claim_field.py: --cf-blue, --cf-b2, --cf-b3), solid
    assert look('.cell[data-s="close"][data-sh="low"]') == ["rgb(188, 203, 247)", "1"]
    assert look('.cell[data-s="contra"][data-sh="high"]') == ["rgb(26, 75, 224)", "1"]
    assert look('.cell[data-sh="medium"]') == ["rgb(106, 139, 238)", "1"]
    # only black, blue by shade or light grey: never a hollow outlined cell
    assert p.R("#cols .cell").evaluate_all(
        "els => els.filter(e => getComputedStyle(e).boxShadow !== 'none' || getComputedStyle(e).backgroundColor === 'rgb(255, 255, 255)').length") == 0
    assert p.R('.cell[data-s="differ"]').count() == 0


def test_drawer_is_text_only(make):
    p = make()
    p.start_generic()
    p.page.wait_for_timeout(800)
    # proof pictures stay in the full report: the drawer shows none, and links to the proof instead
    assert p.R("#body img, #body .pic, #body iframe, #body canvas").count() == 0
    assert p.R('.fr[data-id="c1"] .lproof').inner_text() == "Proof"
    assert not p.errors, p.errors


def test_fresh_run_mark(make):
    p = make(cache="off")
    p.start_generic()
    assert p.R('#counts .fresh').inner_text() == "fresh run"
    q = make()
    q.start_generic()
    assert q.R('#counts .fresh').count() == 0


def test_no_field_falls_back_to_drawer_rules(make):
    p = make(with_field=False)
    p.start_generic()
    states = set(p.R("#cols .cell").evaluate_all("els => els.map(e => e.dataset.s)"))
    assert states <= {"agree", "contra", "data", "none"}
    assert not p.errors, p.errors
