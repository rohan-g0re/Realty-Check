"""Full report page, design "Option A": one self-contained HTML document built from a saved run.

Five sections, top to bottom, and nothing else (each one adapts to the run; nothing is invented). Every mismatch
appears exactly once on the page, in section 2.
  1 hero        the count as a huge two-line serif headline ("3 things / don't match.", the second line indented;
                every claim whose verdict is contradicted or close counts, whatever its confidence; calm
                "Everything we could check matches." when nothing is off), then a narrow column at the right:
                the address on two lines, one plain sentence naming the lead mismatch ("Biggest difference: the
                listing says “2 Beds”; the 3D scan shows 1 bedroom and a den.", from the run's own pick
                done.harness.top_mismatch, else the report's ranking), the claim count and sources, the small run
                meta, and the button down. No strike-through here. Fits the first screen at 1440x900 and 390 wide.
  2 mismatch    "Listed vs. in reality" (#mismatch): one master-detail view that fits a screen. Left: a list (tabs),
                one row per mismatch (contradicted or close), the run's pick first and selected, then the most certain
                first: claim type, the listing's words in short, "Doesn't match · 40 low" (the confidence; the word
                only when there is no score). One blue accent, fainter as the confidence drops (_shade). Right: the
                selected row's panel: the listing's words (struck through in blue only when it doesn't match), what
                the sources show, one short reason, the credited sources (never one that says something else) with
                "Confidence 82 / 100 · high" and its why, and the proof. Columnar (Listed | In reality, one record
                line) for a figure; vertical (claim, found value, then the proof filling the panel: live 3D viewer,
                pictures, or the measured roof-height drawing) for a visual proof. One mismatch: the panel alone.
  3 extra       "Not in the listing" (#extra): at most five things the listing does not say that matter to a renter
                or buyer: label, value, one line on why it matters, source, a picture when there is one. Data:
                done.harness.top_facts; else harness.top_facts.pick_top_facts(facts, llm=None); else a small local
                rule (elevator / walk-up, homes available and vacancy, utility meters, 311 count, measured height).
                Never parcel numbers, building class, lot size, ratings, owners, managers or contacts; 311 as a
                count only; nothing that repeats a mismatch.
  4 questions   "Ask before you tour." (#questions): neutral questions from the mismatches and couldn't-checks
                (deterministic template), each with a Copy button, and Copy all.
  5 sources     the claim field heatmap (server.claim_field, or a simple grid when it is missing) and "Four
                sources. One address." (#sources): per source, how many claims it spoke to and what it gave.
After them: one collapsed "Everything we checked" disclosure (#checked: #match confirmed claims, #cant-verify claims
nobody could check; opened when a link points inside it), and the footer.

The data logic and wording come from server.report (image ranking, walk links, fill-in, captions).
Pictures only via /img/<token>; outside requests: the Newsreader font, and Matterport inside the 3D viewers.
Pictures whose cached file is gone (older runs) are taken out before rendering (server.report.drop_dead_images), and
any picture that still fails to load is hidden in the browser (no broken-image icon).

3D viewers (live_3d=True, the server's page): when the selected panel's proof is a 3D scan it shows the live Matterport
viewer inline (made only for the selected panel; picking another row takes it down) (the same-origin /tour/<run_id>?claim=<id>&embed=1 page in an iframe), opened at the picked spot, with
no click and no new tab; it mounts at once, never more than two live at a time, and a still picture covers it until
the tour page reports "ready" (postMessage). Other scan rows, confirmed rows and the claim field panel have "See it
in 3D", which opens a viewer in place. live_3d=False (the extension's static sample, no server) keeps the still
picture with a plain note and never makes a frame.
"""
from __future__ import annotations

import math
import re
from html import escape

from server.tour import PRECONNECT
from server.report import (FACT_FROM, IMG_RE, TOPICS, Run, _cant_reason, _claimed_words, _first_sentence,
                           drop_dead_images,
                           _fill_in, _fmt_n, _groups, _kind, _meta, _norm, _num, _rooms,
                           _said, _same_plan, _scan_title, _short_caption, _source_line, _value_words)

SRC_ORDER = ["record", "scan", "aerial", "county"]
SRC_NAME = {"record": "CoStar", "scan": "Matterport", "aerial": "Vexcel", "county": "County"}
SRC_LONG = {"record": "CoStar building record", "scan": "Matterport 3D scan", "aerial": "Vexcel aerial imagery",
            "county": "County records"}
VERDICT_WORD = {"contradicted": "Doesn't match", "close": "Doesn't match", "confirmed": "Matches", "cant_check": "Couldn't check"}
BRAND = "Realty Check"
SOFT_KINDS = {"amenity", "parking", "laundry", "view", "other"}  # photo checks: never struck as false
KIND_LABEL = {"year_built": "Year built", "stories": "Floors", "unit_count": "Homes", "area_sqft": "Size",
              "bedrooms": "Bedrooms", "bathrooms": "Bathrooms"}
OBS_WORDS = [("stories", "floors"), ("year_built", "year built"), ("unit_count", "number of homes"),
             ("building_sf", "building size"), ("floorplans", "floor plans"), ("kit_plan", "floor plans"),
             ("bedrooms", "bedrooms"), ("bathrooms", "bathrooms"), ("area_sqft", "floor area"),
             ("room_labels", "rooms"), ("rooms", "rooms"), ("height_m", "roof height"),
             ("parcel_id", "the parcel"), ("feature_check", "photo checks")]
SRC_FROM = {"scan": "the 3D scan shows", "county": "county records say", "record": "the CoStar building record says"}
SRC_SAYS = {"scan": "the 3D scan says", "county": "county records say", "record": "the CoStar record says",
            "aerial": "the roof height says"}
SRC_SHORT = {"record": "CoStar", "scan": "3D scan", "aerial": "Vexcel", "county": "County"}
# renter impact: what a wrong claim costs the renter, most first
TIER_UNIT = {"bedrooms", "bathrooms", "area_sqft"}
TIER_BUILDING = {"stories", "year_built", "unit_count"}
MONEY_RE = re.compile(r"\$\s?\d|\brent\b|\bfees?\b|\bdeposit\b|\bprice\b|\bper month\b|/mo\b", re.I)
LIST_SHOW = 12     # matches / couldn't-check entries shown before the "show the other N" button
MAX_QUESTIONS = 5
UNKIND = re.compile(r"(?i)\b(fake|faked|lie|lies|lied|lying|scam\w*|fraud\w*)\b")


CSS = """
:root{--ink:#111;--blue:#1a4be0;--mute:#6b6b6b;--ghost:#858585;--soft:#f4f4f4;--line:#d9d9d9;--bg:#fff;
--gut:clamp(16px,6vw,88px)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:19px/1.5 Newsreader,Georgia,'Times New Roman',serif;
-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:var(--ink)}a:hover{color:var(--blue)}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
img{max-width:100%;display:block}
.wrap{max-width:1440px;margin:0 auto;padding:0 var(--gut)}
.mute{color:var(--mute)}.blue{color:var(--blue)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;min-height:48px;padding:0 22px;
background:var(--ink);color:#fff;border:0;font:inherit;font-size:16px;text-decoration:none;cursor:pointer;
transition:background-color .18s ease,transform .18s ease;white-space:nowrap}
.btn:hover{background:#333;color:#fff}.btn:active{transform:translateY(1px)}
.btn.big{min-height:54px;font-size:17px;padding:0 26px}
.btn.ghost{background:var(--soft);color:var(--ink);min-height:40px;font-size:15px;padding:0 16px}
.btn.ghost:hover{background:#e6e6e6;color:var(--ink)}
.dark .btn{background:#fff;color:var(--ink)}.dark .btn:hover{background:#ddd}
.gone{display:none!important}
.vh{position:absolute!important;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
/* header */
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;min-height:96px;gap:8px 16px}
.brand{font-size:19px;font-weight:500;letter-spacing:-.01em;white-space:nowrap;text-decoration:none}
.top nav{display:flex;align-items:center;gap:4px 20px}
.back{display:inline-flex;align-items:center;gap:6px;min-height:44px;font-size:15px;text-decoration:none}
/* 1 hero: the count, huge, on two staggered lines; a narrow column at the right beneath it */
.hero{display:flex;flex-direction:column;padding:clamp(4px,1.6vw,28px) 0 clamp(56px,7vw,112px)}
.hero-top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:clamp(24px,3.4vh,44px) 6%}
h1{margin:0;flex:1 1 100%;font-weight:300;font-size:clamp(58px,min(11vw,18.5vh),172px);line-height:.92;
letter-spacing:-.05em;display:flex;flex-direction:column}
h1 .l2{padding-left:clamp(0px,24vw - 60px,300px)}
h1 .tick{display:inline-block;width:.42em;height:.42em;margin-left:.12em;vertical-align:.05em}
.addr{flex:0 1 410px;margin-left:auto;display:flex;flex-direction:column;gap:clamp(10px,1.7vh,18px)}
.addr .where{margin:0;font-size:24px;line-height:1.3;letter-spacing:-.01em}
.addr p{margin:0}
.addr .top-line{font-size:19px;line-height:1.4}
.addr .meta{font-size:15px;color:var(--mute)}
.addr .btn{align-self:flex-start}
/* section heads */
.sh{margin:0 0 clamp(24px,3vw,44px);font-weight:300;font-size:clamp(40px,5.4vw,84px);line-height:.95;letter-spacing:-.045em}
.sh-sub{margin:calc(-1 * clamp(10px,1.6vw,26px)) 0 clamp(24px,3vw,40px);font-size:17px;color:var(--mute);max-width:56ch}
.empty{font-size:20px;color:var(--mute);margin:0}
/* 2 listed vs. in reality: one master-detail view that fits a screen (list left, the selected panel right) */
.lvr{padding-bottom:clamp(48px,5vw,88px);scroll-margin-top:8px}
.lvr-top{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 24px;
margin:0 0 clamp(12px,1.4vw,20px);padding-bottom:12px;border-bottom:1px solid var(--ink)}
.lvr-top .sh{margin:0;font-size:clamp(34px,3.6vw,56px);line-height:1.08}
.lvr-n{margin:0;font-size:16px;color:var(--mute)}
.md{display:grid;grid-template-columns:minmax(250px,1fr) minmax(0,2fr);column-gap:clamp(20px,3vw,52px);
height:clamp(440px,calc(100vh - 150px),780px)}
.md.one{grid-template-columns:minmax(0,1fr)}
.md.flat{height:clamp(340px,52vh,480px)}  /* figures only: no picture to fill a tall panel */
.md-p[hidden]{display:none}html:not(.js) .md{height:auto}html:not(.js) .md-p[hidden]{display:flex}
.md-list{position:relative;overflow-y:auto;overscroll-behavior:contain;display:flex;flex-direction:column;
border-right:1px solid var(--line);scrollbar-width:thin}
.md-i{position:relative;flex:none;display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:1px;
align-items:baseline;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid #ececec;
padding:13px 16px 13px 18px;font:inherit;color:var(--ink);cursor:pointer;transition:background-color .18s ease}
.md-i:hover{background:var(--soft)}
.md-i:focus-visible{outline:2px solid var(--blue);outline-offset:-2px}
.md-i[aria-selected=true]{background:var(--acc-bg,#eef2fd)}
.md-i::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--acc,var(--blue));transform:scaleY(0);
transition:transform .22s cubic-bezier(.2,.7,.2,1)}
.md-i[aria-selected=true]::before{transform:none}
.md-t{font-size:19px;line-height:1.25;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.md-s{grid-column:1/-1;font-size:14px;line-height:1.35;color:var(--mute);min-width:0;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.md-g{grid-row:1/3;grid-column:2;align-self:center;display:flex;flex-direction:column;align-items:flex-end;
color:var(--acc-t,var(--blue));white-space:nowrap;font-variant-numeric:tabular-nums;line-height:1}
/* the score: a serif numeral, the row's right-hand element; "/100" and the level small under it */
.sc-n{font-weight:300;font-size:36px;letter-spacing:-.03em;line-height:.95}
.sc-w{font-weight:300;font-size:24px;letter-spacing:-.01em;line-height:1.1}
.sc-u{font-size:12px;letter-spacing:.03em;margin-top:4px;opacity:.9}
/* one blue accent, fainter as the confidence drops: high full, medium mid, low (or close with no score) faint */
.s-high{--acc:#1a4be0;--acc-t:#1a4be0;--acc-bg:#eef2fd}
.s-medium{--acc:#6585ea;--acc-t:#4a6fe6;--acc-bg:#f1f4fd}
.s-low{--acc:#b3c2f4;--acc-t:#8199ec;--acc-bg:#f6f8fe}
.md-ps{min-width:0;min-height:0}
.md-p{height:100%;display:flex;flex-direction:column;gap:12px;min-height:0;overflow:hidden;outline:0;scroll-margin-top:8px}
.md-p.in{animation:mdin .34s cubic-bezier(.2,.7,.2,1)}
@keyframes mdin{from{opacity:0;transform:translateY(8px)}}
.lvr-k{margin:0;display:flex;align-items:baseline;gap:12px;font-size:15px;color:var(--mute);flex:none}
.lvr-tag{margin-left:auto;display:inline-flex;align-items:baseline;gap:6px;color:var(--acc-t,var(--blue));
font-variant-numeric:tabular-nums;white-space:nowrap}
.lvr-tag .sc-n{font-size:clamp(36px,3.4vw,52px)}.lvr-tag .sc-w{font-size:28px}
.lvr-tag .sc-u{font-size:15px;margin:0}
.lvr-lab{display:block;font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin-bottom:4px}
.lvr-r .lvr-lab,.vstack>div+div .lvr-lab{color:var(--acc-t,var(--blue))}
.lvr-said,.lvr-truth{display:block;font-weight:300;letter-spacing:-.04em;overflow-wrap:anywhere}
.lvr-said{color:var(--ghost)}
.lvr-truth{color:var(--acc-t,var(--blue))}
.pcols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:clamp(20px,3vw,52px);flex:none;
border-top:1px solid var(--line);padding:clamp(14px,2vh,26px) 0 clamp(6px,1vh,14px)}
.pcols .lvr-said,.pcols .lvr-truth{font-size:clamp(40px,4.6vw,80px);line-height:1}
.pcols.long .lvr-said,.pcols.long .lvr-truth{font-size:clamp(28px,2.8vw,46px);line-height:1.06}
.vstack{flex:none;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:clamp(20px,3vw,52px);
border-top:1px solid var(--line);padding-top:12px}
.vstack .lvr-said,.vstack .lvr-truth{font-size:clamp(26px,2.5vw,40px);line-height:1.05}
.lvr-why{margin:0;font-size:18px;line-height:1.4;max-width:62ch;flex:none}
.lvr-cred{flex:none;display:flex;flex-direction:column;gap:6px}
.lvr-cred p{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px}
.lvr-cite{gap:6px 8px}
.cite-l{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
.cite-l+.cite{margin-left:2px}.cite+.cite-l{margin-left:10px}
.cite{display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:3px 10px;border:1px solid var(--line);
font-size:15px;line-height:1.2;color:var(--ink);text-decoration:none;background:#fff;transition:border-color .15s,color .15s}
.cite.dec{border-color:var(--ink);font-size:16px}
.cite i{font-style:normal;font-size:13px;color:var(--blue)}
a.cite:hover,a.cite:focus-visible{border-color:var(--blue);color:var(--blue)}
.art-lib{margin:16px 0 0 auto;max-width:360px;font-size:17px}.art-lib a{color:#fff}
.lvr-cred .lvr-cwhy{font-size:15px;line-height:1.4;color:var(--mute);max-width:72ch;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden}
.lvr-proof{margin:0;min-width:0}
.lvr-proof .rline{margin:0;font-size:15px;line-height:1.4;color:var(--ink);border-left:2px solid var(--acc,var(--blue));padding:2px 0 2px 12px}
.lvr-proof .rline span{color:var(--mute)}
figure.lvr-proof{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;align-items:stretch;gap:8px;width:100%}
figure.lvr-proof>.walk,figure.lvr-proof>.draw,figure.lvr-proof>.cards{flex:1 1 auto;min-height:0}
figure.lvr-proof .walk{aspect-ratio:auto;max-height:none;min-height:200px;width:100%}
figure.lvr-proof .pics{flex:1 1 auto;min-height:0;grid-template-rows:minmax(0,1fr) auto}
figure.lvr-proof .pics .lead{min-height:0}
figure.lvr-proof .pics .lead img{height:100%;aspect-ratio:auto}
figure.lvr-proof .thumbs{grid-template-columns:repeat(auto-fill,96px)}
figure.lvr-proof figcaption{flex:none}
figure.lvr-proof .draw{padding:clamp(12px,1.6vw,24px)}
figure.lvr-proof .draw svg{height:100%;max-height:100%}
.strike{background:linear-gradient(var(--acc,var(--blue)),var(--acc,var(--blue))) no-repeat 0 52%/100% .075em;
-webkit-box-decoration-break:clone;box-decoration-break:clone;padding:0 .03em}
.js .rv .strike{background-size:0 .075em;transition:background-size 1s cubic-bezier(.7,0,.2,1) .35s}
.js .rv.in .strike{background-size:100% .075em}
.js .rv.in .md-p.in .strike{animation:strk .9s cubic-bezier(.7,0,.2,1) .2s backwards}
@keyframes strk{from{background-size:0 .075em}}
.sq{display:inline-flex;gap:4px;flex:none}.sq i{width:10px;height:10px;background:var(--line);display:block}
.sq i.on{background:var(--ink)}.sq i.hit{background:var(--acc,var(--blue))}
.js .rv .sq i{transform:scale(0);transition:transform .4s cubic-bezier(.2,.7,.2,1)}
.js .rv.in .sq i{transform:none}
.js .rv.in .sq i:nth-child(2){transition-delay:.08s}.js .rv.in .sq i:nth-child(3){transition-delay:.16s}
.js .rv.in .sq i:nth-child(4){transition-delay:.24s}
@media (max-width:760px){
.md{display:block;height:auto}
.md-list{flex-direction:row;overflow-x:auto;overflow-y:hidden;border-right:0;gap:8px;padding:2px 2px 10px;
scroll-snap-type:x proximity}
.md-i{width:auto;min-width:190px;max-width:76vw;border:1px solid var(--line);padding:10px 14px 10px 16px;scroll-snap-align:start}
.sc-n{font-size:30px}
.lvr-k{flex-wrap:wrap;row-gap:4px}.lvr-tag .sc-n{font-size:34px}.lvr-tag .sc-u{font-size:14px}
.md-ps{margin-top:14px}
.md-p{height:auto;overflow:visible}
.pcols,.vstack{grid-template-columns:minmax(0,1fr);row-gap:12px}
.pcols .lvr-said,.pcols .lvr-truth{font-size:40px}
figure.lvr-proof .walk{aspect-ratio:4/3;min-height:260px}
figure.lvr-proof .pics .lead img{height:auto;aspect-ratio:16/10}
figure.lvr-proof .draw svg{height:auto;max-height:340px}
}
/* proof: 3D walk */
.stage{position:relative;background:#ededeb;overflow:hidden;aspect-ratio:16/8;min-height:300px}
.stage>img,.stage>svg.room{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.stage>img{animation:drift 16s ease-in-out infinite alternate;transform-origin:50% 60%}
.stage .tag{position:absolute;left:clamp(12px,2vw,24px);top:clamp(12px,2vw,24px);background:#fff;padding:10px 16px;
font-size:16px;line-height:1.35;max-width:calc(100% - 24px)}
.stage .tag small{display:block;color:var(--mute);font-size:14px}
.stage .pin{position:absolute;left:50%;top:55%;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
background:var(--blue);box-shadow:0 0 0 3px #fff}
.stage .pin::after{content:'';position:absolute;inset:0;border-radius:50%;background:var(--blue);animation:pulse 2.4s ease-out infinite}
.walk{position:relative;background:#17181a;overflow:hidden;aspect-ratio:16/9;min-height:320px;max-height:82vh}
.cf-panel .walk,.see3d-box .walk{aspect-ratio:4/3;min-height:0}
.walk>iframe{position:absolute;inset:0;width:100%;height:100%;border:0;z-index:1;background:#17181a}
.walk>iframe.dock{position:fixed;inset:auto;left:0;top:0;opacity:0;pointer-events:none;z-index:-1}
.walk>.stage{position:absolute;inset:0;z-index:2;aspect-ratio:auto;min-height:0;transition:opacity .2s ease,visibility 0s linear 0s}
.walk.live>.stage{opacity:0;visibility:hidden;pointer-events:none;transition:opacity .2s ease,visibility 0s linear .2s}
.walk .spin{position:absolute;right:clamp(12px,2vw,24px);bottom:clamp(12px,2vw,24px);width:26px;height:26px;border-radius:50%;
border:3px solid rgba(255,255,255,.55);border-top-color:var(--blue);animation:spin 1s linear infinite;display:none}
.walk.loading .spin{display:block}
.walk .fs{position:absolute;right:12px;top:12px;z-index:3;display:none;background:#fff;color:var(--ink);border:0;font:inherit;
font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding:9px 12px;cursor:pointer}
.walk .fs:hover{color:var(--blue)}
.walk.live .fs{display:block}
.walk:fullscreen{max-height:none;aspect-ratio:auto}
button.see3d{background:none;border:0;padding:0;min-height:32px;font:inherit;font-size:16px;color:var(--blue);
text-decoration:underline;cursor:pointer}
.see3d-box{margin:12px 0 4px;width:100%}
/* proof: pictures, drawings, record cards */
.pics{display:grid;gap:10px}
.pics .lead img{width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--soft)}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px}
.thumbs img{width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
a.pic{display:block;overflow:hidden;transition:transform .25s ease,box-shadow .25s ease}
a.pic:hover{transform:translateY(-4px);box-shadow:0 14px 30px -18px rgba(0,0,0,.45)}
a.pic img{transition:transform .6s ease}
a.pic.plan img{object-fit:contain;background:#fff;padding:6px}a.pic:hover img{transform:scale(1.03)}
.proof figcaption,.cap{font-size:15px;color:var(--mute)}
.draw{background:var(--soft);padding:clamp(20px,3vw,40px);display:flex;justify-content:center;align-items:center}
.draw svg{width:100%;max-width:520px;height:auto;max-height:440px;overflow:visible}
.card{background:var(--soft);padding:clamp(22px,2.6vw,38px);display:flex;flex-direction:column;gap:10px;
transition:transform .25s ease,box-shadow .25s ease}
.card:hover{transform:translateY(-4px);box-shadow:0 14px 30px -20px rgba(0,0,0,.4)}
.card .eb{font-size:15px;color:var(--mute)}
.card .row{display:flex;justify-content:space-between;align-items:baseline;gap:16px;font-size:clamp(26px,2.6vw,38px);line-height:1.15}
.card .row b{font-weight:400;color:var(--blue);text-align:right}
.card .also{font-size:16px;color:var(--mute)}
.cards{display:flex;flex-direction:column;gap:10px}
.nopic{background:var(--soft);min-height:160px;display:flex;align-items:center;justify-content:center;padding:24px;
text-align:center;font-weight:300;font-size:clamp(34px,4vw,60px);line-height:1;letter-spacing:-.04em;color:var(--blue)}
/* 3 not in the listing + 4 questions: side by side on a wide screen, compact rows */
.duo{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:clamp(32px,4vw,80px);align-items:start;
padding-bottom:clamp(48px,5vw,88px)}
@media (max-width:1040px){.duo{grid-template-columns:minmax(0,1fr)}}
.duo .sh{font-size:clamp(34px,3.6vw,56px);margin:0 0 6px}
.duo .sh-sub{margin:0 0 14px;font-size:16px}
.nil,.ask{scroll-margin-top:24px;min-width:0}.duo>.wrap{padding:0;max-width:none;margin:0}
.nil-rows{list-style:none;margin:0;padding:0;border-top:1px solid var(--ink)}
.nil-row{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:16px;padding:12px 0;border-bottom:1px solid #ececec;
align-items:start}
.nil-m{display:flex;flex-direction:column;gap:2px;min-width:0}
.nil-k{margin:0;display:flex;align-items:baseline;gap:10px;font-size:15px;line-height:1.35;color:var(--mute)}
.nil-k b{font-weight:400;color:var(--blue);font-variant-numeric:tabular-nums}
.nil-v{margin:0;font-weight:300;font-size:clamp(22px,1.9vw,28px);line-height:1.15;letter-spacing:-.02em;overflow-wrap:anywhere}
.nil-m p.nil-w{margin:0;font-size:16px;line-height:1.4;color:var(--ink)}
.nil-src{display:flex;align-items:center;gap:10px;font-size:14px;color:var(--mute)}
.nil-row a.pic{width:112px}.nil-row a.pic img{aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
/* 4 questions */
.qlist{list-style:none;margin:0;padding:0;border-top:1px solid var(--ink)}
.qlist li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:8px 16px;
padding:11px 0;border-bottom:1px solid #ececec;font-size:18px;line-height:1.38}
.qlist .qn{color:var(--blue);font-size:14px;font-variant-numeric:tabular-nums;align-self:baseline}
.qlist .btn.ghost{min-height:34px;font-size:14px;padding:0 12px}
.ask-all{margin:16px 0 0}
@media (max-width:560px){.qlist li{grid-template-columns:auto minmax(0,1fr);font-size:17px}
.qlist li .btn{grid-column:2;justify-self:start}.nil-row a.pic{width:88px}}
/* 5 heatmap + sources (the dark band) */
.field-sec{padding-bottom:clamp(48px,5vw,88px);scroll-margin-top:24px}
.fld{display:flex;gap:clamp(10px,1.6vw,24px);overflow-x:auto;padding-bottom:6px}
.fld-names{flex:none;display:grid;grid-auto-rows:clamp(30px,4vw,56px);gap:6px;font-size:17px;width:clamp(100px,13%,180px)}
.fld-names span{display:flex;align-items:center;justify-content:space-between;gap:8px}
.fld-names b{font-weight:400;color:var(--mute);font-variant-numeric:tabular-nums}
.fld-cols{flex:1;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(6px,1fr);gap:4px;min-width:min(100%,var(--fw))}
.fld-col{display:grid;grid-auto-rows:clamp(30px,4vw,56px);gap:6px}
.fld-col i{display:block;background:#ececec;transform-origin:bottom}
.fld-col i.m{background:var(--ink)}.fld-col i.x{background:var(--blue)}.fld-col i.w{background:#bdbdbd}
.legend{display:flex;flex-wrap:wrap;gap:6px 24px;font-size:15px;color:var(--mute);margin-top:16px}
.legend i{display:inline-block;width:11px;height:11px;margin-right:8px;background:#ececec;vertical-align:-1px}
.legend i.m{background:var(--ink)}.legend i.x{background:var(--blue)}.legend i.w{background:#bdbdbd}
.dark{background:#111;color:#f2f2f2}.dark a{color:#fff}.dark .mute{color:#a9a9a9}
.srcs{padding-top:clamp(64px,7vw,110px);padding-bottom:clamp(56px,6vw,96px)}
.h2{margin:0;font-weight:300;font-size:clamp(48px,8vw,124px);line-height:.92;letter-spacing:-.05em;display:flex;flex-direction:column}
.h2 .l2{padding-left:clamp(0px,15vw,220px)}
.lede{margin:clamp(24px,3vw,40px) 0 0 auto;max-width:360px;font-size:17px}
.src-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:clamp(16px,2.4vw,48px);margin-top:clamp(32px,4vw,64px)}
.src{display:flex;flex-direction:column;gap:10px;min-width:0}
.src .tile{background:#1d1d1d;aspect-ratio:4/3;display:flex;flex-direction:column;justify-content:space-between;padding:18px;
font-size:15px;line-height:1.45;color:#cfcfcf;overflow:hidden;transition:transform .3s ease}
.src:hover .tile{transform:translateY(-4px)}
.src .tile svg{width:100%;height:100%;max-height:150px}
.src .tile b{font-weight:400;color:#fff;font-size:17px}
.src .n{font-weight:300;font-size:clamp(64px,7vw,110px);line-height:.95;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.src h3{margin:0;font-weight:400;font-size:24px}
.src p{margin:0;font-size:16px;color:#bdbdbd}
.src.off .tile,.src.off .n{opacity:.38}
/* everything we checked: one collapsed disclosure */
.checked{padding-top:clamp(40px,5vw,80px);padding-bottom:clamp(40px,5vw,80px);scroll-margin-top:24px}
.allck>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:14px;min-height:48px;font-size:22px}
.allck>summary::-webkit-details-marker{display:none}
.allck>summary::before{content:'+';display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;
border:1px solid var(--ink);font-size:20px;line-height:1;flex:none}
.allck[open]>summary::before{content:'\\2212'}
.allck>summary span{color:var(--mute);font-size:16px}
.ck-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:clamp(32px,4vw,72px);margin-top:28px}
.ck h3{margin:0 0 14px;font-weight:400;font-size:24px}
.mlist{list-style:none;margin:0;padding:0;font-size:19px;line-height:1.35}
.mlist li{padding:0 0 12px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.mlist small{display:block;font-size:14px;color:var(--mute)}
.reasons{display:flex;flex-direction:column;gap:18px}
.reasons p{margin:0 0 4px;font-size:15px;color:var(--mute);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.reasons .qs{font-size:19px;line-height:1.4}
.toggle{margin-top:18px}
.js .more-wrap:not(.open) .more{display:none}
/* footer */
.foot{padding-top:clamp(56px,6vw,96px);padding-bottom:40px}
.mark{margin:0;font-weight:300;font-size:clamp(52px,15.5vw,224px);line-height:.95;letter-spacing:-.05em;white-space:nowrap}
.foot-row{display:flex;flex-wrap:wrap;justify-content:space-between;gap:32px;margin-top:clamp(40px,5vw,80px)}
.foot-links{display:flex;flex-direction:column;gap:6px;font-size:clamp(26px,2.6vw,36px)}
.foot-note{max-width:420px;font-size:15px;color:#cfcfcf}
.how{margin-top:40px;font-size:15px;color:#bdbdbd}.how summary{cursor:pointer}
.how ol{margin:12px 0 0;padding-left:20px}.how .ag{color:#fff;margin-right:8px}
.foot-end{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;margin-top:56px;font-size:15px;color:#a9a9a9}
/* lightbox */
.lb{position:fixed;inset:0;z-index:90;background:rgba(17,17,17,.92);display:flex;flex-direction:column;align-items:center;
justify-content:center;gap:12px;padding:56px 16px 24px}
.lb[hidden]{display:none}
.lb img{max-width:100%;max-height:82vh;object-fit:contain}
.lb p{color:#eee;margin:0;font-size:16px}
.lb button{position:absolute;top:12px;right:12px}
/* motion */
.js .rv{opacity:0;transform:translateY(28px);transition:opacity .8s ease var(--d,0ms),transform .8s cubic-bezier(.2,.7,.2,1) var(--d,0ms)}
.js .rv.in{opacity:1;transform:none}
.js .fld-col i{transform:scaleY(.2);opacity:0;transition:transform .5s cubic-bezier(.2,.7,.2,1),opacity .5s ease}
.js .rv.in .fld-col i{transform:none;opacity:1}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%{transform:scale(1);opacity:.55}100%{transform:scale(3.2);opacity:0}}
@keyframes drift{from{transform:scale(1)}to{transform:scale(1.07) translateX(-1%)}}
@keyframes tick{from{stroke-dashoffset:40}to{stroke-dashoffset:0}}
h1 .tick path{stroke-dasharray:40;animation:tick .9s ease .5s backwards}
@media (max-width:900px){.src-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:560px){
body{font-size:18px}
.top{min-height:72px}
.addr .where{font-size:21px}.addr .top-line{font-size:18px}
.src-grid{grid-template-columns:minmax(0,1fr)}
.src .tile{aspect-ratio:auto;min-height:150px}
.stage{aspect-ratio:auto;min-height:300px}
.walk{min-height:280px}
}
@media (prefers-reduced-motion: reduce){
html{scroll-behavior:auto}
.js .rv,.js .rv.in{opacity:1;transform:none;transition:none}
.js .rv .strike{background-size:100% .075em;transition:none}
.js .rv.in .md-p.in .strike,.md-p.in{animation:none}
.md-i::before{transition:none}
.js .rv .sq i{transform:none;transition:none}
.js .fld-col i{transform:none;opacity:1;transition:none}
.stage>img,.stage .pin::after,h1 .tick path{animation:none}
.stage .pin::after{opacity:0}
a.pic,a.pic img,.card,.src .tile,.btn{transition:none}
a.pic:hover,.card:hover,.src:hover .tile{transform:none}
}
"""

SCRIPT = r"""
(function(){var d=document.documentElement;d.className+=' js';
var still=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
var rv=[].slice.call(document.querySelectorAll('.rv'));
function count(el){var n=+el.getAttribute('data-n');if(still||!n){el.textContent=n;return;}var t0=null;
function f(t){if(!t0)t0=t;var p=Math.min(1,(t-t0)/900);el.textContent=Math.round(n*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(f);}
requestAnimationFrame(f);}
function show(el){el.classList.add('in');[].forEach.call(el.querySelectorAll('[data-n]'),count);}
if(still||!('IntersectionObserver' in window)){rv.forEach(show);}
else{var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){show(e.target);io.unobserve(e.target);}});},
{rootMargin:'0px 0px -8% 0px',threshold:0.05});rv.forEach(function(el){io.observe(el);});}
function copy(text,btn){var done=function(){var o=btn.getAttribute('data-label')||btn.textContent;btn.setAttribute('data-label',o);
btn.textContent='Copied';setTimeout(function(){btn.textContent=o;},1600);};
function old(){var t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');t.style.position='fixed';
t.style.opacity='0';document.body.appendChild(t);t.select();try{document.execCommand('copy');done();}catch(e){}document.body.removeChild(t);}
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done,old);}else{old();}}
/* a picture whose file is gone never shows a broken-image icon: hide its slot; an emptied proof shows its text */
document.addEventListener('error',function(e){var t=e.target;if(!t||t.tagName!=='IMG'||!t.closest)return;
if(t.closest('.lb'))return;if(t.closest('.stage')){t.classList.add('gone');return;}
var a=t.closest('a.pic');(a||t).classList.add('gone');
var pics=t.closest('.pics');if(pics&&!pics.querySelector('a.pic:not(.gone)'))pics.classList.add('gone');
var fig=t.closest('figure.proof');if(fig&&!fig.querySelector('a.pic:not(.gone),.walk,.draw,.cards,.nopic')){
[].forEach.call(fig.querySelectorAll('figcaption'),function(c){c.classList.add('gone');});
var alt=fig.getAttribute('data-alt');if(alt){var d=document.createElement('div');d.className='nopic';d.textContent=alt;fig.appendChild(d);}}},true);
/* the lightbox picture is made when a picture opens and removed on close: never an empty image tag */
var lb=document.getElementById('lb'),li=null,lc=lb.querySelector('p');
function shut(){lb.hidden=true;if(li){li.remove();li=null;}}
lb.addEventListener('click',shut);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!lb.hidden)shut();});
document.addEventListener('click',function(e){var t=e.target.closest?e.target:null;if(!t)return;
var a=t.closest('a.pic');if(a){e.preventDefault();if(!li){li=document.createElement('img');lb.insertBefore(li,lc);}li.src=a.getAttribute('href');var c=a.getAttribute('data-cap')||'';
li.alt=c||'Proof picture';lc.textContent=c;lb.hidden=false;lb.querySelector('button').focus();return;}
var c2=t.closest('button.copy');if(c2){copy(c2.getAttribute('data-text')||'',c2);return;}
var sh=t.closest('button.share');if(sh){copy(location.href,sh);return;}
var m=t.closest('button.morebtn');if(m){var w=document.getElementById(m.getAttribute('aria-controls'));if(!w)return;
var open=w.classList.toggle('open');m.setAttribute('aria-expanded',open?'true':'false');
var l=m.getAttribute('data-less'),o=m.getAttribute('data-more');m.textContent=open?l:o;return;}
var fs=t.closest('.walk .fs');if(fs){var wf=fs.closest('.walk');if(wf.requestFullscreen)wf.requestFullscreen();return;}
var s3=t.closest('button.see3d');if(s3&&window.RC3D){var bx=window.RC3D.see(s3.getAttribute('data-claim'),s3.getAttribute('data-src'),
s3.closest('.lvr-r')||s3.closest('.more-wrap')||s3.parentNode);if(bx)bx.scrollIntoView({behavior:still?'auto':'smooth',block:'nearest'});}});

/* a link into the collapsed "Everything we checked" opens it */
function openTo(){var id=location.hash.slice(1);if(!id)return;var t=document.getElementById(id);if(!t)return;
var dd=t.closest('details')||t.querySelector&&t.querySelector('details.allck');if(dd&&!dd.open){dd.open=true;t.scrollIntoView();}}
openTo();window.addEventListener('hashchange',openTo);
})();
(function(){
/* 2 'Listed vs. in reality': a list of tabs, one panel shown. Arrow keys move the selection; #claim-<id> and
   #mismatch select / show it. A 3D viewer is made only for the selected panel, after a short pause (fast arrowing
   never starts several), and the one in the panel left is taken down. */
var md=document.querySelector('.md');if(!md)return;
var list=md.querySelector('[role=tablist]'),tabs=[].slice.call(md.querySelectorAll('[role=tab]')),tm=null;
var still=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
function pan(t){return document.getElementById(t.getAttribute('aria-controls'));}
function tabOf(p){return document.getElementById(p.getAttribute('aria-labelledby')||'');}
function keep(t){if(!list)return;var L=list;
if(L.scrollHeight>L.clientHeight){if(t.offsetTop<L.scrollTop)L.scrollTop=t.offsetTop;
else if(t.offsetTop+t.offsetHeight>L.scrollTop+L.clientHeight)L.scrollTop=t.offsetTop+t.offsetHeight-L.clientHeight;}
if(L.scrollWidth>L.clientWidth){if(t.offsetLeft<L.scrollLeft)L.scrollLeft=t.offsetLeft;
else if(t.offsetLeft+t.offsetWidth>L.scrollLeft+L.clientWidth)L.scrollLeft=t.offsetLeft+t.offsetWidth-L.clientWidth;}}
function select(t,focus){if(!t)return;var shown=null;
tabs.forEach(function(x){var on=x===t,p=pan(x);x.setAttribute('aria-selected',on?'true':'false');x.tabIndex=on?0:-1;if(!p)return;
if(on){shown=p;if(p.hidden){p.hidden=false;p.classList.remove('in');void p.offsetWidth;p.classList.add('in');}}
else if(!p.hidden){p.hidden=true;p.classList.remove('in');
[].forEach.call(p.querySelectorAll('.walk'),function(w){if(window.RC3D)window.RC3D.unmount(w);});}});
if(focus)t.focus({preventScroll:true});keep(t);clearTimeout(tm);
var w=shown&&shown.querySelector('.walk[data-src]');
if(w)tm=setTimeout(function(){if(!shown.hidden&&window.RC3D)window.RC3D.mount(w);},260);}
if(list){list.addEventListener('click',function(e){var t=e.target.closest('[role=tab]');if(t)select(t,true);});
list.addEventListener('keydown',function(e){var i=tabs.indexOf(document.activeElement);if(i<0)return;var j=null;
if(e.key==='ArrowDown'||e.key==='ArrowRight')j=Math.min(tabs.length-1,i+1);
else if(e.key==='ArrowUp'||e.key==='ArrowLeft')j=Math.max(0,i-1);
else if(e.key==='Home')j=0;else if(e.key==='End')j=tabs.length-1;
if(j===null)return;e.preventDefault();select(tabs[j],true);});}
function show(p,scroll){var t=p&&tabOf(p);if(t&&p.hidden)select(t,false);
if(scroll){var s=document.getElementById('mismatch');if(s)s.scrollIntoView({behavior:'auto',block:'start'});}}
window.RCMD={show:show,select:select};
function go(){var id=location.hash.slice(1);if(!id)return;var el=document.getElementById(id);if(!el)return;
var p=el.closest&&el.closest('.md-p');if(p){show(p,true);return;}
if(id==='mismatch'||id==='compare')show(null,true);}
go();window.addEventListener('hashchange',go);
})();
(function(){
/* live 3D viewers: mount without a click, at most MAX live, still picture on top until the tour page says ready */
var MAX=2,live=[],T0=performance.now(),R=window.RC3D={live:live,times:{},max:MAX};
function all(){return [].slice.call(document.querySelectorAll('.walk[data-src]'));}
function gap(w){var r=w.getBoundingClientRect(),h=innerHeight||800;return r.bottom<0?-r.bottom:r.top>h?r.top-h:0;}
function note(w,s){var n=w.querySelector('.wnote');if(n&&s)n.textContent=s;}
function stamp(w,k){var c=w.getAttribute('data-claim')||'?';(R.times[c]=R.times[c]||{})[k]=Math.round(performance.now()-T0);}
function unmount(w){if(!w._f)return;try{w._f.src='about:blank';}catch(e){}w._f.remove();w._f=null;clearTimeout(w._to);
var i=live.indexOf(w);if(i>=0)live.splice(i,1);w.classList.remove('live','loading');}
function mount(w){if(w._f||w.classList.contains('off'))return;
while(live.length>=MAX){var far=live.slice().sort(function(a,b){return gap(b)-gap(a);})[0];unmount(far);}
var f=document.createElement('iframe');f.title='3D walkthrough';f.setAttribute('allow','fullscreen; xr-spatial-tracking');
f.setAttribute('allowfullscreen','');
/* Chrome pauses a cross-origin viewer that is off screen, so one mounted ahead of time loads "docked": fixed inside the
   screen at its slot's size, invisible, until its slot scrolls into view (moving it by CSS never reloads it) */
if(gap(w)>0){var r=w.getBoundingClientRect();f.className='dock';f.tabIndex=-1;f.style.width=r.width+'px';f.style.height=r.height+'px';}
f.src=w.getAttribute('data-src');w.insertBefore(f,w.firstChild);w._f=f;live.push(w);
w.classList.add('loading');stamp(w,'mount');if(inview)inview.observe(w);
w._to=setTimeout(function(){if(w._f&&!w.classList.contains('live')){w.classList.add('live');w.classList.remove('loading');}},30000);}
function undock(w){var f=w._f;if(!f||f.className!=='dock')return;f.className='';f.removeAttribute('tabindex');
f.style.width='';f.style.height='';stamp(w,'undock');}
R.mount=mount;R.unmount=unmount;
var twin=location.origin.replace('//127.0.0.1','//localhost');  /* the viewer runs on localhost (SDK key rule) */
/* Esc while a viewer has focus: keys go to the cross-origin viewer, never to this page. Our tour page forwards Esc
   ({rc3d:1,key:'Escape'}), and focus comes back here when the pointer leaves a viewer */
function ours(src){return [].some.call(document.querySelectorAll('iframe'),function(f){return f.contentWindow===src;});}
function regain(){var a=document.activeElement;if(!a||a.tagName!=='IFRAME')return;
var p=a.closest('[role=dialog]'),x=p&&!p.hidden&&p.querySelector('.cf-x');if(x)x.focus({preventScroll:true});else a.blur();}
document.addEventListener('mouseover',function(e){var a=document.activeElement;if(a&&a.tagName==='IFRAME'&&e.target!==a)regain();});
window.addEventListener('message',function(e){if((e.origin!==location.origin&&e.origin!==twin)||!e.data||e.data.rc3d!==1)return;
if(e.data.key==='Escape'){if(ours(e.source)){regain();document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));}return;}
var w=all().filter(function(x){return x._f&&x._f.contentWindow===e.source;})[0];if(!w)return;var d=e.data;stamp(w,d.state);
if(d.state==='ready'){w.classList.add('live');w.classList.remove('loading');clearTimeout(w._to);
if(d.room==='start')note(w,d.note||'Showing the scan from its start position.');}
else if(d.state==='finding'){note(w,'Finding the spot that shows this…');clearTimeout(w._to);
w._to=setTimeout(function(){if(w._f&&!w.classList.contains('live')){w.classList.add('live');w.classList.remove('loading');}},60000);}
else if(d.state==='error'){note(w,d.note);w.classList.add('live','nospot');w.classList.remove('loading');clearTimeout(w._to);}
else if(d.state==='off'){unmount(w);w.classList.add('off');note(w,d.note||'The live 3D view is not available here.');}});
R.see=function(cid,src,host){var a=cid&&document.getElementById('claim-'+cid),f=a&&a.closest('.lvr-row'),w=f&&f.querySelector('.walk[data-src]');
if(w&&f.hidden&&window.RCMD)window.RCMD.show(f,true);
if(w){mount(w);w.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});return w;}
if(!src||!host)return null;var box=host.querySelector('.see3d-box');
if(!box){box=document.createElement('div');box.className='see3d-box';box.innerHTML="<div class=walk><div class=stage>"+
"<div class=tag>Live 3D · Matterport<small class=wnote></small></div><span class=spin aria-hidden=true></span></div>"+
"<button type=button class=fs>Full screen</button></div>";var ul=host.querySelector('ul');
if(ul&&ul.parentNode===host)host.insertBefore(box,ul.nextSibling);else host.appendChild(box);watch(box.querySelector('.walk'));}
var w2=box.querySelector('.walk');if(w2.getAttribute('data-claim')!==(cid||'')){unmount(w2);w2.classList.remove('off','nospot');}
w2.setAttribute('data-src',src);w2.setAttribute('data-claim',cid||'');note(w2,'Opening at the spot that shows this…');mount(w2);return w2;};
var near=null,far=null,inview=null;
function watch(w){if(near){near.observe(w);far.observe(w);}}
if('IntersectionObserver' in window){
near=new IntersectionObserver(function(es){es.forEach(function(x){if(x.isIntersecting)mount(x.target);});},{rootMargin:'700px 0px'});
far=new IntersectionObserver(function(es){es.forEach(function(x){if(!x.isIntersecting)unmount(x.target);});},{rootMargin:'2400px 0px'});
inview=new IntersectionObserver(function(es){es.forEach(function(x){if(x.isIntersecting)undock(x.target);});});}
/* a fade-in parent (opacity 0, transform) would keep Chrome from running the viewer and pin the dock to it: show those
   parents at once */
all().forEach(function(w){var p=w.closest('.rv');if(p)p.classList.add('in');});
/* a viewer in a list panel that is not selected is never made: only the selected panel's */
var ws=all().filter(function(w){return !w.closest('.md-p[hidden]');}),first=null,h=location.hash.match(/^#claim-([A-Za-z0-9_-]{1,32})$/);
if(h){var a=document.getElementById('claim-'+h[1]),fd=a&&a.closest('.lvr-row');first=fd&&fd.querySelector('.walk[data-src]');}
first=first||ws[0];if(first)mount(first);
ws.forEach(function(w){if(near)watch(w);else if(w!==first&&live.length<MAX)mount(w);});
})();
"""

ARROW_DOWN = ("<svg aria-hidden=true width=16 height=16 viewBox='0 0 20 20' fill=none stroke=currentColor stroke-width=1.8 "
              "stroke-linecap=round stroke-linejoin=round><path d='M10 4v12M5 11l5 5 5-5'/></svg>")
ARROW_LEFT = ("<svg aria-hidden=true width=16 height=16 viewBox='0 0 18 18' fill=none stroke=currentColor stroke-width=1.8 "
              "stroke-linecap=round stroke-linejoin=round><path d='M11 4L6 9l5 5'/></svg>")
TICK = ("<svg class=tick aria-hidden=true viewBox='0 0 40 40' fill=none stroke=currentColor stroke-width=2.4 "
        "stroke-linecap=round stroke-linejoin=round><path d='M6 21l9 9L35 9'/></svg>")


# ---------- small helpers ----------

def _e(s) -> str:
    return escape(str(s if s is not None else ""), True)


def _cap(s: str) -> str:
    s = str(s or "").strip()
    return s[:1].upper() + s[1:]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _size(text: str, lead: bool) -> str:
    n = len(text)
    if lead:
        return "sz-xl" if n <= 12 else "sz-l" if n <= 22 else "sz-m" if n <= 40 else "sz-s"
    return "sz-l" if n <= 12 else "sz-m" if n <= 24 else "sz-s"


def _truth_text(v: dict) -> str:
    found = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", str(v.get("found") or "").strip())
    found = _cap(found)
    if found and found[-1] not in ".!?":
        found += "."
    return found


def _strike_text(run: Run, v: dict, c: dict) -> tuple[str, bool]:
    """(the listing's own words, whether they are a page quote)."""
    cc = dict(c)
    cc["_claimed"] = _claimed_words(v, c)
    said = _said([cc], list(run.claims.values())) or cc["_claimed"]
    q = c.get("quote")
    is_quote = bool(q) and _norm(said) == _norm(q)
    return said, is_quote


def _relevant(run: Run, v: dict) -> set:
    """Sources whose evidence spoke to this claim (the winning source included)."""
    c = run.claim(v)
    kind = _kind(v, c)
    cid = v.get("claim_id")
    out = set()
    if v.get("source") in SRC_NAME and v.get("verdict") != "cant_check":
        out.add(v["source"])
    for e in run.evidence:
        src, obs = e.get("source"), e.get("observed") or {}
        if src not in SRC_NAME or e.get("status", "ok") != "ok":
            continue
        if obs.get("feature_check"):
            if cid in (obs.get("claim_ids") or []):
                out.add(src)
            continue
        if kind not in obs and not (kind in ("bedrooms", "bathrooms", "area_sqft") and obs.get("floorplans")):
            continue
        plan = run.plan_of(e)
        if c.get("label"):
            if plan and not _same_plan(plan, c.get("label")):
                continue
        elif plan and plan != run.only_label and not obs.get("floorplans"):
            continue
        out.add(src)
    return out


def _marks(run: Run, v: dict, label: str = "", credit: bool = False) -> str:
    """Verdict word, one square per source, the source names. credit=True (the hero): only the sources credited
    with the found value (_credit), the deciding one first, then the confidence band; else every source that spoke
    to the claim (_relevant)."""
    verdict = v.get("verdict")
    off = verdict in ("contradicted", "close")
    hit = v.get("source")
    if credit:
        keys, band = _credit(v)
        srcs = set(keys)
        hit = keys[0] if keys else hit
        names = " · ".join(x for x in (" + ".join(SRC_NAME[k] for k in keys), f"{band} confidence" if band else "") if x)             or "No source"
    else:
        srcs = _relevant(run, v)
        names = " · ".join(SRC_NAME[s] for s in SRC_ORDER if s in srcs) or "No source"
    sq = "".join(f"<i class='{'hit' if off and s == hit else 'on' if s in srcs else ''}'></i>"
                 for s in SRC_ORDER)
    word = label or VERDICT_WORD.get(verdict, "Couldn't check")
    cls = "off" if off else "cant" if verdict not in ("confirmed",) else ""
    aria = "From " + names if srcs else "No source spoke to this"
    return (f"<p class=marks><span class='vw {cls}'>{_e(word)}</span><span class=sq role=img aria-label='{_e(aria)}'>"
            f"{sq}</span><span>{_e(names)}</span></p>")


def _walk_hrefs(run: Run, v: dict):
    """(page, frame) of the 3D walkthrough for one scan verdict, else None (same rule as server.report._walk)."""
    from server.tour import tour_href, tour_target
    cid = str(v.get("claim_id") or "")
    if v.get("source") != "scan" or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid) \
            or not re.fullmatch(r"[a-z0-9-]{1,64}", str(run.raw.get("run_id") or "")):
        return None
    try:
        if not tour_target(run.raw, cid):
            return None
    except Exception:
        return None
    return tour_href(run.raw, cid), tour_href(run.raw, cid, embed=True), cid


def _fig(p: str, cap: str, cls: str = "") -> str:
    short = _short_caption(cap)
    if "floor plan" in str(cap).lower():
        cls = (cls + " plan").strip()
    return (f"<a class='pic {cls}' href='{_e(p)}' data-cap='{_e(short)}'>"
            f"<img src='{_e(p)}' alt='{_e(short or 'Proof picture')}' loading=lazy></a>")


def _pics(imgs: list, caption: bool = True) -> str:
    if not imgs:
        return ""
    lead, rest = imgs[0], imgs[1:]
    h = ["<div class=pics>", _fig(*lead, cls="lead")]
    if rest:
        h.append("<div class=thumbs>" + "".join(_fig(p, c) for p, c in rest) + "</div>")
    h.append("</div>")
    caps = list(dict.fromkeys(_short_caption(c) for _, c in imgs if _short_caption(c)))
    if caption and caps:
        h.append(f"<figcaption>{_e(' · '.join(caps[:4]))}</figcaption>")
    return "".join(h)


# ---------- proof drawings ----------

def _draw_area(claimed: float, found: float) -> str:
    big = max(claimed, found)
    s_c, s_f = 300 * math.sqrt(claimed / big), 300 * math.sqrt(found / big)
    pad = 30
    W = 300 + 2 * pad
    oc, of = pad + (300 - s_c) / 2, pad + (300 - s_f) / 2
    return (f"<div class=draw><svg viewBox='0 0 {W} {W + 40}' role=img aria-label='Listed {_fmt_n(claimed)} sq ft, "
            f"found {_fmt_n(found)} sq ft'>"
            f"<rect x='{of:.1f}' y='{of:.1f}' width='{s_f:.1f}' height='{s_f:.1f}' fill='#fff' stroke='#111' stroke-width='8'/>"
            f"<rect x='{oc:.1f}' y='{oc:.1f}' width='{s_c:.1f}' height='{s_c:.1f}' fill='none' stroke='#1a4be0' "
            "stroke-width='2.5' stroke-dasharray='9 7'/>"
            f"<text x='{W / 2}' y='{W / 2 + 8}' text-anchor=middle font-size='30' fill='#111' "
            f"font-family='Newsreader,Georgia,serif'>{_fmt_n(found)} sq ft</text>"
            f"<text x='{W / 2}' y='{W + 28}' text-anchor=middle font-size='18' fill='#1a4be0' "
            f"font-family='Newsreader,Georgia,serif'>listed outline, {_fmt_n(claimed)}</text></svg></div>")


def _draw_floors(claimed: int, found: int, height_m) -> str:
    top = max(claimed, found)
    if top <= 0 or top > 60:
        return ""
    fh = max(14, min(56, 360 // top))
    w, x0, base = 220, 60, 30 + top * fh
    parts = []
    for i in range(top):
        y = base - (i + 1) * fh
        if i < found:
            parts.append(f"<rect x='{x0}' y='{y}' width='{w}' height='{fh}' fill='#fff' stroke='#111' stroke-width='3'/>")
            for k in range(4):
                if fh >= 22:
                    parts.append(f"<rect x='{x0 + 18 + k * 50}' y='{y + fh * .28:.1f}' width='30' height='{fh * .44:.1f}' fill='#e3e3e0'/>")
        else:
            parts.append(f"<rect x='{x0}' y='{y}' width='{w}' height='{fh}' fill='#e7ecfc' stroke='#1a4be0' "
                         "stroke-width='2' stroke-dasharray='8 6'/>")
    if claimed > found:
        ylab = base - claimed * fh + (claimed - found) * fh / 2 + 6
        parts.append(f"<text x='{x0 + w / 2}' y='{ylab:.1f}' text-anchor=middle font-size='17' fill='#1a4be0' "
                     f"font-family='Newsreader,Georgia,serif'>listed: {claimed}</text>")
    parts.append(f"<line x1='{x0 - 30}' y1='{base}' x2='{x0 + w + 40}' y2='{base}' stroke='#111' stroke-width='3'/>")
    hm = _num(height_m)
    if hm:
        yt = base - found * fh
        parts.append(f"<line x1='{x0 + w + 22}' y1='{yt}' x2='{x0 + w + 22}' y2='{base}' stroke='#111' stroke-width='2'/>"
                     f"<line x1='{x0 + w + 14}' y1='{yt}' x2='{x0 + w + 30}' y2='{yt}' stroke='#111' stroke-width='2'/>"
                     f"<text x='{x0 + w + 32}' y='{(yt + base) / 2 + 6:.1f}' font-size='18' fill='#111' "
                     f"font-family='Newsreader,Georgia,serif'>{_fmt_n(round(hm, 1))} m</text>")
    H = base + 12
    return (f"<div class=draw><svg viewBox='0 0 {x0 + w + 90} {H}' role=img "
            f"aria-label='Listed {claimed} floors, found {found}'>{''.join(parts)}</svg></div>")


def _draw_beds(claimed: int, found: int, den: bool) -> str:
    if max(claimed, found) > 8:
        return ""

    def row(n, y, color, label, extra=""):
        cells = []
        for i in range(max(n, 0)):
            cells.append(f"<rect x='{110 + i * 70}' y='{y}' width='56' height='44' fill='none' stroke='{color}' stroke-width='3'/>"
                         f"<rect x='{116 + i * 70}' y='{y + 6}' width='18' height='12' fill='{color}'/>")
        if n <= 0:
            cells.append(f"<text x='110' y='{y + 30}' font-size='20' fill='{color}' font-family='Newsreader,Georgia,serif'>studio</text>")
        if extra:
            x = 110 + max(n, 1) * 70
            cells.append(f"<rect x='{x}' y='{y}' width='56' height='44' fill='#e7ecfc' stroke='{color}' stroke-width='2' "
                         f"stroke-dasharray='6 5'/><text x='{x + 28}' y='{y + 28}' text-anchor=middle font-size='15' "
                         f"fill='{color}' font-family='Newsreader,Georgia,serif'>{extra}</text>")
        return (f"<text x='0' y='{y + 28}' font-size='18' fill='#6b6b6b' font-family='Newsreader,Georgia,serif'>{label}</text>"
                + "".join(cells))

    W = 110 + (max(claimed, found) + 1) * 70
    return (f"<div class=draw><svg viewBox='0 0 {W} 150' role=img aria-label='Listed {claimed} bedrooms, found {found}'>"
            + row(claimed, 10, "#858585", "Listed") + row(found, 90, "#1a4be0", "Found", "den" if den else "")
            + "</svg></div>")


def _measured(run: Run, v: dict) -> str:
    c = run.claim(v)
    kind = _kind(v, c)
    a, b = _num(v.get("claimed")), _num(v.get("found"))
    if kind == "bedrooms" and str(v.get("claimed") or "").lower().startswith("studio"):
        a = 0.0
    if a is None or b is None:
        return ""
    if kind == "area_sqft" and a > 0 and b > 0:
        return _draw_area(a, b)
    if kind == "stories" and a == int(a) and b == int(b):
        hm = next(((e.get("observed") or {}).get("height_m") for e in run.evidence
                   if e.get("source") == "aerial" and (e.get("observed") or {}).get("height_m")), None)
        return _draw_floors(int(a), int(b), hm if v.get("source") == "aerial" else None)
    if kind == "bedrooms" and a == int(a) and b == int(b):
        return _draw_beds(int(a), int(b), "den" in str(v.get("found") or "").lower())
    return ""


def _line_cards(run: Run, v: dict) -> str:
    c = run.claim(v)
    kind = _kind(v, c)
    src = v.get("source")
    if src not in ("record", "county"):
        return ""
    lab = KIND_LABEL.get(kind, _cap(kind.replace("_", " ")) if kind else "On record")
    cards = [f"<div class=card><span class=eb>{_e(SRC_LONG[src])}</span>"
             f"<div class=row><span>{_e(lab)}</span><b>{_e(_cap(str(v.get('found') or '')))}</b></div>"]
    others = run.others(v, c)
    if others:
        cards.append(f"<span class=also>{_e(others)}</span>")
    cards.append("</div>")
    return "<div class=cards>" + "".join(cards) + "</div>"


# ---------- sections ----------

def _counts(run: Run) -> dict:
    n = {"contradicted": 0, "close": 0, "confirmed": 0, "cant_check": 0}
    for v in run.verdicts:
        k = v.get("verdict")
        n[k if k in n else "cant_check"] += 1
    return n


def _tier(run: Run, v: dict) -> int:
    """Renter impact of a claim: 0 beds/baths/size, 1 rent and fees, 2 building facts, 3 amenities and the rest."""
    c = run.claim(v)
    kind = _kind(v, c)
    if kind in TIER_UNIT:
        return 0
    if MONEY_RE.search(f"{c.get('quote') or ''} {v.get('claimed') or ''} {c.get('value') or ''}"):
        return 1
    if kind in TIER_BUILDING:
        return 2
    return 3


def _gap(run: Run, v: dict) -> float:
    """How far off the listing is, relative to what it says (0 when the numbers cannot be read)."""
    kind = _kind(v, run.claim(v))
    a, b = _num(v.get("claimed")), _num(v.get("found"))
    if kind == "bedrooms" and str(v.get("claimed") or "").lower().startswith("studio"):
        a = 0.0
    if a is None or b is None:
        return 0.0
    if kind == "year_built":
        return abs(a - b) / 50.0  # five years off weighs like a tenth
    return abs(a - b) / max(abs(a), 1.0)


def _rank(run: Run, v: dict) -> tuple:
    """Tie-break key: hard mismatches first, then by renter impact, a bigger gap first, then page order."""
    pos = run.order.index(v["claim_id"]) if v.get("claim_id") in run.order else 10**6
    return (0 if v.get("verdict") == "contradicted" else 1, _tier(run, v), -_gap(run, v), pos)


def _top_line(run: Run, v: dict) -> str:
    """One plain line naming the top-ranked mismatch: 'Listing says “2 Beds”; the 3D scan shows 1 bedroom and a den.'"""
    c = run.claim(v)
    said, is_q = _strike_text(run, v, c)
    said = f"“{said}”" if is_q else said
    found = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", str(v.get("found") or "")).strip()
    if not found:
        return ""
    who = SRC_FROM.get(v.get("source")) or ("the measured roof height suggests" if _kind(v, c) == "stories"
                                             else "Vexcel aerial photos suggest")
    plan = f" for {c['label']}" if c.get("label") else ""
    return f"Biggest difference: the listing says {said}{plan}; {who} {found}."


def _off_count(n: dict) -> int:
    """Every claim that does not match: contradicted or close, whatever its confidence (a low one is fainter on the
    page, never left out of the count)."""
    return n["contradicted"] + n["close"]


def _headline(n: dict) -> tuple[str, str, bool]:
    off = _off_count(n)
    if off == 1:
        return "1 thing", "doesn't match.", False
    if off > 1:
        return f"{off} things", "don't match.", False
    if n["confirmed"]:
        return "Everything we could", "check matches.", True
    if n["cant_check"]:
        return "Nothing here could", "be checked.", True
    return "No claims", "to check.", True


def _lead(off: list):
    """The finding the hero names: the first mismatch in the list (_ranked_off: the highest score)."""
    return off[0] if off else None


def top_finding(run_dict: dict) -> dict:
    """The hero's headline and lead finding, for the drawer (done.top, docs/CONTRACT.md 7): the same counts, ranking
    and words the report uses, so the two never disagree.
    {"headline": "1 thing doesn't match.", "lines": ["1 thing", "doesn't match."], "calm": bool,
     "hard": the mismatch count (contradicted + close), "claim_id": id of the lead mismatch or None,
     "line": "Biggest difference: ..." or "", "order": claim ids of every mismatch in the report's order (highest
     confidence score first)}"""
    run = Run(run_dict)
    n = _counts(run)
    off = _ranked_off(run, run.verdicts)
    top = _lead(off)
    l1, l2, calm = _headline(n)
    return {"headline": f"{l1} {l2}", "lines": [l1, l2], "calm": calm, "hard": _off_count(n),
            "claim_id": top.get("claim_id") if top else None, "line": _top_line(run, top) if top else "",
            "order": [v.get("claim_id") for v in off], "why": _pick_why(run, top),
            "by": _pick_by(run, top)}


def _source_used(run: Run) -> dict:
    ok = {s: [e for e in run.evidence if e.get("source") == s and e.get("status", "ok") == "ok"] for s in SRC_ORDER}
    return ok


def _fallback_field(run: Run) -> str:
    cols = []
    per = {s: 0 for s in SRC_ORDER}
    ordered = sorted(run.verdicts, key=lambda v: run.order.index(v["claim_id"]) if v.get("claim_id") in run.order else 10**6)
    for v in ordered:
        rel = _relevant(run, v)
        cells = []
        for s in SRC_ORDER:
            if s in rel:
                per[s] += 1
            if s == v.get("source") and v.get("verdict") in ("contradicted", "close"):
                cells.append("<i class=x></i>")
            elif s == v.get("source") and v.get("verdict") == "confirmed":
                cells.append("<i class=m></i>")
            elif s in rel:
                cells.append("<i class=w></i>")
            else:
                cells.append("<i></i>")
        cols.append(f"<div class=fld-col>{''.join(cells)}</div>")
    if not cols:
        return ""
    names = "".join(f"<span>{SRC_NAME[s]}<b>{per[s] or 'none'}</b></span>" for s in SRC_ORDER)
    return (f"<figure class='rv' style='margin:0'><div class=fld aria-hidden=true><div class=fld-names>{names}</div>"
            f"<div class=fld-cols style='--fw:{len(cols) * 10}px'>{''.join(cols)}</div></div>"
            "<figcaption class=legend><span><i class=m></i>settled, matches</span><span><i class=x></i>settled, doesn't match"
            "</span><span><i class=w></i>weighed in</span><span><i></i>nothing to say</span>"
            "<span>One column per claim.</span></figcaption></figure>")


def _field(run: Run, live_3d: bool = True) -> str:
    try:
        from server.claim_field import claim_field_section
    except ImportError:
        return _fallback_field(run)
    try:
        out = claim_field_section(run.raw, tours=live_3d)
        if isinstance(out, str) and out.strip():
            return out
    except Exception:
        pass
    return _fallback_field(run)



def _hero(run: Run, n: dict, top=None, has_facts: bool = False) -> str:
    """1: the count as a huge two-line headline, then a narrow column at the right: the address on two lines, one
    plain sentence naming the lead mismatch (`top`, no strike-through: that lives in section 2), the claim count and
    sources, the run meta and the button down."""
    l1, l2, calm = _headline(n)
    address = run.snap.get("address_text") or run.match.get("address") or "Unknown address"
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    where = _e(parts[0]) + ("<br>" + _e(", ".join(parts[1:])) if len(parts) > 1 else "")
    used = [SRC_NAME[s] for s, evs in _source_used(run).items() if evs]
    total = len(run.verdicts)
    lead_line = _top_line(run, top) if top else ""
    if calm and n["confirmed"]:
        sub = f"{_plural(n['confirmed'], 'claim matches', 'claims match')}."
        if n["cant_check"]:
            sub += f" {n['cant_check']} we couldn't check."
    elif total:
        srcs = (", ".join(used[:-1]) + " and " + used[-1]) if len(used) > 1 else (used[0] if used else "")
        sub = f"{_plural(total, 'claim', 'claims')}" + (f", held up to {srcs}." if srcs else ", but no source had data.")
    else:
        sub = "The listing made no claims we could read."
    btn = ""
    if _off_count(n):
        btn = f"<a class='btn big' href='#mismatch'>Show me what's off{ARROW_DOWN}</a>"
    elif n["confirmed"]:
        btn = f"<a class='btn big' href='#checked'>See what we checked{ARROW_DOWN}</a>"
    elif has_facts:
        btn = f"<a class='btn big' href='#extra'>See what the listing left out{ARROW_DOWN}</a>"
    meta = _meta(run)
    tick = TICK if calm and n["confirmed"] else ""
    lines = (f"<p class=top-line>{_e(lead_line)}</p>" if lead_line else "") + f"<p>{_e(sub)}</p>"
    return ("<section class=hero aria-label='Verdict'><div class=hero-top>"
            f"<h1><span>{_e(l1)}</span><span class=l2>{_e(l2)}{tick}</span></h1>"
            f"<div class='addr rv' style='--d:150ms'><p class=where>{where}</p>{lines}"
            + (f"<p class=meta>{_e(meta)}</p>" if meta else "") + btn + "</div></div></section>")

def _short_found(run: Run, v: dict) -> str:
    """'1 bedroom and a den' -> '1 bed + den': the found value in a few words (server.claim_field.compact), else the
    whole found text."""
    c = run.claim(v)
    if v.get("verdict") == "close" and _kind(v, c) in SOFT_KINDS:
        return "Not seen in the photos"
    found = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", str(v.get("found") or "")).strip()
    try:
        from server.claim_field import compact
        short = compact(found, 40)
    except Exception:  # noqa: BLE001
        short = ""
    return short or _cap(found).rstrip(".")


def _supports(says, found) -> bool:
    """Whether a source's own figure ('216 apartments', 'about 6 floors') is the found value: the same first number
    when the found value has one ('built in 2019' vs 'built in 2020': no), else the same words."""
    s, f = _bare("", says), _bare("", found)
    if not s or not f:
        return False
    ns, nf = _num(s), _num(f)
    if nf is not None:
        return ns is not None and abs(ns - nf) < 1e-9
    return _norm(s) == _norm(f)


def _credit(v: dict) -> tuple[list, str]:
    """(keys of the sources credited with this verdict's found value: the deciding one (confidence.decided_by, else
    the verdict's source) first, then each other source in confidence.sources whose own `says` is the found value;
    a source that says something else is never credited; the confidence band 'high' | 'medium' | 'low' or '')."""
    conf = v.get("confidence") if isinstance(v.get("confidence"), dict) else {}
    keys = []
    for k in (conf.get("decided_by"), v.get("source")):
        if k in SRC_NAME:
            keys.append(k)
            break
    for r in conf.get("sources") or []:
        if isinstance(r, dict) and r.get("source") in SRC_NAME and r["source"] not in keys \
                and r.get("agrees_with_listing") is not True and _supports(r.get("says"), v.get("found")):
            keys.append(r["source"])
    band = str(conf.get("band") or "").lower()
    return keys, (band if band in ("high", "medium", "low") else "")


def _deciders(v: dict) -> tuple[list, str]:
    """(names of the credited sources (_credit); the confidence band or '')."""
    keys, band = _credit(v)
    return [SRC_NAME[k] for k in keys], band



def _kind_text(s) -> str:
    """One short line fit to show: whitespace folded, '' when it is long or uses unkind words or markup."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or len(s) > 160 or UNKIND.search(s) or "<" in s:
        return ""
    return s


def _squares(keys: list) -> str:
    """One square per source, in source order: the deciding one blue, any other credited one black."""
    if not keys:
        return ""
    sq = "".join(f"<i class='{'hit' if keys and s == keys[0] else 'on' if s in keys else ''}'></i>" for s in SRC_ORDER)
    return f"<span class=sq aria-hidden=true>{sq}</span>"


def _record_line(run: Run, v: dict) -> str:
    """One plain line of what the deciding source holds: 'CoStar building record · Floors: 4', 'Matterport scan
    titled “Plan E - 1 Bed + Den - 850 SF”', 'Vexcel roof height · 23.1 m'; '' when there is none."""
    c = run.claim(v)
    kind = _kind(v, c)
    src = v.get("source")
    found = _bare(kind, v.get("found"))
    lab = KIND_LABEL.get(kind) or _cap(str(kind or "").replace("_", " "))
    if src in ("record", "county") and found and kind not in SOFT_KINDS:
        return f"<p class=rline>{_e(SRC_LONG[src])} <span>· {_e(lab)}: {_e(found)}</span></p>"
    if src == "scan":
        title = _scan_title(run, c.get("label")) if c.get("label") else ""
        if not title:
            title = next((str((e.get("observed") or {}).get("model_name") or "") for e in run.evidence
                          if e.get("source") == "scan" and (e.get("observed") or {}).get("model_name")
                          and not (e.get("observed") or {}).get("example_for")), "")
        if title:
            return f"<p class=rline>Matterport 3D scan <span>· titled “{_e(title)}”</span></p>"
    if src == "aerial" and kind == "stories":
        hm = next((_num((e.get("observed") or {}).get("height_m")) for e in run.evidence
                   if e.get("source") == "aerial" and _num((e.get("observed") or {}).get("height_m"))), None)
        if hm:
            return f"<p class=rline>Vexcel roof height <span>· {_e(_fmt_n(round(hm, 1)))} m</span></p>"
    return ""


def _conf(v: dict) -> tuple[int | None, str, str]:
    """(the verdict's 0-100 trust score (confidence.score) or None, band 'high' | 'medium' | 'low' or '', its
    one-line why or ''). Never a made-up number: no score, None."""
    conf = v.get("confidence") if isinstance(v.get("confidence"), dict) else {}
    s = conf.get("score")
    score = int(round(s)) if isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 100 else None
    band = str(conf.get("band") or "").lower()
    return score, (band if band in ("high", "medium", "low") else ""), _kind_text(conf.get("why"))


SHADE_SCORE = {"high": 85, "medium": 60, "low": 30}  # a band with no score, for ordering only (never shown)


def _shade(v: dict) -> str:
    """'high' | 'medium' | 'low': how strong the blue is for one mismatch. The confidence band (else read from the
    score); no confidence at all: a contradicted one full, a close one faint."""
    score, band, _ = _conf(v)
    if band:
        return band
    if score is not None:
        return "high" if score >= 75 else "medium" if score >= 45 else "low"
    return "high" if v.get("verdict") == "contradicted" else "low"


def _strength(v: dict) -> float:
    """How sure the run is of one mismatch, for ordering: the score, else its band's middle (SHADE_SCORE)."""
    score, _, _ = _conf(v)
    return float(score) if score is not None else float(SHADE_SCORE[_shade(v)])


def _tag(v: dict) -> str:
    """The score as text: '82 / 100 · high', '82 / 100', 'High' (no score: the level word only, never a made-up
    number), or '' (neither)."""
    score, band, _ = _conf(v)
    if score is not None:
        return f"{score} / 100" + (f" · {band}" if band else "")
    return _cap(band)


def _score_html(v: dict) -> str:
    """The score as the eye reads it: a serif numeral with a small '/ 100 · high' after it; no score: the level word
    alone at the numeral's size; '' with neither."""
    score, band, _ = _conf(v)
    if score is not None:
        unit = "/ 100" + (f" · {band}" if band else "")
        return f"<span class=sc-n>{score}</span><span class=sc-u>{_e(unit)}</span>"
    return f"<span class=sc-w>{_e(_cap(band))}</span>" if band else ""


def _tidy(s: str) -> str:
    """'its plan Plan F 2 Bedroom has' -> 'its Plan F 2 Bedroom has' (a doubled word from harness/graph/judge.py)."""
    return re.sub(r"\b(?i:plan)\s+(?=Plan\b)", "", str(s or ""))


def _conf_text(v: dict) -> str:
    """'Confidence 82 / 100 · high', 'Medium confidence' (no score), or ''."""
    score, band, _ = _conf(v)
    if score is not None:
        return f"Confidence {score} / 100" + (f" · {band}" if band else "")
    return f"{_cap(band)} confidence" if band else ""


def _art_href(run: Run, cid: str | None = None, key: str | None = None) -> str:
    """Link into the run's artifact library (server/artifacts.py, GET /artifacts/<run_id>): the page, or one source's
    proof for one claim (#a-<claim_id>-<costar|matterport|vexcel|county>). '' when the ids are not plain."""
    rid = str(run.raw.get("run_id") or "")
    if not re.fullmatch(r"[a-z0-9-]{1,64}", rid):
        return ""
    if cid is None:
        return f"/artifacts/{rid}"
    if key not in SRC_NAME or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(cid)):
        return ""
    return f"/artifacts/{rid}#a-{cid}-{SRC_NAME[key].lower()}"


def _src_names(run: Run, cid: str, keys: list, links: bool, lead: bool = False) -> str:
    """One square-cornered chip per source, each a link to its artifact in a new tab (links=True, the server's page)
    or plain text; lead=True: the first one (the deciding source) emphasised."""
    out = []
    for i, k in enumerate(keys):
        cls = "cite dec" if lead and i == 0 else "cite"
        href = _art_href(run, cid, k) if links else ""
        out.append(f"<a class='{cls}' href='{_e(href)}' target=_blank rel=noopener>{_e(SRC_NAME[k])}"
                   "<i aria-hidden=true>\u2197</i></a>" if href else f"<span class='{cls}'>{_e(SRC_NAME[k])}</span>")
    return "".join(out)


def _also_keys(run: Run, v: dict, keys: list) -> list:
    """The sources that spoke to this claim (_relevant, and the judge's own list) but do not back the found value:
    the 'also checked' ones, in source order. The drawer uses the same list (server/app.py)."""
    conf_rows = (v.get("confidence") or {}).get("sources") if isinstance(v.get("confidence"), dict) else None
    spoke = _relevant(run, v) | {r.get("source") for r in conf_rows or [] if isinstance(r, dict)}
    return [k for k in SRC_ORDER if k in spoke and k not in keys]


def _panel_proof(run: Run, v: dict, first: bool, live_3d: bool, used_imgs: set, used_walk: list) -> tuple[str, str]:
    """(layout, proof html) for one mismatch panel, decided by the proof it has. 'v' (vertical: the listing's
    words, the found value, then the visual proof filling the panel): a 3D scan view, scan or aerial pictures, or a
    measured drawing from the Vexcel roof height. 'c' (columnar: Listed | In reality, then one record line): a
    figure from CoStar / County / Vexcel numbers or a scan title."""
    c = run.claim(v)
    label = c.get("label")
    walk = _walk_hrefs(run, v)
    imgs = [x for x in run.images_for([v], label) if x[0] not in used_imgs]
    fid = " id=f1" if first else ""
    alt = _e(_truth_text(v).rstrip(".")[:40])
    if walk:
        if first:
            used_walk.append("#f1")
        view = next(((p, cap) for p, cap in imgs if "floor plan" not in cap.lower()), None)
        if view:
            used_imgs.add(view[0])
        bg = f"<img src='{_e(view[0])}' alt='' loading=lazy>" if view else (
            "<svg class=room viewBox='0 0 160 80' preserveAspectRatio='none' aria-hidden=true>"
            "<path d='M0 0h160v80H0z' fill='#e4e4e1'/><path d='M0 0l55 22h50L160 0z' fill='#f5f5f3'/>"
            "<path d='M0 80l55-22h50l55 22z' fill='#d6d5d1'/><path d='M55 22h50v36H55z' fill='#efefed'/></svg>")
        scan_name = _scan_title(run, label) if label else ""
        small = (f"{scan_name} · " if scan_name else "") + "Opening at the spot that shows this…"
        return "v", (f"<figure class='proof lvr-proof'{fid} data-proof='walk' data-alt='{alt}'>"
                     f"{_walk_box(walk[1], walk[2], bg, small, live_3d)}</figure>")
    if imgs:
        imgs = imgs[:3]
        used_imgs.update(p for p, _ in imgs)
        return "v", f"<figure class='proof lvr-proof'{fid} data-proof='pictures' data-alt='{alt}'>{_pics(imgs)}</figure>"
    if v.get("source") == "aerial":
        measured = _measured(run, v)
        if measured:
            return "v", f"<figure class='proof lvr-proof'{fid} data-proof='measured' data-alt='{alt}'>{measured}</figure>"
    line = _record_line(run, v)
    return "c", (f"<div class=lvr-proof data-proof='record'>{line}</div>" if line else "")


def _mismatch_view(run: Run, off: list, live_3d: bool = True, used_walk: list | None = None) -> str:
    """2 'Listed vs. in reality': one master-detail view that fits a screen. Left: a list, one row per mismatch
    (contradicted or close; never a match or a couldn't check) in the report's order (the run's pick first and
    selected, then the most certain): the claim type, the listing's words in short, 'Doesn't match · 40 low' (_tag).
    The blue of the tag, the found value and the left bar is fainter as the confidence drops (_shade).
    Right: the selected row's panel, two-sided: the listing's words (struck through in blue only when it doesn't
    match), what the sources show, one short reason, the credited sources (_credit) with the confidence score and
    its why, and the proof (_panel_proof: columnar for a figure, vertical for a visual proof). One mismatch: the
    panel only, full width. Only an anchor when nothing is off."""
    if not off:
        return "<span id=mismatch></span>"
    used_walk = used_walk if used_walk is not None else []
    used_imgs: set = set()
    one = len(off) == 1
    tabs, panels, layouts = [], [], []
    for i, v in enumerate(off):
        first = i == 0
        c = run.claim(v)
        kind = _kind(v, c)
        hard = v.get("verdict") == "contradicted"
        soft = not hard and kind in SOFT_KINDS
        said, is_q = _strike_text(run, v, c)
        said_html = f"“{_e(said)}”" if is_q else _e(_cap(said))
        truth = "Not seen in the photos" if soft else _truth_text(v).rstrip(".")
        what = KIND_LABEL.get(kind) or _cap(str(kind or "").replace("_", " ")) or "Claim"
        if kind == "amenity" and isinstance(c.get("value"), str) and 0 < len(c["value"]) <= 28:
            what = _cap(c["value"])  # "Pool" reads better in the list than "Amenity"
        name = " · ".join(x for x in (what, c.get("label")) if x)
        why = _kind_text(_tidy(_pick_why(run, v) or _first_sentence(v.get("reason") or "")))
        keys, _band = _credit(v)
        _s, _b, cwhy = _conf(v)
        conf_txt = _conf_text(v)
        others = _also_keys(run, v, keys)  # also checked: spoke, did not back it
        layout, proof = _panel_proof(run, v, first, live_3d, used_imgs, used_walk)
        layouts.append(layout)
        strike = "strike" if hard else ""  # a close one: the listing's words in plain grey, never struck
        cid = str(v.get("claim_id") or "")
        names = _src_names(run, cid, keys, live_3d, lead=True)
        also = _src_names(run, cid, others, live_3d)
        sid = re.sub(r"[^A-Za-z0-9_-]", "", cid) + f"-{i}"
        anchor = f"<span class=cl-a id='claim-{_e(cid)}'></span>" if cid else ""
        word = _score_html(v)
        shade = f" s-{_shade(v)}"
        listed = (f"<span class=lvr-lab>Listed</span><span class=lvr-said><span class='{strike}'>{said_html}</span>"
                  "</span>")
        found = f"<span class=lvr-lab>In reality</span><span class=lvr-truth>{_e(truth)}</span>"
        cred = ""
        if names or cwhy:
            cred = ("<div class=lvr-cred>"
                    + (f"<p class=lvr-cite><span class=cite-l>{'Sources' if len(keys) > 1 else 'Source'}</span>{names}"
                       + (f"<span class=cite-l>also checked</span>{also}" if also else "") + "</p>" if names else "")
                    + (f"<p class=lvr-cwhy>{_e(cwhy)}</p>" if cwhy else "") + "</div>")
        why_html = f"<p class=lvr-why>{_e(why)}</p>" if why else ""
        if layout == "v":
            body = f"<div class=vstack><div>{listed}</div><div>{found}</div></div>{proof}{why_html}{cred}"
        else:
            long = " long" if max(len(said), len(truth)) > 18 else ""  # a sentence-long side: smaller type
            body = (f"<div class='pcols{long}'><div class=lvr-l>{listed}</div><div class=lvr-r>{found}</div></div>"
                    f"{why_html}{cred}{proof}")
        cls = f"lvr-row md-p lay-{layout}{shade}" + (" in" if first else "")
        role = "" if one else f" role=tabpanel aria-labelledby=tab-{sid} tabindex=0"
        hide = "" if first or one else " hidden"
        panels.append(f"<div class='{cls}' id=pan-{sid}{role}{hide} data-claim='{_e(cid)}' data-layout='{layout}'>"
                      f"{anchor}<p class=lvr-k><span>{_e(name)}</span><span class=lvr-tag{f" aria-label='{_e(conf_txt)}'" if conf_txt else ""}>{word}</span></p>{body}</div>")
        tip = f" title='{_e(conf_txt)}'" if conf_txt else ""
        short = f"“{said}”" if is_q else _cap(said)
        tabs.append(f"<button type=button role=tab class='md-i{shade}' id=tab-{sid} "
                    f"aria-controls=pan-{sid} aria-selected={'true' if first else 'false'} "
                    f"tabindex={'0' if first else '-1'}><span class=md-t>{_e(name)}</span>"
                    f"<span class=md-g{tip}>{word}</span><span class=md-s>{_e(short)}</span></button>")
    n = len(off)
    sub = "1 difference." if one else f"{n} differences, highest confidence first."
    lst = "" if one else (f"<div class=md-list role=tablist aria-orientation=vertical aria-label=Differences>"
                          f"{''.join(tabs)}</div>")
    return ("<section class='wrap lvr' id=mismatch aria-labelledby=lvr-h><span id=compare></span>"
            f"<div class='lvr-top rv'><h2 class=sh id=lvr-h>Listed vs. in reality</h2><p class=lvr-n>{_e(sub)}</p></div>"
            f"<div class='md rv{' one' if one else ''}{'' if 'v' in layouts else ' flat'}'>{lst}<div class=md-ps>{''.join(panels)}</div></div></section>")


def _walk_box(src: str | None, cid: str, bg: str, small: str, live_3d: bool) -> str:
    """The inline 3D viewer: a still picture (bg) the page's script covers with the live viewer once it stands at
    the spot. live_3d False (static sample): the picture only, with a plain note, and no frame is ever made."""
    if not live_3d or not src:
        return (f"<div class=walk><div class=stage>{bg}<div class=tag>3D scan · Matterport<small class=wnote>"
                "The live 3D view opens in the report from the local checker.</small></div></div></div>")
    return (f"<div class=walk data-src='{_e(src)}' data-claim='{_e(cid)}'><div class=stage>{bg}"
            f"<div class=tag>Live 3D · Matterport<small class=wnote>{_e(small)}</small></div>"
            "<span class=pin aria-hidden=true></span><span class=spin aria-hidden=true></span></div>"
            "<button type=button class=fs>Full screen</button></div>")



def _bare(kind: str, s) -> str:
    """'built in 2015' -> '2015'; '815 sq ft (scan title)' -> '815 sq ft'."""
    s = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", str(s or "")).strip()
    if kind == "year_built":
        s = re.sub(r"(?i)^built\s+(?:in\s+)?", "", s)
    return s




def _pick(run: Run) -> dict | None:
    """The run's own choice of the leading mismatch (harness.top_mismatch.pick_top, recorded on `done` as
    harness.top_mismatch, or top_mismatch): {"claim_id", "why", "by"}; None for runs that have none."""
    d = run.done or {}
    for p in ((d.get("harness") or {}).get("top_mismatch") if isinstance(d.get("harness"), dict) else None,
              d.get("top_mismatch")):
        if isinstance(p, dict) and p.get("claim_id") is not None:
            return p
    return None


def _ranked_off(run: Run, verdicts: list) -> list:
    """Every mismatch (contradicted or close), strictly by confidence score, highest first; the ones with no score
    after them, a contradicted one before a close one; ties keep the run's own order. The run's recorded pick
    (_pick) never moves a row: the hero names whichever comes first."""
    def key(iv):
        i, v = iv
        score, _, _ = _conf(v)
        return (score is None, -(score or 0), 0 if v.get("verdict") == "contradicted" else 1, i)
    off = [iv for iv in enumerate(verdicts) if iv[1].get("verdict") in ("contradicted", "close")]
    return [v for _, v in sorted(off, key=key)]


def _pick_by(run: Run, v: dict | None) -> str | None:
    """"model" or "rule": who chose this verdict to lead (no recorded pick: the report's own rule); None: no lead."""
    if not v:
        return None
    p = _pick(run)
    return "model" if p and p.get("claim_id") == v.get("claim_id") and p.get("by") == "model" else "rule"


def _pick_why(run: Run, v: dict | None) -> str:
    """The pick's short reason, when the pick is this verdict and the reason is fit to show."""
    p = _pick(run)
    if not v or not p or p.get("claim_id") != v.get("claim_id"):
        return ""
    why = re.sub(r"\s+", " ", str(p.get("why") or "")).strip()
    if not why or len(why) > 120 or re.search(r"(?i)\b(fake|faked|lie|lies|lied|lying|scam|fraud\w*)\b|<", why):
        return ""
    return why


def _source_tile(run: Run, s: str, evs: list) -> str:
    obs: dict = {}
    for e in evs:
        for k, val in (e.get("observed") or {}).items():
            obs.setdefault(k, val)
    line = "#f2f2f2"
    if s == "record":
        bits = []
        rec = run.match.get("record") or {}
        if rec.get("year_built"):
            bits.append(f"Built {rec['year_built']}")
        if rec.get("stories"):
            bits.append(_plural(int(_num(rec["stories"]) or 0), "floor", "floors"))
        if rec.get("unit_count"):
            bits.append(_plural(int(_num(rec["unit_count"]) or 0), "home", "homes"))
        plans = len(run.match.get("floorplans") or [])
        name = run.match.get("name") if run.match.get("status") == "matched" else ""
        return (f"<b>{_e(name or 'Building record')}</b><span>{_e(' · '.join(bits))}"
                + (f"<br>{_plural(plans, 'floor plan', 'floor plans')} on record" if plans else "") + "</span>")
    if s == "scan":
        area = next((_num((e.get("observed") or {}).get("area_sqft")) for e in evs
                     if _num((e.get("observed") or {}).get("area_sqft"))), None)
        models = {(e.get("observed") or {}).get("model_id") for e in evs if (e.get("observed") or {}).get("model_id")}
        lab = f"{_fmt_n(round(area))} sq ft" if area else _plural(len(models), "scan", "scans")
        return ("<svg viewBox='0 0 180 110' aria-hidden=true fill=none>"
                f"<path d='M40 8h100v94H40z M40 58h100 M96 8v50 M96 30h44' stroke='{line}' stroke-width='3'/>"
                "<rect x='98' y='32' width='40' height='24' fill='#3b4a8c'/><circle cx='118' cy='44' r='4' fill='#8aa2ff'/>"
                f"<text x='50' y='92' fill='{line}' font-size='13' font-family='Newsreader,Georgia,serif'>{_e(lab)}</text></svg>")
    if s == "aerial":
        hm = _num(obs.get("height_m"))
        lab = f"roof {_fmt_n(round(hm, 1))} m" if hm else "seen from above"
        return ("<svg viewBox='0 0 180 110' aria-hidden=true fill=none>"
                f"<path d='M30 20h110v74H112V48H58v46H30z' stroke='{line}' stroke-width='3'/>"
                "<rect x='68' y='60' width='34' height='18' stroke='#8aa2ff' stroke-width='2'/>"
                "<path d='M152 14v86M164 14v86' stroke='#555' stroke-width='1.5'/>"
                f"<text x='30' y='12' fill='{line}' font-size='13' font-family='Newsreader,Georgia,serif'>{_e(lab)}</text></svg>")
    yb = obs.get("year_built")
    lab = f"built {yb}" if yb else "public record"
    return ("<svg viewBox='0 0 180 110' aria-hidden=true fill=none>"
            "<path d='M0 24h180M0 88h180M36 0v110M144 0v110' stroke='#444' stroke-width='1.5'/>"
            f"<rect x='54' y='30' width='72' height='52' fill='#1d1d1d' stroke='{line}' stroke-width='3'/>"
            f"<text x='90' y='61' text-anchor=middle fill='{line}' font-size='13' font-family='Newsreader,Georgia,serif'>"
            f"{_e(lab)}</text></svg>")


def _contribution(run: Run, s: str, evs: list, facts: list) -> str:
    keys = set()
    pics = 0
    for e in evs:
        keys |= set((e.get("observed") or {}).keys())
        pics += len([im for im in e.get("images") or [] if isinstance(im, dict) and IMG_RE.match(str(im.get("path") or ""))])
    words = list(dict.fromkeys(w for k, w in OBS_WORDS if k in keys))
    bits = []
    if words:
        bits.append(_cap(", ".join(words[:5])))
    if pics:
        bits.append(_plural(pics, "picture", "pictures"))
    nf = sum(1 for f in facts if f.get("source") == s)
    if nf:
        bits.append(f"{_plural(nf, 'fact', 'facts')} not on the listing")
    return ". ".join(bits) + "." if bits else ""


def _sources(run: Run, facts: list, links: bool = True) -> str:
    used = _source_used(run)
    try:  # the heatmap's own counts, so the two never disagree
        from server.claim_field import claim_field, source_counts
        per = source_counts(claim_field(run.raw))
        settled = {s: per[s]["settled"] for s in SRC_ORDER}
        weighed = {s: per[s]["weighed"] for s in SRC_ORDER}
    except Exception:  # noqa: BLE001  (claim_field missing or failed: count here)
        settled = {s: 0 for s in SRC_ORDER}
        weighed = {s: 0 for s in SRC_ORDER}
        for v in run.verdicts:
            for s in _relevant(run, v):
                if s == v.get("source") and v.get("verdict") != "cant_check":
                    settled[s] += 1
                else:
                    weighed[s] += 1
    cells = []
    for i, s in enumerate(SRC_ORDER):
        evs = used[s]
        had_facts = any(f.get("source") == s for f in facts)
        if not evs and not had_facts:
            tried = any(e.get("source") == s for e in run.evidence)
            note = "Had nothing for this address." if tried else "Not available for this address."
            cells.append(f"<div class='src off rv' style='--d:{i * 120}ms'><div class=tile aria-hidden=true></div>"
                         f"<span class=n>0</span><h3>{SRC_NAME[s]}</h3><p>{note}</p></div>")
            continue
        tot = settled[s] + weighed[s]
        what = []
        if settled[s]:
            what.append(f"settled {settled[s]}")
        if weighed[s]:
            what.append(f"weighed in on {weighed[s]}")
        spoke = (_plural(tot, "claim", "claims") + ": " + ", ".join(what) + ".") if tot else "Spoke to no claim."
        cells.append(f"<div class='src rv' style='--d:{i * 120}ms'><div class=tile>{_source_tile(run, s, evs)}</div>"
                     f"<span class=n data-n='{tot}'>{tot}</span><h3>{SRC_NAME[s]}</h3>"
                     f"<p>{_e(spoke)}</p><p class=mute>{_e(_contribution(run, s, evs, facts))}</p></div>")
    return ("<section class='dark' id=sources aria-label='Four sources'><div class='wrap srcs'>"
            "<h2 class='h2 rv'><span>Four sources.</span><span class=l2>One address.</span></h2>"
            "<p class='lede mute rv'>Every claim goes to each source that can speak to it. The number is how many it spoke to.</p>"
            + (f"<p class='art-lib rv'><a href='{_e(_art_href(run))}' target=_blank rel=noopener>Open the artifact library \u2192</a></p>"
               if links and _art_href(run) else "")
            +
            f"<div class=src-grid>{''.join(cells)}</div></div></section>")



TOPF_MAX = 5
TOPF_BANNED_IDS = {"class", "rating", "lot", "county-parcel", "owner", "county-owner", "manager", "leed",
                   "county-land-use", "building-size", "county-size"}
TOPF_BANNED = re.compile(r"(?i)parcel|building class|lot size|rating|owner|manag|contact|phone|e-?mail|\bagent\b")
TOPF_COUNT_ONLY = re.compile(r"(?i)311|complaint|service request")
# a 311 / complaint value: a count, optionally with when and how near ("43 since 2026-01-01 within 100 m"); never
# what the requests say
TOPF_COUNT_VALUE = re.compile(r"(?i)\d[\d,]*(?:\s+(?:requests?|complaints?|on record))?"
                              r"(?:\s+(?:since|in|over|within|near|during|the|last|past|of|around|nearby|"
                              r"\d[\d,./-]*|m|km|ft|feet|metres|meters|miles?|mi|days?|weeks?|months?|years?|"
                              r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)[,.]?)*")
# a top fact that names one of these is left out when a claim of that kind is already a mismatch (shown once)
TOPF_KIND_RX = {"stories": r"(?i)\b(floors?|stor(?:y|ies)|roof height|height)\b", "year_built": r"(?i)\byear built\b",
                "unit_count": r"(?i)\bnumber of homes\b|\bhomes \(county\)", "bedrooms": r"(?i)\bbedrooms?\b",
                "bathrooms": r"(?i)\bbathrooms?\b", "area_sqft": r"(?i)\bfloor area\b"}
TOPF_WHY = {
    "elevators": "Whether there is a lift, or every move-in and grocery run is by the stairs.",
    "available": "How many homes are open now: more choice, and maybe room to talk about rent.",
    "metering": "Decides whether utilities are billed to you or shared across the building.",
    "county-311": "Service requests logged with the city near the address. A count only.",
    "county-complaints": "Complaints on record for the address. A count only.",
    "roof-height": "Measured from the air: a check on how tall the building really is.",
}
TOPF_FILL = ["rent-range", "short-term", "lease", "pets", "parking", "parking-types", "aerial-features",
             "renovated", "units-on-record"]
TOPIC_WHY = {"Rent": "Helps you weigh the asking rent.", "Rules": "A day-to-day rule you would live with.",
             "Around it": "What sits right around the building.", "Building": "How the building is set up.",
             "Homes & floor plans": "What the homes here are like.", "Records": "From the public record."}


def _rule_top_facts(facts: list, off_kinds: set) -> list:
    """The local rule when the run recorded no pick: elevator / walk-up, homes available with the vacancy, utility
    meters, the 311 count, the measured height (only when floors are not already a mismatch), then a few plain
    rent / rules / around-it facts; at most TOPF_MAX."""
    by: dict = {}
    for f in facts:
        by.setdefault(str(f.get("id") or ""), f)
    out = []

    def add(f, label=None, value=None, why=None, image=None):
        if not f or f.get("id") in TOPF_BANNED_IDS:
            return
        img = image if image is not None else f.get("image")
        out.append({"label": label or f.get("label"), "value": value or f.get("value"),
                    "why": why or TOPF_WHY.get(f.get("id")) or TOPIC_WHY.get(f.get("topic"), ""),
                    "sources": [f.get("source")], "image": img, "topic": f.get("topic")})

    add(by.get("elevators"))
    av, vac = by.get("available"), by.get("vacancy")
    if av and vac:
        add(av, "Homes available", f"{av.get('value')} · {vac.get('value')}", TOPF_WHY["available"])
    else:
        add(av or vac, why=TOPF_WHY["available"])
    add(by.get("metering"))
    add(by.get("county-311") or by.get("county-complaints"))
    if "stories" not in off_kinds:
        view = by.get("aerial-view")
        add(by.get("roof-height"), image=(view or {}).get("image"))
    for fid in TOPF_FILL:
        if len(out) >= TOPF_MAX:
            break
        add(by.get(fid))
    return out


def _top_facts(run: Run, facts: list, off: list) -> list:
    """3's rows: done.harness.top_facts when the run recorded them, else harness.top_facts.pick_top_facts (no
    model), else the local rule; each checked (_clean_top_fact) and at most TOPF_MAX."""
    off_kinds = {_kind(v, run.claim(v)) for v in off}
    d = run.done or {}
    h = d.get("harness") if isinstance(d.get("harness"), dict) else {}
    raw = h.get("top_facts") if isinstance(h.get("top_facts"), list) else None
    if not raw and facts:
        def rule_only(*_a, **_k):  # rendering a report never calls a model: pick_top_facts falls back to its rule
            raise RuntimeError("rule only")
        try:
            from harness.top_facts import pick_top_facts
            raw = pick_top_facts(facts, verdicts=run.verdicts, claims=list(run.claims.values()), llm=rule_only,
                                 cache=False)
        except Exception:  # noqa: BLE001  (not there, or it failed: the local rule)
            raw = None
        if not isinstance(raw, list) or not raw:
            raw = _rule_top_facts(facts, off_kinds)
    # a picture only from the fill_in facts an item rests on (those carry /img/<token>); never the item's own path
    by_id = {str(f.get("id")): f for f in facts}
    fixed = []
    for it in raw or []:
        if isinstance(it, dict) and isinstance(it.get("fact_ids"), list):
            img = next((by_id[i]["image"] for i in it["fact_ids"] if i in by_id and by_id[i].get("image")), None)
            if img is None and "Vexcel" in (it.get("sources") or []) and by_id.get("aerial-view"):
                img = by_id["aerial-view"].get("image")
            it = {**it, "image": img}
        fixed.append(it)
    out = []
    for it in fixed:
        f = _clean_top_fact(it, off_kinds)
        if f and all(f["label"] != o["label"] for o in out):
            out.append(f)
        if len(out) >= TOPF_MAX:
            break
    return out


def _clean_top_fact(it, off_kinds: set) -> dict | None:
    """A top fact fit to show, or None: label and value present; never a parcel number, building class, lot size,
    rating, owner, manager or contact; a 311 / complaint row only as a count; never one that repeats a mismatch."""
    if not isinstance(it, dict):
        return None
    label = re.sub(r"\s+", " ", str(it.get("label") or "")).strip()
    value = re.sub(r"\s+", " ", str(it.get("value") if it.get("value") is not None else "")).strip()
    # a manager named next to something useful ("24 of 215 homes available; managed by X"): keep the useful part
    trimmed = re.sub(r"(?i)\s*(?:[;,·]|\band\b|\s[-–])?\s*\(?\b(?:managed|run|operated) by\b.*$", "", value).strip(" ;,·-")
    if trimmed != value and re.search(r"(?i)manag", label):
        label = "Homes available" if re.search(r"(?i)avail|vacan|homes", label + " " + trimmed) else ""
        value = trimmed
    if not label or not value or len(label) > 80 or len(value) > 120 or "<" in label + value:
        return None
    if it.get("id") in TOPF_BANNED_IDS or TOPF_BANNED.search(f"{label} {it.get('id') or ''}"):
        return None
    if TOPF_COUNT_ONLY.search(label) and not TOPF_COUNT_VALUE.fullmatch(value):
        return None
    if UNKIND.search(label + " " + value):
        return None
    if any(k in off_kinds and re.search(rx, label) for k, rx in TOPF_KIND_RX.items()):
        return None
    srcs = it.get("sources") if isinstance(it.get("sources"), list) else [it.get("source")]
    keys = []
    for s in srcs:
        k = s if s in SRC_NAME else next((x for x, nm in SRC_NAME.items() if nm == s), None)
        if k and k not in keys:
            keys.append(k)
    img = it.get("image")
    return {"label": label, "value": value, "why": _kind_text(it.get("why")), "keys": keys,
            "image": img if isinstance(img, str) and IMG_RE.match(img) else None}


def _not_listed(run: Run, facts: list, off: list) -> str:
    """3 'Not in the listing': at most five rows, each a label, the value, one line on why it matters, the source
    and a picture when there is one."""
    items = _top_facts(run, facts, off)
    head = "<h2 class='sh rv' id=nil-h>Not in the listing.</h2>"
    if not items:
        return (f"<section class='wrap nil' id=extra aria-labelledby=nil-h>{head}"
                "<p class=empty>Our sources have nothing to add beyond the listing.</p></section>")
    rows = []
    for i, f in enumerate(items):
        names = " + ".join(SRC_NAME[k] for k in f["keys"])
        pic = _fig(f["image"], f["label"]) if f["image"] else ""
        rows.append(f"<li class='nil-row rv' style='--d:{i * 70}ms'><div class=nil-m>"
                    f"<p class=nil-k><b>{i + 1}</b>{_e(f['label'])}</p><p class=nil-v>{_e(f['value'])}</p>"
                    + (f"<p class=nil-w>{_e(f['why'])}</p>" if f["why"] else "")
                    + (f"<span class=nil-src>{_squares(f['keys'])}{_e(names)}</span>" if names else "")
                    + "</div>" + pic + "</li>")
    sub = "What our sources know that the listing doesn't say."
    return (f"<section class='wrap nil' id=extra aria-labelledby=nil-h>{head}<p class='sh-sub rv'>{_e(sub)}</p>"
            f"<ol class=nil-rows>{''.join(rows)}</ol></section>")


def _checked(run: Run, ok: list, cant: list, live_3d: bool = True) -> str:
    """After the five sections: one collapsed 'Everything we checked' (#checked) with what matches (#match, 'See
    it in 3D' where a scan shows it) and what nobody could check (#cant-verify, grouped by reason)."""
    if not ok and not cant:
        return "<span id=checked></span><span id=match></span><span id=cant-verify></span>"
    blocks = []
    if ok:
        items = []
        for label, vs in _groups(run, ok):
            for v in vs:
                c = run.claim(v)
                said, is_q = _strike_text(run, v, c)
                walk = _walk_hrefs(run, v)
                link = (f"<button type=button class=see3d data-src='{_e(walk[1])}' data-claim='{_e(walk[2])}'>"
                        "See it in 3D</button>" if walk and live_3d else "")
                small = f"<small>{_e(label)}</small>" if label else ""
                text = f"“{_e(said)}”" if is_q else _e(_cap(said))
                items.append(f"<li><span>{small}{text}</span>{link}</li>")
        blocks.append(f"<div class='ck more-wrap' id=match><h3>{_e(_plural(len(items), 'thing matches', 'things match'))}"
                      f"</h3><ul class=mlist>{''.join(items)}</ul></div>")
    else:
        blocks.append("<span id=match></span>")
    if cant:
        by_reason: dict = {}
        for label, vs in _groups(run, cant):
            for v in vs:
                c = dict(run.claim(v))
                c["_claimed"] = _claimed_words(v, c)
                said, is_q = _strike_text(run, v, c)
                txt = f"“{said}”" if is_q else _cap(said)
                by_reason.setdefault(_cant_reason(run, v, c), []).append(f"{label}: {txt}" if label else txt)
        rs = "".join(f"<div><p title='{_e(r)}'>{_e(r)}</p><div class=qs>{_e(' · '.join(dict.fromkeys(t)))}</div></div>"
                     for r, t in by_reason.items())
        blocks.append(f"<div class=ck id=cant-verify><h3>{_e(_plural(len(cant), 'thing', 'things'))} nobody could check"
                      f"</h3><div class=reasons>{rs}</div></div>")
    else:
        blocks.append("<span id=cant-verify></span>")
    n = len(ok) + len(cant)
    return ("<section class='wrap checked' id=checked aria-label='Everything we checked'><details class=allck>"
            f"<summary>Everything we checked <span>{_e(_plural(len(ok), 'match', 'matches'))} · "
            f"{_e(str(len(cant)))} couldn't check · {_e(_plural(n, 'claim', 'claims'))}</span></summary>"
            f"<div class=ck-grid>{''.join(blocks)}</div></details></section>")

def _question(run: Run, v: dict) -> str:
    c = run.claim(v)
    kind = _kind(v, c)
    said, is_q = _strike_text(run, v, c)
    said = f"“{said}”" if is_q else said
    found = re.sub(r"\s*\([^)]*\)\s*$", "", str(v.get("found") or "")).strip()
    plan = f" for {c['label']}" if c.get("label") else ""
    verdict = v.get("verdict")
    if verdict in ("contradicted", "close") and kind not in SOFT_KINDS and found:
        src = v.get("source")
        who = SRC_FROM.get(src) or ("a roof height measurement suggests" if kind == "stories" else "Vexcel aerial photos suggest")
        if verdict == "close":
            return f"The listing says {said}{plan}; {who} {found}. Which number is exact?"
        return f"The listing says {said}{plan}, but {who} {found}. Which is right?"
    if kind in SOFT_KINDS:
        return f"Can you show me {said}{plan} when I tour?"
    if kind == "bedrooms" and not is_q and str(said).strip().lower() == "studio":
        return f"Can you confirm in writing that {c['label'] if c.get('label') else 'the home'} is a studio?"
    if c.get("label"):
        return f"Can you confirm in writing that {c['label']} has {said if is_q else str(said).strip()}?"
    return f"Can you confirm {said} in writing?"



def _question_list(run: Run, off: list, cant: list) -> list:
    """One question per mismatch (every one, however many), then the couldn't-checks until MAX_QUESTIONS."""
    qs: list = []
    for v in off:
        q = _question(run, v)
        if q and q not in qs:
            qs.append(q)
    for v in cant:
        if len(qs) >= MAX_QUESTIONS:
            break
        q = _question(run, v)
        if q and q not in qs:
            qs.append(q)
    return qs


def _questions(run: Run, off: list, cant: list) -> str:
    """4 'Ask before you tour.': a draft of neutral questions (deterministic template), each with Copy, and Copy
    all. Every mismatch gets its question."""
    qs = _question_list(run, off, cant)
    if not qs:
        return "<span id=questions></span>"
    lis = "".join(f"<li class=rv style='--d:{i * 80}ms'><span class=qn>{i + 1}</span><span>{_e(q)}</span>"
                  f"<button type=button class='btn ghost copy' data-text='{_e(q)}'>Copy</button></li>"
                  for i, q in enumerate(qs))
    every = "\n".join(qs)
    all_btn = (f"<p class=ask-all><button type=button class='btn copy' data-text='{_e(every)}'>"
               f"Copy all {len(qs)}</button></p>" if len(qs) > 1 else "")
    return ("<section class='wrap ask' id=questions aria-labelledby=ask-h>"
            "<h2 class='sh rv' id=ask-h>Ask before you tour.</h2>"
            "<p class='sh-sub rv'>A draft for the landlord or agent.</p>"
            f"<ol class=qlist>{lis}</ol>{all_btn}</section>")

def _footer(run: Run) -> str:
    url = run.snap.get("url") or ""
    title = run.snap.get("title") or "the listing"
    links = []
    if url.startswith(("http://", "https://")):
        links.append(f"<a href='{_e(url)}' target=_blank rel=noopener>Open the listing</a>")
    links.append("<a href='#top'>Back to the top</a>")
    meta = _meta(run)
    steps = [e for e in run.events if e.get("type") == "step" and e.get("text")]
    how = ""
    if steps:
        from server.report import AGENTS
        lis = "".join(f"<li><span class=ag>{_e(AGENTS.get(e.get('agent'), str(e.get('agent') or '').title()))}</span>"
                      f"{_e(e['text'])}</li>" for e in steps)
        how = f"<details class=how><summary>How we checked</summary><ol>{lis}</ol></details>"
    rid = str(run.raw.get("run_id") or "")
    return (f"<footer class=dark><div class='wrap foot'><p class=mark aria-hidden=true>{BRAND}</p>"
            f"<div class=foot-row><div class=foot-links>{''.join(links)}</div>"
            "<p class=foot-note>Sources: CoStar building records, Matterport 3D scans, Vexcel aerial imagery and county "
            "public records."
            + (f" {_e(meta)}." if meta else "") + f" Listing: {_e(title)}.</p></div>{how}"
            f"<div class=foot-end><span>Report {_e(rid)}</span><a href='#top'>Back to the top</a></div></div></footer>")



def render_report(run_dict: dict, live_3d: bool = True) -> str:
    run_dict = drop_dead_images(run_dict)  # older runs: pictures whose cached file is gone are left out
    run = Run(run_dict)
    address = run.snap.get("address_text") or "Unknown address"
    n = _counts(run)
    off = _ranked_off(run, run.verdicts)  # every mismatch: the run's pick, then the most certain first
    ok = [v for v in run.verdicts if v.get("verdict") == "confirmed"]
    cant = [v for v in run.verdicts if v.get("verdict") not in ("contradicted", "close", "confirmed")]
    facts = _fill_in(run)
    used_walk: list = []

    mismatch = _mismatch_view(run, off, live_3d, used_walk)  # 2
    extra = _not_listed(run, facts, off)  # 3
    hero = _hero(run, n, _lead(off), has_facts="nil-row" in extra)  # 1
    url = run.snap.get("url") or ""
    nav = []
    if url.startswith(("http://", "https://")):
        nav.append(f"<a class=back href='{_e(url)}' target=_blank rel=noopener>{ARROW_LEFT}<span>Listing</span></a>")
    if used_walk:
        nav.append(f"<a class=btn href='{_e(used_walk[0])}'>3D walk</a>")  # the lead row's own viewer
    else:
        nav.append("<button type=button class='btn share'>Share</button>")
    err = ""
    if not run.done and run.err:
        err = f"<p class='wrap empty'>{_e(run.err.get('message') or 'This check did not finish.')}</p>"
    field = (f"<div class='wrap field-sec' id=heatmap><h2 class='sh rv'>Every claim, every source.</h2>"
             f"{_field(run, live_3d)}</div>")

    h = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         f"<title>{BRAND}: {_e(address)}</title>",
         "<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>",
         "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap'>",
         PRECONNECT if used_walk and live_3d else "",
         f"<style>{CSS}</style></head><body id=top>",
         f"<div class=wrap><header class=top><a class=brand href='#top'>{BRAND}</a><nav aria-label=Report>{''.join(nav)}</nav></header>",
         hero, "</div>", err, mismatch, "<div class='wrap duo'>", extra, _questions(run, off, cant), "</div>", field, _sources(run, facts, live_3d),
         _checked(run, ok, cant, live_3d), _footer(run),
         "<div class=lb id=lb hidden role=dialog aria-modal=true aria-label='Picture'>"
         "<button type=button class=btn>Close</button><p></p></div>",
         f"<script>{SCRIPT}</script></body></html>"]
    return "".join(h)
