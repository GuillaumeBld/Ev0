"""Tests for /wc2026 API endpoints."""

import re
import unicodedata
from unittest.mock import MagicMock

from app.api.wc2026 import (
    _WC_SORT_FIELD_MAP,
    _build_squad_response,
    _group_by_position,
    _normalize_name,
    _row_to_player_dict,
)


def _make_player(
    nation: str = "France",
    group_letter: str = "E",
    player_name: str = "Mbappé",
    club: str = "Real Madrid",
    position: str = "FWD",
    shirt_number: int = 10,
    flag_emoji: str = "🇫🇷",
) -> MagicMock:
    p = MagicMock()
    p.nation = nation
    p.group_letter = group_letter
    p.player_name = player_name
    p.club = club
    p.position = position
    p.shirt_number = shirt_number
    p.flag_emoji = flag_emoji
    return p


def test_group_by_position_separates_positions():
    players = [
        _make_player(position="GK", player_name="Maignan"),
        _make_player(position="DEF", player_name="Saliba"),
        _make_player(position="MID", player_name="Kanté"),
        _make_player(position="FWD", player_name="Mbappé"),
    ]
    grouped = _group_by_position(players)
    assert len(grouped["gk"]) == 1
    assert len(grouped["def_"]) == 1
    assert len(grouped["mid"]) == 1
    assert len(grouped["fwd"]) == 1
    assert grouped["gk"][0]["player_name"] == "Maignan"


def test_group_by_position_unknown_falls_to_mid():
    players = [_make_player(position="???")]
    grouped = _group_by_position(players)
    assert len(grouped["mid"]) == 1


def test_build_squad_response_structure():
    players = [
        _make_player(position="GK", player_name="Maignan"),
        _make_player(position="FWD", player_name="Mbappé"),
    ]
    resp = _build_squad_response("France", "E", players)
    assert resp["nation"] == "France"
    assert resp["group_letter"] == "E"
    assert len(resp["gk"]) == 1
    assert len(resp["fwd"]) == 1
    assert resp["gk"][0]["player_name"] == "Maignan"
    assert resp["gk"][0]["club"] == "Real Madrid"
    assert resp["gk"][0]["shirt_number"] == 10


def test_normalize_name_accents():
    assert _normalize_name("Dembélé") == _normalize_name("Dembele")
    assert _normalize_name("M'Bappé") == "mbappe"
    assert _normalize_name("Van Dijk") == "van dijk"
    assert _normalize_name("Müller") == "muller"


def test_row_to_player_dict_stats_present():
    row = {
        "player_name": "Mbappé",
        "club": "Real Madrid",
        "position": "FWD",
        "shirt_number": 10,
        "flag_emoji": "🇫🇷",
        "matches_played": 30,
        "minutes_played": 2500,
        "goals": 25,
        "assists": 8,
        "xg": 22.5,
        "xa": 6.1,
        "xg_per90": 0.81,
        "xa_per90": 0.22,
        "avg_rating": 7.4,
        "saves": None,
        "form_goals_5": 3,
        "form_xg_5": 2.1,
        "form_rating_5": 7.8,
    }
    d = _row_to_player_dict(row)
    assert d["goals"] == 25
    assert d["assists"] == 8
    assert d["xg_per90"] == 0.81
    assert d["saves"] is None


def test_row_to_player_dict_stats_null():
    row = {
        "player_name": "Inconnu",
        "club": None,
        "position": "MID",
        "shirt_number": None,
        "flag_emoji": None,
        "matches_played": None,
        "minutes_played": None,
        "goals": None,
        "assists": None,
        "xg": None,
        "xa": None,
        "xg_per90": None,
        "xa_per90": None,
        "avg_rating": None,
        "saves": None,
        "form_goals_5": None,
        "form_xg_5": None,
        "form_rating_5": None,
    }
    d = _row_to_player_dict(row)
    assert d["goals"] is None
    assert d["avg_rating"] is None
    assert d["player_name"] == "Inconnu"


def test_row_to_player_dict_gk_saves():
    row = {
        "player_name": "Maignan",
        "club": "AC Milan",
        "position": "GK",
        "shirt_number": 1,
        "flag_emoji": "🇫🇷",
        "matches_played": 30,
        "minutes_played": 2700,
        "goals": 0,
        "assists": 0,
        "xg": 0.0,
        "xa": 0.0,
        "xg_per90": 0.0,
        "xa_per90": 0.0,
        "avg_rating": 7.1,
        "saves": 89,
        "form_goals_5": 0,
        "form_xg_5": 0.0,
        "form_rating_5": 7.3,
    }
    d = _row_to_player_dict(row)
    assert d["saves"] == 89
    assert d["position"] == "GK"


def test_wc_sort_field_map_has_expected_keys():
    expected = {
        "goals", "assists", "xg", "xa", "xg_per90", "xa_per90",
        "avg_rating", "matches_played", "minutes_played", "saves",
        "form_xg_5", "form_goals_5", "form_rating_5",
    }
    assert expected.issubset(set(_WC_SORT_FIELD_MAP.keys()))


def test_wc_sort_field_map_values_are_safe_sql():
    import re
    for key, val in _WC_SORT_FIELD_MAP.items():
        assert ";" not in val, f"SQL injection risk for key {key!r}: {val!r}"
        # Allow COALESCE expressions (spaces in function calls are fine);
        # reject dangerous patterns like DROP, DELETE, --, etc.
        assert not re.search(r'\b(drop|delete|insert|update|truncate|--)\b', val, re.I), \
            f"Dangerous keyword in sort value for key {key!r}: {val!r}"


def test_row_to_player_dict_includes_nation_when_present():
    row = {
        "player_name": "Mbappé", "club": "Real Madrid", "position": "FWD",
        "shirt_number": 10, "nation": "France", "group_letter": "E",
        "matches_played": 30, "minutes_played": 2500, "goals": 25,
        "assists": 8, "xg": 22.5, "xa": 6.1, "xg_per90": 0.81,
        "xa_per90": 0.22, "avg_rating": 7.4, "saves": None,
        "form_goals_5": 3, "form_xg_5": 2.1, "form_rating_5": 7.8,
    }
    d = _row_to_player_dict(row)
    assert d["nation"] == "France"
    assert d["group_letter"] == "E"


def test_row_to_player_dict_nation_defaults_none_when_absent():
    row = {
        "player_name": "Mbappé", "club": "Real Madrid", "position": "FWD",
        "shirt_number": 10, "matches_played": None, "minutes_played": None,
        "goals": None, "assists": None, "xg": None, "xa": None,
        "xg_per90": None, "xa_per90": None, "avg_rating": None, "saves": None,
        "form_goals_5": None, "form_xg_5": None, "form_rating_5": None,
    }
    d = _row_to_player_dict(row)
    assert d["nation"] is None
    assert d["group_letter"] is None
