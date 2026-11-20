# These are the decision making code
from dataclasses import dataclass, field
from datetime import date

from homefinder.schemas import CityCosts, Listing, UserProfile
from homefinder.core import formulas
from homefinder.core.evidence import reputation_values
from homefinder.core.formulas import (
    CostInputs, cost, non_rent_extras, per_person, lifestyle_score, match_prefs, rent_vs_benchmark_pct,
    violated_musts,
)


@dataclass
class Rejection:
    """One failed hard cutoff. `gap` is the relative overshoot (0.06 = 6% over); None for a must-have clash."""
    kind: str  # budget | upfront | commute | must_have | unsuitable
    text: str
    gap: float | None = None


@dataclass
class Candidate:
    listing: Listing
    effective_monthly: float
    upfront: float
    scores: dict[str, float]
    score: float
    rejections: list[Rejection] = field(default_factory=list)
    cost_basis: str = "default assumption"  # where food/utilities came from: city data, suspect city data, or defaults
    rent_vs_benchmark_pct: float | None = None  # per-person rent vs the city benchmark; informational, not scored
    deposit_basis: str = "stated"  # stated | estimated (median of N listings) | estimated (default assumption)
    confidence: str = "medium"     # high | medium | low; shown to the user, never scored
    confidence_reasons: list[str] = field(default_factory=list)
    age_days: int | None = None    # days since the earliest sighting of this property
    staleness_penalty: float = 0.0
    reverify: bool = False

    @property
    def reasons_rejected(self) -> list[str]:
        return [r.text for r in self.rejections]

    @property
    def flags(self) -> list[str]:
        """Short caveats for the table and the explanation."""
        out = ["deposit estimated"] if self.deposit_basis != "stated" else []
        if self.reverify:
            out.append("re-verify")
        elif self.staleness_penalty:
            out.append(f"first seen {self.age_days} days ago")
        return out


@dataclass
class Decision:
    ranked: list[Candidate]
    rejected: list[Candidate]
    recommended_kind: str | None
    closest_misses: list[Candidate] = field(default_factory=list)  # filled only when fewer than top_n pass


def deposit_medians(listings: list[Listing], rb: dict) -> dict[str, tuple[float, int]]:
    """Per kind: (median deposit in months of rent, how many listings state one), where enough listings state it."""
    out = {}
    for kind in ("pg", "flat"):
        m = formulas.median_deposit_months(listings, kind, rb["defaults"]["deposit_median_min"])
        if m:
            out[kind] = m
    return out


def _deposit(l: Listing, rb: dict, medians: dict | None) -> tuple[float, str]:
    """(deposit for the whole unit, where it came from). A missing deposit is estimated and labelled."""
    if l.deposit is not None:
        return l.deposit, "stated"
    if medians and l.kind in medians:
        months, n = medians[l.kind]
        return l.rent * months, f"estimated (median of {n} listings)"
    return l.rent * rb["defaults"][f"{l.kind}_deposit_months"], "estimated (default assumption)"


def _cost_inputs(p: UserProfile, l: Listing, rb: dict, city: CityCosts | None = None,
                 medians: dict | None = None) -> CostInputs:
    d, m = rb["defaults"], rb["money"]
    meal_cost, flat_extras = d["meal_cost"], d["flat_extras"]
    if city:  # city data replaces the flat guesses; [defaults] stay as the fallback
        meal_cost, flat_extras = non_rent_extras(
            city.cost_one_person, city.rent_one_person, m["housing_linked_share"], m["food_share_of_linked"])
    flat = l.kind == "flat"
    n = p.sharers if flat else 1  # flat totals are split; PG prices are already per bed
    split = (lambda x: per_person(x, n))

    rent = l.rent
    deposit, _ = _deposit(l, rb, medians)
    brokerage = l.brokerage if l.brokerage is not None else (rent * d["flat_brokerage_months"] if flat else 0)
    extras = l.extras if l.extras is not None else (flat_extras if flat else 0)
    setup = d["flat_setup"][l.furnishing or "unfurnished"] if flat else 0

    extras = split(extras)
    if p.food == "need meals" and l.meals_included is not True:
        extras += meal_cost * (p.group_size if p.mover == "family" else 1)

    return CostInputs(
        rent=split(rent), maintenance=split(l.maintenance or 0), extras=extras,
        deposit=split(deposit), brokerage=split(brokerage), setup=split(setup),
        advance_rent=0,
        lock_in_months=l.lock_in_months if l.lock_in_months is not None else d[f"{l.kind}_lock_in"],
        horizon_months=p.horizon, opp_rate=m["opp_rate"], deduction_frac=m["delta_deposit_deduction"],
    )


def evaluate(p: UserProfile, l: Listing, rb: dict, city_costs: CityCosts | None = None,
             deposit_months: dict | None = None, today: date | None = None) -> Candidate:
    """`deposit_months` comes from `deposit_medians`; `today` turns on staleness (None = no age penalty)."""
    c = cost(_cost_inputs(p, l, rb, city_costs, deposit_months))
    sc, lf = rb["scoring"], rb["lifestyle"]
    prefs = match_prefs(p, l, lf["match"], lf["clash"], lf["unknown"])
    notice = l.notice_months if l.notice_months is not None else rb["defaults"][f"{l.kind}_notice"]
    lock_in = l.lock_in_months if l.lock_in_months is not None else rb["defaults"][f"{l.kind}_lock_in"]
    scores = {
        "affordability": formulas.s_affordability(
            c.effective_monthly / p.budget, sc["afford_full_ratio"], sc["afford_zero_ratio"]),
        "commute": sc["commute_unknown"] if l.commute_minutes is None
        else formulas.s_commute(l.commute_minutes, p.commute_tolerance, sc["commute_full_frac"]),
        "liquidity": formulas.s_liquidity(c.upfront, p.upfront_cash, rb["liquidity"]["healthy_buffer"]),
        "lifestyle": lifestyle_score(prefs),
        "reputation": formulas.s_reputation(
            reputation_values(l.evidence, rb["source_values"], rb["evidence"]),
            sc["reputation_base"], sc["reputation_divisor"]),
        "flexibility": formulas.s_flexibility(
            lock_in, notice, p.horizon, sc["notice_zero_months"], sc["flex_lock_weight"]),
    }
    w = formulas.effective_weights(
        rb["weights"], p.upfront_cash, p.budget,
        rb["liquidity"]["tight_cash_multiple"], rb["liquidity"]["tight_cash_mult"],
    )
    # Staleness: age counts from the earliest sighting across merged duplicates, so a repost does not reset it.
    seen = [o.first_seen_at for o in l.observations] or ([l.provenance.first_seen_at] if l.provenance else [])
    age = formulas.age_days(min(seen), today) if seen and today else None
    penalty, reverify = formulas.staleness(age, rb["staleness"]["bands"])
    cand = Candidate(l, c.effective_monthly, c.upfront, scores, formulas.final_score(scores, w, penalty),
                     age_days=age, staleness_penalty=penalty, reverify=reverify)

    # Confidence tag: missing fields and the extractor's own confidence. Shown to the user, never scored.
    cand.deposit_basis = _deposit(l, rb, deposit_months)[1]
    missing = l.missing_fields or formulas.missing_fields(l)
    cand.confidence = formulas.confidence_tag(l.extraction_confidence, len(missing), rb["confidence"])
    if missing:
        cand.confidence_reasons.append(f"{len(missing)} not stated: {', '.join(missing)}")
    if cand.deposit_basis != "stated":
        cand.confidence_reasons.append(f"deposit {cand.deposit_basis}")
    if l.extraction_confidence in ("medium", "low"):
        cand.confidence_reasons.append(f"extraction confidence {l.extraction_confidence}")
    if city_costs:
        cand.cost_basis = "city data" if city_costs.data_quality == "ok" else "city data (suspect)"
        if p.mover != "family":  # a whole family flat is not comparable with a one-person benchmark
            rent_pp = l.rent if l.kind == "pg" else l.rent / p.sharers
            cand.rent_vs_benchmark_pct = rent_vs_benchmark_pct(rent_pp, city_costs.rent_one_person)

    # Hard cutoffs
    if c.effective_monthly > p.budget:
        cand.rejections.append(Rejection(
            "budget", f"over budget (Rs {c.effective_monthly:,.0f}/mo vs Rs {p.budget:,.0f})",
            c.effective_monthly / p.budget - 1))
    if c.upfront > p.upfront_cash:
        cand.rejections.append(Rejection(
            "upfront", f"upfront Rs {c.upfront:,.0f} exceeds cash Rs {p.upfront_cash:,.0f}",
            c.upfront / p.upfront_cash - 1))
    if l.commute_minutes is not None and l.commute_minutes > p.commute_tolerance:
        cand.rejections.append(Rejection(
            "commute", f"commute ~{l.commute_minutes:.0f} min exceeds {p.commute_tolerance}",
            l.commute_minutes / p.commute_tolerance - 1))
    if p.mover == "family" and l.kind == "pg" and rb["decision"].get("family_excludes_pg", True):
        cand.rejections.append(Rejection("unsuitable", "a PG is not a home for a family"))
    for k in violated_musts(p, prefs, lf["clash"]):
        cand.rejections.append(Rejection("must_have", f"fails must-have: {k}"))
    return cand


def closest_misses(rejected: list[Candidate], room: int) -> list[Candidate]:
    """Rejected listings that failed exactly one hard cutoff, nearest first: smallest overshoot, then best score.
    A must-have clash has no size, so those come last. An unsuitable kind (a PG for a family) is never a near miss."""
    one = [c for c in rejected if len(c.rejections) == 1 and c.rejections[0].kind != "unsuitable"]
    one.sort(key=lambda c: (c.rejections[0].gap is None, c.rejections[0].gap or 0.0, -c.score))
    return one[:max(0, room)]


def decide(p: UserProfile, candidates: list[Candidate], rb: dict) -> Decision:
    ok = sorted((c for c in candidates if not c.rejections), key=lambda c: -c.score)
    rejected = [c for c in candidates if c.rejections]
    ranked = ok[: rb["decision"]["top_n"]]

    # PG vs flat: whichever kind has the better average of its top-N survivors.
    avg = {}
    for kind in ("pg", "flat"):
        top = [c.score for c in ok if c.listing.kind == kind][: rb["decision"]["kind_window"]]
        if top:
            avg[kind] = sum(top) / len(top)
    kind = max(avg, key=avg.get) if avg else None
    return Decision(ranked, rejected, kind, closest_misses(rejected, rb["decision"]["top_n"] - len(ranked)))
