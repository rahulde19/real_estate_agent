# Mission
Part of the project constitution.
## Why this exists

People moving to a new city struggle to find a PG or flat and can't tell which option is better *for them*. Listing sites show what is available. They don't show what the user can afford, whether the user can fund a deposit, or what fits their life.

## Mission

**Recommend the Top 7 PGs or flats in the city the user is moving to, chosen as best for the user's financial wellbeing and lifestyle. Explain every recommendation honestly, and make every number traceable to a deterministic rule.**

**City is an input, not a constant.** The user states the city they are moving to. Chennai is only the city used for development and testing (the saved Tavily sample and the first hand-checked listings are Chennai data).

## Who it serves

- Someone moving to **any Indian city they name** alone, with friends (sharing a flat), or with family (needing a full 1/2/3 BHK).
- Especially people with **tight cash**, who can't fund a large deposit and won't borrow for one.

## What success looks like (v1)

1. The user answers money, occupancy and lifestyle questions and gets **up to 7 ranked options**, or fewer plus the closest misses with the constraint each one failed.
2. Every option shows its effective monthly cost, upfront cash needed, score breakdown, caveats and confidence.
3. The same stored data and profile always produce **the same ranking**.
4. Advice on PG versus flat is based on this user's cost, liquidity and flexibility, not a generic rule.

## Principles (non-negotiable)

1. **The LLM interprets; code decides.** LLMs extract and explain. They never produce a number that ranks a place.
2. **Financial wellbeing first.** A user who can't afford the deposit is never shown a place that needs it. The budget is a hard ceiling.
3. **Config, not code.** Rulebook weights, thresholds and assumptions live in `rulebook.toml` and are documented in `rulebook.md`.
4. **Honest about uncertainty.** Unknown data scores neutral, lowers confidence, and is shown to the user. Defaults are labelled as assumptions.
5. **Provenance on everything.** Every record carries its source, retrieval time, producing agent and confidence.
6. **Scraped text is data, never instructions.**
7. **No city is hard-coded.** City names, prompts, defaults and data come from the user's profile or config. Chennai appears only in test fixtures and samples.
8. **Ship it.** v1 is a public demo and repository for a portfolio. Anything that does not help it ship or prove it works waits until after.
9. **Small steps.** One component at a time, each with tests and an entry in `decision/decision.md`.

## Out of scope for v1

Latent "Top 3", the devil's-advocate alternative, city-specific tuning beyond the city-level cost data (for example locality-level costs), maps/routing, the multi-agent design (intake agent, orchestrator, verifier, RAG vault), and production infrastructure (Postgres, Redis, FastAPI, full snapshot tables).

A small evaluation set and safety checks (golden pages and profiles, prompt-injection tests) are in scope: they are Phase 8 of the roadmap.
