"""Phase 9.2: demo mode makes no search or extraction call, and its run limits hold. Offline."""
import json
from datetime import datetime, timezone

import pytest

from homefinder.config.config import DEMO_SEARCH_PATH
from homefinder.evals.golden import gold_answer, load_pages, load_profiles
from homefinder.services.demo import DailyLimiter, DemoDataMissing, demo_graph, demo_results


@pytest.fixture
def data_file(tmp_path):
    """Demo data built from the golden keys instead of a model, for the pages of the live capture."""
    urls = {r["url"] for r in demo_results(DEMO_SEARCH_PATH)}
    docs = [{"url": p["url"], "answer": gold_answer(p).model_dump(mode="json")} for p in load_pages() if p["url"] in urls]
    path = tmp_path / "demo.json"
    path.write_text(json.dumps({"captured_at": "2026-11-21T10:00:00+00:00", "prompt_version": "pv-test", "model_id": "m-test",
                                "documents": docs}), encoding="utf-8")
    return path


def test_the_demo_runs_the_whole_pipeline_without_a_search_or_extraction_call(data_file, monkeypatch):
    import homefinder.services.search as search
    monkeypatch.setattr(search, "search_web", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))
    g, data = demo_graph(data_path=data_file, explain=lambda p, d: "explained")
    out = g.invoke({"profile": load_profiles()["solo_comfortable"]})
    assert out["suggestion"] == "explained" and data["model_id"] == "m-test"
    assert out["extraction_stats"]["calls"] == 0 and out["extraction_stats"]["extracted"] == 0
    assert out["extraction_stats"]["reused"] > 0 and out["decision"].ranked
    assert all(c.age_days == 0 and not c.reverify for c in out["candidates"])  # age counts from the capture day, not today


def test_a_page_with_no_saved_answer_is_a_failure_not_a_new_call(data_file):
    data = json.loads(data_file.read_text())
    data["documents"] = data["documents"][:1]  # only one page answered
    data_file.write_text(json.dumps(data))
    g, _ = demo_graph(data_path=data_file, explain=lambda p, d: "ok")
    out = g.invoke({"profile": load_profiles()["solo_comfortable"]})
    assert out["extraction_stats"]["calls"] == 0 and out["extraction_stats"]["failed"] > 0


def test_missing_demo_data_is_a_clear_error(tmp_path):
    with pytest.raises(DemoDataMissing, match="build_demo_data"):
        demo_graph(data_path=tmp_path / "nope.json")


def test_daily_limit_counts_per_utc_day_and_stops_at_the_limit():
    lim = DailyLimiter(":memory:", per_day=3)
    mon = datetime(2026, 11, 23, 23, 59, tzinfo=timezone.utc)
    tue = datetime(2026, 11, 24, 0, 1, tzinfo=timezone.utc)
    assert [lim.try_run(mon) for _ in range(5)] == [True, True, True, False, False]
    assert lim.used(mon) == 3  # refused runs are not counted
    assert lim.try_run(tue) and lim.used(tue) == 1


def test_the_build_script_dry_run_makes_no_call(monkeypatch, capsys):
    import importlib.util, sys
    from homefinder.config.config import ROOT
    spec = importlib.util.spec_from_file_location("build_demo_data", ROOT / "scripts" / "build_demo_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("called the model"))
    monkeypatch.setattr(mod, "extract_document", boom)
    monkeypatch.setattr(mod, "extract_documents", boom)
    monkeypatch.setattr(sys, "argv", ["build_demo_data.py"])
    mod.main()
    out = capsys.readouterr().out
    assert "22 pages" in out and "Dry run" in out
