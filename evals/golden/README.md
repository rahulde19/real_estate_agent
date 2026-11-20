# Golden set

Frozen inputs and answer keys that the evaluation phases (roadmap Phase 8) read. Loader: `src/homefinder/evals/golden.py`. Checked offline by `tests/test_golden.py`.

## What is in here

| File | What |
|---|---|
| `pages.json` | 42 pages, each with the text the extraction step would see and the expected answer |
| `profiles.json` | 5 golden user profiles (`UserProfile` values) |
| `expected.json` | ranking snapshot per profile (ranked names and scores, recommended kind, closest misses), written by `scripts/snapshot_ranking.py`; `reviewed` is false until a person reads it |
| `review.md` | the pages whose keys are most open to argument, for a person to confirm |

## Where the pages come from

- `g01`–`g28` (`origin: sample` / `live-capture`): the 6 saved sample pages and the 22 pages of one live Tavily capture for Chennai, unchanged. Their keys are the earlier files `tavily_sample/*_gold.json`, which stay where they are because `tests/test_prefilter.py` and `scripts/benchmark_extraction.py` read them. `tests/test_golden.py` checks the two copies agree.
- `s01`–`s14` (`origin: synthetic`): written by hand, one hard case each, on reserved `.example` domains. They test the cases (price ranges, several listings on a page, duplicates across portals, a contradiction, review and forum text, a second city, junk that contains rupee amounts). They do **not** represent the real distribution of scraped text. Real pages keep their `g` ids.

## How the keys were made, and how far to trust them

Written by Claude on 16-11-2026 by reading each page. The keys are a **judgment**, not ground truth from the sites. A listing marked `"judgment": true` was built from a price range or a "from" price (the low end is the rent). A page is `reviewed: claude` until a person confirms it (`reviewed: user`). On 20-11-2026 the user confirmed the 16 most arguable pages, listed with their decisions in `review.md`; the other 26 are still unreviewed.

## Page record

```
id, origin, city, url, title, content (at most 1200 characters, as the app sees it), tags, note, reviewed,
dup_group (optional: pages that show the same property)
expected.source_type    ad | builder | forum | review | null
expected.listings[]     Listing fields (name, kind, rent, deposit, area, sharing, ...), plus
                        judgment (bool), alt_rents (other accepted rents), extraction_confidence (optional)
expected.evidence[]     subject, aspect, polarity, severity
```

Tags used: `ad`, `builder`, `forum`, `review`, `price_range`, `multi_listing`, `no_listing`, `junk`, `off_topic`, `truncated`, `two_city`, `duplicate_pair`, `contradiction`, `evidence`, `rupee_amount_not_a_rent`, `budget_not_a_rent`, `good_property_bad_caretakers`, `positive_only`, `flat_full_terms`, `flat_no_deposit`, `lifestyle_fields`.

## Matching rules (used by Phase 8.2)

- A key listing is **found** when the model returns a listing of the same `kind` whose `rent` is within ₹1 of the key's `rent` or of any of its `alt_rents`. Extra listings the model returns are false positives.
- Other fields are then scored one by one: exact for `kind`, `deposit`, `sharing`, booleans and `furnishing`; case-insensitive for `area`. A field the key leaves out is not scored.
- Evidence items match on `aspect` and `polarity` (subject by name); `severity` is reported but is not a pass/fail field.
- `source_type` is not scored on pages tagged `off_topic`, which are not about housing in the target cities. Any evidence item the model returns for an `off_topic` page is a false positive (it must not reach a user's Reputation score).
- A key rent must appear in the page text; `tests/test_golden.py` enforces this so a key cannot claim a number the page does not contain.

## Known limits

- The prefilter (`core/prefilter.py`) skips pages with no rupee amount, which includes review pages `s07` and `s08`. They are in the set so 8.2 can measure evidence extraction by calling the extractor directly, but the app only sees such pages if their domain is in `[extraction] evidence_domains`.
- 42 pages is enough to catch a regression, not to give tight confidence intervals.

## Adding a page

Add a record to `pages.json` with a new id (`g` real, `s` synthetic), a key, a `note` saying why the key is what it is, and `reviewed: claude`. Run `.venv/bin/pytest tests/test_golden.py`. Never edit a key to make a model pass: change it only when the key was wrong, and say so in `decision/decision.md`.
