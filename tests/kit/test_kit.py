"""tools.kit: address matching against the CoStar kit index. Needs RC_PRIVATE_DIR (skips otherwise).
Kit-backed tests hold no real addresses: they pick a property from the private index at run time."""
import json
import os
import re
import time

import pytest

from tests.private_golden import golden
from tools.kit.normalize import parse, street_key

needs_kit = pytest.mark.skipif(not os.environ.get("RC_PRIVATE_DIR"), reason="RC_PRIVATE_DIR not set")
ZIP_HOW = "street number + street name + zip"


# ---- pure normalization (no kit needed) ----

def test_parse_full_address():
    q = parse("418 LINDEN GROVE AVE, HOUSTON, TX 77006-xxxx", ["houston"])
    assert (q["num_lo"], q["streets"][0], q["zip"], q["city"]) == (418, "linden", "77006", "houston")


def test_parse_no_commas_and_unit():
    q = parse("1820 Marrow Street #412 Houston TX", ["houston", "los angeles"])
    assert (q["num_lo"], q["streets"][0], q["zip"], q["city"], q["unit"]) == (1820, "marrow", None, "houston", "412")


def test_parse_unit_first_does_not_become_zip():
    q = parse("Unit 141, 14020 Quillfeather Road, Houston TX", ["houston"])
    assert (q["num_lo"], q["zip"], q["unit"]) == (14020, None, "141")


def test_street_key_peels_direction_and_type():
    assert street_key("S Corbel Blvd") == "corbel"
    assert street_key("Park Row") == "park"
    assert street_key("First Street") == "1st"


# ---- against the kit ----

# "<num> <Word> St, <City>, <ST> <zip>": lets the test build Street / St. / no-comma variants.
_ADDR = re.compile(r"^(\d+) ([A-Za-z]+) St, ([^,]+), ([A-Z]{2}) (\d{5})$")


@pytest.fixture(scope="module")
def kit():
    if not os.environ.get("RC_PRIVATE_DIR"):
        pytest.skip("RC_PRIVATE_DIR not set")
    from tools.kit import match_address, record_evidence
    match_address("warm up")  # loads (or builds) the index once
    return match_address, record_evidence


@pytest.fixture(scope="module")
def prop(kit):
    """First kit building with a plain '<num> <Name> St' address, a zip and a story count that matches itself
    by address and by its own (rounded) coordinates."""
    from tools.kit.build_index import index_path
    match_address = kit[0]
    for p in json.loads(index_path().read_text(encoding="utf-8"))["properties"]:
        if p["kind"] != "building" or (p.get("record") or {}).get("stories") is None or p["lat"] is None:
            continue
        if not _ADDR.match(p["address"] or ""):
            continue
        m = match_address(p["address"])
        if m["status"] != "matched" or m["property_id"] != p["property_id"] or m["how"] != ZIP_HOW:
            continue
        near = match_address("somewhere downtown", lat=round(p["lat"], 4), lon=round(p["lon"], 4))
        if near.get("property_id") == p["property_id"]:  # no closer kit building shadows it
            return p
    pytest.skip("no suitable kit property found")


@needs_kit
def test_property_matches_with_record(kit, prop):
    match_address, record_evidence = kit
    m = match_address(prop["address"])
    assert m["status"] == "matched" and m["property_id"] == prop["property_id"]
    assert m["record"]["stories"] == prop["record"]["stories"]
    assert m["how"] == ZIP_HOW and m["confidence"] >= 0.9
    ev = record_evidence(m)
    assert ev["source"] == "record" and ev["status"] == "ok" and ev["observed"]["stories"] == prop["record"]["stories"]
    assert set(ev["observed"]) <= {"stories", "year_built", "unit_count", "building_sf"}
    assert all(v is not None for v in ev["observed"].values())


def _variants(address: str) -> list[str]:
    num, name, city, st, z = _ADDR.match(address).groups()
    return [f"{num} {name} Street {city} {st}",
            f"{num} {name} St, {city}, {st} {z}-8909".upper(),
            f"{num} {name} St, {city}, {st} {z}-xxxx".upper(),
            f"{num} {name} St #412, {city}, {st} {z}",
            f"{num} {name} St. Apt 412, {city}, {st} {z}"]


@needs_kit
def test_variants_match_same_property(kit, prop):
    for text in _variants(prop["address"]):
        m = kit[0](text)
        assert m["status"] == "matched" and m["property_id"] == prop["property_id"], (text, m)


@needs_kit
def test_plan_has_code_and_beds(kit):
    g = golden("kit_plan")
    m = kit[0](g["address"])
    assert m["status"] == "matched" and m["property_id"] == g["property_id"]
    plan = [f for f in m["floorplans"] if g["plan_code"] in f["name"].split()]
    assert plan and plan[0]["name"] == g["plan_name"] and plan[0]["beds"] == g["beds"]
    assert plan[0]["matterport_model_id"] in m["matterport_model_ids"]


@needs_kit
def test_no_match_never_guesses(kit):
    match_address, record_evidence = kit
    m = match_address("1 Nowhere Rd, Princeton, NJ")
    assert m["status"] == "no_match" and m["confidence"] == 0 and m["how"]
    assert record_evidence(m)["status"] == "no_evidence"


@needs_kit
def test_latlon_fallback_within_60m(kit, prop):
    m = kit[0]("somewhere downtown", lat=round(prop["lat"], 4), lon=round(prop["lon"], 4))
    assert m["status"] == "matched" and m["property_id"] == prop["property_id"] and m["how"].startswith("nearest")
    assert kit[0]("somewhere", lat=40.35, lon=-74.66)["status"] == "no_match"


@needs_kit
def test_lookup_is_fast(kit, prop):
    t = time.perf_counter()
    kit[0](prop["address"].split(",")[0] + " " + prop["city"])
    assert time.perf_counter() - t < 0.5


@needs_kit
def test_cold_load_from_existing_index_is_fast(kit, prop):
    import tools.kit.match as km
    km._IDX = None
    t = time.perf_counter()
    m = kit[0](prop["address"])
    assert m["status"] == "matched" and time.perf_counter() - t < 0.5
