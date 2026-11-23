"""Ecussons et competition Atlas exposes sur les reponses match."""
import pytest

from app.api._badges import TeamBadgeOut, badge_fields, badge_out
from app.services.atlas_source import TeamBadge
from app.services.competitions import competition_name


def _modeles():
    from app.api.fixtures import FixtureOut
    from app.api.history import HistoryItem
    from app.api.lineups import FixtureLineupsOut
    from app.api.pricing import MatchPriceResponse
    from app.api.recommendations import Recommendation
    from app.api.wc2026_matches import MatchDetail, MatchListItem

    return [
        FixtureOut, MatchPriceResponse, Recommendation, HistoryItem,
        FixtureLineupsOut, MatchListItem, MatchDetail,
    ]


@pytest.mark.parametrize("modele", _modeles(), ids=lambda m: m.__name__)
def test_modele_match_porte_ecussons_et_competition(modele):
    for champ in ("home_badge", "away_badge", "competition_api_id", "competition_name"):
        assert champ in modele.model_fields, champ
        assert modele.model_fields[champ].default is None


def test_badge_out_none():
    assert badge_out(None) is None


def test_badge_out_convertit():
    b = badge_out(TeamBadge("Paris SG", "http://x/psg.png", 85, "FR"))
    assert b == TeamBadgeOut(
        name_fr="Paris SG", crest_url="http://x/psg.png", api_football_id=85, country_iso="FR"
    )


def test_badge_fields_club_inconnu_reste_none():
    badges = {1: TeamBadge("A", None, None, None)}
    f = badge_fields(badges, 1, 2, 61)
    assert f["home_badge"].name_fr == "A"
    assert f["away_badge"] is None
    assert f["competition_api_id"] == 61
    assert f["competition_name"] == competition_name(61)


def test_badge_fields_sans_ids():
    f = badge_fields({}, None, None, None)
    assert f == {
        "home_badge": None, "away_badge": None,
        "competition_api_id": None, "competition_name": None,
    }
