"""Fusion des lignes canoniques en double.

On conserve l'ANCIENNE ligne : elle porte transfermarkt_club_id et
api_football_id, et 1 860 fixtures la referencent. On lui transfere le bon
bzz_team_id et l'engagement portes par la recente.
"""
from unittest.mock import AsyncMock, MagicMock

from app.scripts.merge_duplicate_teams import choisir_paire, merge


def _ligne(id, name_fr, bzz, league=None, tm=None, season=None, name_en=None):
    o = MagicMock()
    o.id, o.name_fr, o.name_en = id, name_fr, name_en or name_fr
    o.bzz_team_id, o.league_api_id, o.season = bzz, league, season
    o.transfermarkt_club_id = tm
    o.aliases = []
    return o


def test_conserve_la_ligne_porteuse_de_l_identite():
    ancienne = _ligne(66, "Barcelone", 2817, tm=131, name_en="Barcelona")
    recente = _ligne(390, "FC Barcelona", 44, league=3, season="2026-2027")
    a, r = choisir_paire([recente, ancienne])
    assert a is ancienne and r is recente


def test_paire_ambigue_refusee():
    # deux lignes engagees : on ne devine pas
    a = _ligne(1, "X", 10, league=3)
    b = _ligne(2, "X", 20, league=4)
    assert choisir_paire([a, b]) is None
    # une seule ligne : rien a fusionner
    assert choisir_paire([a]) is None


async def test_fusion_transfere_identifiant_et_engagement():
    ancienne = _ligne(66, "Barcelone", 2817, tm=131, name_en="Barcelona")
    recente = _ligne(390, "FC Barcelona", 44, league=3, season="2026-2027")

    session = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = [ancienne, recente]
    session.execute = AsyncMock(return_value=res)
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    fusionnes, ambigus = await merge(session)

    assert (fusionnes, ambigus) == (1, 0)
    # la recente est supprimee, l'ancienne reprend son identifiant
    session.delete.assert_awaited_once_with(recente)
    assert ancienne.bzz_team_id == 44
    assert ancienne.league_api_id == 3
    assert ancienne.season == "2026-2027"
    # l'identite historique est preservee
    assert ancienne.transfermarkt_club_id == 131


async def test_aucun_doublon_ne_fait_rien():
    seule = _ligne(66, "Barcelone", 44, league=3)
    session = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = [seule]
    session.execute = AsyncMock(return_value=res)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    assert await merge(session) == (0, 0)
    session.delete.assert_not_called()
