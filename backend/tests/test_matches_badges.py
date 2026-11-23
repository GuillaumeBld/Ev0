"""/api/v1/matches porte les ecussons et la competition (source unique Atlas)."""
import inspect

from app.api import lineups, matches
from app.api._badges import badge_fields
from app.api.history import HistoryItem
from app.api.recommendations import Recommendation
from app.services.atlas_source import TeamBadge


def test_list_matches_utilise_badge_fields_en_un_lot():
    src = inspect.getsource(matches.list_matches)
    assert "badge_fields(" in src
    assert src.count("team_badges(") == 1  # un seul SELECT pour toute la liste
    assert "e.league_api_id" in src


def test_badge_fields_champs_et_inconnu_reste_none():
    b = TeamBadge(name_fr="Lens", crest_url="u", api_football_id=1, country_iso="FR")
    out = badge_fields({7: b}, 7, 8, 999999)
    assert out["home_badge"].name_fr == "Lens"
    assert out["away_badge"] is None
    assert out["competition_api_id"] == 999999


def test_player_ids_exposes_pour_les_liens_fiche():
    assert "player_id" in Recommendation.model_fields
    assert "player_id" in HistoryItem.model_fields
    assert "player_api_id" in lineups.LineupPlayerOut.model_fields


def test_route_competitions_declaree_avant_la_route_dynamique():
    chemins = [r.path for r in matches.router.routes]
    assert "/matches/competitions" in chemins
    assert chemins.index("/matches/competitions") < chemins.index("/matches/{event_api_id}")
    src = inspect.getsource(matches.list_match_competitions)
    assert "distinct()" in src and "competition_name(" in src
