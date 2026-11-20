"""Phase 8.1: the golden set is well formed, its keys are consistent, and the pipeline runs on it. Offline, no LLM."""
import json
from collections import Counter

import pytest

from conftest import plain_rulebook
from homefinder.agent.extraction import rent_supported
from homefinder.agent.graph import build_graph
from homefinder.config.config import ROOT
from homefinder.core.prefilter import prefilter_reason
from homefinder.evals.golden import (GOLDEN_DIR, as_results, gold_answer, key_listings, load_expected, load_pages,
                                     load_profiles, stub_extractor)
from homefinder.schemas import Aspect, UserProfile
from homefinder.services.documents import DocumentRepo

PAGES = load_pages()
REQUIRED_TAGS = {"price_range", "multi_listing", "junk", "review", "forum", "two_city", "builder", "contradiction",
                 "duplicate_pair", "evidence", "no_listing"}


def test_pages_are_well_formed_and_unique():
    assert len(PAGES) >= 30
    assert len({p["id"] for p in PAGES}) == len(PAGES) and len({p["url"] for p in PAGES}) == len(PAGES)
    for p in PAGES:
        assert p["origin"] in ("sample", "live-capture", "synthetic") and p["reviewed"] in ("claude", "user")
        assert p["id"].startswith("s" if p["origin"] == "synthetic" else "g")
        assert p["title"] and p["content"].strip() and len(p["content"]) <= 1200  # as the app would see it
        assert p["expected"]["source_type"] in ("ad", "builder", "forum", "review", None)
        assert p["note"], f"{p['id']} has no note saying why the key is what it is"


def test_every_key_is_a_valid_answer_with_rents_that_appear_in_the_page():
    for p in PAGES:
        answer = gold_answer(p)  # validates listings, evidence and source_type against the schemas
        assert len(answer.listings) == len(p["expected"]["listings"])
        text = f"{p['title']} {p['content']}"
        for raw, listing in zip(p["expected"]["listings"], key_listings(p)):
            for rent in [listing.rent, *raw.get("alt_rents", [])]:
                assert rent_supported(rent, text), f"{p['id']}: rent {rent} is not in the page text"
        for e in p["expected"]["evidence"]:
            assert e["aspect"] in Aspect.__args__


def test_coverage_of_the_hard_cases():
    tags = Counter(t for p in PAGES for t in p["tags"])
    assert REQUIRED_TAGS <= set(tags), REQUIRED_TAGS - set(tags)
    assert len({p["city"] for p in PAGES}) >= 2
    assert sum(1 for p in PAGES if p["expected"]["evidence"]) >= 3
    assert sum(1 for p in PAGES if p["origin"] == "synthetic") >= 10 and sum(1 for p in PAGES if p["origin"] != "synthetic") >= 20
    assert any(p["expected"]["evidence"] and not p["expected"]["listings"] for p in PAGES)  # evidence without a listing
    two = [p for p in PAGES if p["id"] in ("s07",)]
    assert two and [e["aspect"] for e in two[0]["expected"]["evidence"]] == ["property_condition", "management"]  # two items


def test_no_rupee_amount_page_is_marked_as_junk_or_evidence():
    """Pages whose key has a listing must pass the prefilter; the ones it skips are junk or review text (known limit)."""
    for p in PAGES:
        reason = prefilter_reason(p["url"], p["title"], p["content"], 80, [])
        if p["expected"]["listings"]:
            assert reason is None, f"{p['id']} has a key listing but the prefilter skips it ({reason})"
        elif reason == "no_amount" and p["expected"]["evidence"]:
            assert "review" in p["tags"] or "forum" in p["tags"]


@pytest.mark.parametrize("src,gold", [("tavily_ans.json", "tavily_ans_gold.json"),
                                      ("tavily_live_chennai.json", "tavily_live_chennai_gold.json")])
def test_pages_from_the_earlier_gold_files_agree_with_them(src, gold):
    """The older per-capture keys stay where the prefilter test and the benchmark read them; the two copies must not drift."""
    results = json.loads((ROOT / "tavily_sample" / src).read_text(encoding="utf-8"))["results"]
    keys = json.loads((ROOT / "tavily_sample" / gold).read_text(encoding="utf-8"))["pages"]
    by_url = {p["url"]: p for p in PAGES}
    for r in results:
        page = by_url[r["url"]]
        assert page["content"] == r["content"][:1200] and page["expected"]["listings"] == keys[r["url"]]["listings"]


def test_duplicate_pairs_describe_one_property():
    groups = Counter(p["dup_group"] for p in PAGES if p.get("dup_group"))
    assert groups and all(n >= 2 for n in groups.values())
    from homefinder.core.dedup import dedupe
    pair = [l for p in PAGES if p.get("dup_group") == "palm-residency" for l in key_listings(p)]
    assert len(pair) == 2 and len(dedupe(pair)) == 1  # the dedup step merges them (7.2)


def test_profiles_are_valid_and_distinct():
    profiles = load_profiles()
    assert len(profiles) >= 5 and all(isinstance(p, UserProfile) for p in profiles.values())
    assert len({p.model_dump_json() for p in profiles.values()}) == len(profiles)
    assert {"Chennai", "Pune"} <= {p.city for p in profiles.values()}
    assert {p.mover for p in profiles.values()} == {"solo", "friends", "family"}
    assert any(p.horizon_months == 6 for p in profiles.values()) and any(p.must_have.get("privacy") for p in profiles.values())
    cities = {p["city"] for p in PAGES}
    assert all(p.city in cities for p in profiles.values())  # every profile has pages for its city


def test_expected_file_lists_every_profile():
    assert set(load_expected()["profiles"]) == set(load_profiles())


@pytest.mark.parametrize("profile_id", list(load_profiles()))
def test_pipeline_runs_on_the_golden_set_with_the_keys_as_extractor(profile_id):
    """Dry run for 8.3: nothing crashes, and everything shown carries provenance, confidence and flags. Ranks are not checked yet."""
    profile = load_profiles()[profile_id]
    pages = [p for p in PAGES if p["city"].lower() == profile.city.lower()]
    g = build_graph(search=lambda loc, city: as_results(pages), extract=stub_extractor(pages), explain=lambda p, d: "ok",
                    repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="golden", rulebook=plain_rulebook(),
                    city_lookup=lambda c: None, agent_source="golden")
    out = g.invoke({"profile": profile})
    d = out["decision"]
    assert len(out["listings"]) + out["extraction_stats"]["duplicates_merged"] == sum(len(p["expected"]["listings"]) for p in pages)
    assert len(d.ranked) == min(7, len(out["listings"]) - len(d.rejected))  # ranked is the top 7 of those that pass
    for c in d.ranked + d.closest_misses:
        assert c.listing.provenance and c.listing.provenance.source_url.startswith("http") and c.confidence in ("high", "medium", "low")
        assert c.listing.property_id and 0 <= c.score <= 10
    assert all(len(c.rejections) == 1 for c in d.closest_misses)
    assert len(d.ranked) + len(d.closest_misses) <= 7


def test_pages_named_in_review_md_are_exactly_the_user_reviewed_ones_and_off_topic_pages_expect_no_evidence():
    import re
    review = (GOLDEN_DIR / "review.md").read_text(encoding="utf-8")
    rows = [line.split("|")[1] for line in review.splitlines() if line.startswith("| `")]  # first cell of each decision row
    named = set(re.findall(r"`([gs]\d\d)`", " ".join(rows)))
    by_id = {p["id"]: p for p in PAGES}
    assert named <= set(by_id)
    confirmed = {i for i, p in by_id.items() if p["reviewed"] == "user"}
    assert named == confirmed and len(confirmed) == 16
    for p in PAGES:
        if "off_topic" in p["tags"]:
            assert not p["expected"]["evidence"] and not p["expected"]["listings"] and p["expected"]["source_type"] is None
