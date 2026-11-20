"""City cost-of-living data: loads the CSV once and looks a city up by name.

Quality rules (Phase 4.1):
- impossible: rent above `max_rent_share` of total cost (includes rent above cost) -> unusable, callers
  fall back to the labelled [defaults] in rulebook.toml.
- repeated: the same (cost, rent) pair appears for more than one city -> flagged suspect, numbers kept.
  Single repeated columns are common in this data (Chennai shares its cost with two other cities), so they are not flagged.
"""
import csv
from collections import Counter
from functools import lru_cache
from pathlib import Path

from homefinder.config.config import CITY_COSTS_PATH, load_rulebook
from homefinder.schemas import CityCosts


def city_key(name: str) -> str:
    """'  Hyderabad, Telangana' -> 'hyderabad'."""
    return name.split(",")[0].strip().lower()


def load_city_costs(path: Path = CITY_COSTS_PATH, max_rent_share: float = 0.75) -> dict[str, CityCosts]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    pairs = Counter((r["cost_one_person_inr"], r["rent_one_person_inr"]) for r in rows)
    out: dict[str, CityCosts] = {}
    for r in rows:
        cost, rent = float(r["cost_one_person_inr"]), float(r["rent_one_person_inr"])
        impossible = rent > max_rent_share * cost
        repeated = pairs[(r["cost_one_person_inr"], r["rent_one_person_inr"])] > 1
        out[city_key(r["City"])] = CityCosts(
            name=r["City"].strip(), cost_one_person=cost, rent_one_person=rent,
            data_quality="suspect" if impossible or repeated else "ok", usable=not impossible,
        )
    return out


@lru_cache(maxsize=1)
def _default_table() -> dict[str, CityCosts]:
    return load_city_costs(max_rent_share=load_rulebook()["city_costs"]["max_rent_share"])


def lookup_city_costs(city: str) -> CityCosts | None:
    """The city's usable row, or None (unknown city or impossible row) so callers use the defaults."""
    row = _default_table().get(city_key(city))
    return row if row and row.usable else None
