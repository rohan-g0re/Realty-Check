"""Small validator for the CONTRACT shapes (docs/CONTRACT.md section 2). Raises AssertionError on a bad shape."""
from __future__ import annotations

KINDS = {"bedrooms", "bathrooms", "area_sqft", "stories", "year_built", "unit_count", "amenity", "parking",
         "laundry", "view", "other"}
AGENTS = {"orchestrator", "claims", "matcher", "inside", "outside", "paper", "judge"}
VERDICTS = {"confirmed", "contradicted", "close", "cant_check"}


def _keys(d, keys, what):
    assert isinstance(d, dict), f"{what} is not a dict: {d!r}"
    missing = [k for k in keys if k not in d]
    assert not missing, f"{what} missing {missing}: {d!r}"


def check_claim(c):
    _keys(c, ["id", "kind", "value", "unit", "scope", "label", "quote", "checkable"], "claim")
    assert c["kind"] in KINDS and c["scope"] in ("unit", "building") and isinstance(c["checkable"], bool)
    assert isinstance(c["quote"], str) and len(c["quote"]) <= 120


def check_match(m):
    _keys(m, ["status", "confidence", "how"], "match")
    assert m["status"] in ("matched", "no_match")
    if m["status"] == "matched":
        _keys(m, ["property_id", "name", "address", "lat", "lon", "record", "floorplans", "matterport_model_ids"], "match")


def check_evidence(e):
    _keys(e, ["source", "status", "summary", "observed", "images", "detail"], "evidence")
    assert e["source"] in ("record", "scan", "aerial", "county"), e["source"]
    assert e["status"] in ("ok", "no_evidence", "error"), e["status"]
    assert isinstance(e["summary"], str) and isinstance(e["observed"], dict) and isinstance(e["images"], list)
    for im in e["images"]:
        _keys(im, ["path", "caption"], "image")


def check_verdict(v):
    _keys(v, ["claim_id", "verdict", "claimed", "found", "source", "reason", "image"], "verdict")
    assert v["verdict"] in VERDICTS
    assert v["source"] in (None, "record", "scan", "aerial", "county")
    assert isinstance(v["reason"], str) and v["reason"]


TOPICS = {"Building", "Homes & floor plans", "Rent", "Rules", "Around it", "Records"}


def check_fact(f):
    _keys(f, ["id", "topic", "label", "value", "source", "note", "image"], "fact")
    assert f["topic"] in TOPICS and f["source"] in ("record", "scan", "aerial", "county")
    assert isinstance(f["label"], str) and f["label"] and isinstance(f["value"], str) and f["value"]
    assert isinstance(f["note"], str) and (f["image"] is None or isinstance(f["image"], str))


def check_event(ev):
    t = ev.get("type")
    if t == "run_started":
        _keys(ev, ["run_id", "address"], t)
    elif t == "step":
        _keys(ev, ["agent", "text"], t)
        assert ev["agent"] in AGENTS, ev["agent"]
        assert isinstance(ev["text"], str) and ev["text"].strip() and "\n" not in ev["text"]
    elif t == "claims":
        for c in ev["claims"]:
            check_claim(c)
    elif t == "match":
        check_match(ev["match"])
    elif t == "evidence":
        check_evidence(ev["evidence"])
    elif t == "verdict":
        check_verdict(ev["verdict"])
    elif t == "done":
        _keys(ev, ["run_id", "counts", "headline", "report_url"], t)
        _keys(ev["counts"], sorted(VERDICTS), "counts")
        assert ev["report_url"] == f"/report/{ev['run_id']}"
        assert isinstance(ev["headline"], str) and ev["headline"]
    elif t == "fill_in":
        ids = [check_fact(f) or f["id"] for f in ev["facts"]]
        assert len(ids) == len(set(ids)), "fact ids repeat"
    elif t == "error":
        _keys(ev, ["message"], t)
    else:
        raise AssertionError(f"unknown event type {t!r}")


def check_run(events):
    """Every event valid; order run_started, claims, match, ..., one verdict per claim, done last."""
    for ev in events:
        check_event(ev)
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "done" and types.count("done") == 1
    assert types.index("claims") < types.index("match")
    claims = next(e["claims"] for e in events if e["type"] == "claims")
    verdicts = [e["verdict"] for e in events if e["type"] == "verdict"]
    assert [v["claim_id"] for v in verdicts] == [c["id"] for c in claims]
    first_verdict = types.index("verdict") if verdicts else len(types) - 1
    assert all(i > types.index("match") and i < first_verdict for i, t in enumerate(types) if t == "evidence")
    assert all(t in ("verdict", "fill_in", "done", "step") for t in types[first_verdict:])
    if "fill_in" in types:  # after the verdicts, before done
        fi = types.index("fill_in")
        assert types.count("fill_in") == 1 and all(i < fi for i, t in enumerate(types) if t in ("verdict", "evidence"))
    done = events[-1]
    assert sum(done["counts"].values()) == len(claims)
    for k, n in done["counts"].items():
        assert n == sum(v["verdict"] == k for v in verdicts)
    return done
