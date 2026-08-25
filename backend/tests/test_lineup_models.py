from app.models.lineups import TeamLineup, TeamLineupPlayer


def test_team_lineup_fields():
    lu = TeamLineup(fixture_id=1, team="psg", lineup_type="probable_manual")
    assert lu.lineup_type == "probable_manual"


def test_team_lineup_player_defaults():
    p = TeamLineupPlayer(lineup_id=1, player_name="Donnarumma", position="GK")
    assert p.is_starter is True
    assert p.jersey_number is None


def test_team_lineup_porte_sa_publication():
    """Savoir si une compo etait officielle AU MOMENT du calcul.

    lineup_type dit d'ou vient la compo et sert la priorite du resolveur ;
    lineup_status conserve ce que Bzzoiro declare, et published_at l'heure de
    publication.
    """
    from app.models.lineups import TeamLineup

    cols = TeamLineup.__table__.columns
    assert "lineup_status" in cols
    assert "published_at" in cols
    # Nullables : les compos manuelles n'ont pas de statut Bzzoiro.
    assert cols["lineup_status"].nullable is True
    assert cols["published_at"].nullable is True
