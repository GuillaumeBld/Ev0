"""Identifiant du passeur : lu dans `sequence` des incidents Bzzoiro."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.sync_incidents import (
    _parse_incidents,
    backfill_assist_player_ids,
)

BUT_SEQ = {
    "type": "goal", "minute": 30, "player": "Bremer", "assist": "D. Luiz",
    "goal_type": "regular",
    "sequence": [
        {"pid": 4304, "event": "cross", "assist": True, "player": "D. Luiz"},
        {"pid": 4634, "event": "goal", "player": "Bremer"},
    ],
}


def _passe(raw):
    return [e for e in _parse_incidents(raw) if e["event_type"] == "assist"][0]


def test_passeur_id_par_drapeau_assist():
    assert _passe([BUT_SEQ])["player_id"] == 4304


def test_passeur_id_par_nom_quand_pas_de_drapeau():
    inc = dict(BUT_SEQ, sequence=[
        {"pid": 4304, "event": "cross", "player": "D. Luiz"},
        {"pid": 4634, "event": "goal", "player": "Bremer"},
    ])
    assert _passe([inc])["player_id"] == 4304


def test_passeur_sans_sequence_est_none():
    inc = {k: v for k, v in BUT_SEQ.items() if k != "sequence"}
    assert _passe([inc])["player_id"] is None


def test_assist_id_de_secours():
    inc = {k: v for k, v in BUT_SEQ.items() if k != "sequence"}
    inc["assist_id"] = 55
    assert _passe([inc])["player_id"] == 55


@pytest.mark.asyncio
async def test_backfill_remplit_et_est_idempotent():
    etat = {"rempli": False}

    async def execute(stmt, params=None, *a, **k):
        t = str(stmt)
        res = MagicMock()
        if "JOIN bzz_events" in t:
            res.all.return_value = [] if etat["rempli"] else [(1, [BUT_SEQ])]
        elif t.lstrip().startswith("SELECT id, minute"):
            res.all.return_value = [] if etat["rempli"] else [(10, 30, "D. Luiz")]
        else:  # UPDATE
            assert params == {"pid": 4304, "id": 10}
            etat["rempli"] = True
            res.rowcount = 1
        return res

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    assert await backfill_assist_player_ids(session) == 1
    assert await backfill_assist_player_ids(session) == 0
