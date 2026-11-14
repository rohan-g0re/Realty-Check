"""Address normalisation: abbreviated street names and 'number-unit' labels. Invented data only."""
from tools.kit.normalize import abbrev_hits, abbrev_match, parse, street_key, unit_label

CITIES = ["houston"]


def prop(pid, address, street_name, num, zip_code="77006", city="houston"):
    return ({"property_id": pid, "kind": "building", "address": address},
            {"num_lo": num, "num_hi": num, "street_name": street_name, "zip": zip_code, "city": city})


def index(*rows):
    out = {}
    for p, k in rows:
        out.setdefault(street_key(k["street_name"]), []).append((p, k))
    return out


GROVE = prop("900001", "418 Linden Grove Ave, Houston, TX 77006", "Linden Grove", 418)


# ---- unit labels ----

def test_unit_label_plain_forms():
    for t in ("#1204", "Apt 1204", "Unit 1204", "unit #1204", "1204"):
        assert unit_label(t) == "1204", t


def test_unit_label_number_dash_unit_with_street_number():
    assert unit_label("418-1310", 418) == "1310"
    assert unit_label("418-1310", "418") == "1310"
    assert unit_label("41811-310", 41811) == "310"


def test_unit_label_number_dash_unit_without_street_number():
    assert unit_label("41811-310") == "310"  # long number, shorter smaller right part
    assert unit_label("12-301") == "12301"    # not a street number: left to norm_unit
    assert unit_label("12-301", 418) == "12301"


def test_parse_number_dash_unit_is_a_unit():
    q = parse("41811-310 Linden Grove Ave, Houston, TX 77006", CITIES)
    assert (q["num_lo"], q["num_hi"], q["unit"], q["streets"][0]) == (41811, 41811, "310", "linden")


def test_parse_genuine_ranges_unchanged():
    q = parse("101-105 Linden Grove Ave, Houston, TX 77006", CITIES)
    assert (q["num_lo"], q["num_hi"], q["unit"]) == (101, 105, None)
    q = parse("1200-08 Linden Grove Ave, Houston, TX 77006", CITIES)
    assert (q["num_lo"], q["num_hi"], q["unit"]) == (1200, 8, None)


def test_parse_explicit_unit_wins_over_dash():
    q = parse("41811-310 Linden Grove Ave #1204, Houston, TX 77006", CITIES)
    assert (q["num_lo"], q["unit"]) == (41811, "1204")


def test_parse_street_type():
    assert parse("418 Linden Grove Avenue, Houston, TX 77006", CITIES)["street_type"] == "ave"
    assert parse("418 Lind Dr N, Houston, TX 77006", CITIES)["street_type"] == "dr"


# ---- abbreviated street names ----

def test_abbrev_match_rules():
    assert abbrev_match("lind", "linden") and abbrev_match("linden", "lind")
    assert not abbrev_match("li", "linden")          # too short
    assert not abbrev_match("linden", "linden")      # equal is not an abbreviation
    assert not abbrev_match("lind oak", "linden")    # word counts differ
    assert not abbrev_match("lane", "linden")


def test_abbrev_hit_when_number_type_and_zip_agree():
    q = parse("418 Lind Grove Ave, Houston, TX 77006", CITIES)
    assert q["streets"][0] == "lind"
    assert [p["property_id"] for p, _ in abbrev_hits(q, index(GROVE))] == ["900001"]


def test_abbrev_hit_by_city_when_no_zip():
    q = parse("418 Lind Grove Ave, Houston TX", CITIES)
    assert [p["property_id"] for p, _ in abbrev_hits(q, index(GROVE))] == ["900001"]


def test_abbrev_no_hit_when_anything_disagrees():
    idx = index(GROVE)
    assert abbrev_hits(parse("419 Lind Grove Ave, Houston, TX 77006", CITIES), idx) == []   # number
    assert abbrev_hits(parse("418 Lind Grove Ave, Houston, TX 77007", CITIES), idx) == []   # zip
    assert abbrev_hits(parse("418 Lind Grove Dr, Houston, TX 77006", CITIES), idx) == []    # type
    assert abbrev_hits(parse("418 Lind Grove, Houston, TX 77006", CITIES), idx) == []       # no type given
    assert abbrev_hits(parse("418 Lind Grove Ave", CITIES), idx) == []                      # no zip or city


def test_abbrev_no_hit_when_two_streets_fit():
    other = prop("900002", "418 Lindale Grove Ave, Houston, TX 77006", "Lindale Grove", 418)
    assert abbrev_hits(parse("418 Lind Grove Ave, Houston, TX 77006", CITIES), index(GROVE, other)) == []
