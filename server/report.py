"""Full report page: one self-contained, minimalist HTML document built from a saved run."""
from __future__ import annotations

from html import escape

SOURCE_WORDS = {"scan": "3D scan", "aerial": "Aerial measurement", "county": "County record", "record": "Building record"}
VERDICT_WORDS = {"contradicted": "Contradicted", "close": "Close", "confirmed": "Confirmed", "cant_check": "Can't check"}
ORDER = ["contradicted", "close", "confirmed", "cant_check"]

CSS = """
:root{--ink:#16181a;--muted:#6b6f73;--line:#e6e6e3;--bg:#fff;--accent:#2f5d8a;
--red:#c0392b;--amber:#c98a12;--green:#2e8b57;--grey:#9a9ea3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:720px;margin:0 auto;padding:56px 24px 96px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.eyebrow{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
.page{font-size:14px;color:var(--muted);margin:0;overflow-wrap:anywhere}
.headline{font-size:26px;line-height:1.35;font-weight:500;margin:40px 0 32px}
.counts{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);
border-bottom:1px solid var(--line);margin:0 0 48px}
.count{padding:16px 0}.count+.count{padding-left:16px;border-left:1px solid var(--line)}
.count b{display:block;font-size:24px;font-weight:600}
.count span{font-size:13px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.contradicted .dot,.dot.contradicted{background:var(--red)}.close .dot,.dot.close{background:var(--amber)}
.confirmed .dot,.dot.confirmed{background:var(--green)}.cant_check .dot,.dot.cant_check{background:var(--grey)}
.card{border:1px solid var(--line);border-radius:6px;padding:24px;margin:0 0 16px}
.label{font-size:13px;font-weight:600;margin:0 0 12px}
.contradicted .label{color:var(--red)}.close .label{color:var(--amber)}
.confirmed .label{color:var(--green)}.cant_check .label{color:var(--muted)}
blockquote{margin:0 0 20px;font-size:19px;font-weight:500}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:0 0 16px}
.cmp dt{font-size:13px;color:var(--muted)}.cmp dd{margin:2px 0 0}
.reason{margin:0;color:var(--ink)}.src{font-size:13px;color:var(--muted);margin:8px 0 0}
figure{margin:20px 0 0}figure img{display:block;max-width:100%;max-height:440px;object-fit:contain;border:1px solid var(--line);border-radius:4px}
figcaption{font-size:13px;color:var(--muted);margin-top:6px}
details{margin-top:48px;border-top:1px solid var(--line);padding-top:16px}
summary{cursor:pointer;font-size:15px;font-weight:500}
ol{padding-left:20px;color:var(--muted);font-size:14px}ol li{margin:6px 0}
.empty{color:var(--muted)}
@media(max-width:560px){.counts{grid-template-columns:repeat(2,1fr)}.count:nth-child(3){border-left:0;padding-left:0}
.headline{font-size:22px}.cmp{grid-template-columns:1fr}}
"""


def _first(events: list[dict], kind: str) -> dict | None:
    return next((e for e in events if e.get("type") == kind), None)


def render_report(run: dict) -> str:
    events = run.get("events") or []
    snap = run.get("snapshot") or {}
    done = _first(events, "done") or {}
    err = _first(events, "error")
    claims = {c.get("id"): c for e in events if e.get("type") == "claims" for c in e.get("claims") or []}
    verdicts = [e["verdict"] for e in events if e.get("type") == "verdict" and e.get("verdict")]
    captions = {}
    for e in events:
        if e.get("type") == "evidence":
            for im in (e.get("evidence") or {}).get("images") or []:
                captions[im.get("path")] = im.get("caption") or ""

    counts = {k: 0 for k in ORDER}
    for v in verdicts:
        if v.get("verdict") in counts:
            counts[v["verdict"]] += 1
    counts.update({k: v for k, v in (done.get("counts") or {}).items() if k in counts})

    address = snap.get("address_text") or "Unknown address"
    url = snap.get("url") or ""
    title = snap.get("title") or url
    headline = done.get("headline") or (err and err.get("message")) or "This check did not finish."

    h = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         f"<title>Reality Check: {escape(address)}</title><style>{CSS}</style></head><body><main>",
         "<p class=eyebrow>Reality Check</p>", f"<h1>{escape(address)}</h1>"]
    if url:
        h.append(f"<p class=page><a href='{escape(url, True)}' target=_blank rel=noopener>{escape(title)}</a></p>")
    h.append(f"<p class=headline>{escape(headline)}</p>")
    h.append("<div class=counts>")
    for k in ORDER:
        h.append(f"<div class='count {k}'><b>{counts[k]}</b><span><i class=dot></i>{VERDICT_WORDS[k]}</span></div>")
    h.append("</div>")

    ranked = sorted(verdicts, key=lambda v: ORDER.index(v.get("verdict")) if v.get("verdict") in ORDER else 9)
    if not ranked:
        h.append("<p class=empty>No claims were judged.</p>")
    for v in ranked:
        kind = v.get("verdict") if v.get("verdict") in ORDER else "cant_check"
        c = claims.get(v.get("claim_id")) or {}
        quote = c.get("quote") or v.get("claimed") or ""
        src = SOURCE_WORDS.get(v.get("source"), "")
        h.append(f"<section class='card {kind}'><p class=label><i class=dot></i>{VERDICT_WORDS[kind]}</p>")
        h.append(f"<blockquote>&ldquo;{escape(quote)}&rdquo;</blockquote>")
        h.append("<dl class=cmp>"
                 f"<div><dt>Listing says</dt><dd>{escape(str(v.get('claimed') or ''))}</dd></div>"
                 f"<div><dt>We found</dt><dd>{escape(str(v.get('found') or ''))}</dd></div></dl>")
        h.append(f"<p class=reason>{escape(v.get('reason') or '')}</p>")
        if src and kind != "cant_check":
            h.append(f"<p class=src>Source: {src}</p>")
        img = v.get("image")
        if img and str(img).startswith("/img/"):
            cap = captions.get(img) or src
            h.append(f"<figure><img src='{escape(img, True)}' alt='{escape(cap, True)}' loading=lazy>"
                     f"<figcaption>{escape(cap)}</figcaption></figure>")
        h.append("</section>")

    steps = [e.get("text") for e in events if e.get("type") == "step" and e.get("text")]
    if steps:
        h.append("<details><summary>How we checked</summary><ol>")
        h.extend(f"<li>{escape(s)}</li>" for s in steps)
        h.append("</ol></details>")
    h.append("</main></body></html>")
    return "".join(h)
