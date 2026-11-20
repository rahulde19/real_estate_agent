"""Phase 8.2: score the real extraction model against the golden set. Calls Gemini, so it only runs with --yes.

    .venv/bin/python scripts/eval_extraction.py --mode single|batch|both [--limit N | --quick] [--max-calls N] [--yes]

Safety cap: the script refuses to run if it would make more than --max-calls Gemini calls (default 7), even with --yes.
Raise the cap on purpose for a full run, e.g. --mode both --max-calls 51.

Baseline within the 7-call limit: all 42 pages in batch mode with --batch-size 6 (42 / 6 = 7 calls):
    .venv/bin/python scripts/eval_extraction.py --mode batch --batch-size 6 --yes
This tests the batching the app uses, on the whole golden set. Batch size 6 is above the rulebook's 5 and the eval
sends batches directly, so the app's batch_max_chars (6000) is not applied: the batch size used is printed and saved.

--quick runs 7 hand-picked pages in single mode: exactly 7 Gemini calls. The pages cover a price range, two flats on one
page, a flat with every cost field, a two-opinion review, a forum complaint, a contradictory price and an off-topic page.
Seven pages is a smoke test, not a measurement: one miss moves a rate by several points, so do not set thresholds from it.

Without --yes it prints how many calls it would make and stops. With --yes it calls the extractor directly on every
page (the prefilter is bypassed so review pages are measured too), scores the answers with
homefinder.evals.extraction_eval, and writes docs/evals/extraction_<mode>.json and a summary in docs/evals/extraction.md.
Batch mode sends pages in batches of the rulebook batch_size with no fallback, so it measures raw batch quality; invalid batches count as invalid
for every page in them.
"""
import argparse
import json
import math
import sys
from datetime import date

from homefinder.agent.prompts import extract_prompt_version
from homefinder.config.config import MODEL, ROOT, load_rulebook, require_keys
from homefinder.evals.extraction_eval import aggregate, pct, score_page
from homefinder.evals.golden import load_pages
from homefinder.services.llm import ExtractionInvalid, extract_document, extract_documents
from homefinder.services.metrics import METER

OUT = ROOT / "docs" / "evals"
MAX_CALLS = 7  # default cap on Gemini calls per run
BATCH = load_rulebook()["extraction"]["batch_size"]  # the app's own batch size, so the eval measures what ships
QUICK_PAGES = ["g01", "g17", "s02", "s07", "s09", "s13", "g25"]  # 7 pages = 7 single-mode calls
# Proposed pass thresholds. They become real only after the first baseline run is reviewed (roadmap 8.2).
PROPOSED = {"recall": 0.85, "precision": 0.85, "schema_valid_rate": 0.98, "invented_rent_rate": 0.02,
            "source_type_accuracy": 0.85}


def run_single(pages):
    answers = {}
    for p in pages:
        try:
            answers[p["id"]] = extract_document(p["title"], p["url"], p["content"])
        except ExtractionInvalid:
            answers[p["id"]] = None
    return answers


def run_batch(pages, size=BATCH):
    answers = {}
    for i in range(0, len(pages), size):
        group = pages[i:i + size]
        try:
            out = extract_documents([(j, p["title"], p["url"], p["content"]) for j, p in enumerate(group)])
        except ExtractionInvalid:
            out = {}
        for j, p in enumerate(group):
            answers[p["id"]] = out.get(j)  # None = invalid batch or page missing from the answer
    return answers


def evaluate(mode, pages, size=BATCH):
    METER.reset()
    answers = run_single(pages) if mode == "single" else run_batch(pages, size)
    m = METER.snapshot()
    result = aggregate([score_page(p, answers[p["id"]]) for p in pages])
    # What the model actually returned per page (None = invalid), so failing pages can be read later without new calls.
    result["answers"] = {i: (a.model_dump(mode="json", exclude_none=True) if a else None) for i, a in answers.items()}
    result.update({"mode": mode, "model": MODEL, "batch_size": size if mode == "batch" else 1, "prompt_version": extract_prompt_version(), "date": date.today().isoformat(),
                   "calls": m["calls"]["extract"], "tokens_in": m["tokens_in"]["extract"], "tokens_out": m["tokens_out"]["extract"]})
    return result


def summary(r):
    ev, f = r["evidence"], r["field_accuracy"]
    rows = [("Listing recall", pct(r["recall"]), pct(PROPOSED["recall"])), ("Listing precision", pct(r["precision"]), pct(PROPOSED["precision"])),
            ("Schema-valid pages", pct(r["schema_valid_rate"]), pct(PROPOSED["schema_valid_rate"])),
            ("Invented rents", pct(r["invented_rent_rate"]), f"<= {pct(PROPOSED['invented_rent_rate'])}"),
            ("source_type accuracy", pct(r["source_type_accuracy"]), pct(PROPOSED["source_type_accuracy"])),
            ("Evidence precision / recall", f"{pct(ev['precision'])} / {pct(ev['recall'])}", "none yet"),
            ("Confidence agreement (keys that state one)", pct(r["confidence_agreement"]), "none yet")]
    lines = [f"### {r['mode']} ({r['date']}, model `{r['model']}`, prompt `{r['prompt_version']}`)", "",
             f"{r['pages']} pages, {r['calls']} extraction calls (batch size {r['batch_size']}), {r['tokens_in']:,} tokens in / {r['tokens_out']:,} out.", "",
             "| Measure | Result | Proposed threshold |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    lines += ["", "Field accuracy (matched listings, where the key states the field): "
              + ", ".join(f"{k} {pct(v['rate'])} ({v['correct']}/{v['scored']})" for k, v in f.items()), "",
              f"Pages with a missed or extra listing, a missed or extra evidence item, a wrong source_type or invalid output: {', '.join(r['pages_with_misses']) or 'none'}", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["single", "batch", "both"], default="both")
    ap.add_argument("--limit", type=int, help="only the first N pages (cheaper trial)")
    ap.add_argument("--quick", action="store_true", help="7 hand-picked pages, single mode: exactly 7 calls (smoke test)")
    ap.add_argument("--batch-size", type=int, default=BATCH, help="pages per call in batch mode (default: rulebook batch_size); 6 makes all 42 pages 7 calls")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS, help="refuse to run if it would make more calls than this (default 7)")
    ap.add_argument("--yes", action="store_true", help="actually call Gemini")
    a = ap.parse_args()
    pages = load_pages()
    if a.quick:
        pages, a.mode = [p for p in pages if p["id"] in QUICK_PAGES], "single"
    elif a.limit:
        pages = pages[: a.limit]
    modes = ["single", "batch"] if a.mode == "both" else [a.mode]
    calls = sum(len(pages) if m == "single" else math.ceil(len(pages) / a.batch_size) for m in modes)
    print(f"{len(pages)} pages, modes {modes}: about {calls} Gemini calls with model {MODEL}.")
    if calls > a.max_calls:
        sys.exit(f"Refusing to run: {calls} calls is over the cap of {a.max_calls}. Use --quick (7 calls), a smaller "
                 f"--limit or --mode, or raise the cap on purpose with --max-calls {calls}.")
    if not a.yes:
        print("Dry run. Add --yes to call Gemini.")
        return
    if missing := require_keys():
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    OUT.mkdir(parents=True, exist_ok=True)
    tag = "_quick" if a.quick else (f"_b{a.batch_size}" if a.batch_size != BATCH else "")
    md = [f"# Extraction evaluation against the golden set", "",
          "Generated by `scripts/eval_extraction.py`. Keys are Claude's judgment; only the pages in `evals/golden/review.md` "
          "are candidates for user review. Thresholds below are proposals until a baseline is accepted.", ""]
    for m in modes:
        r = evaluate(m, pages, a.batch_size)
        (OUT / f"extraction_{m}{tag}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")  # includes per-page answers
        md.append(summary(r))
        print(summary(r))
    (OUT / f"extraction{tag}.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
