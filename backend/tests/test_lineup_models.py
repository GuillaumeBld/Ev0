from app.models.players import Player
from app.models.lineups import TeamLineup, TeamLineupPlayer


def test_player_is_striker_defaults_to_false():
    p = Player(name="Test", external_id="x1")
    assert p.is_striker is False


def test_player_is_striker_can_be_set():
    p = Player(name="Ramos", external_id="x2", is_striker=True)
    assert p.is_striker is True


def test_team_lineup_fields():
    lu = TeamLineup(fixture_id=1, team="psg", lineup_type="probable_manual")
    assert lu.lineup_type == "probable_manual"


def test_team_lineup_player_defaults():
    p = TeamLineupPlayer(lineup_id=1, player_name="Donnarumma", position="GK")
    assert p.is_starter is True
    assert p.jersey_number is None
