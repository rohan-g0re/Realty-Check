"""Full report page, design "Option A": one self-contained HTML document built from a saved run.

Sections, top to bottom (each one adapts to the run; nothing is invented):
  hero          headline from counts ("3 things don't match." / calm "Everything we could check matches."), the
                address, the run meta, and the claim field (server.claim_field, or a simple grid when it is missing)
  compare       "Listing vs record": one table, a row per claim (mismatches first), what the listing says beside what
                CoStar, Matterport, Vexcel and the county found, the result, and the judge's confidence on a mismatch
                (server.claim_field.compare_table)
  findings      one module per hard mismatch item (contradicted; plan claims grouped as in server.report), ranked by
                what costs the renter most (beds/baths/size, then rent and fees, then building facts, then
                amenities; a bigger relative gap first): the listing's words struck through in blue, the truth set
                huge beneath, one line, source marks, and a proof slot picked from the evidence the item actually
                has (3D walk, pictures, a measured drawing, a record/county line, or nothing). "Close" verdicts follow
                as a quiet list, never struck through ("Listing says 2015 · county records say 2014 — close")
  sources       "Four sources. One address.": per source, how many claims it settled or weighed in on, and what
                kind of thing it contributed in this run; a missing source says so
  extra         "Not on the listing" facts (fill_in), floor plans drawn to scale, topic groups, long lists collapsed
  matches       confirmed claims, a small proof picture where the evidence has one; long lists collapsed
  cant-verify   claims nobody could check, grouped by reason
  questions     simple neutral questions from the findings (deterministic template), each with a Copy button
  footer        wordmark, listing link, run meta, how we checked

The data logic and wording come from server.report (grouping, image ranking, walk links, fill-in, captions).
Pictures only via /img/<token>; outside requests: the Newsreader font, and Matterport inside the 3D viewers.
Pictures whose cached file is gone (older runs) are taken out before rendering (server.report.drop_dead_images), and
any picture that still fails to load is hidden in the browser (no broken-image icon; a finding falls back to text).

3D viewers (live_3d=True, the server's page): a finding whose proof is a 3D scan shows the live Matterport viewer
inline (the same-origin /tour/<run_id>?claim=<id>&embed=1 page in an iframe), opened at the picked spot, with no
click and no new tab. The viewer of the lead finding (or of the #claim-<id> the page was opened at) mounts at once;
the others mount when they come near the screen (IntersectionObserver) and unmount when far away, never more than
two live at a time. A still picture covers each viewer until the tour page reports "ready" (postMessage), so the
reader sees the spot, not the scan's start. "See it in 3D" on matches and in the claim field panel opens a viewer
in place (or scrolls to the finding's own one). live_3d=False (the extension's static sample, no server) keeps the
still picture with a plain note and never makes a frame.
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
VERDICT_WORD = {"contradicted": "Doesn't match", "close": "Close", "confirmed": "Matches", "cant_check": "Couldn't check"}
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
FACT_MANY = 24     # more facts than this: each topic shows FACT_SHOW rows, the rest behind a button
FACT_SHOW = 5
LIST_SHOW = 12     # matches / couldn't-check entries shown before the "show the other N" button
MAX_QUESTIONS = 5

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
/* header */
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;min-height:96px;gap:8px 16px}
.brand{font-size:19px;font-weight:500;letter-spacing:-.01em;white-space:nowrap;text-decoration:none}
.top nav{display:flex;align-items:center;gap:4px 20px}
.back{display:inline-flex;align-items:center;gap:6px;min-height:44px;font-size:15px;text-decoration:none}
/* hero */
.hero{display:flex;flex-direction:column;gap:clamp(40px,6vw,88px);padding:clamp(12px,3vw,48px) 0 clamp(80px,10vw,180px)}
.hero-top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:36px 6%}
h1{margin:0;flex:1 1 100%;font-weight:300;font-size:clamp(54px,10.5vw,156px);line-height:.92;letter-spacing:-.05em;
display:flex;flex-direction:column}
h1 .l2{padding-left:clamp(0px,24vw - 60px,300px)}
h1 .tick{display:inline-block;width:.42em;height:.42em;margin-left:.12em;vertical-align:.05em}
.addr{flex:0 1 400px;margin-left:auto;display:flex;flex-direction:column;gap:18px}
.addr .where{margin:0;font-size:24px;line-height:1.3;letter-spacing:-.01em}
.addr p{margin:0}
.addr .meta{font-size:15px;color:var(--mute)}
.addr .btn{align-self:flex-start}
.field-wrap{min-width:0}
/* fallback claim grid */
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
/* findings */
.findings{display:flex;flex-direction:column;gap:clamp(96px,11vw,180px);padding-bottom:clamp(96px,11vw,180px)}
.find{display:flex;flex-direction:column;gap:clamp(24px,3vw,44px);scroll-margin-top:24px}
.find .plan{margin:0;font-size:17px;color:var(--mute);letter-spacing:.01em}
.pair{margin:0;font-weight:300;line-height:.9;letter-spacing:-.05em;display:flex;flex-direction:column;gap:.06em}
.pair .said{color:var(--ghost);align-self:flex-start;max-width:100%;overflow-wrap:anywhere}
.pair .truth{color:var(--blue);overflow-wrap:anywhere}
.pair .truth.calm{color:var(--ink)}
.find.lead>.pair .truth{align-self:flex-end;text-align:right}
.sz-xl{font-size:clamp(60px,13.5vw,204px)}.sz-l{font-size:clamp(48px,9vw,140px)}
.sz-m{font-size:clamp(40px,6.4vw,100px)}.sz-s{font-size:clamp(34px,4.6vw,70px)}
.sub .pair{font-size:clamp(30px,4vw,60px)}
.strike{background:linear-gradient(var(--blue),var(--blue)) no-repeat 0 58%/100% .075em;
-webkit-box-decoration-break:clone;box-decoration-break:clone;padding:0 .03em}
.js .rv .strike{background-size:0 .075em;transition:background-size 1s cubic-bezier(.7,0,.2,1) .35s}
.js .rv.in .strike{background-size:100% .075em}
.line{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px 6%}
.why{margin:0;font-size:24px;line-height:1.3;letter-spacing:-.01em;max-width:44ch}
.marks{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;font-size:17px}
.marks .vw{font-weight:500}.marks .vw.off{color:var(--blue)}.marks .vw.cant{color:var(--mute);font-weight:400}
.sq{display:inline-flex;gap:4px;flex:none}.sq i{width:10px;height:10px;background:var(--line);display:block}
.sq i.on{background:var(--ink)}.sq i.hit{background:var(--blue)}
.subs{display:flex;flex-direction:column;gap:clamp(28px,3vw,40px);padding-top:8px;border-top:1px solid #ececec}
.sub{display:flex;flex-direction:column;gap:14px;padding-top:clamp(20px,2.4vw,32px)}
.sub .why{font-size:20px}
.two{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:clamp(28px,5vw,88px);align-items:center}
.two.flip{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}.two.flip .proof{order:2}
.two .text{display:flex;flex-direction:column;gap:clamp(20px,2.4vw,32px);min-width:0}
.proof{margin:0;min-width:0;display:flex;flex-direction:column;gap:12px}
.proof figcaption,.cap{font-size:15px;color:var(--mute)}
.rooms{font-size:16px}.rooms summary{cursor:pointer;color:var(--mute)}
.rooms ul{list-style:none;margin:10px 0 0;padding:0;columns:2 220px;column-gap:32px}
.rooms li{display:flex;justify-content:space-between;gap:12px;break-inside:avoid;border-bottom:1px solid #eee;padding:4px 0}
/* proof: 3D walk */
.stage{position:relative;background:#ededeb;overflow:hidden;aspect-ratio:16/8;min-height:300px}
.stage>img,.stage>svg.room{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.two .stage{aspect-ratio:4/3;min-height:0}
.stage>img{animation:drift 16s ease-in-out infinite alternate;transform-origin:50% 60%}
.stage .tag{position:absolute;left:clamp(12px,2vw,24px);top:clamp(12px,2vw,24px);background:#fff;padding:10px 16px;
font-size:16px;line-height:1.35;max-width:calc(100% - 24px)}
.stage .tag small{display:block;color:var(--mute);font-size:14px}
.stage .pin{position:absolute;left:50%;top:55%;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
background:var(--blue);box-shadow:0 0 0 3px #fff}
.stage .pin::after{content:'';position:absolute;inset:0;border-radius:50%;background:var(--blue);animation:pulse 2.4s ease-out infinite}
.stage .go{position:absolute;right:clamp(12px,2vw,24px);bottom:clamp(12px,2vw,24px)}
.walk{position:relative;background:#17181a;overflow:hidden;aspect-ratio:16/9;min-height:320px;max-height:82vh}
.two .walk,.cf-panel .walk,.see3d-box .walk{aspect-ratio:4/3}
.walk>iframe{position:absolute;inset:0;width:100%;height:100%;border:0;z-index:1;background:#17181a}
.walk>iframe.dock{position:fixed;inset:auto;left:0;top:0;opacity:0;pointer-events:none;z-index:-1}
.walk>.stage{position:absolute;inset:0;z-index:2;aspect-ratio:auto;min-height:0;transition:opacity .6s ease,visibility 0s linear 0s}
.walk.live>.stage{opacity:0;visibility:hidden;pointer-events:none;transition:opacity .6s ease,visibility 0s linear .6s}
.walk .spin{position:absolute;right:clamp(12px,2vw,24px);bottom:clamp(12px,2vw,24px);width:26px;height:26px;border-radius:50%;
border:3px solid rgba(255,255,255,.55);border-top-color:var(--blue);animation:spin 1s linear infinite;display:none}
.walk.loading .spin{display:block}
.walk .fs{position:absolute;right:12px;top:12px;z-index:3;display:none;background:#fff;color:#17181a;border:0;font:inherit;
font-size:14px;padding:7px 12px;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.walk.live .fs{display:block}
.walk:fullscreen{max-height:none;aspect-ratio:auto}
button.see3d{background:none;border:0;padding:0;font:inherit;color:var(--blue);text-decoration:underline;cursor:pointer}
.see3d-box{margin:12px 0 4px;width:100%}
/* proof: pictures */
.pics{display:grid;gap:10px}
.pics .lead img{width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--soft)}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px}
.thumbs img{width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
a.pic{display:block;overflow:hidden;transition:transform .25s ease,box-shadow .25s ease}
a.pic:hover{transform:translateY(-4px);box-shadow:0 14px 30px -18px rgba(0,0,0,.45)}
a.pic img{transition:transform .6s ease}
a.pic.plan img{object-fit:contain;background:#fff;padding:6px}a.pic:hover img{transform:scale(1.03)}
/* proof: drawings, lines, none */
.draw{background:var(--soft);padding:clamp(20px,3vw,40px);display:flex;justify-content:center;align-items:center}
.draw svg{width:100%;max-width:520px;height:auto;max-height:440px;overflow:visible}
.card{background:var(--soft);padding:clamp(22px,2.6vw,38px);display:flex;flex-direction:column;gap:10px;
transition:transform .25s ease,box-shadow .25s ease}
.card:hover{transform:translateY(-4px);box-shadow:0 14px 30px -20px rgba(0,0,0,.4)}
.card .eb{font-size:15px;color:var(--mute)}
.card .row{display:flex;justify-content:space-between;align-items:baseline;gap:16px;font-size:clamp(26px,2.6vw,38px);line-height:1.15}
.card .row b{font-weight:400;color:var(--blue);text-align:right}
.card .also{font-size:16px;color:var(--mute)}
.cards{display:flex;flex-direction:column;gap:10px}.cards .card+.card{margin-left:clamp(0px,4%,44px)}
.nopic{background:var(--soft);min-height:clamp(180px,18vw,260px);display:flex;align-items:center;justify-content:center;
padding:24px;text-align:center;font-weight:300;font-size:clamp(40px,5vw,76px);line-height:1;letter-spacing:-.04em;color:var(--blue)}
.nopic.q{color:var(--ghost)}
/* close, not exact: quiet, no strike-through */
.near{display:flex;flex-direction:column;gap:14px;padding-top:clamp(8px,1vw,16px);scroll-margin-top:24px}
.near h3{margin:0;font-weight:400;font-size:clamp(26px,2.6vw,36px);letter-spacing:-.02em}
.near ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.near li{display:flex;align-items:center;gap:14px 18px;padding:12px 0;border-top:1px solid #ececec;font-size:19px;
line-height:1.35}
.near li:last-child{border-bottom:1px solid #ececec}
.near .nk{flex:0 0 clamp(110px,16%,200px);color:var(--mute);font-size:16px}
.near .nv{flex:1;min-width:0}
.near .nv b{font-weight:400;color:var(--mute)}
.near a.pic{flex:none;width:72px}.near a.pic img{aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
.top-line{margin:0;font-size:20px;line-height:1.35}
.top-line q{quotes:'\201c' '\201d'}
.gone{display:none!important}
/* sources (the one dark band) */
.dark{background:#111;color:#f2f2f2}.dark a{color:#fff}.dark .mute{color:#a9a9a9}
.srcs{padding-top:clamp(96px,10vw,170px);padding-bottom:clamp(80px,8vw,140px)}
.h2{margin:0;font-weight:300;font-size:clamp(48px,8vw,124px);line-height:.92;letter-spacing:-.05em;display:flex;flex-direction:column}
.h2 .l2{padding-left:clamp(0px,15vw,220px)}
.lede{margin:clamp(24px,3vw,40px) 0 0 auto;max-width:360px;font-size:17px}
.src-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:clamp(16px,2.4vw,48px);margin-top:clamp(48px,6vw,96px)}
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
/* extra (not on the listing) */
.extra{padding-top:clamp(96px,10vw,170px);padding-bottom:clamp(60px,6vw,100px);scroll-margin-top:12px}
.plans{display:flex;flex-wrap:wrap;align-items:flex-end;gap:clamp(24px,3.4vw,56px);margin:clamp(48px,6vw,96px) 0 0}
.plans h3,.topics h3{margin:0 0 20px;font-weight:400;font-size:24px;flex:1 1 100%}
.plans h3 span{color:var(--ghost)}
.fp{display:flex;flex-direction:column;gap:6px;max-width:100%}
.fp svg{display:block;max-width:100%;height:auto;transition:transform .3s ease}.fp:hover svg{transform:translateY(-6px)}
.fp .nm{font-size:22px;line-height:1.2}.fp.this .nm{color:var(--blue)}
.fp .vl{font-size:16px;line-height:1.4;max-width:30ch}
.topics{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:clamp(32px,4vw,64px) clamp(28px,4vw,72px);
margin-top:clamp(56px,7vw,110px)}
.topic h3{margin-bottom:12px}
.fact{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr) auto;gap:6px 14px;align-items:baseline;padding:6px 0;font-size:17px}
.fact .k{color:var(--mute)}.fact .v{text-align:right;overflow-wrap:anywhere}
.fact .fs{font-size:13px;color:var(--mute);min-width:52px;text-align:right;white-space:nowrap}
.fact .sq i{width:6px;height:6px}.fact .sq{gap:2px}
.fact a.pic{grid-column:1/-1;max-width:220px}.fact a.pic img{aspect-ratio:16/10;object-fit:cover}
.topic .note{margin:8px 0 0;font-size:14px;color:var(--mute)}
.js .more-wrap:not(.open) .more{display:none}
.factfoot{display:flex;flex-wrap:wrap;align-items:center;gap:12px 24px;margin-top:clamp(28px,3vw,48px);font-size:15px;color:var(--mute)}
.empty{font-size:20px;color:var(--mute);margin:24px 0 0}
/* matches + couldn't check */
.split{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,8fr);gap:clamp(32px,5vw,88px);align-items:start}
.matches{padding-top:clamp(80px,8vw,140px);padding-bottom:clamp(80px,8vw,140px)}
.h3{margin:0;font-weight:300;font-size:clamp(46px,6vw,96px);line-height:.95;letter-spacing:-.045em}
.split .note{margin:16px 0 0;font-size:17px}
.mlist{list-style:none;margin:0;padding:0;columns:3 220px;column-gap:clamp(24px,3vw,56px);font-size:22px;line-height:1.35}
.mlist li{break-inside:avoid;padding:0 0 12px;display:flex;gap:12px;align-items:flex-start}
.mlist .mt{flex:1;min-width:0}
.mlist a.pic{flex:none;width:64px;margin-top:4px;margin-left:0}.mlist a.pic img{aspect-ratio:4/3;object-fit:cover;background:var(--soft)}
.mlist small{display:block;font-size:14px;color:var(--mute)}
.mlist a{font-size:14px;margin-left:6px}
.cant{background:var(--soft);padding-top:clamp(72px,8vw,130px);padding-bottom:clamp(72px,8vw,130px)}
.reasons{display:flex;flex-direction:column;gap:22px}
.reasons p{margin:0 0 4px;font-size:15px;color:var(--mute)}
.reasons .qs{font-size:22px;line-height:1.4}
.toggle{margin-top:18px}
/* questions */
.ask{padding-top:clamp(96px,10vw,170px);padding-bottom:clamp(96px,10vw,170px)}
.ask .split{grid-template-columns:minmax(0,6fr) minmax(0,6fr)}
.ask.solo .split{grid-template-columns:minmax(0,1fr)}
.qlist{list-style:none;margin:clamp(28px,3vw,48px) 0 0;padding:0;display:flex;flex-direction:column;gap:22px}
.qlist li{display:flex;flex-direction:column;align-items:flex-start;gap:10px;font-size:22px;line-height:1.35;max-width:36ch}
.ask .pics .lead img{aspect-ratio:4/5}
/* footer */
.foot{padding-top:clamp(80px,8vw,130px);padding-bottom:40px}
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
@media (max-width:900px){
.two,.two.flip,.split,.ask .split{grid-template-columns:minmax(0,1fr)}.two.flip .proof{order:0}
.src-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.find.lead .pair .truth{align-self:flex-start;text-align:left}
}
@media (max-width:560px){
body{font-size:18px}
.src-grid{grid-template-columns:minmax(0,1fr)}
.src .tile{aspect-ratio:auto;min-height:150px}
.why{font-size:21px}
.stage{aspect-ratio:auto;min-height:340px}
.stage .go{left:12px;right:12px}
.mlist,.reasons .qs,.qlist li{font-size:19px}
.near li{flex-wrap:wrap;font-size:17px}.near .nk{flex-basis:100%}
.fact{grid-template-columns:minmax(0,1fr) minmax(0,1.2fr) auto;font-size:16px}
.cards .card+.card{margin-left:0}
.top{min-height:72px}
}
@media (prefers-reduced-motion: reduce){
html{scroll-behavior:auto}
.js .rv,.js .rv.in{opacity:1;transform:none;transition:none}
.js .rv .strike{background-size:100% .075em;transition:none}
.js .fld-col i{transform:none;opacity:1;transition:none}
.stage>img,.stage .pin::after,h1 .tick path{animation:none}
.stage .pin::after{opacity:0}
a.pic,a.pic img,.card,.fp svg,.src .tile,.btn{transition:none}
a.pic:hover,.card:hover,.fp:hover svg,.src:hover .tile{transform:none}
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
var lb=document.getElementById('lb'),li=lb.querySelector('img'),lc=lb.querySelector('p');
function shut(){lb.hidden=true;li.removeAttribute('src');}
lb.addEventListener('click',shut);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!lb.hidden)shut();});
document.addEventListener('click',function(e){var t=e.target.closest?e.target:null;if(!t)return;
var a=t.closest('a.pic');if(a){e.preventDefault();li.src=a.getAttribute('href');var c=a.getAttribute('data-cap')||'';
li.alt=c||'Proof picture';lc.textContent=c;lb.hidden=false;lb.querySelector('button').focus();return;}
var c2=t.closest('button.copy');if(c2){copy(c2.getAttribute('data-text')||'',c2);return;}
var sh=t.closest('button.share');if(sh){copy(location.href,sh);return;}
var m=t.closest('button.morebtn');if(m){var w=document.getElementById(m.getAttribute('aria-controls'));if(!w)return;
var open=w.classList.toggle('open');m.setAttribute('aria-expanded',open?'true':'false');
var l=m.getAttribute('data-less'),o=m.getAttribute('data-more');m.textContent=open?l:o;return;}
var fs=t.closest('.walk .fs');if(fs){var wf=fs.closest('.walk');if(wf.requestFullscreen)wf.requestFullscreen();return;}
var s3=t.closest('button.see3d');if(s3&&window.RC3D){var bx=window.RC3D.see(s3.getAttribute('data-claim'),s3.getAttribute('data-src'),
s3.closest('.more-wrap')||s3.parentNode);if(bx)bx.scrollIntoView({behavior:still?'auto':'smooth',block:'nearest'});}});
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
R.see=function(cid,src,host){var a=cid&&document.getElementById('claim-'+cid),f=a&&a.closest('.find'),w=f&&f.querySelector('.walk[data-src]');
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
var ws=all(),first=null,h=location.hash.match(/^#claim-([A-Za-z0-9_-]{1,32})$/);
if(h){var a=document.getElementById('claim-'+h[1]),fd=a&&a.closest('.find');first=fd&&fd.querySelector('.walk[data-src]');}
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


def _marks(run: Run, v: dict, label: str = "") -> str:
    srcs = _relevant(run, v)
    verdict = v.get("verdict")
    off = verdict in ("contradicted", "close")
    sq = "".join(f"<i class='{'hit' if off and s == v.get('source') else 'on' if s in srcs else ''}'></i>"
                 for s in SRC_ORDER)
    names = " · ".join(SRC_NAME[s] for s in SRC_ORDER if s in srcs) or "No source"
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
    """Sort key: hard mismatches first, then by renter impact, a bigger gap first, then page order."""
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


def _headline(n: dict) -> tuple[str, str, bool]:
    hard = n["contradicted"]
    if hard == 1:
        return "1 thing", "doesn't match.", False
    if hard > 1:
        return f"{hard} things", "don't match.", False
    if n["close"]:
        return "Nothing is", "clearly off.", True  # only near misses: never sounds like a mismatch
    if n["confirmed"]:
        return "Everything we could", "check matches.", True
    if n["cant_check"]:
        return "Nothing here could", "be checked.", True
    return "No claims", "to check.", True


def _lead(off: list):
    """The finding the hero names: the top-ranked one, only when it is a hard mismatch (a close one never leads)."""
    return off[0] if off and off[0].get("verdict") == "contradicted" else None


def top_finding(run_dict: dict) -> dict:
    """The hero's headline and lead finding, for the drawer (done.top, docs/CONTRACT.md 7): the same counts, ranking
    and words the report uses, so the two never disagree.
    {"headline": "1 thing doesn't match.", "lines": ["1 thing", "doesn't match."], "calm": bool, "hard": int,
     "claim_id": id of the lead hard mismatch or None, "line": "Biggest difference: ..." or "",
     "order": claim ids of every hard mismatch, then every close one, in the report's order}"""
    run = Run(run_dict)
    n = _counts(run)
    off = _ranked_off(run, run.verdicts)
    top = _lead(off)
    l1, l2, calm = _headline(n)
    return {"headline": f"{l1} {l2}", "lines": [l1, l2], "calm": calm, "hard": n["contradicted"],
            "claim_id": top.get("claim_id") if top else None, "line": _top_line(run, top) if top else "",
            "order": [v.get("claim_id") for v in off]}


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


def _compare(run_dict: dict) -> str:
    """'Listing vs record' (server.claim_field.compare_table): one table, every claim, right after the hero."""
    try:
        from server.claim_field import compare_table
        return compare_table(run_dict)
    except Exception:  # noqa: BLE001  the table must never break the report
        return ""


def _hero(run: Run, n: dict, first_find: str | None, live_3d: bool = True, top=None) -> str:
    l1, l2, calm = _headline(n)
    address = run.snap.get("address_text") or run.match.get("address") or "Unknown address"
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    where = _e(parts[0]) + ("<br>" + _e(", ".join(parts[1:])) if len(parts) > 1 else "")
    used = [SRC_NAME[s] for s, evs in _source_used(run).items() if evs]
    total = len(run.verdicts)
    lead_line = _top_line(run, top) if top else ""
    near = ""
    if n["close"]:
        near = (_plural(n["close"], "detail is", "details are") + " close, not exact."
                if not n["contradicted"] else
                _plural(n["close"], "more is", "more are") + " close, not exact.")
    if calm and n["close"] and not n["contradicted"]:
        sub = near + (f" {_plural(n['confirmed'], 'claim matches', 'claims match')}." if n["confirmed"] else "")
        near = ""
    elif calm and n["confirmed"]:
        sub = f"{_plural(n['confirmed'], 'claim matches', 'claims match')}."
        if n["cant_check"]:
            sub += f" {n['cant_check']} we couldn't check."
    elif total:
        srcs = (", ".join(used[:-1]) + " and " + used[-1]) if len(used) > 1 else (used[0] if used else "")
        sub = f"{_plural(total, 'claim', 'claims')}" + (f", held up to {srcs}." if srcs else ", but no source had data.")
    else:
        sub = "The listing made no claims we could read."
    btn = ""
    if first_find:
        btn = f"<a class='btn big' href='#{first_find}'>Show me what's off{ARROW_DOWN}</a>"
    elif n["close"]:
        btn = f"<a class='btn big' href='#near'>See what's close{ARROW_DOWN}</a>"
    elif n["confirmed"]:
        btn = f"<a class='btn big' href='#match'>See what we checked{ARROW_DOWN}</a>"
    elif _fill_in(run):
        btn = f"<a class='btn big' href='#extra'>See what the records know{ARROW_DOWN}</a>"
    meta = _meta(run)
    tick = TICK if calm and n["confirmed"] and not n["close"] else ""
    lines = (f"<p class=top-line>{_e(lead_line)}</p>" if lead_line else "") + f"<p>{_e(sub)}"
    lines += (f" {_e(near)}" if near else "") + "</p>"
    return ("<section class=hero aria-label='Verdict'><div class=hero-top>"
            f"<h1><span>{_e(l1)}</span><span class=l2>{_e(l2)}{tick}</span></h1>"
            f"<div class='addr rv' style='--d:150ms'><p class=where>{where}</p>{lines}"
            + (f"<p class=meta>{_e(meta)}</p>" if meta else "") + btn + "</div></div>"
            f"<div class=field-wrap>{_field(run, live_3d)}</div></section>")


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


def _finding_group(run: Run, label, vs: list, idx: int, used_walk: list, live_3d: bool = True) -> str:
    lead = idx == 0
    v0 = vs[0]
    walk = None
    for v in vs:
        walk = _walk_hrefs(run, v)
        if walk:
            break
    imgs = run.images_for(vs, label)
    measured = _measured(run, v0)
    lines = _line_cards(run, v0)

    # proof slot, picked from what this item actually has
    if walk:
        used_walk.append(f"#f{idx + 1}")
        view = next(((p, c) for p, c in imgs if "floor plan" not in c.lower()), None)
        if view:
            imgs = [view] + [x for x in imgs if x != view]
        bg = f"<img src='{_e(view[0])}' alt='' loading=lazy>" if view else (
            "<svg class=room viewBox='0 0 160 80' preserveAspectRatio='none' aria-hidden=true>"
            "<path d='M0 0h160v80H0z' fill='#e4e4e1'/><path d='M0 0l55 22h50L160 0z' fill='#f5f5f3'/>"
            "<path d='M0 80l55-22h50l55 22z' fill='#d6d5d1'/><path d='M55 22h50v36H55z' fill='#efefed'/></svg>")
        scan_name = _scan_title(run, label) if label else ""
        small = (f"{scan_name} · " if scan_name else "") + "Opening at the spot that shows this…"
        proof = _walk_box(walk[1], walk[2], bg, small, live_3d)
        rest = imgs[1:] if view else imgs
        if rest:
            proof += "<div class=thumbs>" + "".join(_fig(p, c) for p, c in rest) + "</div>"
        kind_word = "walk"
    elif imgs:
        proof = _pics(imgs)
        kind_word = "pictures"
    elif measured:
        proof = measured
        kind_word = "measured"
    elif lines:
        proof = lines
        kind_word = "record"
    else:
        txt = _truth_text(v0).rstrip(".")
        proof = f"<div class=nopic aria-hidden=true>{_e(txt[:40])}</div>"
        kind_word = "none"
    if walk and measured:
        proof += measured  # a 3D walk and a measured drawing can both be shown
    elif imgs and lines:
        proof += lines

    def pair(v, big: bool) -> str:
        c = run.claim(v)
        said, is_q = _strike_text(run, v, c)
        soft = v.get("verdict") == "close" and _kind(v, c) in SOFT_KINDS
        said_html = f"“{_e(said)}”" if is_q else _e(_cap(said))
        truth = _truth_text(v)
        if v.get("verdict") == "close" and not soft and truth:
            truth = "≈ " + truth
        size = _size(max(said, truth, key=len), lead and big)
        strike = "" if soft else " strike"
        return (f"<h2 class='pair {size}'><span class=said><span class='{strike.strip()}'>{said_html}</span></span>"
                f"<span class=truth>{_e(truth)}</span></h2>")

    def line(v) -> str:
        c = run.claim(v)
        why = _first_sentence(v.get("reason") or "")
        soft = v.get("verdict") == "close" and _kind(v, c) in SOFT_KINDS
        return (f"<div class=line>{f'<p class=why>{_e(why)}</p>' if why else ''}"
                f"{_marks(run, v, 'Not seen in photos' if soft else '')}</div>")

    plan = ""
    if label:
        st = _scan_title(run, label)
        plan = f"<p class=plan>{_e(label)}" + (f" · scan titled “{_e(st)}”" if st and not walk else "") + "</p>"
    subs = ""
    if len(vs) > 1:
        subs = "<div class=subs>" + "".join(
            f"<div class='sub rv' style='--d:{i * 90}ms'>{pair(v, False)}{line(v)}</div>" for i, v in enumerate(vs[1:])) + "</div>"
    rooms = _rooms(run, label) if label else ""
    rooms = rooms.replace("<details class=rooms>", "<details class=rooms>")
    anchor = f"f{idx + 1}"
    # the drawer links a finding as #claim-<id>; one empty target per claim in this group
    marks = "".join(f"<span class=cl-a id='claim-{_e(v.get('claim_id'))}'></span>" for v in vs if v.get("claim_id"))
    text = f"{marks}{plan}{pair(v0, True)}{line(v0)}"
    alt = _truth_text(v0).rstrip(".")[:40]  # the text the proof slot shows if its pictures fail to load
    if lead:
        return (f"<article class='find lead rv' id='{anchor}' data-proof='{kind_word}'>{text}"
                f"<figure class=proof data-alt='{_e(alt)}'>{proof}</figure>{subs}{rooms}</article>")
    flip = " flip" if idx % 2 == 0 else ""
    return (f"<article class='find rv' id='{anchor}' data-proof='{kind_word}'><div class='two{flip}'>"
            f"<figure class=proof data-alt='{_e(alt)}'>{proof}</figure><div class=text>{text}</div></div>{subs}{rooms}</article>")


def _bare(kind: str, s) -> str:
    """'built in 2015' -> '2015'; '815 sq ft (scan title)' -> '815 sq ft'."""
    s = re.sub(r"\s*\((?:scan title|[^)]*record[^)]*)\)\s*$", "", str(s or "")).strip()
    if kind == "year_built":
        s = re.sub(r"(?i)^built\s+(?:in\s+)?", "", s)
    return s


def _near_row(run: Run, v: dict) -> str:
    """One quiet line for a 'close' verdict: no strike-through, no big type."""
    c = run.claim(v)
    kind = _kind(v, c)
    said, is_q = _strike_text(run, v, c)
    name = KIND_LABEL.get(kind) if kind in KIND_LABEL else (f"“{said}”" if is_q else _cap(said))
    if c.get("label"):
        name = f"{c['label']} · {name}"
    if kind in SOFT_KINDS:
        why = _first_sentence(v.get("reason") or "") or "Not visible in the photos"
        body = f"{_e(why.rstrip('.'))} <b>— a photo can't show it's missing</b>"
    else:
        claimed = _bare(kind, v.get("claimed") or c.get("quote"))
        found = _bare(kind, v.get("found"))
        who = SRC_SAYS.get(v.get("source"), "our sources say")
        body = f"Listing says {_e(claimed)} · {_e(who)} {_e(found)} <b>— close</b>"
    imgs = run.images_for([v], c.get("label"))[:1] if v.get("image") else []
    pic = _fig(*imgs[0]) if imgs else ""
    anchor = f"<span class=cl-a id='claim-{_e(v.get('claim_id'))}'></span>" if v.get("claim_id") else ""
    return f"<li>{anchor}<span class=nk>{_e(name)}</span><span class=nv>{body}</span>{pic}</li>"


def _findings(run: Run, verdicts: list, used_walk: list, live_3d: bool = True) -> str:
    """Hard mismatches as big modules, ranked by renter impact; then the 'close' ones as one quiet list."""
    if not verdicts:
        return "<span id=mismatch></span>"
    hard = [v for v in verdicts if v.get("verdict") == "contradicted"]
    near = sorted((v for v in verdicts if v.get("verdict") != "contradicted"), key=lambda v: _rank(run, v))
    groups = _groups(run, hard)
    for _, vs in groups:
        vs.sort(key=lambda v: _rank(run, v))
    groups.sort(key=lambda g: _rank(run, g[1][0]))
    h = [_finding_group(run, label, vs, i, used_walk, live_3d) for i, (label, vs) in enumerate(groups)]
    if near:
        rows = "".join(_near_row(run, v) for v in near)
        h.append(f"<div class='near rv' id=near><h3>Close, not exact</h3><ul>{rows}</ul></div>")
    return f"<section class='wrap findings' id=mismatch aria-label='What does not match'>{''.join(h)}</section>"


def _ranked_off(run: Run, verdicts: list) -> list:
    return sorted((v for v in verdicts if v.get("verdict") in ("contradicted", "close")), key=lambda v: _rank(run, v))


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


def _sources(run: Run, facts: list) -> str:
    used = _source_used(run)
    settled = {s: 0 for s in SRC_ORDER}
    weighed = {s: 0 for s in SRC_ORDER}
    for v in run.verdicts:
        rel = _relevant(run, v)
        for s in rel:
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
            f"<div class=src-grid>{''.join(cells)}</div></div></section>")


_PLAN_RE = re.compile(r"(\d+|studio)\s*(?:bed|br\b)", re.I)
_SQFT_RE = re.compile(r"([\d,]{3,})(?:\s*[–-]\s*[\d,]+)?\s*sq\s*ft", re.I)


def _plan_fact(f: dict):
    if f.get("topic") != "Homes & floor plans":
        return None
    val = str(f.get("value") or "")
    m, s = _PLAN_RE.search(val), _SQFT_RE.search(val)
    if not m or not s:
        return None
    beds = 0 if m.group(1).lower() == "studio" else int(m.group(1))
    sq = _num(s.group(1))
    if not sq or sq < 100 or sq > 10000 or beds > 8:
        return None
    return beds, sq


def _plan_svg(beds: int, sq: float, biggest: float, this: bool) -> str:
    side = 90 + 170 * math.sqrt(sq / biggest)
    w, h = side, side * 1.08
    col = "#1a4be0" if this else "#111"
    sw = 7
    p = [f"<rect x='{sw / 2}' y='{sw / 2}' width='{w - sw:.1f}' height='{h - sw:.1f}' fill='#f4f4f4' stroke='{col}' stroke-width='{sw}'/>"]
    split = h * .52
    if beds == 0:
        p.append(f"<path d='M{sw / 2} {split:.1f}h{w * .45:.1f}V{sw / 2}' fill='none' stroke='{col}' stroke-width='{sw * .6}'/>")
    else:
        p.append(f"<path d='M{sw / 2} {split:.1f}H{w - sw / 2:.1f}' stroke='{col}' stroke-width='{sw * .7}'/>")
        for i in range(1, beds):
            x = w * i / beds
            p.append(f"<path d='M{x:.1f} {sw / 2}V{split:.1f}' stroke='{col}' stroke-width='{sw * .7}'/>")
        if this:
            p.append(f"<rect x='{sw}' y='{sw}' width='{w / beds - sw * 1.5:.1f}' height='{split - sw * 1.5:.1f}' fill='#e2e8fb'/>")
    return f"<svg width='{w:.0f}' height='{h:.0f}' viewBox='0 0 {w:.1f} {h:.1f}' aria-hidden=true>{''.join(p)}</svg>"


def _fact_row(f: dict, more: bool) -> str:
    src = f.get("source")
    img = f.get("image")
    pic = _fig(img, str(f.get("label") or "")) if isinstance(img, str) and IMG_RE.match(img) else ""
    return (f"<div class='fact{' more' if more else ''}'><span class=k>{_e(f['label'])}</span>"
            f"<span class=v>{_e(f['value'])}</span><span class=fs title='{_e(FACT_FROM.get(src, ''))}'>"
            f"{_e(SRC_SHORT.get(src, ''))}</span>{pic}</div>")


def _extra(run: Run, facts: list) -> str:
    if not facts:
        return ("<section class='wrap extra' id=extra aria-label='Not on the listing'>"
                "<h2 class='h2 rv'><span>Not on the listing.</span></h2>"
                "<p class=empty>Our sources have nothing to add beyond the listing.</p></section>")
    srcs = [SRC_NAME[s] for s in SRC_ORDER if any(f.get("source") == s for f in facts)]
    l1 = f"What {srcs[0]} knows" if len(srcs) == 1 else "What the records know"
    topics: dict = {}
    for f in facts:
        topics.setdefault(str(f.get("topic") or "Other"), []).append(f)
    order = sorted(topics, key=lambda t: TOPICS.index(t) if t in TOPICS else len(TOPICS))
    # floor plans drawn to scale
    labels = [c.get("label") for c in run.claims.values() if c.get("label")]
    plans = [(f, _plan_fact(f)) for f in facts]
    plans = [(f, p) for f, p in plans if p][:10]
    drawn = {id(f) for f, _ in plans}
    plan_html = ""
    if len(plans) >= 2:
        big = max(p[1] for _, p in plans)
        items = []
        psrc = list(dict.fromkeys(SRC_NAME.get(f.get("source"), "") for f, _ in plans))
        for i, (f, (beds, sq)) in enumerate(plans):
            this = any(_same_plan(f.get("label"), lb) for lb in labels)
            items.append(f"<div class='fp rv{' this' if this else ''}' style='--d:{i * 70}ms'>{_plan_svg(beds, sq, big, this)}"
                         f"<span class=nm>{_e(f['label'])}{', on the listing' if this else ''}</span>"
                         f"<span class=vl>{_e(f['value'])}</span></div>")
        plan_html = (f"<div class=plans><h3>Homes &amp; floor plans <span>· {_e(', '.join(p for p in psrc if p))}</span></h3>"
                     + "".join(items) + "</div>")
    else:
        drawn = set()
    many = len(facts) > FACT_MANY
    blocks = []
    hidden = 0
    for t in order:
        rows = [f for f in topics[t] if id(f) not in drawn]
        if not rows:
            continue
        h = [f"<div class='topic rv'><h3>{_e(t)}</h3>"]
        for j, f in enumerate(rows):
            more = many and j >= FACT_SHOW
            hidden += more
            h.append(_fact_row(f, more))
        notes = list(dict.fromkeys(str(f.get("note") or "").strip() for f in rows if str(f.get("note") or "").strip()))
        if notes:
            h.append(f"<p class=note>{_e(' '.join(notes[:2]))}</p>")
        h.append("</div>")
        blocks.append("".join(h))
    btn = ""
    if hidden:
        btn = (f"<button type=button class='btn ghost morebtn' aria-controls=extra-more aria-expanded=false "
               f"data-more='Show all {len(facts)} facts' data-less='Show fewer'>Show all {len(facts)} facts</button>")
    present = [SRC_LONG[s] for s in SRC_ORDER if any(f.get("source") == s for f in facts)]
    foot = f"<div class=factfoot><span>Sources: {_e(', '.join(present))}.</span>{btn}</div>"
    count = f"{_plural(len(facts), 'fact', 'facts')}, in {_plural(len(order), 'topic', 'topics')}, each marked with its source. Not a fact-check."
    return ("<section class='wrap extra' id=extra aria-label='Not on the listing'>"
            f"<h2 class='h2 rv'><span>{_e(l1)}</span><span class=l2>the listing left out.</span></h2>"
            f"<p class='lede rv'>{_e(count)}</p>{plan_html}"
            f"<div class=more-wrap id=extra-more><div class=topics>{''.join(blocks)}</div></div>{foot}</section>")


def _matches(run: Run, verdicts: list, used_walk: list, live_3d: bool = True) -> str:
    if not verdicts:
        return "<span id=match></span>"
    items = []
    multi = 0
    for label, vs in _groups(run, verdicts):
        for v in vs:
            c = run.claim(v)
            said, is_q = _strike_text(run, v, c)
            if len(_relevant(run, v)) >= 2:
                multi += 1
            walk = _walk_hrefs(run, v)
            link = (f"<button type=button class=see3d data-src='{_e(walk[1])}' data-claim='{_e(walk[2])}'>"
                    "See it in 3D</button>" if walk and live_3d else "")
            text = f"“{_e(said)}”" if is_q else _e(_cap(said))
            small = f"<small>{_e(label)}</small>" if label else ""
            imgs = run.images_for([v], c.get("label"))[:1]  # a small proof picture where the evidence has one
            pic = _fig(*imgs[0]) if imgs else ""
            items.append(f"{pic}<span class=mt>{small}{text}{link}</span>")
    n = len(items)
    lis = "".join(f"<li class='{'more' if i >= LIST_SHOW else ''}'>{x}</li>" for i, x in enumerate(items))
    btn = ""
    if n > LIST_SHOW:
        btn = (f"<button type=button class='btn ghost morebtn toggle' aria-controls=match-more aria-expanded=false "
               f"data-more='Show the other {n - LIST_SHOW}' data-less='Show fewer'>Show the other {n - LIST_SHOW}</button>")
    note = f"{multi} by two sources or more." if multi else ""
    return ("<section class='wrap matches' id=match aria-label='What matches'><div class=split>"
            f"<div class=rv><h2 class=h3>{_e(_plural(n, 'thing matches.', 'things match.'))}</h2>"
            + (f"<p class=note>{_e(note)}</p>" if note else "") + "</div>"
            f"<div class='more-wrap rv' id=match-more><ul class=mlist>{lis}</ul>{btn}</div></div></section>")


def _cant(run: Run, verdicts: list) -> str:
    if not verdicts:
        return "<span id=cant-verify></span>"
    by_reason: dict = {}
    for label, vs in _groups(run, verdicts):
        for v in vs:
            c = dict(run.claim(v))
            c["_claimed"] = _claimed_words(v, c)
            said, is_q = _strike_text(run, v, c)
            txt = (f"“{said}”" if is_q else _cap(said))
            if label:
                txt = f"{label}: {txt}"
            by_reason.setdefault(_cant_reason(run, v, c), []).append(txt)
    n = len(verdicts)
    blocks, shown = [], 0
    for reason, texts in by_reason.items():
        texts = list(dict.fromkeys(texts))
        vis, rest = [], []
        for t in texts:
            (vis if shown < LIST_SHOW else rest).append(t)
            shown += 1
        body = _e(" · ".join(vis))
        if rest:
            body += f"<span class=more> · {_e(' · '.join(rest))}</span>"
        cls = "" if vis else " class=more"
        blocks.append(f"<div{cls}><p>{_e(reason)}</p><div class=qs>{body}</div></div>")
    btn = ""
    if shown > LIST_SHOW:
        btn = (f"<button type=button class='btn ghost morebtn toggle' aria-controls=cant-more aria-expanded=false "
               f"data-more='Show the other {shown - LIST_SHOW}' data-less='Show fewer'>Show the other {shown - LIST_SHOW}</button>")
    word = "nobody could check." if n != 1 else "nobody could check."
    return ("<section class=cant id=cant-verify aria-label=\"Couldn't check\"><div class='wrap split'>"
            f"<h2 class='h3 rv'>{n} {word}</h2>"
            f"<div class='more-wrap rv' id=cant-more><div class=reasons>{''.join(blocks)}</div>{btn}</div></div></section>")


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
    return f"Can you confirm {said}{plan} in writing?"


def _questions(run: Run, off: list, cant: list, pic) -> str:
    qs = []
    for v in off + cant:
        q = _question(run, v)
        if q not in qs:
            qs.append(q)
        if len(qs) >= MAX_QUESTIONS:
            break
    if not qs:
        return ""
    lis = "".join(f"<li class=rv style='--d:{i * 80}ms'><span>{_e(q)}</span>"
                  f"<button type=button class='btn ghost copy' data-text='{_e(q)}'>Copy</button></li>" for i, q in enumerate(qs))
    every = "\n".join(qs)
    all_btn = (f"<p style='margin-top:28px'><button type=button class='btn copy' data-text='{_e(every)}'>"
               f"Copy all {len(qs)}</button></p>" if len(qs) > 1 else "")
    side = ""
    if pic:
        side = f"<figure class='proof rv'>{_pics([pic], caption=False)}<figcaption>{_e(_short_caption(pic[1]))}</figcaption></figure>"
    solo = "" if side else " solo"
    return (f"<section class='wrap ask{solo}' id=questions aria-label='Questions to ask'><div class=split><div>"
            "<h2 class='h3 rv'>Ask before you tour.</h2>"
            f"<ul class=qlist>{lis}</ul>{all_btn}</div>{side}</div></section>")


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
    off = _ranked_off(run, run.verdicts)  # hard mismatches first, by renter impact; then the close ones
    ok = [v for v in run.verdicts if v.get("verdict") == "confirmed"]
    cant = [v for v in run.verdicts if v.get("verdict") not in ("contradicted", "close", "confirmed")]
    facts = _fill_in(run)
    used_walk: list = []

    findings = _findings(run, off, used_walk, live_3d)  # claims images first, as the old report does
    lead_pic = None
    for m in re.finditer(r"<a class='pic[^']*' href='(/img/[a-f0-9]{16})' data-cap='([^']*)'", findings):
        if not m.group(2).lower().startswith("floor plan"):
            lead_pic = (m.group(1), m.group(2))
            break
    top = _lead(off)
    hero = _hero(run, n, "f1" if top else None, live_3d, top)
    url = run.snap.get("url") or ""
    nav = []
    if url.startswith(("http://", "https://")):
        nav.append(f"<a class=back href='{_e(url)}' target=_blank rel=noopener>{ARROW_LEFT}<span>Listing</span></a>")
    if used_walk:
        nav.append(f"<a class=btn href='{_e(used_walk[0])}'>3D walk</a>")  # the finding's own inline viewer
    else:
        nav.append("<button type=button class='btn share'>Share</button>")
    err = ""
    if not run.done and run.err:
        err = f"<p class='wrap empty'>{_e(run.err.get('message') or 'This check did not finish.')}</p>"

    h = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         f"<title>{BRAND}: {_e(address)}</title>",
         "<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>",
         "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap'>",
         PRECONNECT if used_walk and live_3d else "",
         f"<style>{CSS}</style></head><body id=top>",
         f"<div class=wrap><header class=top><a class=brand href='#top'>{BRAND}</a><nav aria-label=Report>{''.join(nav)}</nav></header>",
         hero, "</div>", _compare(run_dict), err, findings, _sources(run, facts), _extra(run, facts), _matches(run, ok, used_walk, live_3d),
         _cant(run, cant), _questions(run, off, cant, lead_pic), _footer(run),
         "<div class=lb id=lb hidden role=dialog aria-modal=true aria-label='Picture'>"
         "<button type=button class=btn>Close</button><img alt=''><p></p></div>",
         f"<script>{SCRIPT}</script></body></html>"]
    return "".join(h)
