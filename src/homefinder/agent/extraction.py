"""Extraction orchestration (Phase 5 cache + Phase 6 cost controls).

For each search result: prefilter (optional) -> reuse the saved extraction if current -> otherwise extract,
one page per call or several pages per call, and save the answer per page URL.

Batching is built so the worst case is today's behaviour: any batch that fails or looks wrong is split
down to single pages, and single pages use the unchanged one-page extractor.
"""
import re

from homefinder.core.prefilter import prefilter_reason
from homefinder.core.formulas import missing_fields
from homefinder.schemas import EvidenceItem, Listing, ListingBatch, Provenance
from homefinder.services.documents import Document, DocumentRepo
from homefinder.services.llm import ExtractionInvalid

_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|lacs?|lakhs?)?\b", re.IGNORECASE)


def numbers_in(text: str) -> set[float]:
    """Every number in the text, with 15k and 1.7 Lac expanded (used to check a rent really appears in a page)."""
    out = set()
    for m in _NUM.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        v *= 1000 if unit == "k" else 100000 if unit.startswith("l") else 1
        out.add(round(v, 2))
    return out


def rent_supported(rent: float, text: str) -> bool:
    return any(abs(n - rent) <= 1 for n in numbers_in(text))


def looks_mixed_up(own_text: str, other_texts: list[str], listings: list[Listing]) -> bool:
    """A rent that is not in this page but is in another page of the same batch means the model mixed pages up."""
    return any(not rent_supported(l.rent, own_text) and any(rent_supported(l.rent, t) for t in other_texts)
               for l in listings)


def _as_answer(x) -> ListingBatch:
    """Extractors return a page answer; a bare list of listings is also accepted."""
    return x if isinstance(x, ListingBatch) else ListingBatch(listings=list(x))


def make_batches(docs: list[Document], batch_size: int, max_chars: int) -> list[list[Document]]:
    batches: list[list[Document]] = []
    for doc in docs:
        cur = batches[-1] if batches else None
        if cur and len(cur) < batch_size and sum(len(d.raw_text) for d in cur) + len(doc.raw_text) <= max_chars:
            cur.append(doc)
        else:
            batches.append([doc])
    return batches


def extract_pages(results: list[dict], repo: DocumentRepo, *, extract_one, extract_many=None,
                  prompt_version: str, model_id: str,
                  cfg: dict | None = None) -> tuple[list[Listing], list[EvidenceItem], dict]:
    """Returns (listings in result order, evidence items, stats).
    stats: reused, extracted, failed, skipped, calls (extraction requests).
    Each listing carries its provenance (from the saved document) and its missing-fields list, both set here by code."""
    cfg = cfg or {}
    stats = {"reused": 0, "extracted": 0, "failed": 0, "skipped": 0, "calls": 0}
    found: dict[str, ListingBatch] = {}
    todo: list[Document] = []

    for r in results:
        doc = repo.get(r["url"])
        reason = None
        if cfg.get("prefilter"):
            reason = prefilter_reason(doc.source_url, doc.title or "", doc.raw_text,
                                      cfg.get("min_chars", 80), cfg.get("blocked_domains", ()),
                                      cfg.get("evidence_domains", ()))
        if reason:
            if doc.extraction_status in ("pending", "skipped"):  # never overwrite a saved extraction
                repo.save_skipped(doc.source_url, reason)
            stats["skipped"] += 1
        elif repo.needs_extraction(doc, prompt_version, model_id):
            todo.append(doc)
        elif doc.extraction_status == "ok":
            stats["reused"] += 1
            found[doc.source_url] = ListingBatch.model_validate(doc.answer_json())
        else:  # failed earlier under this prompt version and model: not retried until one changes
            stats["failed"] += 1

    def save(doc: Document, answer) -> None:
        answer = _as_answer(answer)
        repo.save_extraction(doc.source_url, answer.model_dump(mode="json"), prompt_version, model_id)
        stats["extracted"] += 1
        found[doc.source_url] = answer

    def one(doc: Document) -> None:  # today's path: one page, one call, failure marker on invalid output
        stats["calls"] += 1
        try:
            answer = extract_one(doc.title or "", doc.source_url, doc.raw_text)
        except ExtractionInvalid as e:  # record it; network/API errors are transient and propagate
            repo.save_failure(doc.source_url, str(e), prompt_version, model_id)
            stats["failed"] += 1
            return
        save(doc, answer)

    def split(group: list[Document]) -> None:
        mid = len(group) // 2
        batch(group[:mid])
        batch(group[mid:])

    def batch(group: list[Document]) -> None:
        if len(group) == 1:
            one(group[0])
            return
        stats["calls"] += 1
        try:
            answer = extract_many([(i, d.title or "", d.source_url, d.raw_text) for i, d in enumerate(group)])
        except ExtractionInvalid:  # bad JSON or cut-off answer: try smaller batches
            split(group)
            return
        missing, redo = [], []
        for i, doc in enumerate(group):
            if i not in answer:
                missing.append(doc)
            elif looks_mixed_up(doc.raw_text, [d.raw_text for j, d in enumerate(group) if j != i],
                                _as_answer(answer[i]).listings):
                redo.append(doc)
            else:
                save(doc, answer[i])
        for doc in redo:  # re-extract alone with the unchanged one-page extractor
            one(doc)
        if missing:  # every page must be answered; resend the unanswered ones smaller
            split(group) if len(missing) == len(group) else batch(missing)

    batch_size = max(1, int(cfg.get("batch_size", 1)))
    if batch_size == 1 or extract_many is None:
        for doc in todo:
            one(doc)
    else:
        for group in make_batches(todo, batch_size, int(cfg.get("batch_max_chars", 6000))):
            batch(group)

    listings, evidence = [], []
    for r in results:
        answer = found.get(r["url"])
        if answer is None:
            continue
        doc = repo.get(r["url"])
        prov = Provenance(source_url=doc.source_url, source_type=answer.source_type, agent_source=doc.agent_source,
                          first_seen_at=doc.first_seen_at, retrieved_at=doc.retrieved_at)
        for l in answer.listings:
            l.url = doc.source_url
            l.provenance, l.observations = prov, [prov]
            l.missing_fields = missing_fields(l)
            listings.append(l)
        for e in answer.evidence:
            e.source_url, e.source_type = doc.source_url, answer.source_type
            evidence.append(e)
    return listings, evidence, stats
