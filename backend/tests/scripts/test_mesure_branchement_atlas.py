"""Mesure du branchement : ratios et formatage (fonctions pures, sans base)."""
from app.scripts.mesure_branchement_atlas import (
    OBJECTIFS, FixtureRow, calc_ecart_effectif, calc_ecussons_manquants,
    calc_equipes_reliees, calc_mal_ranges, calc_minutes, calc_saisons,
    ligne_cotes, ligne_ecussons, ligne_effectif, ligne_equipes,
    ligne_mal_ranges, ligne_minutes, ligne_saisons, pct, team_slots,
)


def _fx(i, h, a, league="premier_league", comp=1, bzz=1):
    return FixtureRow(i, h, a, league, comp, bzz)


def test_pct_sans_denominateur_est_none():
    assert pct(0, 0) is None
    assert pct(1, 4) == 25.0


def test_equipes_reliees():
    slots = team_slots([_fx(1, 10, 11), _fx(2, 12, None)])
    assert slots == [10, 11, 12, None]
    assert calc_equipes_reliees(slots, {10, 11}) == (2, 4)
    assert ligne_equipes(2, 4) == "1. Équipes reliées à l'Atlas : 2/4 (50.0 %)"


def test_ecart_effectif():
    assert calc_ecart_effectif({1, 2, 3}, {2, 3, 4, 5}) == (2, 1, 4)
    assert "0 manquants, 0 en trop" in ligne_effectif(0, 0, 10)


def test_minutes_ignorees_zero_quand_toutes_lignes_comptees():
    rows = [(1, "2026-2027", 300), (1, "2026-2027", 200), (2, "2025-2026", 900)]
    comptees, totales = calc_minutes(rows, "2026-2027")
    assert (comptees, totales) == (500, 500)
    assert ligne_minutes(comptees, totales).startswith("3. Minutes ignorées : 0.0 %")


def test_minutes_ignorees_non_nulles_affichees():
    assert "20.0 %" in ligne_minutes(80, 100)
    assert "n/a" in ligne_minutes(0, 0)


def test_saisons_anciennes_et_sans_stat():
    rows = [(1, "2026-2027", 10), (2, "2025-2026", 10), (2, "2024-2025", 10)]
    assert calc_saisons({1, 2, 3}, rows, "2026-2027") == (1, 1, 3)
    assert "1/3 (33.3 %)" in ligne_saisons(1, 1, 3)


def test_ecussons_manquants():
    slots = [1, 2, 3, 4]
    crests = {1: (None, None), 2: ("u", None), 3: (None, 5)}
    assert calc_ecussons_manquants(slots, crests) == (1, 4)
    assert ligne_ecussons(1, 4) == "5. Écussons manquants : 1 places sur 4"


def test_mal_ranges():
    cle = lambda i: {1: "premier_league", 8: "comp_8"}[i]
    fixtures = [
        _fx(1, 1, 2, "premier_league", 1, 1),   # bon
        _fx(2, 1, 2, "la_liga", 8, 8),          # mauvaise etiquette
        _fx(3, 1, 2, "comp_8", None, 8),        # competition vide
        _fx(4, 1, 2, "comp_8", 1, 8),           # competition differente
    ]
    assert calc_mal_ranges(fixtures, cle) == (3, 4)
    assert ligne_mal_ranges(3, 4) == "6. Matchs mal rangés : 3 sur 4"


def test_cotes_et_objectifs():
    assert ligne_cotes(95, 100).endswith("95/100 (95.0 %)")
    assert "mal rangés 0" in OBJECTIFS and "≥ 95 %" in OBJECTIFS
