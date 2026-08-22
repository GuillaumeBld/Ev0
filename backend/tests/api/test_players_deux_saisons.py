"""Page Joueurs : saison en cours au-dessus de la precedente.

La structure ne bouge pas — les clubs et effectifs restent ceux de la saison
en cours, ce sont les joueurs actifs a pricer. Seules les statistiques suivent
la saison demandee.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.players import saison_precedente


def test_saison_precedente():
    assert saison_precedente("2026-2027") == "2025-2026"
    assert saison_precedente("2021-2022") == "2020-2021"


def test_saison_precedente_refuse_un_format_invalide():
    with pytest.raises(ValueError):
        saison_precedente("2026")
    with pytest.raises(ValueError):
        saison_precedente("2026-2028")


async def test_les_equipes_viennent_toujours_de_la_saison_en_cours():
    """Demander les stats de 2025-2026 ne doit pas changer la liste des clubs.

    Ce sont les effectifs actuels qu'on price ; la saison demandee ne concerne
    que les statistiques affichees.
    """
    import app.api.players as mod

    vus = []

    async def _ids(session, league_api_id, season):
        vus.append(season)
        return [63, 77]

    async def _noms(session, ids):
        return [{"api_id": 63, "name": "AC Milan"}]

    async def _courante(session):
        return "2026-2027"

    o = (mod.team_ids_for_league, mod._nommer_clubs, mod.current_season)
    mod.team_ids_for_league, mod._nommer_clubs, mod.current_season = _ids, _noms, _courante
    try:
        await mod.list_player_teams(
            session=MagicMock(), league_api_id=4, season="2025-2026"
        )
    finally:
        mod.team_ids_for_league, mod._nommer_clubs, mod.current_season = o

    # la resolution s'est faite sur la saison COURANTE, pas sur celle demandee
    assert vus == ["2026-2027"]


def test_construit_le_bloc_de_comparaison():
    """Chaque joueur porte les valeurs de la saison precedente sous les siennes."""
    from app.api.players import bloc_precedent

    merged = {
        "season": "2025-2026", "matches_played": 38, "minutes_played": 3200,
        "goals": 14, "goal_assist": 3, "xg_per_90": 0.3668,
        "xa_per_90": 0.11, "avg_rating": 6.9811,
    }
    b = bloc_precedent(merged)
    assert b["season"] == "2025-2026"
    assert b["matches_played"] == 38
    assert b["goals"] == 14
    assert b["xg_per_90"] == pytest.approx(0.3668)
    assert b["avg_rating"] == pytest.approx(6.9811)


def test_bloc_precedent_absent_rend_none():
    from app.api.players import bloc_precedent

    assert bloc_precedent(None) is None
