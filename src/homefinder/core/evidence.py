"""Evidence items (Phase 7.5): match them to properties and turn independent ones into Reputation values. No LLM."""
from homefinder.core.dedup import norm
from homefinder.schemas import EvidenceItem, Listing

_MIN_PARTIAL = 6  # a shorter name must match exactly ("PG" must not match every "... PG")


def _about(subject: str, l: Listing) -> bool:
    s = norm(subject)
    for name in {norm(l.name), norm(l.building_name)} - {""}:
        short, long = sorted((s, name), key=len)
        if s == name or (len(short) >= _MIN_PARTIAL and f" {short} " in f" {long} "):
            return True
    return False


def attach_evidence(listings: list[Listing], items: list[EvidenceItem]) -> list[Listing]:
    """Give each listing the evidence items about it (matched on the property name), without repeats."""
    for l in listings:
        seen, l.evidence = set(), []
        for e in items:
            key = (e.source_url, e.aspect, e.polarity)
            if key not in seen and _about(e.subject, l):
                seen.add(key)
                l.evidence.append(e)
    return listings


def reputation_values(items: list[EvidenceItem], source_values: dict, cfg: dict) -> list[float]:
    """One value per distinct independent source (reviews and forum posts): the mean of its items, each worth the
    rulebook source value times its severity factor. Ads and builder pages never count towards Reputation."""
    by_source: dict[str, list[float]] = {}
    for e in items:
        if e.source_type in cfg["independent_sources"]:
            base = source_values["review_positive"] if e.polarity == "positive" else source_values["complaint"]
            by_source.setdefault(e.source_url or "", []).append(base * cfg["severity"][e.severity])
    return [sum(v) / len(v) for v in by_source.values()]


def caveats_by_criterion(items: list[EvidenceItem], aspect_criteria: dict) -> dict[str, list[str]]:
    """Evidence grouped under the criterion its aspect belongs to, as short readable lines for the UI."""
    out: dict[str, list[str]] = {}
    for e in items:
        line = f"{e.aspect.replace('_', ' ')}: {e.polarity} ({e.severity}, {e.source_type or 'unknown source'})"
        out.setdefault(aspect_criteria.get(e.aspect, "reputation"), []).append(line)
    return out
