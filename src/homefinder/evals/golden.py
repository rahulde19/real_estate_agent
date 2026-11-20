"""Loader for the golden set in evals/golden/ (Phase 8.1). Pure data loading: no LLM, no network.

The answer keys are a judgment written by Claude (see evals/golden/README.md), not ground truth from the sites.
"""
import json
from pathlib import Path

from homefinder.config.config import ROOT
from homefinder.schemas import EvidenceItem, Listing, ListingBatch, UserProfile

GOLDEN_DIR = ROOT / "evals" / "golden"
_KEY_ONLY = {"judgment", "alt_rents"}  # fields of a key listing that the Listing model does not have


def load_pages(path: Path = GOLDEN_DIR / "pages.json") -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_profiles(path: Path = GOLDEN_DIR / "profiles.json") -> dict[str, UserProfile]:
    """{profile id: UserProfile}."""
    return {p["id"]: UserProfile(**p["profile"]) for p in json.loads(path.read_text(encoding="utf-8"))}


def load_expected(path: Path = GOLDEN_DIR / "expected.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def as_results(pages: list[dict], city: str | None = None) -> list[dict]:
    """The {title, url, content} dicts a search step returns, for pages of one city (all pages when city is None)."""
    return [{"title": p["title"], "url": p["url"], "content": p["content"]}
            for p in pages if city is None or p["city"].lower() == city.lower()]


def key_listings(page: dict) -> list[Listing]:
    return [Listing(**{k: v for k, v in d.items() if k not in _KEY_ONLY}, url=page["url"])
            for d in page["expected"]["listings"]]


def gold_answer(page: dict) -> ListingBatch:
    """The answer key as an extraction answer, so offline tests can use it as a stub extractor."""
    e = page["expected"]
    return ListingBatch(source_type=e["source_type"], listings=key_listings(page),
                        evidence=[EvidenceItem(**x) for x in e["evidence"]])


def stub_extractor(pages: list[dict]):
    """An `extract(title, url, text)` stub that returns each page's key (an empty answer for unknown URLs)."""
    by_url = {p["url"]: p for p in pages}
    return lambda title, url, text: gold_answer(by_url[url]) if url in by_url else ListingBatch()
