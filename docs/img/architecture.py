"""Draws docs/img/architecture.excalidraw. Needs the simple-excalidraw skill's sx.py on the path (SX_DIR)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("SX_DIR", str(Path.home() / ".claude/skills/simple-excalidraw/scripts")))
from sx import Diagram, BLUE, RED, GREEN, GREY_FILL  # noqa: E402

d = Diagram("How a listing gets checked", x=180, y=60)
d.box("page", "LISTING PAGE", sub="any listing site", x=610, y=160, w=600, h=150, color=BLUE, fill=GREY_FILL)
d.box("ext", "CHROME EXTENSION", sub="reads the page, shows the result", x=610, y=430, w=600, h=150)
d.box("server", "LOCAL SERVER", sub="streams the run live", x=610, y=700, w=600, h=150)
d.box("orch", "ORCHESTRATOR", sub="deep agent: reads claims, finds the building",
      x=510, y=970, w=800, h=150, color=RED, emphasis=True)
helpers = [("inside", "INSIDE HELPER", "Matterport 3D scan"),
           ("outside", "OUTSIDE HELPER", "Vexcel aerial"),
           ("paper", "PAPER HELPER", "CoStar + County records")]
for i, (key, name, sub) in enumerate(helpers):
    d.box(key, name, sub=sub, x=180 + i * 500, y=1270, w=460, h=150, color=BLUE, size=26)
d.box("judge", "JUDGE", sub="fixed rules, no model", x=610, y=1570, w=600, h=150)
d.box("report", "RESULT", sub="sidebar, full report, exhibits", x=610, y=1840, w=600, h=150, color=GREEN)
d.flow(["page", "ext", "server", "orch"])
for key, shift in (("inside", -250), ("outside", 0), ("paper", 250)):
    d.arrow("orch", key, src_shift=shift)
    d.arrow(key, "judge", dst_shift=shift * 0.8)
d.arrow("judge", "report")
d.save(str(Path(__file__).with_name("architecture.excalidraw")))
