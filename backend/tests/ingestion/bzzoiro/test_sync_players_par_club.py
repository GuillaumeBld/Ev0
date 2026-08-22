"""Les effectifs se chargent club par club, pas par la liste mondiale.

La liste mondiale compte 117 439 joueurs pagines par 50, soit 2 349 pages,
alors que get_all plafonne a 500 : 78 % des joueurs n'etaient jamais
rafraichis. Bastoni y portait encore current_team_api_id = 2697 et
current_team_name = "Gimnastica Torrelavega" alors que l'API rend
current_team.id = 77.
"""
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.sync_players import (
    build_player_values,
    sync_players_for_team,
)


def _joueur(pid, nom, team_id, team_nom, api_id=None):
    ct = {"id": team_id, "name": team_nom}
    if api_id is not None:
        ct["api_id"] = api_id
    return {"id": pid, "name": nom, "current_team": ct}


def _session():
    s = MagicMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    return s


def test_identifiant_de_l_espace_evenements_prioritaire():
    """current_team.id fait foi ; api_id releve d'un autre espace.

    L'ancien code lisait api_id en premier, d'ou des rattachements faux :
    Bastoni -> 2697 -> "Gimnastica Torrelavega".
    """
    from datetime import UTC, datetime

    v = build_player_values(
        _joueur(1, "Alessandro Bastoni", 77, "Inter", api_id=2697),
        datetime.now(UTC),
    )
    assert v["current_team_api_id"] == 77
    assert v["current_team_name"] == "Inter"


def test_joueur_sans_identifiant_est_ignore():
    from datetime import UTC, datetime

    assert build_player_values({"name": "Sans id"}, datetime.now(UTC)) is None


async def test_charge_l_effectif_d_un_club():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[
        _joueur(1, "Alessandro Bastoni", 77, "Inter"),
        _joueur(2, "Ange-Yoan Bonny", 77, "Inter"),
    ])

    n = await sync_players_for_team(_session(), client, 77)

    assert n == 2
    client.get_all.assert_called_once_with("/api/players/", {"team": 77})


async def test_club_sans_joueur_n_ecrit_rien():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])
    session = _session()

    n = await sync_players_for_team(session, client, 999)

    assert n == 0
    session.commit.assert_not_called()


async def test_sync_players_parcourt_le_referentiel():
    """96 appels au lieu de 2 349 pages, et rien hors perimetre."""
    import app.ingestion.bzzoiro.sync_players as mod

    session = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = [77, 63, 62]
    session.execute = AsyncMock(return_value=res)
    session.commit = AsyncMock()

    vus = []

    async def _stub(sess, cl, team_api_id):
        vus.append(team_api_id)
        return 25

    original = mod.sync_players_for_team
    mod.sync_players_for_team = _stub
    try:
        total = await mod.sync_players(session, MagicMock())
    finally:
        mod.sync_players_for_team = original

    assert vus == [77, 63, 62]
    assert total == 75


async def test_referentiel_vide_avertit_sans_planter():
    """Sans referentiel, on previent au lieu d'echouer silencieusement."""
    import app.ingestion.bzzoiro.sync_players as mod

    session = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=res)

    assert await mod.sync_players(session, MagicMock()) == 0
