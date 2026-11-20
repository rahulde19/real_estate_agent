"""Phase 4: city cost dataset, extras estimate and rent benchmark. Uses the real CSV in data/."""
import pytest
from conftest import pg

from homefinder.agent.graph import build_graph
from conftest import plain_rulebook
from homefinder.core.formulas import non_rent_extras, rent_vs_benchmark_pct
from homefinder.core.rules import evaluate
from homefinder.schemas import CityCosts, Listing
from homefinder.services.documents import DocumentRepo
from homefinder.services.city_costs import city_key, load_city_costs, lookup_city_costs


@pytest.fixture(scope="module")
def table():
    return load_city_costs(max_rent_share=0.75)


# ---- 4.1 loader ----
def test_chennai_row_loads_clean(table):
    c = table["chennai"]
    assert c.name == "Chennai"  # leading space cleaned
    assert c.cost_one_person == pytest.approx(41295.99) and c.rent_one_person == pytest.approx(13970.34)
    assert c.data_quality == "ok" and c.usable


@pytest.mark.parametrize("name", ["Chennai", " chennai ", "Chennai, Tamil Nadu"])
def test_city_lookup_normalises_names(name):
    assert lookup_city_costs(name).name == "Chennai"


def test_city_with_state_in_csv_resolves():
    assert city_key("Hyderabad, Telangana") == "hyderabad"
    assert lookup_city_costs("Hyderabad") is not None


def test_unknown_city_returns_none():
    assert lookup_city_costs("Atlantis") is None


def test_repeated_pair_is_suspect_but_usable(table):
    k = table["kolkata"]
    assert k.data_quality == "suspect" and k.usable


def test_impossible_rows_are_unusable_and_fall_back(table):
    for name in ("surat", "nellore"):
        assert table[name].data_quality == "suspect" and not table[name].usable
        assert lookup_city_costs(name) is None  # callers use the labelled defaults


def test_flag_counts(table):
    repeated = [c for c in table.values() if c.usable and c.data_quality == "suspect"]
    assert len(repeated) >= 14  # the 16 repeated-pair cities, minus any that are also unusable
    assert sum(1 for c in table.values() if not c.usable) >= 2


def test_max_rent_share_is_configurable(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("City,cost_one_person_inr,rent_one_person_inr\n A,1000,700\n B,1000,800\n")
    assert load_city_costs(f, 0.75)["a"].usable and not load_city_costs(f, 0.75)["b"].usable
    assert not load_city_costs(f, 0.6)["a"].usable


# ---- 4.2 extras ----
def test_chennai_food_and_utilities_estimates(table):
    c = table["chennai"]
    food, utilities = non_rent_extras(c.cost_one_person, c.rent_one_person, 0.5, 0.7)
    assert food == pytest.approx(9564, abs=1)
    assert utilities == pytest.approx(4099, abs=1)


def test_meal_cost_uses_city_data_and_falls_back(profile, rb, table):
    base = dict(private_room=True)
    with_city = lambda meals: evaluate(profile, pg(meals_included=meals, **base), rb, table["chennai"])
    food = non_rent_extras(table["chennai"].cost_one_person, table["chennai"].rent_one_person, 0.5, 0.7)[0]
    assert with_city(False).effective_monthly - with_city(True).effective_monthly == pytest.approx(food)
    assert with_city(False).cost_basis == "city data"
    default = evaluate(profile, pg(meals_included=False, **base), rb)
    assert default.cost_basis == "default assumption"
    assert default.effective_monthly - evaluate(profile, pg(meals_included=True, **base), rb).effective_monthly \
        == rb["defaults"]["meal_cost"]


def test_flat_extras_use_city_utilities(profile, rb, table):
    p = profile.model_copy(update={"food": "eat out", "must_have": {}})
    flat = Listing(name="F", kind="flat", rent=10000, deposit=0, brokerage=0, furnishing="furnished")
    c = table["chennai"]
    utilities = non_rent_extras(c.cost_one_person, c.rent_one_person, 0.5, 0.7)[1]
    assert evaluate(p, flat, rb, c).effective_monthly == pytest.approx(10000 + utilities, abs=1)


def test_housing_share_in_rulebook_changes_estimate(profile, rb, table):
    rb2 = {**rb, "money": {**rb["money"], "housing_linked_share": 0.25}}
    args = (profile, pg(meals_included=False, private_room=True))
    assert evaluate(*args, rb2, table["chennai"]).effective_monthly < evaluate(*args, rb, table["chennai"]).effective_monthly


def test_suspect_city_data_is_labelled(profile, rb, table):
    c = evaluate(profile, pg(private_room=True), rb, table["kolkata"])
    assert c.cost_basis == "city data (suspect)"


# ---- 4.3 benchmark ----
def test_benchmark_percent_sign():
    assert rent_vs_benchmark_pct(11200, 10000) == pytest.approx(12)
    assert rent_vs_benchmark_pct(9200, 10000) == pytest.approx(-8)


def test_candidate_benchmark_pg_shared_flat_and_family(profile, rb, table):
    chennai = table["chennai"]  # one-person rent 13,970.34
    assert evaluate(profile, pg(rent=9000), rb, chennai).rent_vs_benchmark_pct < 0
    friends = profile.model_copy(update={"mover": "friends", "group_size": 3, "food": "eat out", "must_have": {}})
    flat = Listing(name="F", kind="flat", rent=60000, deposit=0, brokerage=0, furnishing="furnished")
    assert evaluate(friends, flat, rb, chennai).rent_vs_benchmark_pct == pytest.approx(
        rent_vs_benchmark_pct(20000, chennai.rent_one_person))
    family = profile.model_copy(update={"mover": "family", "group_size": 4, "food": "eat out", "must_have": {}})
    assert evaluate(family, flat, rb, chennai).rent_vs_benchmark_pct is None
    assert evaluate(profile, pg(), rb, None).rent_vs_benchmark_pct is None


def test_benchmark_does_not_change_score(profile, rb, table):
    l = pg(private_room=True, meals_included=True)
    assert evaluate(profile, l, rb, table["chennai"]).score == evaluate(profile, l, rb, None).score


def test_graph_passes_city_costs_to_evaluate(profile):
    seen = []
    g = build_graph(
        search=lambda loc, city: [{"title": "t", "url": "u", "content": "c"}],
        extract=lambda title, url, text: [pg(name="A", private_room=True, meals_included=True)],
        repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="m", rulebook=plain_rulebook(),
        explain=lambda p, d: "ok",
        city_lookup=lambda city: seen.append(city) or CityCosts(name="X", cost_one_person=30000, rent_one_person=10000),
    )
    out = g.invoke({"profile": profile})
    assert seen == ["Chennai"] and out["decision"].ranked[0].cost_basis == "city data"
