"""Draws docs/img/architecture.excalidraw. Needs the simple-excalidraw skill's sx.py on the path (SX_DIR).

Brand marks are optional: put langchain / matterport / vexcel / costar as .svg or .png in docs/img/logos/ and they
are placed on the diagram; without them the boxes carry the names as text.
"""
import base64
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("SX_DIR", str(Path.home() / ".claude/skills/simple-excalidraw/scripts")))
from sx import Diagram, BLUE, RED, GREEN, ORANGE, GREY_FILL  # noqa: E402

HERE = Path(__file__).parent
LOGOS = HERE / "logos"


def logo(name):
    for ext in ("svg", "png"):
        p = LOGOS / f"{name}.{ext}"
        if p.is_file():
            return p
    return None


def ratio(p):
    """Width over height of a logo file."""
    if p.suffix == ".png":
        w, h = int.from_bytes(p.read_bytes()[16:20], "big"), int.from_bytes(p.read_bytes()[20:24], "big")
        return w / h
    s = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', s)
    if m:
        return float(m.group(1)) / float(m.group(2))
    w, h = re.search(r'\bwidth="([\d.]+)', s), re.search(r'\bheight="([\d.]+)', s)
    return float(w.group(1)) / float(h.group(1)) if w and h else 1.0


d = Diagram("How Realty Check checks one listing", x=-380, y=40)
images = []  # (name, x, y, height, max width)

d.box("page", "LISTING PAGE", sub="any listing site", x=-380, y=150, w=420, h=130, color=BLUE, fill=GREY_FILL, size=26)
d.box("ext", "CHROME EXTENSION", sub="sidebar + page marks", x=190, y=150, w=420, h=130, size=26)
d.box("server", "LOCAL SERVER", sub="sends page text + address, streams the run", x=760, y=150, w=620, h=130, size=26)
d.box("model", "CLAUDE CODE CLI", sub="sonnet, no API key", x=140, y=815, w=420, h=150, size=26)

d.container("harness", x=700, y=380, w=1540, h=1770, color=ORANGE)
mark = logo("langchain")
if mark:
    images.append(("langchain", 724, 400, 44, 300))
d.note("deepagents" if mark else "LangChain deepagents", x=724 + (int(44 * ratio(mark)) + 18 if mark else 0), y=404,
       color=ORANGE, size=29)

d.box("orch", "ORCHESTRATOR", sub="read_claims, match_listing, then delegates", x=760, y=500, w=620, h=780, color=RED,
      emphasis=True, size=26)
d.box("close", "ALWAYS-RUN CLOSE", sub="makes any skipped source call", x=760, y=1400, w=620, h=150, size=26)
d.box("judge", "JUDGE", sub="rules per claim type, 0-100 confidence", x=760, y=1660, w=620, h=150, size=26)
d.box("cite", "CITATIONS", sub="no proof = couldn't check", x=760, y=1920, w=620, h=150, size=26)

subagents = [("inside", "INSIDE SUBAGENT", "check_floor_plans, unit_feature_photos", 500),
             ("outside", "OUTSIDE SUBAGENT", "measure_building, outdoor_feature_photos", 710),
             ("paper", "PAPER SUBAGENT", "county_lookup, building_record", 920)]
HEIGHT = {"paper": 360}
for key, name, tools, y in subagents:
    d.box(key, name, sub=tools, x=1560, y=y, w=620, h=HEIGHT.get(key, 150), color=BLUE, size=24)

sources = [("matterport", "MATTERPORT", "3D scan", 500), ("vexcel", "VEXCEL", "aerial imagery", 710),
           ("costar", "COSTAR", "property record", 920), ("county", "COUNTY", "public record", 1120)]
for key, name, role, y in sources:
    if logo(key):
        d.box(key, "\n\n" + role, x=2440, y=y, w=420, h=160, fill=GREY_FILL, size=24)
        tall = ratio(logo(key)) < 1.5  # a stacked mark needs more height than a wordmark
        images.append((key, 2440, y + (8 if tall else 28), 86 if tall else 56, 340))
    else:
        d.box(key, name, sub=role, x=2440, y=y, w=420, h=160, fill=GREY_FILL, size=26)

d.box("result", "RESULT", sub="sidebar, full report, exhibits", x=760, y=2260, w=620, h=150, color=GREEN, size=26)

d.arrow("page", "ext")
d.arrow("ext", "server", double=True)
d.arrow("server", "orch", src_shift=190, dst_shift=190)  # clear of the top-left label
d.arrow("model", "orch", double=True)
d.arrow("orch", "inside", label="task", double=True, src_shift=-315)
d.arrow("orch", "outside", label="task", double=True, src_shift=-105)
d.arrow("orch", "paper", label="task", double=True, src_shift=210)
d.arrow("inside", "matterport")
d.arrow("outside", "vexcel")
d.arrow("paper", "costar", src_shift=-100, dst_shift=0)
d.arrow("paper", "county", src_shift=100, dst_shift=0)
d.flow(["orch", "close", "judge", "cite", "result"])

out = HERE / "architecture.excalidraw"
doc = d.save(str(out))
if images:
    base = next(e for e in doc["elements"] if e["type"] == "rectangle")
    for i, (name, x, y, h, wmax) in enumerate(images):
        p = logo(name)
        w = h * ratio(p)
        if w > wmax:
            w, h = wmax, wmax / ratio(p)
        if name != "langchain":
            x = x + (420 - w) / 2
        mime = "image/svg+xml" if p.suffix == ".svg" else "image/png"
        fid = f"logo-{name}"
        doc["files"][fid] = {"mimeType": mime, "id": fid, "created": 1, "lastRetrieved": 1,
                             "dataURL": f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()}
        el = {**base, "id": f"img-{name}", "type": "image", "x": x, "y": y, "width": w, "height": h, "seed": 9000 + i,
              "strokeColor": "transparent", "backgroundColor": "transparent", "boundElements": [], "roundness": None,
              "fileId": fid, "status": "saved", "scale": [1, 1], "roughness": 0}
        doc["elements"].append(el)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
