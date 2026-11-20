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
docs/workflow_and_design.md complete workflow and design reference (one run end to end, formulas, modes, evals)
docs/response1/             worked example: LLM input and cutoff/score arithmetic for one run
data/                       livingcost_india_all_inr.csv: city cost of living (source: livingcost.org)
data/homefinder.db           saved search results and extractions (created on first run, git-ignored)
src/homefinder/
  config/    config.py (paths, env, loader), rulebook.toml (weights, money, scoring thresholds, lifestyle values, decision, defaults - edit here), rulebook.md
  core/      formulas.py, rules.py, dedup.py, evidence.py, prefilter.py   (deterministic logic)
  agent/     graph.py, extraction.py, state.py, prompts.py  (LangGraph)
  services/  demo.py (demo mode: saved search and answers, daily run limit), search.py (Tavily), llm.py (Gemini), documents.py (SQLite store), city_costs.py (cost-of-living CSV loader), metrics.py (LLM call/token meter)
  evals/     golden.py (loads the golden set), extraction_eval.py, ranking_eval.py, explanation_eval.py
  ui/app.py  Streamlit page
scripts/build_demo_data.py         builds data/demo_extractions.json for demo mode (calls Gemini only with --yes)
scripts/snapshot_ranking.py        writes the ranking snapshot evals/golden/expected.json (offline)
scripts/eval_extraction.py        scores the real model against the golden set (calls Gemini only with --yes; writes docs/evals/)
scripts/benchmark_extraction.py   offline benchmark of extraction options (no LLM); writes docs/benchmarks/phase6_results.md
tavily_sample/             saved search: tavily_ans.json (default sample), tavily_live_chennai.json (one live capture) + hand-made answer keys (*_gold.json)
docs/benchmarks/           Phase 6 findings and generated results
data/run_log.jsonl         one line per app run: calls, tokens, seconds (git-ignored)
evals/golden/              golden set for the evaluation phase: pages.json (42 pages with answer keys), profiles.json, README.md (how the keys were made), review.md
tests/
```
**Live demo**: https://realestateagent-fpkc5mkc5tgmpsmd3w6ins.streamlit.app/

**Deploying the demo (Streamlit Community Cloud)**: push the repository to GitHub, create an app there with main file `streamlit_app.py` (it adds `src/` to the import path, so the package need not be installed), Python 3.11 or newer, and put the keys in the app's Secrets box, never in the repository:

```
GEMINI_API_KEY = "your-key"
MODEL = "gemini-3.5-flash-lite"
DEMO_MODE = "1"
```

Top-level secrets reach the app as environment variables, which is where `config.py` reads them. The daily run counter is a local file and resets when the app restarts. `.github/workflows/tests.yml` runs the offline tests on every push (no keys needed).

**Demo mode** (`DEMO_MODE=1`, needs only `GEMINI_API_KEY` and `MODEL`): serves the saved Chennai search with saved model answers from `data/demo_extractions.json`, so a run makes no search and no extraction call, only the explanation call. Limits: 3 runs per session, 50 per UTC day (`DEMO_SESSION_RUNS`, `DEMO_DAILY_RUNS`). Rebuild the saved answers after a prompt change with `scripts/build_demo_data.py` (dry run by default; `--yes` makes about 1 Gemini call).

Default `SEARCH_SOURCE=sample` reads a saved Chennai-only search; other cities need `SEARCH_SOURCE=live` (the app says so instead of showing wrong results).
Each option shows a confidence tag, flags ("deposit estimated", "re-verify") and its sources; if fewer than 7 pass your limits, the closest misses are listed with the limit each one failed.
Listings come from web snippets; verify before paying anything. Defaults in `[defaults]` are assumptions, not market data.
