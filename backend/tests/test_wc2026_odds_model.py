"""Tests for WC2026OutrightOdd model."""

from app.models.wc2026_odds import WC2026OutrightOdd


def test_model_instantiation():
    obj = WC2026OutrightOdd(
        nation="France",
        player_name=None,
        market_type="winner",
        bookmaker="betclic",
        odds=4.5,
    )
    assert obj.nation == "France"
    assert obj.market_type == "winner"
    assert obj.odds == 4.5


def test_model_player_outright():
    obj = WC2026OutrightOdd(
        nation=None,
        player_name="Kylian Mbappé",
        market_type="top_scorer",
        bookmaker="unibet",
        odds=7.0,
    )
    assert obj.player_name == "Kylian Mbappé"
    assert obj.nation is None


def test_model_in_all():
    from app.models import WC2026OutrightOdd as Imported
    assert Imported is WC2026OutrightOdd
