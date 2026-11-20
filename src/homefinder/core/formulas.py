"""Deterministic formulas: costs, sub-scores and lifestyle matching.

Numbers come from rulebook.toml (passed in by rules.py); nothing here reads config.
"""
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median

from homefinder.schemas import Listing, UserProfile


# ============================== Costs ==============================

@dataclass(frozen=True)
class CostInputs:
    rent: float              # R, per person per month
    maintenance: float       # M
    extras: float            # O, out-of-pocket not in rent
    deposit: float           # D (per person)
    brokerage: float         # B, one-off
    setup: float             # S, one-off
    advance_rent: float      # only in upfront cash U
    lock_in_months: int      # L
    horizon_months: int      # t
    opp_rate: float          # r_m
    deduction_frac: float    # delta


@dataclass(frozen=True)
class CostResult:
    upfront: float
    months_paid: int
    tco: float
    effective_monthly: float


def cost(c: CostInputs) -> CostResult:
    t = c.horizon_months
    t_paid = max(t, c.lock_in_months)
    tco = (
        c.brokerage + c.setup
        + (c.rent + c.maintenance + c.extras) * t_paid
        + c.deposit * c.opp_rate * t_paid      # opportunity cost only
        + c.deduction_frac * c.deposit         # expected forfeiture
    )
    upfront = c.deposit + c.brokerage + c.advance_rent + c.setup

    return CostResult(upfront, t_paid, tco, tco / t)


def per_person(flat_total: float, sharers: int) -> float:
    """Applies to R, D, B, S for shared flats (normalization rule)."""
    return flat_total / sharers


def non_rent_extras(cost_one_person: float, rent_one_person: float,
                    housing_share: float = 0.5, food_share: float = 0.7) -> tuple[float, float]:
    """City non-rent cost -> (food, utilities + wifi) per person per month.

    The housing-linked share of non-rent cost follows the housing choice; `food_share` of it is food.
    """
    linked = (cost_one_person - rent_one_person) * housing_share
    food = linked * food_share
    return food, linked - food


def rent_vs_benchmark_pct(rent_per_person: float, benchmark: float) -> float:
    """+12.0 means 12% above the city's one-person rent benchmark. Informational, never scored."""
    return (rent_per_person / benchmark - 1) * 100


def median_deposit_months(listings: list[Listing], kind: str, min_count: int = 3) -> tuple[float, int] | None:
    """Median deposit, in months of rent, over the listings of this kind that state one: (months, how many).
    None when fewer than `min_count` state a deposit (the caller then falls back to the rulebook default)."""
    months = [l.deposit / l.rent for l in listings if l.kind == kind and l.deposit is not None]
    return (median(months), len(months)) if len(months) >= max(1, min_count) else None


# ============================== Confidence and staleness ==============================

# Fields whose absence makes a listing less certain. Brokerage is not expected on a PG.
_KEY_FIELDS = ("area", "deposit", "brokerage", "maintenance", "lock_in_months", "notice_months", "furnishing",
               "private_room", "meals_included", "vibe", "gender_restricted")


def missing_fields(l: Listing) -> list[str]:
    return [k for k in _KEY_FIELDS if getattr(l, k) is None and not (k == "brokerage" and l.kind == "pg")]


def confidence_tag(extraction_confidence: str | None, n_missing: int, cfg: dict) -> str:
    """'high' / 'medium' / 'low' from a count of weak points: one per missing key field, plus the
    [confidence.extraction_weak] points for the extractor's own confidence. Never affects the score."""
    weak = n_missing + cfg["extraction_weak"][extraction_confidence or "unknown"]
    return "high" if weak <= cfg["high_max_weak"] else "medium" if weak <= cfg["medium_max_weak"] else "low"


def age_days(first_seen_at: str, today: date) -> int:
    """Whole days since we first saw a listing (never negative)."""
    return max(0, (today - datetime.fromisoformat(first_seen_at).date()).days)


def staleness(age: int | None, bands: list[dict]) -> tuple[float, bool]:
    """(penalty on the 0-10 final score, re-verify flag) for the oldest band the age has reached."""
    hit = max((b for b in bands if age is not None and age >= b["min_days"]), key=lambda b: b["min_days"], default=None)
    return (hit["penalty"], bool(hit.get("reverify"))) if hit else (0.0, False)


# ============================== Scores ==============================

# Order follows spec section 11 wording.
# These are the formulas to calculate the scores from taking the number from the rulebook.toml
KEYS = ("affordability", "liquidity", "commute", "lifestyle", "reputation", "flexibility")


def _clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


def s_affordability(r: float, full_ratio: float = 0.6, zero_ratio: float = 1.0) -> float:
    """r = effective monthly cost / budget. r <= full_ratio -> 10, r >= zero_ratio -> 0."""
    return _clamp(10 * (zero_ratio - r) / (zero_ratio - full_ratio))


def s_commute(minutes: float, tolerance: float, full_frac: float = 0.25) -> float:
    """m <= full_frac*T -> 10, m >= T -> 0."""
    return _clamp(10 * (tolerance - minutes) / ((1 - full_frac) * tolerance))


def s_liquidity(U: float, cash: float, healthy_buffer: float = 0.5) -> float:
    """h = 1 - U/cash. Keeping >= healthy_buffer of cash -> 10."""
    h = 1 - U / cash
    return _clamp(10 * h / healthy_buffer)


def s_flexibility(
    lock_in: float,
    notice: float,
    horizon: float,
    notice_zero_months: float = 3,
    lock_weight: float = 0.6,
) -> float:
    lock = _clamp(1 - lock_in / horizon, 0, 1)
    notice_part = _clamp(1 - notice / notice_zero_months, 0, 1)
    return lock_weight * 10 * lock + (1 - lock_weight) * 10 * notice_part


def s_reputation(
    source_values: list[float] | None = None, base: float = 5.0, divisor: float = 2.0
) -> float:
    """Sum of distinct-source values; no evidence -> base."""
    return _clamp(base + sum(source_values or []) / divisor)


def effective_weights(
    weights: dict,
    cash: float,
    budget: float,
    tight_multiple: float = 3,
    tight_mult: float = 1.5,
) -> dict:
    """Boost liquidity weight when cash is tight, then renormalize to 100."""
    w = dict(weights)
    if cash < tight_multiple * budget:
        w["liquidity"] *= tight_mult
    total = sum(w.values())
    return {k: v * 100 / total for k, v in w.items()}


def final_score(scores: dict, weights: dict, staleness_penalty: float = 0.0) -> float:
    """Sum(weight * score) / 100, minus staleness penalty, clamped to 0-10."""
    raw = sum(weights[k] * scores[k] for k in KEYS) / 100
    return _clamp(raw - staleness_penalty)


# ============================== Lifestyle ==============================

_RANK = {"unfurnished": 0, "semi": 1, "furnished": 2}

# Defaults mirror [lifestyle] in rulebook.toml; evaluate() passes the rulebook values.
MATCH, CLASH, UNKNOWN = 1.0, 0.0, 0.5


def _flag(value: bool | None, match: float = MATCH, clash: float = CLASH, unknown: float = UNKNOWN) -> float:
    return unknown if value is None else (match if value else clash)


def match_prefs(
    p: UserProfile, l: Listing, match: float = MATCH, clash: float = CLASH, unknown: float = UNKNOWN
) -> dict[str, float | None]:
    def flag(v):
        return _flag(v, match, clash, unknown)

    if p.food == "need meals":
        food = flag(l.meals_included)
    elif p.food == "cook myself":
        food = flag(l.kitchen_access if l.kitchen_access is not None else (True if l.kind == "flat" else None))
    else:
        food = None

    if p.privacy == "private room":
        privacy = flag(l.private_room if l.private_room is not None else (True if l.kind == "flat" and p.sharers == 1 else None))
    else:
        privacy = None

    if l.furnishing is None:
        furnishing = unknown
    else:
        furnishing = match if _RANK[l.furnishing] >= _RANK[p.furnishing] else clash

    if p.noise == "no preference":
        noise = None
    elif l.vibe is None:
        noise = unknown
    else:
        noise = match if l.vibe == p.noise else clash

    gender = flag(l.gender_restricted) if p.gender_restricted else None

    return {"food": food, "privacy": privacy, "furnishing": furnishing, "noise": noise, "gender": gender}


def lifestyle_score(prefs: dict[str, float | None]) -> float:
    vals = [v for v in prefs.values() if v is not None]
    return 10 * sum(vals) / len(vals) if vals else 10.0


def violated_musts(p: UserProfile, prefs: dict[str, float | None], clash: float = CLASH) -> list[str]:
    return [k for k, v in prefs.items() if v == clash and p.must_have.get(k)]
