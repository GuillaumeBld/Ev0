"""Tests for 100% player coverage in _load_team_players.

Verifies that players on the team roster (BzzPlayer.current_team_api_id)
but without BzzPlayerSeasonStat rows are still included in the result via
the fallback query, with zero stats and has_bzz_stats=False.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bzz_team(api_id: int = 10, name: str = "Test FC") -> MagicMock:
    team = MagicMock()
    team.id = 1
    team.api_id = api_id
    team.name = name
    return team


def _make_bzz_player(
    api_id: int,
    name: str,
    position: str = "F",
    current_team_api_id: int = 10,
) -> MagicMock:
    player = MagicMock()
    player.api_id = api_id
    player.name = name
    player.position = position
    player.current_team_api_id = current_team_api_id
    return player


def _make_bzz_season_stat(
    player_api_id: int,
    minutes_played: int = 900,
    xg_per_90: float = 0.25,
    shot_accuracy: float = 0.4,
    xg_per_shot: float = 0.12,
    avg_rating: float = 7.0,
    key_pass_per_90: float = 0.8,
    xa_per_90: float = 0.15,
    accurate_cross_per_90: float = 0.3,
    form_xg_5: float = 1.2,
    expected_goals: float = 2.5,
    goals: int = 3,
) -> MagicMock:
    stat = MagicMock()
    stat.player_api_id = player_api_id
    stat.minutes_played = minutes_played
    stat.xg_per_90 = xg_per_90
    stat.shot_accuracy = shot_accuracy
    stat.xg_per_shot = xg_per_shot
    stat.avg_rating = avg_rating
    stat.key_pass_per_90 = key_pass_per_90
    stat.xa_per_90 = xa_per_90
    stat.accurate_cross_per_90 = accurate_cross_per_90
    stat.form_xg_5 = form_xg_5
    stat.expected_goals = expected_goals
    stat.goals = goals
    stat.matches_played = 10
    stat.starts = 8
    stat.shots_on_target_per_90 = 0.0
    stat.expected_assists = 1.2
    stat.form_assists_5 = None
    stat.npxg_total = 0.0
    return stat


def _build_session(
    team: MagicMock,
    stat_rows: list,
    roster_players: list,
) -> AsyncMock:
    """Build a mock async session with 3 execute calls:

    1. team lookup → scalar_one_or_none returns team
    2. season-stats JOIN → all() returns stat_rows
    3. fallback roster query → scalars().all() returns roster_players
    """
    session = AsyncMock()

    # Execute 1: team lookup
    team_exec = MagicMock()
    team_exec.scalar_one_or_none.return_value = team

    # Execute 2: season stats JOIN
    stats_exec = MagicMock()
    stats_exec.all.return_value = stat_rows

    # Execute 3: fallback roster
    roster_scalars = MagicMock()
    roster_scalars.all.return_value = roster_players
    roster_exec = MagicMock()
    roster_exec.scalars.return_value = roster_scalars

    session.execute = AsyncMock(
        side_effect=[team_exec, stats_exec, roster_exec]
    )
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_team_players_includes_players_without_stats():
    """Players with no BzzPlayerSeasonStat must still appear with zero stats."""
    from app.pricing.team_xg import _load_team_players

    team = _make_bzz_team(api_id=10, name="Test FC")

    # Player WITH stats
    player_with_stats = _make_bzz_player(api_id=101, name="Alice Forward", position="F")
    stat = _make_bzz_season_stat(player_api_id=101)
    stat_row = (stat, player_with_stats)

    # Player WITHOUT stats (forward — should appear)
    player_no_stats = _make_bzz_player(api_id=202, name="Bob Striker", position="F")

    session = _build_session(
        team=team,
        stat_rows=[stat_row],
        roster_players=[player_with_stats, player_no_stats],
    )

    result = await _load_team_players(session, "Test FC", "ligue_1")

    assert len(result) == 2

    names = [p["name"] for p in result]
    assert "Alice Forward" in names
    assert "Bob Striker" in names

    bob = next(p for p in result if p["name"] == "Bob Striker")
    assert bob["matches_played"] == 0
    assert bob["xg"] == 0.0
    assert bob["has_bzz_stats"] is False
    assert bob["rating"] == 0.0

    # Player with stats: "rating" (not "avg_rating") must equal avg_rating / 10.0
    alice = next(p for p in result if p["name"] == "Alice Forward")
    assert "rating" in alice
    assert alice["rating"] == pytest.approx(stat.avg_rating / 10.0)


@pytest.mark.asyncio
async def test_load_team_players_skips_gk_in_fallback():
    """GK players must be excluded even when added via the fallback query."""
    from app.pricing.team_xg import _load_team_players

    team = _make_bzz_team(api_id=10, name="Test FC")

    # GK player without stats
    gk_player = _make_bzz_player(api_id=301, name="Carlos Keeper", position="G")

    session = _build_session(
        team=team,
        stat_rows=[],  # no season stats at all
        roster_players=[gk_player],
    )

    result = await _load_team_players(session, "Test FC", "ligue_1")

    assert result == []


@pytest.mark.asyncio
async def test_load_team_players_no_duplicate_in_fallback():
    """A player already loaded via season stats must NOT appear twice."""
    from app.pricing.team_xg import _load_team_players

    team = _make_bzz_team(api_id=10, name="Test FC")

    # Player WITH stats
    player = _make_bzz_player(api_id=101, name="Alice Forward", position="F")
    stat = _make_bzz_season_stat(player_api_id=101)
    stat_row = (stat, player)

    # Same player appears in roster fallback
    session = _build_session(
        team=team,
        stat_rows=[stat_row],
        roster_players=[player],  # same player, same api_id
    )

    result = await _load_team_players(session, "Test FC", "ligue_1")

    assert len(result) == 1
    assert result[0]["name"] == "Alice Forward"
    assert result[0]["has_bzz_stats"] is True
