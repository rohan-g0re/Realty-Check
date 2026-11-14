"""Integration check: the extension against the REAL local checker on the two demo pages (a script, not a test).

Start the checker first (`python -m server`, port 8787), then:
  .venv/Scripts/python.exe tests/extension/run_real.py [--headed] [--server URL] [--tag real] [--pages houston_austin.html ...]

For each page: click the highlighted address, click "Inspect location", wait for the run to finish, and save
screenshots under $RC_PRIVATE_DIR/rc_cache/shots/<tag>_<page>_{a_midrun,b_drawer,c_marks,d_claim,e_report}.png.
Prints the headline, counts, every claim with its verdict, and the seconds taken.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_harness as H  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGES = ("houston_austin.html", "la_sepulveda.html")


def shots_dir() -> Path:
    p = os.environ.get("RC_PRIVATE_DIR")
    if not p:
        sys.exit("Set RC_PRIVATE_DIR (screenshots go to $RC_PRIVATE_DIR/rc_cache/shots).")
    d = Path(p) / "rc_cache" / "shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_page(ctx, base, server, name, out, tag, timeout_s):
    stem = f"{tag}_{Path(name).stem}"
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/{name}")
    page.wait_for_selector("#rc-host .addr-mark", state="attached", timeout=15000)
    target = page.locator("#rc-host .addr-mark").first
    target.scroll_into_view_if_needed()
    target.click()
    page.locator("#rc-host .pill").click()
    t0 = time.time()

    # (a) mid-run, once a few progress lines are up
    try:
        page.locator("#rc-host .steps li").nth(2).wait_for(timeout=timeout_s * 1000)
        page.wait_for_timeout(600)
    except Exception:
        pass
    if not page.locator("#rc-host #headline").count():
        page.screenshot(path=str(out / f"{stem}_a_midrun.png"))
    else:
        print("  (run finished before a mid-run shot could be taken)")

    page.locator("#rc-host #headline").wait_for(timeout=timeout_s * 1000)
    secs = time.time() - t0
    page.wait_for_timeout(1500)  # proof images arrive as data URLs
    page.screenshot(path=str(out / f"{stem}_b_drawer.png"))

    headline = page.locator("#rc-host #headline").inner_text()
    counts = dict(zip(["contradicted", "close", "confirmed", "cant_check"], page.locator("#rc-host .cnt b").all_inner_texts()))
    rows = page.locator("#rc-host .claim").evaluate_all(
        "els => els.map(e => [e.dataset.verdict, e.querySelector('.q').innerText.replace(/\\n/g,' '), e.querySelector('.sub').innerText])")

    # (c) page marks scrolled into view (first contradicted mark if any)
    cm = page.locator('#rc-host .claim-mark[data-verdict="contradicted"]')
    if not cm.count():
        cm = page.locator("#rc-host .claim-mark")
    n_marks = page.locator("#rc-host .claim-mark").count()
    if cm.count():
        cm.first.scroll_into_view_if_needed()
        page.evaluate("window.scrollBy(0, -200)")
        page.wait_for_timeout(500)
        try:
            cm.first.hover(timeout=3000)
        except Exception:
            print("  note: another mark covers the first contradicted mark; hovering with force")
            cm.first.hover(force=True)
        page.wait_for_timeout(400)
    page.screenshot(path=str(out / f"{stem}_c_marks.png"))

    # (d) one expanded claim with its proof image (contradicted first)
    proof = False
    order = page.locator('#rc-host .claim[data-verdict="contradicted"], #rc-host .claim[data-verdict="close"], #rc-host .claim[data-verdict="confirmed"]')
    lis = [order.nth(i) for i in range(order.count())]
    said = headline.split("Listing says ", 1)[-1].split(",")[0].strip().lower()
    lis.sort(key=lambda li: 0 if li.locator(".q").inner_text().lower().startswith(said) else 1)  # the headline claim first
    for li in lis:
        li.locator(".row1").click()
        try:
            li.locator(".proof").wait_for(timeout=4000)
            proof = True
        except Exception:
            li.locator(".row1").click()  # collapse again, try the next one
            continue
        li.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        break
    page.screenshot(path=str(out / f"{stem}_d_claim.png"))

    # (e) the full report tab
    with ctx.expect_page() as newp:
        page.locator("#rc-host #btnFull").click()
    rep = newp.value
    rep.wait_for_load_state("load")
    rep.wait_for_timeout(1500)
    rep.screenshot(path=str(out / f"{stem}_e_report.png"), full_page=True)
    rep_imgs = rep.evaluate("[...document.images].map(i => i.complete && i.naturalWidth > 0)")
    report_url = rep.url

    # the saved events give claimed/found/source for every claim
    detail = {}
    try:
        run_id = report_url.rstrip("/").split("/")[-1]
        with urllib.request.urlopen(f"{server}/runs/{run_id}.json", timeout=10) as r:
            evs = json.load(r)
        evs = evs.get("events", evs) if isinstance(evs, dict) else evs
        claims = {}
        for ev in evs:
            if ev.get("type") == "claims":
                claims = {c["id"]: c for c in ev["claims"]}
            elif ev.get("type") == "verdict":
                v = ev["verdict"]
                c = claims.get(v["claim_id"], {})
                detail[v["claim_id"]] = (c.get("kind"), c.get("label"), c.get("quote"), v["verdict"], v.get("claimed"),
                                         v.get("found"), v.get("source"), bool(v.get("image")))
    except Exception as e:
        print(f"  (could not read saved events: {type(e).__name__})")

    print(f"\n== {name}: {secs:.1f} s")
    print(f"address:  {page.locator('#rc-host .addr').inner_text()}")
    print(f"headline: {headline}")
    print(f"counts:   {counts}")
    print(f"page marks: {n_marks}; proof image shown in drawer: {proof}; report images loaded: {sum(rep_imgs)}/{len(rep_imgs)}")
    print("drawer rows (in order):")
    for v, q, sub in rows:
        print(f"  [{v}] {q} -> {sub}")
    if detail:
        print("verdicts (kind | label | quote | verdict | claimed | found | source | image):")
        for cid, d in detail.items():
            print(f"  {cid}: " + " | ".join(str(x) for x in d))
    if errors:
        print(f"page errors: {errors}")
    rep.close()
    page.close()
    return secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--server", default="http://127.0.0.1:8787")
    ap.add_argument("--tag", default="real")
    ap.add_argument("--timeout", type=int, default=150, help="seconds to wait for a run")
    ap.add_argument("--pages", nargs="*", default=list(PAGES))
    a = ap.parse_args()
    out = shots_dir()
    with urllib.request.urlopen(a.server + "/health", timeout=5) as r:
        print("health:", r.read().decode())
    httpd, base = H.serve_fixtures()
    with sync_playwright() as pw:
        ctx, sw, udd = H.launch(pw, headless=not a.headed, viewport=(1440, 900))
        try:
            H.set_server(sw, a.server)
            for name in a.pages:
                run_page(ctx, base, a.server, name, out, a.tag, a.timeout)
            print(f"\nscreenshots: {out}\\{a.tag}_*.png")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            httpd.shutdown()
            H.cleanup(udd)


if __name__ == "__main__":
    main()
