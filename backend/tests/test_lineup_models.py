from app.models.lineups import TeamLineup, TeamLineupPlayer


def test_team_lineup_fields():
    lu = TeamLineup(fixture_id=1, team="psg", lineup_type="probable_manual")
    assert lu.lineup_type == "probable_manual"


def test_team_lineup_player_defaults():
    p = TeamLineupPlayer(lineup_id=1, player_name="Donnarumma", position="GK")
    assert p.is_starter is True
    assert p.jersey_number is None
