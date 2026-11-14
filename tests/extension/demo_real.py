"""Headed demo: the extension on the two demo listing pages, pointed at the real local checker.

Run:  .venv/Scripts/python.exe tests/extension/demo_real.py [--keep 900] [--server http://127.0.0.1:8787]

Opens a bundled Chromium window with the extension loaded and one tab per page:
  demo_houston.html  (1820 Marrow Bend St, Houston, TX 77002)
  demo_la.html       (2750 Corbel Ave, Los Angeles, CA 90064)
served from tests/extension/fixture on a random local port.
Set RC_DEMO_PAGES=<folder> to serve every *.html in that folder instead (e.g. pages with addresses the checker knows). It clicks nothing: click the highlighted
address, then "Inspect location". Start the checker on 127.0.0.1:8787 first. The window stays open
for --keep seconds (or until you close it).
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGES_DIR = os.environ.get("RC_DEMO_PAGES") or None
PAGES = tuple(sorted(p.name for p in Path(PAGES_DIR).glob("*.html"))) if PAGES_DIR else ("demo_houston.html", "demo_la.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=900)
    ap.add_argument("--server", default="http://127.0.0.1:8787")
    a = ap.parse_args()
    httpd, base = H.serve_fixtures(PAGES_DIR)
    with sync_playwright() as pw:
        ctx, sw, udd = H.launch(pw, headless=False, viewport=(1440, 900))
        try:
            H.set_server(sw, a.server)
            first = ctx.pages[0] if ctx.pages else ctx.new_page()
            tabs = [first] + [ctx.new_page() for _ in PAGES[1:]]
            for page, name in zip(tabs, PAGES):
                page.goto(f"{base}/{name}")
                page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=15000)
                print(f"loaded {name}: {page.locator('#rc-host').get_attribute('data-count')} address(es) highlighted", flush=True)
            tabs[0].bring_to_front()
            print(f"checker: {a.server}. Click the address, then 'Inspect location'. Window stays open {a.keep} s.", flush=True)
            end = time.time() + a.keep
            while time.time() < end and ctx.pages:
                time.sleep(1)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            httpd.shutdown()
            H.cleanup(udd)


if __name__ == "__main__":
    main()
