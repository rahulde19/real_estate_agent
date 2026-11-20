"""Phase 6.2: the pre-filter skips pages that cannot hold a listing, without losing any listing from the answer keys."""
import json
from pathlib import Path

import pytest
from conftest import pg

from homefinder.agent.graph import build_graph
from homefinder.config.config import ROOT, load_rulebook
from homefinder.core.prefilter import has_amount, prefilter_reason
from homefinder.schemas import Listing
from homefinder.services.documents import DocumentRepo

SAMPLES = ROOT / "tavily_sample"
CAPTURES = [("tavily_live_chennai.json", "tavily_live_chennai_gold.json"), ("tavily_ans.json", "tavily_ans_gold.json")]
CFG = load_rulebook()["extraction"]


def load(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("text,expected", [
    ("rent is Rs 9,000", True), ("₹9000 per month", True), ("Rs. 5500", True), ("INR 12000", True),
    ("9500/month", True), ("9500 per month", True), ("15k rent", True), ("1.7 Lac", True), ("Rent: 9500", True),
    ("rent $9,000 a month", False), ("$8,000 a month", False),
    ("built in 2014, 25,720 sq ft, pin 600113", False),  # years, areas and pin codes are not amounts
    ("no numbers here", False),
])
def test_amount_detection(text, expected):
    assert has_amount(text) is expected


def test_reasons():
    ok = "Furnished PG at Rs 9,000 per month with food and wifi included near the metro station."
    assert prefilter_reason("https://a.com/x", "t", ok, 80, []) is None
    assert prefilter_reason("https://www.youtube.com/watch?v=1", "t", ok, 80, ["youtube.com"]) == "blocked_domain"
    assert prefilter_reason("https://m.facebook.com/p", "t", ok, 80, ["facebook.com"]) == "blocked_domain"  # subdomain
    assert prefilter_reason("https://notfacebook.com/p", "t", ok, 80, ["facebook.com"]) is None  # not a suffix match on text
    assert prefilter_reason("https://a.com/x", "t", "Rs 9000", 80, []) == "too_short"
    assert prefilter_reason("https://a.com/x", "t", "x" * 200, 80, []) == "no_amount"


@pytest.mark.parametrize("src,gold", CAPTURES)
def test_no_page_with_an_answer_key_listing_is_skipped(src, gold):
    results, pages = load(src)["results"], load(gold)["pages"]
    assert {r["url"] for r in results} == set(pages)  # the key covers every page
    for r in results:
        reason = prefilter_reason(r["url"], r["title"], r["content"][:1200], CFG["min_chars"], CFG["blocked_domains"])
        if pages[r["url"]]["listings"]:
            assert reason is None, f"lost listings: {r['url']} ({reason})"


def test_live_capture_drops_most_junk():
    results = load("tavily_live_chennai.json")["results"]
    kept = [r for r in results if prefilter_reason(r["url"], r["title"], r["content"][:1200],
                                                   CFG["min_chars"], CFG["blocked_domains"]) is None]
    assert len(results) == 22 and len(kept) == 5  # lease deed, US hostels, finance news, etc. are skipped


def _graph(profile, repo, results, prefilter, calls):
    rb = {**load_rulebook(), "extraction": {**CFG, "prefilter": prefilter, "batch_size": 1}}

    def extract(title, url, text):
        calls.append(url)
        return [pg(name=f"L-{len(calls)}", rent=9000, url=url, private_room=True)]

    return build_graph(search=lambda loc, city: results, extract=extract, explain=lambda p, d: "ok", rulebook=rb,
                       repo=repo, prompt_version="v1", model_id="m", city_lookup=lambda c: None).invoke({"profile": profile})


def test_junk_page_makes_zero_llm_calls_and_keeps_its_text(profile):
    results = [{"title": "News", "url": "https://news/1", "content": "Stocks rose 3.5% in 2024. " * 10},
               {"title": "PG", "url": "https://pg/1", "content": "PG near the metro at Rs 9,000 per month with food. " * 2}]
    repo, calls = DocumentRepo(":memory:"), []
    out = _graph(profile, repo, results, True, calls)
    assert calls == ["https://pg/1"] and out["extraction_stats"]["skipped"] == 1
    junk = repo.get("https://news/1")
    assert junk.extraction_status == "skipped" and junk.failure == "no_amount" and "Stocks" in junk.raw_text


def test_filter_can_be_switched_off_and_is_rechecked_every_run(profile):
    results = [{"title": "News", "url": "https://news/1", "content": "Stocks rose 3.5% in 2024. " * 10}]
    repo, calls = DocumentRepo(":memory:"), []
    _graph(profile, repo, results, True, calls)
    assert calls == []
    _graph(profile, repo, results, False, calls)  # filter switched off: the skipped page is now extracted
    assert calls == ["https://news/1"] and repo.get("https://news/1").extraction_status == "ok"
    assert load_rulebook()["extraction"]["prefilter"] is True and load_rulebook()["extraction"]["batch_size"] == 5


def test_filter_never_overwrites_a_saved_extraction(profile):
    results = [{"title": "PG", "url": "https://pg/1", "content": "PG at Rs 9,000 per month with food and wifi. " * 3}]
    repo, calls = DocumentRepo(":memory:"), []
    _graph(profile, repo, results, False, calls)
    assert repo.get("https://pg/1").extraction_status == "ok"
    # text unchanged but the page would now be filtered by a stricter rule: its saved extraction survives
    rb = {**load_rulebook(), "extraction": {**CFG, "prefilter": True, "min_chars": 10_000}}
    out = build_graph(search=lambda loc, city: results, extract=lambda *a: [], explain=lambda p, d: "ok", rulebook=rb,
                      repo=repo, prompt_version="v1", model_id="m", city_lookup=lambda c: None).invoke({"profile": profile})
    assert out["extraction_stats"]["skipped"] == 1 and repo.get("https://pg/1").extraction_status == "ok"
