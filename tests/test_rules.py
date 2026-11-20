from conftest import pg
from homefinder.core.rules import decide, evaluate
from homefinder.schemas import Listing


def test_cheap_pg_passes_and_scores(profile, rb):
    c = evaluate(profile, pg(rent=9000, private_room=True, meals_included=True, deposit=18000,
                             furnishing="furnished", vibe="quiet", commute_minutes=15), rb)
    assert not c.reasons_rejected
    assert c.upfront == 18000
    assert 0 < c.score <= 10


def test_over_budget_rejected(profile, rb):
    c = evaluate(profile, pg(rent=20000, private_room=True), rb)
    assert any("over budget" in r for r in c.reasons_rejected)


def test_upfront_over_cash_rejected(profile, rb):
    c = evaluate(profile, pg(rent=9000, deposit=90000, private_room=True), rb)
    assert any("upfront" in r for r in c.reasons_rejected)


def test_must_have_clash_rejected_but_unknown_passes(profile, rb):
    assert evaluate(profile, pg(private_room=False), rb).reasons_rejected
    assert not evaluate(profile, pg(private_room=None), rb).reasons_rejected


def test_meal_cost_added_when_not_included(profile, rb):
    with_meals = evaluate(profile, pg(meals_included=True, private_room=True), rb)
    without = evaluate(profile, pg(meals_included=False, private_room=True), rb)
    assert without.effective_monthly - with_meals.effective_monthly == rb["defaults"]["meal_cost"]


def test_flat_costs_split_among_friends(profile, rb):
    p = profile.model_copy(update={"mover": "friends", "group_size": 3, "must_have": {}, "food": "eat out"})
    flat = Listing(name="F", kind="flat", rent=30000, deposit=90000, brokerage=0, extras=0, furnishing="furnished")
    c = evaluate(p, flat, rb)
    assert round(c.upfront) == 30000  # 90000 / 3
    assert 10000 <= c.effective_monthly < 11000


def test_decide_top_n_and_kind(profile, rb):
    cands = [evaluate(profile, pg(name=f"P{i}", rent=5000 + 300 * i, private_room=True), rb) for i in range(10)]
    cands.append(evaluate(profile, pg(name="Bad", rent=99999), rb))
    d = decide(profile, cands, rb)
    assert len(d.ranked) == 7 and len(d.rejected) == 1
    assert [c.score for c in d.ranked] == sorted((c.score for c in d.ranked), reverse=True)
    assert d.recommended_kind == "pg"


# --- Phase 2.3: rulebook values drive evaluate/decide ---
def _rb_with(rb, **sections):
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in rb.items()}
    for sec, vals in sections.items():
        out[sec] = {**out[sec], **vals}
    return out


def test_commute_unknown_value_comes_from_rulebook(profile, rb):
    base = evaluate(profile, pg(private_room=True), rb)
    assert base.scores["commute"] == 5.0
    changed = evaluate(profile, pg(private_room=True), _rb_with(rb, scoring={"commute_unknown": 7.0}))
    assert changed.scores["commute"] == 7.0


def test_lifestyle_unknown_value_comes_from_rulebook(profile, rb):
    p = profile.model_copy(update={"must_have": {}})
    a = evaluate(p, pg(private_room=None), rb).scores["lifestyle"]
    b = evaluate(p, pg(private_room=None), _rb_with(rb, lifestyle={"unknown": 0.0})).scores["lifestyle"]
    assert b < a


def test_kind_window_changes_pg_vs_flat_pick(profile, rb):
    p = profile.model_copy(update={"mover": "solo", "must_have": {}, "food": "eat out"})
    # Flat: one very good listing, then weak ones. PG: consistently decent.
    flats = [Listing(name=f"F{i}", kind="flat", rent=r, deposit=0, brokerage=0, extras=0,
                     furnishing="furnished") for i, r in enumerate([3000, 11000, 11500])]
    pgs = [pg(name=f"P{i}", rent=10500, deposit=0, furnishing="furnished") for i in range(3)]
    cands = [evaluate(p, l, rb) for l in flats + pgs]
    assert decide(p, cands, _rb_with(rb, decision={"kind_window": 1})).recommended_kind == "flat"
    assert decide(p, cands, _rb_with(rb, decision={"kind_window": 3})).recommended_kind == "pg"
