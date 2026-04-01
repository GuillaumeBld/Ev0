"""Tests for fixture sync via The Odds API."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.ingestion.odds import SPORT_KEYS
from app.ingestion.fixture_matcher import normalize_team_name


class TestSportKeys:
    def test_covers_all_six_leagues(self):
        expected = {
            "ligue_1", "premier_league", "bundesliga",
            "la_liga", "serie_a", "champions_league",
        }
        assert set(SPORT_KEYS.keys()) == expected

    def test_bundesliga_key(self):
        assert SPORT_KEYS["bundesliga"] == "soccer_germany_bundesliga"

    def test_la_liga_key(self):
        assert SPORT_KEYS["la_liga"] == "soccer_spain_la_liga"

    def test_serie_a_key(self):
        assert SPORT_KEYS["serie_a"] == "soccer_italy_serie_a"


class TestTeamAliases:
    """Vérifie que les noms The Odds API normalisent vers les noms DB."""

    def test_athletic_bilbao_normalizes(self):
        # The Odds API retourne "Athletic Bilbao", DB a "Athletic Club"
        assert normalize_team_name("Athletic Bilbao") == normalize_team_name("Athletic Club")

    def test_inter_milan_normalizes(self):
        assert normalize_team_name("Inter Milan") == normalize_team_name("Inter")

    def test_ac_milan_normalizes(self):
        assert normalize_team_name("AC Milan") == normalize_team_name("Milan")

    def test_pisa_normalizes(self):
        # The Odds API: "Pisa" / DB peut avoir "AC Pisa 1909"
        assert normalize_team_name("AC Pisa 1909") == normalize_team_name("Pisa")

    def test_bayer_leverkusen_normalizes(self):
        assert normalize_team_name("Bayer 04 Leverkusen") == normalize_team_name("Bayer Leverkusen")


from app.ingestion.odds import fetch_events_for_league


class TestFetchEventsForLeague:
    @pytest.mark.asyncio
    async def test_returns_list_of_events(self):
        mock_events = [
            {"id": "abc", "home_team": "PSG", "away_team": "Marseille",
             "commence_time": "2026-04-05T18:45:00Z"},
        ]
        with patch("app.ingestion.odds.OddsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.get_events = AsyncMock(return_value=mock_events)
            result = await fetch_events_for_league("ligue_1")
        assert result == mock_events

    @pytest.mark.asyncio
    async def test_unknown_league_returns_empty(self):
        result = await fetch_events_for_league("ligue_inconnue")
        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        with patch("app.ingestion.odds.OddsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.get_events = AsyncMock(side_effect=Exception("HTTP 403"))
            result = await fetch_events_for_league("ligue_1")
        assert result == []


from app.ingestion.fixture_matcher import match_event_to_fixture_by_teams


class TestMatchEventToFixtureByTeams:
    def _make_fixture(self, home, away, kickoff=None):
        fix = MagicMock()
        fix.home_team = home
        fix.away_team = away
        fix.kickoff_utc = kickoff
        fix.odds_api_event_id = None
        return fix

    def test_matches_by_team_names(self):
        event = {"home_team": "Paris Saint Germain", "away_team": "Toulouse",
                 "commence_time": "2026-04-03T18:45:00Z", "id": "x1"}
        fixtures = [self._make_fixture("Paris Saint-Germain", "Toulouse")]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is not None
        assert result.home_team == "Paris Saint-Germain"

    def test_no_date_window_constraint(self):
        """Must match even when DB kickoff is completely wrong (placeholder)."""
        event = {"home_team": "Lyon", "away_team": "Rennes",
                 "commence_time": "2026-04-05T18:45:00Z", "id": "x2"}
        from datetime import datetime, timezone
        wrong_kickoff = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)  # 3 months off
        fixtures = [self._make_fixture("Lyon", "Rennes", kickoff=wrong_kickoff)]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is not None

    def test_returns_none_when_no_match(self):
        event = {"home_team": "Liverpool", "away_team": "Arsenal",
                 "commence_time": "2026-04-05T18:45:00Z", "id": "x3"}
        fixtures = [self._make_fixture("PSG", "Marseille")]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is None
