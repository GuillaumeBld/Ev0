"""Tests for fixture sync via The Odds API."""
import pytest
from datetime import datetime, timezone
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


class TestJobSyncFixtures:
    """Tests for job_sync_fixtures — vérifie mise à jour des kickoff_utc."""

    def _make_fixture(self, id_, home, away, kickoff, league="ligue_1"):
        fix = MagicMock()
        fix.id = id_
        fix.home_team = home
        fix.away_team = away
        fix.kickoff_utc = kickoff
        fix.league = league
        fix.odds_api_event_id = None
        fix.status = "scheduled"
        return fix

    @pytest.mark.asyncio
    async def test_updates_kickoff_when_different(self):
        wrong_kickoff = datetime(2026, 4, 5, 15, 0, tzinfo=timezone.utc)
        correct_kickoff = datetime(2026, 4, 3, 18, 45, tzinfo=timezone.utc)
        fixture = self._make_fixture(631, "Paris Saint-Germain", "Toulouse", wrong_kickoff, league="ligue_1")

        mock_events = [{
            "id": "event-abc",
            "home_team": "Paris Saint Germain",
            "away_team": "Toulouse",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        async def fake_fetch(league):
            return mock_events if league == "ligue_1" else []

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(side_effect=fake_fetch)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=fixture), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fixture])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()

        assert fixture.kickoff_utc == correct_kickoff

    @pytest.mark.asyncio
    async def test_no_update_when_kickoff_already_correct(self):
        correct_kickoff = datetime(2026, 4, 3, 18, 45, tzinfo=timezone.utc)
        fixture = self._make_fixture(631, "Paris Saint-Germain", "Toulouse", correct_kickoff, league="ligue_1")

        mock_events = [{
            "id": "event-abc",
            "home_team": "Paris Saint Germain",
            "away_team": "Toulouse",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        original_kickoff = fixture.kickoff_utc

        async def fake_fetch(league):
            return mock_events if league == "ligue_1" else []

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(side_effect=fake_fetch)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=fixture), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fixture])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()

        assert fixture.kickoff_utc == original_kickoff

    @pytest.mark.asyncio
    async def test_skips_event_with_no_fixture_match(self):
        mock_events = [{
            "id": "event-xyz",
            "home_team": "Unknown Team",
            "away_team": "Also Unknown",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(return_value=mock_events)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=None), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()  # must not raise

    @pytest.mark.asyncio
    async def test_one_league_error_does_not_block_others(self):
        """Une erreur sur ligue_1 ne doit pas empêcher le traitement de premier_league."""
        call_count = 0

        async def fake_fetch(league):
            nonlocal call_count
            call_count += 1
            if league == "ligue_1":
                raise Exception("Simulated API error")
            return []

        with patch("app.worker.fetch_events_for_league", new_callable=AsyncMock, side_effect=fake_fetch), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})):

            from app.worker import job_sync_fixtures
            from app.worker import DEFAULT_LEAGUES
            await job_sync_fixtures()  # must not raise

        assert call_count == len(DEFAULT_LEAGUES)
