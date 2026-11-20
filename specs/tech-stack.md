# Tech stack

_Part of the project constitution. **Current** = in the code today; **Planned** = decided, not built yet._

## Stack

| Layer | Choice | Status | Notes |
|---|---|---|---|
| Language | Python ≥ 3.11 (dev on 3.13) | Current | src-layout package `homefinder` |
| Env / packaging | `uv`, `pyproject.toml` + pinned `requirements.txt` | Current | Update both files together |
| Orchestration | LangGraph | Current | Linear 5-node graph. Interrupts, loops and a checkpointer are optional work after shipping |
| LLM | Gemini via `langchain-google-genai`, model from `MODEL` (default `gemini-3.5-flash-lite`) | Current | **Swappable:** every LLM call stays in `services/llm.py` |
| LLM tiers | Cheaper model for extraction, stronger model for the explanation | After shipping | Open item (spec §13) |
| Schemas / validation | pydantic v2 | Current | All LLM output is validated against a schema, with one retry |
| Search | Tavily (`tavily-python`), queries built from the user's city | Current | Default `SEARCH_SOURCE=sample` reads `tavily_sample/tavily_ans.json` (Chennai only; saves API credits and gives repeatable runs); `live` calls the API for any city |
| Rulebook | `src/homefinder/config/rulebook.toml` + `rulebook.md` | Current | Loader checks that the weights sum to 100 |
| Cost-of-living data | `data/livingcost_india_all_inr.csv` + `services/city_costs.py`, looked up by the user's city | Current | Covers 221 Indian cities; a city missing from it, or an impossible row, falls back to a labelled default; repeated rows are flagged suspect |
| Storage | SQLite with one `documents` table (`raw_text`, `extraction_json`, `prompt_version`, `model_id`, provenance), `services/documents.py` | Current | `DB_PATH`, default `data/homefinder.db`; all SQL in one module. SQLite is the final store for this project; Postgres was dropped (decision #49) |
| Checkpointer | LangGraph SQLite checkpointer | After shipping | Only if the chat intake agent is built |
| RAG vault | Developer-uploaded verified docs, stable area facts only | After shipping | Store and embedding model not chosen |
| UI | Streamlit (`ui/app.py`) | Current | Form intake |
| Tests | pytest, offline (search and LLM stubbed) | Current | No network or keys needed |
| Evaluation | Golden set in `evals/`, scripts that call the real model, results in `docs/evals/` | Planned (Phase 8) | Extraction accuracy, ranking regression, explanation checks, prompt-injection tests. Run by hand because they cost LLM calls |
| Hosting | Streamlit Community Cloud, GitHub Actions for the offline tests | Planned (Phase 9) | Demo runs on the saved sample with run limits |
| Config / secrets | `python-dotenv`, `.env` (see `.env.example`) | Current | `require_keys()` stops the UI if keys are missing |

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Always | LLM access |
| `MODEL` | Always | Gemini model id |
| `SEARCH_SOURCE` | No (default `sample`) | `sample` or `live` |
| `TAVILY_API_KEY` | Only when `live` | Tavily search |
| `DB_PATH` | No (default `data/homefinder.db`) | SQLite file for saved documents |

## Stack rules

1. **Provider isolation:** no module outside `services/llm.py` imports an LLM SDK. Changing provider should touch only that file, the dependencies and `.env.example`.
2. **Deterministic core:** `core/` (costs, scoring, lifestyle, rules) is pure Python with no I/O and no LLM.
3. **Develop offline by default:** the saved Tavily sample (and, later, saved `extraction_json`) means a normal run spends no search credits and repeats exactly.
4. **City-agnostic code:** the city comes from `UserProfile.city`. Prompts, queries, UI text and defaults must not name a city. Per-city values live in config or data files. Sample mode is a Chennai-only dev aid: any other city is blocked with a clear message (`SampleModeError`, UI error) unless `SEARCH_SOURCE=live`.
5. **No new infrastructure:** no Postgres, Redis, FastAPI, maps API or hosted vector DB. SQLite and Streamlit are enough to ship.
6. **Pin what you install:** a new dependency goes into both `pyproject.toml` and `requirements.txt`, with a `decision.md` entry.

## Dropped

Postgres, Redis, FastAPI and the separate `extraction_snapshots` / `evidence_items` / `score_runs` tables (decision #49).

## After shipping (optional)

Maps/routing API for commute, per-city rulebook tuning (rent defaults, deposit norms) and locality-level data. See the roadmap.
