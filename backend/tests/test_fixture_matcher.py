"""Tests for fixture_matcher module."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.fixture_matcher import (
    match_odds_event_to_fixture,
    normalize_team_name,
)


@dataclass
class FakeFixture:
    """Lightweight stand-in for the Fixture ORM model."""

    external_id: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    odds_api_event_id: str | None = None


class TestNormalizeTeamName:
    """Tests for team name normalization."""

    def test_basic_normalization(self):
        assert normalize_team_name("Paris Saint Germain") == "paris-saint-germain"

    def test_strips_accents(self):
        assert normalize_team_name("Montpellier Hérault") == "montpellier-herault"

    def test_removes_fc_suffix(self):
        result = normalize_team_name("Nantes FC")
        assert "fc" not in result

    def test_alias_override(self):
        assert normalize_team_name("PSG") == "paris-saint-germain"
        assert normalize_team_name("Olympique Lyonnais") == "lyon"
        assert normalize_team_name("Man City") == "manchester-city"
        assert normalize_team_name("Wolves") == "wolverhampton"

    def test_removes_punctuation(self):
        assert normalize_team_name("O'Brien's FC") == "obriens"

    def test_case_insensitive(self):
        assert normalize_team_name("LYON") == normalize_team_name("lyon")


class TestMatchOddsEventToFixture:
    """Tests for matching Odds API events to DB fixtures."""

    @pytest.fixture
    def now(self):
        return datetime.now(UTC)

    @pytest.fixture
    def fixtures(self, now):
        return [
            FakeFixture(
                external_id="fotmob_1",
                home_team="Paris Saint-Germain",
                away_team="Lyon",
                kickoff_utc=now + timedelta(hours=2),
            ),
            FakeFixture(
                external_id="fotmob_2",
                home_team="Marseille",
                away_team="Monaco",
                kickoff_utc=now + timedelta(hours=24),
            ),
        ]

    def test_exact_team_match(self, fixtures, now):
        """Matching normalized teams should return the fixture."""
        event = {
            "id": "odds-uuid-1",
            "home_team": "Paris Saint Germain",
            "away_team": "Olympique Lyonnais",
            "commence_time": (now + timedelta(hours=2)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, fixtures)
        assert result is not None
        assert result.external_id == "fotmob_1"

    def test_alias_map_override(self, fixtures, now):
        """Alias map should resolve known mismatches."""
        event = {
            "id": "odds-uuid-2",
            "home_team": "Olympique de Marseille",
            "away_team": "AS Monaco",
            "commence_time": (now + timedelta(hours=24)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, fixtures)
        assert result is not None
        assert result.external_id == "fotmob_2"

    def test_no_match_returns_none(self, fixtures, now):
        """Unknown teams should return None."""
        event = {
            "id": "odds-uuid-3",
            "home_team": "Barcelona",
            "away_team": "Real Madrid",
            "commence_time": (now + timedelta(hours=2)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, fixtures)
        assert result is None

    def test_date_window_filtering(self, fixtures, now):
        """Events outside the 36h window should not match."""
        event = {
            "id": "odds-uuid-4",
            "home_team": "Paris Saint Germain",
            "away_team": "Olympique Lyonnais",
            # 3 days in the future — outside the 36h window
            "commence_time": (now + timedelta(days=3)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, fixtures)
        assert result is None

    def test_single_team_match_rejected(self, fixtures, now):
        """Only one team matching should be rejected for safety."""
        event = {
            "id": "odds-uuid-5",
            "home_team": "Paris Saint Germain",
            "away_team": "Marseille",  # wrong away team for fixture 1
            "commence_time": (now + timedelta(hours=2)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, fixtures)
        assert result is None

    def test_cached_odds_api_event_id_fast_path(self, now):
        """If fixture already has odds_api_event_id, match instantly."""
        fixture = FakeFixture(
            external_id="fotmob_99",
            home_team="Nice",
            away_team="Rennes",
            kickoff_utc=now + timedelta(hours=5),
            odds_api_event_id="cached-uuid-99",
        )
        event = {
            "id": "cached-uuid-99",
            "home_team": "Completely Wrong Name",
            "away_team": "Also Wrong",
            "commence_time": (now + timedelta(hours=5)).isoformat(),
        }
        result = match_odds_event_to_fixture(event, [fixture])
        assert result is not None
        assert result.external_id == "fotmob_99"
