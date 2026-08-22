"""La reconstruction se plie strictement aux effectifs reglementaires.

Une segmentation approximative est pire qu'une absence de segmentation : elle
donne l'illusion d'etre juste. Un ecart interrompt donc la reconstruction
avant toute ecriture.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.constants import EFFECTIFS_REGLEMENTAIRES
from app.scripts.rebuild_team_registry import (
    SegmentationError,
    enumerer_engages,
    rebuild,
)


def _event(home_id, home_nom, away_id, away_nom):
    return {
        "id": 1000 + home_id,
        "home_team_obj": {"id": home_id, "name": home_nom},
        "away_team_obj": {"id": away_id, "name": away_nom},
    }


def _events_pour(n_clubs: int, decalage: int = 0):
    """Fabrique des matchs couvrant exactement n_clubs clubs distincts."""
    clubs = [(decalage + i, f"Club {decalage + i}") for i in range(1, n_clubs + 1)]
    evs = []
    for i in range(0, n_clubs, 2):
        (hid, hn), (aid, an) = clubs[i], clubs[i + 1]
        evs.append(_event(hid, hn, aid, an))
    return evs


def _session():
    s = MagicMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    s.add = MagicMock()
    return s


def test_effectifs_reglementaires():
    assert EFFECTIFS_REGLEMENTAIRES == {1: 20, 3: 20, 4: 20, 5: 18, 6: 18}
    assert sum(EFFECTIFS_REGLEMENTAIRES.values()) == 96
    # La Ligue des champions en est absente : les tirages de la phase de ligue
    # n'ont pas eu lieu.
    assert 7 not in EFFECTIFS_REGLEMENTAIRES


async def test_enumeration_rend_les_clubs_distincts():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=_events_pour(18))

    engages = await enumerer_engages(client, 6, "2026-2027")

    assert len(engages) == 18
    assert engages[1] == "Club 1"


async def test_enumeration_par_fenetre_de_dates_jamais_par_season():
    """season= est inoperant : il rend 408 110 evenements remontant a 1930."""
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])

    await enumerer_engages(client, 6, "2026-2027")

    params = client.get_all.call_args.args[1]
    assert params["league"] == 6
    assert params["date_from"] == "2026-08-01"
    assert params["date_to"] == "2027-08-01"
    assert "season" not in params


async def test_ecart_d_effectif_interrompt_sans_rien_ecrire():
    """18 clubs la ou 20 sont attendus : on s'arrete, on ne commet rien."""
    client = MagicMock()
    client.get_all = AsyncMock(return_value=_events_pour(18))
    session = _session()

    with pytest.raises(SegmentationError) as exc:
        await rebuild(session, client, season="2026-2027")

    message = str(exc.value)
    assert "18" in message and "20" in message
    session.commit.assert_not_called()
    session.add.assert_not_called()


async def test_un_club_engage_dans_deux_championnats_est_refuse():
    """L'invariant : un club appartient a exactement un championnat."""
    async def _get_all(path, params=None):
        n = EFFECTIFS_REGLEMENTAIRES[params["league"]]
        evs = _events_pour(n)
        # Le club 1 apparait dans toutes les competitions.
        evs[0]["home_team_obj"] = {"id": 1, "name": "Club 1"}
        return evs

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)
    session = _session()

    with pytest.raises(SegmentationError) as exc:
        await rebuild(session, client, season="2026-2027")

    assert "deux championnats" in str(exc.value)
    session.commit.assert_not_called()


async def test_reconstruction_nominale():
    """Effectifs conformes : le referentiel est ecrit."""
    decalages = {}
    base = 0
    for lid in EFFECTIFS_REGLEMENTAIRES:
        decalages[lid] = base
        base += 100

    async def _get_all(path, params=None):
        lid = params["league"]
        return _events_pour(EFFECTIFS_REGLEMENTAIRES[lid], decalages[lid])

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    session = _session()
    vide = MagicMock()
    vide.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=vide)

    comptes = await rebuild(session, client, season="2026-2027")

    assert comptes == EFFECTIFS_REGLEMENTAIRES
    assert session.add.call_count == 96
    session.commit.assert_awaited_once()
