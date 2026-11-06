"""Tests for GET /players/{player_api_id}/match-odds — cotes closing Betclic par match.

Jointure bzz_events -> fixtures -> player_odds_snapshots -> bzz_player_match_stats
(voir app.api.players._MATCH_ODDS_SQL). Le "closing" est la cote Betclic au
scraped_at le plus recent par (fixture, market_type, player_name) : DISTINCT ON
cote SQL, donc teste ici en simulant directement ce que Postgres rendrait
apres ce DISTINCT ON (mock au niveau result.mappings().all(), meme convention
que test_player_breakdown.py). Le SQL ne filtre plus par nom (bug corrige :
matching insensible aux accents/ponctuation requis) — le rattachement par nom
se fait en Python via _names_match (reutilisee depuis app.ingestion.auto_settle,
pas reimplementee), d'ou le test `test_match_odds_accent_mismatch_still_matches`
qui prouve qu'un nom de cote different par un accent est bien rattache.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_match_odds_closing_betclic():
    """Un joueur avec un event couvert par une fixture et des cotes Betclic.

    La requete SQL fait elle-meme le DISTINCT ON (fixture_id, market_type,
    player_name) ORDER BY scraped_at DESC pour ne garder que le closing ; on
    simule donc directement les lignes pivotables (une par market_type) que
    Postgres rendrait une fois ce closing applique, pour l'event lie a la
    fixture. Le pivot buteur/passeur et le filtre de nom se font en Python.
    """
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Kylian Mbappe"

    odds_result = MagicMock()
    odds_result.mappings.return_value.all.return_value = [
        {"event_api_id": 456, "market_type": "goalscorer", "player_name": "Kylian Mbappe", "odds": 2.10},
        {"event_api_id": 456, "market_type": "assist", "player_name": "Kylian Mbappe", "odds": 4.50},
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[name_result, odds_result])

    response = await get_player_match_odds(player_api_id=1, session=mock_db)

    assert response == {
        "odds": [{"event_api_id": 456, "buteur": 2.10, "passeur": 4.50}]
    }

    # Le second execute porte le SQL texte avec le DISTINCT ON du closing,
    # pas une requete ORM arbitraire. Le nom n'est PLUS un parametre SQL : le
    # filtrage par nom se fait en Python (via _names_match), pas en base.
    second_call = mock_db.execute.call_args_list[1]
    sql_text = str(second_call.args[0])
    assert "DISTINCT ON" in sql_text
    assert "scraped_at DESC" in sql_text
    assert second_call.args[1] == {"player_api_id": 1}


@pytest.mark.asyncio
async def test_match_odds_missing_market_is_none():
    """Un event n'ayant qu'une cote buteur (pas de passeur) -> passeur=None,
    pas d'event manquant dans la liste."""
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Harry Kane"

    odds_result = MagicMock()
    odds_result.mappings.return_value.all.return_value = [
        {"event_api_id": 789, "market_type": "goalscorer", "player_name": "Harry Kane", "odds": 1.80},
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


@pytest.mark.asyncio
async def test_match_odds_accent_mismatch_still_matches():
    """Bug corrige : le snapshot Betclic porte "Mbappe" (sans accent, scraping
    brut) alors que BzzPlayer.name est "Mbappé" (accentue). Le SQL ne filtre
    plus par nom -> cette ligne remonte brute, et c'est _names_match (reutilisee
    depuis app.ingestion.auto_settle, qui neutralise accents/tirets/apostrophes)
    qui la rattache bien au joueur cote Python. Sans la correction, un ILIKE
    exact sur le nom accentue aurait echoue silencieusement et rendu odds=[].
    """
    from app.api.players import get_player_match_odds

    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Kylian Mbappé"

    odds_result = MagicMock()
    odds_result.mappings.return_value.all.return_value = [
        {"event_api_id": 111, "market_type": "goalscorer", "player_name": "Mbappe", "odds": 1.95},
        {"event_api_id": 111, "market_type": "assist", "player_name": "Mbappe", "odds": 5.00},
        # Bruit : une cote pour un autre joueur sur le meme event, ne doit pas
        # polluer le resultat pivote de notre joueur.
        {"event_api_id": 111, "market_type": "goalscorer", "player_name": "Ousmane Dembele", "odds": 3.40},
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[name_result, odds_result])

    response = await get_player_match_odds(player_api_id=3, session=mock_db)

    assert response == {
        "odds": [{"event_api_id": 111, "buteur": 1.95, "passeur": 5.00}]
    }
