"""Web search. Default reads a saved Tavily response from disk; SEARCH_SOURCE=live calls Tavily."""
import json
import os

from tavily import TavilyClient

from homefinder.config.config import SAMPLE_CITY, SAMPLE_PATH, SEARCH_SOURCE, load_rulebook, sample_supports


class SampleModeError(ValueError):
    """The saved sample only covers one city; other cities need live search."""


def sample_mode_message(city: str) -> str:
    return (f"Sample search data only covers {SAMPLE_CITY}, so it can't be used for '{city}'. "
            "Set SEARCH_SOURCE=live and TAVILY_API_KEY in .env to search other cities.")


def build_queries(work_location: str, city: str) -> list[str]:
    """Location-only queries. Budget, size, etc. are applied later when scoring listings."""
    loc = work_location
    return [
        f"PG paying guest near {loc} {city} rent per month deposit food",
        f"flat for rent near {loc} {city} rent deposit brokerage",
        f"best PG hostel reviews near {loc} {city}",
    ]


def _clean(r: dict, max_chars: int) -> dict:
    return {"title": r.get("title", ""), "url": r["url"], "content": r.get("content", "")[:max_chars]}


def search_sample(max_chars: int = 1200) -> list[dict]:
    """Saved Tavily output (same shape as the live API response)."""
    with open(SAMPLE_PATH, encoding="utf-8") as f:
        return [_clean(r, max_chars) for r in json.load(f).get("results", [])]


def search_web(work_location: str, city: str, max_results: int | None = None, max_chars: int | None = None) -> list[dict]:
    """Sizes default to the rulebook [search] table."""
    cfg = load_rulebook()["search"]
    max_results, max_chars = max_results or cfg["max_results"], max_chars or cfg["max_chars"]
    if SEARCH_SOURCE != "live":
        if not sample_supports(city):
            raise SampleModeError(sample_mode_message(city))
        return search_sample(max_chars)
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    seen, out = set(), []
    for q in build_queries(work_location, city):
        for r in client.search(query=q, max_results=max_results).get("results", []):
            if r["url"] not in seen:
                seen.add(r["url"])
                out.append(_clean(r, max_chars))
    return out
