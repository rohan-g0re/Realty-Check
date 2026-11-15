"""harness.citations: the proof list behind each verdict and the "no openable proof" rule. Invented data only:
"418 Linden Grove Ave", ids 900001 / 900002 / 900003, scan SAMPLEscan01, units 1204 / 1310, Example Property
Management."""
import json

import scripted_cli
from harness.citations import NO_PROOF_FOUND, apply_proof_rule, build_citations, cite
from harness.graph import Toolbox, judge, run_check
from rc_common import evidence

IMG = "C:/rc_cache_fake"
ADDRESS = "418 Linden Grove Ave, Houston, TX 77006"
MATCH = {"status": "matched", "property_id": "900001", "address": ADDRESS}
COUNTY_URL = ("https://gis.example.test/arcgis/rest/services/Parcels/MapServer/0/query"
              "?geometry=-95.37%2C29.75&f=json&outFields=acct_num")


def claim(cid, kind, value, quote, label=None, scope="building"):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": scope, "label": label, "quote": quote,
            "checkable": True}


def plan_record(plan="Plan E", beds=2, baths=2, sqft=1100):
    return evidence("record", "ok", f"The building record lists plan {plan}.",
                    {"plan": plan, "kit_plan": plan, "bedrooms": beds, "bathrooms": baths, "area_sqft": sqft},
                    detail="CoStar hackathon kit floor plan list of property 900001; listing plan matched by name.")


def building_record(**obs):
    return evidence("record", "ok", "CoStar record.", obs, detail="CoStar hackathon kit property 900001, matched by zip")


def county(url=COUNTY_URL, **obs):
    base = {"parcel_id": "900003", "parcel_address": "418 LINDEN GROVE", "parcel_match": "point",
            "source_name": "Harris County Appraisal District (HCAD) + City of Houston 311", "source_url": url,
            "owner_type": "company", "houston_311_count": 7,
            "city_calls": {"total": 7, "by_type": {"Water leak at the corner": 7}, "since": "2025-01-01",
                           "radius_m": 150, "source_url": "https://data.example.test/311"}}
    return evidence("county", "ok", "County roll lists 5 floors.", {**base, **obs},
                    detail="HCAD Parcels MapServer point query (state_class B2, tax_year 2025) gave account 900003; "
                           "owner_name_1 -> owner_type (the name Jane Example is not kept).")


def scan_counts():
    return evidence("scan", "ok", "The 3D scan titled '1BR W Den' shows 1 bedroom.",
                    {"model_id": "SAMPLEscan01", "model_name": "1BR W Den", "plan": "Plan E", "bedrooms": 1,
                     "bathrooms": 1, "area_sqft": 968, "room_labels": ["Bedroom", "Den", "Bathroom"]},
                    detail="Matterport Model API; applicationKey=SDKKEYSAMPLE never shown")


def scan_stills():
    return evidence("scan", "ok", "Saved 2 stills.", {"model_id": "SAMPLEscan01", "image_count": 2, "plan": "Plan E"},
                    [{"path": f"{IMG}/scan/SAMPLEscan01/pano_4.jpg",
                      "caption": "Panorama 4 in the Bedroom: the four horizontal views around the camera"},
                     {"path": f"{IMG}/scan/SAMPLEscan01/floorplan.png", "caption": "Schematic floor plan"}])


def roof(height=23.1, stories=6):
    return evidence("aerial", "ok", "Roof is 23.1 m above ground.",
                    {"height_m": height, "stories": stories, "capture_date": "2025-03-14"},
                    [{"path": f"{IMG}/aerial/oblique_north.jpg", "caption": "Angled Vexcel aerial view (2025-03-14)"}])


def property_data(count=1):
    return evidence("aerial", "ok", "Vexcel property data.",
                    {"property": {"captured": "2025-06-01",
                                  "pool": {"present": True, "count": count, "stable": True, "area_sqft": 1900}}})


def outlined():
    return evidence("aerial", "ok", "Pool detected near the address, outlined.",
                    {"feature_check": True, "outlined": ["pool"]},
                    [{"path": f"{IMG}/aerial/outlined.jpg", "caption": "Pool detected near the address, outlined"},
                     {"path": f"{IMG}/aerial/top.jpg", "caption": "Top-down Vexcel aerial photo (2025-03-14)"}])


def decided(cits):
    d = [c for c in cits if c["role"] == "decided"]
    assert len(d) == 1, cits
    return d[0]


def by_source(cits, name):
    return [c for c in cits if c["source"] == name]


KEYS = {"source", "read", "where", "date", "value", "image", "link", "role"}


# ---------- each source ----------

def test_costar_plan_field_decides_with_property_plan_and_field():
    c = claim("c1", "bedrooms", 2, "2 Beds", label="Plan E", scope="unit")
    evs = [plan_record()]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    cits = build_citations(v, c, evs, MATCH)
    d = decided(cits)
    assert set(d) == KEYS
    assert d["source"] == "CoStar" and d["read"] == "Bedrooms field"
    assert d["where"] == "CoStar property 900001, floor plan Plan E, field Bedrooms"
    assert d["value"] == "2 bedrooms" and d["date"] is None and d["link"] is None
    assert cits[-1] == {"source": "Listing", "read": "Listing text", "where": "listing page, Plan E", "date": None,
                        "value": "2 Beds", "image": None, "link": None, "role": "context"}


def test_county_gives_source_parcel_roll_year_and_query_link():
    c = claim("c2", "year_built", 2016, "Built in 2016")
    evs = [county(year_built=2016)]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed"
    d = decided(build_citations(v, c, evs, MATCH))
    assert d["source"] == "County" and d["read"] == "Year built field"
    assert d["where"] == "Harris County Appraisal District (HCAD), parcel 900003, field Year built"
    assert d["date"] == "2025" and d["link"] == COUNTY_URL


def test_county_link_with_a_key_or_signature_is_dropped():
    c = claim("c2", "year_built", 2016, "Built in 2016")
    for bad in (COUNTY_URL + "&token=abc", COUNTY_URL + "&sig=abc", "ftp://gis.example.test/x",
                "https://user:pw@gis.example.test/q"):
        evs = [county(url=bad, year_built=2016)]
        assert decided(build_citations(judge(c, evs), c, evs, MATCH))["link"] is None


def test_matterport_names_scan_and_spot_and_keeps_the_proof_picture():
    c = claim("c1", "bedrooms", 2, "2 Beds", label="Plan E", scope="unit")
    evs = [scan_counts(), scan_stills()]
    v = judge(c, evs)
    assert v["verdict"] == "contradicted" and v["source"] == "scan"
    d = decided(build_citations(v, c, evs, MATCH))
    assert d["source"] == "Matterport" and d["read"] == "Room labels"
    assert d["where"] == "scan '1BR W Den' (SAMPLEscan01), spot: Panorama 4 in the Bedroom"
    assert d["image"] == v["image"] == f"{IMG}/scan/SAMPLEscan01/pano_4.jpg"
    assert d["value"] == "1 bedroom + den"


def test_vexcel_roof_height_has_capture_date_and_photo():
    c = claim("c3", "stories", 6, "6-story building")
    evs = [roof()]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed" and v["source"] == "aerial"
    d = decided(build_citations(v, c, evs, MATCH))
    assert d["source"] == "Vexcel" and d["read"] == "Measured roof height" and d["date"] == "2025-03-14"
    assert d["where"].startswith("Vexcel roof height measured at the address")
    assert d["image"] == f"{IMG}/aerial/oblique_north.jpg"


def test_vexcel_property_data_decides_a_pool_with_the_outlined_photo():
    c = {"id": "c5", "kind": "amenity", "value": "pool", "unit": None, "scope": "building", "label": None,
         "quote": "Resort-style pool", "checkable": True}
    evs = [property_data(), outlined()]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed" and v["source"] == "aerial"
    cits = build_citations(v, c, evs, MATCH)
    d = decided(cits)
    assert d["source"] == "Vexcel" and d["read"] == "Property data" and d["date"] == "2025-06-01"
    assert d["image"] == f"{IMG}/aerial/outlined.jpg" and d["value"] == "Shows pool"
    assert all(x["role"] != "differs" for x in cits)  # absence or a photo is never "differs" for a feature


def test_costar_amenity_list_decides_a_named_amenity():
    c = {"id": "c6", "kind": "amenity", "value": "fitness center", "unit": None, "scope": "building", "label": None,
         "quote": "Fitness Center", "checkable": True}
    evs = [building_record(amenities=["Fitness Center", "Pool"])]
    v = judge(c, evs)
    assert v["verdict"] == "confirmed" and v["source"] == "record"
    d = decided(build_citations(v, c, evs, MATCH))
    assert d["source"] == "CoStar" and d["read"] == "Amenity list" and d["value"].startswith("Named:")


# ---------- decided role ----------

def test_other_sources_agree_or_differ_against_the_decided_value():
    c = claim("c3", "stories", 5, "5-story building")
    evs = [building_record(stories=5), county(stories=5), roof()]
    v = judge(c, evs)
    cits = build_citations(v, c, evs, MATCH)
    d = decided(cits)
    assert v["verdict"] == "confirmed" and d["source"] == {"record": "CoStar", "county": "County",
                                                           "aerial": "Vexcel"}[v["source"]]
    roles = {x["source"]: x["role"] for x in cits if x["role"] != "decided" and x["source"] != "Listing"}
    assert roles.get("Vexcel") == "differs"
    assert [x["source"] for x in cits][-1] == "Listing" and cits[0]["role"] == "decided"


def test_records_differ_home_count_credits_the_record_the_judge_used():
    c = claim("c4", "unit_count", 300, "300 apartment homes")
    evs = [building_record(unit_count=295), county(unit_count=100)]
    v = judge(c, evs)
    assert v["verdict"] == "close" and v["source"] == "record"
    cits = build_citations(v, c, evs, MATCH)
    d = decided(cits)
    assert d["source"] == "CoStar" and d["read"] == "Number of units field" and d["value"] == "295 apartments"
    assert by_source(cits, "County")[0]["role"] == "differs"


def test_sources_differ_cant_check_has_no_decided_but_cites_both_records():
    c = claim("c3", "stories", 5, "5-story building")
    evs = [building_record(stories=5), county(stories=7)]
    v = judge(c, evs)
    assert v["verdict"] == "cant_check" and v["found"] == "sources differ"
    nv = cite(v, c, evs, MATCH)
    assert nv["verdict"] == "cant_check" and "downgraded_from" not in nv
    roles = {x["source"]: x["role"] for x in nv["citations"]}
    assert roles == {"CoStar": "agrees", "County": "differs", "Listing": "context"}


# ---------- the proof rule ----------

def test_result_without_any_proof_becomes_cant_check():
    c = claim("c1", "bedrooms", 2, "2 Beds", label="Plan E", scope="unit")
    v = {"claim_id": "c1", "verdict": "confirmed", "claimed": "2 bedrooms", "found": "2 bedrooms", "source": "record",
         "reason": "Listing says 2 bedrooms. The building record agrees: 2 bedrooms.", "image": None}
    nv = cite(v, c, [], MATCH)
    assert nv["verdict"] == "cant_check" and nv["downgraded_from"] == "confirmed" and nv["found"] == NO_PROOF_FOUND
    assert nv["source"] is None and "no openable proof" in nv["reason"]
    assert [x["source"] for x in nv["citations"]] == ["Listing"]
    assert set(v) <= set(nv)  # additive: no field removed


def test_rule_never_touches_a_verdict_that_has_proof_or_is_already_cant_check():
    cases = [
        (claim("c1", "bedrooms", 2, "2 Beds", label="Plan E", scope="unit"), [scan_counts(), scan_stills()]),
        (claim("c2", "year_built", 2016, "Built in 2016"), [county(year_built=2016)]),
        (claim("c3", "stories", 6, "6-story building"), [roof()]),
        (claim("c4", "unit_count", 300, "300 apartment homes"), [building_record(unit_count=295), county(unit_count=100)]),
        (claim("c7", "area_sqft", 1100, "1,100 sq ft", label="Plan E", scope="unit"), [plan_record()]),
        (claim("c8", "bedrooms", 3, "3 Beds", label="Plan E", scope="unit"), []),
    ]
    for c, evs in cases:
        v = judge(c, evs)
        nv = cite(v, c, evs, MATCH)
        assert {k: x for k, x in nv.items() if k != "citations"} == v, (c, v, nv)


def test_listing_only_citations_count_as_no_proof():
    v = {"claim_id": "c1", "verdict": "contradicted", "found": "1 bedroom", "source": "scan", "image": None,
         "reason": "r", "claimed": "2 bedrooms"}
    assert apply_proof_rule(v, [{"source": "Listing", "role": "context"}])["verdict"] == "cant_check"
    kept = apply_proof_rule(v, [{"source": "Matterport", "role": "decided"}])
    assert kept["verdict"] == "contradicted" and "downgraded_from" not in kept


# ---------- privacy ----------

def test_no_names_contacts_311_text_signed_urls_or_keys():
    c = claim("c9", "unit_count", 100, "100 homes. Managed by Example Property Management, call (555) 010-0199 "
              "or leasing@example.test, https://example.test/apply?sig=abc")
    evs = [building_record(unit_count=100), county(unit_count=100), scan_counts(), scan_stills()]
    v = judge(c, evs)
    nv = cite(v, c, evs, MATCH)
    blob = json.dumps(nv["citations"])
    for bad in ("Jane", "owner", "company", "Water leak", "311", "(555)", "010-0199", "leasing@", "sig=", "http://",
                "https://example.test", "applicationKey", "SDKKEYSAMPLE", "data.example.test", "LINDEN GROVE"):
        assert bad not in blob, bad
    listing = by_source(nv["citations"], "Listing")[0]
    assert listing["value"].startswith("100 homes. Managed by Example Property Management")
    for x in nv["citations"]:
        assert x["link"] in (None, COUNTY_URL)
        for k in ("read", "where", "value"):
            assert not x[k] or (":/" not in x[k] and "rc_cache" not in x[k]), x


# ---------- wired into the run ----------

def test_every_run_verdict_carries_citations_and_none_is_downgraded(monkeypatch):
    scripted_cli.install(monkeypatch)
    events = []
    run_check({"url": "https://example.test/listing", "title": "Example Lofts", "address_text": ADDRESS,
               "text": "2 Beds 1,100 sq ft", "jsonld": []}, events.append, Toolbox.fake())
    vs = [e["verdict"] for e in events if e["type"] == "verdict"]
    assert vs
    for v in vs:
        assert isinstance(v["citations"], list) and "downgraded_from" not in v
        for x in v["citations"]:
            assert set(x) == KEYS
        n = sum(x["role"] == "decided" for x in v["citations"])
        assert n == (1 if v["verdict"] in ("confirmed", "contradicted", "close") else 0), v
