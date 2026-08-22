"""Liste des saisons disponibles — alimente le selecteur de la page Joueurs.

Rien n'est fige : la liste vient des donnees, sinon il faudrait la retoucher
chaque ete.
"""
from unittest.mock import AsyncMock, MagicMock

from app.api.players import list_player_seasons


def _session(saisons):
    session = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = list(saisons)
    session.execute = AsyncMock(return_value=res)
    return session


async def test_saisons_triees_de_la_plus_recente_a_la_plus_ancienne(monkeypatch):
    import app.api.players as mod

    async def _courante(session):
        return "2026-2027"

    monkeypatch.setattr(mod, "current_season", _courante)

    res = await list_player_seasons(
        session=_session(["2024-2025", "2026-2027", "2025-2026"])
    )
    assert [s["season"] for s in res] == ["2026-2027", "2025-2026", "2024-2025"]


async def test_la_saison_en_cours_est_signalee(monkeypatch):
    import app.api.players as mod

    async def _courante(session):
        return "2026-2027"

    monkeypatch.setattr(mod, "current_season", _courante)

    res = await list_player_seasons(session=_session(["2025-2026", "2026-2027"]))
    courantes = [s["season"] for s in res if s["current"]]
    assert courantes == ["2026-2027"]


async def test_saison_en_cours_ajoutee_meme_sans_statistiques(monkeypatch):
    """En debut de saison aucune stat n'existe encore : le selecteur doit
    malgre tout proposer la saison en cours."""
    import app.api.players as mod

    async def _courante(session):
        return "2026-2027"

    monkeypatch.setattr(mod, "current_season", _courante)

    res = await list_player_seasons(session=_session(["2025-2026"]))
    assert [s["season"] for s in res] == ["2026-2027", "2025-2026"]
    assert res[0]["current"] is True
