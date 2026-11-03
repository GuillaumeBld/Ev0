"""Tests Tache 6 — job quotidien d'expansion, backfill historique one-shot,
orchestrateur `build_full_referentiel`.

Trois garanties testees :

(a) le perimetre du nouveau job (`expansion_league_ids`) exclut a la fois ce
    que `job_sync_bzzoiro_events` couvre deja (big-5 + C1 + international) ET
    les tournois historiques (66, 67) — ces derniers n'ont de toute facon
    jamais ete dans `PLAYER_STATS_LEAGUE_IDS` ;
(b) `job_sync_referentiel_expansion` n'appelle JAMAIS le controle de
    coherence du calendrier paris (alertes Telegram "incidents") et avale
    toute exception d'etape sans jamais la propager ;
(c) `build_full_referentiel.run()` enchaine ses etapes dans l'ordre fixe
    documente, et `--include-historical` insere bien le rattrapage {66, 67}
    avant cet enchainement.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.league_registry import (
    HISTORICAL_SELECTION_LEAGUE_IDS,
    PLAYER_STATS_LEAGUE_IDS,
)

# ---------------------------------------------------------------------------
# (a) Selection des ligues du nouveau job
# ---------------------------------------------------------------------------


def test_expansion_league_ids_excludes_already_covered_and_historical():
    """Sur le registre REEL : le perimetre d'expansion ne recoupe ni le
    big-5/C1, ni l'international, ni les deux tournois historiques — et reste
    un sous-ensemble de PLAYER_STATS_LEAGUE_IDS."""
    from app.ingestion.bzzoiro.expansion_scope import expansion_league_ids

    ids = expansion_league_ids()

    assert set(ids) & set(TARGET_LEAGUE_INTERNAL_ID_LIST) == set()
    assert set(ids) & set(INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST) == set()
    assert set(ids) & HISTORICAL_SELECTION_LEAGUE_IDS == set()
    assert set(ids).issubset(set(PLAYER_STATS_LEAGUE_IDS))
    # Non vide, et strictement plus petit que le perimetre stats complet —
    # sinon la derivation ne retranche rien.
    assert ids
    assert len(ids) < len(PLAYER_STATS_LEAGUE_IDS)
    # Repere connu : Liga Portugal (2) est bien une ligue d'expansion.
    assert 2 in ids


def test_expansion_league_ids_derivation_mocked():
    """Derivation pure testee independamment des vraies donnees du registre :
    seules les ligues ni couvertes ni historiques survivent."""
    with patch(
        "app.ingestion.bzzoiro.expansion_scope.PLAYER_STATS_LEAGUE_IDS",
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
    ), patch(
        "app.ingestion.bzzoiro.expansion_scope.TARGET_LEAGUE_INTERNAL_ID_LIST",
        [1, 6],
    ), patch(
        "app.ingestion.bzzoiro.expansion_scope.INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST",
        [7],
    ):
        from app.ingestion.bzzoiro.expansion_scope import expansion_league_ids

        assert expansion_league_ids() == [2, 3, 4, 5, 8, 9]


def test_historical_leagues_never_in_player_stats_scope():
    """66/67 sont hors PLAYER_STATS_LEAGUE_IDS (donc hors du recurrent),
    reserves exclusivement au backfill one-shot de build_full_referentiel."""
    assert 66 not in PLAYER_STATS_LEAGUE_IDS
    assert 67 not in PLAYER_STATS_LEAGUE_IDS
    assert frozenset({66, 67}) == HISTORICAL_SELECTION_LEAGUE_IDS


# ---------------------------------------------------------------------------
# (b) job_sync_referentiel_expansion
# ---------------------------------------------------------------------------


def _session_ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _client_ctx(client):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_job_sync_referentiel_expansion_no_key():
    """Sans cle API, le job sort tot sans rien appeler."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_events", new_callable=AsyncMock
    ) as mock_sync_events, patch(
        "app.worker.rebuild", new_callable=AsyncMock
    ) as mock_rebuild:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_referentiel_expansion

        await job_sync_referentiel_expansion()

        mock_sync_events.assert_not_called()
        mock_rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_job_sync_referentiel_expansion_calls_expected_chain():
    """Le job appelle sync_events avec le perimetre d'expansion, puis rebuild,
    attach_european, check_coverage — jamais le controle de coherence du
    calendrier paris ni send_alert (canal 'incidents')."""
    from app.ingestion.bzzoiro.expansion_scope import expansion_league_ids

    expected_ids = expansion_league_ids()

    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.async_session", return_value=_session_ctx(mock_session)
    ), patch(
        "app.worker.BzzoiroClient", return_value=_client_ctx(mock_client)
    ), patch(
        "app.worker.sync_events", new_callable=AsyncMock, return_value=123
    ) as mock_sync_events, patch(
        "app.worker.rebuild", new_callable=AsyncMock, return_value={}
    ) as mock_rebuild, patch(
        "app.worker.attach_european", new_callable=AsyncMock, return_value={}
    ) as mock_attach, patch(
        "app.worker.check_coverage", new_callable=AsyncMock, return_value=[]
    ) as mock_coverage, patch(
        "app.alerts.send_alert", new_callable=AsyncMock
    ) as mock_send_alert:
        mock_settings.bzzoiro_api_key = "cle-test"
        from app.worker import job_sync_referentiel_expansion

        await job_sync_referentiel_expansion()

    mock_sync_events.assert_awaited_once()
    call = mock_sync_events.await_args
    assert call.args[:2] == (mock_session, mock_client)
    assert call.kwargs["league_internal_ids"] == expected_ids

    mock_rebuild.assert_awaited_once_with(mock_session, mock_client)
    mock_attach.assert_awaited_once_with(mock_session, mock_client)
    mock_coverage.assert_awaited_once()

    # Jamais l'alerte Telegram "incidents" du controle de coherence paris.
    mock_send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_job_sync_referentiel_expansion_never_touches_coherence_module():
    """Le job n'importe/n'appelle jamais `controler`/`resumer` de
    `app.services.coherence_calendrier` — contrairement a
    `job_sync_bzzoiro_events`, qui lui s'en sert explicitement."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.async_session", return_value=_session_ctx(mock_session)
    ), patch(
        "app.worker.BzzoiroClient", return_value=_client_ctx(mock_client)
    ), patch(
        "app.worker.sync_events", new_callable=AsyncMock, return_value=0
    ), patch(
        "app.worker.rebuild", new_callable=AsyncMock, return_value={}
    ), patch(
        "app.worker.attach_european", new_callable=AsyncMock, return_value={}
    ), patch(
        "app.worker.check_coverage", new_callable=AsyncMock, return_value=[]
    ), patch(
        "app.services.coherence_calendrier.controler"
    ) as mock_controler, patch(
        "app.services.coherence_calendrier.resumer"
    ) as mock_resumer:
        mock_settings.bzzoiro_api_key = "cle-test"
        from app.worker import job_sync_referentiel_expansion

        await job_sync_referentiel_expansion()

    mock_controler.assert_not_called()
    mock_resumer.assert_not_called()


@pytest.mark.asyncio
async def test_job_sync_referentiel_expansion_catches_step_exception():
    """Une exception levee par une etape (ici rebuild) est avalee — le job ne
    doit jamais faire planter le worker."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.async_session", return_value=_session_ctx(mock_session)
    ), patch(
        "app.worker.BzzoiroClient", return_value=_client_ctx(mock_client)
    ), patch(
        "app.worker.sync_events", new_callable=AsyncMock, return_value=0
    ), patch(
        "app.worker.rebuild",
        new_callable=AsyncMock,
        side_effect=RuntimeError("segmentation interrompue"),
    ) as mock_rebuild, patch(
        "app.worker.attach_european", new_callable=AsyncMock
    ) as mock_attach, patch(
        "app.worker.check_coverage", new_callable=AsyncMock
    ) as mock_coverage:
        mock_settings.bzzoiro_api_key = "cle-test"
        from app.worker import job_sync_referentiel_expansion

        await job_sync_referentiel_expansion()  # ne doit jamais lever

    mock_rebuild.assert_awaited_once()
    # L'exception a interrompu la chaine : les etapes suivantes ne sont pas
    # appelees pour ce passage (rattrapees le lendemain).
    mock_attach.assert_not_called()
    mock_coverage.assert_not_called()


def test_job_sync_referentiel_expansion_is_scheduled():
    """Le job est bien enregistre auprès du scheduler, sous un id distinct de
    `sync_bzzoiro_events`."""
    import inspect

    import app.worker as w

    src = inspect.getsource(w)
    assert 'id="sync_referentiel_expansion"' in src
    assert "job_sync_referentiel_expansion" in src


# ---------------------------------------------------------------------------
# (c) build_full_referentiel.run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_full_referentiel_runs_steps_in_order():
    import app.scripts.build_full_referentiel as mod

    call_order: list[str] = []

    def _tracker(name, return_value=None):
        async def _fake(*_args, **_kwargs):
            call_order.append(name)
            return return_value

        return AsyncMock(side_effect=_fake)

    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch.object(
        mod, "current_season", _tracker("current_season", "2025-2026")
    ), patch.object(
        mod, "rebuild", _tracker("rebuild", {})
    ), patch.object(
        mod, "attach_european", _tracker("attach_european", {})
    ), patch.object(
        mod, "sync_events", _tracker("sync_events", 0)
    ), patch.object(
        mod.backfill_player_stats, "backfill", _tracker("backfill", (0, 0))
    ), patch.object(
        mod, "aggregate_all_leagues", _tracker("aggregate_all_leagues", 0)
    ), patch.object(
        mod, "check_coverage", _tracker("check_coverage", [])
    ):
        reports = await mod.run(mock_session, mock_client)

    assert reports == []
    assert call_order == [
        "current_season",
        "rebuild",
        "attach_european",
        "sync_events",
        "backfill",
        "aggregate_all_leagues",
        "check_coverage",
    ]


@pytest.mark.asyncio
async def test_build_full_referentiel_include_historical_adds_backfill_first():
    """--include-historical insere un appel backfill({66,67}) AVANT
    l'enchainement normal (dont son propre appel backfill pour le perimetre
    courant)."""
    import app.scripts.build_full_referentiel as mod

    call_order: list[tuple] = []

    def _tracker(name, return_value=None):
        async def _fake(*args, **kwargs):
            call_order.append((name, args, kwargs))
            return return_value

        return AsyncMock(side_effect=_fake)

    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch.object(
        mod, "current_season", _tracker("current_season", "2025-2026")
    ), patch.object(
        mod, "rebuild", _tracker("rebuild", {})
    ), patch.object(
        mod, "attach_european", _tracker("attach_european", {})
    ), patch.object(
        mod, "sync_events", _tracker("sync_events", 0)
    ), patch.object(
        mod.backfill_player_stats, "backfill", _tracker("backfill", (0, 0))
    ), patch.object(
        mod, "aggregate_all_leagues", _tracker("aggregate_all_leagues", 0)
    ), patch.object(
        mod, "check_coverage", _tracker("check_coverage", [])
    ):
        await mod.run(mock_session, mock_client, include_historical=True)

    backfill_calls = [c for c in call_order if c[0] == "backfill"]
    assert len(backfill_calls) == 2

    historical_call = backfill_calls[0]
    assert historical_call[2]["leagues"] == [66, 67]

    names_in_order = [c[0] for c in call_order]
    assert names_in_order[0] == "backfill"  # le backfill historique passe en premier
    assert names_in_order.index("backfill") < names_in_order.index("rebuild")


@pytest.mark.asyncio
async def test_build_full_referentiel_dry_run_calls_nothing():
    import app.scripts.build_full_referentiel as mod

    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch.object(mod, "rebuild", new_callable=AsyncMock) as mock_rebuild, patch.object(
        mod, "sync_events", new_callable=AsyncMock
    ) as mock_sync_events, patch.object(
        mod.backfill_player_stats, "backfill", new_callable=AsyncMock
    ) as mock_backfill:
        reports = await mod.run(mock_session, mock_client, dry_run=True)

    assert reports == []
    mock_rebuild.assert_not_called()
    mock_sync_events.assert_not_called()
    mock_backfill.assert_not_called()


def test_build_full_referentiel_cli_flags():
    from app.scripts.build_full_referentiel import _parse_args

    args = _parse_args(["--include-historical", "--dry-run"])
    assert args.include_historical is True
    assert args.dry_run is True

    args_default = _parse_args([])
    assert args_default.include_historical is False
    assert args_default.dry_run is False
