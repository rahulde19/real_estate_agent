"""Pre-filter for search results: skip pages that cannot contain a rental listing. Plain code, no LLM.

A page is skipped only when it is clearly useless, because wrongly skipping a page loses listings while
wrongly keeping one costs only a little prompt space. The extraction rules already say a listing needs a
concrete monthly rent in rupees, so a page with no rupee-style amount cannot produce one.
"""
import re
from urllib.parse import urlparse

# A rupee-style amount (dollar/pound/euro amounts do not count): a currency marker next to a number, "1.7 Lac", "15k", "9500/month", or "rent ... 9500".
# Bare 4+ digit numbers are NOT enough: years, pin codes and square feet would keep junk pages (e.g. a lease deed).
AMOUNT = re.compile(
    r"""(?:₹|\bRs\b\.?|\bINR\b|\brupees?\b)\s*\d
      | \d\s*(?:lacs?|lakhs?)\b
      | (?<![$£€\d.,])\b\d{1,3}(?:\.\d+)?\s?k\b
      | (?<![$£€\d.,])\b\d[\d,]{3,}\s*(?:/-|/\s*(?:month|mo)\b|per\s+month|a\s+month|pm\b)
      | \brent\w*\W{0,15}(?<![$£€])\d[\d,]{3,}
    """,
    re.IGNORECASE | re.VERBOSE,
)


def has_amount(text: str) -> bool:
    return bool(AMOUNT.search(text))


def _domain_blocked(url: str, blocked: list[str]) -> bool:  # also used as a plain 'domain is in this list' test
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in blocked)


def prefilter_reason(url: str, title: str, text: str, min_chars: int = 80,
                     blocked_domains: list[str] | tuple = (), evidence_domains: list[str] | tuple = ()) -> str | None:
    """Why this page should be skipped ('blocked_domain', 'too_short', 'no_amount'), or None to keep it.
    Pages on `evidence_domains` (review and forum sites) are kept without a rupee amount: they can hold evidence items."""
    if _domain_blocked(url, list(blocked_domains)):
        return "blocked_domain"
    if len(text.strip()) < min_chars:
        return "too_short"
    if not has_amount(f"{title}\n{text}") and not _domain_blocked(url, list(evidence_domains)):
        return "no_amount"
    return None
