"""Le reglement est gouverne par les minutes jouees, dans TOUS les cas.

Regression du 07/11/2026.

Le reglement lisait `player_match_minutes` pour savoir qui avait joue. Cette
table compte 470 lignes, contre 1 441 029 dans `bzz_player_match_stats` --
qui couvre 1 718 des 1 728 matchs termines des 30 derniers jours et distingue
explicitement 461 402 lignes a zero minute, les joueurs du groupe jamais
entres.

Consequence : des que les buts d'un match etaient ingeres, tout joueur absent
des buteurs etait marque PERDU -- y compris un remplacant qui n'etait jamais
entre. Une perte inscrite au registre pour un pari ou la mise aurait du etre
rendue.

La regle verifiee ici : minutes == 0 (ou joueur hors du groupe) -> ANNULE,
toujours, et AVANT toute lecture des buts.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.auto_settle import (
    _minutes_du_joueur,
    settle_approved_recommendations,
)


def _reco(nom, bet_type="goal", market_type="standard", rec_id=1):
    r = MagicMock()
    r.id = rec_id
    r.player_name = nom
    r.bet_type = bet_type
    r.market_type = market_type
    r.best_odds = 5.0
    r.result = None
    r.pnl = None
    r.settled_utc = None
    return r


def _fixture(fid=1, score=(2, 1)):
    f = MagicMock()
    f.id = fid
    f.external_id = f"bzz_{9000 + fid}"
    f.home_score, f.away_score = score
    f.home_team, f.away_team = "A", "B"
    f.kickoff_utc = datetime.now(UTC) - timedelta(days=1)
    f.status = "finished"
    return f


def _db(recos, fixture, minutes, buteurs, nb_buts=None):
    """Session mockee : recos a regler, minutes du match, events buts.

    `minutes` : dict {nom -> minutes} ou None (aucune donnee)
    `buteurs` : noms presents dans les events du marche
    `nb_buts` : couverture des events (defaut = score, donc complete)
    """
    if nb_buts is None:
        nb_buts = (fixture.home_score or 0) + (fixture.away_score or 0)
    etat = {"n": 0}

    async def execute(stmt, params=None, *a, **k):
        etat["n"] += 1
        res = MagicMock()
        texte = str(stmt)

        if etat["n"] == 1:                       # les recos a regler
            res.all.return_value = [(r, fixture) for r in recos]
        elif "bzz_player_match_stats" in texte:  # minutes Bzzoiro
            res.all.return_value = list((minutes or {}).items())
        elif "player_match_minutes" in texte:    # complement PMM
            scal = MagicMock(); scal.all.return_value = []
            res.scalars.return_value = scal
        elif "count" in texte.lower():           # couverture des buts
            res.all.return_value = [("goal", nb_buts)]
        else:                                    # les events du marche
            evs = []
            for n in buteurs:
                e = MagicMock(); e.player_name = n; evs.append(e)
            scal = MagicMock(); scal.all.return_value = evs
            res.scalars.return_value = scal
        return res

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    return db


# ── La regle ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remplacant_jamais_entre_est_ANNULE_pas_perdu():
    """Le coeur de la regression."""
    r = _reco("Remplaçant Non Entré")
    f = _fixture()
    db = _db([r], f, minutes={"Remplaçant Non Entré": 0.0, "Titulaire": 90.0},
             buteurs=["Titulaire"])

    bilan = await settle_approved_recommendations(db)

    assert r.result == "void", "un joueur jamais entre ne perd pas son pari"
    assert r.pnl == 0.0, "la mise est rendue"
    assert bilan["void"] == 1 and bilan["lost"] == 0


@pytest.mark.asyncio
async def test_joueur_hors_du_groupe_est_ANNULE():
    """Absent de la feuille alors que le match a ses minutes : il n'etait pas
    dans le groupe."""
    r = _reco("Jamais Convoqué")
    f = _fixture()
    db = _db([r], f, minutes={"Titulaire": 90.0}, buteurs=["Titulaire"])

    await settle_approved_recommendations(db)
    assert r.result == "void"
    assert r.pnl == 0.0


@pytest.mark.asyncio
async def test_joueur_qui_a_joue_sans_marquer_est_PERDU():
    r = _reco("Titulaire Muet")
    f = _fixture()
    db = _db([r], f, minutes={"Titulaire Muet": 90.0, "Buteur": 90.0},
             buteurs=["Buteur"])

    bilan = await settle_approved_recommendations(db)
    assert r.result == "lost"
    assert r.pnl == -10.0
    assert bilan["lost"] == 1 and bilan["void"] == 0


@pytest.mark.asyncio
async def test_buteur_est_GAGNE_avec_la_cote():
    r = _reco("Le Buteur")
    f = _fixture()
    db = _db([r], f, minutes={"Le Buteur": 78.0}, buteurs=["Le Buteur"])

    await settle_approved_recommendations(db)
    assert r.result == "won"
    assert r.pnl == pytest.approx(10.0 * (5.0 - 1))


@pytest.mark.asyncio
async def test_un_entrant_qui_marque_est_GAGNE():
    """Entre a la 85e et marque : 5 minutes suffisent."""
    r = _reco("Entrant Décisif")
    f = _fixture()
    db = _db([r], f, minutes={"Entrant Décisif": 5.0}, buteurs=["Entrant Décisif"])

    await settle_approved_recommendations(db)
    assert r.result == "won"


# ── Ce qui ne doit PAS etre tranche ───────────────────────────────

@pytest.mark.asyncio
async def test_sans_aucune_minute_le_reglement_attend():
    """Aucune donnee de minutes : supposer qu'il a joue marquerait PERDU a
    tort. On laisse courir."""
    r = _reco("Inconnu")
    f = _fixture()
    db = _db([r], f, minutes=None, buteurs=["Un Autre"])

    bilan = await settle_approved_recommendations(db)
    assert r.result is None, "rien ne doit etre tranche sans les minutes"
    assert bilan["settled"] == 0
    assert f.id in bilan["stuck_fixture_ids"]


@pytest.mark.asyncio
async def test_minutes_connues_mais_buts_incomplets_attend():
    """Couverture partielle des buts : regler marquerait PERDU des paris
    gagnants. Constate le 09/07/2026 sur 151 matchs a 40 % de couverture."""
    r = _reco("Titulaire")
    f = _fixture(score=(3, 1))
    db = _db([r], f, minutes={"Titulaire": 90.0}, buteurs=[], nb_buts=1)

    bilan = await settle_approved_recommendations(db)
    assert r.result is None
    assert f.id in bilan["stuck_fixture_ids"]


@pytest.mark.asyncio
async def test_zero_minute_tranche_meme_si_les_buts_sont_incomplets():
    """L'ordre compte : les minutes passent AVANT la couverture des buts. Un
    joueur jamais entre est annulable sans connaitre les buts."""
    r = _reco("Non Entré")
    f = _fixture(score=(3, 1))
    db = _db([r], f, minutes={"Non Entré": 0.0}, buteurs=[], nb_buts=1)

    await settle_approved_recommendations(db)
    assert r.result == "void", "les minutes doivent trancher avant les buts"


# ── La fonction de lecture des minutes ────────────────────────────

def test_none_signifie_inconnu_et_zero_signifie_na_pas_joue():
    """Distinction essentielle : None -> on attend, 0.0 -> on annule."""
    assert _minutes_du_joueur(None, "X") is None
    assert _minutes_du_joueur({"X": 0.0}, "X") == 0.0
    assert _minutes_du_joueur({"Autre": 90.0}, "X") == 0.0


@pytest.mark.asyncio
async def test_un_but_prouve_la_participation_meme_sans_minutes():
    """GAGNE se tranche sans les minutes : marquer prouve qu'on a joue."""
    r = _reco("Buteur Sans Minutes")
    f = _fixture()
    db = _db([r], f, minutes=None, buteurs=["Buteur Sans Minutes"])

    await settle_approved_recommendations(db)
    assert r.result == "won"
