"""Esc closes the report's evidence panel while its 3D viewer (a cross-origin frame) has focus.

The real server runs on its own free port with an invented run and a made-up SDK key; it sends each /tour/ frame
to localhost (cross-origin, as in use), where the page is our own embed message page (it says "ready", no
viewer); every Matterport request is blocked, so no scan ever loads. Headless bundled Chromium. Skips without Playwright.
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_report_esc.py -q
"""
import socket
import threading
import time

import pytest

RUN_ID = "20990101-000000-esc001"
FAKE_KEY = "fake-sdk-key-for-tests-only"


def _run():
    claims = [{"id": "c1", "kind": "bedrooms", "value": 2, "unit": None, "scope": "unit", "label": "Plan Z",
               "quote": "2 Beds", "checkable": True},
              {"id": "c5", "kind": "bathrooms", "value": 1, "unit": None, "scope": "unit", "label": "Plan Z",
               "quote": "1 Bath", "checkable": True}]
    scan = {"source": "scan", "status": "ok", "summary": "Invented scan.", "images": [], "detail": "",
            "observed": {"model_id": "SAMPLEscan01", "model_name": "1BR 1BA - 700 SF", "bedrooms": 1, "bathrooms": 1,
                         "plan": "Plan Z"}}
    verdicts = [{"claim_id": "c1", "verdict": "contradicted", "claimed": "2 bedrooms", "found": "1 bedroom",
                 "source": "scan", "reason": "The 3D scan shows 1 bedroom.", "image": None},
                {"claim_id": "c5", "verdict": "confirmed", "claimed": "1 bathroom", "found": "1 bathroom",
                 "source": "scan", "reason": "The 3D scan shows 1 bathroom.", "image": None}]
    events = [{"type": "run_started", "run_id": RUN_ID, "address": "418 Linden Grove Ave"},
              {"type": "claims", "claims": claims}, {"type": "evidence", "evidence": scan}]
    events += [{"type": "verdict", "verdict": v} for v in verdicts]
    events.append({"type": "done", "run_id": RUN_ID, "headline": "x", "counts": {}})
    return {"run_id": RUN_ID, "harness": "fake", "snapshot": {"address_text": "418 Linden Grove Ave"},
            "address_key": "418 linden grove ave", "finished": True, "events": events, "images": {}}


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    ps = pytest.importorskip("playwright.sync_api")
    import uvicorn

    import server.app as srv
    from server import tour

    mp = pytest.MonkeyPatch()
    mp.setenv("RC_PRIVATE_DIR", str(tmp_path_factory.mktemp("private")))
    mp.setenv("MATTERPORT_SDK_KEY", FAKE_KEY)
    mp.setattr(tour, "PREWARM", False)
    # every /tour/ frame is our own embed message page: no spot pick, no viewer, nothing from Matterport
    frame_page = tour.message_page(RUN_ID, "Invented viewer stand-in.", embed=True, state="ready")
    mp.setattr(tour, "spot_for", lambda target, wait_s=None: {"status": "none"})
    mp.setattr(tour, "render_tour", lambda *a, **k: frame_page)
    srv.save_run(_run())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with ps.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:  # browser not installed
                pytest.skip(f"Chromium unavailable: {e}")
            pg = browser.new_page(viewport={"width": 1300, "height": 900})
            pg.route("**/*matterport*/**", lambda r: r.abort())
            pg.goto(f"http://127.0.0.1:{port}/report/{RUN_ID}")
            yield pg
            browser.close()
    finally:
        server.should_exit = True
        mp.undo()


def _open_panel_3d(pg):
    """Open the claim field panel on the claim with a 3D button that mounts inside the panel; return the frame."""
    cells = pg.locator(".cf-cell[data-r='1']:not(.cf-none)")
    for i in range(cells.count()):
        cells.nth(i).click()
        pg.locator("#cf-panel.cf-show").wait_for()
        go = pg.locator("#cf-panel .cf-3d[data-claim='c5']")
        if go.count():
            go.click()
            pg.wait_for_selector("#cf-panel .cf-3dhost iframe")
            frame = pg.frame_locator("#cf-panel .cf-3dhost iframe")
            frame.locator("h1").wait_for()
            return frame
        pg.locator("#cf-panel .cf-x").click()
        pg.wait_for_function("document.getElementById('cf-panel').hidden")
    raise AssertionError("no panel 3D button for c5")


def _focus_frame(pg, frame):
    frame.locator("h1").click()
    assert pg.evaluate("document.activeElement.tagName") == "IFRAME"
    origin = frame.locator("body").evaluate("() => location.origin")
    assert origin.startswith("http://localhost:")  # cross-origin from the report on 127.0.0.1


def test_esc_inside_viewer_closes_panel(page):
    pg = page
    frame = _open_panel_3d(pg)
    _focus_frame(pg, frame)
    pg.keyboard.press("Escape")  # typed inside the frame: only the forwarded message can reach the report
    pg.wait_for_function("document.getElementById('cf-panel').hidden", timeout=3000)
    assert pg.locator("#cf-panel .cf-3dhost iframe").count() == 0


def test_focus_comes_back_when_pointer_leaves_viewer(page):
    pg = page
    frame = _open_panel_3d(pg)
    _focus_frame(pg, frame)
    bar = pg.locator("#cf-panel .cf-where").bounding_box()
    pg.mouse.move(bar["x"] + 5, bar["y"] + 5)
    assert pg.evaluate("document.activeElement.className") == "cf-x"
    pg.keyboard.press("Escape")
    pg.wait_for_function("document.getElementById('cf-panel').hidden", timeout=3000)


def test_message_from_another_origin_is_ignored(page):
    pg = page
    _open_panel_3d(pg)
    pg.evaluate("window.dispatchEvent(new MessageEvent('message', {data: {rc3d: 1, key: 'Escape'}, "
                "origin: 'https://elsewhere.invalid', source: window}))")
    pg.evaluate("window.dispatchEvent(new MessageEvent('message', {data: {rc3d: 1, key: 'Escape'}, "
                "origin: location.origin, source: window}))")  # right origin, not one of our frames
    pg.wait_for_timeout(400)
    assert pg.evaluate("document.getElementById('cf-panel').hidden") is False
    pg.locator("#cf-panel .cf-x").click()
