"""Rebuild the architecture diagram: archify draws system-architecture.json, theme.css gives it the Realty Check look.

    python docs/architecture/render.py /path/to/archify/bin/archify.mjs

Writes system-architecture.html and, when Playwright is installed, system-architecture.png next to this file."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "system-architecture.json"
PAGE = HERE / "system-architecture.html"
IMAGE = HERE / "system-architecture.png"
FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap">')


def build(archify):
    subprocess.run(["node", archify, "render", "architecture", str(SOURCE), str(PAGE), "--quality", "showcase"], check=True)
    html = PAGE.read_text(encoding="utf-8")
    html = html.replace('data-theme="dark"', 'data-theme="light"', 1)
    theme = (HERE / "theme.css").read_text(encoding="utf-8")
    html = html.replace("</head>", f"{FONT}<style id=\"realty-check-theme\">{theme}</style></head>", 1)
    PAGE.write_text(html, encoding="utf-8")


def snapshot():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed: skipped the PNG")
        return
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2, color_scheme="light")
        page.goto(PAGE.as_uri())
        page.wait_for_timeout(2500)          # fonts and the draw-in animation
        page.screenshot(path=str(IMAGE), full_page=True)
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build(sys.argv[1])
    snapshot()
    print(f"wrote {PAGE.name} and {IMAGE.name}")
