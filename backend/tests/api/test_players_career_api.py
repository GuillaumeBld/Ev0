"""Tests for GET /players/{player_api_id}/career (brique 3).

Endpoint returns:
  - career: one entry per season (aggregated across competitions), each
    with a `competitions` sub-list giving the per-competition detail
  - blended_rhythm: from app.pricing.career_blend.blended_rhythm(), or None
  - player: {player_api_id, name}

404 when the player itself does not exist (consistent with GET /players/{id}).
A player that exists but has zero career rows is NOT a 404 — it returns
career=[] and blended_rhythm=None (blended_rhythm() already handles "no
data at all" gracefully by returning None, so an existing player with no
career/no bzz coverage is a normal, valid state).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.player_career import PlayerCareerSeason
from app.pricing.career_blend import BlendedRhythm


def _career_row(
    season: str = "24/25",
    season_start_year: int = 2024,
    competition_code: str = "GB1",
    competition: str = "Premier League",
    appearances: int = 30,
    goals: int = 10,
    assists: int = 5,
    minutes: int = 2500,
) -> PlayerCareerSeason:
    return PlayerCareerSeason(
        player_api_id=1,
        season=season,
        season_start_year=season_start_year,
        competition_code=competition_code,
        competition=competition,
        appearances=appearances,
        goals=goals,
        assists=assists,
        minutes=minutes,
    )


# ---------------------------------------------------------------------------
# 404 when the player does not exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_career_404_when_player_not_found():
    from app.api.players import get_player_career

    player_result = MagicMock()
    player_result.first.return_value = None
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=player_result)

    with pytest.raises(HTTPException) as exc_info:
        await get_player_career(player_api_id=999, session=mock_db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Player not found"


# ---------------------------------------------------------------------------
# Player exists, no career rows at all -> career=[], blended_rhythm=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_career_empty_when_no_career_rows(monkeypatch):
    from app.api import players as players_mod

    player_row = MagicMock()
    player_row.api_id = 1
    player_row.name = "Kylian Mbappe"
    player_result = MagicMock()
    player_result.first.return_value = player_row

    career_result = MagicMock()
    career_result.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[player_result, career_result])

    called_with: dict = {}

    async def fake_blended_rhythm(session, player_api_id):
        called_with["player_api_id"] = player_api_id
        return None

    monkeypatch.setattr(players_mod, "blended_rhythm", fake_blended_rhythm)

    response = await players_mod.get_player_career(player_api_id=1, session=mock_db)

    assert response["player"] == {"player_api_id": 1, "name": "Kylian Mbappe"}
    assert response["career"] == []
    assert response["blended_rhythm"] is None
    assert called_with["player_api_id"] == 1


# ---------------------------------------------------------------------------
# Full detail: multiple seasons, multiple competitions per season, blended
# rhythm present -> aggregation + sort + sub-list all correct.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_career_full_detail(monkeypatch):
    from app.api import players as players_mod

    player_row = MagicMock()
    player_row.api_id = 1
    player_row.name = "Kylian Mbappe"
    player_result = MagicMock()
    player_result.first.return_value = player_row

    rows = [
        # 24/25 — two competitions (Premier League + Champions League)
        _career_row(
            season="24/25", season_start_year=2024, competition_code="GB1",
            competition="Premier League", appearances=30, goals=10, assists=5, minutes=2500,
        ),
        _career_row(
            season="24/25", season_start_year=2024, competition_code="CL",
            competition="Champions League", appearances=8, goals=4, assists=2, minutes=650,
        ),
        # 23/24 — single competition, older season
        _career_row(
            season="23/24", season_start_year=2023, competition_code="FR1",
            competition="Ligue 1", appearances=25, goals=20, assists=8, minutes=2100,
        ),
    ]
    career_result = MagicMock()
    career_result.scalars.return_value.all.return_value = rows

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[player_result, career_result])

    rhythm = BlendedRhythm(
        goal_rate_per_90=0.42, assist_rate_per_90=0.21, seasons_used=3, has_career=True,
    )

    async def fake_blended_rhythm(session, player_api_id):
        return rhythm

    monkeypatch.setattr(players_mod, "blended_rhythm", fake_blended_rhythm)

    response = await players_mod.get_player_career(player_api_id=1, session=mock_db)

    assert response["player"] == {"player_api_id": 1, "name": "Kylian Mbappe"}

    career = response["career"]
    assert len(career) == 2  # 2 distinct seasons

    # Sorted by season_start_year descending -> 24/25 first
    s0 = career[0]
    assert s0.season == "24/25"
    assert s0.season_start_year == 2024
    # Aggregated totals across the 2 competitions of that season
    assert s0.appearances == 38
    assert s0.goals == 14
    assert s0.assists == 7
    assert s0.minutes == 3150
    assert len(s0.competitions) == 2
    comp_codes = {c.competition_code for c in s0.competitions}
    assert comp_codes == {"GB1", "CL"}
    gb1 = next(c for c in s0.competitions if c.competition_code == "GB1")
    assert gb1.competition == "Premier League"
    assert gb1.appearances == 30
    assert gb1.goals == 10
    assert gb1.assists == 5
    assert gb1.minutes == 2500

    s1 = career[1]
    assert s1.season == "23/24"
    assert s1.season_start_year == 2023
    assert s1.appearances == 25
    assert s1.goals == 20
    assert s1.assists == 8
    assert s1.minutes == 2100
    assert len(s1.competitions) == 1

    br = response["blended_rhythm"]
    assert br is not None
    assert br.goal_rate_per_90 == pytest.approx(0.42)
    assert br.assist_rate_per_90 == pytest.approx(0.21)
    assert br.seasons_used == 3
    assert br.has_career is True


# ---------------------------------------------------------------------------
# Seasons with a missing (None) season_start_year sort last, not first.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_career_none_season_start_year_sorts_last(monkeypatch):
    from app.api import players as players_mod

    player_row = MagicMock()
    player_row.api_id = 1
    player_row.name = "Test Player"
    player_result = MagicMock()
    player_result.first.return_value = player_row

    rows = [
        _career_row(season="unknown", season_start_year=None, competition_code="X1", appearances=1, goals=0, assists=0, minutes=90),
        _career_row(season="24/25", season_start_year=2024, competition_code="GB1", appearances=30, goals=10, assists=5, minutes=2500),
    ]
    career_result = MagicMock()
    career_result.scalars.return_value.all.return_value = rows

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[player_result, career_result])

    async def fake_blended_rhythm(session, player_api_id):
        return None

    monkeypatch.setattr(players_mod, "blended_rhythm", fake_blended_rhythm)

    response = await players_mod.get_player_career(player_api_id=1, session=mock_db)

    seasons = [s.season for s in response["career"]]
    assert seasons == ["24/25", "unknown"]


# ---------------------------------------------------------------------------
# blended_rhythm() is called with the exact player_api_id from the path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_career_calls_blended_rhythm_with_correct_id(monkeypatch):
    from app.api import players as players_mod

    player_row = MagicMock()
    player_row.api_id = 42
    player_row.name = "Someone"
    player_result = MagicMock()
    player_result.first.return_value = player_row

    career_result = MagicMock()
    career_result.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[player_result, career_result])

    calls = []

    async def fake_blended_rhythm(session, player_api_id):
        calls.append((session, player_api_id))
        return None

    monkeypatch.setattr(players_mod, "blended_rhythm", fake_blended_rhythm)

    await players_mod.get_player_career(player_api_id=42, session=mock_db)

    assert len(calls) == 1
    assert calls[0][0] is mock_db
    assert calls[0][1] == 42
