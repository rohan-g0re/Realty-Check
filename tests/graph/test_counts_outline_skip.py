"""Pool / hot tub counts from Vexcel property data, the outlined top-down photo as evidence, and the vision look
skipped when property data already answers every outdoor amenity claim.
Invented data only (418 Linden Grove Ave, ids 900001 / 900002); no network."""
import importlib

import pytest

import scripted_cli
from graph_events import check_run
from harness.graph import Toolbox, run_check, trust
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence

judge_mod = importlib.import_module("harness.graph.judge")

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
SNAP = {"url": "https://example.test/listing", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
        "text": "Two resort-style pools", "jsonld": []}
MATCH = {"status": "matched", "confidence": 0.97, "property_id": "900001", "kind": "building",
         "name": "Linden Grove", "address": ADDRESS, "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
         "record": {"stories": 4, "year_built": 2012, "unit_count": 80}, "floorplans": [],
         "matterport_model_ids": [], "how": "street number + street name + zip"}
OUTLINED = "C:/x/aerial/900002/topdown_h60_outlined_v1.jpg"


def claim(cid, value, kind="amenity"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "building", "label": None,
            "quote": value, "checkable": True}


def prop(**over):
    base = {"captured": "2025-12-19", "source": "Vexcel property data",
            "pool": {"present": True, "confidence": 0.95, "area_sqft": 1900, "kind": "in-ground", "stable": True},
            "hot_tub": {"present": False, "stable": True}, "sport_court": {"present": True, "stable": True},
            "playground": {"present": True, "stable": True}, "solar": {"present": True, "stable": True},
            "vehicles": 12, "tree_cover_pct": 18.5, "buildings": 2}
    base.update(over)
    return base


def pool(count=None, stable=True, present=True):
    p = {"present": present, "confidence": 0.95, "area_sqft": 1900, "kind": "in-ground", "stable": stable}
    if count is not None:
        p["count"] = count
    return p


def prop_ev(p):
    return evidence("aerial", "ok", "Vexcel property data.", {"property": p})


def outline_ev(feats=("pool",)):
    return evidence("aerial", "ok", "Top-down Vexcel aerial photo with the pool detected near the address outlined.",
                    {"feature_check": True, "outlined": list(feats)},
                    [{"path": OUTLINED, "caption": "Top-down Vexcel aerial photo: pool detected near the address, "
                                                   "outlined"},
                     {"path": "C:/x/top.jpg", "caption": "Top-down Vexcel aerial photo"}])


def look(cid, presence, conf, feat="pool"):
    return evidence("aerial", "ok", "x", {"feature_check": True, "claim_ids": [cid], "feature": feat,
                                          "presence": presence, "presence_confidence": conf},
                    [{"path": "C:/x/oblique.jpg", "caption": "Angled Vexcel aerial view"}])


PHOTOS = evidence("aerial", "ok", "Top-down Vexcel aerial photo.", {},
                  [{"path": "C:/x/top.jpg", "caption": "Top-down Vexcel aerial photo"}])
REC_POOL = evidence("record", "ok", "Amenities.", {"property_id": "900001", "amenities": ["Pool", "Fitness center"]})


def soft(v):
    for word in ("fake", "lie", "scam", "fraud"):
        assert word not in v["reason"].lower()
        assert word not in (v.get("confidence") or {}).get("why", "").lower()


# ---- 1. number claims vs Vexcel counts -----------------------------------------------------------------

@pytest.mark.parametrize("text,key,n", [
    ("2 pools", "pool", 2), ("Two resort-style pools", "pool", 2), ("two sparkling swimming pools", "pool", 2),
    ("3 hot tubs", "hot_tub", 3), ("Pool", "pool", None), ("1 pool", "pool", None), ("2 pool tables", "pool", None),
    ("3 bedroom with pools", "pool", None), ("24-hour pools", "pool", None), ("Two pools", "hot_tub", None),
])
def test_claimed_count(text, key, n):
    assert judge_mod.claimed_count(claim("p1", text), key) == n


def test_plural_pools_and_spas_are_property_claims():
    assert trust.property_key(claim("p1", "Two resort-style pools")) == "pool"
    assert trust.property_key(claim("p1", "3 hot tubs")) == "hot_tub"
    assert trust.property_key(claim("p1", "Two spas")) == "hot_tub"
    assert trust.property_key(claim("p1", "2 pool tables")) is None


@pytest.mark.parametrize("count", [2, 3])
def test_count_at_or_above_the_claim_confirms(count):
    # a higher count is never a contradiction: the 120 m survey can take in a neighbour's pool
    v = judge(claim("p1", "Two resort-style pools"), [prop_ev(prop(pool=pool(count)))])
    assert v["verdict"] == "confirmed" and v["source"] == "aerial"
    assert f"Vexcel property data shows {count} pools." in v["reason"]
    assert "confidence" not in v
    soft(v)


def test_lower_stable_count_is_close_never_a_mismatch():
    for evs in ([prop_ev(prop(pool=pool(1)))],
                [prop_ev(prop(pool=pool(1))), look("p1", "seen", 0.95)],  # a sighting does not count pools
                [prop_ev(prop(pool=pool(1))), look("p1", "not_seen", 0.9)]):
        v = judge(claim("p1", "Two resort-style pools"), evs)
        assert v["verdict"] == "close" and v["source"] == "aerial"
        assert "Vexcel property data shows 1 pool; it can miss rooftop and indoor pools." in v["reason"]
        assert v["found"] == "1 pool in Vexcel property data"
        conf = v["confidence"]
        assert conf["band"] in ("low", "medium") and conf["score"] <= trust.COUNT_SCORE_MAX
        assert conf["decided_by"] == "aerial" and "rooftop and indoor" in conf["why"]
        soft(v)


def test_lower_hot_tub_count_is_close():
    p = prop(hot_tub={"present": True, "stable": True, "count": 1})
    v = judge(claim("h1", "3 hot tubs"), [prop_ev(p)])
    assert v["verdict"] == "close"
    assert "Vexcel property data shows 1 hot tub; it can miss rooftop and indoor ones." in v["reason"]
    v = judge(claim("h1", "3 hot tubs"), [prop_ev(prop(hot_tub={"present": True, "stable": True, "count": 4}))])
    assert v["verdict"] == "confirmed" and "Vexcel property data shows 4 hot tubs." in v["reason"]


def test_unstable_or_missing_count_falls_back_to_presence():
    c = claim("p1", "Two resort-style pools")
    v = judge(c, [prop_ev(prop(pool=pool(1, stable=False)))])
    assert v["verdict"] == "confirmed" and "less certain" in v["reason"] and "1 pool" not in v["reason"]
    v = judge(c, [prop_ev(prop(pool=pool(None)))])
    assert v["verdict"] == "confirmed" and "Vexcel property data shows a pool, about 1,900 sq ft." in v["reason"]


def test_one_pool_claim_ignores_the_count():
    v = judge(claim("p1", "Resort-style pool"), [prop_ev(prop(pool=pool(3)))])
    assert v["verdict"] == "confirmed" and "Vexcel property data shows a pool, about 1,900 sq ft." in v["reason"]


def test_the_building_record_naming_it_still_decides():
    v = judge(claim("p1", "Two resort-style pools"), [REC_POOL, prop_ev(prop(pool=pool(1)))])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_sport_courts_stay_presence_only():
    v = judge(claim("t1", "Two tennis courts"), [prop_ev(prop())])
    assert v["verdict"] == "confirmed" and "Vexcel property data shows a sport court." in v["reason"]


def test_count_weight_is_reduced():
    c = claim("p1", "Two pools")
    plain = trust.property_evidence(None, "pool", pool())
    counted = trust.property_evidence(None, "pool", pool())
    counted["observed"]["count"] = 1
    assert "aerial_count" in trust.quality(counted, c) and trust.weight(counted, c) < trust.weight(plain, c)


# ---- 2. outlined photo in the judge ---------------------------------------------------------------------

def test_outlined_photo_is_the_proof_for_a_present_feature_it_boxed():
    v = judge(claim("p1", "Resort-style pool"), [PHOTOS, outline_ev(), prop_ev(prop())])
    assert v["verdict"] == "confirmed" and v["image"] == OUTLINED
    v = judge(claim("p1", "Resort-style pool"), [PHOTOS, outline_ev(), look("p1", "seen", 0.95)])
    assert v["verdict"] == "confirmed" and v["image"] == OUTLINED
    v = judge(claim("p1", "Two resort-style pools"), [PHOTOS, outline_ev(), prop_ev(prop(pool=pool(1)))])
    assert v["verdict"] == "close" and v["image"] == OUTLINED
    v = judge(claim("s1", "Solar panels", kind="other"), [PHOTOS, outline_ev(["pool", "solar"]), prop_ev(prop())])
    assert v["verdict"] == "confirmed" and v["image"] == OUTLINED


def test_outlined_photo_never_on_other_features_or_absent_ones():
    evs = [outline_ev(), PHOTOS, prop_ev(prop())]  # the outlined Evidence first: it must still be skipped
    v = judge(claim("g1", "Playground"), evs)
    assert v["verdict"] == "confirmed" and v["image"] == "C:/x/top.jpg"
    v = judge(claim("t1", "Tennis court"), evs)  # present, but the detection boxed no court
    assert v["verdict"] == "confirmed" and v["image"] == "C:/x/top.jpg"
    no = [outline_ev(), PHOTOS, prop_ev(prop(pool=pool(present=False))), look("p1", "not_seen", 0.9)]
    v = judge(claim("p1", "Pool"), no)
    assert v["verdict"] == "cant_check" and v["image"] != OUTLINED  # not seen: absence, so couldn't check


# ---- the run --------------------------------------------------------------------------------------------

def _tb(claims, prop_fn=lambda lat, lon: prop(), outline=None, looks=None, outline_log=None):
    looks = looks if looks is not None else []
    log = outline_log if outline_log is not None else []

    def find_features(paths, features, setting="aerial"):
        looks.append(sorted(features))
        return {"status": "ok", "confidence": 0.9, "notes": "synthetic",
                "answer": {k: {"presence": "seen", "confidence": 0.95, "image": 1} for k in features}}
    tb = Toolbox.fake(extract_claims=lambda s, use_model=True: [dict(c) for c in claims],
                      match_address=lambda *a, **k: dict(MATCH), find_features=find_features)
    tb.property_attributes = prop_fn
    if outline is not None:
        def outlined(lat, lon):
            log.append((lat, lon))
            if isinstance(outline, Exception):
                raise outline
            return outline
        tb.outlined_image = outlined
    return tb


def _outline(caption="Top-down Vexcel aerial photo (2026-01-02), detected pool outlined"):
    return {"path": OUTLINED, "caption": caption}


def _run(tb):
    run = run_mod.Run(SNAP, lambda e: None, tb)
    run.read_claims()
    run.match_listing()
    return run


def _outlined_evs(run):
    return [e for e in run.evidence.values() if (e["observed"] or {}).get("outlined")]


def test_outlined_photo_fetched_once_and_attached_only_to_matching_claims():
    log, looks = [], []
    claims = [claim("p1", "Resort-style pool"), claim("r1", "Rooftop deck"), claim("g1", "Playground")]
    run = _run(_tb(claims, outline=_outline(), looks=looks, outline_log=log))
    run.measure_building()
    run.features("aerial", "outside")
    assert log == [(29.75, -95.37)]  # once per run, though both aerial checks start it
    (ev,) = _outlined_evs(run)
    assert ev["observed"]["outlined"] == ["pool"] and ev["observed"]["feature_check"] is True
    assert "claim_ids" not in ev["observed"]
    first, second = ev["images"]
    assert set(first) == {"path", "caption"} and first["path"] == OUTLINED
    assert "detected near the address" in first["caption"] and "(2026-01-02)" in first["caption"]
    assert second["caption"].startswith("Top-down")  # the plain photo next to it
    assert looks == [["playground", "pool", "rooftop_deck"]]  # the rooftop deck still needs the look
    by_feat = {e["observed"].get("feature"): e for e in run.evidence.values() if e["observed"].get("claim_ids")}
    assert any(im["path"] == OUTLINED for im in by_feat["pool"]["images"])
    assert by_feat["pool"]["images"][0]["path"] != OUTLINED  # next to the plain photos, not in their place
    for other in ("rooftop_deck", "playground"):
        assert not any(im["path"] == OUTLINED for im in by_feat[other]["images"])
    verdicts = {v["claim_id"]: v for v in run.judge_all("test")}
    assert verdicts["p1"]["image"] == OUTLINED
    assert verdicts["r1"]["image"] != OUTLINED and verdicts["g1"]["image"] != OUTLINED


@pytest.mark.parametrize("outline", [RuntimeError("detect failed"), None, {"path": "", "caption": "x"},
                                     {"path": OUTLINED, "caption": "Top-down Vexcel aerial photo, nothing named"}])
def test_outline_failure_or_nothing_is_tolerated(outline):
    log = []
    tb = _tb([claim("p1", "Resort-style pool")], outline=outline, outline_log=log)
    if outline is None:
        tb.outlined_image = lambda lat, lon: log.append(1)  # returns None
    run = _run(tb)
    run.measure_building()
    run.features("aerial", "outside")
    assert len(log) == 1 and not _outlined_evs(run)
    (v,) = run.judge_all("test")
    assert v["verdict"] == "confirmed" and v["image"] != OUTLINED


def test_no_outline_call_without_a_pool_court_or_solar_claim():
    log = []
    run = _run(_tb([claim("r1", "Rooftop deck"), claim("h1", "Hot tub")], outline=_outline(), outline_log=log))
    run.measure_building()
    run.features("aerial", "outside")
    assert log == [] and not _outlined_evs(run)


def test_real_toolbox_loads_outlined_image_and_the_fake_one_does_not():
    import tools.aerial
    from tools.aerial import aerial as aerial_mod
    assert run_mod._outlined_fn(Toolbox.fake()) is None
    assert run_mod._outlined_fn(Toolbox.fake(aerial_images=aerial_mod.aerial_images)) is tools.aerial.outlined_image


def test_outlined_features_from_the_caption():
    f = run_mod._outlined_features
    assert f({"caption": "Top-down Vexcel aerial photo (2026-01-02), detected pool, sport court, solar panels "
                         "outlined"}) == ["pool", "sport_court", "solar"]
    assert f({"caption": "Top-down Vexcel aerial photo, detected solar panels outlined"}) == ["solar"]
    assert f({"caption": "x", "features": ["solar", "car"]}) == ["solar"]
    assert f({"caption": "no names"}) == []


# ---- 3. the vision look skipped when property data already answers --------------------------------------

def test_vision_skipped_when_every_aerial_claim_is_answered():
    looks = []
    run = _run(_tb([claim("p1", "Resort-style pool"), claim("t1", "Tennis court"), claim("s1", "Solar", "other")],
                   looks=looks))
    out = run.features("aerial", "outside")
    assert looks == [] and run.vision_calls == 0 and run.photo_look_skipped
    assert "not needed" in out
    note = [e for e in run.evidence.values() if e["observed"].get("photo_look") == "not_needed"]
    assert len(note) == 1 and "pool and sport court" in note[0]["summary"]
    assert "claim_ids" not in note[0]["observed"]
    verdicts = {v["claim_id"]: v for v in run.judge_all("test")}
    assert verdicts["p1"]["verdict"] == verdicts["t1"]["verdict"] == "confirmed"


@pytest.mark.parametrize("claims,p", [
    ([claim("p1", "Resort-style pool"), claim("r1", "Rooftop deck")], prop()),       # rooftop deck: not in the data
    ([claim("p1", "Resort-style pool"), claim("c1", "Garage parking", "parking")], prop()),
    ([claim("p1", "Resort-style pool")], prop(pool=pool(present=False))),            # data says absent
    ([claim("p1", "Resort-style pool")], prop(pool=pool(stable=False))),             # unstable
    ([claim("p1", "Resort-style pool"), claim("g1", "Playground")],
     prop(playground={"present": True, "stable": False})),
    ([claim("p1", "Resort-style pool")], None),                                      # no property data
])
def test_vision_runs_when_any_aerial_claim_is_unanswered(claims, p):
    looks = []
    run = _run(_tb(claims, prop_fn=lambda lat, lon: p, looks=looks))
    run.features("aerial", "outside")
    assert len(looks) == 1 and not run.photo_look_skipped
    assert not [e for e in run.evidence.values() if e["observed"].get("photo_look") == "not_needed"]


def test_vision_runs_when_the_property_fetch_fails():
    looks = []

    def boom(lat, lon):
        raise RuntimeError("no service")
    run = _run(_tb([claim("p1", "Resort-style pool")], prop_fn=boom, looks=looks))
    run.features("aerial", "outside")
    assert len(looks) == 1


def test_whole_run_skips_the_look_and_shows_the_outline(monkeypatch):
    scripted_cli.install(monkeypatch)
    looks, log = [], []
    tb = _tb([claim("p1", "Resort-style pool"), claim("p2", "Two resort-style pools")],
             prop_fn=lambda lat, lon: prop(pool=pool(2)), outline=_outline(), looks=looks, outline_log=log)
    events = []
    done = run_check(SNAP, events.append, tb)
    check_run(events)
    assert done["type"] == "done" and looks == [] and len(log) == 1
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert any("photo look was not needed" in s for s in steps)
    outlined = [e["evidence"] for e in events if e["type"] == "evidence" and e["evidence"]["observed"].get("outlined")]
    assert len(outlined) == 1 and outlined[0]["images"][0]["path"] == OUTLINED
    v1, v2 = [e["verdict"] for e in events if e["type"] == "verdict"]
    assert v1["verdict"] == v2["verdict"] == "confirmed" and v1["image"] == v2["image"] == OUTLINED
    assert "Vexcel property data shows 2 pools." in v2["reason"]
