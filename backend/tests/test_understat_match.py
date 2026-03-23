"""Unit tests for understat_match team normalization and event conversion."""
from app.ingestion.understat_match import (
    UNDERSTAT_TEAM_MAP,
    norm_understat_team,
    _roster_to_events,
    PlayerMatchRow,
)


def test_norm_understat_team_lowercase_and_strip():
    assert norm_understat_team("  PSG  ") == "psg"


def test_norm_understat_team_maps_known_names():
    assert norm_understat_team("Olympique de Marseille") == "marseille"
    assert norm_understat_team("AC Milan") == "milan"
    assert norm_understat_team("1. FC Union Berlin") == "union berlin"


def test_norm_understat_team_passthrough_unknown():
    assert norm_understat_team("Paris Saint-Germain") == "paris saint-germain"


def test_team_map_covers_all_leagues():
    assert "olympique de marseille" in UNDERSTAT_TEAM_MAP  # Ligue 1
    assert "1. fc union berlin" in UNDERSTAT_TEAM_MAP       # Bundesliga
    assert "ac milan" in UNDERSTAT_TEAM_MAP                 # Serie A
    assert "tottenham" in UNDERSTAT_TEAM_MAP                # Premier League


def test_roster_to_events_goal():
    roster = [PlayerMatchRow(player_name="Mbappé", team_side="h", minutes=90, goals=2, assists=0)]
    events = _roster_to_events(roster)
    assert len(events) == 1
    assert events[0] == {"player_name": "Mbappé", "event_type": "goal", "minute": None}


def test_roster_to_events_assist():
    roster = [PlayerMatchRow(player_name="Griezmann", team_side="h", minutes=90, goals=0, assists=1)]
    events = _roster_to_events(roster)
    assert len(events) == 1
    assert events[0] == {"player_name": "Griezmann", "event_type": "assist", "minute": None}


def test_roster_to_events_both():
    roster = [PlayerMatchRow(player_name="Messi", team_side="h", minutes=90, goals=1, assists=1)]
    events = _roster_to_events(roster)
    assert len(events) == 2
    types = {e["event_type"] for e in events}
    assert types == {"goal", "assist"}


def test_roster_to_events_no_contribution():
    roster = [PlayerMatchRow(player_name="Lloris", team_side="h", minutes=90, goals=0, assists=0)]
    events = _roster_to_events(roster)
    assert events == []


def test_roster_to_events_empty_name_skipped():
    roster = [PlayerMatchRow(player_name="", team_side="h", minutes=90, goals=1, assists=0)]
    events = _roster_to_events(roster)
    assert events == []
