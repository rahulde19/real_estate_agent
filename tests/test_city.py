"""Phase 3: the city is an input, not a constant. 'Chennai' appears here only as the dev/sample city."""
from datetime import date

import pytest
from pydantic import ValidationError

from homefinder.agent.prompts import EXTRACT_SYSTEM, explain_system
from homefinder.config.config import sample_supports
from homefinder.schemas import UserProfile
from homefinder.services import search
from homefinder.services.search import SampleModeError, build_queries

BASE = dict(work_location="Tidel Park", budget=15000, upfront_cash=60000, mover="solo", group_size=1,
            commute_tolerance=40, move_in_date=date(2026, 12, 1), food="eat out",
            privacy="sharing is fine", furnishing="furnished", noise="no preference", gender_restricted=False)


def test_profile_requires_city():
    with pytest.raises(ValidationError):
        UserProfile(**BASE)


def test_profile_rejects_blank_city_and_strips():
    with pytest.raises(ValidationError):
        UserProfile(city="   ", **BASE)
    assert UserProfile(city="  Pune ", **BASE).city == "Pune"


def test_queries_use_given_city():
    qs = " ".join(build_queries("Tidel Park", "Pune"))
    assert "Pune" in qs and "Chennai" not in qs


def test_explain_prompt_uses_given_city():
    text = explain_system("Pune")
    assert "Pune" in text and "Chennai" not in text and "{city}" not in text


def test_extract_prompt_is_user_and_city_independent():
    assert "{city}" not in EXTRACT_SYSTEM
    assert "work location" not in EXTRACT_SYSTEM.lower() and "chennai" not in EXTRACT_SYSTEM.lower()


def test_sample_supports_normalises_city():
    assert sample_supports("Chennai") and sample_supports(" chennai ") and sample_supports("Chennai, Tamil Nadu")
    assert not sample_supports("Pune")


def test_sample_mode_other_city_gives_clear_error(monkeypatch):
    monkeypatch.setattr(search, "SEARCH_SOURCE", "sample")
    with pytest.raises(SampleModeError, match="SEARCH_SOURCE=live"):
        search.search_web("Hinjewadi", "Pune")


def test_sample_mode_sample_city_returns_sample(monkeypatch):
    monkeypatch.setattr(search, "SEARCH_SOURCE", "sample")
    assert search.search_web("Taramani", "Chennai")
    assert search.search_web("Taramani", "chennai, tamil nadu")
