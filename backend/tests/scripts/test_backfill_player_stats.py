"""Le rattrapage enumere par fenetre de dates, jamais par season=.

Le parametre season= de l'API est inoperant : ?season=2024-2025 rend 408 110
evenements remontant a 1930. Seul league + date_from + date_to filtre.
"""
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.constants import BACKFILL_SEASONS
from app.scripts.backfill_player_stats import backfill, season_window


def _event(event_id: int, status: str = "finished"):
    return {
        "id": event_id,
        "status": status,
        "home_team_obj": {"id": 102},
        "away_team_obj": {"id": 114},
    }


def _session(deja_ingeres=()):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(deja_ingeres)
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def test_cinq_saisons_au_perimetre():
    assert BACKFILL_SEASONS == [
        "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026",
    ]


def test_fenetre_de_saison_juillet_a_juin():
    assert season_window("2024-2025") == ("2024-07-01", "2025-06-30")
    assert season_window("2021-2022") == ("2021-07-01", "2022-06-30")


async def test_enumeration_par_fenetre_de_dates_jamais_par_season():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])

    await backfill(_session(), client, seasons=["2024-2025"], leagues=[6])

    appels_events = [
        c for c in client.get_all.call_args_list if c.args[0] == "/api/events/"
    ]
    assert appels_events, "aucune enumeration d'evenements"
    for appel in appels_events:
        params = appel.args[1]
        assert "season" not in params
        assert params["date_from"] == "2024-07-01"
        assert params["date_to"] == "2025-06-30"
        assert params["league"] == 6


async def test_saute_les_matchs_deja_ingeres():
    """Une execution interrompue puis relancee ne retraite pas l'existant."""
    client = MagicMock()

    async def _get_all(path, params=None):
        if path == "/api/events/":
            return [_event(111), _event(222)]
        return []

    client.get_all = AsyncMock(side_effect=_get_all)

    traites, ignores = await backfill(
        _session(deja_ingeres=[111]), client, seasons=["2024-2025"], leagues=[6],
    )
    assert ignores == 1
    assert traites == 1


async def test_ignore_les_matchs_non_termines():
    client = MagicMock()

    async def _get_all(path, params=None):
        if path == "/api/events/":
            return [_event(111, status="notstarted"), _event(222)]
        return []

    client.get_all = AsyncMock(side_effect=_get_all)

    traites, _ = await backfill(
        _session(), client, seasons=["2024-2025"], leagues=[6],
    )
    assert traites == 1


async def test_passe_les_identifiants_d_equipe_du_bon_espace():
    """/api/events/ n'expose pas home_team_api_id mais home_team_obj.id."""
    vus = []

    async def _get_all(path, params=None):
        if path == "/api/events/":
            return [_event(333)]
        vus.append(params)
        return []

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    import app.ingestion.bzzoiro.sync_player_stats as ingestion
    appels = []

    async def _spy(session, cl, event_api_id, home_id, away_id):
        appels.append((event_api_id, home_id, away_id))
        return 1

    import app.scripts.backfill_player_stats as script
    original = script.sync_player_stats_for_event
    script.sync_player_stats_for_event = _spy
    try:
        await backfill(_session(), client, seasons=["2024-2025"], leagues=[6])
    finally:
        script.sync_player_stats_for_event = original

    assert appels == [(333, 102, 114)]
    assert ingestion is not None  # le module d'ingestion reste la source unique


async def test_parcourt_toutes_les_saisons_et_competitions():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])

    await backfill(
        _session(), client,
        seasons=["2023-2024", "2024-2025"], leagues=[1, 6],
    )

    appels_events = [
        c for c in client.get_all.call_args_list if c.args[0] == "/api/events/"
    ]
    assert len(appels_events) == 4
