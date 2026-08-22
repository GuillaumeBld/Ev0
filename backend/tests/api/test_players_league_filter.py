"""Le filtre de championnat resout par identifiant, jamais par nom de club.

_get_team_dominant_leagues groupait les joueurs par current_team_name — faux
pour 886 joueurs sur 2 401 — d'ou des clubs andorrans dans le filtre Ligue des
champions, "Alcione Milano" en Serie A, et le vrai Milan absent d'Italie.
"""
from unittest.mock import AsyncMock, MagicMock

from app.api.players import team_ids_for_league


def _session_rendant(ids):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(ids)
    session.execute = AsyncMock(return_value=result)
    return session


async def test_rend_les_identifiants_du_championnat():
    session = _session_rendant([63, 77, 62])
    assert await team_ids_for_league(session, 4, "2026-2027") == [63, 77, 62]


async def test_championnat_sans_club_rend_une_liste_vide():
    assert await team_ids_for_league(_session_rendant([]), 99, "2026-2027") == []


async def test_la_requete_filtre_sur_le_championnat_et_la_saison():
    session = _session_rendant([63])
    await team_ids_for_league(session, 4, "2026-2027")

    requete = str(session.execute.call_args.args[0])
    assert "canonical_teams" in requete
    assert "league_api_id" in requete
    assert "season" in requete
    # aucune resolution par nom de club
    assert "current_team_name" not in requete


def test_la_deduction_par_nom_a_disparu():
    """La fonction n'avait de raison d'etre qu'en l'absence de colonne."""
    import app.api.players as mod

    assert not hasattr(mod, "_get_team_dominant_leagues")
    assert not hasattr(mod, "_MIN_PLAYERS_FOR_TARGET_LEAGUE")


# ---------------------------------------------------------------------------
# Saisons passees : canonical_teams ne porte que l'engagement courant
# ---------------------------------------------------------------------------


async def test_saison_passee_derive_du_calendrier():
    """Sans engagement enregistre, on derive de bzz_events plutot que de
    rendre une liste vide — sinon tout l'historique devient infiltrable."""
    import app.api.players as mod

    session = MagicMock()
    vide = MagicMock()
    vide.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=vide)

    appels = []

    async def _repli(sess, league_api_id, season):
        appels.append((league_api_id, season))
        return [63, 77]

    original = mod._team_ids_from_events
    mod._team_ids_from_events = _repli
    try:
        ids = await team_ids_for_league(session, 4, "2024-2025")
    finally:
        mod._team_ids_from_events = original

    assert ids == [63, 77]
    assert appels == [(4, "2024-2025")]


async def test_saison_courante_n_utilise_pas_le_repli():
    """Quand l'engagement existe, il fait foi — segmentation stricte."""
    import app.api.players as mod

    session = _session_rendant([63, 77, 62])
    appele = []

    async def _repli(sess, league_api_id, season):
        appele.append(True)
        return []

    original = mod._team_ids_from_events
    mod._team_ids_from_events = _repli
    try:
        ids = await team_ids_for_league(session, 4, "2026-2027")
    finally:
        mod._team_ids_from_events = original

    assert ids == [63, 77, 62]
    assert appele == []


async def test_repli_borne_la_saison_des_deux_cotes():
    """Sans borne haute, une saison passee ramasserait les suivantes."""
    import app.api.players as mod

    session = MagicMock()
    res = MagicMock()
    res.all.return_value = [(63,), (77,)]
    session.execute = AsyncMock(return_value=res)

    ids = await mod._team_ids_from_events(session, 4, "2024-2025")

    assert ids == [63, 77]
    requete = str(session.execute.call_args.args[0])
    assert "bzz_events" in requete
    assert requete.count("event_date") >= 2


# ---------------------------------------------------------------------------
# Liste d'equipes : le frontend interroge la saison PRECEDENTE
# ---------------------------------------------------------------------------


async def test_liste_equipes_saison_passee_n_est_pas_vide():
    """Le frontend demande season=2025-2026 ; le referentiel ne porte que
    2026-2027. Sans repli, le selecteur d'equipe etait vide."""
    import app.api.players as mod

    async def _ids(sess, league_api_id, season):
        return [63, 77]

    async def _noms(sess, ids):
        return [{"api_id": 63, "name": "AC Milan"}, {"api_id": 77, "name": "Inter"}]

    o1, o2 = mod.team_ids_for_league, mod._nommer_clubs
    mod.team_ids_for_league, mod._nommer_clubs = _ids, _noms
    try:
        res = await mod.list_player_teams(
            session=MagicMock(), league_api_id=4, season="2025-2026"
        )
    finally:
        mod.team_ids_for_league, mod._nommer_clubs = o1, o2

    assert res == [
        {"api_id": 63, "name": "AC Milan"},
        {"api_id": 77, "name": "Inter"},
    ]


async def test_nommer_clubs_prefere_le_referentiel():
    import app.api.players as mod

    session = MagicMock()
    canon = MagicMock()
    canon.all.return_value = [(63, "AC Milan"), (77, "Inter")]
    session.execute = AsyncMock(return_value=canon)

    res = await mod._nommer_clubs(session, [63, 77])

    assert res == [
        {"api_id": 63, "name": "AC Milan"},
        {"api_id": 77, "name": "Inter"},
    ]


async def test_nommer_clubs_replie_puis_rend_un_libelle_de_secours():
    """Un club inconnu du referentiel garde une ligne plutot que disparaitre."""
    import app.api.players as mod

    session = MagicMock()
    canon = MagicMock()
    canon.all.return_value = [(63, "AC Milan")]
    joueurs = MagicMock()
    joueurs.all.return_value = [(77, "Inter Milan")]
    session.execute = AsyncMock(side_effect=[canon, joueurs])

    res = await mod._nommer_clubs(session, [63, 77, 999])

    noms = {x["api_id"]: x["name"] for x in res}
    assert noms[63] == "AC Milan"
    assert noms[77] == "Inter Milan"
    assert noms[999] == "Club 999"
    # tri par nom
    assert [x["name"] for x in res] == sorted(noms.values())


async def test_repli_refuse_les_competitions_sans_format_connu():
    """La Ligue des champions rendait 141 clubs, tours preliminaires compris.

    Le calendrier melange preliminaires et phase principale, et aucun seuil de
    matchs joues ne les separe (71 clubs encore a 8 matchs). Une liste vide
    est visible et se corrige ; 141 clubs induisent en erreur.
    """
    import app.api.players as mod

    session = MagicMock()
    vide = MagicMock()
    vide.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=vide)

    appele = []

    async def _repli(sess, league_api_id, season):
        appele.append(league_api_id)
        return [1, 2, 3]

    original = mod._team_ids_from_events
    mod._team_ids_from_events = _repli
    try:
        ldc = await team_ids_for_league(session, 7, "2025-2026")
        serie_a = await team_ids_for_league(session, 4, "2025-2026")
    finally:
        mod._team_ids_from_events = original

    assert ldc == []          # LDC : pas de repli
    assert serie_a == [1, 2, 3]  # championnat : repli applique
    assert appele == [4]
