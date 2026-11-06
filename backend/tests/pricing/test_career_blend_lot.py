"""La version par lot du melange carriere doit rendre exactement la meme
chose que la version unitaire.

`blended_rhythms_for` existe parce que le pricing a besoin du rythme carriere
d'une cinquantaine de joueurs a chaque match, dans une boucle qui tourne
toutes les 60 secondes : un appel par joueur y serait trop couteux.

Deux implementations des memes regles finissent toujours par diverger. Ce
fichier est le verrou : si l'une evolue sans l'autre, le premier test casse.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pricing.career_blend import (
    MIN_SEASON_MINUTES,
    blended_rhythm,
    blended_rhythms_for,
)

# (joueur, saison, minutes, xg, xa)
LIGNES_BZZ = [
    (10, "2025-2026", 1800, 12.0, 4.0),
    (10, "2025-2026",  900,  6.0, 2.0),   # 2e competition, meme saison
    (10, "2024-2025", 2700, 15.0, 6.0),
    (10, "2023-2024",   45,  0.5, 0.1),   # sous le seuil -> ecartee du melange
    (20, "2025-2026",  120,  0.8, 0.3),
]

# (joueur, annee, buts, passes, minutes)
LIGNES_CARRIERE = [
    (10, 2023, 8, 3, 2400),   # 2023-2024 est couverte par bzz -> EXCLUE
    (10, 2021, 5, 2, 2000),   # non couverte -> prise
    (10, 2020, 4, 1, 1500),
    (20, 2022, 9, 4, 2600),
    (30, 2022, 3, 1, 1700),   # joueur sans aucune ligne bzz
]


def _session(bzz, carriere):
    """Session qui rend les lignes bzz puis les lignes carriere, dans l'ordre
    ou les deux fonctions les demandent."""
    appels = {"n": 0}

    async def execute(stmt, *a, **k):
        # L'ordre est le meme dans les deux implementations : bzz puis carriere.
        appels["n"] += 1
        res = MagicMock()
        res.all.return_value = bzz if appels["n"] == 1 else carriere
        return res

    s = MagicMock()
    s.execute = AsyncMock(side_effect=execute)
    return s


def _sans_colonne_joueur(lignes, pid):
    """Les lignes d'un seul joueur, au format de la version unitaire."""
    return [r[1:] for r in lignes if r[0] == pid]


@pytest.mark.asyncio
async def test_lot_identique_a_lunitaire(monkeypatch):
    """Le verrou. Pour chaque joueur, lot == unitaire, au flottant pres."""
    monkeypatch.setattr(
        "app.pricing.career_blend.current_season", AsyncMock(return_value="2026-2027")
    )

    lot = await blended_rhythms_for(_session(LIGNES_BZZ, LIGNES_CARRIERE), [10, 20, 30])

    for pid in (10, 20, 30):
        unitaire = await blended_rhythm(
            _session(_sans_colonne_joueur(LIGNES_BZZ, pid),
                     _sans_colonne_joueur(LIGNES_CARRIERE, pid)),
            pid,
        )
        obtenu = lot.get(pid)
        if unitaire is None:
            assert obtenu is None, f"joueur {pid} : le lot rend un rythme, l'unitaire non"
            continue
        assert obtenu is not None, f"joueur {pid} : l'unitaire rend un rythme, le lot non"
        assert obtenu.goal_rate_per_90 == pytest.approx(unitaire.goal_rate_per_90, rel=1e-9)
        assert obtenu.assist_rate_per_90 == pytest.approx(unitaire.assist_rate_per_90, rel=1e-9)
        assert obtenu.seasons_used == unitaire.seasons_used
        assert obtenu.has_career == unitaire.has_career


@pytest.mark.asyncio
async def test_une_saison_vue_cote_bzz_nest_jamais_reprise_de_la_carriere(monkeypatch):
    """Anti-double-comptage : 2023-2024 existe cote bzz (meme sous le seuil de
    minutes), donc la ligne de carriere 2023 doit etre ignoree -- sinon la
    meme saison compte deux fois, en xG puis en buts reels."""
    monkeypatch.setattr(
        "app.pricing.career_blend.current_season", AsyncMock(return_value="2026-2027")
    )
    lot = await blended_rhythms_for(_session(LIGNES_BZZ, LIGNES_CARRIERE), [10])
    r = lot[10]
    # bzz retenues : 2025-2026 et 2024-2025 (2023-2024 a 45 min < seuil).
    # carriere retenues : 2021 et 2020 (2023 exclue car couverte par bzz).
    assert r.seasons_used == 4
    assert r.has_career is True


@pytest.mark.asyncio
async def test_joueur_sans_aucune_donnee_absent_du_resultat(monkeypatch):
    """L'appelant doit pouvoir retomber sur l'a priori de poste."""
    monkeypatch.setattr(
        "app.pricing.career_blend.current_season", AsyncMock(return_value="2026-2027")
    )
    lot = await blended_rhythms_for(_session([], []), [99])
    assert 99 not in lot


@pytest.mark.asyncio
async def test_saison_sous_le_seuil_de_minutes_ecartee(monkeypatch):
    monkeypatch.setattr(
        "app.pricing.career_blend.current_season", AsyncMock(return_value="2026-2027")
    )
    maigre = [(50, "2025-2026", MIN_SEASON_MINUTES - 1, 5.0, 2.0)]
    lot = await blended_rhythms_for(_session(maigre, []), [50])
    assert 50 not in lot, "une saison sous le seuil ne doit rien produire"


@pytest.mark.asyncio
async def test_liste_vide_ne_declenche_aucune_requete():
    s = MagicMock()
    s.execute = AsyncMock()
    assert await blended_rhythms_for(s, []) == {}
    s.execute.assert_not_awaited()
