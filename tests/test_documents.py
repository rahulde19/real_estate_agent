"""Phase 5: saved documents, saved extractions, re-extraction and failure markers. Offline, in-memory SQLite."""
import json

import pytest
from conftest import pg

from homefinder.agent import prompts
from homefinder.agent.graph import build_graph
from conftest import plain_rulebook
from homefinder.schemas import Listing
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import ExtractionInvalid

RESULTS = [{"title": f"T{i}", "url": f"https://x/{i}", "content": f"text {i}"} for i in range(3)]


class Extractor:
    """Counting stub: one PG listing per document, named after its URL."""
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def __call__(self, title, url, text):
        self.calls.append(url)
        if url in self.fail_on:
            raise ExtractionInvalid("bad json")
        return [pg(name=f"P-{url[-1]}", rent=8000 + int(url[-1]) * 100, private_room=True, url=url)]


def counts(out):
    """The three Phase 5 counters (stats also carries skipped and calls since Phase 6)."""
    return {k: out["extraction_stats"][k] for k in ("reused", "extracted", "failed")}


def run(profile, repo, extract, results=RESULTS, version="v1", model="m"):
    g = build_graph(search=lambda loc, city: results, extract=extract, explain=lambda p, d: "ok",
                    repo=repo, prompt_version=version, model_id=model, rulebook=plain_rulebook())
    return g.invoke({"profile": profile})


# ---- 5.1 repository ----
def test_create_read_update_rows():
    repo = DocumentRepo(":memory:")
    d = repo.save_result(RESULTS[0], "tavily:sample", now="2026-11-13T10:00:00+00:00")
    assert (d.source_url, d.agent_source, d.raw_text, d.extraction_status) == ("https://x/0", "tavily:sample", "text 0", "pending")
    assert d.first_seen_at == d.retrieved_at == "2026-11-13T10:00:00+00:00"
    assert repo.get("https://x/0").id == d.id and repo.get("https://nope") is None
    repo.save_extraction("https://x/0", [{"a": 1}], "v1", "m")
    got = repo.get("https://x/0")
    assert (got.extraction_status, got.prompt_version, got.model_id, got.listings_json()) == ("ok", "v1", "m", [{"a": 1}])


def test_db_file_is_created_on_disk(tmp_path):
    path = tmp_path / "sub" / "h.db"
    DocumentRepo(path).save_result(RESULTS[0], "tavily:sample")
    assert path.exists() and DocumentRepo(path).get("https://x/0") is not None  # persists across connections


# ---- 5.2 store search results ----
def test_rerun_does_not_duplicate_rows_and_keeps_first_seen(profile):
    repo = DocumentRepo(":memory:")
    run(profile, repo, Extractor())
    first = {d.source_url: d.first_seen_at for d in repo.all()}
    run(profile, repo, Extractor())
    assert len(repo.all()) == 3
    assert {d.source_url: d.first_seen_at for d in repo.all()} == first
    assert all(d.agent_source.startswith("tavily:") for d in repo.all())


def test_changed_text_resets_saved_extraction():
    repo = DocumentRepo(":memory:")
    repo.save_result(RESULTS[0], "tavily:live")
    repo.save_extraction("https://x/0", [], "v1", "m")
    repo.save_result({**RESULTS[0], "content": "new text"}, "tavily:live")
    d = repo.get("https://x/0")
    assert d.raw_text == "new text" and d.extraction_status == "pending" and d.extraction_json is None


# ---- 5.3 save and reuse ----
def test_second_run_makes_zero_extraction_calls_with_same_listings(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor()
    out1 = run(profile, repo, ex)
    assert len(ex.calls) == 3 and counts(out1) == {"reused": 0, "extracted": 3, "failed": 0}
    out2 = run(profile, repo, ex)
    assert len(ex.calls) == 3  # no new calls
    assert counts(out2) == {"reused": 3, "extracted": 0, "failed": 0}
    skip = {"provenance", "observations"}  # retrieval time is refreshed on every run
    assert [l.model_dump(exclude=skip) for l in out2["listings"]] == [l.model_dump(exclude=skip) for l in out1["listings"]]
    assert all(isinstance(l, Listing) for l in out2["listings"])


def test_extractor_receives_only_document_text(profile):
    seen = []
    run(profile, DocumentRepo(":memory:"), lambda title, url, text: seen.append((title, url, text)) or [])
    assert seen[0] == ("T0", "https://x/0", "text 0")  # nothing about the user, city or work location


def test_prompt_version_hash_tracks_prompt_text(monkeypatch):
    v = prompts.extract_prompt_version()
    assert v == prompts.extract_prompt_version() and len(v) == 10
    monkeypatch.setattr(prompts, "EXTRACT_SYSTEM", prompts.EXTRACT_SYSTEM + " Extra rule.")
    assert prompts.extract_prompt_version() != v


# ---- 5.4 re-extraction and failures ----
def test_version_bump_reextracts_only_what_is_needed(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor()
    run(profile, repo, ex, results=RESULTS[:2], version="v1")
    assert len(ex.calls) == 2
    run(profile, repo, ex, results=RESULTS, version="v2")  # 2 stale + 1 new
    assert len(ex.calls) == 2 + 3
    run(profile, repo, ex, results=RESULTS, version="v2")
    assert len(ex.calls) == 5  # all current now


def test_only_stale_rows_are_reextracted(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor()
    run(profile, repo, ex, results=RESULTS[:1], version="v1")
    run(profile, repo, ex, results=RESULTS[1:], version="v2")  # docs 1,2 at v2; doc 0 stays v1 and is not searched
    n = len(ex.calls)
    run(profile, repo, ex, results=RESULTS[1:], version="v2")
    assert len(ex.calls) == n  # fresh rows untouched


def test_model_change_reextracts(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor()
    run(profile, repo, ex, model="m1")
    run(profile, repo, ex, model="m2")
    assert len(ex.calls) == 6


def test_changed_document_text_reextracts_only_that_document(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor()
    run(profile, repo, ex)
    changed = [RESULTS[0], {**RESULTS[1], "content": "updated"}, RESULTS[2]]
    run(profile, repo, ex, results=changed)
    assert ex.calls[3:] == ["https://x/1"]


def test_invalid_output_is_recorded_not_retried_and_does_not_crash(profile):
    repo, ex = DocumentRepo(":memory:"), Extractor(fail_on={"https://x/1"})
    out = run(profile, repo, ex)
    assert counts(out) == {"reused": 0, "extracted": 2, "failed": 1} and len(out["listings"]) == 2
    bad = repo.get("https://x/1")
    assert bad.extraction_status == "failed" and "bad json" in bad.failure
    out2 = run(profile, repo, ex)  # same version and model: not retried
    assert len(ex.calls) == 3 and out2["extraction_stats"]["failed"] == 1
    ex.fail_on.clear()
    out3 = run(profile, repo, ex, version="v2")  # a bump retries it (and re-extracts the rest)
    assert repo.get("https://x/1").extraction_status == "ok" and out3["extraction_stats"]["failed"] == 0


def test_transient_errors_are_not_recorded_as_failures(profile):
    repo = DocumentRepo(":memory:")

    def boom(title, url, text):
        raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        run(profile, repo, boom)
    assert all(d.extraction_status == "pending" for d in repo.all())
