"""Tests for GET /players/{player_api_id}/breakdown — stats par (saison x competition).

Agrege bzz_player_match_stats (pas bzz_player_season_stats) car la maquette a
besoin de champs absents de la table saison : dispossessed, possession_lost,
was_fouled, total_clearance, aerial_lost. Le mock cible directement la requete
SQL brute (session.execute -> result.mappings().all()), comme c'est la forme
utilisee par l'implementation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _row(
    season_start_year: int = 2025,
    league_api_id: int = 1,
    league_name: str = "Premier League",
    kind: str = "domestic_1",
    matches_played: int = 10,
    minutes_played: int = 900,
    starts: int = 9,
    goals: int = 5,
    goal_assist: int = 3,
    expected_goals: float = 4.2,
    expected_assists: float = 2.1,
    total_shots: int = 20,
    shots_on_target: int = 12,
    key_pass: int = 8,
    total_pass: int = 300,
    accurate_pass: int = 260,
    total_cross: int = 5,
    accurate_cross: int = 2,
    total_long_balls: int = 10,
    accurate_long_balls: int = 7,
    duel_won: int = 15,
    duel_lost: int = 10,
    aerial_won: int = 6,
    aerial_lost: int = 4,
    total_tackle: int = 12,
    won_tackle: int = 9,
    interception: int = 7,
    ball_recovery: int = 20,
    total_clearance: int = 3,
    dispossessed: int = 11,
    possession_lost: int = 25,
    was_fouled: int = 9,
    fouls: int = 6,
    yellow_card: int = 2,
    red_card: int = 0,
    avg_rating: float = 7.3,
) -> dict:
    """Mimics a row produced by result.mappings().all() for the breakdown query."""
    return {
        "season_start_year": season_start_year,
        "league_api_id": league_api_id,
        "league_name": league_name,
        "kind": kind,
        "matches_played": matches_played,
        "minutes_played": minutes_played,
        "starts": starts,
        "goals": goals,
        "goal_assist": goal_assist,
        "expected_goals": expected_goals,
        "expected_assists": expected_assists,
        "total_shots": total_shots,
        "shots_on_target": shots_on_target,
        "key_pass": key_pass,
        "total_pass": total_pass,
        "accurate_pass": accurate_pass,
        "total_cross": total_cross,
        "accurate_cross": accurate_cross,
        "total_long_balls": total_long_balls,
        "accurate_long_balls": accurate_long_balls,
        "duel_won": duel_won,
        "duel_lost": duel_lost,
        "aerial_won": aerial_won,
        "aerial_lost": aerial_lost,
        "total_tackle": total_tackle,
        "won_tackle": won_tackle,
        "interception": interception,
        "ball_recovery": ball_recovery,
        "total_clearance": total_clearance,
        "dispossessed": dispossessed,
        "possession_lost": possession_lost,
        "was_fouled": was_fouled,
        "fouls": fouls,
        "yellow_card": yellow_card,
        "red_card": red_card,
        "avg_rating": avg_rating,
    }


@pytest.mark.asyncio
async def test_breakdown_groups_by_season_and_league():
    """Deux lignes de la requete SQL (2 ligues/2 saisons) -> 2 BreakdownRow,
    sommes correctes, league_name/kind portes depuis `leagues`, triees saison
    desc puis league."""
    from app.api.players import get_player_breakdown

    row_ligue1_2025 = _row(
        season_start_year=2025,
        league_api_id=1,
        league_name="Premier League",
        kind="domestic_1",
        matches_played=10,
        minutes_played=900,
        goals=5,
        dispossessed=11,
        possession_lost=25,
        was_fouled=9,
        total_clearance=3,
        aerial_lost=4,
        avg_rating=7.3,
    )
    row_ucl_2024 = _row(
        season_start_year=2024,
        league_api_id=7,
        league_name="UEFA Champions League",
        kind="uefa_club",
        matches_played=6,
        minutes_played=480,
        goals=2,
        dispossessed=4,
        possession_lost=9,
        was_fouled=3,
        total_clearance=1,
        aerial_lost=2,
        avg_rating=6.9,
    )

    result = MagicMock()
    result.mappings.return_value.all.return_value = [row_ligue1_2025, row_ucl_2024]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    response = await get_player_breakdown(player_api_id=1, session=mock_db)

    assert "rows" in response
    rows = response["rows"]
    assert len(rows) == 2

    # Triees saison desc puis league -> 2025-2026 (Premier League) avant 2024-2025 (UCL)
    row0 = rows[0]
    assert row0.season == "2025-2026"
    assert row0.league_api_id == 1
    assert row0.league_name == "Premier League"
    assert row0.kind == "domestic_1"
    assert row0.matches_played == 10
    assert row0.minutes_played == 900
    assert row0.goals == 5
    assert row0.dispossessed == 11
    assert row0.possession_lost == 25
    assert row0.was_fouled == 9
    assert row0.total_clearance == 3
    assert row0.aerial_lost == 4
    assert row0.avg_rating == pytest.approx(7.3)

    row1 = rows[1]
    assert row1.season == "2024-2025"
    assert row1.league_api_id == 7
    assert row1.league_name == "UEFA Champions League"
    assert row1.kind == "uefa_club"
    assert row1.matches_played == 6
    assert row1.goals == 2
    assert row1.dispossessed == 4

    # La requete a ete faite avec le bon player_api_id en parametre
    call_args = mock_db.execute.call_args
    assert call_args.args[1] == {"player_api_id": 1}


@pytest.mark.asyncio
async def test_breakdown_empty_when_no_match_stats():
    """Joueur sans stats de match -> rows vide, pas d'erreur."""
    from app.api.players import get_player_breakdown

    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    response = await get_player_breakdown(player_api_id=999, session=mock_db)
    assert response == {"rows": []}
