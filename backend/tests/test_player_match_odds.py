"""Tests for GET /players/{player_api_id}/match-odds — cotes closing Betclic par match.

Jointure bzz_events -> fixtures -> player_odds_snapshots -> bzz_player_match_stats
(voir app.api.players._MATCH_ODDS_SQL). Le "closing" est la cote Betclic au
scraped_at le plus recent par (fixture, market_type) : DISTINCT ON cote
Postgres cote SQL, donc teste ici en simulant directement ce que Postgres
rendrait apres ce DISTINCT ON (mock au niveau result.mappings().all(), meme
convention que test_player_breakdown.py). Le second test verifie que le SQL
texte contient bien le DISTINCT ON + ORDER BY scraped_at DESC, pour prouver
que la logique de closing est bien celle demandee et pas un hasard de mock.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_match_odds_closing_betclic():
    """Un joueur avec un event couvert par une fixture et des cotes Betclic.

    La requete SQL fait elle-meme le DISTINCT ON (fixture_id, market_type)
    ORDER BY scraped_at DESC pour ne garder que le closing ; on simule donc
    directement la ligne pivotee (buteur/passeur) que Postgres rendrait une
    fois ce closing applique, pour l'event lie a la fixture.
    """
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Kylian Mbappe"

    odds_result = MagicMock()
    odds_result.mappings.return_value.all.return_value = [
        {"event_api_id": 456, "buteur": 2.10, "passeur": 4.50},
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[name_result, odds_result])

    response = await get_player_match_odds(player_api_id=1, session=mock_db)

    assert response == {
        "odds": [{"event_api_id": 456, "buteur": 2.10, "passeur": 4.50}]
    }

    # Le second execute porte le SQL texte avec le DISTINCT ON du closing,
    # pas une requete ORM arbitraire.
    second_call = mock_db.execute.call_args_list[1]
    sql_text = str(second_call.args[0])
    assert "DISTINCT ON" in sql_text
    assert "scraped_at DESC" in sql_text
    assert second_call.args[1] == {"player_api_id": 1, "nom": "Kylian Mbappe"}


@pytest.mark.asyncio
async def test_match_odds_missing_market_is_none():
    """Un event n'ayant qu'une cote buteur (pas de passeur) -> passeur=None,
    pas d'event manquant dans la liste."""
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Harry Kane"

    odds_result = MagicMock()
    odds_result.mappings.return_value.all.return_value = [
        {"event_api_id": 789, "buteur": 1.80, "passeur": None},
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[name_result, odds_result])

    response = await get_player_match_odds(player_api_id=2, session=mock_db)

    assert response == {
        "odds": [{"event_api_id": 789, "buteur": 1.80, "passeur": None}]
    }


@pytest.mark.asyncio
async def test_match_odds_unknown_player_returns_empty():
    """Joueur introuvable -> odds=[], pas d'erreur, et pas de 2e requete inutile."""
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=name_result)

    response = await get_player_match_odds(player_api_id=999, session=mock_db)

    assert response == {"odds": []}
    assert mock_db.execute.call_count == 1
