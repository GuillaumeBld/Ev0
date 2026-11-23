"""Reglement par identifiant joueur (tache 9).

Une reco qui porte `player_id` (identifiant Bzzoiro) est reglee par cet
identifiant : le nom ecrit sur la feuille de match peut differer sans que le
verdict change. Une reco sans `player_id` (tout l'historique du backtest)
suit le chemin par nom, inchange -- les tests test_auto_settle_* le prouvent.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.auto_settle import settle_approved_recommendations


def _reco(nom, player_id, rec_id=1):
    r = MagicMock()
    r.id = rec_id
    r.player_name = nom
    r.player_id = player_id
    r.bet_type = "goal"
    r.market_type = "standard"
    r.best_odds = 5.0
    r.result = None
    r.pnl = None
    r.settled_utc = None
    return r


def _fixture(score=(1, 0)):
    f = MagicMock()
    f.id = 1
    f.external_id = "bzz_9001"
    f.home_score, f.away_score = score
    f.home_team, f.away_team = "A", "B"
    f.kickoff_utc = datetime.now(UTC) - timedelta(days=1)
    f.status = "finished"
    return f


def _db(recos, fixture, minutes_par_id, buteurs):
    """minutes_par_id : {api_id: minutes} ou None ; buteurs : [(nom, id)]."""
    etat = {"n": 0}

    async def execute(stmt, params=None, *a, **k):
        etat["n"] += 1
        res = MagicMock()
        texte = str(stmt)
        if etat["n"] == 1:
            res.all.return_value = [(r, fixture) for r in recos]
        elif "bzz_player_match_stats" in texte:
            res.all.return_value = list((minutes_par_id or {}).items())
        elif "player_match_minutes" in texte:
            scal = MagicMock(); scal.all.return_value = []
            res.scalars.return_value = scal
        elif "count" in texte.lower():
            res.all.return_value = [("goal", len(buteurs))]
        else:
            evs = []
            for nom, pid in buteurs:
                e = MagicMock(); e.player_name = nom; e.player_id = pid
                evs.append(e)
            scal = MagicMock(); scal.all.return_value = evs
            res.scalars.return_value = scal
        return res

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_nom_different_regle_par_identifiant_gagne():
    r = _reco("Lautaro Martinez", 777)
    db = _db([r], _fixture(), {777: 90.0, 5: 90.0}, [("L. Martínez Toro", 777)])
    bilan = await settle_approved_recommendations(db)
    assert r.result == "won" and bilan["won"] == 1


@pytest.mark.asyncio
async def test_homonyme_ne_gagne_pas_par_le_nom():
    """Meme nom, autre identifiant : le but n'est pas a lui."""
    r = _reco("Carlos Silva", 777)
    db = _db([r], _fixture(), {777: 90.0, 888: 90.0}, [("Carlos Silva", 888)])
    await settle_approved_recommendations(db)
    assert r.result == "lost"


@pytest.mark.asyncio
async def test_identifiant_a_zero_minute_est_annule():
    r = _reco("Remplacant", 777)
    db = _db([r], _fixture(), {777: 0.0, 5: 90.0}, [("Autre", 5)])
    await settle_approved_recommendations(db)
    assert r.result == "void" and r.pnl == 0.0


@pytest.mark.asyncio
async def test_identifiant_hors_du_groupe_est_annule():
    r = _reco("Absent", 777)
    db = _db([r], _fixture(), {5: 90.0}, [("Autre", 5)])
    await settle_approved_recommendations(db)
    assert r.result == "void"


@pytest.mark.asyncio
async def test_minutes_inconnues_sans_but_reste_en_attente():
    r = _reco("Inconnu", 777)
    db = _db([r], _fixture(), None, [("Autre", 5)])
    bilan = await settle_approved_recommendations(db)
    assert r.result is None and bilan["stuck_fixture_ids"] == [1]


@pytest.mark.asyncio
async def test_event_sans_identifiant_jamais_perdu_en_silence():
    """Un but sans identifiant ne permet pas de conclure PERDU par id."""
    r = _reco("Titulaire", 777)
    db = _db([r], _fixture(), {777: 90.0}, [("Buteur Sans Id", None)])
    await settle_approved_recommendations(db)
    assert r.result is None


def _reco_passe(nom, player_id):
    r = _reco(nom, player_id)
    r.bet_type = "assist"
    return r


@pytest.mark.asyncio
async def test_passeur_regle_par_identifiant():
    r = _reco_passe("Lamine Yamal", 42)
    db = _db([r], _fixture(), {42: 90.0, 5: 90.0}, [("L. Yamal", 42)])
    await settle_approved_recommendations(db)
    assert r.result == "won"


@pytest.mark.asyncio
async def test_identifiant_hors_des_minutes_reste_en_attente_jamais_perdu():
    """Un but dont l'id n'est pas dans les minutes du match : pas de PERDU."""
    r = _reco("Titulaire", 777)
    db = _db([r], _fixture(), {777: 90.0}, [("Buteur", 999)])
    bilan = await settle_approved_recommendations(db)
    assert r.result is None and bilan["stuck_fixture_ids"] == [1]
