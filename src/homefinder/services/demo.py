"""Public demo mode: the saved Chennai search plus saved model answers, so a run costs one explanation call.

No search call and no extraction call is ever made here: pages are read from disk, saved answers are loaded into an
in-memory store under the prompt version and model they were made with, and the extractors are replaced by functions that
refuse to run. Listing age is counted from the day the data was captured, so the demo does not drift into "re-verify".
"""
import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from homefinder.agent.graph import build_graph
from homefinder.config.config import DEMO_DATA_PATH, DEMO_SEARCH_PATH, DEMO_USAGE_PATH, load_rulebook
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import write_suggestion
from homefinder.services.search import _clean


class DemoDataMissing(RuntimeError):
    """data/demo_extractions.json has not been built yet (scripts/build_demo_data.py)."""


def demo_results(path: Path = DEMO_SEARCH_PATH, max_chars: int | None = None) -> list[dict]:
    max_chars = max_chars or load_rulebook()["search"]["max_chars"]
    return [_clean(r, max_chars) for r in json.loads(Path(path).read_text(encoding="utf-8")).get("results", [])]


def load_demo_data(path: Path = DEMO_DATA_PATH) -> dict:
    if not Path(path).exists():
        raise DemoDataMissing(f"{path} is missing. Build it once with scripts/build_demo_data.py --yes.")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _no_call(*args, **kwargs):
    raise RuntimeError("demo mode makes no extraction calls")


def demo_graph(data_path: Path = DEMO_DATA_PATH, search_path: Path = DEMO_SEARCH_PATH, explain=write_suggestion,
               run_log: Path | None = None, rulebook: dict | None = None):
    """The normal pipeline wired to saved data. Returns (graph, data) where data carries `captured_on`."""
    data = load_demo_data(data_path)
    captured = data["captured_at"]
    repo = DocumentRepo(":memory:")
    answers = {d["url"]: d["answer"] for d in data["documents"]}
    for r in demo_results(search_path):
        repo.save_result(r, "demo", now=captured)
        if r["url"] in answers:
            repo.save_extraction(r["url"], answers[r["url"]], data["prompt_version"], data["model_id"])
        else:  # filtered pages never reach this; a page the model could not read is a saved failure, never a new call
            repo.save_failure(r["url"], "not extracted when the demo data was built", data["prompt_version"], data["model_id"])
    day = date.fromisoformat(captured[:10])
    g = build_graph(search=lambda loc, city: demo_results(search_path), extract=_no_call, extract_many=_no_call,
                    explain=explain, rulebook=rulebook, repo=repo, prompt_version=data["prompt_version"],
                    model_id=data["model_id"], agent_source="demo", run_log=run_log, today=lambda: day)
    return g, data


class DailyLimiter:
    """Counts demo runs per UTC day across all visitors, in a small SQLite file. `try_run` is atomic."""

    def __init__(self, path: Path | str = DEMO_USAGE_PATH, per_day: int = 50):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.per_day, self._lock = per_day, threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, runs INTEGER NOT NULL)")

    @staticmethod
    def _day(now: datetime | None) -> str:
        return (now or datetime.now(timezone.utc)).date().isoformat()

    def used(self, now: datetime | None = None) -> int:
        row = self._db.execute("SELECT runs FROM usage WHERE day = ?", (self._day(now),)).fetchone()
        return row[0] if row else 0

    def try_run(self, now: datetime | None = None) -> bool:
        """Take one run if today's limit allows it. False = limit reached (nothing is counted)."""
        day = self._day(now)
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO usage (day, runs) VALUES (?, 0)", (day,))
            cur = self._db.execute("UPDATE usage SET runs = runs + 1 WHERE day = ? AND runs < ?", (day, self.per_day))
            return cur.rowcount == 1
