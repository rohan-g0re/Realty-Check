"""Kit facts: amenities named from photo captions, owner type only (never a name), old files still read.
Offline, invented data only."""
import json

from tools.kit import facts as kf


def _rec(**kw):
    return {"id": 900001, "propertyApartment": {}, **kw}


def test_amenities_from_captions_fixed_names_only():
    atts = [{"attachmentType": 4, "caption": "Resort-Style Pool"},
            {"attachmentType": 33, "caption": "Fitness Center"},
            {"attachmentType": 134, "caption": "Sky Lounge"},
            {"attachmentType": 4, "caption": "Pool Table in the Game Room"},
            {"attachmentType": 4, "caption": "Pet Spa"},
            {"attachmentType": 4, "caption": "Clubroom"},
            {"attachmentType": 10, "caption": "Pool"},                      # a floor plan image: skipped
            {"attachmentType": 4, "caption": "Leasing office"},             # not an amenity
            {"attachmentType": 4, "caption": "Covered parking"},            # parking has its own rows
            {"attachmentType": 4, "caption": "Call Jane at 555-0100 about the dog park and the pool " * 2},
            {"attachmentType": 4, "caption": None}]
    got = kf.amenities_from_captions(atts)
    assert got == ["Pool", "Fitness center", "Clubhouse", "Rooftop deck", "Game room", "Pet spa"]
    assert "Spa or hot tub" not in got and "Dog park" not in got  # long caption ignored, "pet spa" is not a spa


def test_extract_owner_type_never_name():
    contacts = [{"contactRole": 7, "locationId": 11, "endDate": None},
                {"contactRole": 4, "locationId": 12, "endDate": None}]
    names = {11: "Linden Grove Holdings LLC", 12: "Sample Residential LLC"}
    x = kf.extract(_rec(contacts=contacts), None, names)
    assert x["owner_type"] == "company" and "owner" not in x
    assert "Linden Grove Holdings" not in json.dumps(x)
    # the company record says the owner is an individual
    x = kf.extract(_rec(contacts=contacts), None, {11: "Example Owner", 12: "Sample Residential LLC"}, {11: 5})
    assert x["owner_type"] == "individual" and "Example Owner" not in json.dumps(x)
    # an ended owner contact, or an owner the kit cannot name: nothing is said
    ended = [{"contactRole": 7, "locationId": 11, "endDate": "2020-01-01"}]
    assert "owner_type" not in kf.extract(_rec(contacts=ended), None, names)
    assert "owner_type" not in kf.extract(_rec(contacts=contacts), None, {})
    # recorded owner when no true owner is on record
    rec_only = [{"contactRole": 3, "locationId": 13, "endDate": None}]
    assert kf.extract(_rec(contacts=rec_only), None, {13: "Example Trust"})["owner_type"] == "company"


def test_extract_amenities_and_renovation():
    r = _rec(attachments=[{"attachmentType": 4, "caption": "Dog Park"}],
             constructionEvents=[{"eventType": 4, "year": 2019}, {"eventType": 2, "year": 2001}])
    x = kf.extract(r, None, {})
    assert x["amenities"] == ["Dog park"] and x["renovated"] == 2019 and x["version"] == kf.FACTS_VERSION


def test_kit_facts_reads_old_and_new_versions(monkeypatch, tmp_path):
    monkeypatch.setattr(kf, "facts_dir", lambda: tmp_path)
    monkeypatch.setattr(kf, "_mem", {})
    (tmp_path / "900001.json").write_text(json.dumps({"version": 1, "property_id": "900001", "style": "Garden"}))
    (tmp_path / "900002.json").write_text(json.dumps({"version": 2, "property_id": "900002", "amenities": ["Pool"]}))
    (tmp_path / "900003.json").write_text(json.dumps({"version": 99, "property_id": "900003"}))
    assert kf.kit_facts("900001")["style"] == "Garden"
    assert kf.kit_facts("900002")["amenities"] == ["Pool"]
    assert kf.kit_facts("900003") is None
