"""Scoring for extraction accuracy against the golden set (Phase 8.2). Pure functions: no LLM, no network.

Matching rules are the ones in evals/golden/README.md:
- a key listing is found when a returned listing has the same `kind` and a rent within Rs 1 of the key's rent or of
  any of its `alt_rents`; each returned listing can match one key listing; extra returned listings are false positives;
- fields are scored one by one on matched pairs, only where the key states a value;
- evidence matches on (aspect, polarity); severity is reported, not scored; evidence on an `off_topic` page is
  always a false positive; `source_type` is not scored on `off_topic` pages.
"""
from collections import Counter
from dataclasses import dataclass, field

from homefinder.agent.extraction import rent_supported
from homefinder.schemas import Listing, ListingBatch

SCORED_FIELDS = ("deposit", "area", "sharing", "private_room", "meals_included", "furnishing", "gender_restricted",
                 "bhk", "maintenance", "brokerage", "lock_in_months", "notice_months", "vibe")


@dataclass
class PageScore:
    page_id: str
    valid: bool = True                 # the model returned parseable, schema-valid output for this page
    n_key: int = 0
    n_returned: int = 0
    found: int = 0                     # key listings matched
    false_positives: int = 0           # returned listings that match no key listing
    invented: int = 0                  # returned listings whose rent is not in the page text
    fields: dict = field(default_factory=dict)       # name -> [correct, scored]
    source_type_scored: bool = False
    source_type_ok: bool = False
    ev_tp: int = 0
    ev_fp: int = 0
    ev_fn: int = 0
    severity_ok: int = 0               # matched evidence items whose severity also agrees
    confidence_ok: int | None = None   # for a key that states extraction_confidence: 1 if equal, else 0


def rent_matches(key: dict, listing: Listing) -> bool:
    return listing.kind == key["kind"] and any(abs(listing.rent - r) <= 1 for r in [key["rent"], *key.get("alt_rents", [])])


def match_listings(keys: list[dict], returned: list[Listing]) -> list[tuple[int, int]]:
    """Greedy one-to-one matching: [(key index, returned index)]. Keys are taken in order, first free match wins."""
    used, pairs = set(), []
    for i, k in enumerate(keys):
        for j, l in enumerate(returned):
            if j not in used and rent_matches(k, l):
                used.add(j)
                pairs.append((i, j))
                break
    return pairs


def _field_equal(name: str, expected, got) -> bool:
    if isinstance(expected, str) and isinstance(got, str) and name == "area":
        return expected.strip().lower() == got.strip().lower()
    return expected == got


def score_page(page: dict, answer: ListingBatch | None) -> PageScore:
    """Score one page. `answer` is None when the model's output was invalid or missing."""
    exp = page["expected"]
    keys = exp["listings"]
    s = PageScore(page_id=page["id"], n_key=len(keys))
    if answer is None:
        s.valid = False
        s.ev_fn = 0 if "off_topic" in page["tags"] else len(exp["evidence"])
        return s
    returned = answer.listings
    text = f"{page['title']} {page['content']}"
    s.n_returned = len(returned)
    pairs = match_listings(keys, returned)
    s.found = len(pairs)
    s.false_positives = len(returned) - len(pairs)
    s.invented = sum(1 for l in returned if not rent_supported(l.rent, text))
    for i, j in pairs:
        key, got = keys[i], returned[j]
        for name in SCORED_FIELDS:
            if key.get(name) is not None:
                c = s.fields.setdefault(name, [0, 0])
                c[1] += 1
                c[0] += _field_equal(name, key[name], getattr(got, name))
        if key.get("extraction_confidence"):
            s.confidence_ok = int(got.extraction_confidence == key["extraction_confidence"])
    if "off_topic" not in page["tags"]:
        s.source_type_scored, s.source_type_ok = True, answer.source_type == exp["source_type"]
        want = Counter((e["aspect"], e["polarity"]) for e in exp["evidence"])
        have = Counter((e.aspect, e.polarity) for e in answer.evidence)
        s.ev_tp = sum((want & have).values())
        s.ev_fn = sum((want - have).values())
        s.ev_fp = sum((have - want).values())
        left = list(exp["evidence"])
        for e in answer.evidence:  # severity agreement on the matched items
            for x in left:
                if (x["aspect"], x["polarity"]) == (e.aspect, e.polarity):
                    s.severity_ok += x["severity"] == e.severity
                    left.remove(x)
                    break
    else:
        s.ev_fp = len(answer.evidence)  # any evidence from an off-topic page is an error
    return s


def _ratio(num: float, den: float) -> float | None:
    return None if not den else num / den


def aggregate(scores: list[PageScore]) -> dict:
    """Totals and rates over all pages. A rate with no denominator is None."""
    n_key, n_ret = sum(s.n_key for s in scores), sum(s.n_returned for s in scores)
    found = sum(s.found for s in scores)
    fields = {}
    for s in scores:
        for name, (ok, total) in s.fields.items():
            c = fields.setdefault(name, [0, 0])
            c[0] += ok
            c[1] += total
    tp, fp, fn = (sum(getattr(s, k) for s in scores) for k in ("ev_tp", "ev_fp", "ev_fn"))
    st = [s for s in scores if s.source_type_scored]
    conf = [s.confidence_ok for s in scores if s.confidence_ok is not None]
    return {
        "pages": len(scores),
        "schema_valid_rate": _ratio(sum(s.valid for s in scores), len(scores)),
        "listings_in_key": n_key, "listings_returned": n_ret, "listings_found": found,
        "recall": _ratio(found, n_key), "precision": _ratio(found, n_ret),
        "false_positive_listings": sum(s.false_positives for s in scores),
        "invented_rent_rate": _ratio(sum(s.invented for s in scores), n_ret),
        "field_accuracy": {k: {"correct": c, "scored": t, "rate": _ratio(c, t)} for k, (c, t) in sorted(fields.items())},
        "source_type_accuracy": _ratio(sum(s.source_type_ok for s in st), len(st)),
        "evidence": {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                     "severity_agreement": _ratio(sum(s.severity_ok for s in scores), tp)},
        "confidence_agreement": _ratio(sum(conf), len(conf)),
        "pages_with_misses": [s.page_id for s in scores if s.found < s.n_key or s.false_positives or not s.valid
                              or s.ev_fn or s.ev_fp or (s.source_type_scored and not s.source_type_ok)],
    }


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"
