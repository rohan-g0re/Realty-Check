"""Guard: no sponsor kit data in files git tracks (or would track).

The kit index lives only in the private folder ($RC_PRIVATE_DIR/rc_cache/kit/index.json), so this test skips without
it. It fails when a tracked text file holds a kit property id as a standalone number next to an id-like word, a kit
3D-scan id, or a kit street address ("<number> <street>"). Failures name file:line and the kind of hit only, never
the value, so the log itself does not leak.
"""
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NUM = re.compile(r"(?<![\w.\-/])(\d{4,})(?![\w\-]|\.\d)")
ID_CONTEXT = re.compile(r"\bid\b|_id|\bids?\b|property|pid|kit")
TOKEN11 = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{11}(?![A-Za-z0-9])")
HOUSE = re.compile(r"(?<![\w$#\-])(\d{1,6})\s+(?=[A-Za-z])")
SUFFIXES = {"st", "street", "ave", "avenue", "blvd", "boulevard", "dr", "drive", "rd", "road", "ln", "lane", "way",
            "pl", "place", "ct", "court", "pkwy", "pky", "parkway", "hwy", "highway", "cir", "circle", "ter", "trl"}


def _index():
    root = os.environ.get("RC_PRIVATE_DIR")
    if not root:
        pytest.skip("RC_PRIVATE_DIR not set: no private dataset to check against")
    p = Path(root) / "rc_cache" / "kit" / "index.json"
    if not p.is_file():
        pytest.skip("private kit index not built")
    return json.loads(p.read_text(encoding="utf-8"))["properties"]


def _files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    for name in filter(None, out.splitlines()):
        path = REPO / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        yield name, data.decode("utf-8", errors="replace")


@pytest.fixture(scope="module")
def kit_values():
    props = _index()
    ids = {str(p["property_id"]) for p in props}
    scans = {m for p in props for m in (p.get("matterport_model_ids") or [])}
    streets = defaultdict(set)  # house number -> lower-case "number street..." lines
    for p in props:
        line = (p.get("address") or "").split(",")[0].strip().lower()
        words = re.sub(r"[.\s]+", " ", line).split()
        if len(words) >= 2 and re.fullmatch(r"\d{1,6}", words[0]):
            if len(words) >= 3 and words[-1] in SUFFIXES:
                words = words[:-1]  # "<n> <name> St" also matches "<n> <name> Street" and a bare "<n> <name>" heading
            core = " ".join(words)
            if len(core) >= 6:
                streets[words[0]].add(core)
    return ids, scans, streets


def test_no_kit_values_in_tracked_files(kit_values):
    ids, scans, streets = kit_values
    hits = []
    for name, text in _files():
        for n, line in enumerate(text.splitlines(), 1):
            low = re.sub(r"[.\s]+", " ", line.lower())
            for m in NUM.finditer(line):
                if m.group(1) in ids and ID_CONTEXT.search(line[max(0, m.start() - 40):m.end() + 10].lower()):
                    hits.append(f"{name}:{n} kit property id")
            for m in TOKEN11.finditer(line):
                if m.group(0) in scans:
                    hits.append(f"{name}:{n} kit 3D-scan id")
            for m in HOUSE.finditer(low):
                rest = low[m.start():]
                for street in streets.get(m.group(1), ()):
                    if rest.startswith(street) and (len(rest) == len(street) or not rest[len(street)].isalnum()):
                        hits.append(f"{name}:{n} kit street address")
                        break
    assert not hits, "sponsor kit data in the repo:\n" + "\n".join(sorted(set(hits)))
