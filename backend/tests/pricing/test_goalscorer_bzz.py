"""Tests for bzz_player_season_stats integration in pricing engine.

Covers:
  1. _load_team_players returns [] when no BzzTeam found
  2. _load_team_players returns player dicts with correct fields when team + stats exist
  3. Quality multiplier formula (goalscorer.py)
  4. Creation multiplier formula (assist.py)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pricing.assist import calculate_creation_multiplier
from app.pricing.goalscorer import calculate_quality_multiplier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_no_team() -> AsyncMock:
    """Session that returns no BzzTeam for the team lookup."""
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    session.execute.return_value = execute_result
    return session


def _make_bzz_team(api_id: int = 10, name: str = "Paris Saint-Germain") -> MagicMock:
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
    return stat


# ---------------------------------------------------------------------------
# Tests for _load_team_players
# ---------------------------------------------------------------------------


class TestLoadTeamPlayersEmpty:
    @pytest.mark.asyncio
    async def test_load_team_players_empty(self):
        """When no BzzTeam is found for team_name, returns empty list."""
        from app.pricing.team_xg import _load_team_players

        session = _make_session_no_team()
        result = await _load_team_players(session, "Unknown FC", "ligue_1")
        assert result == []


class TestLoadTeamPlayersBasic:
    @pytest.mark.asyncio
    async def test_load_team_players_basic(self):
        """With 1 team + 2 players with season stats, returns 2 player dicts with correct fields."""
        from app.pricing.team_xg import _load_team_players

        team = _make_bzz_team(api_id=10, name="Paris Saint-Germain")

        player1 = _make_bzz_player(api_id=101, name="Kylian Mbappé", position="F")
        stat1 = _make_bzz_season_stat(
            player_api_id=101,
            minutes_played=900,
            xg_per_90=0.50,
            shot_accuracy=0.55,
            xg_per_shot=0.18,
            avg_rating=8.2,
            key_pass_per_90=1.2,
            xa_per_90=0.20,
            accurate_cross_per_90=0.1,
            form_xg_5=2.0,
            expected_goals=5.0,
            goals=6,
        )

        player2 = _make_bzz_player(api_id=102, name="Ousmane Dembélé", position="M")
        stat2 = _make_bzz_season_stat(
            player_api_id=102,
            minutes_played=720,
            xg_per_90=0.20,
            shot_accuracy=0.38,
            xg_per_shot=0.10,
            avg_rating=7.5,
            key_pass_per_90=1.5,
            xa_per_90=0.25,
            accurate_cross_per_90=0.6,
            form_xg_5=0.8,
            expected_goals=1.6,
            goals=2,
        )

        # Rows returned by the JOIN query: (BzzPlayerSeasonStat, BzzPlayer)
        row1 = (stat1, player1)
        row2 = (stat2, player2)

        session = AsyncMock()

        # First execute: team lookup → returns team
        team_exec_result = MagicMock()
        team_exec_result.scalar_one_or_none.return_value = team

        # Second execute: player season stats JOIN → returns rows
        players_exec_result = MagicMock()
        players_exec_result.all.return_value = [row1, row2]

        # Third execute: fallback roster query → same players (already in seen_ids, no duplicates added)
        roster_scalars = MagicMock()
        roster_scalars.all.return_value = [player1, player2]
        roster_exec_result = MagicMock()
        roster_exec_result.scalars.return_value = roster_scalars

        session.execute = AsyncMock(side_effect=[team_exec_result, players_exec_result, roster_exec_result])

        result = await _load_team_players(session, "Paris Saint-Germain", "ligue_1")

        assert len(result) == 2

        # Check player 1 fields
        p1 = next(p for p in result if p["name"] == "Kylian Mbappé")
        assert p1["minutes_played"] == 900
        assert p1["xg_per_90"] == 0.50
        assert p1["shot_accuracy"] == 0.55
        assert p1["xg_per_shot"] == 0.18
        assert abs(p1["avg_rating"] - 8.2) < 1e-6  # brut (0-10)
        assert p1["key_pass_per_90"] == 1.2
        assert p1["xa_per_90"] == 0.20
        assert p1["accurate_cross_per_90"] == 0.1
        assert p1["form_xg_5"] == 2.0
        assert abs(p1["finishing_delta"] - 1.0) < 1e-6  # goals(6) - xG(5.0)

        # Check player 2 fields
        p2 = next(p for p in result if p["name"] == "Ousmane Dembélé")
        assert p2["minutes_played"] == 720
        assert abs(p2["avg_rating"] - 7.5) < 1e-6  # brut (0-10)
        assert p2["xa_per_90"] == 0.25
        assert abs(p2["finishing_delta"] - 0.4) < 1e-6  # goals(2) - xG(1.6)

        assert p1["has_bzz_stats"] is True
        assert p2["has_bzz_stats"] is True


# ── Finishing multiplier, conversion, λ top-down tests ──────────────────────

from app.pricing.goalscorer import (
    calculate_finishing_multiplier,
    calculate_conversion_rate,
    calculate_goalscorer_lambda,
)


class TestFinishingMultiplier:
    def test_average_fw_returns_near_one(self):
        stats = {"shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert 0.95 <= mult <= 1.05

    def test_elite_fw_clamped_to_max(self):
        stats = {"shot_accuracy": 0.80, "xg_per_shot": 0.30, "avg_rating": 9.0}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert mult == 1.50

    def test_poor_fw_clamped_to_min(self):
        stats = {"shot_accuracy": 0.05, "xg_per_shot": 0.01, "avg_rating": 4.0}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert mult == 0.70

    def test_none_stats_return_min(self):
        stats = {"shot_accuracy": None, "xg_per_shot": None, "avg_rating": None}
        mult = calculate_finishing_multiplier(stats, "MF")
        assert mult == 0.70

    def test_unknown_position_uses_fallback(self):
        stats = {"shot_accuracy": 0.37, "xg_per_shot": 0.10, "avg_rating": 6.8}
        mult = calculate_finishing_multiplier(stats, None)
        assert 0.95 <= mult <= 1.05


class TestConversionRate:
    def test_below_min_matches_returns_one(self):
        stats = {"matches_played": 3, "goals": 5, "npxg_total": 2.0}
        assert calculate_conversion_rate(stats) == 1.0

    def test_overperformer_clamped(self):
        stats = {"matches_played": 10, "goals": 15, "npxg_total": 5.0}
        assert calculate_conversion_rate(stats) == 1.40

    def test_underperformer_clamped(self):
        stats = {"matches_played": 10, "goals": 1, "npxg_total": 8.0}
        assert calculate_conversion_rate(stats) == 0.75

    def test_zero_xg_returns_one(self):
        stats = {"matches_played": 10, "goals": 0, "npxg_total": 0.0}
        assert calculate_conversion_rate(stats) == 1.0

    def test_normal_conversion(self):
        stats = {"matches_played": 10, "goals": 8, "npxg_total": 8.0}
        assert calculate_conversion_rate(stats) == pytest.approx(1.0, abs=0.01)


class TestGoalscorerLambda:
    def test_basic_lambda(self):
        lam = calculate_goalscorer_lambda(
            share=0.30, lambda_team=1.5, finishing_mult=1.0,
            conversion=1.0, mins_ratio=1.0, is_pen_taker=False,
        )
        assert lam == pytest.approx(0.45, abs=0.001)

    def test_pen_taker_bonus_added(self):
        lam_no_pen = calculate_goalscorer_lambda(0.20, 1.5, 1.0, 1.0, 1.0, False)
        lam_pen    = calculate_goalscorer_lambda(0.20, 1.5, 1.0, 1.0, 1.0, True)
        assert lam_pen > lam_no_pen
        # bonus ≈ PEN_CONVERSION(0.78) × PENS_PER_MATCH(0.10) × 1.0 = 0.078
        assert lam_pen - lam_no_pen == pytest.approx(0.078, abs=0.001)

    def test_lambda_clamped_to_max(self):
        lam = calculate_goalscorer_lambda(1.0, 10.0, 1.5, 1.4, 1.0, False)
        assert lam == 3.0  # CLAMP_LAMBDA_MAX

    def test_lambda_clamped_to_min(self):
        lam = calculate_goalscorer_lambda(0.0, 0.0, 0.70, 0.75, 0.0, False)
        assert lam == 0.01  # CLAMP_LAMBDA_MIN
