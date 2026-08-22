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
