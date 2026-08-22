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
