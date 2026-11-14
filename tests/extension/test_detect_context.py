"""Address context scoring in detect.js: which address on a page is the listing's own.

detect.js only reads the DOM, so it is loaded straight into plain invented pages (no extension needed) and
`RC.addressCandidates(RC.scanAddresses(RC.textNodes()))` is read back. All addresses and names are invented.

Run: .venv/Scripts/python.exe -m pytest tests/extension/test_detect_context.py -q
"""
import functools
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
DETECT = Path(__file__).resolve().parents[2] / "extension" / "content" / "detect.js"

LD = '<script type="application/ld+json">{"@type":"%s","name":"%s","address":{"@type":"PostalAddress",' \
     '"streetAddress":"%s","addressLocality":"Faketown","addressRegion":"TX","postalCode":"77999"}}</script>'

PAGES = {
    # building site: hero shows the building, footer shows the management company's office
    "corp-footer.html": (
        "Larkspur Commons",
        "<header class='site-hero'><h1>Larkspur Commons</h1><p>418 Linden Grove Ave, Faketown, TX 77999</p></header>"
        "<main><p>Studios to 2 bedrooms with a rooftop deck.</p></main>"
        "<footer><p>Corporate Office</p><p>900 Placeholder Pkwy, Suite 300, Faketown, TX 77998</p></footer>"),
    # single-property site: the only address is in the footer, and it is the building
    "the-alder.html": (
        "The Alder | Apartments",
        "<h1>The Alder</h1><main><p>Bright homes, quiet street.</p></main>"
        "<footer class='site-footer'><p>The Alder</p><p>77 Alder Example St, Faketown, TX 77999</p></footer>"),
    # single-property site: building address and management office both in the footer
    "maple-court.html": (
        "Maple Court Apartments",
        "<h1>Maple Court</h1><main><p>Two and three bedrooms.</p></main>"
        "<footer><p>Visit us at 25 Maple Example Ct, Faketown, TX 77999</p>"
        "<p>Professionally managed by Example Living, 6000 Office Example Blvd, Faketown, TX 77990</p></footer>"),
    # aggregator: the nearby listings come first in the page
    "listing-1234.html": (
        "Cedar House - Faketown, TX",
        "<section class='results'><h2>Nearby apartments</h2><ul>"
        "<li>1500 Nearby Example Dr, Faketown, TX 77999</li><li>1600 Other Example Way, Faketown, TX 77999</li></ul></section>"
        "<main><div class='property-header'><h1>Cedar House</h1><p>310 Cedar Example Ln, Faketown, TX 77999</p></div>"
        "<p>1 to 3 bedrooms.</p></main>"
        "<div><h3>Similar listings</h3><div class='cards'><p>1700 Third Example Rd, Faketown, TX 77999</p></div></div>"),
    # JSON-LD: the organization's address is visible first; the ApartmentComplex address is the building
    "ld-types.html": (
        "Birch Flats",
        LD % ("Organization", "Example Holdings", "500 Holding Example Ave")
        + LD % ("ApartmentComplex", "Birch Flats", "12 Birch Example St")
        + "<p>500 Holding Example Ave, Faketown, TX 77999</p><main><h2>Birch Flats</h2><p>12 Birch Example St, Faketown, TX 77999</p></main>"),
    # map link: the "Get directions" link carries the building address
    "map-link.html": (
        "Willow Place",
        "<h1>Willow Place</h1><p><a href='https://www.google.com/maps/search/?api=1&query=40+Willow+Example+Rd+Faketown+TX+77999'>Get directions</a></p>"
        "<aside><p>Leasing office: 41 Willow Example Rd, Faketown, TX 77999</p></aside>"),
}


@pytest.fixture(scope="module")
def page_of():
    d = Path(tempfile.mkdtemp(prefix="rc_ctx_"))
    for name, (title, body) in PAGES.items():
        (d / name).write_text(f"<!doctype html><meta charset=utf-8><title>{title}</title><body>{body}</body>", encoding="utf-8")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(d)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        def run(name):
            page = browser.new_page()
            page.goto(f"{base}/{name}")
            page.add_script_tag(path=str(DETECT))
            out = page.evaluate("RC.addressCandidates(RC.scanAddresses(RC.textNodes()), 10)")
            page.close()
            return out
        try:
            yield run
        finally:
            browser.close()
            httpd.shutdown()
            shutil.rmtree(d, ignore_errors=True)


def texts(cands):
    return [c["text"] for c in cands]


def test_candidate_shape(page_of):
    c = page_of("corp-footer.html")
    assert c and all(set(x) == {"text", "score", "where", "why"} for x in c), c
    assert all(isinstance(x["where"], list) and isinstance(x["why"], str) and x["why"] for x in c)
    assert [x["score"] for x in c] == sorted((x["score"] for x in c), reverse=True)


def test_hero_building_beats_corporate_footer(page_of):
    c = page_of("corp-footer.html")
    assert c[0]["text"] == "418 Linden Grove Ave, Faketown, TX 77999", c
    corp = next(x for x in c if x["text"].startswith("900 Placeholder Pkwy"))
    assert "footer" in corp["where"] and "label:corporate" in corp["where"], corp
    assert "hero" in c[0]["where"]


def test_single_property_footer_address_is_kept(page_of):
    c = page_of("the-alder.html")
    assert texts(c) == ["77 Alder Example St, Faketown, TX 77999"], c
    assert "footer" in c[0]["where"] and "footer" in c[0]["why"]


def test_footer_building_beats_footer_management_office(page_of):
    c = page_of("maple-court.html")
    assert c[0]["text"] == "25 Maple Example Ct, Faketown, TX 77999", c
    assert "label:located" in c[0]["where"]
    mgmt = next(x for x in c if x["text"].startswith("6000 Office"))
    assert "label:corporate" in mgmt["where"]


def test_aggregator_nearby_listings_rank_below_the_listing(page_of):
    c = page_of("listing-1234.html")
    assert c[0]["text"] == "310 Cedar Example Ln, Faketown, TX 77999", c
    for x in c[1:]:
        assert "nearby" in x["where"], x


def test_jsonld_apartment_complex_beats_organization(page_of):
    c = page_of("ld-types.html")
    assert c[0]["text"] == "12 Birch Example St, Faketown, TX 77999", c
    assert "jsonld:ApartmentComplex" in c[0]["where"]
    org = next(x for x in c if x["text"].startswith("500 Holding"))
    assert "jsonld:Organization" in org["where"] and org["score"] < c[0]["score"]


def test_map_link_address_beats_leasing_office(page_of):
    c = page_of("map-link.html")
    assert c[0]["text"] == "40 Willow Example Rd, Faketown, TX 77999", c
    assert "map" in c[0]["where"]
