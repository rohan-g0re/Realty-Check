"""Live-ish tests for tools.scan. The first run makes a few read-only Matterport calls; later runs use the cache."""
import json
import os
from pathlib import Path

import pytest

if not os.environ.get("RC_PRIVATE_DIR"):
    pytest.skip("RC_PRIVATE_DIR unset", allow_module_level=True)

import rc_common
from PIL import Image
from tools.scan import scan_stills, scan_summary
from tools.scan import matterport as mp
from tests.private_golden import golden

BAD = ("X-Amz", "Signature=", "token=")


@pytest.fixture(scope="module")
def GOLDEN():
    """The private demo scan (a 1BR + den plan whose listing says 2 beds); its id lives in the private folder."""
    return golden("scan_model_id")


def _clean(obj):
    s = json.dumps(obj)
    assert not any(b in s for b in BAD), "signed URL / token leaked"


def test_golden_summary(GOLDEN):
    ev = scan_summary(GOLDEN)
    print(json.dumps(ev, indent=1))
    assert ev["source"] == "scan" and ev["status"] == "ok"
    o = ev["observed"]
    assert "1BR W Den" in (o.get("model_name") or "") or any("den" in x.lower() for x in o.get("room_labels", []))
    assert o["bedrooms"] == 1
    if "area_sqft" in o:
        assert 300 < o["area_sqft"] < 2000
    _clean(ev)


def test_golden_stills_cached(GOLDEN):
    ev = scan_stills(GOLDEN, 4)
    assert ev["status"] == "ok", ev
    assert 1 <= len(ev["images"]) <= 4
    cache = rc_common.cache_dir().resolve()
    for im in ev["images"]:
        p = Path(im["path"])
        assert p.exists() and cache in p.resolve().parents
        assert im["caption"]
        with Image.open(p) as img:
            assert max(img.size) <= 1568
    _clean(ev)
    before = mp.CALLS["n"]
    ev2 = scan_stills(GOLDEN, 4)
    assert mp.CALLS["n"] == before, "second call hit the network"
    assert [i["path"] for i in ev2["images"]] == [i["path"] for i in ev["images"]]


def test_bogus_ids():
    for bad in ("zzzzzzzzzzz", "", "../etc", None):
        for fn in (scan_summary, scan_stills):
            ev = fn(bad)
            assert ev["status"] in ("no_evidence", "error") and ev["summary"]
            _clean(ev)


def test_cache_has_no_signed_urls(GOLDEN):
    scan_summary(GOLDEN)
    for p in rc_common.cache_dir("scan").rglob("*.json"):
        txt = p.read_text(encoding="utf-8")
        assert not any(b in txt for b in BAD), p.name
        assert "https://" not in txt, p.name


def test_name_parsing():
    assert mp._beds_from_name("1BR W Den 1BA - 815 SF") == 1
    assert mp._baths_from_name("1BR W Den 1BA - 815 SF") == 1
    assert mp._beds_from_name("Studio A") == 0
    assert mp._beds_from_name("Plan E") is None
