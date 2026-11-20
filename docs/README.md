# Home Finder

Agentic app (LangGraph + Streamlit) that suggests the top 7 PGs / flats for someone moving to any Indian city they name. Chennai is only the dev/test city.

```
Streamlit form → Tavily search → LLM: extract listings + evidence (saved per document, reused) → merge duplicates → formulas.py → rules.py (rulebook.toml) → LLM: explain
```

## Setup
```bash
uv venv --python 3.13 .venv && uv pip install -r requirements.txt && uv pip install -e . --no-deps
cp .env.example .env        # add GEMINI_API_KEY (and MODEL); TAVILY_API_KEY only for SEARCH_SOURCE=live
.venv/bin/streamlit run src/homefinder/ui/app.py
.venv/bin/pytest            # offline tests
```

## Layout
```
decision/decision.md        log of design decisions
specs/                      constitution: mission.md, tech-stack.md, roadmap.md (phase order)
docs/response1/             worked example: LLM input and cutoff/score arithmetic for one run
data/                       livingcost_india_all_inr.csv: city cost of living (source: livingcost.org)
data/homefinder.db           saved search results and extractions (created on first run, git-ignored)
src/homefinder/
  config/    config.py (paths, env, loader), rulebook.toml (weights, money, scoring thresholds, lifestyle values, decision, defaults - edit here), rulebook.md
  core/      formulas.py, rules.py, dedup.py, evidence.py, prefilter.py   (deterministic logic)
  agent/     graph.py, extraction.py, state.py, prompts.py  (LangGraph)
  services/  search.py (Tavily), llm.py (Gemini), documents.py (SQLite store), city_costs.py (cost-of-living CSV loader), metrics.py (LLM call/token meter)
  evals/     golden.py (loads the golden set), extraction_eval.py, ranking_eval.py, explanation_eval.py
  ui/app.py  Streamlit page
scripts/snapshot_ranking.py        writes the ranking snapshot evals/golden/expected.json (offline)
scripts/eval_extraction.py        scores the real model against the golden set (calls Gemini only with --yes; writes docs/evals/)
scripts/benchmark_extraction.py   offline benchmark of extraction options (no LLM); writes docs/benchmarks/phase6_results.md
tavily_sample/             saved search: tavily_ans.json (default sample), tavily_live_chennai.json (one live capture) + hand-made answer keys (*_gold.json)
docs/benchmarks/           Phase 6 findings and generated results
data/run_log.jsonl         one line per app run: calls, tokens, seconds (git-ignored)
evals/golden/              golden set for the evaluation phase: pages.json (42 pages with answer keys), profiles.json, README.md (how the keys were made), review.md
tests/
```
Default `SEARCH_SOURCE=sample` reads a saved Chennai-only search; other cities need `SEARCH_SOURCE=live` (the app says so instead of showing wrong results).
Each option shows a confidence tag, flags ("deposit estimated", "re-verify") and its sources; if fewer than 7 pass your limits, the closest misses are listed with the limit each one failed.
Listings come from web snippets; verify before paying anything. Defaults in `[defaults]` are assumptions, not market data.
