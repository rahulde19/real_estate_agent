# Roadmap

## Phase 1 — Baseline Pipeline (current state)

### 1.1 Linear pipeline
- [x] Define project mission and scope
- [x] Finalize tech stack decisions
- [x] Streamlit intake form producing a `UserProfile`
- [x] Search step with saved Tavily sample (`SEARCH_SOURCE=sample`) and live mode
- [x] LLM extraction of listings (validated, one retry)
- [x] Deterministic costs, six scores and hard cutoffs
- [x] Rank top 7 and pick PG vs flat
- [x] LLM explanation grounded in the ranked facts
- [x] Offline tests with stubbed search and LLM

---

## Phase 2 — Deterministic Core Hardened

### 2.1 Cost engine tests
- [x] Deposit double-count: ₹60k refundable deposit over 12 months costs about ₹5.4k, not ₹65k
- [x] Short stay (`t = 2`): check which term dominates
- [x] Partial forfeiture: `delta·D` flows through to TCO
- [x] Shared flat: 3BHK with 3 flatmates divides `R`, `D`, `B`, `S`
- [x] Lock-in longer than the stay: `t_paid = lock_in` raises the cost

### 2.2 Scoring tests
- [x] "Weights flip the ranking" test: A = 6.75 / B = 7.00 with 30/15/20/15/10/10
- [x] Same sub-scores with 50/10/10/15/10/5 give A = 7.45 / B = 6.70

### 2.3 Rulebook cleanup
- [x] Move hard-coded thresholds (0.6 / 1.0, 25% commute, 3-month notice, lifestyle 1 / 0.5 / 0) into `rulebook.toml` (also the 60/40 flexibility split, neutral commute 5, reputation 5 and /2, top-3 PG vs flat window)
- [x] Set `delta` to 10%
- [x] Confirm the existing default brokerage `B` and setup cost `S` (kept as generic assumptions)
- [x] Update `rulebook.md` and log the decisions

---

## Phase 3 — City Is an Input

Chennai stays as the dev and test city (sample file, fixtures). The user chooses the city.

### 3.1 Remove hard-coded city
- [x] Make the city an editable UI input (default Chennai) and drop Chennai from the title and caption
- [x] Remove the `"Chennai"` default from `UserProfile` and `search_web`
- [x] Take the city from the profile in `agent/prompts.py` and the search queries
- [x] Check `grep -i chennai src/` finds nothing outside comments and dev defaults

### 3.2 Sample-mode guard
- [x] Warn when the city is not Chennai and `SEARCH_SOURCE=sample`
- [x] Require `SEARCH_SOURCE=live` for other cities
- [x] Test: other city + sample mode gives a clear message, not wrong results

### 3.3 Generic defaults
- [x] Mark `[defaults]` in `rulebook.toml` as a generic baseline
- [x] Add an empty per-city override table
- [x] Update `rulebook.md` to say the defaults are assumptions, not Chennai data

---

## Phase 4 — City Cost Context

### 4.1 CSV loader
- [x] Add `livingcost_india_all_inr.csv` to the repo
- [x] Load it keyed by the user's city, cleaning leading spaces and "City, State" names
- [x] Flag repeated or impossible values as `data_quality: suspect`
- [x] Fall back to a labelled default when the city is not in the CSV
- [x] Tests: Chennai row loads, unknown city falls back, suspect rows flagged

### 4.2 Estimate out-of-pocket extras
- [x] Compute non-rent cost, then the housing-linked share (50%) split 70% food / 30% utilities and wifi
- [x] Use `housing_linked_share` and `food_share_of_linked` from the rulebook
- [x] Replace the `meal_cost` and `flat_extras` guesses
- [x] Test: Chennai food ≈ ₹9,564 and utilities + wifi ≈ ₹4,099

### 4.3 Benchmark label
- [x] Show "X% above/below city benchmark" on rent (not scored)

---

## Phase 5 — Saved Documents

### 5.1 Documents table
- [x] Create the SQLite `documents` table
- [x] Add a small repository module (create, read, update)
- [x] Test: create and read rows

### 5.2 Store search results
- [x] Save each search result as a row with `source_url`, `agent_source`, `retrieved_at` and `raw_text`
- [x] Test: re-running the sample does not duplicate rows

### 5.3 Save and reuse extraction
- [x] Make the extraction prompt user-independent
- [x] Save validated output as `extraction_json` with `prompt_version` and `model_id`
- [x] Reuse the saved answer when the version matches
- [x] Test: a second run makes zero LLM extraction calls

### 5.4 Re-extraction and failures
- [x] Re-extract lazily when `prompt_version` or `model_id` changes
- [x] Record a failure marker for invalid output
- [x] Test: bumping the version re-extracts only the rows that are needed

---

## Phase 6 — Cut Extraction LLM Calls

Extraction is one LLM call per search result (about 20 per fresh run, plus a retry on invalid JSON). The saved-document cache only helps repeat runs. This phase explores ways to cut calls on a first run without shrinking the candidate pool. RAG was considered and set aside here: Tavily is already the retrieval step, and dropping pages would drop listings (the RAG vault in 8.5 is for stable area facts, not listings).

Built and measured offline on one live Chennai search plus the default sample (no Gemini calls; tokens are estimates). Results: `docs/benchmarks/phase6_extraction.md`. New settings are in `rulebook.toml` and default to `prefilter = true`, `batch_size = 3` (choice made in 6.5; set `false` / `1` for the old behaviour).

### 6.1 Measure the baseline
- [x] Record per run: LLM calls, input/output tokens, wall time, listings found (shown in the app, appended to `data/run_log.jsonl`)
- [x] Save the Chennai sample numbers as the baseline in `docs/` (estimated; real numbers appear on the first real run)

### 6.2 Pre-filter pages (no LLM)
- [x] Skip pages with no rent-like pattern, too little text, or non-listing domains
- [x] Put the filter thresholds in `rulebook.toml`
- [x] Test: a junk page makes zero LLM calls; no listing found in the baseline is lost on the sample (and on the live capture)

### 6.3 Batch pages per call
- [x] Send k pages in one prompt and split the answer back per `source_url`
- [x] Keep the per-document cache and failure markers working per URL
- [x] Test: N uncached pages make about N/k calls; a bad batch falls back to smaller batches
- [x] Check batching quality against the real Gemini model (16-11, small check only: on the 6-page sample, two batched calls answered every page with valid JSON and no fallback; the 22-page live capture is still unmeasured)

### 6.4 Reduce inputs
- [x] Try lower `max_results` and a shorter text limit per result (offline; no gain found, not recommended)
- [x] Check the top-ranked options are unchanged against the baseline (weak evidence: the answer keys are small)

### 6.5 Compare and decide
- [x] Table: calls, tokens, listings found and top-7 overlap for each option and combination
- [x] Choose the defaults (`prefilter = true`, `batch_size = 3`) and log the decision (#41); batch size raised to 5 on 20-11 (#54)

_The old 6.5 "cheaper extraction model" step was dropped: `gemini-3.5-flash-lite` is already the smallest tier in use._

---

## Phase 7 — Rulebook Completeness

Built and tested offline, then run once on the Chennai sample with Gemini (decision #48). The extraction prompt and listing schema changed once, so saved pages are re-extracted once on the next real run. Open choices from design spec §13 were settled here and logged as decisions #42–#47.

### 7.1 Provenance and confidence
- [x] Add provenance, `extraction_confidence` and a missing-fields list to `Listing`
- [x] Add a confidence tag, with imputed deposits labelled "estimated" (median of the run's listings, rulebook default as fallback)
- [x] Show both in the UI

### 7.2 Deduplication
- [x] Block by locality, type and BHK/sharing
- [x] Match on phone, building name, geo-point and rent within ±5%
- [x] Assign a `property_id` and merge matches (computed per run, not stored as a column)
- [x] Test: repeated broker ads count once

### 7.3 Staleness
- [x] Compute age from the earliest `first_seen_at`
- [x] Apply penalties and the "re-verify" flag (spec bands 15 / 30 / 45 days; penalties 0.3 / 0.5 / 0.7, a tenth of the spec's −3 / −5 / −7)
- [x] Test each age band

### 7.4 Closest misses
- [x] Define "closest miss" (failed exactly one hard cutoff; smallest overshoot first)
- [x] Show fewer than 7 plus misses that name the failed constraint
- [x] Show them in the UI

### 7.5 Evidence items
- [x] Define the evidence schema (aspect, polarity, severity)
- [x] Map aspects to the six criteria
- [x] Use ads for source reliability only, and reviews and forum posts for Reputation
- [x] Test: "good property, bad caretakers" splits into two items
- [ ] Check evidence quality against the real Gemini model (the 6-page sample has no review text, so no evidence item has been seen from Gemini yet; `source_type` looked right on all 6 pages)

---

## Phase 8 — Evaluation and Safety

Goal: show, with numbers, that the two LLM steps can be trusted and that scraped text cannot steer the app. Offline checks run in every test run; checks that call Gemini are run by hand, cost a known number of calls, and write their results to `docs/evals/`. Storage stays SQLite (no Postgres).

### 8.1 Golden set
- [x] Grow the hand-made answer keys (`tavily_sample/*_gold.json`) into one frozen golden set in `evals/golden/`: 42 pages (28 real, 14 hand-written) with the expected listings and fields
- [x] Cover the hard cases: price-range pages, several listings on one page, junk pages with no listing, review / forum text with evidence items, a second city, duplicate ads across portals, a contradictory price
- [x] Add 5 golden user profiles (solo tight cash, solo comfortable, friends sharing, family, short stay in a second city)
- [x] Fill in the expected top options and closest misses per profile (`evals/golden/expected.json`, written by `scripts/snapshot_ranking.py`)
- [ ] A person reads the five rankings in `expected.json` and sets `"reviewed": true` (the file is a snapshot of the code's output, not an independent answer; see the family-of-four finding in 8.3)
- [x] Write down how each key was made and by whom (`evals/golden/README.md`: a judgment by Claude, not ground truth)
- [x] A person confirms the ambiguous keys listed in `evals/golden/review.md` (16 pages now `reviewed: user`; the other 26 are unreviewed)

### 8.2 Extraction evaluation (real model)
- [x] `scripts/eval_extraction.py`: run the golden pages through Gemini and compare with the keys (built and tested offline; dry run by default, `--yes` calls Gemini; the Gemini budget for this phase is 7 calls per run, enforced by `--max-calls`, default 7)
- [ ] Smoke test: `--quick` (7 pages, exactly 7 calls, single mode) to check the script and the scoring on the real model. It uses the same 7 calls as the baseline, so run only one of the two unless more Gemini access is available
- [x] Baseline run (7 calls, done 21-11, results in `docs/evals/extraction_b6.md` and `extraction_batch_b6.json`; recall 86%, precision 100%, schema-valid 86%, invented rents 0%, source_type 88%): `--mode batch --batch-size 6 --yes` sends all 42 golden pages in 7 batches of 6. The full single-page run (about 51 calls) is dropped because of the 7-call limit. Batch mode has no fallback, so it measures raw batch quality, and batch size 6 is one above the shipped 5 (the app's `batch_max_chars` is not applied in the eval)
- [x] Report listing recall and precision, per-field accuracy (deposit, area, sharing, booleans, furnishing, ...), and the schema-valid rate (scoring in `src/homefinder/evals/extraction_eval.py`)
- [x] Report the invented-number rate: rents not present in the page text (reuses `rent_supported`)
- [x] Compare one page per call against batches of 3, and record calls and tokens for each (`--mode single|batch|both`; batches have no fallback, so this measures raw batch quality)
- [x] Report evidence items and `source_type` against the keys (scoring built; the 7.5 open item closes when the real run is done)
- [ ] Set pass thresholds and save the run as the baseline, so a prompt or model change can be compared with it (thresholds from 7 pages are rough: say so in the 8.7 report)

### 8.3 Ranking regression and determinism
- [x] Golden profiles × golden listings: the ranked names, scores and closest misses match a saved snapshot (`tests/test_ranking_regression.py`, `src/homefinder/evals/ranking_eval.py`)
- [x] Same stored data and profile give the same ranking on repeat runs (mission success item 3)
- [x] Property-style tests: a higher budget (or more cash, or a longer commute tolerance) never removes an option; a repeated ad never changes the ranking; unknown commute and lifestyle data never reject; confidence never changes a score
- [x] A family of four no longer sees PGs: `evaluate` rejects every PG as `unsuitable` when the mover is a family (rulebook `family_excludes_pg`), and such PGs are not closest misses

### 8.4 Explanation evaluation
- [x] Code check: every number and property name in the explanation appears in the facts it was given, and the order matches the ranking (`src/homefinder/evals/explanation_eval.py`, tested in `tests/test_explanation_eval.py`)
- [x] Code check: the explanation mentions the flags it was given ("deposit estimated", "re-verify"), never mentions commute times, and ends with a verify-before-paying line
- Not done on purpose: no LLM judge, because of the 7-call Gemini limit. Whether the PG vs flat advice fits the user's answers and whether each caveat is sensible are not checked by anything; the 8.7 report must say so. Checking one real explanation per profile by hand is optional

---

## Phase 9 — Ship

Goal: a public repository and a live demo that someone can open from a portfolio link.

### 9.1 Clean up
- [x] `git init` (done), `.gitignore` check (`.env`, database, run log, `.claude/`, `.DS_Store` are ignored; no key patterns in the tree), MIT licence (`LICENSE`)
- [x] First commit (21-11): made with `tavily_sample/`, `evals/golden/pages.json` and `data/livingcost_india_all_inr.csv` included, by the user's decision (source licences of the scraped text and the CSV are not verified)
- [x] Fix leftovers: `pyproject.toml` description and `pandas` fixed; `docs/response1/` kept as a historical record with a note at the top (its text describes the code of 08-11)
- [x] Commute weight (20% of the score does nothing today): the UI now says so under "Our suggestion"; weights unchanged

### 9.2 Demo mode
- [x] Ship the Chennai sample with its saved extractions, so the public demo costs one explanation call per run (`DEMO_MODE=1`; `src/homefinder/services/demo.py`; `data/demo_extractions.json` built by `scripts/build_demo_data.py` with 1 Gemini call: only 5 of the 22 saved pages pass the prefilter, so the demo has 5 listings to rank)
- [x] Per-session (3) and per-day (50, all visitors, UTC) run limits; live search is switched off in demo mode (a visitor's own keys are not supported)
- [x] UI polish for a first-time visitor: prefilled example (Taramani), what the app does in two lines, a demo notice with the data date. Checked with Streamlit's test harness only, not looked at in a browser; the confidence and closest-miss sections were not changed

### 9.3 Deploy
- [x] Deploy on Streamlit Community Cloud with keys in its secrets store (done 21-11: https://realestateagent-fpkc5mkc5tgmpsmd3w6ins.streamlit.app/, main file `streamlit_app.py`, `GEMINI_API_KEY`, `MODEL`, `DEMO_MODE` in the Secrets box)
- [x] GitHub Actions: run the offline tests on every push (`.github/workflows/tests.yml`; its install-and-test steps passed in a clean local venv with no keys, but the workflow itself has not run on GitHub yet)
- [x] Smoke-test the deployed app end to end (21-11: one demo run with the prefilled example returned a suggestion, 2 options and the commute note; 1 Gemini call). Not yet checked: the per-session limit and the daily limit on the hosted app, the Actions run on GitHub, other browsers or a phone

### 9.4 Portfolio write-up
- [x] README for a reader with two minutes (`README.md` at the root): the problem, a screenshot of the live demo, "LLM interprets, code decides", an evaluation table built from the baseline run (there is no 8.7 report), known limits. The architecture diagram stays in `docs/architecture.md` and is linked, not embedded
- [x] A short "what I would do next" section taken from the list below (also names prompt-injection and failure-handling tests, which were dropped from Phase 8)

---
