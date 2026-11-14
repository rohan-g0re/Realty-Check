"""Headed walk-through of the Realty Check extension on the local fixture page.

Run:  .venv/Scripts/python.exe tests/extension/show_ui.py [--keep 480] [--shots DIR] [--pause 1.5]
Saves a screenshot of every state, then leaves the window open for --keep seconds so you can try it.
The walk-through uses the built-in sample result (the checker address is pointed at an empty port);
afterwards the address goes back to http://127.0.0.1:8787, so if the real checker is running you can use it.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=480)
    ap.add_argument("--pause", type=float, default=1.5)
    default_shots = Path(os.environ.get("RC_PRIVATE_DIR", "")) / "rc_cache" / "shots" if os.environ.get("RC_PRIVATE_DIR") else Path("shots")
    ap.add_argument("--shots", default=str(default_shots))
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    httpd, base = H.serve_fixtures()
    n = [0]

    with sync_playwright() as pw:
        ctx, sw, udd = H.launch(pw, headless=a.headless, slow_mo=150, viewport=(1440, 900))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            def pause(k=1.0):
                page.wait_for_timeout(int(a.pause * 1000 * k))

            def shot(name, p=None):
                n[0] += 1
                path = shots / f"ext_{n[0]:02d}_{name}.png"
                (p or page).screenshot(path=str(path))
                print("saved", path, flush=True)

            R = lambda sel: page.locator("#rc-host " + sel)  # noqa: E731
            H.set_server(sw, f"http://127.0.0.1:{H.free_port()}")
            page.goto(f"{base}/listing.html")
            page.wait_for_selector("#rc-host .addr-mark", state="attached")
            pause(); shot("page_addresses_and_tab")

            R("#tab").hover(); pause(); shot("tab_hover_handle_close")
            R("#tabFace").click(); pause(); shot("drawer_open_page_pushed")
            R("#btnGear").click(); pause(); shot("gear_server_address")
            R("#btnGear").click(); R("#btnCollapse").click(); pause(); shot("drawer_closed")

            R(".addr-mark").first.click(); pause(); shot("address_pill")
            R(".pill").click(); R(".notice").wait_for(); pause(); shot("checker_not_running")
            R("#useSample").click()
            R("#cols .col").first.wait_for(); page.wait_for_timeout(900); shot("run_claim_field")
            R('.cell[data-s="contra"]').nth(1).wait_for(state="attached"); shot("run_verdicts_arriving")
            R("#headline").wait_for(timeout=20000); pause(2); shot("result_concise_verdict")
            R("#fiToggle").click(); pause(); shot("not_on_the_listing_expanded")
            R(".find").first.click(); pause(); shot("finding_flashes_page_words")

            m = R('.claim-mark[data-verdict="contradicted"]').first
            m.scroll_into_view_if_needed(); m.hover(); pause(); shot("page_mark_tooltip")
            page.mouse.move(5, 400)
            page.mouse.wheel(0, 500); pause(); shot("page_marks_scrolled")
            page.mouse.wheel(0, -2000); pause(0.5)

            with ctx.expect_page() as newp:
                R("#btnFull").click()
            rep = newp.value
            rep.wait_for_selector("#headline"); rep.wait_for_timeout(int(a.pause * 1000))
            shot("full_report_tab", rep)
            rep.screenshot(path=str(shots / "ext_full_report_fullpage.png"), full_page=True)
            page.bring_to_front(); pause(0.5)

            dark = ctx.new_page()
            dark.goto(f"{base}/listing_dark.html")
            dark.wait_for_selector("#rc-host .addr-mark", state="attached"); dark.wait_for_timeout(600)
            shot("dark_fixture_highlight", dark)
            dark.locator("#rc-host #tabFace").click(); dark.wait_for_timeout(int(a.pause * 1000))
            shot("dark_fixture_drawer_isolated", dark)
            dark.close()

            page.bring_to_front()
            R("#btnAnother").click(); pause(); shot("check_another_address")
            H.set_server(sw, "http://127.0.0.1:8787")
            if a.keep > 0:
                print(f"window stays open for {a.keep} s — try it yourself", flush=True)
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
