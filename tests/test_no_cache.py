"""RC_NO_CACHE=1: every result-cache read is skipped and the real work runs (invented data, tmp cache dirs)."""
import hashlib
import json

import pytest
from PIL import Image

import rc_common
from tools import cachectl


@pytest.fixture
def priv(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("RC_NO_CACHE", raising=False)
    return tmp_path


def _off(monkeypatch):
    monkeypatch.setenv("RC_NO_CACHE", "1")


def test_switch_values(monkeypatch, tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    monkeypatch.delenv("RC_NO_CACHE", raising=False)
    assert cachectl.enabled() and cachectl.read_ok() and cachectl.hit(f)
    assert not cachectl.hit(None) and not cachectl.hit(tmp_path / "missing")
    for v in ("0", "", "false", "off"):
        monkeypatch.setenv("RC_NO_CACHE", v)
        assert cachectl.hit(f)
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("RC_NO_CACHE", v)
        assert not cachectl.enabled() and not cachectl.read_ok() and not cachectl.hit(f)


def test_claims_extraction(monkeypatch, priv):
    from harness.claims import extract
    from harness.model import ask
    prompt = "invented prompt"
    seeded = rc_common.cache_dir("claims") / (hashlib.sha256(prompt.encode()).hexdigest()[:32] + ".json")
    seeded.write_text(json.dumps({"from": "cache"}))
    calls = []
    monkeypatch.setattr(ask, "ask_json", lambda p: calls.append(p) or {"from": "model"})
    assert extract._ask_cached(prompt) == {"from": "cache"} and not calls  # default: cache used
    _off(monkeypatch)
    assert extract._ask_cached(prompt) == {"from": "model"} and calls == [prompt]
    assert json.loads(seeded.read_text()) == {"from": "model"}  # written in place


def test_vision_answers(monkeypatch, priv):
    from harness.model import ask
    import importlib
    vlook = importlib.import_module("tools.vision.look")
    img = priv / "a.jpg"
    Image.new("RGB", (8, 8), "red").save(img)
    fields = {"color": "main colour"}
    cache = vlook._cache_file([str(img)], "q?", fields)
    cache.write_text(json.dumps({"status": "ok", "answer": {"color": "cached"}, "confidence": 1.0, "notes": ""}))
    calls = []
    monkeypatch.setattr(ask, "ask_images", lambda paths, p: calls.append(p) or
                        '{"answer": {"color": "red"}, "confidence": 0.9, "notes": ""}')
    assert vlook.look([str(img)], "q?", fields)["answer"]["color"] == "cached" and not calls
    _off(monkeypatch)
    assert vlook.look([str(img)], "q?", fields)["answer"]["color"] == "red" and len(calls) == 1


def test_aerial_fetch_and_evidence(monkeypatch, priv):
    from tools.aerial import aerial
    raw = priv / "r.raw"
    raw.write_bytes(b"old")
    calls = []
    monkeypatch.setattr(aerial, "_call", lambda *a, **k: calls.append(a) or b"new")
    assert aerial._fetch(raw, "GET", "x") == b"old" and not calls
    ev_path = priv / "height.json"
    ev_path.write_text(json.dumps(rc_common.evidence("aerial", "ok", "cached")))
    built = []
    build = lambda: built.append(1) or rc_common.evidence("aerial", "ok", "fresh")  # noqa: E731
    assert aerial._cached_evidence(ev_path, build)["summary"] == "cached" and not built
    _off(monkeypatch)
    assert aerial._fetch(raw, "GET", "x") == b"new" and len(calls) == 1 and raw.read_bytes() == b"new"
    assert aerial._cached_evidence(ev_path, build)["summary"] == "fresh" and built == [1]


def test_scan_summary_and_stills(monkeypatch, priv):
    from tools.scan import matterport as mp
    mid = "AbCdEf123456"
    folder = rc_common.cache_dir("scan", mid)
    (folder / "summary.json").write_text(json.dumps({"data": {"model": {"name": "cached"}}}))
    gql = []
    monkeypatch.setattr(mp, "_gql", lambda q, m: gql.append(q) or {"data": {"model": {"name": "fresh"}}})
    assert mp._model(mid, mp.SUMMARY_Q, "summary.json")[0]["name"] == "cached" and not gql
    Image.new("RGB", (8, 8)).save(folder / "floorplan_1.jpg")
    (folder / "stills.json").write_text(json.dumps(
        {"model_id": mid, "available": 1, "images": [{"file": "floorplan_1.jpg", "caption": "c"}]}))
    assert "served from local cache" in mp.scan_stills(mid, 1)["detail"]
    _off(monkeypatch)
    assert mp._model(mid, mp.SUMMARY_Q, "summary.json")[0]["name"] == "fresh" and len(gql) == 1
    asked = []
    monkeypatch.setattr(mp, "_model", lambda *a: asked.append(a) or (None, []))
    ev = mp.scan_stills(mid, 1)
    assert asked and ev["status"] == "no_evidence"  # the manifest was not used


def test_spot_picks_and_faces(monkeypatch, priv):
    from tools.scan import spots
    mid, claim = "AbCdEf123456", {"kind": "bedrooms", "value": "2"}
    spots.cache_path(mid, claim).write_text(json.dumps({"status": "found", "how": "cached"}))
    assert spots.cached_pick(mid, claim)["how"] == "cached"
    pano = {"id": "p1", "skybox": {"children": ["x"] + [f"https://invented.example/{i}" for i in range(4)]}}
    for p in spots._face_paths(mid, pano):
        Image.new("RGB", (4, 4), "blue").save(p)

    class Client:
        n = 0

        def download_image(self, u):
            Client.n += 1
            return Image.new("RGB", (4, 4), "green")
    spots._faces(mid, pano, Client())
    assert Client.n == 0
    _off(monkeypatch)
    assert spots.cached_pick(mid, claim) is None
    spots._faces(mid, pano, Client())
    assert Client.n == 4


def test_county_geocode_and_record(monkeypatch, priv):
    from tools.paper import county, hcad, la, nyc
    addr = "1 Invented St, Houston"
    g = rc_common.cache_dir("paper", "geocode") / f"{county._key(county._norm(addr))}.json"
    g.write_text(json.dumps({"address": addr, "result": {"lat": 1.0, "lon": 2.0, "matched_address": "cached"}}))
    hits = []
    match = {"result": {"addressMatches": [{"coordinates": {"x": 2.5, "y": 1.5}, "matchedAddress": "fresh"}]}}
    monkeypatch.setattr(county.net, "get_json_url", lambda *a, **k: hits.append(a) or (None, match))
    assert county.geocode(addr)["matched_address"] == "cached" and not hits

    monkeypatch.setattr(hcad, "in_area", lambda lat, lon: True)
    monkeypatch.setattr(la, "in_area", lambda lat, lon: False)
    monkeypatch.setattr(nyc, "in_area", lambda lat, lon: False)
    looked = []
    monkeypatch.setattr(county, "_houston", lambda a, lat, lon: looked.append(a) or
                        rc_common.evidence("county", "ok", "fresh"))
    rec = rc_common.cache_dir("paper", "county") / f"{county._key(county._norm(addr), 1.0, 2.0, county.CACHE_VERSION)}.json"
    rec.write_text(json.dumps(rc_common.evidence("county", "ok", "cached")))
    assert county.county_record(addr, 1.0, 2.0)["summary"] == "cached" and not looked

    _off(monkeypatch)
    assert county.geocode(addr)["matched_address"] == "fresh" and len(hits) == 1
    assert county.county_record(addr, 1.0, 2.0)["summary"] == "fresh" and looked == [addr]


def test_run_counts_nothing_as_cached(monkeypatch, priv):
    from harness.graph import run
    mid = "AbCdEf123456"
    folder = rc_common.cache_dir("scan", mid)
    (folder / "summary.json").write_text("{}")
    (folder / "stills.json").write_text("{}")
    assert run._scan_cached(mid) and run._stills_cached(mid)
    _off(monkeypatch)
    assert not run._scan_cached(mid) and not run._stills_cached(mid)
