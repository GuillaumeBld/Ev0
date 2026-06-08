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
