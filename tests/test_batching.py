"""Phase 6.3 + 6.1: batching with fallbacks, per-URL cache and failure markers, and the usage meter. Offline stubs."""
import json
import math

import pytest
from conftest import pg
from langchain_core.messages import AIMessage

from homefinder.agent.extraction import extract_pages, looks_mixed_up, make_batches, numbers_in, rent_supported
from homefinder.agent.graph import build_graph
from homefinder.config.config import load_rulebook
from homefinder.services import llm
from homefinder.services.documents import DocumentRepo
from homefinder.services.llm import ExtractionInvalid
from homefinder.services.metrics import METER


def results(n):
    return [{"title": f"T{i}", "url": f"https://x/{i}", "content": f"PG number {i} costs Rs {8000 + i * 100} per month"}
            for i in range(n)]


def rent_of(url):
    return 8000 + int(url.rsplit("/", 1)[1]) * 100


class Stubs:
    """Counting stand-ins for the one-page and several-pages extractors (the 'perfect model')."""
    def __init__(self, many_fails_over=None, drop=(), swap=(), single_fails=()):
        self.single_calls, self.many_calls = [], []
        self.many_fails_over, self.drop, self.swap, self.single_fails = many_fails_over, set(drop), dict(swap), set(single_fails)

    def one(self, title, url, text):
        self.single_calls.append(url)
        if url in self.single_fails:
            raise ExtractionInvalid("bad json")
        return [pg(name=f"P-{url[-1]}", rent=rent_of(url), url=url)]

    def many(self, docs):
        self.many_calls.append([d[0] for d in docs])
        if self.many_fails_over is not None and len(docs) > self.many_fails_over:
            raise ExtractionInvalid("cut off")
        out = {}
        for i, _, url, _ in docs:
            if url in self.drop:
                continue
            target = self.swap.get(url, url)  # a mixed-up answer carries another page's rent
            out[i] = [pg(name=f"P-{url[-1]}", rent=rent_of(target), url=url)]
        return out


def run(n, stubs, repo=None, **cfg):
    repo = repo or DocumentRepo(":memory:")
    rs = results(n)
    for r in rs:
        repo.save_result(r, "tavily:sample")
    cfg = {"batch_size": 3, "batch_max_chars": 6000, **cfg}
    found, _, stats = extract_pages(rs, repo, extract_one=stubs.one, extract_many=stubs.many,
                                 prompt_version="v1", model_id="m", cfg=cfg)
    return repo, found, stats


# ---- number checks ----
def test_number_parsing_and_rent_support():
    assert numbers_in("Rs 9,000 and 15k and 1.7 Lac") >= {9000.0, 15000.0, 170000.0}
    assert rent_supported(9000, "costs Rs 9,000 per month") and not rent_supported(9100, "costs Rs 9,000 per month")
    assert looks_mixed_up("rent Rs 8000", ["rent Rs 8100"], [pg(rent=8100)])
    assert not looks_mixed_up("rent Rs 8000", ["rent Rs 8100"], [pg(rent=8000)])
    assert not looks_mixed_up("rent Rs 8000", ["rent Rs 8100"], [pg(rent=7777)])  # unsupported anywhere: leave it


def test_make_batches_respects_size_and_chars():
    repo = DocumentRepo(":memory:")
    docs = [repo.save_result({"title": "t", "url": f"u{i}", "content": "x" * 100}, "s") for i in range(7)]
    assert [len(b) for b in make_batches(docs, 3, 10_000)] == [3, 3, 1]
    assert [len(b) for b in make_batches(docs, 5, 250)] == [2] * 3 + [1]  # 250 chars fits two 100-char pages


# ---- 6.3 batching ----
@pytest.mark.parametrize("n,k", [(7, 3), (6, 3), (10, 5), (4, 4)])
def test_n_pages_make_about_n_over_k_calls(n, k):
    stubs = Stubs()
    _, found, stats = run(n, stubs, batch_size=k)
    assert stats["calls"] == math.ceil(n / k) and len(found) == n and stats["extracted"] == n
    assert sorted(l.url for l in found) == sorted(r["url"] for r in results(n))  # one listing per page, right URL


def test_batch_size_one_uses_only_the_one_page_extractor():
    stubs = Stubs()
    _, found, stats = run(4, stubs, batch_size=1)
    assert stubs.many_calls == [] and len(stubs.single_calls) == 4 and stats["calls"] == 4


def test_cached_pages_are_never_resent_and_batch_size_does_not_invalidate_cache():
    repo, stubs = DocumentRepo(":memory:"), Stubs()
    run(6, stubs, repo, batch_size=3)
    again = Stubs()
    _, found, stats = run(6, again, repo, batch_size=1)  # different batch size, same prompt version
    assert again.single_calls == [] and again.many_calls == [] and stats["calls"] == 0 and stats["reused"] == 6
    more = Stubs()  # two new pages: only they are sent
    repo.save_result(results(8)[6], "s"); repo.save_result(results(8)[7], "s")
    extract_pages(results(8), repo, extract_one=more.one, extract_many=more.many, prompt_version="v1", model_id="m",
                  cfg={"batch_size": 3})
    assert [len(c) for c in more.many_calls] == [2] and more.single_calls == []


def test_bad_batch_falls_back_to_smaller_batches_then_single_pages():
    stubs = Stubs(many_fails_over=1)  # any batch of 2+ pages comes back invalid
    _, found, stats = run(4, stubs, batch_size=4)
    assert len(found) == 4 and stats["extracted"] == 4 and stats["failed"] == 0
    assert [len(c) for c in stubs.many_calls] == [4, 2, 2]  # 4 -> 2+2 -> singles via the one-page extractor
    assert len(stubs.single_calls) == 4 and stats["calls"] == 7


def test_missing_page_in_answer_is_resent():
    stubs = Stubs(drop={"https://x/1"})
    _, found, stats = run(3, stubs, batch_size=3)
    assert len(found) == 3 and stats["extracted"] == 3
    assert stubs.single_calls == ["https://x/1"]  # re-sent on its own


def test_mixed_up_rent_triggers_single_reextract():
    stubs = Stubs(swap={"https://x/0": "https://x/1"})  # page 0's answer carries page 1's rent
    _, found, _ = run(3, stubs, batch_size=3)
    assert stubs.single_calls == ["https://x/0"]
    assert {l.url: l.rent for l in found}["https://x/0"] == 8000  # corrected by the one-page extractor


def test_failure_markers_are_per_url():
    stubs = Stubs(many_fails_over=1, single_fails={"https://x/2"})
    repo, found, stats = run(4, stubs, batch_size=4)
    assert stats["failed"] == 1 and stats["extracted"] == 3 and len(found) == 3
    assert [d.source_url for d in repo.all() if d.extraction_status == "failed"] == ["https://x/2"]
    again = Stubs()  # a second run: failed page is not retried, others are reused
    _, _, stats2 = run(4, again, repo, batch_size=4)
    assert stats2["calls"] == 0 and stats2["failed"] == 1 and stats2["reused"] == 3


def test_transient_errors_propagate_from_batches():
    def boom(docs):
        raise ConnectionError("network down")
    repo = DocumentRepo(":memory:")
    rs = results(3)
    for r in rs:
        repo.save_result(r, "s")
    with pytest.raises(ConnectionError):
        extract_pages(rs, repo, extract_one=Stubs().one, extract_many=boom, prompt_version="v1", model_id="m", cfg={"batch_size": 3})
    assert all(d.extraction_status == "pending" for d in repo.all())


# ---- the real batch call, with a fake model ----
class FakeLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, msgs):
        return AIMessage(content=self.content, usage_metadata={"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540})


def docs(n=2):
    return [(i, f"T{i}", f"https://x/{i}", f"text {i}") for i in range(n)]


def listing(name="A", rent=9000, **kw):
    return {"name": name, "kind": "pg", "rent": rent, **kw}


def test_extract_documents_assigns_urls_from_doc_ids(monkeypatch):
    answer = {"documents": [{"doc_id": 1, "listings": [listing("B", url="https://evil/other")]},
                            {"doc_id": 0, "listings": [listing("A", commute_minutes=25)]},
                            {"doc_id": 9, "listings": [listing("ghost")]},          # unknown id: ignored
                            {"doc_id": 0, "listings": [listing("dupe")]}]}          # repeated id: first wins
    monkeypatch.setattr(llm, "_llm", lambda json_mode=False, max_tokens=3000: FakeLLM(json.dumps(answer)))
    out = llm.extract_documents(docs())
    assert set(out) == {0, 1}
    a = out[0].listings[0]
    assert a.name == "A" and a.url == "https://x/0" and a.commute_minutes is None
    assert out[1].listings[0].url == "https://x/1"  # code decides the URL, not the model


def test_extract_documents_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(llm, "_llm", lambda json_mode=False, max_tokens=3000: FakeLLM("not json at all"))
    with pytest.raises(ExtractionInvalid):
        llm.extract_documents(docs())


def test_batch_prompt_and_output_budget(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "_llm", lambda json_mode=False, max_tokens=3000: seen.update(json=json_mode, max=max_tokens) or FakeLLM('{"documents": []}'))
    llm.extract_documents(docs(5))
    assert seen["json"] is True and seen["max"] == 6000  # grows with the batch, so answers are not cut off
    text = llm.batch_prompt(docs(2))
    assert "=== DOC 0 ===" in text and "=== DOC 1 ===" in text and "https://x/1" in text
    assert "documents" in llm.batch_system() and "never combine facts" in llm.batch_system()


# ---- 6.1 meter and run metrics ----
def test_meter_counts_calls_and_tokens(monkeypatch):
    METER.reset()
    monkeypatch.setattr(llm, "_llm", lambda json_mode=False, max_tokens=3000: FakeLLM('{"documents": []}'))
    llm.extract_documents(docs())
    llm.extract_documents(docs())
    snap = METER.snapshot()
    assert snap["calls"]["extract"] == 2 and snap["tokens_in"]["extract"] == 2400 and snap["tokens_out"]["extract"] == 680
    METER.reset()
    assert METER.snapshot()["calls"]["extract"] == 0


def test_graph_records_run_metrics_and_log_without_personal_data(profile, tmp_path):
    log = tmp_path / "logs" / "run_log.jsonl"
    stubs = Stubs()
    rb = {**load_rulebook(), "extraction": {**load_rulebook()["extraction"], "prefilter": False, "batch_size": 3}}
    g = build_graph(search=lambda loc, city: results(5), extract=stubs.one, extract_many=stubs.many, explain=lambda p, d: "ok",
                    rulebook=rb, repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="m",
                    city_lookup=lambda c: None, run_log=log)
    out = g.invoke({"profile": profile})
    m = out["run_metrics"]
    assert m["pages"] == 5 and m["extract_requests"] == 2 and m["extracted"] == 5 and m["listings"] == 5
    assert set(m["seconds"]) == {"search", "extract", "explain"} and m["city"] == "Chennai"
    line = log.read_text()
    assert json.loads(line)["pages"] == 5
    assert profile.work_location not in line and str(profile.budget) not in line  # counts and timings only
