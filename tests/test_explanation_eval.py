"""Phase 8.4: the explanation checks catch each defect and pass a faithful text. Offline, no LLM."""
import pytest

from homefinder.evals.explanation_eval import check
from homefinder.evals.golden import load_profiles
from homefinder.evals.ranking_eval import run
from homefinder.services.llm import explanation_facts

FACTS = {
    "user": {"city": "Chennai", "budget": 12000.0, "upfront_cash": 40000.0, "horizon_months": 12, "move_in_date": "2026-12-01"},
    "recommended_type": "pg",
    "options_in_rank_order": [
        {"name": "Lakshmi Ladies PG", "type": "pg", "area": "Velachery", "monthly_cost": 9500, "upfront_cash": 21000,
         "score_out_of_10": 7.1, "sub_scores": {"affordability": 8.2}, "unknowns": [], "confidence": "high", "flags": ["deposit estimated"]},
        {"name": "Maple Living double room", "type": "pg", "area": "Taramani", "monthly_cost": 11000, "upfront_cash": 30000,
         "score_out_of_10": 6.0, "sub_scores": {"affordability": 6.1}, "unknowns": [], "confidence": "medium", "flags": ["re-verify"]},
    ],
}
GOOD = """A PG suits you: both options fit your budget of Rs 12,000 and your cash of Rs 40,000.

1. Lakshmi Ladies PG, Velachery: Rs 9,500 a month, Rs 21,000 upfront, score 7.1. Caveat: the deposit is estimated.
2. Maple Living double room, Taramani: Rs 11,000 a month, Rs 30,000 upfront, score 6.0. Caveat: please re-verify this ad.

Listings come from web snippets, so verify everything before you pay anything."""


def test_a_faithful_explanation_passes():
    assert check(GOOD, FACTS) == []


@pytest.mark.parametrize("text, expect", [
    (GOOD.replace("Rs 9,500", "Rs 9,200"), "numbers not in the facts: 9200"),
    (GOOD.replace("score 7.1", "score 8.4"), "numbers not in the facts: 8.4"),
    (GOOD.replace("Lakshmi Ladies PG", "Zeta PG", 1), "option not mentioned: Lakshmi Ladies PG"),
    (GOOD.replace("the deposit is estimated", "it looks fine"), "flag not mentioned for Lakshmi Ladies PG: deposit estimated"),
    (GOOD.replace("please re-verify this ad", "it looks fine"), "flag not mentioned for Maple Living double room: re-verify"),
    (GOOD + "\nIt is about 25 minutes from your office.", "mentions a commute time"),
    (GOOD.replace("Listings come from web snippets, so verify everything before you pay anything.", "Enjoy your new home."),
     "no reminder to verify listings at the end"),
])
def test_each_defect_is_caught(text, expect):
    assert expect in check(text, FACTS)


def test_swapped_order_is_caught():
    lines = GOOD.splitlines()
    swapped = "\n".join([lines[0], "", lines[3], lines[2], "", lines[-1]])
    assert "options are not in the given rank order" in check(swapped, FACTS)


def test_list_numbering_and_the_users_own_numbers_are_not_flagged():
    assert check(GOOD.replace("Rs 12,000", "Rs 12000"), FACTS) == []  # budget and horizon come from the profile


@pytest.mark.parametrize("pid", list(load_profiles()))
def test_facts_for_the_golden_profiles_are_well_formed(pid):
    p = load_profiles()[pid]
    d = run(p)["decision"]
    f = explanation_facts(p, d)
    names = [o["name"] for o in f["options_in_rank_order"]]
    assert names == [c.listing.name for c in d.ranked] and f["recommended_type"] == d.recommended_kind
    assert all(o["monthly_cost"] > 0 and 0 <= o["score_out_of_10"] <= 10 for o in f["options_in_rank_order"])
    if pid == "family_of_four":
        assert all(o["type"] == "flat" for o in f["options_in_rank_order"])
    # a model that copies every option's own figures and flags back should pass the number, name, order and flag checks
    lines = [f"{i}. {o['name']}: Rs {o['monthly_cost']} a month, Rs {o['upfront_cash']} upfront, score {o['score_out_of_10']}. "
             + " ".join(o["flags"]) for i, o in enumerate(f["options_in_rank_order"], 1)]
    text = "A short answer.\n" + "\n".join(lines) + "\nPlease verify the listings before paying."
    flag_words = {"deposit estimated": "estimated", "re-verify": "re-verify"}
    for fl in {x for o in f["options_in_rank_order"] for x in o["flags"]} - set(flag_words):
        assert fl.startswith("first seen")  # the only other flag kind
    assert check(text, f) == []
