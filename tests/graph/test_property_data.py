"""Vexcel property data (tools.aerial.property_attributes) in the run and the judge: a "yes" confirms a pool, hot tub,
sport court, playground or solar claim; a "no" is only an absence signal and never makes a mismatch on its own.
Invented data only (418 Linden Grove Ave, ids 900001 / 900002); no network."""
import scripted_cli
from graph_events import check_run
from harness.graph import Toolbox, run_check, trust
from harness.graph import run as run_mod
from harness.graph.judge import judge
from rc_common import evidence

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
SNAP = {"url": "https://example.test/listing", "title": "418 Linden Grove Ave", "address_text": ADDRESS,
        "text": "Resort-style pool", "jsonld": []}
MATCH = {"status": "matched", "confidence": 0.97, "property_id": "900001", "kind": "building",
         "name": "Linden Grove", "address": ADDRESS, "city": "Houston", "state": "TX", "lat": 29.75, "lon": -95.37,
         "record": {"stories": 4, "year_built": 2012, "unit_count": 80}, "floorplans": [],
         "matterport_model_ids": [], "how": "street number + street name + zip"}


def claim(cid, value, kind="amenity", checkable=True):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "building", "label": None,
            "quote": value, "checkable": checkable}


def prop(**over):
    base = {"captured": "2025-12-19", "source": "Vexcel property data",
            "pool": {"present": True, "confidence": 0.95, "area_sqft": 1900, "kind": "in-ground", "stable": True},
            "hot_tub": {"present": False, "stable": True}, "sport_court": {"present": True, "stable": True},
            "playground": {"present": False, "stable": True}, "solar": {"present": True, "stable": True},
            "vehicles": 12, "tree_cover_pct": 18.5, "buildings": 2}
    base.update(over)
    return base


def prop_ev(p):
    return evidence("aerial", "ok", "Vexcel property data.", {"property": p})


def look(cid, presence, conf, feat="pool"):
    return evidence("aerial", "ok", "x", {"feature_check": True, "claim_ids": [cid], "feature": feat,
                                          "presence": presence, "presence_confidence": conf},
                    [{"path": "C:/x/top.jpg", "caption": "Top-down aerial photo"}])


REC_POOL = evidence("record", "ok", "Amenities.", {"property_id": "900001", "amenities": ["Pool", "Fitness center"]})
REC_GYM = evidence("record", "ok", "Amenities.", {"property_id": "900001", "amenities": ["Fitness center"]})


def soft(v):
    for word in ("fake", "lie", "scam"):
        assert word not in v["reason"].lower()
        assert word not in (v.get("confidence") or {}).get("why", "").lower()


# ---- judge --------------------------------------------------------------------------------------------

def test_pool_yes_in_property_data_confirms_and_cites_it():
    v = judge(claim("p1", "Resort-style pool"), [prop_ev(prop())])
    assert v["verdict"] == "confirmed" and v["source"] == "aerial" and v["found"] == "In Vexcel property data"
    assert "Vexcel property data shows a pool, about 1,900 sq ft." in v["reason"]
    assert "confidence" not in v
    soft(v)


def test_property_yes_wins_over_a_vision_not_seen():
    # a "yes" from either reading counts as a sighting
    v = judge(claim("p1", "Pool"), [look("p1", "not_seen", 0.9), prop_ev(prop())])
    assert v["verdict"] == "confirmed" and v["source"] == "aerial" and v["image"] == "C:/x/top.jpg"
    assert "did not see it" in v["reason"] and "counts as a sighting" in v["reason"]


def test_vision_yes_and_property_yes_both_named():
    v = judge(claim("p1", "Pool"), [look("p1", "seen", 0.9), prop_ev(prop())])
    assert v["verdict"] == "confirmed" and "saw it" in v["reason"]
    assert "Vexcel property data shows a pool, about 1,900 sq ft as well." in v["reason"]


def test_vision_yes_and_property_no_still_confirmed():
    v = judge(claim("p1", "Pool"), [look("p1", "seen", 0.9), prop_ev(prop(pool={"present": False, "stable": True}))])
    assert v["verdict"] == "confirmed" and "a sighting counts" in v["reason"]


def test_property_no_but_the_record_names_the_pool_is_confirmed():
    v = judge(claim("p1", "Resort-style pool"), [REC_POOL, prop_ev(prop(pool={"present": False, "stable": True}))])
    assert v["verdict"] == "confirmed" and v["source"] == "record"


def test_property_no_alone_keeps_the_old_verdict():
    no = prop_ev(prop(pool={"present": False, "stable": True}))
    for evs in ([], [REC_GYM]):
        c = claim("p1", "Resort-style pool")
        before, after = judge(c, evs), judge(c, evs + [no])
        assert before["verdict"] == after["verdict"] == "cant_check"
        assert "could not check" in after["reason"] and "rooftop and indoor pools" in after["reason"]
        soft(after)
    unclear = look("p1", "unclear", 0.4)
    c = claim("p1", "Pool")
    assert judge(c, [unclear])["verdict"] == judge(c, [unclear, no])["verdict"] == "cant_check"


def test_property_no_with_vision_not_seen_stays_close_and_raises_the_aerial_weight_a_little():
    c = claim("p1", "Pool")
    alone = judge(c, [REC_GYM, look("p1", "not_seen", 0.9)])
    both = judge(c, [REC_GYM, look("p1", "not_seen", 0.9), prop_ev(prop(pool={"present": False, "stable": True}))])
    assert alone["verdict"] == both["verdict"] == "close"  # never a hard mismatch
    w_alone, w_both = alone["confidence"]["sources"][0]["weight"], both["confidence"]["sources"][0]["weight"]
    assert w_both > w_alone
    assert both["confidence"]["score"] >= alone["confidence"]["score"]
    assert both["confidence"]["band"] != "high"
    assert any(s["says"] == "not in property data" for s in both["confidence"]["sources"])
    assert "Vexcel property data does not show it either" in both["confidence"]["why"]
    soft(both)


def test_agreement_and_disagreement_factors():
    c = claim("p1", "Pool")
    base = look("p1", "not_seen", 0.9)
    agree = {**base, "observed": {**base["observed"], "property_present": False}}
    differ = {**base, "observed": {**base["observed"], "property_present": True}}
    assert trust.weight(differ, c) < trust.weight(base, c) < trust.weight(agree, c)
    assert "property_agrees" in trust.quality(agree, c) and "property_differs" in trust.quality(differ, c)


def test_unstable_property_data_weighs_less():
    c = claim("p1", "Pool")
    stable = trust.property_evidence(None, "pool", {"present": True, "confidence": 0.9, "stable": True})
    unstable = trust.property_evidence(None, "pool", {"present": True, "confidence": 0.9, "stable": False})
    assert trust.weight(unstable, c) < trust.weight(stable, c)
    assert "unstable" in trust.quality(unstable, c)
    low = trust.property_evidence(None, "pool", {"present": True, "confidence": 0.5, "stable": True})
    assert trust.weight(low, c) < trust.weight(stable, c)  # the pool's own confidence scales it
    absent = trust.property_evidence(None, "pool", {"present": False, "stable": True})
    assert "absence" in trust.quality(absent, c)
    v = judge(c, [prop_ev(prop(pool={"present": True, "stable": False}))])
    assert v["verdict"] == "confirmed" and "less certain" in v["reason"]


def test_solar_hot_tub_court_playground():
    p = prop_ev(prop())
    v = judge(claim("s1", "Solar panels on the roof", kind="other"), [p])
    assert v["verdict"] == "confirmed" and "Vexcel property data shows solar panels." in v["reason"]
    assert trust.kind_key(claim("s1", "Solar panels")) == "solar"
    assert trust.relevance("solar", "aerial")[0] > 0.8 and trust.relevance("solar", "scan")[0] == 0
    assert judge(claim("t1", "Tennis court"), [p])["verdict"] == "confirmed"
    assert judge(claim("h1", "Hot tub"), [p])["verdict"] == "cant_check"  # a "no": absence only
    assert trust.kind_key(claim("h1", "Hot tub")) == trust.OUTDOOR
    assert judge(claim("g1", "Playground"), [p])["verdict"] == "cant_check"


def test_vision_only_features_ignore_property_data():
    for words in ("Rooftop deck", "Garage parking", "Courtyard", "Dog run", "Pet spa", "Pool table"):
        assert trust.property_key(claim("r1", words)) is None, words
    assert trust.property_key(claim("r1", "Pool and spa")) == "pool"
    assert trust.property_key(claim("r1", "Spa")) == "hot_tub"
    v = judge(claim("r1", "Rooftop deck"), [prop_ev(prop())])
    assert v["verdict"] == "cant_check" and "Vexcel property data" not in v["reason"]


# ---- the run ------------------------------------------------------------------------------------------

def _tb(prop_fn, find_features=None, log=None):
    calls = log if log is not None else []

    def fetch(lat, lon):
        calls.append((lat, lon))
        return prop_fn(lat, lon)
    over = {"extract_claims": lambda s, use_model=True: [claim("p1", "Resort-style pool")],
            "match_address": lambda *a, **k: dict(MATCH)}
    if find_features:
        over["find_features"] = find_features
    tb = Toolbox.fake(**over)
    tb.property_attributes = fetch
    return tb


def _not_seen(paths, features, setting="aerial"):
    return {"status": "ok", "confidence": 0.9, "notes": "synthetic",
            "answer": {k: {"presence": "not_seen", "confidence": 0.9, "image": 1} for k in features}}


def _go(tb):
    events = []
    done = run_check(SNAP, events.append, tb)
    assert events[-1] == done
    check_run(events)
    return events, done


def test_run_saves_the_property_data_and_the_judge_uses_it(monkeypatch):
    scripted_cli.install(monkeypatch)
    log = []
    events, done = _go(_tb(lambda lat, lon: prop(), _not_seen, log))
    assert log == [(29.75, -95.37)]  # once per run, though both aerial checks ask for it
    saved = [e["evidence"] for e in events if e["type"] == "evidence" and "property" in e["evidence"]["observed"]]
    assert len(saved) == 1 and saved[0]["source"] == "aerial" and saved[0]["status"] == "ok"
    assert saved[0]["observed"]["property"]["pool"]["area_sqft"] == 1900
    assert "a pool (about 1,900 sq ft)" in saved[0]["summary"]
    v = next(e["verdict"] for e in events if e["type"] == "verdict")
    assert v["verdict"] == "confirmed" and "Vexcel property data shows a pool" in v["reason"]


def test_run_completes_when_the_fetch_fails(monkeypatch):
    scripted_cli.install(monkeypatch)

    def boom(lat, lon):
        raise RuntimeError("no service")
    for fn in (boom, lambda lat, lon: None):
        events, done = _go(_tb(fn, _not_seen))
        assert not [e for e in events if e["type"] == "evidence" and "property" in e["evidence"]["observed"]]
        v = next(e["verdict"] for e in events if e["type"] == "verdict")
        assert v["verdict"] == "close"  # as before: the photo look alone, a soft near-miss
        assert done["type"] == "done"


def test_always_run_path_fetches_it_too():
    log = []
    run = run_mod.Run(SNAP, lambda e: None, _tb(lambda lat, lon: prop(), log=log))
    run.read_claims()
    run.match_listing()
    run.run_always()
    assert log == [(29.75, -95.37)]
    assert any("property" in e["observed"] for e in run.evidence.values())


def test_real_toolbox_loads_the_real_fetch_and_the_fake_one_does_not():
    import tools.aerial
    from tools.aerial import aerial as aerial_mod
    assert run_mod._property_fn(Toolbox.fake()) is None
    assert run_mod._property_fn(Toolbox.fake(aerial_images=aerial_mod.aerial_images)) is tools.aerial.property_attributes
