"""A page plan label with a size but no bed count links to a record plan only when exactly one plan has that size.
Invented data only (418 Linden Grove Ave)."""
from tools.kit import link_plan


def fp(name, beds, baths, sqft, mid=None):
    return {"name": name, "beds": beds, "baths": baths, "sqft": sqft, "matterport_model_id": mid}


KIT = [fp("A1", 1, 1, 622, "m900001"), fp("B1", 1, 1.5, 1136, "m900002"), fp("C1", 2, 2.5, 1440),
       fp("C2", 2, 2, 1460)]
MATCH = {"status": "matched", "property_id": "900001", "name": "418 Linden Grove Ave", "floorplans": KIT,
         "matterport_model_ids": ["m900001", "m900002"]}


def claim(kind, value, label, cid, quote=None):
    return {"id": cid, "kind": kind, "value": value, "unit": None, "scope": "unit", "label": label,
            "quote": quote or str(value), "checkable": True}


def test_size_only_links_the_one_plan():
    lab = "Unit 1204"
    claims = [claim("area_sqft", 1136, lab, "c1", "1,136 sqft"), claim("bathrooms", 1.5, lab, "c2")]
    r = link_plan(lab, MATCH, claims)
    assert r["kit"]["name"] == "B1" and r["how"] == "size" and not r["ambiguous"]
    assert "by size alone" in r["note"] and "bedrooms" in r["note"]
    assert r["scan_id"] == "m900002"


def test_size_only_without_baths():
    lab = "Unit 1310"
    r = link_plan(lab, MATCH, [claim("area_sqft", 640, lab, "c1", "640 sqft")])  # 3% from A1
    assert r["kit"]["name"] == "A1" and r["how"] == "size"


def test_size_only_baths_disagree_no_link():
    lab = "Unit 1204"
    claims = [claim("area_sqft", 1136, lab, "c1", "1,136 sqft"), claim("bathrooms", 3, lab, "c2")]
    r = link_plan(lab, MATCH, claims)
    assert r["kit"] is None and r["kit_plans"] == []


def test_size_only_two_plans_fit_no_link():
    lab = "Unit 1204"
    r = link_plan(lab, MATCH, [claim("area_sqft", 1450, lab, "c1", "1,450 sqft")])  # C1 and C2 both within tol
    assert r["kit"] is None and r["how"] is None


def test_size_only_none_fits_no_link():
    lab = "Unit 1204"
    r = link_plan(lab, MATCH, [claim("area_sqft", 900, lab, "c1", "900 sqft")])
    assert r["kit"] is None and r["kit_plans"] == []


def test_size_only_loose_size_no_link():
    lab = "Unit 1204"
    r = link_plan(lab, MATCH, [claim("area_sqft", 1136, lab, "c1", "from 1,136 sqft")])
    assert r["kit"] is None


def test_size_in_label_alone():
    r = link_plan("1,136 sqft plan", MATCH, [])
    assert r["kit"]["name"] == "B1" and "by size alone" in r["note"]


def test_beds_given_keeps_old_path():
    lab = "Unit 1204"
    claims = [claim("area_sqft", 1136, lab, "c1"), claim("bedrooms", 1, lab, "c2")]
    r = link_plan(lab, MATCH, claims)
    assert r["kit"]["name"] == "B1" and "by size alone" not in r["note"]
