# Response 1 — calculation and reasoning

> **Historical record (written 08-11-2026 for an early run, not kept up to date).** The code has changed since: the cost formulas now live in `core/formulas.py` (`core/costs.py` is gone), extraction runs per page or in batches (`agent/extraction.py`, `services/llm.py::extract_document`), `commute_minutes` is no longer asked of the model, and the prompt text below is out of date. Read `docs/architecture.md` and `rulebook.md` for the current behaviour.

Profile (from the response): solo mover, budget Rs 15,000/mo (hard cap), upfront cash Rs 60,000 (hard cap), commute tolerance 40 min. Horizon is not stated; the numbers only reconcile at 12 months (the default when "not sure"). Food and lifestyle answers are not visible in the response.

Why only 2 options: the search gave 6 listings, 4 broke a hard cutoff, 2 survived (`evaluate()` in `core/rules.py`). `top_n = 7` is only a cap, not a target.

## 1. Cost formulas (`core/costs.py`, `_cost_inputs` in `core/rules.py`)

```
upfront           = deposit + brokerage + setup
effective_monthly = [brokerage + setup + (rent + maint + extras) * t_paid + deposit * 0.0075 * t_paid] / horizon
t_paid            = max(horizon, lock_in)
```

Defaults (rulebook.toml) fill what the snippet did not state:
- PG: deposit = 2 x rent, no brokerage, no setup, no extras.
- Flat: deposit = 6 x rent, brokerage = 1 x rent, extras Rs 2,500/mo, setup Rs 40,000 (unfurnished) / 15,000 (semi) / 0 (furnished). Solo means no splitting.

## 2. Hard-limit checks, all 6 listings

Rents are back-solved from the displayed numbers; they match the snippets' range ends exactly.

| Listing | Rent | Deposit | Effective monthly | vs Rs 15,000 | Upfront | vs Rs 60,000 | Commute | vs 40 | Result |
|---|---|---|---|---|---|---|---|---|---|
| Zolostays PG Chennai | 4,720 | 9,440 (2x default) | 4,791 | pass | 9,440 | pass | unknown | skipped | **Pass** |
| Zolostays Velachery PG | 9,000 | 18,000 (2x default) | 9,135 | pass | 18,000 | pass | 25 | pass | **Pass** |
| Perungudi Co-Ed PG | 14,850 | 29,700 (2x default) | 15,073 | **fail (+73)** | 29,700 | pass | n/a | pass | Rejected |
| Perungudi Boys-Only PG | 30,100 | 60,200 (2x default) | 30,552 | **fail** | 60,200 | **fail (+200)** | n/a | pass | Rejected |
| 2 BHK Kodambakkam | 20,000 | 120,000 (6x) | 28,400 | **fail** | 180,000 | **fail** | ~45 | **fail** | Rejected |
| 2 BHK Chetpet | 40,000 | 200,000 (stated) | 47,333 | **fail** | 240,000 | **fail** | ~55 | **fail** | Rejected |

A listing is rejected on any single failure. An unknown commute is not a failure (decision 5 in `decision.md`).

### Worked arithmetic

- **Zolostays Chennai:** 4,720 + 9,440 x 0.0075 = 4,720 + 70.8 = **4,790.8**. Upfront = 9,440.
- **Zolostays Velachery:** 9,000 + 18,000 x 0.0075 = 9,000 + 135 = **9,135**. Upfront = 18,000.
- **Perungudi Co-Ed:** 14,850 + 29,700 x 0.0075 = 14,850 + 222.75 = **15,072.75**, which is Rs 72.75 over the cap.
- **Perungudi Boys-Only:** 30,100 + 60,200 x 0.0075 = 30,100 + 451.5 = **30,551.5**. Upfront 60,200 is Rs 200 over cash.
- **Kodambakkam** (t = 12, lock-in 6, so t_paid = 12):
  - Monthly bill = rent 20,000 + extras 2,500 = 22,500. Deposit carry = 120,000 x 0.0075 = 900.
  - TCO = 20,000 (brokerage) + 40,000 (setup) + (22,500 + 900) x 12 = 60,000 + 280,800 = 340,800.
  - Effective monthly = 340,800 / 12 = **28,400**.
  - Upfront = 120,000 + 20,000 + 40,000 = **180,000**.
- **Chetpet** (furnished, so setup 0):
  - Monthly bill = 40,000 + 2,500 = 42,500. Deposit carry = 200,000 x 0.0075 = 1,500.
  - TCO = 40,000 (brokerage) + (42,500 + 1,500) x 12 = 40,000 + 528,000 = 568,000.
  - Effective monthly = 568,000 / 12 = **47,333**.
  - Upfront = 200,000 + 40,000 = **240,000**.

## 3. Scoring the two survivors (`core/scoring.py`)

Weights: affordability 30, commute 20, liquidity 15, lifestyle 15, reputation 10, flexibility 10. Cash 60,000 is above 3 x budget (45,000), so the liquidity boost is not applied.

| Sub-score | Formula | Zolostays Chennai | Zolostays Velachery |
|---|---|---|---|
| Affordability | `10 x (1 - r) / 0.4`, r = monthly / budget | r = 0.319 -> **10.00** | r = 0.609 -> **9.78** |
| Commute | `10 x (40 - m) / 30`; unknown = 5 | unknown -> **5.00** | m = 25 -> **5.00** |
| Liquidity | `10 x (1 - upfront/cash) / 0.5`, cap 10 | h = 0.843 -> **10.00** | h = 0.700 -> **10.00** |
| Reputation | no signals = 5 | **5.00** | **5.00** |
| Flexibility | 0.6 x 10 x (1 - 1/12) + 0.4 x 10 x (1 - 1/3) | **8.17** | **8.17** |
| Lifestyle (L) | not visible; both have the same unknown fields | L | L |

Total = (30 x aff + 20 x com + 15 x liq + 15 x L + 10 x rep + 10 x flex) / 100:
- Chennai = 3.00 + 1.00 + 1.50 + 0.50 + 0.82 + 0.15 L = **6.82 + 0.15 L**
- Velachery = 2.93 + 1.00 + 1.50 + 0.50 + 0.82 + 0.15 L = **6.75 + 0.15 L**

The ranking holds for any L, since only affordability differs (0.07 points). With L = 5 (all unknown), the scores are 7.57 and 7.50.

## 4. How the final answer was formed

1. **Rank:** Chennai first on the 0.07 affordability gap. Both tie on commute, liquidity and flexibility.
2. **PG vs flat:** `decide()` compares the average score of each kind's survivors. No flat survived, so PG wins by default.
3. **Explain:** LLM #2 gets only the 2 ranked options with their costs, sub-scores and unknowns. The 4 rejected ones are listed by the UI from `reasons_rejected`, so the LLM never saw them.

## 5. Weak spots in this result

- **Deposit overstated.** The Zolostays snippets say the deposit is one month's rent, but the LLM left it null, so the 2-month PG default applied. With the stated deposit, Chennai upfront is Rs 4,720 and Velachery Rs 9,000. Both would still pass.
- **Co-Ed PG lost by Rs 73.** Its deposit is also the default (2 months). With a 1-month deposit, 14,850 + 14,850 x 0.0075 = 14,961, which is under budget. This one would likely have passed.
- **Boys-Only PG:** with a 1-month deposit, upfront is Rs 30,100 (passes), but rent 30,100 still breaks the budget.
- **Range ends read as listings.** Rents are the low or high ends of a price range ("from about", "up to"), not specific properties. "Zolostays PG Chennai" is a city-wide range, with no area, so its Rs 4,720 and 5 neutral commute score are weak evidence.
- **Flats are a poor fit.** Both are whole 2 BHK units priced for a solo user, so even a perfect match would not fit Rs 15,000.
- **The search pool is small.** 5 usable results gave 6 listings. More options need more results or looser cutoffs (for example a small tolerance band).
