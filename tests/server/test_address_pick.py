"""pick_address on an invented mini index (no kit data)."""
from server.address_pick import candidates_of, name_agreement, pick_address, _page_signals


def prop(pid, name, num, street, zip_code="77999", kind="building", units=120, plans=True):
    return {"property_id": pid, "kind": kind, "name": name, "address": f"{num} {street}", "city": "faketown",
            "state": "TX", "lat": 29.0, "lon": -95.0,
            "keys": [{"num_lo": num, "num_hi": num, "street_name": street.rsplit(" ", 1)[0], "zip": zip_code,
                      "city": "faketown", "lat": 29.0, "lon": -95.0}],
            "record": {"stories": 5, "year_built": 2015, "unit_count": units, "building_sf": None},
            "floorplans": [{"name": "A1", "beds": 1, "baths": 1, "sqft": 700}] if plans else [],
            "matterport_model_ids": []}


INDEX = {"properties": [
    prop("90001", "Larkspur Commons", 418, "Linden Grove Ave"),
    prop("90002", "Example Living Office Tower", 900, "Placeholder Pkwy", units=0, plans=False),
    prop("90003", "Cedar House", 310, "Cedar Example Ln"),
    prop("90004", "Nearby Example Apartments", 1500, "Nearby Example Dr"),
    prop("90005", "The Alder", 77, "Alder Example St"),
    prop("90006", "Warehouse Example", 5, "Depot Example Rd", kind="industrial", units=0, plans=False),
]}

HERO = "418 Linden Grove Ave, Faketown, TX 77999"
CORP = "900 Placeholder Pkwy, Suite 300, Faketown, TX 77999"


def cand(text, score, where, why="Found in the page text."):
    return {"text": text, "score": score, "where": where, "why": why}


def test_footer_corporate_address_loses_to_building_named_like_the_page():
    # the extension ranked the footer office first (old behaviour); the picker still finds the building
    snap = {"url": "https://larkspurcommons.example/", "title": "Larkspur Commons | Apartments in Faketown",
            "address_text": CORP,
            "address_candidates": [cand(CORP, 3.0, ["footer", "label:corporate"]), cand(HERO, 2.5, ["main"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == HERO and r["property"]["property_id"] == "90001"
    assert r["why"] == "Matches the building named in the page title." and r["confidence"] >= 0.9
    assert [a["text"] for a in r["alternatives"]] == [CORP]


def test_single_footer_address_on_a_building_site_is_chosen():
    a = "77 Alder Example St, Faketown, TX 77999"
    snap = {"url": "https://thealder.example/", "title": "Welcome home", "address_text": a,
            "address_candidates": [cand(a, 1.0, ["footer"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == a and r["property"]["property_id"] == "90005"
    assert r["why"] == "Matches the building named in the site's web address."


def test_aggregator_nearby_listing_steps_aside():
    own, near = "310 Cedar Example Ln, Faketown, TX 77999", "1500 Nearby Example Dr, Faketown, TX 77999"
    snap = {"url": "https://rentals.example/listing/1234", "title": "Cedar House - Faketown, TX", "address_text": near,
            "address_candidates": [cand(near, 4.0, ["nearby"]), cand(own, 3.0, ["h1"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == own and r["property"]["name"] == "Cedar House"


def test_non_residential_match_is_down_ranked():
    ware, unknown = "5 Depot Example Rd, Faketown, TX 77999", "1 Nowhere Example Rd, Faketown, TX 77999"
    snap = {"url": "https://x.example/", "title": "Loft", "address_text": ware,
            "address_candidates": [cand(ware, 3.0, ["main"]), cand(unknown, 2.0, ["main"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == unknown and r["property"] is None and r["confidence"] < 0.5


def test_nothing_matches_keeps_best_candidate_with_low_confidence():
    a, b = "1 Nowhere Example Rd, Faketown, TX 77999", "2 Nowhere Example Rd, Faketown, TX 77999"
    snap = {"url": "https://x.example/", "title": "Somewhere", "address_text": a,
            "address_candidates": [cand(a, 5.0, ["h1"]), cand(b, 1.0, ["footer"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == a and r["property"] is None and r["confidence"] <= 0.3
    assert "No building record matches" in r["why"]


def test_old_snapshot_without_candidates_uses_address_text():
    snap = {"url": "https://x.example/", "title": "t", "address_text": HERO}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == HERO and r["property"]["property_id"] == "90001" and r["alternatives"] == []
    assert r["why"] == "Matches a building record at this address."


def test_locked_address_is_kept():
    snap = {"url": "https://larkspurcommons.example/", "title": "Larkspur Commons", "address_text": CORP,
            "address_locked": True, "address_candidates": [cand(HERO, 5.0, ["h1"]), cand(CORP, 1.0, ["footer"])]}
    r = pick_address(snap, INDEX)
    assert r["chosen"] == CORP and r["why"] == "You picked this address."


def test_no_address_at_all():
    r = pick_address({"url": "https://x.example/", "title": "t"}, INDEX)
    assert r["chosen"] is None and r["confidence"] == 0.0


def test_matcher_errors_are_a_no_match():
    def boom(_):
        raise RuntimeError("index missing")
    r = pick_address({"address_text": HERO}, matcher=boom)
    assert r["chosen"] == HERO and r["property"] is None


def test_candidates_dedupe_and_string_where():
    snap = {"address_text": HERO, "address_candidates": [cand(HERO, 2, "h1 + footer"), cand(HERO + ".", 1, [])]}
    cs = candidates_of(snap)
    assert len(cs) == 1 and cs[0]["where"] == {"h1", "footer"}


def test_name_agreement_ignores_generic_words():
    sig = _page_signals({"url": "https://rentals.example/x", "title": "Apartments for rent"})
    assert name_agreement("The Apartments", sig) == (0.0, None)
    sig = _page_signals({"url": "https://www.cedarhouse.example/", "title": "Home"})
    assert name_agreement("Cedar House", sig) == (1.0, "domain")


def test_name_agreement_uses_the_part_before_at():
    sig = _page_signals({"url": "https://www.example.com/", "title": "Modern Living at Example Brook"})
    assert name_agreement("Example Brook at Placeholder Heights", sig)[0] == 1.0
