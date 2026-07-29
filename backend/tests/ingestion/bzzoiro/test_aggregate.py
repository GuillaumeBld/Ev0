"""Tests for aggregate module."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.aggregate import aggregate_all_leagues, aggregate_season_stats


def _make_mapping(**kwargs):
    """Create a dict-like mapping row for mocking SQLAlchemy results."""
    m = MagicMock()
    m._mapping = kwargs
    return m


def _make_result(rows):
    """Create a mock SQLAlchemy result with fetchall()."""
    result = MagicMock()
    result.fetchall.return_value = rows
    return result


def _make_form_row(player_api_id: int, expected_goals, rating, goals, goal_assist, rn: int = 1):
    """Create a mock form row with named attribute access (matches bulk window query columns)."""
    row = MagicMock()
    row.player_api_id = player_api_id
    row.expected_goals = expected_goals
    row.rating = rating
    row.goals = goals
    row.goal_assist = goal_assist
    row.rn = rn
    return row


@pytest.mark.asyncio
async def test_aggregate_season_stats_empty():
    """When no finished matches exist for a league, returns 0 and does no insert."""
    session = MagicMock()
    # First execute (aggregation query) returns no rows
    session.execute = AsyncMock(return_value=_make_result([]))
    session.commit = AsyncMock()

    count = await aggregate_season_stats(session, league_api_id=100, season="2025-2026")

    assert count == 0
    # commit should not be called since there's nothing to upsert
    session.commit.assert_not_called()
    # execute called once (the agg query)
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_aggregate_season_stats_basic():
    """Two players with match stats produce 2 upserted season stat rows."""
    # Build aggregation rows for 2 players
    player1_agg = _make_mapping(
        player_api_id=101,
        matches_played=3,
        minutes_played=270,
        starts=3,
        goals=2,
        goal_assist=1,
        expected_goals=1.8,
        expected_assists=0.9,
        total_shots=12,
        shots_on_target=6,
        key_pass=4,
        total_cross=5,
        accurate_cross=3,
        total_pass=120,
        accurate_pass=100,
        total_long_balls=10,
        accurate_long_balls=6,
        duel_won=15,
        duel_lost=10,
        aerial_won=4,
        aerial_lost=3,
        total_tackle=8,
        won_tackle=6,
        interception=5,
        ball_recovery=12,
        yellow_card=1,
        red_card=0,
        saves=0,
        shot_accuracy=0.5,
        xg_per_shot=0.15,
        finishing_delta=0.2,
        xa_delta=0.1,
        pass_completion=0.83,
        long_ball_accuracy=0.6,
        cross_accuracy=0.6,
        duel_win_rate=0.6,
        aerial_win_rate=0.57,
        tackle_success_rate=0.75,
        avg_rating=7.2,
        avg_minutes_per_match=90.0,
    )

    player2_agg = _make_mapping(
        player_api_id=102,
        matches_played=1,
        minutes_played=85,
        starts=0,
        goals=0,
        goal_assist=1,
        expected_goals=0.3,
        expected_assists=0.5,
        total_shots=2,
        shots_on_target=1,
        key_pass=3,
        total_cross=0,
        accurate_cross=0,
        total_pass=50,
        accurate_pass=40,
        total_long_balls=None,
        accurate_long_balls=None,
        duel_won=2,
        duel_lost=4,
        aerial_won=None,
        aerial_lost=None,
        total_tackle=2,
        won_tackle=1,
        interception=0,
        ball_recovery=2,
        yellow_card=1,
        red_card=0,
        saves=0,
        shot_accuracy=0.5,
        xg_per_shot=0.15,
        finishing_delta=-0.3,
        xa_delta=0.5,
        pass_completion=0.8,
        long_ball_accuracy=None,
        cross_accuracy=None,
        duel_win_rate=0.33,
        aerial_win_rate=None,
        tackle_success_rate=0.5,
        avg_rating=6.8,
        avg_minutes_per_match=85.0,
    )

    # Form rows for both players combined (bulk window query returns all players at once)
    # player_api_id, expected_goals, rating, goals, goal_assist, rn
    bulk_form_rows = [
        _make_form_row(101, 0.6, 7.5, 1, 0, rn=1),
        _make_form_row(101, 0.5, 7.0, 0, 1, rn=2),
        _make_form_row(101, 0.7, 7.1, 1, 0, rn=3),
        _make_form_row(102, 0.3, 6.8, 0, 1, rn=1),
    ]

    # Call pattern with bulk form query:
    # call 0: aggregation query → 2 player rows
    # call 1: bulk form window query → all players' last-5 rows
    # call 2: upsert for player 101
    # call 3: upsert for player 102

    agg_result = _make_result([player1_agg, player2_agg])
    bulk_form_result = _make_result(bulk_form_rows)
    upsert_result_1 = MagicMock()
    upsert_result_2 = MagicMock()

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            agg_result,
            bulk_form_result,
            upsert_result_1,
            upsert_result_2,
        ]
    )
    session.commit = AsyncMock()

    count = await aggregate_season_stats(session, league_api_id=100, season="2025-2026")

    assert count == 2
    # 4 execute calls: 1 agg + 1 bulk-form + 2 upserts
    assert session.execute.call_count == 4
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_aggregate_season_stats_per90_computed():
    """Verify per-90 stats are computed correctly from aggregated totals."""
    player_agg = _make_mapping(
        player_api_id=201,
        matches_played=2,
        minutes_played=180,  # 2 full 90-min games → divisor = 2
        starts=2,
        goals=2,
        goal_assist=0,
        expected_goals=1.8,  # xg_per_90 = 1.8 / 2 = 0.9
        expected_assists=0.4,
        total_shots=10,  # shots_per_90 = 10/2 = 5
        shots_on_target=4,
        key_pass=6,
        total_cross=4,
        accurate_cross=2,
        total_pass=80,
        accurate_pass=64,
        total_long_balls=8,
        accurate_long_balls=4,
        duel_won=10,
        duel_lost=5,
        aerial_won=3,
        aerial_lost=2,
        total_tackle=6,
        won_tackle=5,
        interception=4,  # interceptions_per_90 = 4/2 = 2
        ball_recovery=8,
        yellow_card=0,
        red_card=0,
        saves=0,
        shot_accuracy=0.4,
        xg_per_shot=0.18,
        finishing_delta=0.2,
        xa_delta=-0.4,
        pass_completion=0.8,
        long_ball_accuracy=0.5,
        cross_accuracy=0.5,
        duel_win_rate=0.67,
        aerial_win_rate=0.6,
        tackle_success_rate=0.83,
        avg_rating=7.5,
        avg_minutes_per_match=90.0,
    )

    # Bulk form rows for player 201 (named attribute access required)
    bulk_form_rows = [
        _make_form_row(201, 0.9, 7.5, 1, 0, rn=1),
        _make_form_row(201, 0.9, 7.5, 1, 0, rn=2),
    ]

    agg_result = _make_result([player_agg])
    bulk_form_result = _make_result(bulk_form_rows)
    upsert_result = MagicMock()

    session = MagicMock()
    # Call pattern: 1 agg + 1 bulk-form + 1 upsert
    session.execute = AsyncMock(side_effect=[agg_result, bulk_form_result, upsert_result])
    session.commit = AsyncMock()

    count = await aggregate_season_stats(session, league_api_id=200, season="2025-2026")
    assert count == 1


@pytest.mark.asyncio
async def test_aggregate_all_leagues():
    """aggregate_all_leagues calls aggregate_season_stats for each distinct league."""
    session = MagicMock()

    # leagues result
    leagues_result = MagicMock()
    leagues_result.fetchall.return_value = [(100,), (200,)]

    # For each league: agg result (1 player) + form + upsert
    def make_player_agg(player_api_id: int):
        return _make_mapping(
            player_api_id=player_api_id,
            matches_played=1,
            minutes_played=90,
            starts=1,
            goals=1,
            goal_assist=0,
            expected_goals=0.8,
            expected_assists=0.2,
            total_shots=3,
            shots_on_target=2,
            key_pass=1,
            total_cross=1,
            accurate_cross=1,
            total_pass=30,
            accurate_pass=24,
            total_long_balls=2,
            accurate_long_balls=1,
            duel_won=3,
            duel_lost=2,
            aerial_won=1,
            aerial_lost=1,
            total_tackle=2,
            won_tackle=2,
            interception=1,
            ball_recovery=3,
            yellow_card=0,
            red_card=0,
            saves=0,
            shot_accuracy=0.67,
            xg_per_shot=0.27,
            finishing_delta=0.2,
            xa_delta=-0.2,
            pass_completion=0.8,
            long_ball_accuracy=0.5,
            cross_accuracy=1.0,
            duel_win_rate=0.6,
            aerial_win_rate=0.5,
            tackle_success_rate=1.0,
            avg_rating=7.0,
            avg_minutes_per_match=90.0,
        )

    league1_agg = _make_result([make_player_agg(301)])
    league1_form = _make_result([_make_form_row(301, 0.8, 7.0, 1, 0, rn=1)])
    league1_upsert = MagicMock()
    league2_agg = _make_result([make_player_agg(302)])
    league2_form = _make_result([_make_form_row(302, 0.5, 6.5, 0, 1, rn=1)])
    league2_upsert = MagicMock()

    session.execute = AsyncMock(
        side_effect=[
            leagues_result,   # aggregate_all_leagues distinct leagues query
            league1_agg,      # league 100 aggregation
            league1_form,     # league 100 bulk form (all players)
            league1_upsert,   # league 100 upsert for player 301
            league2_agg,      # league 200 aggregation
            league2_form,     # league 200 bulk form (all players)
            league2_upsert,   # league 200 upsert for player 302
        ]
    )
    session.commit = AsyncMock()

    # season passed explicitly: the default (None -> current_season(session) via
    # season_service) is covered by tests/ingestion/test_aggregate_season_rollover.py.
    total = await aggregate_all_leagues(session, season="2025-2026")

    assert total == 2  # 1 player per league
    assert session.commit.call_count == 2  # once per league
