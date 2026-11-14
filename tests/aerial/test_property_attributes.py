"""tools.aerial.property_attributes with the network faked (invented data, temporary cache folder).
Invented parcel: 418 Linden Grove Ave, ids 900001 / 900002, invented coordinates."""
import json

import pytest

import rc_common
from tools.aerial import aerial

LAT, LON = 12.34567, -45.67891


@pytest.fixture
def priv(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("RC_NO_CACHE", raising=False)
    return tmp_path


def _feature(date, pool=True, hot_tub=False, court=False, solar=False, vehicles=3, tree=12.0, n=1, **extra):
    p = {"metadata.ortho-date-estimate": date, "metadata.collection": "invented-collection",
         "request-information.requested-wkt": f"POINT({LON} {LAT})",
         "pool.detected": pool, "pool.score": 0.91 if pool else None, "pool.total-area": 812.4 if pool else None,
         "pool.types.in-ground.detected": bool(pool), "pool.types.above-ground.detected": False,
         "pool.types.hot-tub.detected": hot_tub, "sport-court.detected": court, "playground.detected": False,
         "trampoline.detected": False, "deck.detected": False,
         "vehicles.detected": vehicles is not None, "vehicles.count": vehicles,
         "parcel-tree-cover.percent": tree, "structure.count": n,
         "structures": [{"roof-objects.solar.value": solar, "building-type.class": "residential",
                         "footprint.area": 9999.0}]}
    p.update(extra)
    return {"type": "Feature", "properties": p, "geometry": None}


def _fake(monkeypatch, *features, fail=None):
    calls = []

    def call(method, path, params=None, body=None, timeout=120):
        calls.append((method, path, params, timeout))
        if fail:
            raise fail
        return json.dumps({"type": "FeatureCollection", "features": list(features)}).encode()
    monkeypatch.setattr(aerial, "_call", call)
    return calls


def test_multi_year_uses_newest(monkeypatch, priv):
    calls = _fake(monkeypatch, _feature("2023-05-18T00:00:00", pool=False, vehicles=16),
                  _feature("2025-12-19T00:00:00", vehicles=163, tree=6.0, n=6),
                  _feature("2025-08-25T00:00:00", vehicles=193),  # same year, older: ignored
                  _feature("2024-08-07T00:00:00", vehicles=34))
    out = aerial.property_attributes(LAT, LON)
    assert out["captured"] == "2025-12-19"
    assert out["pool"] == {"present": True, "confidence": 0.91, "area_sqft": 812, "kind": "in-ground",
                           "stable": True}
    assert out["vehicles"] == 163 and out["tree_cover_pct"] == 6.0 and out["buildings"] == 6
    assert out["source"] == "Vexcel property data"
    for hidden in ("building_type", "footprint", "stories", "height_m"):
        assert hidden not in out
    (method, path, params, timeout), = calls
    assert (method, path) == ("GET", "property/extract") and timeout <= 10
    assert params["wkt"] == f"POINT({LON} {LAT})"


def test_flip_between_years_is_not_stable(monkeypatch, priv):
    _fake(monkeypatch, _feature("2026-07-03T19:37:10", pool=True, solar=False, court=False),
          _feature("2025-12-01T00:00:00", pool=False, solar=True, court=True))
    out = aerial.property_attributes(LAT, LON)
    assert out["pool"]["present"] is True and out["pool"]["stable"] is False
    assert out["solar"] == {"present": False, "stable": False}
    assert out["sport_court"] == {"present": False, "stable": False}
    assert out["playground"] == {"present": False, "stable": True}


def test_same_across_years_is_stable(monkeypatch, priv):
    _fake(monkeypatch, _feature("2026-01-02", hot_tub=True), _feature("2025-03-04", hot_tub=True))
    out = aerial.property_attributes(LAT, LON)
    assert out["hot_tub"] == {"present": True, "stable": True}
    assert out["pool"]["kind"] == "in-ground, hot tub" and out["pool"]["stable"] is True
    for k in ("sport_court", "playground", "solar", "trampoline", "deck"):
        assert out[k] == {"present": False, "stable": True}


def test_single_year_cannot_be_stable(monkeypatch, priv):
    _fake(monkeypatch, _feature("2026-01-02"))
    out = aerial.property_attributes(LAT, LON)
    assert out["pool"]["stable"] is False and out["deck"]["stable"] is False


def test_missing_attributes_omitted(monkeypatch, priv):
    f = _feature("2026-01-02", pool=False, vehicles=None, tree=None, n=None)
    p = f["properties"]
    for k in ("sport-court.detected", "deck.detected", "pool.types.hot-tub.detected"):
        del p[k]
    p["vehicles.detected"] = None
    p["trampoline.detected"] = None
    p["structures"] = []
    _fake(monkeypatch, f)
    out = aerial.property_attributes(LAT, LON)
    assert out["pool"] == {"present": False, "confidence": None, "area_sqft": None, "stable": False}
    for k in ("sport_court", "deck", "hot_tub", "trampoline", "solar", "vehicles", "tree_cover_pct", "buildings"):
        assert k not in out
    assert out["playground"]["present"] is False


def test_no_vehicles_seen_is_zero(monkeypatch, priv):
    _fake(monkeypatch, _feature("2026-01-02", vehicles=None))
    assert aerial.property_attributes(LAT, LON)["vehicles"] == 0


@pytest.mark.parametrize("features,fail", [
    ((), None),                                              # no data
    ((_feature("not a date"),), None),                       # nothing usable
    ((), aerial._NoCoverage("property/extract answered 404")),
    ((), RuntimeError("Vexcel property/extract answered HTTP 500")),
    ((), TimeoutError("timed out")),
])
def test_failure_is_none_and_not_cached(monkeypatch, priv, features, fail):
    _fake(monkeypatch, *features, fail=fail)
    assert aerial.property_attributes(LAT, LON) is None
    assert not list(rc_common.cache_dir("aerial").rglob("property_v*.json"))


def test_bad_body_is_none(monkeypatch, priv):
    monkeypatch.setattr(aerial, "_call", lambda *a, **k: b"<html>not json</html>")
    assert aerial.property_attributes(LAT, LON) is None


def test_cache_hit(monkeypatch, priv):
    calls = _fake(monkeypatch, _feature("2026-01-02"), _feature("2025-01-02"))
    first = aerial.property_attributes(LAT, LON)
    assert len(calls) == 1
    assert aerial.property_attributes(LAT, LON) == first and len(calls) == 1
    f = rc_common.cache_dir("aerial", aerial._key(LAT, LON)) / f"property_v{aerial.PROPERTY_VERSION}.json"
    assert json.loads(f.read_text(encoding="utf-8")) == first


def test_no_cache_switch(monkeypatch, priv):
    calls = _fake(monkeypatch, _feature("2026-01-02", vehicles=5))
    assert aerial.property_attributes(LAT, LON)["vehicles"] == 5
    calls2 = _fake(monkeypatch, _feature("2026-01-02", vehicles=7))
    assert aerial.property_attributes(LAT, LON)["vehicles"] == 5 and not calls2  # cache used
    monkeypatch.setenv("RC_NO_CACHE", "1")
    assert aerial.property_attributes(LAT, LON)["vehicles"] == 7 and len(calls2) == 1
    monkeypatch.delenv("RC_NO_CACHE")
    assert aerial.property_attributes(LAT, LON)["vehicles"] == 7  # fresh answer written in place
    assert len(calls) == 1
