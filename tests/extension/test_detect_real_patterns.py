"""Address finder on markup patterns seen on real listing sites (pages are built inline; no fixture files).

Run: .venv/Scripts/python.exe -m pytest tests/extension -q
"""
import functools
import shutil
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402

playwright_sync = pytest.importorskip("playwright.sync_api")

LD = '<script type="application/ld+json">{"@type":"ApartmentComplex","address":{"@type":"PostalAddress",' \
     '"streetAddress":"%s","addressLocality":"%s","addressRegion":"%s","postalCode":"%s"}}</script>'

PAGES = {
    # house number in its own element, city line in the next block
    "split.html": "<h1>Larkspur</h1><p><span>418</span><span>Linden Grove Ave</span></p><div>Houston, TX 77006</div>",
    # building named after its address: the heading must not glue onto the street line
    "named.html": "<h1>418 Linden Grove</h1><p>418 Linden Grove Ave, Houston, Texas 77006</p>",
    # address only in JSON-LD (not visible anywhere)
    "ldonly.html": LD % ("7310 Brantley Blvd", "Los Angeles", "CA", "90036") + "<h1>Halvard</h1><p>Studio from $2,400</p>",
    # aggregator: nearby listings come first in the page; the listing's own address is in JSON-LD and the URL
    "640-n-calloway-ave.html": "<ul><li>1150 S Merrow St, Los Angeles, CA 90015</li><li>3480 Ellery Blvd, Los Angeles, CA 90010</li></ul>"
                              + LD % ("640 N Calloway Ave", "Los Angeles", "CA", "90004")
                              + "<main><h1>fernLA</h1><p>640 N Calloway Ave, Los Angeles, CA 90004</p></main>",
    # capitals, and a price that must not be read as a house number
    "caps.html": "<h1>5120 HALVORSEN AVE</h1><p>HOUSTON, TX 77007</p><p>From $1,250 Main St Specials</p>",
}


@pytest.fixture(scope="module")
def env():
    d = Path(tempfile.mkdtemp(prefix="rc_pages_"))
    for name, body in PAGES.items():
        (d / name).write_text(f"<!doctype html><meta charset=utf-8><title>t</title><body>{body}</body>", encoding="utf-8")
    (d / "report").mkdir()
    (d / "report" / "r1.html").write_text("<!doctype html><body><h1>418 Linden Grove Ave, Houston, TX 77006</h1>", encoding="utf-8")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(d)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with playwright_sync.sync_playwright() as pw:
        ctx, sw, udd = H.launch(pw, headless=True)
        try:
            yield {"ctx": ctx, "base": base}
        finally:
            ctx.close()
            httpd.shutdown()
            H.cleanup(udd)
            shutil.rmtree(d, ignore_errors=True)


def picks(env, name):
    page = env["ctx"].new_page()
    page.goto(f"{env['base']}/{name}")
    page.wait_for_selector("#rc-host", state="attached", timeout=10000)
    page.wait_for_timeout(300)
    page.locator("#rc-host #tabFace").click()
    out = page.locator("#rc-host .pick .pa").all_inner_texts()
    marks = page.locator("#rc-host .addr-mark").count()
    page.close()
    return out, marks


def test_split_number_and_city_line(env):
    p, marks = picks(env, "split.html")
    assert p == ["418 Linden Grove Ave, Houston, TX 77006"] and marks >= 1


def test_building_named_after_address_not_glued(env):
    p, _ = picks(env, "named.html")
    assert p == ["418 Linden Grove Ave, Houston, Texas 77006"], p


def test_jsonld_only_address_is_pickable(env):
    p, marks = picks(env, "ldonly.html")
    assert p == ["7310 Brantley Blvd, Los Angeles, CA 90036"] and marks == 0


def test_own_address_ranks_first_on_aggregator(env):
    p, _ = picks(env, "640-n-calloway-ave.html")
    assert p[0] == "640 N Calloway Ave, Los Angeles, CA 90004" and len(p) == 3, p


def test_capitals_and_prices(env):
    p, _ = picks(env, "caps.html")
    assert p == ["5120 HALVORSEN AVE, HOUSTON, TX 77007"], p


def test_own_report_page_is_not_scanned(env):
    page = env["ctx"].new_page()
    page.goto(f"{env['base']}/report/r1.html")
    page.wait_for_timeout(1200)
    assert page.locator("#rc-host").count() == 0  # the checker's own pages get no button and no drawer at all
    page.close()
