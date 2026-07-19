"""_parse_incidents émet désormais les substitutions (entrant + sortant)."""

from app.ingestion.bzzoiro.sync_incidents import _parse_incidents
from app.models.match_events import MatchEvent


def test_modele_a_la_colonne_related_player_name():
    assert hasattr(MatchEvent, "related_player_name")


def test_parse_substitution_nominale():
    raw = [{
        "incidentType": "substitution",
        "playerIn": {"name": "Gonçalo Ramos"},
        "playerOut": {"name": "Ousmane Dembélé"},
        "time": 63,
    }]
    rows = _parse_incidents(raw)
    assert rows == [{
        "player_name": "Gonçalo Ramos",
        "event_type": "substitution",
        "minute": 63,
        "related_player_name": "Ousmane Dembélé",
    }]


def test_parse_substitution_cles_alternatives():
    """Certains payloads utilisent player/relatedPlayer — parsing défensif."""
    raw = [{
        "incidentType": "substitution",
        "player": {"name": "Warren Zaïre-Emery"},
        "relatedPlayer": {"shortName": "Vitinha"},
        "minute": 75,
    }]
    rows = _parse_incidents(raw)
    assert rows[0]["player_name"] == "Warren Zaïre-Emery"
    assert rows[0]["related_player_name"] == "Vitinha"
    assert rows[0]["minute"] == 75


def test_substitution_incomplete_ignoree_sans_crash():
    raw = [{"incidentType": "substitution", "playerIn": {"name": "X"}, "time": 80}]
    assert _parse_incidents(raw) == []


def test_les_buts_portent_related_player_name_none():
    raw = [{"incidentType": "goal", "player": {"name": "Mbappé"}, "time": 12}]
    rows = _parse_incidents(raw)
    assert rows[0]["related_player_name"] is None
