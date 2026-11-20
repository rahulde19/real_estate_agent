"""Phase 8.2: the scoring that grades the real model against the golden set. Offline; the model is simulated."""
import pytest

from homefinder.evals.extraction_eval import aggregate, match_listings, rent_matches, score_page
from homefinder.evals.golden import gold_answer, key_listings, load_pages
from homefinder.schemas import EvidenceItem, Listing, ListingBatch

PAGES = load_pages()
BY_ID = {p["id"]: p for p in PAGES}


def perfect():
    return [score_page(p, gold_answer(p)) for p in PAGES]


def test_a_perfect_reader_scores_everything_right():
    a = aggregate(perfect())
    assert a["recall"] == a["precision"] == a["schema_valid_rate"] == a["source_type_accuracy"] == 1.0
    assert a["invented_rent_rate"] == 0 and a["false_positive_listings"] == 0 and a["pages_with_misses"] == []
    assert a["evidence"]["precision"] == a["evidence"]["recall"] == a["evidence"]["severity_agreement"] == 1.0
    assert all(v["rate"] == 1.0 for v in a["field_accuracy"].values()) and a["confidence_agreement"] == 1.0
    assert a["listings_in_key"] == a["listings_found"] == 28 and {"deposit", "sharing", "area"} <= set(a["field_accuracy"])


def test_empty_answers_miss_every_listing_but_stay_valid():
    a = aggregate([score_page(p, ListingBatch()) for p in PAGES])
    assert a["recall"] == 0 and a["precision"] is None and a["schema_valid_rate"] == 1.0
    assert a["evidence"]["recall"] == 0 and set(a["pages_with_misses"]) >= {"g01", "s01", "s07"}


def test_invalid_output_counts_against_validity_and_recall():
    s = score_page(BY_ID["s01"], None)
    assert not s.valid and s.n_key == 3 and s.found == 0
    a = aggregate([s, score_page(BY_ID["s02"], gold_answer(BY_ID["s02"]))])
    assert a["schema_valid_rate"] == 0.5 and a["recall"] == pytest.approx(1 / 4)


def test_rent_matching_uses_kind_tolerance_and_alt_rents():
    key = {"kind": "pg", "rent": 12000, "alt_rents": [7000]}
    pg = lambda rent, kind="pg": Listing(name="x", kind=kind, rent=rent)
    assert rent_matches(key, pg(12000)) and rent_matches(key, pg(12001)) and rent_matches(key, pg(7000))
    assert not rent_matches(key, pg(12002)) and not rent_matches(key, pg(12000, "flat")) and not rent_matches(key, pg(9500))


def test_matching_is_one_to_one():
    keys = [{"kind": "pg", "rent": 9000}, {"kind": "pg", "rent": 9000}]
    got = [Listing(name="a", kind="pg", rent=9000)]
    assert match_listings(keys, got) == [(0, 0)]  # one returned listing cannot satisfy two key listings


def test_extra_listings_and_invented_rents_are_counted():
    page = BY_ID["s03"]  # key: one flat at Rs 14,500
    extra = ListingBatch(source_type="ad", listings=[*key_listings(page), Listing(name="ghost", kind="flat", rent=99999)])
    s = score_page(page, extra)
    assert (s.found, s.false_positives, s.invented) == (1, 1, 1)
    assert aggregate([s])["precision"] == 0.5


def test_field_scoring_only_where_the_key_states_a_value():
    page = BY_ID["s01"]  # keys state deposit, area, sharing, private_room, meals_included
    answer = gold_answer(page)
    answer.listings[0].deposit = 12345                    # wrong
    answer.listings[1].area = "kothrud"                   # area compares case-insensitively
    answer.listings[2].furnishing = "furnished"           # not in the key: not scored
    s = score_page(page, answer)
    assert s.fields["deposit"] == [2, 3] and s.fields["area"] == [3, 3] and "furnishing" not in s.fields


def test_source_type_and_off_topic_pages():
    page = BY_ID["g25"]  # Yelp hostel in New York: off topic
    assert "off_topic" in page["tags"]
    clean = score_page(page, ListingBatch(source_type="review"))
    assert not clean.source_type_scored and clean.ev_fp == 0
    noisy = score_page(page, ListingBatch(source_type="review", evidence=[
        EvidenceItem(subject="HI New York City Hostel", aspect="safety", polarity="positive")]))
    assert noisy.ev_fp == 1 and aggregate([noisy])["evidence"]["fp"] == 1  # evidence on an off-topic page is an error
    wrong = score_page(BY_ID["s01"], ListingBatch(source_type="forum", listings=key_listings(BY_ID["s01"])))
    assert wrong.source_type_scored and not wrong.source_type_ok


def test_evidence_matches_on_aspect_and_polarity_and_reports_severity_separately():
    page = BY_ID["s07"]  # property_condition positive, management negative (both medium)
    e = lambda aspect, pol, sev="medium": EvidenceItem(subject="Palm Residency PG", aspect=aspect, polarity=pol, severity=sev)
    both = score_page(page, ListingBatch(source_type="review", evidence=[e("property_condition", "positive"), e("management", "negative", "high")]))
    assert (both.ev_tp, both.ev_fp, both.ev_fn, both.severity_ok) == (2, 0, 0, 1)  # severity differs on one item, still a match
    one_lumped = score_page(page, ListingBatch(source_type="review", evidence=[e("management", "negative")]))
    assert (one_lumped.ev_tp, one_lumped.ev_fn) == (1, 1)  # the "good property" half was lost
    flipped = score_page(page, ListingBatch(source_type="review", evidence=[e("property_condition", "negative"), e("management", "negative")]))
    assert (flipped.ev_tp, flipped.ev_fp, flipped.ev_fn) == (1, 1, 1)  # wrong polarity is both a miss and an error


def test_confidence_agreement_only_for_keys_that_state_one():
    page = BY_ID["s13"]  # key expects extraction_confidence "low"
    answer = gold_answer(page)
    assert score_page(page, answer).confidence_ok == 1
    answer.listings[0].extraction_confidence = "high"
    assert score_page(page, answer).confidence_ok == 0
    assert score_page(BY_ID["s01"], gold_answer(BY_ID["s01"])).confidence_ok is None


def _load_script():
    import importlib.util
    from homefinder.config.config import ROOT
    spec = importlib.util.spec_from_file_location("eval_extraction", ROOT / "scripts" / "eval_extraction.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quick_mode_is_exactly_seven_calls_and_covers_the_hard_cases(monkeypatch, capsys):
    import sys
    mod = _load_script()
    monkeypatch.setattr(sys, "argv", ["eval_extraction.py", "--quick"])
    mod.main()
    assert "7 pages, modes ['single']: about 7 Gemini calls" in capsys.readouterr().out
    quick = [p for p in PAGES if p["id"] in mod.QUICK_PAGES]
    assert len(quick) == len(mod.QUICK_PAGES) == 7
    tags = {t for p in quick for t in p["tags"]}
    assert {"price_range", "evidence", "forum", "contradiction", "off_topic", "flat_full_terms"} <= tags
    assert any(len(p["expected"]["listings"]) > 1 for p in quick) and any(p["expected"]["evidence"] for p in quick)


def test_script_dry_run_makes_no_calls(monkeypatch, capsys):
    import importlib.util, sys
    from homefinder.config.config import ROOT
    spec = importlib.util.spec_from_file_location("eval_extraction", ROOT / "scripts" / "eval_extraction.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def boom(*a, **k):
        raise AssertionError("must not call the model without --yes")

    monkeypatch.setattr(mod, "extract_document", boom)
    monkeypatch.setattr(mod, "extract_documents", boom)
    monkeypatch.setattr(sys, "argv", ["eval_extraction.py", "--mode", "both", "--max-calls", "51"])
    mod.main()
    out = capsys.readouterr().out
    assert "Dry run" in out and "51 Gemini calls" in out  # 42 single + 9 batches of 5


def test_default_cap_refuses_a_full_run_even_with_yes(monkeypatch):
    import sys
    import pytest
    mod = _load_script()
    monkeypatch.setattr(mod, "extract_document", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called the model")))
    monkeypatch.setattr(mod, "extract_documents", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called the model")))
    monkeypatch.setattr(sys, "argv", ["eval_extraction.py", "--mode", "both", "--yes"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert "Refusing to run: 51 calls" in str(e.value)
    monkeypatch.setattr(sys, "argv", ["eval_extraction.py", "--mode", "single", "--yes"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert "42 calls" in str(e.value)


def test_batch_size_six_covers_all_pages_in_seven_calls_under_the_default_cap(monkeypatch, capsys):
    import sys
    mod = _load_script()
    seen = []

    def stub(docs):  # a perfect reader: answers every doc in the batch from the keys
        seen.append(len(docs))
        return {i: gold_answer(next(p for p in PAGES if p["title"] == t and p["url"] == u)) for i, t, u, _ in docs}

    monkeypatch.setattr(mod, "extract_documents", stub)
    monkeypatch.setattr(mod, "require_keys", lambda: [])
    monkeypatch.setattr(mod, "OUT", mod.OUT.parent / "_tmp_test_out")
    monkeypatch.setattr(sys, "argv", ["eval_extraction.py", "--mode", "batch", "--batch-size", "6"])
    mod.main()  # dry run: planned calls are within the cap of 7
    assert f"{len(PAGES)} pages, modes ['batch']: about 7 Gemini calls" in capsys.readouterr().out
    r = mod.evaluate("batch", PAGES, 6)
    assert seen == [6] * 7 and r["batch_size"] == 6 and r["recall"] == 1.0
