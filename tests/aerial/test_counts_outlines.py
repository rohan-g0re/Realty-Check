"""Pool / hot-tub counts in property_attributes, feature_outlines and outlined_image, with the network faked.
Invented data only: invented coordinates, invented boxes, a synthetic grey photo."""
import json

import pytest
from PIL import Image

import rc_common
from tools.aerial import aerial
from tools.aerial import feature_outlines as exported_outlines, outlined_image as exported_image

LAT, LON = 12.34567, -45.67891


@pytest.fixture
def priv(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("RC_NO_CACHE", raising=False)
    return tmp_path


def _prop(**p):
    base = {"metadata.ortho-date-estimate": "2026-01-02T00:00:00", "pool.detected": True, "pool.score": 0.9,
            "pool.types.in-ground.detected": True, "pool.types.hot-tub.detected": False}
    base.update(p)
    return json.dumps({"features": [{"type": "Feature", "properties": base, "geometry": None}]}).encode()


@pytest.mark.parametrize("fields,count", [
    ({"pool.types.in-ground.count": 2}, 2),
    ({"pool.types.in-ground.enclosed.count": 1, "pool.types.in-ground.non-enclosed.count": 1}, 2),
    ({"pool.types.in-ground.non-enclosed.count": 1, "pool.types.above-ground.count": 1}, 2),
    ({"pool.types.in-ground.count": None, "pool.types.in-ground.enclosed.count": None}, None),
])
def test_pool_count(monkeypatch, priv, fields, count):
    monkeypatch.setattr(aerial, "_call", lambda *a, **k: _prop(**fields))
    pool = aerial.property_attributes(LAT, LON)["pool"]
    assert pool.get("count") == count


def test_hot_tub_count_and_cache_name(monkeypatch, priv):
    monkeypatch.setattr(aerial, "_call", lambda *a, **k: _prop(**{
        "pool.types.hot-tub.detected": True, "pool.types.hot-tub.non-enclosed.count": 1,
        "pool.types.in-ground.count": 1}))
    out = aerial.property_attributes(LAT, LON)
    assert out["hot_tub"]["count"] == 1 and out["pool"]["count"] == 1
    assert (rc_common.cache_dir("aerial", aerial._key(LAT, LON)) / "property_v2.json").exists()


def _box(w, s, e, n):
    return f"POLYGON (({w} {n}, {e} {n}, {e} {s}, {w} {s}, {w} {n}))"


def _detect(*dets):
    feats = {}
    for label, wkt, conf in dets:
        feats.setdefault(label, []).append({"imageId": "ortho-1", "bbox": wkt, "confidence": conf})
    return json.dumps({"type": "FeatureCollection", "images": [{"image-id": "ortho-1"}],
                       "features": [{"type": "Feature", "properties": {"label": l, "detections": d}}
                                    for l, d in feats.items()]}).encode()


POOL_WKT = _box(LON - 0.0002, LAT - 0.0001, LON + 0.0001, LAT + 0.0001)


def _fake_detect(monkeypatch, body=None, fail=None):
    calls = []

    answer = body

    def call(method, path, params=None, body=None, timeout=120):
        calls.append((method, path, timeout, body))
        if fail:
            raise fail
        return answer
    monkeypatch.setattr(aerial, "_call", call)
    return calls


def test_outlines_parse_and_cache(monkeypatch, priv):
    calls = _fake_detect(monkeypatch, _detect(("swimming pool", POOL_WKT, 0.80908203125),
                                              ("tennis court", "not a polygon", 0.7),
                                              ("car", POOL_WKT, 0.9)))
    out = aerial.feature_outlines(LAT, LON)
    assert out == [{"feature": "pool", "bbox": [round(LON - 0.0002, 7), round(LAT - 0.0001, 7),
                                                 round(LON + 0.0001, 7), round(LAT + 0.0001, 7)],
                    "confidence": 0.81}]
    (method, path, timeout, body), = calls
    assert (method, path) == ("POST", "elements/detect") and timeout <= 20
    labels = {e["label"] for e in body["model-input"]["elements"]}
    assert labels == {"swimming pool", "tennis court", "solar panel"}
    assert body["images"][0]["request"]["wkt"] == aerial._square(LAT, LON, 60)
    assert aerial.feature_outlines(LAT, LON) == out and len(calls) == 1  # cached
    assert exported_outlines is aerial.feature_outlines


def test_outlines_empty_is_list(monkeypatch, priv):
    _fake_detect(monkeypatch, _detect())
    assert aerial.feature_outlines(LAT, LON) == []


@pytest.mark.parametrize("body,fail", [
    (None, RuntimeError("Vexcel elements/detect answered HTTP 500")),
    (None, TimeoutError("timed out")),
    (b"<html>no</html>", None),
    (b'{"detail": "bad"}', None),
])
def test_outlines_failure_none_not_cached(monkeypatch, priv, body, fail):
    _fake_detect(monkeypatch, body, fail)
    assert aerial.feature_outlines(LAT, LON) is None
    assert not list(rc_common.cache_dir("aerial").rglob("outlines_v*.json"))


def test_to_px_corners():
    size = (1000, 1000)
    sq = aerial._bbox_of_wkt(aerial._square(LAT, LON, 60))
    w, s, e, n = sq
    assert aerial._to_px(LAT, LON, 60, size, w, n) == pytest.approx((0, 0), abs=1e-6)
    assert aerial._to_px(LAT, LON, 60, size, e, s) == pytest.approx((1000, 1000), abs=1e-6)
    assert aerial._to_px(LAT, LON, 60, size, LON, LAT) == pytest.approx((500, 500), abs=1e-6)


def _photo(priv):
    d = rc_common.cache_dir("aerial", aerial._key(LAT, LON))
    p = d / "topdown_h60.jpg"
    Image.new("RGB", (400, 400), (90, 90, 90)).save(p, "JPEG")
    return p


def test_outlined_image(monkeypatch, priv):
    photo = _photo(priv)
    _fake_detect(monkeypatch, _detect(("swimming pool", POOL_WKT, 0.8)))
    monkeypatch.setattr(aerial, "aerial_images", lambda lat, lon, half_m=60: rc_common.evidence(
        "aerial", "ok", "x", images=[{"path": str(photo), "caption": "Top-down Vexcel aerial photo (2026-01-02)"}]))
    im = aerial.outlined_image(LAT, LON)
    assert set(im) == {"path", "caption"}
    assert im["caption"] == "Top-down Vexcel aerial photo (2026-01-02), detected pool outlined"
    out = Image.open(im["path"]).convert("RGB")
    assert out.size == (400, 400)
    # box edge drawn where the pool's west edge falls; centre of the photo far from it stays grey
    x0, y0 = aerial._to_px(LAT, LON, 60, out.size, LON - 0.0002, LAT)
    r, g, b = out.getpixel((int(round(x0)), int(round(y0))))
    assert r > 180 and g > 150 and b < 120
    assert max(out.getpixel((380, 380))) < 130
    assert exported_image is aerial.outlined_image


def test_outlined_image_none_when_nothing(monkeypatch, priv):
    _photo(priv)
    _fake_detect(monkeypatch, _detect())
    assert aerial.outlined_image(LAT, LON) is None
    _fake_detect(monkeypatch, fail=RuntimeError("x"))
    monkeypatch.setenv("RC_NO_CACHE", "1")
    assert aerial.outlined_image(LAT, LON) is None


def test_outlined_image_none_without_photo(monkeypatch, priv):
    _fake_detect(monkeypatch, _detect(("swimming pool", POOL_WKT, 0.8)))
    monkeypatch.setattr(aerial, "aerial_images", lambda *a, **k: rc_common.evidence("aerial", "no_evidence", "none"))
    assert aerial.outlined_image(LAT, LON) is None
