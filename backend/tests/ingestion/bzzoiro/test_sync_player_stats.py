"""Tests for sync_player_stats module."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.sync_player_stats import (
    compute_derived_metrics,
    sync_player_stats_for_player,
)


def test_compute_derived_metrics_normal():
    row = {
        "total_shots": 5,
        "shots_on_target": 3,
        "expected_goals": 1.5,
        "goals": 2,
        "goal_assist": 1,
        "expected_assists": 0.8,
        "total_pass": 40,
        "accurate_pass": 32,
        "total_long_balls": None,
        "accurate_long_balls": None,
        "total_cross": None,
        "accurate_cross": None,
        "duel_won": 3,
        "duel_lost": 2,
        "aerial_won": None,
        "aerial_lost": None,
        "won_tackle": 4,
        "total_tackle": 5,
    }
    result = compute_derived_metrics(row)

    assert result["shot_accuracy"] == pytest.approx(0.6)
    assert result["xg_per_shot"] == pytest.approx(0.3)
    assert result["finishing_delta"] == pytest.approx(0.5)
    assert result["xa_delta"] == pytest.approx(0.2)
    assert result["pass_completion"] == pytest.approx(0.8)
    assert result["duel_win_rate"] == pytest.approx(0.6)
    assert result["tackle_success_rate"] == pytest.approx(0.8)
    # None denominators
    assert result["long_ball_accuracy"] is None
    assert result["cross_accuracy"] is None
    assert result["aerial_win_rate"] is None


def test_compute_derived_metrics_zero_denominator():
    row = {
        "total_shots": 0,
        "shots_on_target": None,
        "expected_goals": None,
        "goals": None,
        "goal_assist": None,
        "expected_assists": None,
        "total_pass": 0,
        "accurate_pass": None,
        "total_long_balls": None,
        "accurate_long_balls": None,
        "total_cross": None,
        "accurate_cross": None,
        "duel_won": 0,
        "duel_lost": 0,
        "aerial_won": None,
        "aerial_lost": None,
        "won_tackle": None,
        "total_tackle": 0,
    }
    result = compute_derived_metrics(row)

    assert result["shot_accuracy"] is None
    assert result["xg_per_shot"] is None
    assert result["finishing_delta"] is None
    assert result["xa_delta"] is None
    assert result["pass_completion"] is None
    assert result["long_ball_accuracy"] is None
    assert result["cross_accuracy"] is None
    assert result["duel_win_rate"] is None
    assert result["aerial_win_rate"] is None
    assert result["tackle_success_rate"] is None


@pytest.mark.asyncio
async def test_sync_player_stats_for_player():
    """The API returns stats without player identity; player is known from context."""
    stat_rows = [
        {
            "event": {"id": 500, "api_id": 999},
            "minutes_played": 90,
            "rating": 7.5,
            "touches": 55,
            "goals": 1,
            "goal_assist": 0,
            "expected_goals": 0.8,
            "expected_assists": 0.2,
            "total_shots": 4,
            "shots_on_target": 2,
            "total_pass": 30,
            "accurate_pass": 24,
            "key_pass": 1,
            "total_long_balls": 3,
            "accurate_long_balls": 2,
            "total_cross": 2,
            "accurate_cross": 1,
            "duel_won": 5,
            "duel_lost": 3,
            "aerial_won": 2,
            "aerial_lost": 1,
            "total_tackle": 4,
            "won_tackle": 3,
            "total_clearance": 0,
            "interception": 1,
            "ball_recovery": 4,
            "yellow_card": 0,
            "red_card": 0,
            "fouls": 1,
            "was_fouled": 2,
            "dispossessed": 1,
            "possession_lost": 3,
            "saves": 0,
            "goals_conceded": 0,
        },
        {
            "event": {"id": 501, "api_id": 998},
            "minutes_played": 85,
            "rating": 6.8,
            "touches": 42,
            "goals": 0,
            "goal_assist": 1,
            "expected_goals": 0.3,
            "expected_assists": 0.5,
            "total_shots": 2,
            "shots_on_target": 1,
            "total_pass": 50,
            "accurate_pass": 40,
            "key_pass": 3,
            "total_long_balls": None,
            "accurate_long_balls": None,
            "total_cross": None,
            "accurate_cross": None,
            "duel_won": 2,
            "duel_lost": 4,
            "aerial_won": None,
            "aerial_lost": None,
            "total_tackle": 2,
            "won_tackle": 1,
            "total_clearance": 2,
            "interception": 0,
            "ball_recovery": 2,
            "yellow_card": 1,
            "red_card": 0,
            "fouls": 2,
            "was_fouled": 0,
            "dispossessed": 2,
            "possession_lost": 5,
            "saves": 0,
            "goals_conceded": 0,
        },
    ]

    client = MagicMock()
    client.get_all = AsyncMock(return_value=stat_rows)

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_player_stats_for_player(
        session, client, player_api_id=101, player_internal_id=42
    )
    assert count == 2
    client.get_all.assert_called_once_with("/api/player-stats/", {"player_id": 42})


@pytest.mark.asyncio
async def test_sync_player_stats_for_player_skips_missing_event_api_id():
    """Rows without event.api_id are silently skipped."""
    stat_rows = [
        {"event": {}, "minutes_played": 90, "rating": 7.0},  # no api_id in event
        {"event": {"api_id": 999}, "minutes_played": 45, "rating": 6.5},
    ]

    client = MagicMock()
    client.get_all = AsyncMock(return_value=stat_rows)

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_player_stats_for_player(session, client, player_api_id=101, player_internal_id=42)
    assert count == 1


def test_compute_derived_metrics_partial_duel():
    row = {
        "total_shots": None, "shots_on_target": None,
        "expected_goals": None, "goals": None,
        "goal_assist": None, "expected_assists": None,
        "total_pass": None, "accurate_pass": None,
        "total_long_balls": None, "accurate_long_balls": None,
        "total_cross": None, "accurate_cross": None,
        "duel_won": 5, "duel_lost": None,
        "aerial_won": None, "aerial_lost": 2,
        "won_tackle": None, "total_tackle": None,
    }
    result = compute_derived_metrics(row)
    assert result["duel_win_rate"] is None
    assert result["aerial_win_rate"] is None
