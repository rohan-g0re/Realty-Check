"""Live (cached) tests for tools.paper. Skip when RC_PRIVATE_DIR is unset (the cache lives there).
Run: RC_PRIVATE_DIR=... .venv/Scripts/python.exe -m pytest tests/paper -q
First Houston run builds the HCAD index once (~45 s); later runs read the cache."""
import json
import os

import pytest

if not os.environ.get("RC_PRIVATE_DIR"):
    pytest.skip("RC_PRIVATE_DIR not set", allow_module_level=True)

from tools.paper import county_record, geocode
from tools.paper import net
from tests.private_golden import golden

PRINCETON = "100 Nassau St, Princeton, NJ 08542"


@pytest.fixture(scope="module")
def houston():
    """Private Houston demo building (address, coordinates, county facts) from the private folder."""
    return golden("houston")


@pytest.fixture(scope="module")
def la():
    """Private Los Angeles demo building, verified against the LA County assessor."""
    return golden("la")


def _ll(g):
    return g["lat"], g["lon"]


def _shape(ev):
    assert set(ev) == {"source", "status", "summary", "observed", "images", "detail"}
    assert ev["source"] == "county"
    json.dumps(ev)


def test_geocode_houston(houston):
    g = geocode(houston["address"])
    assert g and abs(g["lat"] - houston["lat"]) < 0.01 and abs(g["lon"] - houston["lon"]) < 0.01
    assert houston["street_word"].upper() in g["matched_address"].upper()


def test_houston_county_record(houston):
    ev = county_record(houston["address"], *_ll(houston), "Houston")
    _shape(ev)
    assert ev["status"] == "ok", ev
    o = ev["observed"]
    assert o["stories"] == houston["stories"]
    assert o["parcel_id"] == houston["parcel_id"]
    assert o["year_built"] == houston["year_built"] and o["unit_count"] == houston["unit_count"]
    assert o["source_url"].startswith("https://") and f"{houston['stories']} floors" in ev["summary"]


def test_houston_from_geocode_only(houston):
    ev = county_record(houston["address"], None, None, "Houston")
    assert ev["status"] == "ok" and ev["observed"]["stories"] == houston["stories"], ev


def test_la_county_record(la):
    ev = county_record(la["address"], *_ll(la), "Los Angeles")
    _shape(ev)
    assert ev["status"] == "ok", ev
    o = ev["observed"]
    assert o["parcel_id"] == la["parcel_id"]
    assert o["year_built"] == la["year_built"] and o["unit_count"] == la["unit_count"]
    assert o.get("stories") == la["stories"]  # City of LA Certificate of Occupancy of_stories


def test_nyc_pluto():
    ev = county_record("350 5th Ave, New York, NY 10118", 40.7484514, -73.9857117, "New York")
    _shape(ev)
    assert ev["status"] == "ok", ev
    assert ev["observed"]["stories"] == 102 and ev["observed"]["year_built"] == 1931


def test_princeton_no_evidence():
    for ev in (county_record(PRINCETON, 40.3487, -74.6593, "Princeton"),
               county_record(PRINCETON, None, None, "Princeton")):
        _shape(ev)
        assert ev["status"] == "no_evidence"
    assert ev["summary"] == "No county source wired for this location yet."


def test_second_call_zero_network(monkeypatch, houston, la):
    county_record(houston["address"], *_ll(houston), "Houston")
    county_record(la["address"], *_ll(la), "Los Angeles")
    geocode(houston["address"])

    def boom(*a, **k):
        raise AssertionError("network call on a cached request")
    monkeypatch.setattr(net, "get", boom)
    monkeypatch.setattr(net, "head_size", boom)
    before = net.calls
    assert county_record(houston["address"], *_ll(houston), "Houston")["status"] == "ok"
    assert county_record(la["address"], *_ll(la), "Los Angeles")["status"] == "ok"
    assert geocode(houston["address"])
    assert net.calls == before


def test_network_failure_is_error_not_raise(monkeypatch):
    def down(*a, **k):
        raise ConnectionError("simulated outage")
    monkeypatch.setattr(net, "get", down)
    ev = county_record("999 Nowhere St, Houston, TX", 29.70123, -95.40123, "Houston")
    _shape(ev)
    assert ev["status"] == "error"
    monkeypatch.undo()
