"""Deduplication (Phase 7.2): repeated ads for one property become one listing. Plain code, no LLM.

Block by locality + type (BHK / sharing must not conflict), then match inside a block when the rents are
within a tolerance and one identity signal agrees: phone, building name or geo-point.
"""
import hashlib
import math
import re

from homefinder.core.formulas import missing_fields
from homefinder.schemas import Listing

_CODE_FIELDS = {"url", "provenance", "observations", "missing_fields", "property_id", "evidence"}


def norm(s: str | None) -> str:
    """Lower-case letters and digits only, single spaces; dots and apostrophes vanish: 'Sri  Sai P.G.' -> 'sri sai pg'."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", re.sub(r"[.'’]", "", (s or "").lower())).split())


def _phone(s: str | None) -> str:
    digits = re.sub(r"\D", "", s or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _metres(a: Listing, b: Listing) -> float:
    """Distance between two geo-points (haversine)."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b.lng - a.lng) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _conflict(x, y) -> bool:
    return x is not None and y is not None and x != y


def same_property(a: Listing, b: Listing, rent_tolerance: float = 0.05, geo_radius_m: float = 100) -> bool:
    if a.kind != b.kind or norm(a.area) != norm(b.area):  # block: locality + type
        return False
    if _conflict(a.bhk, b.bhk) or _conflict(a.sharing, b.sharing):  # block: BHK / sharing (unknown fits any)
        return False
    if abs(a.rent - b.rent) > rent_tolerance * max(a.rent, b.rent):
        return False
    if _phone(a.phone) and _phone(a.phone) == _phone(b.phone):
        return True
    name_a, name_b = norm(a.building_name or a.name), norm(b.building_name or b.name)
    if name_a and name_a == name_b:
        return True
    return None not in (a.lat, a.lng, b.lat, b.lng) and _metres(a, b) <= geo_radius_m


def _reliability(l: Listing, source_values: dict) -> float:
    """Ads and builder pages are source reliability (rulebook [source_values]); other sources count 0."""
    kind = l.provenance.source_type if l.provenance else None
    return source_values.get(kind, 0) if kind in ("ad", "builder") else 0


def _merge(group: list[Listing], source_values: dict) -> Listing:
    """Most reliable source first, then the earliest sighting; its blanks are filled from the others."""
    order = sorted(group, key=lambda l: (-_reliability(l, source_values),
                                         l.provenance.first_seen_at if l.provenance else "", l.url or ""))
    out = order[0].model_copy(deep=True)
    for other in order[1:]:
        for field in Listing.model_fields.keys() - _CODE_FIELDS:
            if getattr(out, field) is None and getattr(other, field) is not None:
                setattr(out, field, getattr(other, field))
    seen = {}
    for l in order:
        for o in l.observations or ([l.provenance] if l.provenance else []):
            seen.setdefault(o.source_url, o)
    out.observations = list(seen.values())
    out.missing_fields = missing_fields(out)
    return out


def _identity(l: Listing) -> str:
    return "|".join([l.kind, norm(l.area), norm(l.building_name or l.name), str(l.bhk), str(l.sharing)])


def dedupe(listings: list[Listing], cfg: dict | None = None, source_values: dict | None = None) -> list[Listing]:
    """One listing per property, in first-seen order, each with a `property_id` and all its `observations`."""
    cfg, source_values = cfg or {}, source_values or {}
    tol, radius = cfg.get("rent_tolerance", 0.05), cfg.get("geo_radius_m", 100)
    parent = list(range(len(listings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(listings)):
        for j in range(i):
            if find(i) != find(j) and same_property(listings[i], listings[j], tol, radius):
                low, high = sorted((find(i), find(j)))
                parent[high] = low  # chains merge (A~B, B~C -> one group); the root stays the group's first index

    groups: dict[int, list[Listing]] = {}
    for i, l in enumerate(listings):
        groups.setdefault(find(i), []).append(l)

    out, used = [], {}
    for root in sorted(groups):  # roots are each group's first index, so order follows the search results
        merged = _merge(groups[root], source_values)
        pid = hashlib.sha1(_identity(merged).encode("utf-8")).hexdigest()[:10]
        used[pid] = used.get(pid, 0) + 1
        merged.property_id = pid if used[pid] == 1 else f"{pid}-{used[pid]}"  # same name, different rent: kept apart
        out.append(merged)
    return out
