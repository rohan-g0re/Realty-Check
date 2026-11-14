"""A building page that lists its units in a table: one row per unit (id, price, beds, baths, sq ft, amenities).
Rows are labelled "Unit <id>" by the layout code, rows without an id by their own facts; off-topic sections give
no claims; page_unit reads such a page as a list of homes. Model stubbed. Invented data only (418 Linden Grove Ave)."""
import pytest

import harness.claims.extract as ex
from harness.claims import validate_claim
from harness.claims.extract import page_unit

ADDRESS = "418 Linden Grove Ave #1204, Houston, TX 77002"
PAGE = """418 Linden Grove Ave #1204
Houston, TX 77002
11 units
Building Amenities
Dogs Ok
Fitness Center
Swimming Pool
Units
1204
$1,450/mo
1 Bed, 1 Bath
705 sqft
Air Conditioning • Fireplace • High Ceilings
1310
$1,400/mo
1 Bed, 1 Bath
712 sqft
Balcony • Hardwood Floor
#101
$1,700/mo
1 Bed, 1.5 Baths
1,148 sqft
Walk-In Closets • Fireplace
418-367
$2,300/mo
2 Beds, 3 Baths
1,342 sqft
Balcony • Walk-In Closets
$1,900/mo
2 Beds, 1.5 Baths
1,264 sqft
Carpet • Fireplace
About 418 Linden Grove Ave #1204
This unit is a lovely home in a gated community with 1 Bed and 1 Bath.
Rent Trends
Studio $1,100
1 Bed $1,350
2 Beds $1,900
Nearby Schools
Linden Elementary School 0.4 mi
Walk Score 72
Transit Score 41
Nearby Properties
420 Linden Grove Ave
2 Beds, 2 Baths 1,100 sqft
"""
ROWS = {"Unit 1204": (1, 1, 705), "Unit 1310": (1, 1, 712), "Unit 101": (1, 1.5, 1148), "Unit 367": (2, 3, 1342),
        "2 bed, 1.5 bath, 1,264 sq ft": (2, 1.5, 1264)}


def snap(text=PAGE, address=ADDRESS, jsonld=None, title="418 Linden Grove Ave - Linden Grove"):
    return {"url": "https://example.test/address/418-linden-grove", "title": title, "address_text": address,
            "text": text, "jsonld": jsonld or []}


def unit_facts(claims):
    got = {}
    for c in claims:
        if c["kind"] in ex.UNIT_KINDS:
            got.setdefault(c["label"], {})[c["kind"]] = c["value"]
    return {lab: (v.get("bedrooms"), v.get("bathrooms"), v.get("area_sqft")) for lab, v in got.items()}


def cands_of(s):
    lines = s["text"].splitlines()
    self_lines, excluded = ex._line_roles(lines, ex._self_re(s["address_text"]))
    return ex.candidates(lines, excluded, self_lines, street=ex.street_number(s["address_text"]))


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)


# ---------------------------------------------------------------- unit ids

@pytest.mark.parametrize("raw, street, want", [
    ("#101", "418", "Unit 101"), ("Apt 1310", "418", "Unit 1310"), ("Apt. 1310", None, "Unit 1310"),
    ("418-367", "418", "Unit 367"), ("418-367", "500", "Unit 418-367"), ("418-367", None, "Unit 418-367"),
    ("Unit #1204", "418", "Unit 1204"), ("1204", "418", "Unit 1204"), ("12b", None, "Unit 12B"),
])
def test_unit_label_normalises_the_id(raw, street, want):
    assert ex.unit_label(raw, street) == want


def test_street_number():
    assert ex.street_number(ADDRESS) == "418" and ex.street_number("Linden Grove") is None


# ---------------------------------------------------------------- rules layer

def test_each_row_is_labelled_with_its_unit_and_has_beds_baths_size():
    claims = ex.extract_claims(snap(), use_model=False)
    assert unit_facts(claims) == ROWS, unit_facts(claims)
    for c in claims:
        assert validate_claim(c) == [], c
        if c["kind"] in ex.UNIT_KINDS:
            assert c["scope"] == "unit" and c["label"], c  # no orphan unlabelled numbers
    assert isinstance(next(c for c in claims if c["label"] == "Unit 101" and c["kind"] == "bathrooms")["value"], float)


def test_row_features_carry_the_row_label():
    claims = ex.extract_claims(snap(), use_model=False)
    balc = [(c["label"], c["scope"]) for c in claims if c["value"] == "balcony"]
    assert balc == [("Unit 1310", "unit"), ("Unit 367", "unit")]
    # building amenities above the table stay unlabelled
    assert {(c["value"], c["label"]) for c in claims if c["value"] in ("pool", "fitness center")} == {
        ("pool", None), ("fitness center", None)}


def test_one_line_table_rows_and_keyword_ids():
    text = ("418 Linden Grove Ave\nUnits\n"
            "1204\t$1,450\t1 Bed\t1 Bath\t705 sq ft\n"
            "Apt 1310\t$1,400\t1 Bed\t1 Bath\t712 ft²\n"
            "#101 | $1,700 | 1 Bed | 1.5 Baths | 1,148 sq ft\n"
            "418-367 $2,300 2 Beds 3 Baths 1,342 sq ft\n")
    claims = ex.extract_claims(snap(text, address="418 Linden Grove Ave"), use_model=False)
    assert unit_facts(claims) == {k: v for k, v in ROWS.items() if k.startswith("Unit")}, unit_facts(claims)


def test_two_rows_with_identical_facts_stay_apart():
    text = "418 Linden Grove Ave\n1204\n1 Bed, 1 Bath\n648 sqft\n1310\n1 Bed, 1 Bath\n648 sqft\n"
    claims = ex.extract_claims(snap(text, address="418 Linden Grove Ave"), use_model=False)
    assert unit_facts(claims) == {"Unit 1204": (1, 1, 648), "Unit 1310": (1, 1, 648)}


def test_bare_numbers_are_not_row_ids_without_a_table():
    # one bare number over a spec line is no table; sizes and counts on their own line are not ids
    text = "418 Linden Grove Ave\n2019\n2 Beds, 2 Baths\n1,050 sqft\n"
    claims = ex.extract_claims(snap(text, address="418 Linden Grove Ave"), use_model=False)
    assert not [c for c in claims if c["label"]], claims
    text = "418 Linden Grove Ave\nPlan A\n640\nsq ft\n1 Bed\nPlan B\n780\nsq ft\n2 Beds\n"
    labels = {c["label"] for c in ex.extract_claims(snap(text, address="418 Linden Grove Ave"), use_model=False)}
    assert not any(str(lab).startswith("Unit") for lab in labels), labels


# ---------------------------------------------------------------- building count vs availability

@pytest.mark.parametrize("line, want", [
    ("11 units", 11), ("This building has 120 units", 120), ("11 Units Available", None), ("11 unit Rentals", None),
    ("Showing 11 units", None), ("4 units for rent", None), ("11 units now available", None),
])
def test_unit_count_only_for_the_building_total(line, want):
    claims = ex.extract_claims(snap(f"418 Linden Grove Ave\n{line}\nPet friendly\n", address="418 Linden Grove Ave"),
                               use_model=False)
    assert [c["value"] for c in claims if c["kind"] == "unit_count"] == ([want] if want else [])


# ---------------------------------------------------------------- sections about other things

def test_rent_trends_schools_scores_and_nearby_give_no_claims():
    claims = ex.extract_claims(snap(), use_model=False)
    quotes = " | ".join(c["quote"] for c in claims)
    assert "1,100" not in quotes and "$" not in quotes
    texts = [c["text"] for c in cands_of(snap())]
    for t in ("Studio $1,100", "1 Bed $1,350", "2 Beds $1,900", "Walk Score 72", "2 Beds, 2 Baths 1,100 sqft"):
        assert t not in texts, t


def test_off_topic_zone_ends_at_the_listing_s_own_section():
    text = "418 Linden Grove Ave\nSchools\nLinden Elementary 2 beds of tulips\nAmenities\nSwimming pool\n"
    claims = ex.extract_claims(snap(text, address="418 Linden Grove Ave"), use_model=False)
    assert [c["value"] for c in claims if c["kind"] == "amenity"] == ["pool"]
    assert not [c for c in claims if c["kind"] == "bedrooms"]


# ---------------------------------------------------------------- metadata duplicate of a row

def test_page_unit_metadata_repeating_its_row_is_dropped():
    jsonld = [{"@type": "SingleFamilyResidence", "address": {"streetAddress": "418 Linden Grove Ave #1204"},
               "floorSize": {"value": 705, "unitCode": "FTK"}, "numberOfBedrooms": 1, "yearBuilt": 1984}]
    claims = ex.extract_claims(snap(jsonld=jsonld), use_model=False)
    assert not [c for c in claims if c["kind"] in ex.UNIT_KINDS and not c["label"]], claims
    assert [c["value"] for c in claims if c["kind"] == "year_built"] == [1984]


# ---------------------------------------------------------------- model layer (stubbed)

def answers(cands, cap_rows=False):
    """A model that labels nothing itself, misreads one row, skips beds on others, and counts availability."""
    out = []
    for c in cands:
        t = c["text"]
        if t == "11 units" or t == "3 Units Available":
            out.append({"n": c["n"], "kind": "unit_count", "value": int(t.split()[0])})
        elif t in ("Dogs Ok", "Fitness Center", "Swimming Pool"):
            out.append({"n": c["n"], "kind": "amenity", "value": t, "scope": "building"})
        elif "•" in t:
            for v in t.split(" • "):
                out.append({"n": c["n"], "kind": "amenity", "value": v, "scope": "building"})
        elif t.endswith("sqft"):
            out.append({"n": c["n"], "kind": "area_sqft", "value": 999 if c["label"] == "Unit 1310" else t})
        elif "Bath" in t and ", " in t and c["label"] != "Unit 101":
            out.append({"n": c["n"], "kind": "bathrooms", "value": t.split(", ")[1]})
    return out


def test_model_rows_are_fixed_by_the_rules_and_features_take_the_row_label(monkeypatch):
    text = PAGE.replace("Units\n", "3 Units Available\nUnits\n")
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return answers(cands_of(snap(text)))
    monkeypatch.setattr(ex, "_ask_cached", ask)
    claims = ex.extract_claims(snap(text))
    assert len(calls) == 1 and "[plan: Unit 101]" in calls[0] and "[plan: Unit 367]" in calls[0]
    assert unit_facts(claims) == ROWS, unit_facts(claims)  # 999 misread replaced, missing beds filled
    assert [c["value"] for c in claims if c["kind"] == "unit_count"] == [11]
    fire = [(c["label"], c["scope"]) for c in claims if c["value"] == "Fireplace"]
    assert fire == [("Unit 1204", "unit"), ("Unit 101", "unit"), ("2 bed, 1.5 bath, 1,264 sq ft", "unit")], fire
    for c in claims:
        assert validate_claim(c) == [], c
        assert ex._norm(c["quote"]) in ex._norm(text), c


def test_cap_prefers_row_numbers_then_building_then_row_amenities(monkeypatch):
    rows = "".join(f"{1201 + k}\n$1,400/mo\n{1 + k % 2} Bed, 1 Bath\n{700 + k} sqft\nFireplace • Carpet • Walk-In Closets\n"
                   for k in range(11))
    text = "418 Linden Grove Ave #1204\nBuilding Amenities\nDogs Ok\nFitness Center\nSwimming Pool\nUnits\n" + rows
    monkeypatch.setattr(ex, "_ask_cached", lambda p: answers(cands_of(snap(text))))
    claims = ex.extract_claims(snap(text))
    assert len(claims) == ex.MAX_CLAIMS
    nums = [c for c in claims if c["kind"] in ex.UNIT_KINDS]
    assert len(nums) == 33 and claims[:33] == nums
    feats = claims[33:]
    # building amenities (record-named first), then the rows' own amenities in page order
    assert [c["value"] for c in feats[:3]] == ["Fitness Center", "Swimming Pool", "Dogs Ok"]
    assert all(c["label"] for c in feats[3:]) and feats[3]["label"] == "Unit 1201"


# ---------------------------------------------------------------- page_unit on a unit table page

def test_page_unit_is_none_on_a_unit_table_page():
    s = snap()
    assert page_unit(s, ex.extract_claims(s, use_model=False)) is None
    # rows without the page's unit, plus an id-less row: still a list of homes
    text = PAGE.replace("1204\n$1,450/mo\n1 Bed, 1 Bath\n705 sqft\nAir Conditioning • Fireplace • High Ceilings\n", "")
    s = snap(text)
    claims = ex.extract_claims(s, use_model=False)
    assert "Unit 1204" not in {c["label"] for c in claims}
    assert page_unit(s, claims) is None


def test_page_unit_still_reads_a_one_unit_page():
    s = snap("418 Linden Grove Ave #1204\nUnit 1204\n1 Bed, 1 Bath\n705 sqft\nBalcony\n")
    claims = ex.extract_claims(s, use_model=False)
    assert {c["label"] for c in claims if c["kind"] in ex.UNIT_KINDS} == {"Unit 1204"}
    assert page_unit(s, claims) == "1204"
