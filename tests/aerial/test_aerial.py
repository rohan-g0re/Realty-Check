"""Live (cached) tests for tools.aerial. Skip when RC_PRIVATE_DIR is unset.
Run: RC_PRIVATE_DIR=... .venv/Scripts/python.exe -m pytest tests/aerial -q"""
import json
import os
from pathlib import Path

import pytest

if not os.environ.get("RC_PRIVATE_DIR"):
    pytest.skip("RC_PRIVATE_DIR not set", allow_module_level=True)

from PIL import Image

import rc_common
from tools.aerial import aerial, aerial_images, building_height
from tools.aerial.aerial import stories_from_height
from tests.private_golden import golden


@pytest.fixture(scope="module")
def houston():
    """The private Houston demo building: its coordinates and expected aerial height live in the private folder."""
    return golden("houston")


@pytest.fixture(scope="module")
def HOUSTON_BLDG(houston):
    return (houston["lat"], houston["lon"])


def _no_key(obj):
    key = rc_common.secret("VEXCEL_API_KEY")
    assert key not in json.dumps(obj), "API key leaked"  # never print the key


def test_stories_rule():
    assert stories_from_height(23.1) == (6, [6, 7])
    assert stories_from_height(16.2)[0] == 4


def test_height_houston(houston, HOUSTON_BLDG):
    ev = building_height(*HOUSTON_BLDG)
    assert ev["source"] == "aerial" and ev["status"] == "ok", ev["summary"]
    lo_m, hi_m = houston["aerial_height_m"]
    assert lo_m <= ev["observed"]["height_m"] <= hi_m
    assert ev["observed"]["stories"] == houston["aerial_stories"]
    lo, hi = ev["observed"]["stories_range"]
    assert lo <= houston["aerial_stories"] <= hi
    _no_key(ev)


def test_images_houston(HOUSTON_BLDG):
    ev = aerial_images(*HOUSTON_BLDG)
    assert ev["status"] == "ok", ev["summary"]
    cache = rc_common.cache_dir()
    tops = [i for i in ev["images"] if i["caption"].startswith("Top-down")]
    obls = [i for i in ev["images"] if i["caption"].startswith("Angled")]
    assert len(tops) >= 1 and len(obls) >= 1
    for i in ev["images"]:
        p = Path(i["path"])
        assert p.exists() and p.suffix == ".jpg" and cache in p.parents
        assert max(Image.open(p).size) <= 1568
    _no_key(ev)


def test_repeat_calls_are_cached(HOUSTON_BLDG):
    building_height(*HOUSTON_BLDG)
    aerial_images(*HOUSTON_BLDG)
    n = aerial.CALLS["n"]
    building_height(*HOUSTON_BLDG)
    aerial_images(*HOUSTON_BLDG)
    assert aerial.CALLS["n"] == n


def test_atlantic_no_exception():
    for fn in (building_height, aerial_images):
        ev = fn(0.0, -30.0)
        assert ev["status"] in ("no_evidence", "error")
        assert ev["summary"]
        _no_key(ev)


def test_cached_json_has_no_key():
    key = rc_common.secret("VEXCEL_API_KEY")
    for f in rc_common.cache_dir("aerial").rglob("*.json"):
        assert key not in f.read_text(encoding="utf-8"), f.name
