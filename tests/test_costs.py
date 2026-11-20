"""Spec section 11: cost-engine unit tests. Pure arithmetic, no LLM or network."""
from datetime import date

import pytest

from homefinder.config.config import load_rulebook
from homefinder.core.formulas import CostInputs, cost, per_person
from homefinder.core.rules import evaluate
from homefinder.schemas import Listing, UserProfile

R_M = 0.0075  # monthly opportunity-cost rate (9% a year)


def inputs(**kw) -> CostInputs:
    base = dict(rent=0, maintenance=0, extras=0, deposit=0, brokerage=0, setup=0, advance_rent=0,
                lock_in_months=0, horizon_months=12, opp_rate=R_M, deduction_frac=0.0)
    return CostInputs(**{**base, **kw})


def test_deposit_is_not_double_counted():
    """Refundable Rs 60k deposit over 12 months costs ~Rs 5.4k (opportunity cost), not Rs 65k."""
    r = cost(inputs(deposit=60000))
    assert r.tco == pytest.approx(60000 * R_M * 12)  # 5,400
    assert r.tco < 6000
    assert r.upfront == 60000  # but the full deposit still counts as cash needed on day one


def test_short_stay_one_offs_dominate_and_pg_beats_flat():
    """t = 2: brokerage + setup dwarf rent, so a flat loses to a PG; at t = 24 rent dominates."""
    def flat(t):
        return inputs(rent=20000, deposit=120000, brokerage=20000, setup=40000, lock_in_months=1, horizon_months=t)

    def pg(t):
        return inputs(rent=9000, deposit=18000, lock_in_months=1, horizon_months=t)

    f2 = flat(2)
    assert f2.brokerage + f2.setup > f2.rent * 2          # one-offs (60k) beat two months' rent (40k)
    assert cost(flat(2)).effective_monthly == pytest.approx((60000 + 20000 * 2 + 120000 * R_M * 2) / 2)
    assert cost(pg(2)).effective_monthly < cost(flat(2)).effective_monthly

    f24 = flat(24)
    assert f24.brokerage + f24.setup < f24.rent * 24      # over two years rent dominates


def test_partial_forfeiture_flows_into_tco():
    """delta * D is added to the total cost."""
    base = cost(inputs(deposit=60000, deduction_frac=0.0))
    with_delta = cost(inputs(deposit=60000, deduction_frac=0.1))
    assert with_delta.tco - base.tco == pytest.approx(6000)
    assert with_delta.effective_monthly - base.effective_monthly == pytest.approx(6000 / 12)


def test_shared_flat_divides_r_d_b_s():
    """3BHK with 3 flatmates: rent, deposit, brokerage and setup are each split three ways."""
    assert per_person(30000, 3) == 10000

    p = UserProfile(
        city="Chennai", work_location="Taramani", budget=50000, upfront_cash=200000, mover="friends", group_size=3,
        commute_tolerance=40, move_in_date=date(2026, 12, 1), food="eat out", privacy="sharing is fine",
        furnishing="unfurnished", noise="no preference", gender_restricted=False,
    )
    flat = Listing(name="3BHK", kind="flat", rent=30000, deposit=90000, brokerage=30000, extras=0,
                   maintenance=0, lock_in_months=12, furnishing="unfurnished")
    rb = {
        **load_rulebook(),  # sections this test does not pin (confidence, staleness, evidence, ...)
        "money": {"opp_rate": R_M, "delta_deposit_deduction": 0.0},
        "defaults": {"flat_setup": {"furnished": 0, "semi": 15000, "unfurnished": 45000},
                     "flat_deposit_months": 6, "deposit_median_min": 3, "flat_brokerage_months": 1, "flat_extras": 0,
                     "flat_lock_in": 6, "flat_notice": 2, "meal_cost": 0},
        "liquidity": {"healthy_buffer": 0.5, "tight_cash_multiple": 3, "tight_cash_mult": 1.5},
        "weights": {"affordability": 30, "commute": 20, "liquidity": 15, "lifestyle": 15, "reputation": 10, "flexibility": 10},
        "scoring": {"afford_full_ratio": 0.6, "afford_zero_ratio": 1.0, "commute_full_frac": 0.25,
                    "commute_unknown": 5.0, "notice_zero_months": 3, "flex_lock_weight": 0.6,
                    "reputation_base": 5.0, "reputation_divisor": 2.0},
        "lifestyle": {"match": 1.0, "clash": 0.0, "unknown": 0.5},
    }
    c = evaluate(p, flat, rb)
    # per person: D 30,000 + B 10,000 + S 15,000 (45,000 / 3)
    assert c.upfront == pytest.approx(55000)
    # TCO = B 10,000 + S 15,000 + R 10,000 * 12 + D 30,000 * r_m * 12 = 147,700 -> /12
    assert c.effective_monthly == pytest.approx((10000 + 15000 + 10000 * 12 + 30000 * R_M * 12) / 12)


def test_lock_in_longer_than_stay_raises_cost():
    """t = 2 with a 6-month lock-in: you still pay 6 months (t_paid = lock_in)."""
    short = cost(inputs(rent=9000, lock_in_months=1, horizon_months=2))
    locked = cost(inputs(rent=9000, lock_in_months=6, horizon_months=2))
    assert short.months_paid == 2
    assert locked.months_paid == 6
    assert locked.tco == pytest.approx(9000 * 6)
    assert locked.effective_monthly > short.effective_monthly
    assert locked.effective_monthly == pytest.approx(9000 * 6 / 2)  # spread over the 2-month stay
