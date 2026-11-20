"""LangGraph pipeline: search -> extract (LLM, saved per document) + dedupe -> evaluate -> decide -> explain (LLM)."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from homefinder.agent.prompts import extract_prompt_version
from homefinder.config.config import DB_PATH, MODEL, SEARCH_SOURCE, load_rulebook
from homefinder.core.dedup import dedupe
from homefinder.core.evidence import attach_evidence
from homefinder.core.rules import decide, deposit_medians, evaluate
from homefinder.services.city_costs import lookup_city_costs
from homefinder.agent.extraction import extract_pages
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import extract_document, extract_documents, write_suggestion
from homefinder.services.metrics import METER
from homefinder.services.search import search_web
from homefinder.agent.state import AgentState


def _run_metrics(s: AgentState, timings: dict) -> dict:
    """Phase 6.1: what one run cost. Token counts come from the Gemini responses (0 when stubbed)."""
    st, m = s.get("extraction_stats", {}), METER.snapshot()
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "city": s["profile"].city,
        "pages": len(s.get("search_results", [])),
        "skipped": st.get("skipped", 0), "reused": st.get("reused", 0),
        "extracted": st.get("extracted", 0), "failed": st.get("failed", 0),
        "extract_requests": st.get("calls", 0),
        "llm_calls": dict(m["calls"]),
        "tokens_in": dict(m["tokens_in"]), "tokens_out": dict(m["tokens_out"]),
        "seconds": dict(timings),
        "listings": len(s.get("listings", [])), "ranked": len(s["decision"].ranked),
        "duplicates_merged": st.get("duplicates_merged", 0), "evidence_items": len(s.get("evidence", [])),
        "closest_misses": len(s["decision"].closest_misses),
    }


def build_graph(search=search_web, extract=extract_document, explain=write_suggestion, rulebook=None,
                city_lookup=lookup_city_costs, repo=None, prompt_version=None, model_id=MODEL, agent_source=None,
                extract_many=extract_documents, run_log: Path | None = None, today=None):
    """`today` is a function returning the date used for listing age (default: the current UTC date)."""
    rb = rulebook or load_rulebook()
    repo = repo or DocumentRepo(DB_PATH)
    prompt_version = prompt_version or extract_prompt_version()
    agent_source = agent_source or f"tavily:{SEARCH_SOURCE}"
    today = today or (lambda: datetime.now(timezone.utc).date())
    timings: dict[str, float] = {}

    def n_search(s: AgentState):
        METER.reset()
        timings.clear()
        t0 = time.perf_counter()
        p = s["profile"]  # search needs only the location; the full profile is for scoring
        results = search(p.work_location, p.city)
        for r in results:
            repo.save_result(r, agent_source)  # provenance: source_url, agent, retrieval time, raw text
        timings["search"] = round(time.perf_counter() - t0, 2)
        return {"search_results": results}

    def n_extract(s: AgentState):  # LLM #1: saved answers are reused, new pages are filtered and batched
        t0 = time.perf_counter()
        found, evidence, stats = extract_pages(
            s["search_results"], repo, extract_one=extract, extract_many=extract_many,
            prompt_version=prompt_version, model_id=model_id, cfg=rb.get("extraction", {}))
        timings["extract"] = round(time.perf_counter() - t0, 2)
        # Repeated ads become one property; evidence items are then matched to properties by name. No LLM.
        listings = attach_evidence(dedupe(found, rb.get("dedup", {}), rb.get("source_values", {})), evidence)
        stats["duplicates_merged"] = len(found) - len(listings)
        return {"listings": listings, "evidence": evidence, "extraction_stats": stats}

    def n_evaluate(s: AgentState):  # formulas.py + rules.py
        city = city_lookup(s["profile"].city)  # None -> labelled defaults
        medians = deposit_medians(s["listings"], rb)  # a missing deposit uses the median of this run's listings
        day = today()
        return {"candidates": [evaluate(s["profile"], l, rb, city, medians, day) for l in s["listings"]]}

    def n_decide(s: AgentState):  # rulebook
        return {"decision": decide(s["profile"], s["candidates"], rb)}

    def n_explain(s: AgentState):  # LLM #2
        t0 = time.perf_counter()
        suggestion = explain(s["profile"], s["decision"])
        timings["explain"] = round(time.perf_counter() - t0, 2)
        metrics = _run_metrics(s, timings)
        if run_log:  # one JSON line per run; holds counts and timings only, nothing about the user
            run_log.parent.mkdir(parents=True, exist_ok=True)
            with open(run_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(metrics) + "\n")
        return {"suggestion": suggestion, "run_metrics": metrics}

    g = StateGraph(AgentState)
    for name, fn in [("search", n_search), ("extract", n_extract), ("evaluate", n_evaluate),
                     ("decide", n_decide), ("explain", n_explain)]:
        g.add_node(name, fn)
    g.add_edge(START, "search")
    g.add_edge("search", "extract")
    g.add_edge("extract", "evaluate")
    g.add_edge("evaluate", "decide")
    g.add_edge("decide", "explain")
    g.add_edge("explain", END)
    return g.compile()
