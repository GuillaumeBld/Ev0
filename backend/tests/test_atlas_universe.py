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
