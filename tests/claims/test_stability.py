"""Claim reading is stable: fixed candidate statements, the model as judge over them, canonical form, dedupe and a
per-page cache. The model is stubbed (no network, no CLI). Invented data only (418 Linden Grove Ave)."""
import json

import pytest

import harness.claims.extract as ex
from harness.claims import validate_claim

ADDRESS = "418 Linden Grove Ave, Houston, TX 77002"
PAGE = """Linden Grove Residences
418 Linden Grove Ave
Starting base rent $1,950
Available as soon as: 11/14/2026
A 6-story community built in 2019.
Community Amenities
Resort-style pool and spa
Outdoor lounge with grills, fire pits, and a dog park
Fitness center
24-hour fitness center
Plan A1
1 Bed
/
1 Bath
640-700 Sq Ft
4 Available Units
Plan B2
2 Beds
/
2 Baths
1,050 Sq Ft
2 Available Units
"""


def snap(text=PAGE):
    return {"url": "https://example.test/900001", "title": "Linden Grove", "address_text": ADDRESS,
            "text": text, "jsonld": []}


def cands_of(s):
    lines = s["text"].splitlines()
    self_lines, excluded = ex._line_roles(lines, ex._self_re(ADDRESS))
    return ex.candidates(lines, excluded, self_lines)


def answers(cands):
    """What a model would say about PAGE's statements (by statement text, numbered from the candidate list)."""
    n = {}
    for c in cands:
        n.setdefault((c["text"], c["label"]), c["n"])

    def at(text, label=None):
        return n[(text, label)]
    return [
        {"n": at("A 6-story community built in 2019."), "kind": "stories", "value": 6, "scope": "building"},
        {"n": at("A 6-story community built in 2019."), "kind": "year_built", "value": 2019},
        {"n": at("Resort-style pool and spa"), "kind": "amenity", "value": "pool"},
        {"n": at("Resort-style pool and spa"), "kind": "amenity", "value": "spa"},
        {"n": at("Outdoor lounge with grills, fire pits, and a dog park"), "kind": "amenity", "value": "grills"},
        {"n": at("Fitness center"), "kind": "amenity", "value": "Fitness center"},
        {"n": at("24-hour fitness center"), "kind": "amenity", "value": "24-hour fitness center"},
        {"n": at("1 Bed", "Plan A1"), "kind": "bedrooms", "value": 1, "label": "Plan A1"},
        {"n": at("1 Bath", "Plan A1"), "kind": "bathrooms", "value": 1, "label": "A1"},
        {"n": at("640-700 Sq Ft", "Plan A1"), "kind": "area_sqft", "value": 700},
        {"n": at("2 Beds", "Plan B2"), "kind": "bedrooms", "value": "2 beds"},
        {"n": at("2 Baths", "Plan B2"), "kind": "bathrooms", "value": 2.0},
        {"n": at("1,050 Sq Ft", "Plan B2"), "kind": "area_sqft", "value": "1,050"},
        {"n": at("1,050 Sq Ft", "Plan B2"), "kind": "area_sqft", "value": 1050},  # said twice
    ]


@pytest.fixture
def priv(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("RC_NO_CACHE", raising=False)
    return tmp_path


@pytest.fixture
def model(monkeypatch):
    """Stub the model: answer from answers(), count calls, remember the prompts."""
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return answers(cands_of(snap()))
    monkeypatch.setattr(ex, "_ask_cached", ask)
    return calls


def keyset(claims):
    return [(c["kind"], c["value"], c["label"]) for c in claims]


# ---------------------------------------------------------------- deterministic candidates

def test_candidates_are_fixed_numbered_and_drop_volatile_lines():
    a, b = cands_of(snap()), cands_of(snap())
    assert a == b and [c["n"] for c in a] == list(range(1, len(a) + 1))
    texts = [c["text"] for c in a]
    assert "Starting base rent $1,950" not in texts and "Available as soon as: 11/14/2026" not in texts
    assert "4 Available Units" not in texts and "/" not in texts
    # a card's repeated spec lines stay, each with its own card's label
    assert {(c["text"], c["label"]) for c in a} >= {("1 Bed", "Plan A1"), ("2 Beds", "Plan B2")}


def test_long_paragraph_is_cut_into_sentences():
    para = ("Linden Grove offers a resort-style pool. " * 3).strip() + " The six-story tower was finished in 2019."
    c = cands_of(snap("418 Linden Grove Ave\n" + para))
    assert "The six-story tower was finished in 2019." in [x["text"] for x in c]


def test_volatile_only_for_price_date_availability_lines():
    assert ex.volatile("$2,509-$4,609") and ex.volatile("Starting base rent $2,509")
    assert ex.volatile("Available as soon as: 12/05/2026") and ex.volatile("1 Available Unit")
    assert not ex.volatile("2 Beds $2,100/mo") and not ex.volatile("Fitness center")
    assert not ex.volatile("Built in 2019")


# ---------------------------------------------------------------- same answers -> same claims

def test_same_candidates_same_answers_give_identical_claims(model, monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)  # no cache: both runs read the reply
    one, two = ex.extract_claims(snap()), ex.extract_claims(snap())
    assert one == two and len(model) == 2
    for c in one:
        assert validate_claim(c) == [], c
        assert ex._norm(c["quote"]) in ex._norm(PAGE)


def test_answer_order_does_not_change_the_claims(monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    monkeypatch.setattr(ex, "_ask_cached", lambda p: answers(cands_of(snap())))
    one = ex.extract_claims(snap())
    monkeypatch.setattr(ex, "_ask_cached", lambda p: list(reversed(answers(cands_of(snap())))))
    assert ex.extract_claims(snap()) == one


def test_canonical_values_labels_and_duplicates(model, monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    claims = ex.extract_claims(snap())
    ks = keyset(claims)
    # labels come from the card, numbers are canonical, a plan's size range keeps its low end
    for k in [("bedrooms", 1, "Plan A1"), ("bathrooms", 1, "Plan A1"), ("area_sqft", 640, "Plan A1"),
              ("bedrooms", 2, "Plan B2"), ("bathrooms", 2, "Plan B2"), ("area_sqft", 1050, "Plan B2"),
              ("stories", 6, None), ("year_built", 2019, None)]:
        assert ks.count(k) == 1, (k, ks)
    # "Fitness center" and "24-hour fitness center" name the same record amenity: one claim, the first on the page
    fit = [c for c in claims if c["kind"] == "amenity" and "itness" in c["value"]]
    assert [c["value"] for c in fit] == ["Fitness center"], fit
    # a short list item's value is its own words: "pool" + "spa" answers are one "Resort-style pool and spa"
    assert [c["value"] for c in claims if "pool" in str(c["value"]).lower()] == ["Resort-style pool and spa"]
    assert isinstance([c for c in claims if c["kind"] == "bathrooms"][1]["value"], int)


def test_compound_amenity_list_is_split_by_a_fixed_rule(model, monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    claims = ex.extract_claims(snap())
    vals = [c["value"] for c in claims if c["kind"] == "amenity"]
    split = [v for v in vals if v in ("Grills", "Fire pits", "A dog park")]
    # record-named features (grills, dog park) fill the slots before the rest; page order inside each rank
    assert split == ["Grills", "A dog park", "Fire pits"], vals
    assert ex.split_list("barbecue grills, lounge seating, and firepits") == ["Barbecue grills", "Lounge seating",
                                                                               "Firepits"]
    assert ex.split_list("Clubroom with chef’s kitchen and dining area") == ["Clubroom with chef's kitchen and dining area"]
    assert ex.split_list("Pool, spa with lounge seats") == ["Pool", "Spa with lounge seats"]


def test_page_order_within_rank(model, monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    claims = ex.extract_claims(snap())
    nums = [c for c in claims if c["kind"] in ex.NUMERIC_KINDS]
    assert [c["kind"] for c in nums[:2]] == ["stories", "year_built"]  # the page says them first
    assert claims[:len(nums)] == nums and [c["id"] for c in claims] == [f"c{i}" for i in range(1, len(claims) + 1)]


def test_unlabeled_unit_number_dropped_on_a_plan_page(monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    text = "418 Linden Grove Ave\nStudio to 2 bedroom homes, 2 Bedrooms\n" + PAGE

    def ask(prompt):
        c = cands_of(snap(text))
        hero = next(x["n"] for x in c if x["text"].startswith("Studio to 2"))
        return [{"n": hero, "kind": "bedrooms", "value": 2}] + answers(c)
    monkeypatch.setattr(ex, "_ask_cached", ask)
    claims = ex.extract_claims(snap(text))
    assert not [c for c in claims if c["kind"] == "bedrooms" and c["label"] is None], claims


# ---------------------------------------------------------------- per-page cache

def test_cache_hit_returns_identical_list_without_the_model(priv, model):
    first = ex.extract_claims(snap())
    assert len(model) == 1
    assert list((priv / "rc_cache" / "claims" / "pages").glob("*.json"))
    again = ex.extract_claims(snap())
    assert again == first and len(model) == 1


def test_price_and_date_changes_still_hit_the_cache(priv, model):
    first = ex.extract_claims(snap())
    moved = PAGE.replace("$1,950", "$2,025").replace("11/14/2026", "12/01/2026").replace("4 Available", "3 Available")
    assert ex.extract_claims(snap(moved)) == first and len(model) == 1


def test_changed_page_text_misses_the_cache(priv, model):
    ex.extract_claims(snap())
    ex.extract_claims(snap(PAGE.replace("A 6-story community", "A 7-story community")))
    assert len(model) == 2


def test_no_cache_switch_rereads_and_writes_in_place(priv, model, monkeypatch):
    first = ex.extract_claims(snap())
    monkeypatch.setenv("RC_NO_CACHE", "1")
    assert ex.extract_claims(snap()) == first and len(model) == 2
    monkeypatch.delenv("RC_NO_CACHE")
    ex.extract_claims(snap())
    assert len(model) == 2


def test_version_bump_invalidates(priv, model, monkeypatch):
    ex.extract_claims(snap())
    key = ex.page_key(snap())
    monkeypatch.setattr(ex, "EXTRACTOR_VERSION", ex.EXTRACTOR_VERSION + "-next")
    assert ex.page_key(snap()) != key
    ex.extract_claims(snap())
    assert len(model) == 2


def test_stale_version_in_file_is_not_used(priv, model):
    ex.extract_claims(snap())
    path = ex._page_cache_path(snap())
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "version": "old"}), encoding="utf-8")
    ex.extract_claims(snap())
    assert len(model) == 2


def test_model_failure_is_not_cached(priv, monkeypatch):
    def boom(prompt):
        raise RuntimeError("cli down")
    monkeypatch.setattr(ex, "_ask_cached", boom)
    ex.extract_claims(snap())
    assert not list((priv / "rc_cache" / "claims").glob("pages/*.json"))


def test_one_model_call_per_page(model, monkeypatch):
    monkeypatch.delenv("RC_PRIVATE_DIR", raising=False)
    ex.extract_claims(snap())
    assert len(model) == 1 and "1. " in model[0] and "[plan: Plan A1]" in model[0]
