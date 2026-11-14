"""tools.vision tests. Offline by default; live model calls only with RC_LIVE=1.

Synthetic images are drawn with Pillow at run time. Private real images are read from
RC_PRIVATE_DIR at run time only (skipped if absent) and never copied into the repo.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

import harness.model.ask as ask
from tools.vision import check_room, count_floors, find_features, look, read_floorplan
from tools.vision.look import build_prompt

LIVE = os.environ.get("RC_LIVE") == "1"
live = pytest.mark.skipif(not LIVE, reason="set RC_LIVE=1 for live model calls")
FIELDS = {"stories": "integer: number of above-ground floors visible", "has_window": "true/false"}
OK6 = '{"answer": {"stories": 6, "has_window": true}, "confidence": 0.8, "notes": "n"}'


# ---------- synthetic images ----------

def draw_elevation(path: Path, rows: int = 6, cols: int = 5) -> Path:
    """Front elevation of a plain building with exactly `rows` rows of windows."""
    w, floor_h, margin = 900, 110, 80
    h = rows * floor_h + 2 * margin
    im = Image.new("RGB", (w, h), (200, 225, 245))
    d = ImageDraw.Draw(im)
    top, ground = margin, margin + rows * floor_h
    d.rectangle([150, top, w - 150, ground], fill=(190, 120, 90), outline=(60, 40, 30), width=4)
    d.rectangle([0, ground, w, h], fill=(110, 110, 110))
    bw = (w - 300) / cols
    for r in range(rows):
        y0 = top + r * floor_h + 25
        for c in range(cols):
            x0 = 150 + c * bw + bw * 0.25
            d.rectangle([x0, y0, x0 + bw * 0.5, y0 + 60], fill=(235, 245, 255), outline=(40, 40, 40), width=3)
    im.save(path)
    return path


def draw_floorplan(path: Path) -> Path:
    """One-bedroom-plus-den unit plan with labelled rooms."""
    im = Image.new("RGB", (1000, 700), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=36)
    rooms = {"BEDROOM": (40, 40, 420, 360), "BATH": (420, 40, 620, 360), "DEN": (620, 40, 960, 360),
             "KITCHEN": (40, 360, 420, 660), "LIVING": (420, 360, 960, 660)}
    for label, (x0, y0, x1, y1) in rooms.items():
        d.rectangle([x0, y0, x1, y1], outline="black", width=8)
        tw = d.textlength(label, font=font)
        d.text(((x0 + x1 - tw) / 2, (y0 + y1) / 2 - 18), label, fill="black", font=font)
    im.save(path)
    return path


@pytest.fixture
def img(tmp_path):
    return str(draw_elevation(tmp_path / "a.png"))


@pytest.fixture
def no_cache(monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)


def fake_reply(monkeypatch, reply, calls=None):
    def fake(paths, q, **k):
        if calls is not None:
            calls.append(q)
        return reply() if callable(reply) else reply
    monkeypatch.setattr(ask, "ask_images", fake)


# ---------- offline ----------

def test_prompt_has_every_field():
    p = build_prompt("How tall?", FIELDS)
    for name in FIELDS:
        assert name in p
    assert "null" in p and "JSON only" in p


@pytest.mark.parametrize("reply", [
    '```json\n{"answer": {"stories": 6, "has_window": true}, "confidence": 0.9, "notes": "Six rows."}\n```',
    'Here is my answer: {"answer": {"stories": 6, "has_window": true}, "confidence": 0.9, "notes": "Six rows."} Hope it helps.',
])
def test_parses_fenced_and_prosey(monkeypatch, no_cache, img, reply):
    fake_reply(monkeypatch, reply)
    r = look([img], "q", FIELDS)
    assert r == {"status": "ok", "answer": {"stories": 6, "has_window": True}, "confidence": 0.9, "notes": "Six rows."}


def test_find_features_one_call_for_all_features(monkeypatch, no_cache, img):
    calls = []
    fake_reply(monkeypatch, '{"answer": {"pool": {"presence": "seen", "confidence": 0.9, "image": 1}, '
                            '"dog_run": {"presence": "unclear", "confidence": 0.3, "image": null}}, '
                            '"confidence": 0.8, "notes": "Pool in the courtyard."}', calls)
    r = find_features([img], {"pool": "an outdoor swimming pool", "dog_run": "a fenced dog run"}, "aerial")
    assert len(calls) == 1 and "pool" in calls[0] and "dog_run" in calls[0] and "not_seen" in calls[0]
    assert r["status"] == "ok" and r["answer"]["pool"]["presence"] == "seen"
    assert r["answer"]["dog_run"]["presence"] == "unclear"


def test_missing_fields_are_null(monkeypatch, no_cache, img):
    fake_reply(monkeypatch, '{"answer": {"stories": 4}, "notes": "x"}')
    r = look([img], "q", FIELDS)
    assert r["status"] == "ok" and r["answer"] == {"stories": 4, "has_window": None} and r["confidence"] == 0.0


def test_retry_once_on_bad_json(monkeypatch, no_cache, img):
    replies = iter(["no json here", '{"answer": {"stories": 2, "has_window": false}, "confidence": 0.5, "notes": "ok"}'])
    calls = []
    fake_reply(monkeypatch, lambda: next(replies), calls)
    r = look([img], "q", FIELDS)
    assert r["answer"]["stories"] == 2 and len(calls) == 2 and "not valid JSON" in calls[1]


def test_bad_json_twice_is_error(monkeypatch, no_cache, img):
    calls = []
    fake_reply(monkeypatch, "sorry", calls)
    r = look([img], "q", FIELDS)
    assert r["status"] == "error" and r["answer"] == {"stories": None, "has_window": None} and len(calls) == 2


def test_model_exception_is_error_not_raise(monkeypatch, no_cache, img):
    def boom(*a, **k):
        raise RuntimeError("claude CLI exit 1")
    monkeypatch.setattr(ask, "ask_images", boom)
    r = look([img], "q", FIELDS)
    assert r["status"] == "error" and "claude CLI exit 1" in r["notes"]


def test_missing_image_is_error(no_cache, tmp_path):
    assert look([str(tmp_path / "nope.jpg")], "q", FIELDS)["status"] == "error"
    assert look([], "q", FIELDS)["status"] == "error"


def test_downscale_and_max_images(monkeypatch, no_cache, tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGBA", (4000, 1000), (10, 20, 30, 255)).save(big)
    seen = {}

    def fake(paths, q, **k):
        seen["sizes"] = [Image.open(p).size for p in paths]
        return '{"answer": {}, "confidence": 1, "notes": "n"}'
    monkeypatch.setattr(ask, "ask_images", fake)
    r = look([str(big)] * 8, "q", FIELDS)
    assert len(seen["sizes"]) == 6 and all(max(s) <= 1568 for s in seen["sizes"])
    assert Image.open(big).size == (4000, 1000)  # original untouched
    assert "2 extra images ignored" in r["notes"]


def test_cache_hit_avoids_second_call(monkeypatch, tmp_path, img):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path / "private"))
    calls = []
    fake_reply(monkeypatch, OK6, calls)
    r1 = look([img], "q", FIELDS)
    r2 = look([img], "q", FIELDS)
    assert r1 == r2 and len(calls) == 1
    assert list((tmp_path / "private" / "rc_cache" / "vision").glob("*.json"))
    look([img], "other question", FIELDS)
    assert len(calls) == 2


def test_errors_are_not_cached(monkeypatch, tmp_path, img):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path / "private"))
    calls = []
    fake_reply(monkeypatch, "sorry", calls)
    look([img], "q", FIELDS)
    look([img], "q", FIELDS)
    assert len(calls) == 4


def test_no_cache_without_private_dir(monkeypatch, no_cache, img):
    calls = []
    fake_reply(monkeypatch, OK6, calls)
    look([img], "q", FIELDS)
    look([img], "q", FIELDS)
    assert len(calls) == 2


def test_helpers_use_their_fields(monkeypatch, no_cache, img):
    fake_reply(monkeypatch, '{"answer": {}, "confidence": 0, "notes": ""}')
    assert set(count_floors([img])["answer"]) == {"stories", "how_counted"}
    assert set(read_floorplan([img])["answer"]) == {"bedrooms", "bathrooms", "has_den", "room_labels"}
    assert set(check_room([img], "bedroom with window")["answer"]) == {"room_type", "has_window", "supports_claim"}


# ---------- live (RC_LIVE=1) ----------

def _timed(fn, *a):
    t = time.monotonic()
    r = fn(*a)
    print(f"\n[live] {fn.__name__}: {time.monotonic() - t:.1f}s -> {json.dumps(r)}")
    return r


@live
def test_live_count_floors_synthetic(tmp_path):
    r = _timed(count_floors, [str(draw_elevation(tmp_path / "elev.png"))])
    assert r["status"] == "ok" and r["answer"]["stories"] == 6


@live
def test_live_read_floorplan_synthetic(tmp_path):
    r = _timed(read_floorplan, [str(draw_floorplan(tmp_path / "plan.png"))])
    assert r["status"] == "ok"
    assert r["answer"]["bedrooms"] == 1 and r["answer"]["bathrooms"] == 1 and r["answer"]["has_den"] is True


def _private_obliques() -> list[str]:
    p = os.environ.get("RC_PRIVATE_DIR")
    if not p:
        return []
    out = Path(p) / "probes" / "verify_groundtruth" / "out"
    return [str(f) for f in sorted(out.glob("oblique_full_10122224_*.jpg"))]


@live
def test_live_count_floors_private_2111_austin():
    imgs = _private_obliques()
    if not imgs:
        pytest.skip("private oblique images of 2111 Austin St not present")
    r = _timed(count_floors, imgs)
    assert r["status"] == "ok"
    # Earlier manual verification says 6 floors; reported, not asserted, since it is a real-world probe.
    print(f"[live] 2111 Austin St: model says {r['answer']['stories']} (truth 6), confidence {r['confidence']}")
