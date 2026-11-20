"""Phase 8.3: write the ranking snapshot to evals/golden/expected.json. Offline, no LLM calls.

    .venv/bin/python scripts/snapshot_ranking.py

Run it only on purpose, after a change to the rulebook or the scoring that you have read and accept; the diff of
expected.json is the review. tests/test_ranking_regression.py fails whenever the pipeline no longer matches the file.
"""
import json

from homefinder.evals.golden import GOLDEN_DIR
from homefinder.evals.ranking_eval import snapshot_all

NOTE = ("Ranking snapshot of the pipeline on the golden pages with the answer keys as extractor (scripts/snapshot_ranking.py). "
        "It is a regression guard: it says the ranking did not change, not that it is right. 'reviewed' stays false until a person has read it.")

if __name__ == "__main__":
    path = GOLDEN_DIR / "expected.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    data = {"note": NOTE, "reviewed": old.get("reviewed", False), "profiles": snapshot_all()}
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path}")
