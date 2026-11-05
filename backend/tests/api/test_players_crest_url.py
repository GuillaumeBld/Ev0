"""Tests dedies a `crest_url` sur l'API joueurs (`/players`, `/players/{id}`).

Complementaire a `test_players_api.py::test_list_players_includes_api_football_id_*`
et `test_get_player_includes_api_football_id_*` (meme SELECT batch
`_team_crest_map`, desormais a 3 colonnes) : ici on verifie specifiquement
que `crest_url` est bien porte par la reponse, present quand
`canonical_teams.crest_url` est renseigne, `None` sinon — jamais une URL
devinee cote API.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.api.test_players_api import _player, _season_stat

_CREST_URL = (
    "https://assets.footylogos.com/previews/real-madrid/real-madrid-logo-footylogos-320.webp"
)


@pytest.mark.asyncio
async def test_list_players_includes_crest_url_when_present_in_canonical_teams():
    from app.api.players import list_players

    p1 = _player(api_id=1, name="Alpha Player", team="Real Madrid")  # current_team_api_id=100
    s1 = _season_stat(player_api_id=1)

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1]
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s1]
    crest_res = MagicMock()
    # canonical_teams.bzz_team_id=100 (= p1.current_team_api_id) -> (541, crest_url)
    crest_res.all.return_value = [(100, 541, _CREST_URL)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res, crest_res])

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

    assert response[0]["api_football_id"] == 541
    assert response[0]["crest_url"] == _CREST_URL


@pytest.mark.asyncio
async def test_list_players_crest_url_none_sans_club_canonique():
    """Aucun club canonique rattache -> crest_url None, jamais devine."""
    from app.api.players import list_players

    p1 = _player(api_id=1, name="Alpha Player")
    s1 = _season_stat(player_api_id=1)

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1]
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s1]
    crest_res = MagicMock()
    crest_res.all.return_value = []  # pas de ligne canonical_teams pour ce club

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res, crest_res])

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

    assert response[0]["crest_url"] is None


@pytest.mark.asyncio
async def test_list_players_crest_url_none_quand_club_canonique_sans_ecusson_resolu():
    """Club canonique rattache mais crest_url pas (encore) resolu -> None,
    distinct du cas 'pas de club canonique' mais meme resultat cote API."""
    from app.api.players import list_players

    p1 = _player(api_id=1, name="Alpha Player")
    s1 = _season_stat(player_api_id=1)

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1]
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s1]
    crest_res = MagicMock()
    crest_res.all.return_value = [(100, None, None)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res, crest_res])

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

    assert response[0]["api_football_id"] is None
    assert response[0]["crest_url"] is None


@pytest.mark.asyncio
async def test_get_player_includes_crest_url_when_present_in_canonical_teams():
    from app.api.players import get_player

    player = _player(api_id=1, name="Kylian Mbappe")  # current_team_api_id=100
    season_stat = _season_stat(player_api_id=1)

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = [season_stat]

    recent_result = MagicMock()
    recent_result.all.return_value = []

    crest_result = MagicMock()
    crest_result.all.return_value = [(100, 541, _CREST_URL)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result, crest_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    assert response["api_football_id"] == 541
    assert response["crest_url"] == _CREST_URL


@pytest.mark.asyncio
async def test_get_player_crest_url_none_sans_club_canonique():
    from app.api.players import get_player

    player = _player(api_id=1, name="Kylian Mbappe")
    season_stat = _season_stat(player_api_id=1)

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = [season_stat]

    recent_result = MagicMock()
    recent_result.all.return_value = []

    crest_result = MagicMock()
    crest_result.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result, crest_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    assert response["api_football_id"] is None
    assert response["crest_url"] is None
