"""Spec section 11: the same sub-scores rank differently under different weights."""
import pytest

from homefinder.core.formulas import final_score

# Order in the spec: affordability, liquidity, commute, lifestyle, reputation, flexibility
A = dict(affordability=9, liquidity=6, commute=4, lifestyle=7, reputation=5, flexibility=8)
B = dict(affordability=6, liquidity=9, commute=8, lifestyle=7, reputation=7, flexibility=5)


def test_default_weights_favour_b():
    w = dict(affordability=30, liquidity=15, commute=20, lifestyle=15, reputation=10, flexibility=10)
    assert final_score(A, w) == pytest.approx(6.75)
    assert final_score(B, w) == pytest.approx(7.00)
    assert final_score(B, w) > final_score(A, w)


def test_affordability_heavy_weights_flip_to_a():
    w = dict(affordability=50, liquidity=10, commute=10, lifestyle=15, reputation=10, flexibility=5)
    assert final_score(A, w) == pytest.approx(7.45)
    assert final_score(B, w) == pytest.approx(6.70)
    assert final_score(A, w) > final_score(B, w)


# --- Phase 2.3: thresholds come from the rulebook ---
import pytest as _pytest

from homefinder.config.config import load_rulebook
from homefinder.core.formulas import s_affordability, s_commute, s_flexibility, s_reputation


def test_default_thresholds_reproduce_old_hard_coded_values():
    assert s_affordability(0.6) == 10 and s_affordability(1.0) == 0
    assert s_affordability(0.8) == pytest.approx(5.0)
    assert s_commute(10, 40) == pytest.approx(10) and s_commute(40, 40) == 0
    assert s_flexibility(0, 1, 12) == pytest.approx(0.6 * 10 + 0.4 * 10 * (1 - 1 / 3))
    assert s_reputation() == 5 and s_reputation([1, 1]) == 6


def test_changed_thresholds_change_scores():
    assert s_affordability(0.6, full_ratio=0.5) < s_affordability(0.6)
    assert s_commute(20, 40, full_frac=0.5) == pytest.approx(10 * 20 / 20)
    assert s_flexibility(0, 1, 12, notice_zero_months=2) < s_flexibility(0, 1, 12)
    assert s_flexibility(6, 0, 12, lock_weight=0.2) > s_flexibility(6, 0, 12)
    assert s_reputation([1], base=4, divisor=1) == 5


def test_rulebook_has_scoring_sections_with_old_values():
    rb = load_rulebook()
    assert rb["scoring"]["afford_full_ratio"] == 0.6 and rb["scoring"]["notice_zero_months"] == 3
    assert rb["lifestyle"] == {"match": 1.0, "clash": 0.0, "unknown": 0.5}
    assert rb["decision"]["kind_window"] == 3


def test_weights_not_summing_to_100_raise(tmp_path):
    bad = tmp_path / "rb.toml"
    bad.write_text("[weights]\naffordability = 50\ncommute = 20\n")
    with _pytest.raises(ValueError, match="sum to 100"):
        load_rulebook(bad)
