"""Phase 8.3: ranking snapshot, determinism and property tests on the golden set. Offline, no LLM."""
import copy
import json
from datetime import date

import pytest

from homefinder.core.rules import decide, evaluate
from homefinder.evals.golden import load_expected, load_pages, load_profiles
from homefinder.evals.ranking_eval import plain_rulebook, run, snapshot

PAGES = load_pages()
PROFILES = load_profiles()
RB = plain_rulebook()


@pytest.mark.parametrize("pid", list(PROFILES))
def test_ranking_matches_the_saved_snapshot(pid):
    """If this fails after a deliberate change, read the diff, then run scripts/snapshot_ranking.py."""
    assert snapshot(run(PROFILES[pid])) == load_expected()["profiles"][pid]


@pytest.mark.parametrize("pid", list(PROFILES))
def test_same_data_and_profile_give_the_same_ranking_every_time(pid):
    first = snapshot(run(PROFILES[pid]))
    assert all(snapshot(run(PROFILES[pid])) == first for _ in range(3))


def _passing(out) -> set[str]:
    return {c.listing.name for c in out["candidates"] if not c.rejections}


@pytest.mark.parametrize("pid", list(PROFILES))
def test_a_higher_budget_or_more_cash_never_removes_an_option(pid):
    p = PROFILES[pid]
    base = _passing(run(p))
    for kw in ({"budget": p.budget * 1.5}, {"upfront_cash": p.upfront_cash * 2}, {"commute_tolerance": 90}):
        assert base <= _passing(run(p.model_copy(update=kw))), kw


@pytest.mark.parametrize("pid", list(PROFILES))
def test_a_repeated_ad_never_changes_the_ranking(pid):
    p = PROFILES[pid]
    doubled = []
    for page in PAGES:  # every page is also posted again on another URL with the same text
        doubled += [page, {**copy.deepcopy(page), "url": page["url"] + "?repost=1"}]
    assert snapshot(run(p, doubled)) == snapshot(run(p))


@pytest.mark.parametrize("pid", list(PROFILES))
def test_unknown_data_never_rejects(pid):
    """A listing with its commute and lifestyle flags unknown fails no commute cutoff and no must-have."""
    p = PROFILES[pid].model_copy(update={"must_have": {"privacy": True, "food": True, "furnishing": True, "noise": True}})
    blank = {"commute_minutes": None, "private_room": None, "meals_included": None, "kitchen_access": None,
             "furnishing": None, "vibe": None, "gender_restricted": None}
    for c in run(p)["candidates"]:
        if c.listing.kind == "flat" and p.mover != "family":
            continue  # a flat is assumed private / with a kitchen when the text says nothing; that is a stated rule
        e = evaluate(p, c.listing.model_copy(update=blank), RB)
        assert not {r.kind for r in e.rejections} & {"commute", "must_have"}, c.listing.name


@pytest.mark.parametrize("pid", list(PROFILES))
def test_confidence_never_changes_a_score(pid):
    for c in run(PROFILES[pid])["candidates"]:
        scores = set()
        for conf in ("high", "medium", "low", None):
            for missing in ([], ["deposit", "area", "commute_minutes"]):
                e = evaluate(PROFILES[pid], c.listing.model_copy(update={"extraction_confidence": conf, "missing_fields": missing}), RB,
                             None, None, date(2026, 12, 1))
                scores.add((e.score, tuple(r.kind for r in e.rejections)))
        assert len(scores) == 1, c.listing.name


def test_the_snapshot_file_is_complete_and_marked_unreviewed():
    exp = load_expected()
    assert set(exp["profiles"]) == set(PROFILES)
    assert all(v["ranked"] is not None and v["closest_misses"] is not None for v in exp["profiles"].values())
    assert exp["reviewed"] is False  # flip only when a person has read every profile's ranking


def test_a_family_never_gets_a_pg_and_a_pg_is_never_its_closest_miss():
    p = PROFILES["family_of_four"]
    out = run(p)
    d = out["decision"]
    assert any(c.listing.kind == "pg" for c in out["candidates"])  # the golden set does hold PGs
    assert d.ranked and all(c.listing.kind == "flat" for c in d.ranked + d.closest_misses) and d.recommended_kind == "flat"
    pgs = [c for c in out["candidates"] if c.listing.kind == "pg"]
    assert all(any(r.kind == "unsuitable" for r in c.rejections) for c in pgs)
    other = snapshot(run(PROFILES["three_friends_sharing"]))  # friends in a flat still see PGs: only a family is affected
    assert any(r["name"] for r in other["ranked"])
    off = run(p, rulebook=plain_rulebook(family_excludes_pg=False))
    assert any(c.listing.kind == "pg" for c in off["decision"].ranked)  # the rulebook switch turns it off
