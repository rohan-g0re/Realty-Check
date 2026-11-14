"""harness.claims.extract.page_unit: the one apartment a listing page is about, read as a unit (not a floor plan).
Deterministic layer only. Invented data only (418 Linden Grove Ave)."""
import pytest

from harness.claims import extract_claims
from harness.claims.extract import page_unit

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"


def snap(title="418 Linden Grove Ave", text="", address=ADDRESS, url="https://example.test/listing"):
    return {"url": url, "title": title, "address_text": address, "text": text, "jsonld": []}


def test_unit_heading_is_read_as_the_page_unit():
    s = snap(text="418 Linden Grove Ave\nUnit 1204\n2 beds, 2 baths, 1,050 sq ft\n")
    claims = extract_claims(s, use_model=False)
    # the block label stays on the claims (the run drops it once the kit confirms the unit); the unit is carried apart
    assert {c["label"] for c in claims if c["kind"] in ("bedrooms", "bathrooms", "area_sqft")} == {"Unit 1204"}
    assert page_unit(s, claims) == "1204"


@pytest.mark.parametrize("title, address, want", [
    ("418 Linden Grove Ave #207", ADDRESS, "207"),
    ("Linden Grove", "418 Linden Grove Ave Apt A321, Houston, TX", "A321"),
    ("Apartment 1204 at Linden Grove", ADDRESS, "1204"),
    ("Unit 1204 | 418 Linden Grove Ave Unit 1204", ADDRESS, "1204"),  # the same unit twice is one unit
])
def test_unit_from_title_or_address(title, address, want):
    assert page_unit(snap(title=title, address=address)) == want


@pytest.mark.parametrize("text", ["Plan C\nBedrooms: 2\nBathrooms: 2\nSq. Ft.: 1,050\n",
                                  "The Aspen\nBedrooms: 1\nSq. Ft.: 700\nThe Birch\nBedrooms: 2\nSq. Ft.: 950\n",
                                  "A1\n1 bed 1 bath 640 sq ft\n", "Floor Plan B2\n2 beds\n"])
def test_plan_names_are_not_units(text):
    s = snap(text=text)
    claims = extract_claims(s, use_model=False)
    assert page_unit(s, claims) is None


def test_plan_labels_still_label_plan_claims():
    s = snap(text="Plan C\n2 beds, 2 baths, 1,050 sq ft\n")
    claims = extract_claims(s, use_model=False)
    assert {c["label"] for c in claims if c["kind"] in ("bedrooms", "area_sqft")} == {"Plan C"}


def test_two_units_or_a_unit_list_name_no_page_unit():
    assert page_unit(snap(title="Unit 1204", address="418 Linden Grove Ave Unit 1205")) is None
    s = snap(text="Available units\nUnit 1204\n2 beds\n\nUnit 1205\n1 bed\n")
    assert page_unit(s, extract_claims(s, use_model=False)) is None
    # a unit table whose first row sits in the heading: the other rows' labels show it is a list of homes
    s = snap(text="Linden Grove\nUnit 1204 | 2 beds, 1,050 sq ft\n" + "Pet friendly\n" * 8 + "Unit 1205 | 1 bed, 700 sq ft\n")
    claims = extract_claims(s, use_model=False)
    assert {c["label"] for c in claims if c["kind"] == "bedrooms"} == {"Unit 1204", "Unit 1205"}
    assert page_unit(s, claims) is None
    assert page_unit(s, [c for c in claims if c["label"] == "Unit 1204"]) == "1204"


def test_url_and_counts_are_not_units():
    assert page_unit(snap(url="https://example.test/linden-grove/unit-1204")) is None
    assert page_unit(snap(title="418 Linden Grove Ave - 80 units", text="Built 2012\n4 stories\n")) is None
