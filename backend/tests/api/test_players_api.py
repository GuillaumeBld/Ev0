"""Tests for the Bzzoiro-powered players API endpoints."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException


def _make_player(api_id: int = 1, name: str = "Player One", position: str = "F") -> MagicMock:
    p = MagicMock()
    p.api_id = api_id
    p.name = name
    p.short_name = name.split()[-1]
    p.position = position
    p.nationality = "French"
    p.date_of_birth = None
    p.height = 180
    p.jersey_number = 10
    p.market_value = 50_000_000
    p.current_team_api_id = 100
    return p


def _make_season_stat(player_api_id: int = 1) -> MagicMock:
    s = MagicMock()
    s.player_api_id = player_api_id
    s.season = "2025-2026"
    s.league_api_id = 42
    s.matches_played = 20
    s.minutes_played = 1800
    s.starts = 18
    s.goals = 8
    s.goal_assist = 4
    s.total_shots = 50
    s.shots_on_target = 25
    s.key_pass = 30
    s.expected_goals = 6.5
    s.expected_assists = 3.2
    s.xg_per_90 = 0.33
    s.xa_per_90 = 0.16
    s.shots_per_90 = 2.5
    s.shots_on_target_per_90 = 1.25
    s.key_pass_per_90 = 1.5
    s.avg_rating = 7.4
    s.form_xg_5 = 1.8
    s.form_rating_5 = 7.6
    s.form_goals_5 = 3
    s.form_assists_5 = 1
    s.rating_trend = 0.1
    s.shot_accuracy = 0.5
    s.xg_per_shot = 0.13
    s.finishing_delta = 0.05
    s.xa_delta = 0.02
    s.pass_completion = 0.82
    s.duel_win_rate = 0.55
    s.aerial_win_rate = 0.4
    s.tackle_success_rate = 0.6
    s.avg_minutes_per_match = 90.0
    s.starts_pct = 0.9
    return s


def _make_match_stat(
    player_api_id: int = 1,
    event_api_id: int = 456,
    is_home: bool = True,
    team_api_id: int = 100,
) -> MagicMock:
    ms = MagicMock()
    ms.player_api_id = player_api_id
    ms.event_api_id = event_api_id
    ms.team_api_id = team_api_id
    ms.is_home = is_home
    ms.minutes_played = 90
    ms.goals = 1
    ms.goal_assist = 0
    ms.expected_goals = 0.73
    ms.rating = 8.2
    ms.shots_on_target = 3
    ms.key_pass = 2
    return ms


# ---------------------------------------------------------------------------
# Test: list players — empty result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_players_empty():
    from app.api.players import list_players

    mock_db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result)

    response = await list_players(
        session=mock_db,
        league_api_id=None,
        team_api_id=None,
        position=None,
        min_minutes=0,
        season="2025-2026",
        sort_by="xg_per_90",
        sort_order="desc",
        limit=50,
        offset=0,
    )
    assert response == []


# ---------------------------------------------------------------------------
# Test: list players — 2 players with season stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_players_basic():
    from app.api.players import list_players

    p1 = _make_player(api_id=1, name="Alpha Player")
    p2 = _make_player(api_id=2, name="Beta Player", position="M")
    s1 = _make_season_stat(player_api_id=1)
    s2 = _make_season_stat(player_api_id=2)
    s2.xg_per_90 = 0.18

    mock_db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        (p1, s1, "Real Madrid"),
        (p2, s2, "Barcelona"),
    ]
    mock_db.execute = AsyncMock(return_value=result)

    response = await list_players(
        session=mock_db,
        league_api_id=None,
        team_api_id=None,
        position=None,
        min_minutes=0,
        season="2025-2026",
        sort_by="xg_per_90",
        sort_order="desc",
        limit=50,
        offset=0,
    )

    assert len(response) == 2

    item0 = response[0]
    assert item0["player_api_id"] == 1
    assert item0["name"] == "Alpha Player"
    assert item0["position"] == "F"
    assert item0["team_name"] == "Real Madrid"
    assert item0["nationality"] == "French"
    assert item0["xg_per_90"] == 0.33
    assert item0["matches_played"] == 20
    assert item0["minutes_played"] == 1800
    assert item0["season"] == "2025-2026"

    item1 = response[1]
    assert item1["player_api_id"] == 2
    assert item1["xg_per_90"] == 0.18


# ---------------------------------------------------------------------------
# Test: get player — 404 when not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_not_found():
    from app.api.players import get_player

    mock_db = AsyncMock()
    result = MagicMock()
    result.first.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc_info:
        await get_player(player_api_id=999, session=mock_db, season="2025-2026")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Player not found"


# ---------------------------------------------------------------------------
# Test: get player — full detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_detail():
    from app.api.players import get_player

    player = _make_player(api_id=1, name="Kylian Mbappe")
    season_stat = _make_season_stat(player_api_id=1)

    ms1 = _make_match_stat(player_api_id=1, event_api_id=100, is_home=True)
    ms2 = _make_match_stat(player_api_id=1, event_api_id=101, is_home=False)

    dt1 = datetime(2025, 12, 15, 20, 45, tzinfo=timezone.utc)
    dt2 = datetime(2025, 12, 8, 18, 0, tzinfo=timezone.utc)

    # Three execute calls:
    # 1. player + team_name
    # 2. season stats
    # 3. recent matches

    player_result = MagicMock()
    player_result.first.return_value = (player, "Real Madrid")

    season_result = MagicMock()
    season_scalars = MagicMock()
    season_scalars.first.return_value = season_stat
    season_result.scalars.return_value = season_scalars

    recent_result = MagicMock()
    recent_result.all.return_value = [
        (ms1, dt1, 100, 200, "Real Madrid", "Barcelona"),
        (ms2, dt2, 100, 200, "Real Madrid", "Atletico"),
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    assert response["player_api_id"] == 1
    assert response["name"] == "Kylian Mbappe"
    assert response["team_name"] == "Real Madrid"
    assert response["nationality"] == "French"
    assert response["height"] == 180

    # Season stats
    ss = response["season_stats"]
    assert ss is not None
    assert ss.xg_per_90 == 0.33
    assert ss.matches_played == 20
    assert ss.form_xg_5 == 1.8

    # Recent matches
    recent = response["recent_matches"]
    assert len(recent) == 2

    m0 = recent[0]
    assert m0.event_api_id == 100
    assert m0.event_date == dt1
    assert m0.is_home is True
    assert m0.opponent == "Barcelona"
    assert m0.goals == 1
    assert m0.expected_goals == 0.73
    assert m0.rating == 8.2
    assert m0.shots_on_target == 3

    m1 = recent[1]
    assert m1.event_api_id == 101
    assert m1.is_home is False
    assert m1.opponent == "Real Madrid"
