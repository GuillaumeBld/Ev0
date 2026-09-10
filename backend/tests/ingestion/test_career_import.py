"""Tests de l'import carriere automatique (reseau et base mockes)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.career_import import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_UNMATCHED,
    classify_report,
    run_career_import,
    should_retry,
)
from app.scripts.transfermarkt_career import TransfermarktError

NOW = dt.datetime(2026, 9, 10, 3, 0, tzinfo=dt.timezone.utc)


def _session_with_written(api_ids: list[int]) -> MagicMock:
    """Session dont la verification en base rend `api_ids` comme ecrits."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = api_ids
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def _stats(seasons: int = 12) -> MagicMock:
    s = MagicMock()
    s.seasons_upserted = seasons
    return s


# --------------------------------------------------------------------------
# classify_report — seule regle de decision du module
# --------------------------------------------------------------------------

def test_classify_report_matched():
    assert classify_report({"matched": True}) == (OUTCOME_OK, None)


def test_classify_report_unmatched_garde_la_raison():
    outcome, reason = classify_report({"matched": False, "reason": "aucune fiche a cette date"})
    assert outcome == OUTCOME_UNMATCHED
    assert reason == "aucune fiche a cette date"


# --------------------------------------------------------------------------
# should_retry — politique de reessai
# --------------------------------------------------------------------------

def test_un_import_reussi_ne_se_rejoue_jamais():
    assert should_retry(OUTCOME_OK, NOW - dt.timedelta(days=365), NOW) is False


def test_non_resolu_attend_trente_jours():
    assert should_retry(OUTCOME_UNMATCHED, NOW - dt.timedelta(days=29), NOW) is False
    assert should_retry(OUTCOME_UNMATCHED, NOW - dt.timedelta(days=31), NOW) is True


def test_panne_reseau_se_retente_le_lendemain():
    assert should_retry(OUTCOME_ERROR, NOW - dt.timedelta(hours=12), NOW) is False
    assert should_retry(OUTCOME_ERROR, NOW - dt.timedelta(days=2), NOW) is True


# --------------------------------------------------------------------------
# run_career_import — orchestration
# --------------------------------------------------------------------------

JOUEUR_A = {"player_api_id": 101, "name": "Omar Fayed", "nationality": "EG", "dob": "2001-03-04", "club": "X"}
JOUEUR_B = {"player_api_id": 202, "name": "Adam Aznou", "nationality": "MA", "dob": "2006-06-02", "club": "Y"}


@pytest.mark.asyncio
async def test_joueur_ecrit_en_base_est_marque_ok():
    session = _session_with_written([101])
    rapport = {"matched": True, "tm_id": "555", "input_name": "Omar Fayed", "seasons": []}

    with patch("app.ingestion.career_import.players_needing_career", AsyncMock(return_value=[JOUEUR_A])), \
         patch("app.ingestion.career_import.build_player_report", return_value=rapport), \
         patch("app.ingestion.career_import.import_career_data", AsyncMock(return_value=_stats())), \
         patch("app.ingestion.career_import.record_attempt", AsyncMock()) as rec:
        run = await run_career_import(session, client=MagicMock(), now=NOW)

    assert run.imported == 1
    assert run.unmatched == 0
    assert rec.await_args.kwargs.get("tm_id") == "555" or "555" in rec.await_args.args
    assert rec.await_args.args[2] == OUTCOME_OK


@pytest.mark.asyncio
async def test_identifie_mais_non_ecrit_est_marque_non_resolu():
    """Regression : un joueur que Transfermarkt identifie mais que l'ecriture
    refuse (date de naissance manquante, homonyme, tm_id invalide) ne doit PAS
    etre marque 'ok'. Sinon il sort de la file pour toujours, sans carriere et
    sans trace — echec silencieux."""
    session = _session_with_written([])  # rien n'a ete ecrit
    rapport = {"matched": True, "tm_id": "555", "input_name": "Omar Fayed", "seasons": []}

    with patch("app.ingestion.career_import.players_needing_career", AsyncMock(return_value=[JOUEUR_A])), \
         patch("app.ingestion.career_import.build_player_report", return_value=rapport), \
         patch("app.ingestion.career_import.import_career_data", AsyncMock(return_value=_stats(0))), \
         patch("app.ingestion.career_import.record_attempt", AsyncMock()) as rec:
        run = await run_career_import(session, client=MagicMock(), now=NOW)

    assert run.imported == 0
    assert run.unmatched == 1
    assert "Omar Fayed" in run.unmatched_names
    assert rec.await_args.args[2] == OUTCOME_UNMATCHED


@pytest.mark.asyncio
async def test_panne_reseau_nenterre_pas_le_reste_du_lot():
    """Une erreur reseau sur un joueur est tracee comme telle et le lot continue."""
    session = _session_with_written([202])
    rapport_b = {"matched": True, "tm_id": "777", "input_name": "Adam Aznou", "seasons": []}

    def _report(client, player, cache):
        if player["player_api_id"] == 101:
            raise TransfermarktError("HTTP 429")
        return rapport_b

    with patch("app.ingestion.career_import.players_needing_career", AsyncMock(return_value=[JOUEUR_A, JOUEUR_B])), \
         patch("app.ingestion.career_import.build_player_report", side_effect=_report), \
         patch("app.ingestion.career_import.import_career_data", AsyncMock(return_value=_stats())), \
         patch("app.ingestion.career_import.record_attempt", AsyncMock()) as rec:
        run = await run_career_import(session, client=MagicMock(), now=NOW)

    assert run.errors == 1
    assert run.imported == 1          # le second joueur a bien ete traite
    assert run.attempted == 2
    issues = [c.args[2] for c in rec.await_args_list]
    assert OUTCOME_ERROR in issues
    assert OUTCOME_OK in issues


@pytest.mark.asyncio
async def test_non_resolu_na_pas_besoin_decriture():
    """Un joueur non identifie ne part jamais a l'ecriture."""
    session = _session_with_written([])
    rapport = {"matched": False, "reason": "aucun candidat a cette date", "input_name": "Omar Fayed"}

    with patch("app.ingestion.career_import.players_needing_career", AsyncMock(return_value=[JOUEUR_A])), \
         patch("app.ingestion.career_import.build_player_report", return_value=rapport), \
         patch("app.ingestion.career_import.import_career_data", AsyncMock()) as imp, \
         patch("app.ingestion.career_import.record_attempt", AsyncMock()):
        run = await run_career_import(session, client=MagicMock(), now=NOW)

    imp.assert_not_awaited()
    assert run.unmatched == 1
    assert run.status == "partial"


@pytest.mark.asyncio
async def test_file_vide_ne_declenche_rien():
    session = _session_with_written([])

    with patch("app.ingestion.career_import.players_needing_career", AsyncMock(return_value=[])), \
         patch("app.ingestion.career_import.build_player_report") as bpr, \
         patch("app.ingestion.career_import.import_career_data", AsyncMock()) as imp:
        run = await run_career_import(session, client=MagicMock(), now=NOW)

    bpr.assert_not_called()
    imp.assert_not_awaited()
    assert run.candidates == 0
    assert run.status == "ok"
