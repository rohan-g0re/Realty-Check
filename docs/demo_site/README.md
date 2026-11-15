# Demo listing pages

Two sets of pages to run the extension against. Both are plain static files.

## `real/` — copies of two real property websites (use these for the demo)

| Page | Building | Values changed on purpose (what the check should catch) |
|---|---|---|
| `real/houston.html` | Houston apartment community | 184 apartments (records say about 216), built 2018 (records say 2019), Plan B 745 sq ft (record says 791) |
| `real/la.html` | Los Angeles apartment community | Plan A "Studio" and 480 sq ft, Plan D 980 sq ft, Plan E 2 beds, Plan F 3 beds |

Everything else on these pages is what the property's own website says.

Serve and open:

```
cd docs/demo_site/real
python -m http.server 8899 --bind 127.0.0.1
```

- http://127.0.0.1:8899/houston.html
- http://127.0.0.1:8899/la.html

Then start the checker (`python -m server`, port 8787), load `extension/` unpacked in Chrome, and run the check from
the page. The checker needs the private sponsor data folder (`RC_PRIVATE_DIR`) to return real results.

Files: `houston.html` + `houston.css`, `la.html` + `la.css`, shared `clone.js`, photos in `img/<city>/listing/`.
Each photo folder has a `sources.txt` listing the page every photo came from.

Rules for these pages:

- Photos come only from the properties' own websites. Never add images from CoStar, Matterport or Vexcel here: a
  listing page is the thing being checked, not a source.
- The site logos are plain text wordmarks, not the real artwork.
- These pages name real buildings and use their photos, so **this repo must stay private**. `tests/test_no_sponsor_data.py`
  skips `docs/demo_site/real/` for that reason, and nothing else.

## `houston.html`, `la.html` (this folder) — invented listings

Made-up buildings on a made-up listing site ("Northstar"), with `site.css` and `site.js`. Safe to show anywhere.
Serve the same way from `docs/demo_site/`.
