"""The drawer's headline is the report's: the real server (on its own free port, invented runs through a stub runner)
sends done.top, the drawer shows it, and /report/<run_id> shows the same headline and lead line.

Cases: one hard mismatch + one close, only close ("Nothing is clearly off."), all confirmed. Headless bundled Chromium
with the unpacked extension, local fixture page only. Skips without Playwright.
RC_HEADLINE_SHOTS=<folder> also saves a screenshot of each drawer as headline_<case>.png.
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_drawer_top.py -q
"""
import html as htmllib
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawer_events as D  # noqa: E402
import rc_harness as H  # noqa: E402

CASES = {
    "hard_and_close": dict(bad=("c2", "c3"), n=8, cant=1, facts=2),  # c2 floors off, c3 year built close
    "only_close": dict(bad=("c3",), n=8, cant=1, facts=2),
    "all_confirmed": dict(bad=(), n=6, cant=0, facts=2),
}
WANT = {"hard_and_close": "1 thing doesn't match.", "only_close": "Nothing is clearly off.",
        "all_confirmed": "Everything we could check matches."}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    ps = pytest.importorskip("playwright.sync_api")
    import uvicorn

    import server.app as srv
    from server import tour

    mp = pytest.MonkeyPatch()
    mp.setenv("RC_PRIVATE_DIR", str(tmp_path_factory.mktemp("private")))  # runs saved here, never the real folder
    mp.delenv("RC_REPLAY", raising=False)
    mp.setattr(tour, "PREWARM", False)
    mp.setattr(srv, "apply_pick", lambda snap: snap)  # no kit lookup: the fixture address goes straight through
    case = {"kw": None}

    def runner(snapshot, emit):
        for ev in D.run_events(snapshot.get("address_text") or "", run_id=snapshot["run_id"], **case["kw"]):
            if ev.get("type") == "run_started" or "_pause" in ev:
                continue
            emit(ev)
    mp.setattr(srv, "pick_runner", lambda: ("fake", runner))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    httpd, base = H.serve_fixtures()
    try:
        with ps.sync_playwright() as pw:
            try:
                ctx, sw, udd = H.launch(pw, headless=True)
            except Exception as e:  # browser not installed
                pytest.skip(f"Chromium unavailable: {e}")
            try:
                H.set_server(sw, f"http://127.0.0.1:{port}")
                yield {"ctx": ctx, "base": base, "server": f"http://127.0.0.1:{port}", "case": case}
            finally:
                ctx.close()
                H.cleanup(udd)
    finally:
        httpd.shutdown()
        server.should_exit = True
        mp.undo()


def _hero(page: str) -> tuple[str, str]:
    h1 = re.search(r"<section class=hero.*?<h1>(.*?)</h1>", page, re.S).group(1)
    h1 = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", h1)).split())
    m = re.search(r"<p class=top-line>(.*?)</p>", page, re.S)
    return h1, htmllib.unescape(m.group(1)) if m else ""


@pytest.mark.parametrize("name", list(CASES))
def test_drawer_headline_is_the_reports(env, name):
    import httpx

    env["case"]["kw"] = CASES[name]
    page = env["ctx"].new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(f"{env['base']}/listing.html")
        page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=10000)
        page.locator("#rc-host .addr-mark").first.click()
        page.locator("#rc-host .pill").click()
        R = lambda sel: page.locator("#rc-host " + sel)  # noqa: E731
        R("#headline").wait_for(timeout=20000)
        drawer = " ".join(R("#headline").inner_text().split())
        line = R("#topLine").inner_text() if R("#topLine").count() else ""
        shots = os.environ.get("RC_HEADLINE_SHOTS")
        if shots:
            page.wait_for_timeout(1200)  # entrance animations settle
            R(".res").screenshot(path=str(Path(shots) / f"headline_{name}.png"))
    finally:
        page.close()
    runs = sorted((Path(os.environ["RC_PRIVATE_DIR"]) / "rc_cache" / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime)
    run_id = runs[-1].stem
    report = httpx.get(f"{env['server']}/report/{run_id}", timeout=30).text
    h1, top_line = _hero(report)
    print(f"{name}: drawer={drawer!r} line={line!r} | report={h1!r} line={top_line!r}")
    assert drawer == h1 == WANT[name]
    assert line == top_line
    assert bool(line) is (name == "hard_and_close")
    assert not errors, errors
