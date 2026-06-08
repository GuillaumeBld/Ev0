"""Tests for _load_national_team_players and international fixture detection."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_player(api_id: int, name: str, position: str, national_team_api_id: int):
    """Build a minimal BzzPlayer-like mock."""
    p = MagicMock()
    p.api_id = api_id
    p.internal_id = None
    p.name = name
    p.position = position  # single char: G/D/M/F
    p.national_team_api_id = national_team_api_id
    return p


def _make_stat(player_api_id: int):
    """Build a minimal BzzPlayerSeasonStat-like mock with realistic values."""
    s = MagicMock()
    s.player_api_id = player_api_id
    s.season = "2025-2026"
    s.matches_played = 10
    s.minutes_played = 850
    s.goals = 3
    s.goal_assist = 2
    s.expected_goals = 2.8
    s.expected_assists = 1.9
    s.xg_per_90 = 0.30
    s.xa_per_90 = 0.20
    s.shot_accuracy = 0.45
    s.xg_per_shot = 0.13
    s.avg_rating = 7.1
    s.cross_accuracy = 0.35
    s.key_pass_per_90 = 0.50
    s.accurate_cross_per_90 = 0.18
    s.shots_on_target_per_90 = 0.90
    s.form_xg_5 = 1.2
    s.form_assists_5 = 1
    return s


class TestLoadNationalTeamPlayers:
    """Unit tests for _load_national_team_players (mocked DB session)."""

    @pytest.mark.asyncio
    async def test_returns_three_outfield_players(self):
        """Given 3 outfield players with national_team_api_id=485, all 3 are returned."""
        from app.pricing.team_xg import _load_national_team_players

        players = [
            _make_player(1001, "Kylian Mbappé", "F", 485),
            _make_player(1002, "Antoine Griezmann", "F", 485),
            _make_player(1003, "N'Golo Kanté", "M", 485),
        ]
        stats = [_make_stat(pid) for pid in [1001, 1002, 1003]]
        # Rows returned by the stats query: list of (stat, player) tuples
        stat_rows = list(zip(stats, players))
        # Roster query: same players (no new additions)
        roster_scalars = MagicMock()
        roster_scalars.all.return_value = players

        db = AsyncMock()
        # First execute call → stats query (returns stat_rows)
        stats_result = MagicMock()
        stats_result.all.return_value = stat_rows
        # Second execute call → roster query (returns players with no stats)
        roster_result = MagicMock()
        roster_result.scalars.return_value = roster_scalars

        db.execute = AsyncMock(side_effect=[stats_result, roster_result])

        result = await _load_national_team_players(db, national_team_api_id=485)

        assert len(result) == 3
        names = {p["player_name"] for p in result}
        assert "Kylian Mbappé" in names
        assert "Antoine Griezmann" in names
        assert "N'Golo Kanté" in names

    @pytest.mark.asyncio
    async def test_goalkeeper_excluded(self):
        """Goalkeepers (position='G') are excluded from the result."""
        from app.pricing.team_xg import _load_national_team_players

        gk = _make_player(2001, "Mike Maignan", "G", 485)
        fw = _make_player(2002, "Marcus Thuram", "F", 485)
        stat_rows = [(_make_stat(2002), fw)]  # GK has no stat row either
        roster_scalars = MagicMock()
        roster_scalars.all.return_value = [gk, fw]

        db = AsyncMock()
        stats_result = MagicMock()
        stats_result.all.return_value = stat_rows
        roster_result = MagicMock()
        roster_result.scalars.return_value = roster_scalars
        db.execute = AsyncMock(side_effect=[stats_result, roster_result])

        result = await _load_national_team_players(db, national_team_api_id=485)

        assert all(p["position"] != "GK" for p in result)
        player_names = [p["player_name"] for p in result]
        assert "Mike Maignan" not in player_names

    @pytest.mark.asyncio
    async def test_player_with_no_stats_appended_with_zeros(self):
        """A player with no season stat row gets a zero-stats entry (roster fallback)."""
        from app.pricing.team_xg import _load_national_team_players

        fw_with_stats = _make_player(3001, "Olivier Giroud", "F", 485)
        fw_no_stats = _make_player(3002, "Randal Kolo Muani", "F", 485)

        stat_rows = [(_make_stat(3001), fw_with_stats)]
        roster_scalars = MagicMock()
        roster_scalars.all.return_value = [fw_with_stats, fw_no_stats]

        db = AsyncMock()
        stats_result = MagicMock()
        stats_result.all.return_value = stat_rows
        roster_result = MagicMock()
        roster_result.scalars.return_value = roster_scalars
        db.execute = AsyncMock(side_effect=[stats_result, roster_result])

        result = await _load_national_team_players(db, national_team_api_id=485)

        zero_player = next(p for p in result if p["player_name"] == "Randal Kolo Muani")
        assert zero_player["matches_played"] == 0
        assert zero_player["npxg_per_90"] == 0.0
        assert zero_player["has_bzz_stats"] is False

    @pytest.mark.asyncio
    async def test_returned_dict_has_required_keys(self):
        """The returned dicts contain all keys expected by compute_player_shares."""
        from app.pricing.team_xg import _load_national_team_players

        REQUIRED_KEYS = {
            "player_id", "player_name", "name", "position",
            "matches_played", "minutes_played", "goals", "xg", "npxg", "xa",
            "npxg_per_90", "xa_per_90", "xgchain_per_90", "xg_per_90",
            "shot_accuracy", "xg_per_shot", "avg_rating", "cross_accuracy",
            "xa_total", "assists_total", "npxg_total", "goals_total",
            "key_pass_per_90", "accurate_cross_per_90", "form_xg_5",
            "form_assists_5", "finishing_delta", "shots_on_target_per_90",
            "touches_attack_pen_area_per_90", "bcc_per_90",
            "accurate_crosses_per_90", "through_balls_per_90", "has_bzz_stats",
        }
        fw = _make_player(4001, "Theo Hernandez", "D", 485)
        stat_rows = [(_make_stat(4001), fw)]
        roster_scalars = MagicMock()
        roster_scalars.all.return_value = [fw]

        db = AsyncMock()
        stats_result = MagicMock()
        stats_result.all.return_value = stat_rows
        roster_result = MagicMock()
        roster_result.scalars.return_value = roster_scalars
        db.execute = AsyncMock(side_effect=[stats_result, roster_result])

        result = await _load_national_team_players(db, national_team_api_id=485)

        assert len(result) == 1
        missing = REQUIRED_KEYS - result[0].keys()
        assert not missing, f"Missing keys: {missing}"

    @pytest.mark.asyncio
    async def test_empty_result_when_no_players(self):
        """Returns empty list when no players are linked to the national team."""
        from app.pricing.team_xg import _load_national_team_players

        roster_scalars = MagicMock()
        roster_scalars.all.return_value = []

        db = AsyncMock()
        stats_result = MagicMock()
        stats_result.all.return_value = []
        roster_result = MagicMock()
        roster_result.scalars.return_value = roster_scalars
        db.execute = AsyncMock(side_effect=[stats_result, roster_result])

        result = await _load_national_team_players(db, national_team_api_id=9999)

        assert result == []


class TestInternationalFixtureDetection:
    """Tests for international league detection and BzzEvent lookup in load_match_pricing."""

    def _make_fixture(
        self,
        league: str,
        external_id: str,
        home_team: str = "France",
        away_team: str = "Northern Ireland",
        home_bzz_team_id: int | None = None,
        away_bzz_team_id: int | None = None,
    ):
        f = MagicMock()
        f.id = 1110181
        f.league = league
        f.external_id = external_id
        f.home_team = home_team
        f.away_team = away_team
        f.home_bzz_team_id = home_bzz_team_id
        f.away_bzz_team_id = away_bzz_team_id
        return f

    def _make_bzz_event(self, home_team_api_id: int, away_team_api_id: int):
        ev = MagicMock()
        ev.home_team_api_id = home_team_api_id
        ev.away_team_api_id = away_team_api_id
        return ev

    @pytest.mark.asyncio
    async def test_international_league_calls_national_team_loader(self):
        """When league is friendly_international, _load_national_team_players is called."""
        fixture = self._make_fixture(
            league="friendly_international",
            external_id="bzz_206695",
        )
        bzz_event = self._make_bzz_event(home_team_api_id=485, away_team_api_id=1150)

        with (
            patch(
                "app.pricing.team_xg._load_national_team_players",
                new_callable=AsyncMock,
            ) as mock_nat,
            patch(
                "app.pricing.team_xg._load_team_players",
                new_callable=AsyncMock,
            ) as mock_club,
            patch(
                "app.pricing.team_xg.MarketXgService",
            ) as mock_svc_cls,
        ):
            # MarketXgService.compute returns a valid result to avoid early return
            mock_market = AsyncMock()
            mock_market.xg_home = 1.2
            mock_market.xg_away = 0.9
            mock_market.xg_source = "market"
            mock_market.last_snapshot_at = None
            mock_svc_cls.return_value.compute = AsyncMock(return_value=mock_market)

            mock_nat.return_value = []

            db = AsyncMock()
            # BzzEvent lookup result
            bzz_result = MagicMock()
            bzz_result.scalar_one_or_none.return_value = bzz_event
            db.execute = AsyncMock(return_value=bzz_result)

            from app.pricing.team_xg import load_match_pricing
            await load_match_pricing(db, fixture)

            # _load_national_team_players must have been called with the IDs from bzz_event
            assert mock_nat.call_count == 2
            call_kwargs = [call.kwargs for call in mock_nat.call_args_list]
            api_ids_used = {kw["national_team_api_id"] for kw in call_kwargs}
            assert 485 in api_ids_used
            assert 1150 in api_ids_used

            # _load_team_players must NOT have been called
            mock_club.assert_not_called()

    @pytest.mark.asyncio
    async def test_club_league_calls_team_players_loader(self):
        """When league is ligue1, _load_team_players is called (unchanged behaviour)."""
        fixture = self._make_fixture(
            league="ligue1",
            external_id="bzz_999",
            home_team="PSG",
            away_team="OM",
        )

        with (
            patch(
                "app.pricing.team_xg._load_national_team_players",
                new_callable=AsyncMock,
            ) as mock_nat,
            patch(
                "app.pricing.team_xg._load_team_players",
                new_callable=AsyncMock,
            ) as mock_club,
            patch(
                "app.pricing.team_xg.MarketXgService",
            ) as mock_svc_cls,
        ):
            mock_market = AsyncMock()
            mock_market.xg_home = 1.5
            mock_market.xg_away = 1.0
            mock_market.xg_source = "market"
            mock_market.last_snapshot_at = None
            mock_svc_cls.return_value.compute = AsyncMock(return_value=mock_market)

            mock_club.return_value = []

            db = AsyncMock()

            from app.pricing.team_xg import load_match_pricing
            await load_match_pricing(db, fixture)

            mock_nat.assert_not_called()
            assert mock_club.call_count == 2

    @pytest.mark.asyncio
    async def test_missing_bzz_event_falls_back_to_team_players(self):
        """If BzzEvent not found, falls back to _load_team_players with a warning."""
        fixture = self._make_fixture(
            league="world_cup_2026",
            external_id="bzz_999999",
        )

        with (
            patch(
                "app.pricing.team_xg._load_national_team_players",
                new_callable=AsyncMock,
            ) as mock_nat,
            patch(
                "app.pricing.team_xg._load_team_players",
                new_callable=AsyncMock,
            ) as mock_club,
            patch(
                "app.pricing.team_xg.MarketXgService",
            ) as mock_svc_cls,
        ):
            mock_market = AsyncMock()
            mock_market.xg_home = 1.0
            mock_market.xg_away = 0.8
            mock_market.xg_source = "market"
            mock_market.last_snapshot_at = None
            mock_svc_cls.return_value.compute = AsyncMock(return_value=mock_market)

            mock_club.return_value = []

            db = AsyncMock()
            # BzzEvent lookup returns None → event not found
            bzz_result = MagicMock()
            bzz_result.scalar_one_or_none.return_value = None
            db.execute = AsyncMock(return_value=bzz_result)

            from app.pricing.team_xg import load_match_pricing
            await load_match_pricing(db, fixture)

            mock_nat.assert_not_called()
            assert mock_club.call_count == 2

    @pytest.mark.asyncio
    async def test_all_four_international_leagues_detected(self):
        """All four INTERNATIONAL_LEAGUES values trigger the national team path."""
        from app.pricing.team_xg import INTERNATIONAL_LEAGUES

        assert "world_cup_2026" in INTERNATIONAL_LEAGUES
        assert "friendly_international" in INTERNATIONAL_LEAGUES
        assert "nations_league_uefa" in INTERNATIONAL_LEAGUES
        assert "nations_league_concacaf" in INTERNATIONAL_LEAGUES
