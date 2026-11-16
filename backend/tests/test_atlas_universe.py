"""Tests des briques pures de l'univers Atlas (`atlas_universe.py`, `countries.py`)."""
from __future__ import annotations

from datetime import date

from app.data.countries import country_iso, country_meta, country_name_fr, fold
from app.ingestion import atlas_universe as u
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY


def _row(team=None, nat=None):
    return {"current_team": team, "national_team": nat}


def test_directories_keep_majority_name_and_never_mix_clubs_and_nations():
    rows = [
        _row({"id": 63, "name": "AC Milan", "country": "Italy"}, {"id": 900, "name": "France"}),
        _row({"id": 63, "name": "AC Milan", "country": "Italy"}),
        _row({"id": 63, "name": "Milan", "country": "Italy"}),
        # un id vu comme sélection n'est jamais un club
        _row({"id": 900, "name": "France", "country": "France"}),
    ]
    clubs, nations = u.build_directories(rows)
    assert clubs == {63: ("AC Milan", "Italy")}
    assert nations == {900: ("France", "France")}


def test_vote_requires_enough_known_players_and_a_majority():
    tm_to_player = {1: 10, 2: 11, 3: 12, 4: 13}
    team = {10: 500, 11: 500, 12: 500, 13: 600}
    assert u.vote_bzz_team([1, 2, 3, 4, 99], tm_to_player, team) == 500
    assert u.vote_bzz_team([1, 2], tm_to_player, team) is None  # trop peu de joueurs connus
    split = {10: 500, 11: 600, 12: 700, 13: 800}
    assert u.vote_bzz_team([1, 2, 3, 4], tm_to_player, split) is None  # pas de majorité


def test_external_league_ids_are_stable_distinct_and_outside_the_registry():
    portal = {u.portal_league_id(c) for c in u.PORTAL_ONLY_LEAGUES}
    assert len(portal) == len(u.PORTAL_ONLY_LEAGUES)
    assert min(portal) == u.PORTAL_LEAGUE_BASE + 1
    assert max(portal) < u.OTHER_LEAGUE_BASE
    assert u.other_league_id("AA") == u.OTHER_LEAGUE_BASE
    assert u.other_league_id("FR") == u.OTHER_LEAGUE_BASE + 5 * 26 + 17
    registry_ids = set(LEAGUE_REGISTRY)
    assert not portal & registry_ids
    assert all(not u.is_external_league(i) for i in registry_ids)
    assert u.is_external_league(u.other_league_id("ZW")) and not u.is_external_league(None)


def test_portal_codes_known_to_bzzoiro_point_into_the_registry():
    assert set(u.PORTAL_TO_REGISTRY.values()) <= set(LEAGUE_REGISTRY)
    assert not set(u.PORTAL_TO_REGISTRY) & set(u.PORTAL_ONLY_LEAGUES)


def test_age_at_handles_birthday_not_yet_reached():
    assert u.age_at(date(2000, 11, 15), date(2026, 11, 14)) == 25
    assert u.age_at(date(2000, 11, 14), date(2026, 11, 14)) == 26
    assert u.age_at(None, date(2026, 11, 14)) is None


def test_countries_resolve_every_spelling_to_one_iso_code():
    assert country_iso("Côte d'Ivoire") == country_iso("Cote d'Ivoire") == "CI"
    assert country_iso("England") == country_iso("Scotland") == "GB"
    assert country_iso("Türkiye") == country_iso("Turkey") == "TR"
    assert country_iso("USA") == "US"
    assert country_iso("Atlantis") is None and country_iso(None) is None
    assert country_meta("Nigeria") == ("NG", "AFR")
    assert country_name_fr("DE") == "Allemagne"
    assert fold("  Ñandú Équipe ") == fold("nandu equipe")


def test_reconciliation_prefers_exact_name_then_a_clear_vote():
    from types import SimpleNamespace as T

    ourense_cf = T(bzz_team_id=1453, name_en="Ourense CF")
    ud_ourense = T(bzz_team_id=1481, name_en="Unión Deportiva Ourense")
    shakhtar = T(bzz_team_id=402, name_en="Shakhtar Donetsk")
    homonyme = T(bzz_team_id=3313, name_en="Shakhtar")
    exact = {
        ("ES", "ourense cf"): [ourense_cf],
        ("UA", "shakhtar donetsk"): [shakhtar],
        ("UA", "shakhtar"): [homonyme],
    }
    by_bzz = {r.bzz_team_id: r for r in (ourense_cf, ud_ourense, shakhtar, homonyme)}
    # UD Ourense : 17 joueurs rangés par Bzzoiro sous Ourense CF, 15 sous UD Ourense
    tm_to_player = {i: i for i in range(1, 40)}
    team = {**{i: 1453 for i in range(1, 18)}, **{i: 1481 for i in range(18, 33)}}
    clubs = {
        55398: ("Ourense CF", "Spain", [], 9006),
        60225: ("UD Ourense", "Spain", list(range(1, 33)), 9006),
        660: ("Shakhtar Donetsk", "Ukraine", [], 9027),
    }
    wanted = u.plan_portal_reconciliation(clubs, exact, tm_to_player, team, by_bzz)
    assert wanted[55398] is ourense_cf  # nom identique
    assert wanted[60225] is ud_ourense  # vote net une fois Ourense CF attribué
    assert wanted[660] is shakhtar  # nom complet, pas l'homonyme


def test_reconciliation_ignores_a_tight_vote():
    from types import SimpleNamespace as T

    a, b = T(bzz_team_id=1, name_en="A"), T(bzz_team_id=2, name_en="B")
    team = {**{i: 1 for i in range(6)}, **{i: 2 for i in range(6, 11)}}
    wanted = u.plan_portal_reconciliation(
        {9: ("Club", "Spain", list(range(11)), 9006)}, {}, {i: i for i in range(11)}, team, {1: a, 2: b},
    )
    assert wanted == {}


def test_squad_country_takes_a_clear_majority_only():
    from collections import Counter

    assert u.squad_country(Counter({"Bahrain": 20, "Brazil": 5, None: 3})) == "BH"
    assert u.squad_country(Counter({"England": 3, "Scotland": 2})) == "GB"  # même code carte
    assert u.squad_country(Counter({"France": 4, "Spain": 4, "Italy": 4})) is None
    assert u.squad_country(Counter({"Bahrain": 2})) is None  # trop peu de joueurs
    # effectif international : Thaïlande 8, puis 3 Brésiliens, 1 de chaque ailleurs
    buriram = Counter({"Thailand": 8, "Brazil": 3, **{c: 1 for c in ("Iran", "Ghana", "Spain", "Serbia", "Romania", "Germany", "Japan", "Australia", "Philippines", "Montenegro")}})
    assert u.squad_country(buriram) == "TH"
    assert u.squad_country(None) is None
