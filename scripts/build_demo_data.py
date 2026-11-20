"""Build data/demo_extractions.json: the model's answers for the demo's saved Chennai search. Calls Gemini, so it needs --yes.

    .venv/bin/python scripts/build_demo_data.py [--max-calls N] [--yes]

Runs the app's own extraction step (prefilter, batches of the rulebook batch_size, fallback to smaller batches) on the
saved search in tavily_sample/tavily_live_chennai.json, then saves the answers with the prompt version and model used.
Without --yes it prints how many pages pass the filter and the least and most calls that could take, and stops. It refuses
to start if the most calls is over --max-calls (default 7), and a call counter stops the run if the cap is reached.
"""
import argparse
import json
import math
import sys
from datetime import datetime, timezone

from homefinder.agent.extraction import extract_pages
from homefinder.agent.prompts import extract_prompt_version
from homefinder.config.config import DEMO_DATA_PATH, DEMO_SEARCH_PATH, MODEL, load_rulebook, require_keys
from homefinder.core.prefilter import prefilter_reason
from homefinder.services.demo import demo_results
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import extract_document, extract_documents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=7)
    ap.add_argument("--yes", action="store_true", help="actually call Gemini")
    a = ap.parse_args()
    rb = load_rulebook()
    cfg = rb["extraction"]
    pages = demo_results(DEMO_SEARCH_PATH)
    keep = [p for p in pages if not prefilter_reason(p["url"], p["title"], p["content"], cfg.get("min_chars", 80),
                                                     cfg.get("blocked_domains", ()), cfg.get("evidence_domains", ()))]
    low = math.ceil(len(keep) / cfg["batch_size"])
    high = low + math.ceil(len(keep) / cfg["batch_size"]) * 2  # a bad batch splits and retries: a loose upper estimate
    print(f"{len(pages)} pages, {len(keep)} pass the filter: at least {low} and at most about {high} Gemini calls with {MODEL}.")
    if high > a.max_calls:
        sys.exit(f"Refusing to run: the worst case ({high} calls) is over the cap of {a.max_calls}. Raise --max-calls on purpose.")
    if not a.yes:
        print("Dry run. Add --yes to call Gemini.")
        return
    if missing := require_keys():
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    calls = {"n": 0}

    def counted(fn):
        def inner(*args, **kw):
            calls["n"] += 1
            if calls["n"] > a.max_calls:
                raise SystemExit(f"Stopped: more than {a.max_calls} Gemini calls.")
            return fn(*args, **kw)
        return inner

    repo, pv = DocumentRepo(":memory:"), extract_prompt_version()
    for p in pages:
        repo.save_result(p, "demo")
    _, _, stats = extract_pages(pages, repo, extract_one=counted(extract_document), extract_many=counted(extract_documents),
                                prompt_version=pv, model_id=MODEL, cfg=cfg)
    docs = [{"url": d.source_url, "answer": d.answer_json()} for d in repo.all() if d.extraction_status == "ok"]
    out = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prompt_version": pv, "model_id": MODEL,
           "source": DEMO_SEARCH_PATH.name, "pages": len(pages), "stats": stats, "documents": docs}
    DEMO_DATA_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"Wrote {DEMO_DATA_PATH}: {len(docs)} pages answered, {stats}, {calls['n']} Gemini calls.")


if __name__ == "__main__":
    main()
