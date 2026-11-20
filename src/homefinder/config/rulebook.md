# Rulebook guide

Explains every variable in [rulebook.toml](rulebook.toml) and the decision framework that uses them. Code: `src/homefinder/core/{formulas,rules,dedup,evidence}.py`.

## Decision framework

Before scoring, repeated ads for one property are merged (`[dedup]`) and evidence items are matched to properties by name. Then for each property the agent runs four stages:

1. **Cost** (`formulas.py`): builds per-person monthly and upfront cost.
   - Flat totals (rent, maintenance, extras, deposit, brokerage, setup) are divided by the number of sharers. PG prices are already per bed.
   - `TCO = brokerage + setup + (rent + maintenance + extras) × months_paid + deposit × opp_rate × months_paid + delta × deposit`, where `months_paid = max(horizon, lock_in)`.
   - `effective monthly = TCO / horizon`. `upfront = deposit + brokerage + advance rent + setup`.
2. **Hard cutoffs** (`rules.py`): a listing is **rejected** if any of these hold:
   - effective monthly > monthly budget
   - upfront > upfront cash
   - known commute > commute tolerance
   - known clash with a lifestyle item marked must-have

   Unknown (missing) data never rejects.
3. **Score** (`formulas.py`): six sub-scores from 0 to 10, combined with the weights below, minus the staleness penalty.
4. **Rank and recommend** (`rules.py::decide`): surviving listings are sorted by final score, the top `top_n` are kept. PG vs flat is recommended by whichever kind has the higher average of its top `kind_window` survivors.
5. **Closest misses** (`rules.py::closest_misses`): when fewer than `top_n` pass, the rejected listings that failed **exactly one** hard cutoff are shown, nearest first: smallest relative overshoot (for example 6% over budget before 20% over), then best score. A must-have clash has no size, so those come last. A PG rejected for a family (`unsuitable`) is never a closest miss. Together with the ranked options they never exceed `top_n`. They are not recommendations and are not sent to the explanation.

Each option also gets a **confidence tag** (`[confidence]`) and flags ("deposit estimated", "re-verify"). These are shown to the user and never change the score.

## Sub-scores (0 to 10)

| Score | Formula |
|---|---|
| affordability | `r = effective monthly / budget`; 10 at r ≤ `afford_full_ratio`, 0 at r ≥ `afford_zero_ratio`, linear between |
| commute | 10 at minutes ≤ `commute_full_frac` × tolerance, 0 at ≥ tolerance; unknown = `commute_unknown` |
| liquidity | `h = 1 − upfront / cash`; 10 when h ≥ `healthy_buffer`, linear below, 0 if cash is exhausted |
| lifestyle | 10 × mean of applicable preferences (`[lifestyle]` match / clash / unknown; "no constraint" items ignored) |
| reputation | `base + sum(source values) / divisor`, one value per distinct review or forum source (`[source_values]`, `[evidence]`); no evidence = base |
| flexibility | `flex_lock_weight` × lock-in part (`1 − lock_in/horizon`) + the rest × notice part (`1 − notice/notice_zero_months`) |

Final score = `Σ weight × score / 100`, minus the staleness penalty (`[staleness]`), clamped to 0 to 10.

## `[weights]`: must sum to 100

| Variable | Value | Meaning |
|---|---|---|
| affordability | 30 | Importance of effective monthly cost vs budget |
| commute | 20 | Travel time to work vs tolerance. Commute is never estimated yet, so every listing gets the same `commute_unknown` score and this weight cannot change the ranking; the UI says so |
| liquidity | 15 | How much upfront cash is left over |
| lifestyle | 15 | Fit with food, privacy, furnishing, noise, gender answers |
| reputation | 10 | Independent evidence (reviews, forum posts) about the property |
| flexibility | 10 | Lock-in and notice period vs stay horizon |

The loader raises an error if the weights don't sum to 100.

## `[money]`

| Variable | Value | Meaning | Used? |
|---|---|---|---|
| opp_rate | 0.0075 | Monthly opportunity cost of money locked in a deposit (9% a year) | Yes, in TCO |
| delta_deposit_deduction | 0.1 | Expected fraction of deposit forfeited to deductions (10%). An assumption, not market data | Yes, in TCO (`delta × deposit`) |
| housing_linked_share | 0.5 | Share of a city's non-rent cost (cost − rent, one person) that follows the housing choice | Yes, in the extras estimate |
| food_share_of_linked | 0.7 | Of that housing-linked share, the part that is food; the rest is utilities + wifi | Yes, in the extras estimate |

## `[liquidity]`

| Variable | Value | Meaning |
|---|---|---|
| tight_cash_multiple | 3 | Cash is "tight" when upfront cash < 3 × monthly budget |
| tight_cash_mult | 1.5 | When tight, the liquidity weight is multiplied by 1.5, then all weights are renormalised to 100 |
| healthy_buffer | 0.5 | Keeping at least 50% of cash after upfront payments earns a full liquidity score |

## `[scoring]`: shape of the sub-score formulas

| Variable | Value | Meaning |
|---|---|---|
| afford_full_ratio | 0.6 | Effective monthly / budget at or below this scores affordability 10 |
| afford_zero_ratio | 1.0 | At or above this scores affordability 0 |
| commute_full_frac | 0.25 | Commute at or below this fraction of tolerance scores 10 (tolerance itself scores 0) |
| commute_unknown | 5.0 | Commute score when minutes are unknown |
| notice_zero_months | 3 | Notice at or above this many months scores 0 on the notice part of flexibility |
| flex_lock_weight | 0.6 | Flexibility = this × lock-in part + (1 − this) × notice part |
| reputation_base | 5.0 | Reputation with no evidence |
| reputation_divisor | 2.0 | Each distinct-source value moves reputation by value / divisor |

## `[lifestyle]`: preference match values

| Variable | Value | Meaning |
|---|---|---|
| match | 1.0 | Listing satisfies the preference |
| clash | 0.0 | Listing is known to conflict (rejects if the item is a must-have) |
| unknown | 0.5 | Listing doesn't say; neutral, never rejects |

## `[decision]`

| Variable | Value | Meaning |
|---|---|---|
| top_n | 7 | Number of options returned |
| kind_window | 3 | PG vs flat compares the mean score of each kind's top N survivors |
| family_excludes_pg | true | when the user is a family, every PG is rejected as "unsuitable" (a family needs a whole flat); such a PG is never shown as a closest miss |

## `[defaults]`: assumptions for missing listing data

These are generic assumptions, not market data for any city. Used only when a scraped listing doesn't state the value. `flat_brokerage_months` and `flat_setup.*` were reviewed in Phase 2.3 and kept as the baseline.

| Variable | Value | Meaning |
|---|---|---|
| pg_deposit_months | 2 | PG deposit as months of rent. **Fallback**, used only when fewer than `deposit_median_min` PGs in the run state a deposit |
| flat_deposit_months | 6 | Flat deposit as months of rent. **Fallback**, as above |
| deposit_median_min | 3 | A missing deposit uses the median deposit-in-months-of-rent of this run's listings of the same type when at least this many state one. Either way the deposit is labelled "estimated" (with its basis) and lowers the confidence tag |
| flat_brokerage_months | 1 | Flat brokerage as months of rent (PG assumed 0) |
| flat_extras | 2500 | ₹/month for the whole flat (power, wifi, water). **Fallback**, used only when the city has no usable cost data |
| meal_cost | 4500 | ₹/month per person, added when the user needs meals and the listing doesn't include them. **Fallback**, used only when the city has no usable cost data |
| pg_lock_in / flat_lock_in | 1 / 6 | Lock-in months |
| pg_notice / flat_notice | 1 / 2 | Notice months |
| flat_setup.furnished | 0 | One-off ₹ to set up a whole flat, by its furnishing state |
| flat_setup.semi | 15000 | " |
| flat_setup.unfurnished | 40000 | " (also used when furnishing is unknown) |

## `[search]`: result size

| Variable | Value | Meaning |
|---|---|---|
| max_results | 8 | Results per Tavily query in live mode |
| max_chars | 1200 | Text kept per result (also applied to the saved sample) |

## `[extraction]`: LLM cost controls (Phase 6)

The shipped defaults are filter on and 5 pages per call (decisions #41 and #54); `prefilter = false` and `batch_size = 1` give the Phase 5 behaviour (one call per page, nothing filtered). The measured effect of each setting is in `docs/benchmarks/phase6_extraction.md`.

| Variable | Value | Meaning |
|---|---|---|
| prefilter | true | When true, pages that cannot hold a listing are skipped with no LLM call (text stays saved as `skipped`) |
| min_chars | 80 | Prefilter: pages with less text than this are skipped |
| blocked_domains | youtube, facebook, instagram, twitter, x, linkedin, pinterest, sec.gov | Prefilter: pages on these domains (and subdomains) are skipped |
| batch_size | 5 | Pages per LLM call; 1 = one call per page. Larger values send several pages together, with fallbacks to smaller batches |
| batch_max_chars | 6000 | Also caps the text of one batch |
| evidence_domains | (empty) | Review and forum sites whose pages pass the prefilter even with no rupee amount, because they can hold evidence items. Empty keeps the Phase 6 call counts; adding a domain adds LLM calls |

The prefilter also skips any page with no rupee-style amount (₹ / Rs / INR / "Lac" / "15k" / "9500/month" / "rent … 9500"). That rule is in `core/prefilter.py`, not tunable: it follows from the extraction rule that a listing needs a concrete monthly rent.

## `[confidence]`: confidence tag (Phase 7.1)

Weak points = number of missing key fields (area, deposit, brokerage for flats, maintenance, lock-in, notice, furnishing, private room, meals, vibe, gender restriction) + the points for the extractor's own confidence in the listing. The tag is shown with its reasons and **never changes the score**.

| Variable | Value | Meaning |
|---|---|---|
| high_max_weak | 3 | At most this many weak points gives "high" |
| medium_max_weak | 6 | At most this many gives "medium"; more gives "low" |
| extraction_weak.high / medium / low / unknown | 0 / 1 / 3 / 1 | Weak points for the extractor's confidence (`unknown` = the model gave none) |

## `[dedup]`: merging repeated ads (Phase 7.2)

Two listings are the same property when they are in the same block (same locality and type; BHK and sharing must not conflict, and an unknown value fits any), their rents are within `rent_tolerance`, and at least one identity signal agrees: phone number (last 10 digits), building name (or the listing name when no building is given), or geo-points within `geo_radius_m`. Chains merge (A = B and B = C gives one property).

| Variable | Value | Meaning |
|---|---|---|
| rent_tolerance | 0.05 | Rents within 5% of the larger one count as the same |
| geo_radius_m | 100 | Geo-points this many metres apart or less count as the same place |

The merged property takes its values from the most reliable source (`[source_values]`), then the earliest sighting; blanks are filled from the other sources. It keeps every source as an observation and gets a `property_id` (a hash of type, locality, name and size, recomputed each run). Known limit: two different places with the same name and locality and near-equal rent are merged.

## `[staleness]`: old listings (Phase 7.3)

Age = days since the **earliest** `first_seen_at` across a property's sources, so a broker's repost does not reset the clock. The penalty of the oldest band reached is subtracted from the 0 to 10 final score.

| From age (days) | Penalty | Flag |
|---|---|---|
| 15 | 0.3 | |
| 30 | 0.5 | |
| 45 | 0.7 | "re-verify" |

The bands are the design spec's (§7.3); the penalties are a tenth of the spec's 3 / 5 / 7, a deliberate deviation (decision #45). Age counts from when this app first saved the page, so it is a lower bound on the listing's real age and a fresh database applies no penalty.

## `[source_values]`: what a source is worth (design spec §7.4)

| Variable | Value | Meaning |
|---|---|---|
| ad | 7 | Source reliability of an advert or portal page. Used only to choose which source's values win when duplicates are merged |
| builder | 5 | Source reliability of the owner's, operator's or builder's own site. Same use |
| review_positive | 7 | What one independent source adds to Reputation for positive evidence |
| complaint | −7 | What one independent source adds to Reputation for a complaint |

## `[evidence]`: evidence items (Phase 7.5)

An evidence item is one opinion about one aspect of one property: `subject` (the property's name), `aspect`, `polarity` (positive / negative) and `severity` (low / medium / high). "Good property, bad caretakers" is two items. Items are matched to a property by name.

| Variable | Value | Meaning |
|---|---|---|
| independent_sources | forum, review | Only evidence from these page types moves Reputation. Ads and builder pages never do |
| severity.low / medium / high | 0.5 / 0.75 / 1.0 | Multiplies the source value |

Reputation: each distinct independent source contributes one value, the mean of its items (`review_positive` or `complaint` × severity). With the defaults, one strong complaint gives `5 + (−7)/2 = 1.5` and one strong positive review gives `8.5`.

`[evidence.aspect_criteria]` maps each aspect to one of the six criteria:

| Criterion | Aspects |
|---|---|
| reputation | property_condition, management, cleanliness, safety |
| lifestyle | food, noise, rules |
| commute | location |
| affordability | hidden_charges |
| liquidity | deposit_refund |
| flexibility | lock_in_notice |

In v1 the map decides which criterion a caveat is shown under in the UI. Only the Reputation score moves, whatever the aspect.

## `[city_costs]`: dataset quality

| Variable | Value | Meaning |
|---|---|---|
| max_rent_share | 0.75 | A row in `data/livingcost_india_all_inr.csv` whose rent is above this share of total cost is unusable and falls back to `[defaults]` (today: Surat, Nellore, Nagpur) |

Rows whose (cost, rent) pair repeats for another city (16 cities, e.g. Kolkata) keep their numbers but are flagged `suspect` and labelled in the UI. Repeats of a single column are common in the data and are not flagged.

## City cost estimates (Phase 4)

With a usable city row: `non_rent = cost_one_person − rent_one_person`; `linked = non_rent × housing_linked_share`; `food = linked × food_share_of_linked` (per person, used as the meal cost when the user needs meals and the listing doesn't include them; family multiplies by group size); `utilities + wifi = linked − food` (used as the whole-flat extras when the listing doesn't state them, then split across sharers). Chennai: food ≈ ₹9,564, utilities + wifi ≈ ₹4,099.

Rent is also shown against the city's one-person rent (`+12%` / `−8%`). It is informational only and never scored; family moves show nothing because a whole flat isn't comparable with a one-person benchmark.

## `[city_overrides]`: reserved

An empty table for per-city overrides of `[defaults]` (for example `[city_overrides.pune]` with `flat_deposit_months = 3`). **Not read by any code yet.** Until it is, every city uses the generic `[defaults]`.

## Still in code (not tunable)

- The hard cutoffs, the closest-miss rule (exactly one failed cutoff) and the PG vs flat rule structure are in `rules.py`.
- The list of key fields behind the missing-fields list is in `formulas.py`; the list of evidence aspects is in `schemas.py`.
- The liquidity formula shape and the weight renormalisation are in `formulas.py` (their parameters are in `[liquidity]`).

`load_rulebook()` raises `ValueError` if `[weights]` doesn't sum to 100.
