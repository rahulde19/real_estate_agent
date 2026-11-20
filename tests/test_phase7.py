"""Phase 7: provenance and confidence, deduplication, staleness, closest misses, evidence items. Offline, no LLM."""
from datetime import date

import pytest

from conftest import pg, plain_rulebook
from homefinder.agent.graph import build_graph
from homefinder.agent.prompts import extract_prompt_version
from homefinder.core import formulas
from homefinder.core.dedup import dedupe, same_property
from homefinder.core.evidence import attach_evidence, caveats_by_criterion, reputation_values
from homefinder.core.rules import closest_misses, decide, deposit_medians, evaluate
from homefinder.schemas import Aspect, EvidenceItem, Listing, ListingBatch, Provenance
from homefinder.services import llm
from homefinder.services.documents import DocumentRepo

TODAY = date(2026, 11, 16)


def prov(url="https://a/1", kind="ad", seen="2026-11-16T00:00:00+00:00"):
    return Provenance(source_url=url, source_type=kind, agent_source="tavily:sample", first_seen_at=seen, retrieved_at=seen)


def seen_days_ago(n, **kw):
    """A PG first seen n days before TODAY."""
    p = prov(seen=f"{date.fromordinal(TODAY.toordinal() - n).isoformat()}T09:00:00+00:00")
    return pg(private_room=True, provenance=p, observations=[p], **kw)


def ev(aspect, polarity, kind="review", url="https://r/1", subject="Sai PG", severity="high"):
    return EvidenceItem(subject=subject, aspect=aspect, polarity=polarity, severity=severity, source_url=url, source_type=kind)


# ---------------- 7.1 provenance and confidence ----------------

def test_code_set_fields_are_hidden_from_the_llm_schema_and_the_prompt_hash():
    schema = str(ListingBatch.model_json_schema())
    for hidden in ("provenance", "observations", "missing_fields", "property_id", "source_url"):
        assert hidden not in schema
    for shown in ("extraction_confidence", "source_type", "evidence", "phone", "building_name"):
        assert shown in schema
    assert len(extract_prompt_version()) == 10


def test_graph_attaches_provenance_and_missing_fields(profile):
    g = build_graph(
        search=lambda loc, city: [{"title": "t", "url": "https://a/1", "content": "c"}],
        extract=lambda title, url, text: ListingBatch(source_type="builder", listings=[pg(name="A", private_room=True, deposit=9000)]),
        explain=lambda p, d: "ok", repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="m",
        rulebook=plain_rulebook(), city_lookup=lambda c: None, agent_source="tavily:sample")
    l = g.invoke({"profile": profile})["listings"][0]
    assert (l.provenance.source_url, l.provenance.source_type, l.provenance.agent_source) == ("https://a/1", "builder", "tavily:sample")
    assert l.provenance.first_seen_at and l.provenance.retrieved_at and l.observations == [l.provenance]
    assert "deposit" not in l.missing_fields and "private_room" not in l.missing_fields
    assert {"area", "furnishing", "lock_in_months"} <= set(l.missing_fields)
    assert "brokerage" not in l.missing_fields  # not expected on a PG
    assert l.property_id


def test_confidence_tag_levels(rb):
    cfg = rb["confidence"]
    assert formulas.confidence_tag("high", 0, cfg) == "high"
    assert formulas.confidence_tag("high", cfg["high_max_weak"], cfg) == "high"
    assert formulas.confidence_tag("high", cfg["high_max_weak"] + 1, cfg) == "medium"
    assert formulas.confidence_tag("low", cfg["high_max_weak"], cfg) == "medium"  # the extractor's doubt counts too
    assert formulas.confidence_tag(None, cfg["medium_max_weak"], cfg) == "low"    # unknown confidence is a weak point
    assert formulas.confidence_tag("high", cfg["medium_max_weak"] + 1, cfg) == "low"


def test_confidence_never_changes_the_score(profile, rb):
    full = dict(private_room=True, deposit=18000, area="Taramani", furnishing="furnished", meals_included=True,
                lock_in_months=1, notice_months=1, vibe="quiet")
    a = evaluate(profile, pg(**full, extraction_confidence="high"), rb)
    b = evaluate(profile, pg(**full, extraction_confidence="low"), rb)
    assert a.score == b.score and a.confidence != b.confidence
    assert any("extraction confidence low" in r for r in b.confidence_reasons)


def test_missing_deposit_uses_median_of_observed_listings_and_is_labelled(profile, rb):
    stated = [pg(name=f"S{i}", rent=10000, deposit=d) for i, d in enumerate((10000, 10000, 30000))]  # 1, 1, 3 months
    medians = deposit_medians(stated, rb)
    assert medians == {"pg": (1.0, 3)}
    c = evaluate(profile, pg(rent=8000, private_room=True), rb, deposit_months=medians)
    assert c.upfront == pytest.approx(8000) and c.deposit_basis == "estimated (median of 3 listings)"
    assert "deposit estimated" in c.flags and any("deposit estimated" in r for r in c.confidence_reasons)


def test_too_few_stated_deposits_falls_back_to_the_rulebook_default(profile, rb):
    assert deposit_medians([pg(deposit=9000), pg(deposit=9000)], rb) == {}  # below deposit_median_min
    c = evaluate(profile, pg(rent=8000, private_room=True), rb, deposit_months={})
    assert c.upfront == pytest.approx(8000 * rb["defaults"]["pg_deposit_months"])
    assert c.deposit_basis == "estimated (default assumption)"
    stated = evaluate(profile, pg(rent=8000, private_room=True, deposit=5000), rb)
    assert stated.deposit_basis == "stated" and "deposit estimated" not in stated.flags


def test_explanation_facts_carry_confidence_flags_and_unknowns(profile, rb, monkeypatch):
    seen = {}

    class Fake:
        def invoke(self, msgs):
            seen["human"] = msgs[1][1]
            return type("R", (), {"text": "ok", "usage_metadata": None})()

    monkeypatch.setattr(llm, "_llm", lambda *a, **k: Fake())
    monkeypatch.setattr(llm.METER, "add", lambda kind, reply: None)
    l = pg(private_room=True)
    l.missing_fields = formulas.missing_fields(l)
    llm.write_suggestion(profile, decide(profile, [evaluate(profile, l, rb)], rb))
    assert '"confidence"' in seen["human"] and "deposit estimated" in seen["human"] and '"furnishing"' in seen["human"]


# ---------------- 7.2 deduplication ----------------

def ad(i, **kw):
    base = dict(name="Sai Residency PG", area="Taramani", rent=9000, phone="+91 98400 12345", private_room=True)
    return pg(**{**base, **kw}, provenance=prov(f"https://broker{i}.com/ad"), observations=[prov(f"https://broker{i}.com/ad")])


def test_five_broker_ads_for_one_property_count_once():
    ads = [ad(0), ad(1, name="SAI RESIDENCY  P.G", phone=None), ad(2, rent=9300, name="Best PG near Tidel"),  # phone match
           ad(3, rent=8700, phone="9840012345"), ad(4, area="taramani ")]
    out = dedupe(ads + [pg(name="Other PG", area="Taramani", rent=9000)])
    assert len(out) == 2
    assert len(out[0].observations) == 5 and out[0].property_id and out[0].property_id != out[1].property_id


def test_rent_must_be_within_five_percent():
    a, edge, far = ad(0, rent=10000), ad(1, rent=9500), ad(2, rent=9400)
    assert same_property(a, edge) and not same_property(a, far)
    assert len(dedupe([a, far])) == 2


def test_blocks_keep_different_localities_types_and_sizes_apart():
    a = ad(0)
    assert not same_property(a, ad(1, area="Velachery"))
    assert not same_property(a, Listing(name="Sai Residency PG", kind="flat", area="Taramani", rent=9000, phone="+91 98400 12345"))
    assert not same_property(ad(0, sharing="single"), ad(1, sharing="double"))
    assert same_property(ad(0, sharing="single"), ad(1))  # unknown sharing fits any
    assert not same_property(ad(0, bhk=2), ad(1, bhk=3))


def test_needs_an_identity_signal_and_geo_point_is_one():
    a, b = ad(0, name="Green PG", phone=None), ad(1, name="Blue PG", phone=None)
    assert not same_property(a, b)  # same block and rent, but nothing says it is the same place
    a.lat, a.lng, b.lat, b.lng = 12.9850, 80.2400, 12.9853, 80.2402  # about 40 m apart
    assert same_property(a, b)
    b.lat = 12.9950  # about 1.1 km away
    assert not same_property(a, b)


def test_merge_prefers_the_more_reliable_source_and_fills_blanks(rb):
    builder = ad(0, deposit=20000, furnishing=None)
    builder.provenance = prov("https://sai.example/pg", "builder", "2026-11-10T00:00:00+00:00"); builder.observations = [builder.provenance]
    advert = ad(1, deposit=18000, furnishing="furnished")
    out = dedupe([builder, advert], rb["dedup"], rb["source_values"])[0]
    assert out.deposit == 18000 and out.provenance.source_type == "ad"   # ad (+7) outranks builder (+5)
    assert {o.source_url for o in out.observations} == {"https://sai.example/pg", "https://broker1.com/ad"}
    lone = dedupe([ad(0, furnishing=None), ad(1, furnishing="semi")], rb["dedup"], rb["source_values"])[0]
    assert lone.furnishing == "semi" and "furnishing" not in lone.missing_fields  # blank filled from the other ad


def test_property_id_is_stable_and_unique():
    first, again = dedupe([ad(0), ad(1)]), dedupe([ad(1), ad(0)])
    assert first[0].property_id == again[0].property_id
    two = dedupe([ad(0, rent=9000), ad(1, rent=14000, phone=None)])  # same name, clearly different rent: two properties
    assert len(two) == 2 and two[0].property_id != two[1].property_id


# ---------------- 7.3 staleness ----------------

@pytest.mark.parametrize("age,penalty,reverify", [
    (0, 0.0, False), (14, 0.0, False), (15, 0.3, False), (29, 0.3, False), (30, 0.5, False), (44, 0.5, False),
    (45, 0.7, True), (200, 0.7, True)])
def test_each_age_band(rb, profile, age, penalty, reverify):
    assert formulas.staleness(age, rb["staleness"]["bands"]) == (penalty, reverify)
    fresh = evaluate(profile, seen_days_ago(0), rb, today=TODAY)
    old = evaluate(profile, seen_days_ago(age), rb, today=TODAY)
    assert (old.age_days, old.staleness_penalty, old.reverify) == (age, penalty, reverify)
    assert old.score == pytest.approx(fresh.score - penalty)
    assert ("re-verify" in old.flags) == reverify


def test_age_counts_from_the_earliest_sighting_across_duplicates(profile, rb):
    old, repost = seen_days_ago(50, name="Sai PG", area="Taramani"), seen_days_ago(1, name="Sai PG", area="Taramani")
    old.provenance.source_url = old.observations[0].source_url = "https://a/old"
    merged = dedupe([repost, old])
    assert len(merged) == 1
    c = evaluate(profile, merged[0], rb, today=TODAY)
    assert c.age_days == 50 and c.reverify  # the repost does not reset the clock


def test_no_date_means_no_penalty(profile, rb):
    c = evaluate(profile, pg(private_room=True), rb, today=TODAY)  # no provenance
    assert (c.age_days, c.staleness_penalty) == (None, 0.0)
    assert evaluate(profile, seen_days_ago(60), rb).staleness_penalty == 0.0  # no `today` given
    assert formulas.age_days("2026-11-20T00:00:00+00:00", TODAY) == 0  # never negative


def test_graph_uses_the_injected_date(profile):
    repo = DocumentRepo(":memory:")
    repo.save_result({"title": "t", "url": "u", "content": "c"}, "tavily:sample", now="2026-09-01T00:00:00+00:00")
    g = build_graph(search=lambda loc, city: [{"title": "t", "url": "u", "content": "c"}],
                    extract=lambda title, url, text: [pg(name="A", private_room=True)], explain=lambda p, d: "ok",
                    repo=repo, prompt_version="v1", model_id="m", rulebook=plain_rulebook(),
                    city_lookup=lambda c: None, today=lambda: TODAY)
    c = g.invoke({"profile": profile})["decision"].ranked[0]
    assert c.age_days == 76 and c.reverify and c.staleness_penalty == 0.7


# ---------------- 7.4 closest misses ----------------

def test_closest_misses_fill_up_to_top_n_and_name_the_failed_constraint(profile, rb):
    ok = pg(name="OK", rent=7000, private_room=True, meals_included=True)
    near = pg(name="Near", rent=11500, private_room=True, deposit=5000)   # a little over budget
    far = pg(name="Far", rent=14000, private_room=True, deposit=5000)    # further over budget
    clash = pg(name="Clash", rent=7000, private_room=False, meals_included=True)  # must-have: no size
    both = pg(name="Both", rent=40000, private_room=True)                # over budget and over cash: not a near miss
    d = decide(profile, [evaluate(profile, l, rb) for l in (both, clash, far, ok, near)], rb)
    assert [c.listing.name for c in d.ranked] == ["OK"]
    assert [c.listing.name for c in d.closest_misses] == ["Near", "Far", "Clash"]
    r = d.closest_misses[0].rejections[0]
    assert r.kind == "budget" and "over budget" in r.text and 0 < r.gap < d.closest_misses[1].rejections[0].gap
    assert d.closest_misses[2].rejections[0].kind == "must_have" and d.closest_misses[2].rejections[0].gap is None
    assert len(d.rejected) == 4  # the full rejected list is unchanged


def test_misses_are_capped_and_absent_when_enough_pass(profile, rb):
    good = [evaluate(profile, pg(name=f"G{i}", rent=7000 + i, private_room=True, meals_included=True), rb) for i in range(7)]
    bad = [evaluate(profile, pg(name=f"B{i}", rent=12000 + i * 100, private_room=True, deposit=5000), rb) for i in range(5)]
    assert all(len(c.rejections) == 1 for c in bad)
    assert decide(profile, good + bad, rb).closest_misses == []
    d = decide(profile, good[:5] + bad, rb)
    assert len(d.ranked) == 5 and [c.listing.name for c in d.closest_misses] == ["B0", "B1"]
    assert closest_misses(bad, 0) == [] and closest_misses(bad, -3) == []


# ---------------- 7.5 evidence items ----------------

def test_every_aspect_maps_to_one_of_the_six_criteria(rb):
    mapping = rb["evidence"]["aspect_criteria"]
    assert set(mapping) == set(Aspect.__args__) and set(mapping.values()) <= set(formulas.KEYS)


def test_good_property_bad_caretakers_is_two_items(profile, rb):
    """A review page says the rooms are good but the caretakers are rude: two items, grouped under their criteria."""
    answer = ListingBatch.model_validate({"source_type": "review", "evidence": [
        {"subject": "Sai PG", "aspect": "property_condition", "polarity": "positive", "severity": "medium"},
        {"subject": "Sai PG", "aspect": "management", "polarity": "negative", "severity": "high"}]})
    g = build_graph(
        search=lambda loc, city: [{"title": "ad", "url": "https://a/1", "content": "c"}, {"title": "rev", "url": "https://r/1", "content": "d"}],
        extract=lambda title, url, text: answer if title == "rev" else ListingBatch(source_type="ad", listings=[pg(name="Sai PG", private_room=True)]),
        explain=lambda p, d: "ok", repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="m",
        rulebook=plain_rulebook(), city_lookup=lambda c: None)
    out = g.invoke({"profile": profile})
    items = out["listings"][0].evidence
    assert [(e.aspect, e.polarity, e.source_type, e.source_url) for e in items] == [
        ("property_condition", "positive", "review", "https://r/1"), ("management", "negative", "review", "https://r/1")]
    assert len(out["evidence"]) == 2
    groups = caveats_by_criterion(items, rb["evidence"]["aspect_criteria"])
    assert list(groups) == ["reputation"] and len(groups["reputation"]) == 2
    # one source, so one value: the mean of +7 x 0.75 and -7 x 1.0
    assert reputation_values(items, rb["source_values"], rb["evidence"]) == [pytest.approx((5.25 - 7) / 2)]
    assert out["candidates"][0].scores["reputation"] == pytest.approx(5 + (5.25 - 7) / 2 / 2)


def test_ads_and_builder_pages_never_move_reputation(profile, rb):
    l = pg(name="Sai PG", private_room=True)
    base = evaluate(profile, l, rb).scores["reputation"]
    attach_evidence([l], [ev("property_condition", "positive", kind="ad"), ev("safety", "positive", kind="builder", url="https://b/1"),
                          ev("food", "positive", kind=None, url="https://x/1")])
    assert len(l.evidence) == 3 and evaluate(profile, l, rb).scores["reputation"] == base == rb["scoring"]["reputation_base"]


def test_one_value_per_distinct_independent_source(profile, rb):
    items = [ev("management", "negative", "forum", "https://reddit.com/1"), ev("safety", "negative", "forum", "https://reddit.com/1"),
             ev("food", "positive", "review", "https://r/2", severity="low")]
    assert sorted(reputation_values(items, rb["source_values"], rb["evidence"])) == [-7.0, 3.5]
    l = attach_evidence([pg(name="Sai PG", private_room=True)], items)[0]
    assert evaluate(profile, l, rb).scores["reputation"] == pytest.approx(5 + (-7 + 3.5) / 2)
    worst = attach_evidence([pg(name="Sai PG")], [ev("safety", "negative", "forum", f"https://f/{i}") for i in range(3)])[0]
    assert evaluate(profile, worst, rb).scores["reputation"] == 0  # clamped


def test_evidence_is_matched_by_property_name_only():
    sai, other, generic = pg(name="Sai PG"), pg(name="Green Homes"), pg(name="PG")
    attach_evidence([sai, other, generic], [ev("noise", "negative", subject="sai pg"), ev("noise", "negative", subject="Sai PG"),
                                           ev("food", "positive", subject="Green Homes Velachery", url="https://r/2"),
                                           ev("food", "positive", subject="Some PG", url="https://r/3")])
    assert len(sai.evidence) == 1   # the same source and aspect is not counted twice
    assert len(other.evidence) == 1  # a longer subject that contains the full name
    assert generic.evidence == []   # a short generic name must match exactly


def test_evidence_domains_keep_review_pages_through_the_prefilter():
    from homefinder.core.prefilter import prefilter_reason
    text = "The caretakers at Sai PG were rude and the water kept running out. " * 3
    assert prefilter_reason("https://www.reddit.com/r/chennai/1", "t", text, 80, []) == "no_amount"
    assert prefilter_reason("https://www.reddit.com/r/chennai/1", "t", text, 80, [], ["reddit.com"]) is None
    assert prefilter_reason("https://www.reddit.com/r/chennai/1", "t", "short", 80, [], ["reddit.com"]) == "too_short"
