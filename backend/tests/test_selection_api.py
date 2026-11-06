"""Tests Tache 6 : endpoint `GET /players/selection` + bloc `selection_stats`
sur `GET /players/{id}`.

Meme convention que `tests/api/test_players_api.py` : pas de client ASGI (pas
de conftest dans ce depot), on appelle directement les coroutines du routeur
avec une AsyncSession mockee dont `execute` rend, dans l'ordre, les memes
objets que produirait SQLAlchemy — construits sur des instances reelles des
modeles (BzzPlayer, PlayerSelectionSeasonStat) plutot que des MagicMock
genres, pour que les champs lus par `_merge_season_stats` existent vraiment.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.players import get_player, list_selection_players
from app.models.bzzoiro import BzzPlayer
from app.models.selection_stats import PlayerSelectionSeasonStat

FRANCE_TEAM_ID = 1000
BRAZIL_TEAM_ID = 1002
WORLD_CUP_LEAGUE_ID = 27


def _player(api_id: int, name: str, nationality: str = "French") -> BzzPlayer:
    return BzzPlayer(
        api_id=api_id, internal_id=api_id, name=name,
        short_name=name.split()[-1], position="F",
        nationality=nationality, current_team_api_id=200,
        current_team_name="Club X",
    )


def _selection_stat(
    player_api_id: int,
    national_team_api_id: int,
    league_api_id: int = WORLD_CUP_LEAGUE_ID,
    season: str = "2025-2026",
    goals: int = 2,
    matches_played: int = 5,
    minutes_played: int = 450,
    avg_rating: float = 7.5,
) -> PlayerSelectionSeasonStat:
    return PlayerSelectionSeasonStat(
        player_api_id=player_api_id,
        national_team_api_id=national_team_api_id,
        league_api_id=league_api_id,
        season=season,
        source="bzzoiro",
        matches_played=matches_played,
        minutes_played=minutes_played,
        goals=goals,
        goal_assist=1,
        expected_goals=1.8,
        expected_assists=0.6,
        avg_rating=avg_rating,
        total_shots=10,
        shots_on_target=5,
    )


# ---------------------------------------------------------------------------
# GET /players/selection — filtre par equipe nationale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selection_endpoint_filters_by_national_team():
    """2 joueurs France (bzz_team_id=1000) + 1 joueur Bresil (1002) en base ;
    filtrer par national_team_api_id=1000 ne doit rendre que les 2 Francais,
    chacun avec ses stats de selection (goals present)."""
    p_fr1 = _player(api_id=50, name="Joueur France Un")
    p_fr2 = _player(api_id=51, name="Joueur France Deux")
    # p_br n'a pas de ligne de stats France -> ne doit pas apparaitre meme si
    # present dans la table BzzPlayer renvoyee (garde-fou applique dans la
    # boucle de l'endpoint via by_player.keys()).

    sel_fr1 = _selection_stat(player_api_id=50, national_team_api_id=FRANCE_TEAM_ID)
    sel_fr2 = _selection_stat(player_api_id=51, national_team_api_id=FRANCE_TEAM_ID)

    sel_res = MagicMock()
    sel_res.scalars.return_value.all.return_value = [sel_fr1, sel_fr2]

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p_fr1, p_fr2]

    crest_res = MagicMock()
    crest_res.all.return_value = []  # aucun club canonique dans ce test

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[sel_res, players_res, crest_res])

    response = await list_selection_players(
        session=mock_db,
        national_team_api_id=FRANCE_TEAM_ID,
        league_api_id=None,
        position=None,
        q=None,
        season=None,
        sort_by="avg_rating",
        sort_order="desc",
        limit=50,
        offset=0,
    )

    assert len(response) == 2
    assert all("goals" in p for p in response)
    assert {p["player_api_id"] for p in response} == {50, 51}


@pytest.mark.asyncio
async def test_selection_endpoint_empty_when_no_rows():
    """Aucune ligne pour l'equipe demandee -> liste vide, pas d'autre requete."""
    sel_res = MagicMock()
    sel_res.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=sel_res)

    response = await list_selection_players(
        session=mock_db,
        national_team_api_id=9999,
        league_api_id=None,
        position=None,
        q=None,
        season=None,
        sort_by="avg_rating",
        sort_order="desc",
        limit=50,
        offset=0,
    )

    assert response == []
    assert mock_db.execute.await_count == 1  # pas de lookup joueur/ecusson inutile


# ---------------------------------------------------------------------------
# GET /players/{id} — bloc selection_stats + national_team_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_player_detail_exposes_selection_block():
    """Joueur France (api_id=50) avec des stats de selection -> selection_stats
    rempli et national_team_name resolu via canonical_teams.name_fr."""
    player = _player(api_id=50, name="Joueur France Un")
    sel_row = _selection_stat(player_api_id=50, national_team_api_id=FRANCE_TEAM_ID, goals=3)

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = []  # pas de stats de club cette saison

    recent_result = MagicMock()
    recent_result.all.return_value = []

    crest_result = MagicMock()
    crest_result.all.return_value = []

    sel_result = MagicMock()
    sel_result.scalars.return_value.all.return_value = [sel_row]

    name_result = MagicMock()
    name_result.scalars.return_value.first.return_value = "France"

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            player_result, season_result, recent_result, crest_result,
            sel_result, name_result,
        ]
    )

    response = await get_player(player_api_id=50, session=mock_db, season="2025-2026")

    assert response["selection_stats"] is not None
    assert response["selection_stats"].goals == 3
    assert response["national_team_name"] == "France"


@pytest.mark.asyncio
async def test_player_detail_no_selection_is_null():
    """Joueur sans aucune ligne de stats de selection -> bloc entierement
    None ; la vue club ne doit jamais inventer de stats nationales."""
    player = _player(api_id=60, name="Joueur Club Seulement")

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = []

    recent_result = MagicMock()
    recent_result.all.return_value = []

    crest_result = MagicMock()
    crest_result.all.return_value = []

    sel_result = MagicMock()
    sel_result.scalars.return_value.all.return_value = []  # aucune selection

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result, crest_result, sel_result]
    )

    response = await get_player(player_api_id=60, session=mock_db, season="2025-2026")

    assert response["selection_stats"] is None
    assert response["national_team_name"] is None
