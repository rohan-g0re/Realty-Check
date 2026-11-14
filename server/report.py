"""Full report page: one self-contained, minimalist HTML document built from a saved run.

Three tabs: Mismatch (contradicted + close), Match (confirmed), Can't verify (cant_check and anything unknown).
Every verdict lands in exactly one tab. Claims about one floor plan are grouped into one item; building-level
claims are their own items. Pictures lead each mismatch item and are never repeated across items.
"""
from __future__ import annotations

import re
import time
from html import escape

TABS = [("mismatch", "Mismatch"), ("match", "Match"), ("cant-verify", "Can't verify")]
TAB_OF = {"contradicted": "mismatch", "close": "mismatch", "confirmed": "match"}  # anything else: cant-verify
DOT_OF = {"contradicted": "red", "close": "amber", "confirmed": "green"}
AGENTS = {"orchestrator": "Lead", "claims": "Claims", "matcher": "Match", "inside": "Inside",
          "outside": "Outside", "paper": "Records", "judge": "Judge"}
OTHER_WORDS = {"county": "County records", "record": "Building record", "scan": "3D scan",
               "aerial": "Roof height measurement"}
KIND_FMT = {"stories": ("floor", "floors"), "bedrooms": ("bedroom", "bedrooms"),
            "bathrooms": ("bathroom", "bathrooms"), "unit_count": ("apartment", "apartments")}
IMG_RE = re.compile(r"^/img/[a-f0-9]{16}$")
GENERIC_CANT = ("none of our sources", "no data", "not checked")

CSS = """
:root{--ink:#16181a;--muted:#6b6f73;--line:#e6e6e3;--bg:#fff;--soft:#fafaf8;--accent:#2f5d8a;
--red:#c0392b;--amber:#c98a12;--green:#2e8b57;--grey:#9a9ea3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:880px;margin:0 auto;padding:56px 24px 96px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.eyebrow{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
.page,.meta{font-size:14px;color:var(--muted);margin:0;overflow-wrap:anywhere}
.headline{font-size:26px;line-height:1.35;font-weight:500;margin:36px 0 32px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:1px;background:var(--grey);flex:none}
.dot.red{background:var(--red)}.dot.amber{background:var(--amber)}.dot.green{background:var(--green)}
.tabs{display:flex;gap:28px;border-bottom:1px solid var(--line);margin:0 0 36px;overflow-x:auto}
.tabs a{color:var(--muted);padding:0 0 12px;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap;font-size:15px}
.tabs a:hover{color:var(--ink);text-decoration:none}
.tabs a.on{color:var(--ink);border-bottom-color:var(--ink);font-weight:500}
.panel>h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:48px 0 16px}
.js .panel>h2{display:none}.js .panel[hidden]{display:none}
.empty{color:var(--muted);margin:0}
.item{border-top:1px solid var(--line);padding:32px 0 36px}.item:first-of-type{border-top:0;padding-top:0}
.item>h3{font-size:20px;font-weight:600;margin:0 0 2px}
.quiet{font-size:13px;color:var(--muted);margin:0}
.item>.quiet{margin-bottom:20px}
.pics{display:grid;grid-template-columns:3fr 2fr;gap:8px;margin:0 0 28px}
.pics.one{grid-template-columns:1fr}
.pics figure{margin:0}.pics a{display:block}
.pics img{display:block;width:100%;border:1px solid var(--line);border-radius:4px;background:var(--soft)}
.pics .lead img{height:420px;object-fit:contain}
.pics.one .lead img{height:auto;max-height:480px}
.thumbs{display:grid;grid-template-columns:1fr 1fr;gap:8px;align-content:start}
.thumbs.col1{grid-template-columns:1fr}
.thumbs img{aspect-ratio:1;object-fit:cover}.tcap{grid-column:1/-1;margin:0}.thumbs.col1 img{aspect-ratio:4/3}
figcaption,.tcap{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.35}
.finding{margin:0 0 28px}.finding:last-child{margin-bottom:0}
.finding+.finding{border-top:1px solid var(--line);padding-top:24px}
.vs{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:0 0 16px}
.vs dt{font-size:13px;color:var(--muted)}
.vs dd{margin:2px 0 0;font-size:26px;line-height:1.25;font-weight:500}
.why{margin:0 0 6px}
.viz{margin:0 0 18px}
.bars{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;align-items:center;font-size:13px;color:var(--muted);max-width:560px}
.bar{height:12px;border-radius:2px;background:var(--line)}.bar.found{background:var(--ink)}
.bars .pct{grid-column:2;font-size:13px;color:var(--ink)}
.floors{display:flex;gap:36px;align-items:flex-end}
.floors figure{margin:0;text-align:center;font-size:13px;color:var(--muted)}
.floors svg{display:block;margin:0 auto 6px}
.beds{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;align-items:center;font-size:13px;color:var(--muted)}
.beds .icons{display:flex;gap:8px;align-items:center;color:var(--ink);min-height:24px}
.den{display:inline-block;border:1px dashed var(--grey);border-radius:3px;padding:0 6px;font-size:12px;color:var(--muted);line-height:20px}
.rows{margin:0 0 4px}
.rowh{font-size:15px;font-weight:600;margin:28px 0 4px}.rowh:first-child{margin-top:0}
.row{display:grid;grid-template-columns:16px 1.2fr 1.2fr 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);align-items:baseline;font-size:15px}
.row .dot{margin:0}
.row .from{font-size:13px;color:var(--muted)}
.rowlabels{font-size:13px;color:var(--muted);padding-top:0}
.row .found{color:var(--ink)}
.row .found small{display:block;color:var(--muted);font-size:13px}
.row img{grid-column:2/-1;width:180px;max-width:100%;border:1px solid var(--line);border-radius:4px;margin-top:6px}
.cv{list-style:none;margin:0;padding:0}
.cv li{display:flex;gap:10px;align-items:baseline;padding:9px 0;border-bottom:1px solid var(--line);font-size:15px}
.cv .dot{margin:0}
.cv .what{flex:1;min-width:0}.cv .what b{font-weight:600;margin-right:6px}
.cv .reason{font-size:13px;color:var(--muted);text-align:right;flex:0 1 40%}
details{margin-top:12px}
details.how{margin-top:56px;border-top:1px solid var(--line);padding-top:16px}
summary{cursor:pointer;font-size:15px;font-weight:500}
details.rooms summary{font-size:13px;color:var(--muted);font-weight:400}
details.rooms ul{margin:8px 0 0;padding:0;list-style:none;font-size:14px;max-width:360px}
details.rooms li{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:4px 0}
ol{padding-left:20px;color:var(--muted);font-size:14px}ol li{margin:6px 0}
ol .ag{display:inline-block;min-width:64px;color:var(--ink)}
@media(max-width:560px){main{padding:36px 16px 72px}.headline{font-size:22px}
.tabs{gap:18px}.tabs a{font-size:14px}
.pics{grid-template-columns:1fr}.pics .lead img{height:300px}
.thumbs,.thumbs.col1{grid-template-columns:repeat(3,1fr)}.thumbs img,.thumbs.col1 img{aspect-ratio:1}
.vs{gap:12px}.vs dd{font-size:21px}
.row{grid-template-columns:16px 1fr 1fr}.row .from{grid-column:2/-1;margin-top:-6px}.rowlabels span:last-child{display:none}
.cv li{flex-wrap:wrap}.cv .reason{flex-basis:100%;text-align:left;padding-left:18px}}
"""

SCRIPT = """
(function(){var d=document.documentElement;d.className+=' js';
var ids=['mismatch','match','cant-verify'];
function show(id){if(ids.indexOf(id)<0)id='mismatch';
ids.forEach(function(k){var p=document.getElementById(k),t=document.getElementById('tab-'+k);
if(p)p.hidden=k!==id;if(t)t.className=k===id?'on':'';});}
document.querySelectorAll('.tabs a').forEach(function(a){a.addEventListener('click',function(e){
e.preventDefault();var id=a.getAttribute('href').slice(1);show(id);
if(history.replaceState)history.replaceState(null,'','#'+id);});});
window.addEventListener('hashchange',function(){show(location.hash.slice(1));});
show(location.hash.slice(1));window.scrollTo(0,0);})();
"""

BED_SVG = ("<svg width='30' height='20' viewBox='0 0 30 20' aria-hidden='true'><path d='M2 4v14M28 11v7M2 14h26M2 11h26'"
           " stroke='currentColor' stroke-width='2' fill='none' stroke-linecap='round'/>"
           "<rect x='5' y='6' width='7' height='5' rx='1.5' fill='currentColor'/></svg>")


# ---------- small helpers ----------

def _num(s) -> float | None:
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _fmt_n(n: float) -> str:
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.1f}"


def _norm(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _same_plan(a, b) -> bool:
    """'Plan E' vs 'Plan E 1 Bedroom + Den': equal or one starts the other at a word boundary."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def _first_sentence(text: str) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(text or "").strip()) if p.strip()]
    if len(parts) > 1 and parts[0].lower().startswith("listing says"):
        parts = parts[1:]
    return parts[0] if parts else ""


def _value_words(kind: str, value) -> str:
    n = _num(value)
    if n is None or isinstance(value, str) and not re.fullmatch(r"[\d,.\s]+", value):
        return str(value)
    if kind == "year_built":
        return f"built {int(n)}"
    if kind == "area_sqft":
        return f"{_fmt_n(n)} sq ft"
    if kind in KIND_FMT:
        one, many = KIND_FMT[kind]
        return f"{_fmt_n(n)} {one if n == 1 else many}"
    return _fmt_n(n)


def _claimed_words(v: dict, c: dict) -> str:
    claimed = str(v.get("claimed") or c.get("quote") or "")
    if c.get("kind") == "bedrooms" and _num(c.get("value")) == 0 and re.match(r"^\s*0\s+bed", claimed):
        return "Studio"
    return claimed


def _kind(v: dict, c: dict) -> str:
    k = c.get("kind") or ""
    if k:
        return k
    text = f"{v.get('claimed', '')} {v.get('found', '')}".lower()
    if "sq ft" in text:
        return "area_sqft"
    if "floor" in text or "stor" in text:
        return "stories"
    if "bedroom" in text or "studio" in text:
        return "bedrooms"
    return "other"


def _short_caption(cap: str) -> str:
    c = str(cap or "").strip()
    low = c.lower()
    if low.startswith("schematic floor plan") or low.startswith("floor plan"):
        return "Floor plan from the 3D scan"
    if low.startswith("panorama"):
        m = re.search(r"\(([^)]*)\)", c)
        room = m.group(1).strip() if m else ""
        if room and "not labelled" not in room.lower() and "not labeled" not in room.lower():
            return f"{room[:1].upper()}{room[1:]} in the 3D scan"
        return "Room view from the 3D scan"
    return c.split(":")[0].strip()


def _img_rank(cap: str) -> int:
    low = (cap or "").lower()
    if "floor plan" in low:
        return 0
    if "top-down" in low or "top down" in low:
        return 1
    return 2


# ---------- run model ----------

class Run:
    def __init__(self, run: dict):
        self.raw = run
        self.events = run.get("events") or []
        ev = self.events
        self.snap = run.get("snapshot") or {}
        self.done = next((e for e in ev if e.get("type") == "done"), None) or {}
        self.err = next((e for e in ev if e.get("type") == "error"), None)
        self.match = (next((e for e in ev if e.get("type") == "match"), None) or {}).get("match") or {}
        self.claims: dict = {}
        self.order: list = []
        for e in ev:
            if e.get("type") == "claims":
                for c in e.get("claims") or []:
                    if isinstance(c, dict) and c.get("id") not in self.claims:
                        self.claims[c.get("id")] = c
                        self.order.append(c.get("id"))
        self.verdicts = [e["verdict"] for e in ev if e.get("type") == "verdict" and isinstance(e.get("verdict"), dict)]
        self.evidence = [e["evidence"] for e in ev if e.get("type") == "evidence" and isinstance(e.get("evidence"), dict)]
        self.captions: dict = {}
        for evd in self.evidence:
            for im in evd.get("images") or []:
                if isinstance(im, dict) and im.get("path") and im["path"] not in self.captions:
                    self.captions[im["path"]] = im.get("caption") or ""
        labels = {c.get("label") for c in self.claims.values() if c.get("label")}
        self.only_label = next(iter(labels)) if len(labels) == 1 else None
        self.used_imgs: set = set()

    def claim(self, v: dict) -> dict:
        return self.claims.get(v.get("claim_id")) or {}

    def plan_of(self, evd: dict):
        obs = evd.get("observed") or {}
        plan = obs.get("plan") or obs.get("floorplan") or obs.get("floor_plan")
        if plan:
            return plan
        if evd.get("source") == "scan" and self.only_label:
            return self.only_label  # untagged scan evidence in a single-plan run
        return None

    def plan_evidence(self, label) -> list:
        return [e for e in self.evidence if e.get("status", "ok") == "ok" and _same_plan(self.plan_of(e), label)]

    def has_scan(self, label) -> bool:
        return any(e.get("source") == "scan" for e in self.plan_evidence(label))

    def images_for(self, verdicts: list, label) -> list:
        """(path, caption) for every proof image of these verdicts, unused so far, floor plan first."""
        paths = []
        vimgs = [v.get("image") for v in verdicts if v.get("image")]
        paths += vimgs
        if label:
            for e in self.plan_evidence(label):
                paths += [im.get("path") for im in e.get("images") or [] if isinstance(im, dict)]
        else:
            kinds = {_kind(v, self.claim(v)) for v in verdicts}
            srcs = {v.get("source") for v in verdicts if v.get("source")}
            cids = {v.get("claim_id") for v in verdicts}
            for e in self.evidence:
                if self.plan_of(e) and self.plan_of(e) != self.only_label:
                    continue
                imgs = [im.get("path") for im in e.get("images") or [] if isinstance(im, dict)]
                obs = e.get("observed") or {}
                if (set(imgs) & set(vimgs)) or obs.get("claim_id") in cids \
                        or (e.get("source") in srcs and kinds & set(obs)):
                    paths += imgs
        out, seen = [], set()
        for p in paths:
            if not isinstance(p, str) or not IMG_RE.match(p) or p in seen or p in self.used_imgs:
                continue
            seen.add(p)
            out.append((p, self.captions.get(p, "")))
        out.sort(key=lambda pc: _img_rank(pc[1]))
        self.used_imgs.update(seen)
        return out[:7]

    def others(self, v: dict, c: dict) -> str:
        """'County records: 6 floors. Building record: 5 floors.' from evidence other than the winning source."""
        kind = _kind(v, c)
        bits, seen = [], set()
        for e in self.evidence:
            src = e.get("source")
            obs = e.get("observed") or {}
            if e.get("status", "ok") != "ok" or src == v.get("source") or src in seen or kind not in obs:
                continue
            if c.get("label") and not _same_plan(self.plan_of(e), c.get("label")):
                continue
            if not c.get("label") and self.plan_of(e) and self.plan_of(e) != self.only_label:
                continue
            word = OTHER_WORDS.get(src)
            if not word or obs.get(kind) in (None, ""):
                continue
            seen.add(src)
            bits.append(f"{word}: {_value_words(kind, obs[kind])}.")
        return " ".join(bits)


def _source_line(v: dict, kind: str, run: Run) -> str:
    src = v.get("source")
    if src == "scan":
        return "From the 3D scan"
    if src == "aerial":
        return "From a roof height measurement" if kind == "stories" else "From aerial photos"
    if src == "county":
        return "From county records"
    if src == "record":
        return "From the building record"
    return ""


def _said(claims: list, everyone: list) -> str:
    """What the listing says for `claims`: a page quote once when every claim using it is among `claims`."""
    mine = {c.get("id") for c in claims}
    out, used = [], set()
    for c in claims:
        q = c.get("quote")
        if q in used:
            continue
        mates = [x for x in everyone if x.get("quote") == q and _norm(x.get("label")) == _norm(c.get("label"))]
        if q and len(q) <= 80 and all(x.get("id") in mine for x in mates):
            used.add(q)
            out.append(q)
        else:
            out.append(c.get("_claimed") or q or "")
    text = ", ".join(x for x in out if x)
    return text[:1].upper() + text[1:]


# ---------- visuals ----------

def _viz_area(claimed: float, found: float) -> str:
    top = max(claimed, found) or 1
    diff = (found - claimed) / claimed * 100 if claimed else 0
    pct = f"{abs(diff):.0f}% {'smaller' if diff < 0 else 'larger'} than listed" if round(diff) else "about the same"
    return ("<div class='viz bars'>"
            f"<span>Listed</span><div class=bar style='width:{claimed / top * 100:.1f}%'></div>"
            f"<span>Measured</span><div class='bar found' style='width:{found / top * 100:.1f}%'></div>"
            f"<span class=pct>{_fmt_n(found)} sq ft measured vs {_fmt_n(claimed)} sq ft listed: {pct}</span></div>")


def _floors_svg(n: int, top: int, extra_from: int | None) -> str:
    fh, gap, w = 12, 3, 46
    h = top * (fh + gap)
    rects = []
    for i in range(n):
        y = h - (i + 1) * (fh + gap) + gap
        extra = extra_from is not None and i >= extra_from
        fill = "#c0392b" if extra else "#9a9ea3"
        op = " fill-opacity='.35'" if extra else ""
        rects.append(f"<rect x='2' y='{y}' width='{w}' height='{fh}' rx='1.5' fill='{fill}'{op}/>")
    return f"<svg width='{w + 4}' height='{h + 2}' viewBox='0 0 {w + 4} {h + 2}' aria-hidden='true'>{''.join(rects)}</svg>"


def _viz_floors(claimed: int, found: int) -> str:
    top = max(claimed, found)
    if top <= 0 or top > 80:
        return ""
    return ("<div class='viz floors'>"
            f"<figure>{_floors_svg(claimed, top, None)}Listed: {claimed}</figure>"
            f"<figure>{_floors_svg(found, top, claimed if found > claimed else None)}Found: {found}</figure></div>")


def _bed_icons(n: int, den: bool, studio_word: bool) -> str:
    if n <= 0:
        icons = "<span class=den>studio, no separate bedroom</span>" if studio_word else "0"
    else:
        icons = BED_SVG * min(n, 8)
    if den:
        icons += "<span class=den>den</span>"
    return f"<span class=icons>{icons}</span>"


def _viz_beds(claimed_n: int, found_n: int, found_text: str) -> str:
    if max(claimed_n, found_n) > 8:
        return ""
    den = "den" in found_text.lower()
    return ("<div class='viz beds'>"
            f"<span>Listed</span>{_bed_icons(claimed_n, False, True)}"
            f"<span>Found</span>{_bed_icons(found_n, den, True)}</div>")


def _visual(kind: str, v: dict) -> str:
    a, b = _num(v.get("claimed")), _num(v.get("found"))
    if kind == "bedrooms" and str(v.get("claimed") or "").lower().startswith("studio"):
        a = 0.0
    if a is None or b is None:
        return ""
    if kind == "area_sqft" and a > 0 and b > 0:
        return _viz_area(a, b)
    if kind == "stories" and a == int(a) and b == int(b):
        return _viz_floors(int(a), int(b))
    if kind == "bedrooms" and a == int(a) and b == int(b):
        return _viz_beds(int(a), int(b), str(v.get("found") or ""))
    return ""


def _pics(imgs: list) -> str:
    if not imgs:
        return ""

    def fig(p, cap, cls=""):
        short = _short_caption(cap)
        return (f"<figure class='{cls}'><a href='{escape(p, True)}' target=_blank rel=noopener>"
                f"<img src='{escape(p, True)}' alt='{escape(short or 'Proof picture', True)}' loading=lazy></a>"
                + (f"<figcaption>{escape(short)}</figcaption>" if short else "") + "</figure>")

    lead, rest = imgs[0], imgs[1:]
    if not rest:
        return f"<div class='pics one'>{fig(*lead, cls='lead')}</div>"
    thumbs = "".join(fig(p, "") for p, _ in rest)
    caps = list(dict.fromkeys(_short_caption(c) for _, c in rest if _short_caption(c)))
    if caps:
        thumbs += f"<p class=tcap>{escape(' · '.join(caps))}</p>"
    col = " col1" if len(rest) <= 2 else ""
    return f"<div class=pics>{fig(*lead, cls='lead')}<div class='thumbs{col}'>{thumbs}</div></div>"


# ---------- grouping ----------

def _groups(run: Run, verdicts: list) -> list:
    """[(label or None, [verdicts])] in claim order; plan claims grouped, building claims one each."""
    out: dict = {}
    for v in verdicts:
        c = run.claim(v)
        key = ("plan", _norm(c.get("label"))) if c.get("label") else ("one", v.get("claim_id"), id(v))
        out.setdefault(key, (c.get("label"), []))[1].append(v)
    pos = {cid: i for i, cid in enumerate(run.order)}

    def first(item):
        return min(pos.get(v.get("claim_id"), 10**6) for v in item[1])

    return sorted(out.values(), key=first)


def _headline_claim(run: Run, verdicts: list) -> str | None:
    head = _norm(run.done.get("headline"))
    best, score = None, 0
    for v in verdicts:
        s = (2 if _norm(v.get("found")) and _norm(v.get("found")) in head else 0) \
            + (1 if _norm(v.get("claimed")) and _norm(v.get("claimed")) in head else 0)
        if s > score:
            best, score = v.get("claim_id"), s
    return best


def _rooms(run: Run, label) -> str:
    for e in run.plan_evidence(label):
        obs = e.get("observed") or {}
        rooms = obs.get("rooms")
        items = []
        if isinstance(rooms, list):
            for r in rooms:
                if isinstance(r, dict):
                    name = r.get("label") or r.get("name") or "Room"
                    area = r.get("area_sqft")
                    if area is None and _num(r.get("area_m2")) is not None:
                        area = _num(r.get("area_m2")) * 10.7639
                    items.append((str(name), f"{_fmt_n(round(_num(area)))} sq ft" if _num(area) is not None else ""))
                elif isinstance(r, str):
                    items.append((r, ""))
        elif isinstance(obs.get("room_labels"), list):
            items = [(str(r), "") for r in obs["room_labels"] if r]
        if items:
            lis = "".join(f"<li><span>{escape(n)}</span><span>{escape(a)}</span></li>" for n, a in items[:40])
            return f"<details class=rooms><summary>Rooms in the 3D scan ({len(items)})</summary><ul>{lis}</ul></details>"
    return ""


def _scan_title(run: Run, label) -> str:
    for e in run.plan_evidence(label):
        name = (e.get("observed") or {}).get("model_name")
        if e.get("source") == "scan" and name:
            return str(name)
    return ""


# ---------- tab bodies ----------

def _finding(run: Run, v: dict) -> str:
    c = run.claim(v)
    kind = _kind(v, c)
    dot = DOT_OF.get(v.get("verdict"), "")
    h = ["<div class=finding>",
         "<dl class=vs>"
         f"<div><dt><i class='dot {dot}'></i>Listing says</dt><dd>{escape(_claimed_words(v, c))}</dd></div>"
         f"<div><dt>We found</dt><dd>{escape(str(v.get('found') or ''))}</dd></div></dl>"]
    h.append(_visual(kind, v))
    why = _first_sentence(v.get("reason") or "")
    if why:
        h.append(f"<p class=why>{escape(why)}</p>")
    src = _source_line(v, kind, run)
    others = run.others(v, c)
    quiet = " · ".join(x for x in (src, others) if x)
    if quiet:
        h.append(f"<p class=quiet>{escape(quiet)}</p>")
    h.append("</div>")
    return "".join(h)


def _mismatch(run: Run, verdicts: list) -> str:
    if not verdicts:
        return "<p class=empty>Nothing here.</p>"
    head = _headline_claim(run, verdicts)
    groups = _groups(run, verdicts)
    groups.sort(key=lambda g: 0 if any(v.get("claim_id") == head for v in g[1]) else 1)
    h = []
    for label, vs in groups:
        vs = sorted(vs, key=lambda v: 0 if v.get("claim_id") == head else 1)
        claims = [run.claim(v) for v in vs]
        if label:
            title = str(label)
            plan_claims = [c for c in run.claims.values() if _same_plan(c.get("label"), label)]
            quotes = list(dict.fromkeys(c.get("quote") for c in plan_claims if c.get("quote")))
            sub = f"On the listing: {', '.join('“' + q + '”' for q in quotes)}" if quotes else ""
            st = _scan_title(run, label)
            if st:
                sub += (". " if sub else "") + f"The 3D scan of this plan is titled “{st}”."
        else:
            c = claims[0]
            title = c.get("quote") or _claimed_words(vs[0], c)
            sub = ""
        h.append(f"<article class=item><h3>{escape(title)}</h3>")
        h.append(f"<p class=quiet>{escape(sub)}</p>" if sub else "<p class=quiet></p>")
        h.append(_pics(run.images_for(vs, label)))
        h.extend(_finding(run, v) for v in vs)
        if label:
            h.append(_rooms(run, label))
        h.append("</article>")
    return "".join(h)


def _match(run: Run, verdicts: list) -> str:
    if not verdicts:
        return "<p class=empty>Nothing here.</p>"
    h = []
    for label, vs in _groups(run, verdicts):
        if label:
            h.append(f"<p class=rowh>{escape(str(label))}</p>")
        for v in vs:
            c = dict(run.claim(v))
            c["_claimed"] = _claimed_words(v, c)
            kind = _kind(v, c)
            said = _said([c], list(run.claims.values()))
            imgs = run.images_for([v], c.get("label"))[:1] if v.get("image") else []
            img = f"<img src='{escape(imgs[0][0], True)}' alt='{escape(_short_caption(imgs[0][1]) or 'Proof picture', True)}' loading=lazy>" if imgs else ""
            found = str(v.get("found") or "")
            found = escape(found[:1].upper() + found[1:])
            others = run.others(v, c)
            if others:
                found += f"<small>{escape(others)}</small>"
            h.append(f"<div class=row><i class='dot {DOT_OF.get(v.get('verdict'), '')}'></i>"
                     f"<span>{escape(said or c['_claimed'])}</span>"
                     f"<span class=found>{found}</span>"
                     f"<span class=from>{escape(_source_line(v, kind, run))}</span>{img}</div>")
    head = ("<div class='row rowlabels'><span></span><span>Listing says</span><span>What we found</span>"
            "<span>Where from</span></div>")
    return "<div class=rows>" + head + "".join(h) + "</div>"


def _cant_reason(run: Run, v: dict, c: dict) -> str:
    label = c.get("label")
    if label and not run.has_scan(label):
        return "No scan for this floor plan"
    reason = str(v.get("reason") or "").strip()
    if not reason or any(g in reason.lower() for g in GENERIC_CANT):
        return "Nothing in our data measures this"
    return _first_sentence(reason)


def _cant(run: Run, verdicts: list) -> str:
    if not verdicts:
        return "<p class=empty>Nothing here.</p>"
    rows = []
    for label, vs in _groups(run, verdicts):
        # one line per floor plan and reason; one line per building claim
        by_reason: dict = {}
        for v in vs:
            c = dict(run.claim(v))
            c["_claimed"] = _claimed_words(v, c)
            by_reason.setdefault(_cant_reason(run, v, c), []).append(c)
        for reason, cs in by_reason.items():
            said = _said(cs, list(run.claims.values())) or ", ".join(c["_claimed"] for c in cs)
            lead = f"<b>{escape(str(label))}</b>" if label else ""
            rows.append(f"<li><i class=dot></i><span class=what>{lead}{escape(said)}</span>"
                        f"<span class=reason>{escape(reason)}</span></li>")
    return "<ul class=cv>" + "".join(rows) + "</ul>"


# ---------- page ----------

def _meta(run: Run) -> str:
    bits = []
    if run.match.get("status") == "matched" and run.match.get("name"):
        bits.append(f"Matched to {run.match['name']}")
    t0, t1 = run.raw.get("started_at"), run.raw.get("finished_at")
    if isinstance(t0, (int, float)):
        bits.append(time.strftime("%d %b %Y, %H:%M", time.localtime(t0)).lstrip("0"))
        if isinstance(t1, (int, float)) and t1 >= t0:
            bits.append(f"took {round(t1 - t0)} s")
    return " · ".join(bits)


def render_report(run_dict: dict) -> str:
    run = Run(run_dict)
    address = run.snap.get("address_text") or "Unknown address"
    url = run.snap.get("url") or ""
    title = run.snap.get("title") or url
    headline = run.done.get("headline") or (run.err and run.err.get("message")) or "This check did not finish."

    split = {k: [] for k, _ in TABS}
    for v in run.verdicts:
        split[TAB_OF.get(v.get("verdict"), "cant-verify")].append(v)

    h = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         f"<title>Reality Check: {escape(address)}</title><style>{CSS}</style></head><body><main>",
         "<p class=eyebrow>Reality Check</p>", f"<h1>{escape(address)}</h1>"]
    if url.startswith(("http://", "https://")):
        h.append(f"<p class=page><a href='{escape(url, True)}' target=_blank rel=noopener>{escape(title)}</a></p>")
    meta = _meta(run)
    if meta:
        h.append(f"<p class=meta>{escape(meta)}</p>")
    h.append(f"<p class=headline>{escape(headline)}</p>")

    h.append("<nav class=tabs>")
    dots = {"mismatch": "red", "match": "green", "cant-verify": ""}
    for i, (k, word) in enumerate(TABS):
        h.append(f"<a href='#{k}' id='tab-{k}' class='{'on' if i == 0 else ''}'>"
                 f"<i class='dot {dots[k]}'></i>{word} ({len(split[k])})</a>")
    h.append("</nav>")

    body = {"mismatch": _mismatch(run, split["mismatch"]), "match": _match(run, split["match"]),
            "cant-verify": _cant(run, split["cant-verify"])}
    for k, word in TABS:
        h.append(f"<section class=panel id='{k}'><h2>{word} ({len(split[k])})</h2>{body[k]}</section>")

    steps = [e for e in run.events if e.get("type") == "step" and e.get("text")]
    if steps:
        h.append("<details class=how><summary>How we checked</summary><ol>")
        for e in steps:
            ag = AGENTS.get(e.get("agent"), str(e.get("agent") or "").title())
            h.append(f"<li><span class=ag>{escape(ag)}</span>{escape(e['text'])}</li>")
        h.append("</ol></details>")
    h.append(f"</main><script>{SCRIPT}</script></body></html>")
    return "".join(h)
