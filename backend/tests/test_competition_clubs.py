"""Tests for GET /competitions/{league_api_id}/clubs — barre latérale de
clubs des coupes d'Europe (phase de ligue) et des compétitions sans round
(Libertadores/Sudamericana/Super Coupe).

Le mock cible directement les requêtes SQL de l'implémentation
(session.execute -> result.all()), même forme que
tests/test_player_breakdown.py. `current_season` est monkeypatché comme dans
tests/test_players_intl.py plutôt que mocké via une requête db — ça évite un
appel db.execute supplémentaire pour l'override app_config.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _exec_result(rows: list[tuple]) -> MagicMock:
    """Mimics result.all() for a raw column select (list of tuples)."""
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_competition_clubs_league_phase():
    """Ligue 7 (C1) : un round de qualification (40 matchs) et un round de
    phase de ligue (18 matchs, 36 clubs) -> seuls les 36 clubs de la phase de
    ligue sont rendus. Noms via canonical_teams ; un club sans canonical
    retombe sur bzz_teams.name, crest/pays restent None.
    """
    import app.api.atlas as mod

    # Round 1 (qualifs) : 40 matchs, mêmes deux equipes repetees -> le round
    # est exclu par le filtre peu importe l'identite des equipes.
    quali_rows = [(1, 9001, 9002) for _ in range(40)]

    # Round 2 (phase de ligue) : 18 matchs, 36 clubs distincts (1..36).
    league_phase_rows = [(2, 2 * i - 1, 2 * i) for i in range(1, 19)]

    event_rows = quali_rows + league_phase_rows
    event_result = _exec_result(event_rows)

    # canonical_teams connait tous les clubs sauf le club 36.
    canonical_rows = [
        (tid, f"Club Canonique {tid}", f"https://crest/{tid}.png", 1000 + tid, "France")
        for tid in range(1, 36)
    ]
    canonical_result = _exec_result(canonical_rows)

    # Fallback bzz_teams.name pour le club 36, absent de canonical_teams.
    fallback_result = _exec_result([(36, "Club Brut 36")])

    # player_count : seul le club 1 a des joueurs avec des stats dans cette
    # competition pour cette saison.
    player_count_result = _exec_result([(1, 3)])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[event_result, canonical_result, fallback_result, player_count_result]
    )

    async def _saison_courante(_db):
        return "2025-2026"

    original_season = mod.current_season
    mod.current_season = _saison_courante
    try:
        response = await mod.competition_clubs(league_api_id=7, db=mock_db)
    finally:
        mod.current_season = original_season

    assert len(response.clubs) == 36
    ids = {c.bzz_team_id for c in response.clubs}
    assert ids == set(range(1, 37))
    # Les equipes du round de qualif (9001/9002) sont absentes.
    assert 9001 not in ids and 9002 not in ids

    club_1 = next(c for c in response.clubs if c.bzz_team_id == 1)
    assert club_1.name == "Club Canonique 1"
    assert club_1.crest_url == "https://crest/1.png"
    assert club_1.api_football_id == 1001
    assert club_1.country == "France"
    assert club_1.player_count == 3

    club_36 = next(c for c in response.clubs if c.bzz_team_id == 36)
    assert club_36.name == "Club Brut 36"
    assert club_36.crest_url is None
    assert club_36.api_football_id is None
    assert club_36.country is None
    assert club_36.player_count == 0


@pytest.mark.asyncio
async def test_competition_clubs_libertadores_all():
    """Ligue 32 (Libertadores) : pas de filtre de round -- un round de
    seulement 3 matchs (qui serait exclu par le filtre phase de ligue d'une
    coupe d'Europe) rend quand meme tous ses clubs."""
    import app.api.atlas as mod

    event_rows = [(1, 601, 602), (1, 603, 604), (1, 605, 606)]
    event_result = _exec_result(event_rows)

    canonical_rows = [
        (tid, f"Club {tid}", None, None, "Argentina") for tid in (601, 602, 603, 604, 605, 606)
    ]
    canonical_result = _exec_result(canonical_rows)
    # Tous les clubs ont un canonical -> pas d'appel bzz_teams fallback.
    player_count_result = _exec_result([])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[event_result, canonical_result, player_count_result]
    )

    async def _saison_courante(_db):
        return "2025-2026"

    original_season = mod.current_season
    mod.current_season = _saison_courante
    try:
        response = await mod.competition_clubs(league_api_id=32, db=mock_db)
    finally:
        mod.current_season = original_season

    assert len(response.clubs) == 6
    ids = {c.bzz_team_id for c in response.clubs}
    assert ids == {601, 602, 603, 604, 605, 606}
    assert all(c.player_count == 0 for c in response.clubs)


@pytest.mark.asyncio
async def test_competition_clubs_empty_when_no_events():
    """Aucun event pour la ligue/saison -> clubs vide, pas d'erreur."""
    import app.api.atlas as mod

    event_result = _exec_result([])
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[event_result])

    async def _saison_courante(_db):
        return "2025-2026"

    original_season = mod.current_season
    mod.current_season = _saison_courante
    try:
        response = await mod.competition_clubs(league_api_id=7, db=mock_db)
    finally:
        mod.current_season = original_season

    assert response.clubs == []
