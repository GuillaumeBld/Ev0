"""Plan de reparation des matchs mal ranges (logique pure)."""
from datetime import datetime, timedelta, timezone

from app.scripts.repair_fixture_competitions import FixtureRow, plan_repair

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
FUTUR = NOW + timedelta(days=3)
PASSE = NOW - timedelta(days=3)


def _row(id, league, comp, bzz, kickoff=FUTUR):
    return FixtureRow(id, league, comp, bzz, kickoff)


def test_match_mal_range_futur_reclasse_et_recos_a_supprimer():
    plan = plan_repair([_row(1, "premier_league", None, 17)], now=NOW)
    assert plan.relabel == {1: (17, "comp_17")}
    assert plan.recos_a_supprimer_fixture_ids == {1}


def test_match_mal_range_passe_reclasse_recos_conservees():
    plan = plan_repair([_row(1, "premier_league", None, 17, PASSE)], now=NOW)
    assert plan.relabel == {1: (17, "comp_17")}
    assert plan.recos_a_supprimer_fixture_ids == set()


def test_match_deja_correct_rien():
    assert plan_repair([_row(1, "premier_league", 1, 1)], now=NOW).relabel == {}


def test_match_juste_sans_colonne_renseigne_pas_de_suppression():
    plan = plan_repair([_row(1, "premier_league", None, 1)], now=NOW)
    assert plan.relabel == {1: (1, "premier_league")}
    assert plan.recos_a_supprimer_fixture_ids == set()


def test_match_non_relie_signale_sans_etre_touche():
    plan = plan_repair([_row(1, "premier_league", None, None)], now=NOW)
    assert plan.relabel == {}
    assert plan.non_relies == [1]


def test_idempotence():
    rows = [
        _row(1, "premier_league", None, 17),
        _row(2, "la_liga", 1, 8, PASSE),
        _row(3, "serie_a", None, 23),
        _row(4, "premier_league", None, 1),
    ]
    plan = plan_repair(rows, now=NOW)
    assert plan.relabel
    corriges = [
        _row(r.id, plan.relabel[r.id][1], plan.relabel[r.id][0], r.bzz_league_api_id, r.kickoff_utc)
        if r.id in plan.relabel else r
        for r in rows
    ]
    again = plan_repair(corriges, now=NOW)
    assert again.relabel == {} and again.recos_a_supprimer_fixture_ids == set()
