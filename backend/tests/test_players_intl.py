"""/players sur les competitions internationales de clubs (C1, C3, C4, ...).

Aucun club n'a `league_api_id` egal a 7/8/83/90/32/33 dans le referentiel
canonique : `team_ids_for_league(7)` rend toujours []. Avant ce fix, cliquer
la Ligue des champions dans l'explorateur de joueurs rendait donc une liste
VIDE alors que les stats de C1 existent dans `bzz_player_season_stats`. Le
fix resout les joueurs par presence d'une ligne de stats dans la competition
demandee, et restreint les stats affichees a cette seule competition (pas
l'agregat toutes-competitions).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat


def _player(api_id: int = 1, name: str = "Alpha Player", position: str = "F",
            team: str = "Real Madrid") -> BzzPlayer:
    return BzzPlayer(
        api_id=api_id, internal_id=api_id, name=name,
        short_name=name.split()[-1], position=position,
        nationality="French", height=180, jersey_number=10,
        market_value=50_000_000, current_team_api_id=100,
        current_team_name=team,
    )


def _season_stat(player_api_id: int, league_api_id: int, goals: int,
                  season: str = "2025-2026") -> BzzPlayerSeasonStat:
    return BzzPlayerSeasonStat(
        player_api_id=player_api_id, season=season, league_api_id=league_api_id,
        matches_played=6, minutes_played=540, starts=6,
        goals=goals, goal_assist=1, total_shots=15, shots_on_target=8,
        key_pass=5, expected_goals=4.1, expected_assists=0.9,
        avg_rating=7.2, form_xg_5=1.1, form_rating_5=7.3, form_goals_5=2,
        form_assists_5=0, rating_trend=0.0,
    )


@pytest.mark.asyncio
async def test_club_comp_lists_players_by_stats():
    """league_api_id=7 (Ligue des champions) : le joueur n'a aucun club en
    league 7 (aucun club n'en a), mais une ligne de stats C1 existe -> il doit
    apparaitre, avec les stats DE CETTE COMPETITION (goals=5), pas l'agregat.
    """
    import app.api.players as mod
    from app.api.players import list_players

    p1 = _player(api_id=1)
    s_ligue1 = _season_stat(player_api_id=1, league_api_id=61, goals=2)  # agregat domestique
    s_ucl = _season_stat(player_api_id=1, league_api_id=7, goals=5)     # C1 seule

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1]
    # La requete all_stats doit etre restreinte a league_api_id=7 : le mock ne
    # rend que la ligne C1, comme le ferait un vrai filtre SQL.
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s_ucl]
    crest_res = MagicMock()
    crest_res.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res, crest_res])

    async def _team_ids_interdits(session, league_api_id, season):
        raise AssertionError(
            "team_ids_for_league ne doit pas etre appele pour une "
            "competition internationale de clubs : aucun club n'y est engage."
        )

    async def _saison_courante(session):
        return "2025-2026"

    original = mod.team_ids_for_league
    original_saison = mod.current_season
    mod.team_ids_for_league = _team_ids_interdits
    mod.current_season = _saison_courante
    try:
        response = await list_players(
            session=mock_db,
            league_api_id=7,
            team_api_id=None,
            position=None,
            min_minutes=0,
            season="2025-2026",
            sort_by="xg_per_90",
            sort_order="desc",
            limit=50,
            offset=0,
        )
    finally:
        mod.team_ids_for_league = original
        mod.current_season = original_saison

    assert len(response) == 1
    assert response[0]["goals"] == 5
    assert response[0]["player_api_id"] == 1
    _ = s_ligue1  # documente le contraste avec l'agregat domestique, non utilise par le mock


@pytest.mark.asyncio
async def test_domestic_league_still_resolves_via_team_ids_for_league():
    """Non-regression : un championnat domestique (ex. Ligue 1, id=1) continue
    de passer par l'effectif de club, jamais par la presence de stats."""
    import app.api.players as mod
    from app.api.players import list_players

    appels = []

    async def _team_ids_vus(session, league_api_id, season):
        appels.append((league_api_id, season))
        return [100]

    async def _saison_courante(session):
        return "2025-2026"

    original = mod.team_ids_for_league
    original_saison = mod.current_season
    mod.team_ids_for_league = _team_ids_vus
    mod.current_season = _saison_courante
    try:
        p1 = _player(api_id=1)
        s1 = _season_stat(player_api_id=1, league_api_id=1, goals=9)

        players_res = MagicMock()
        players_res.scalars.return_value.all.return_value = [p1]
        stats_res = MagicMock()
        stats_res.scalars.return_value.all.return_value = [s1]
        crest_res = MagicMock()
        crest_res.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[players_res, stats_res, crest_res])

        response = await list_players(
            session=mock_db,
            league_api_id=1,
            team_api_id=None,
            position=None,
            min_minutes=0,
            season="2025-2026",
            sort_by="xg_per_90",
            sort_order="desc",
            limit=50,
            offset=0,
        )
    finally:
        mod.team_ids_for_league = original
        mod.current_season = original_saison

    assert appels == [(1, "2025-2026")]
    assert len(response) == 1
    assert response[0]["goals"] == 9


@pytest.mark.asyncio
async def test_domestic_league_without_teams_returns_empty():
    """Non-regression : championnat domestique sans club resolu -> [] (pas de
    repli par stats, contrairement aux competitions internationales)."""
    import app.api.players as mod
    from app.api.players import list_players

    async def _aucun_club(session, league_api_id, season):
        return []

    async def _saison_courante(session):
        return "2025-2026"

    original = mod.team_ids_for_league
    original_saison = mod.current_season
    mod.team_ids_for_league = _aucun_club
    mod.current_season = _saison_courante
    try:
        response = await list_players(
            session=AsyncMock(),
            league_api_id=99,
            team_api_id=None,
            position=None,
            min_minutes=0,
            season="2025-2026",
            sort_by="xg_per_90",
            sort_order="desc",
            limit=50,
            offset=0,
        )
    finally:
        mod.team_ids_for_league = original
        mod.current_season = original_saison

    assert response == []


def test_league_groups_disjoint():
    from app.ingestion.bzzoiro.constants import (
        INTL_CLUB_COMP_LEAGUE_IDS, SELECTION_STATS_LEAGUE_IDS,
    )
    assert INTL_CLUB_COMP_LEAGUE_IDS.isdisjoint(SELECTION_STATS_LEAGUE_IDS)
    assert 7 in INTL_CLUB_COMP_LEAGUE_IDS and 27 in SELECTION_STATS_LEAGUE_IDS
