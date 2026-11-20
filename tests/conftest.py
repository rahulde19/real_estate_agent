from datetime import date

import pytest

from homefinder.config.config import load_rulebook
from homefinder.schemas import Listing, UserProfile


@pytest.fixture
def rb():
    return load_rulebook()


@pytest.fixture
def profile():
    return UserProfile(
        city="Chennai", work_location="Taramani", budget=15000, upfront_cash=60000, mover="solo", group_size=1,
        commute_tolerance=40, move_in_date=date(2026, 12, 1), food="need meals",
        privacy="private room", furnishing="furnished", noise="quiet", gender_restricted=False,
        must_have={"privacy": True},
    )


def pg(name="PG", rent=9000, **kw):
    return Listing(name=name, kind="pg", rent=rent, **kw)


def plain_rulebook():
    """Rulebook with one call per page and no filter: for tests that stub the one-page extractor."""
    rb = load_rulebook()
    return {**rb, "extraction": {**rb["extraction"], "prefilter": False, "batch_size": 1}}
