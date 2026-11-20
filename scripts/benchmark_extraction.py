"""Phase 6 benchmark: what each extraction option costs and whether it loses listings. Offline, no network, no LLM.

Stand-in for the LLM: the hand-made answer key. The stub "model" returns the key's listings for a page, but only
those whose rent still appears in the text it was given, so cutting text can lose listings. It is a perfect reader:
this measures calls, tokens (estimated as characters / 4) and which listings survive each option. It does NOT measure
how well Gemini reads a batched prompt. Batching rows are protected by the fallbacks in agent/extraction.py instead.

Run:  .venv/bin/python scripts/benchmark_extraction.py   (writes docs/benchmarks/phase6_results.md)
"""
import json
import math
from datetime import date
from pathlib import Path

from homefinder.agent.extraction import extract_pages, rent_supported
from homefinder.config.config import ROOT, load_rulebook
from homefinder.evals.golden import load_profiles
from homefinder.core.rules import decide, evaluate
from homefinder.schemas import Listing, UserProfile
from homefinder.services.city_costs import lookup_city_costs
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import batch_prompt, batch_system, single_system

SAMPLES = ROOT / "tavily_sample"
OUT = ROOT / "docs" / "benchmarks" / "phase6_results.md"
TOKENS_PER_LISTING, OUT_OVERHEAD = 105, 30  # measured from one Listing's JSON; fixed JSON wrapper
RB = load_rulebook()
CFG = RB["extraction"]

DATASETS = {
    "Live capture, Chennai (22 pages)": ("tavily_live_chennai.json", "tavily_live_chennai_gold.json"),
    "Default sample (6 pages)": ("tavily_ans.json", "tavily_ans_gold.json"),
}


def profiles() -> dict[str, UserProfile]:
    """The first three golden profiles (evals/golden/profiles.json), under the labels this script has always used."""
    golden = load_profiles()
    return {"solo, tight cash": golden["solo_tight_cash"], "3 friends sharing": golden["three_friends_sharing"],
            "family of 4": golden["family_of_four"]}


def load(dataset):
    src, gold = DATASETS[dataset]
    results = json.loads((SAMPLES / src).read_text(encoding="utf-8"))["results"]
    pages = json.loads((SAMPLES / gold).read_text(encoding="utf-8"))["pages"]
    return results, pages


def gold_listing(d: dict, url: str) -> Listing:
    return Listing(**{k: v for k, v in d.items() if k != "judgment"}, url=url)


def run_config(dataset, *, prefilter=False, batch_size=1, results_per_query=8, max_chars=1200):
    results, gold = load(dataset)
    # results_per_query: the capture lost which query found which page, so approximate by keeping the best-scored 3 x N pages
    keep = {r["url"] for r in sorted(results, key=lambda r: -(r.get("score") or 0))[: 3 * results_per_query]}
    rs = [{"title": r["title"], "url": r["url"], "content": r["content"][:max_chars]} for r in results if r["url"] in keep]
    repo = DocumentRepo(":memory:")
    for r in rs:
        repo.save_result(r, "benchmark")
    tok = {"in": 0, "out": 0}

    def answer(url, text):
        return [gold_listing(l, url) for l in gold[url]["listings"] if rent_supported(l["rent"], text)]

    def one(title, url, text):
        found = answer(url, text)
        tok["in"] += (len(single_system()) + len(f"Title: {title}\nURL: {url}\n\n{text}")) // 4
        tok["out"] += OUT_OVERHEAD + TOKENS_PER_LISTING * len(found)
        return found

    def many(docs):
        tok["in"] += (len(batch_system()) + len(batch_prompt(docs))) // 4
        out = {i: answer(url, text) for i, _, url, text in docs}
        tok["out"] += OUT_OVERHEAD * len(docs) + TOKENS_PER_LISTING * sum(len(v) for v in out.values())
        return out

    cfg = {**CFG, "prefilter": prefilter, "batch_size": batch_size}
    listings, _, stats = extract_pages(rs, repo, extract_one=one, extract_many=many, prompt_version="b", model_id="b", cfg=cfg)
    total_gold = sum(len(g["listings"]) for g in gold.values())
    return {"pages": len(rs), "sent": len(rs) - stats["skipped"], "calls": stats["calls"], "tok_in": tok["in"],
            "tok_out": tok["out"], "found": len(listings), "lost": total_gold - len(listings), "listings": listings}


def top7(listings, profile):
    city = lookup_city_costs(profile.city)
    cands = [evaluate(profile, l, RB, city) for l in listings]
    return [c.listing.name for c in decide(profile, cands, RB).ranked]


def overlap(listings, baseline, profile):
    base = top7(baseline, profile)
    if not base:
        return "n/a"
    return f"{len(set(base) & set(top7(listings, profile)))}/{len(base)}"


CONFIGS = [  # label, kwargs
    ("Baseline (today)", {}),
    ("Pre-filter only", {"prefilter": True}),
    ("Batch 3", {"batch_size": 3}),
    ("Batch 5", {"batch_size": 5}),
    ("Batch 8", {"batch_size": 8}),
    ("Filter + batch 3", {"prefilter": True, "batch_size": 3}),
    ("Filter + batch 5", {"prefilter": True, "batch_size": 5}),
    ("Filter + batch 8", {"prefilter": True, "batch_size": 8}),
    ("6 results per query", {"results_per_query": 6}),
    ("5 results per query", {"results_per_query": 5}),
    ("3 results per query", {"results_per_query": 3}),
    ("Text limit 800", {"max_chars": 800}),
    ("Text limit 600", {"max_chars": 600}),
    ("Text limit 300", {"max_chars": 300}),
    ("Filter + batch 5 + 5 results + 600 chars", {"prefilter": True, "batch_size": 5, "results_per_query": 5, "max_chars": 600}),
]


def table(dataset) -> str:
    base = run_config(dataset)
    profs = profiles()
    head = ("| Option | Pages sent | LLM calls | Est. input tokens | Est. output tokens | Listings found | Answer-key listings lost | "
            + " | ".join(f"Top-7 kept: {n}" for n in profs) + " |")
    lines = [head, "|" + "---|" * (7 + len(profs))]
    for label, kw in CONFIGS:
        r = run_config(dataset, **kw)
        ov = " | ".join(overlap(r["listings"], base["listings"], p) for p in profs.values())
        lines.append(f"| {label} | {r['sent']} of {r['pages']} | {r['calls']} | {r['tok_in']:,} | {r['tok_out']:,} | "
                     f"{r['found']} | {r['lost']} | {ov} |")
    return "\n".join(lines)


def main():
    parts = ["# Phase 6 results (generated)", "",
             "Generated by `scripts/benchmark_extraction.py`. Do not edit; see `phase6_extraction.md` for method and findings.", ""]
    for ds in DATASETS:
        parts += [f"## {ds}", "", table(ds), ""]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print("\n".join(parts))


if __name__ == "__main__":
    main()
