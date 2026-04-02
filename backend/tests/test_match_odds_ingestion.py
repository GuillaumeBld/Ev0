import pytest


def test_match_odds_snapshot_model_importable():
    from app.models.match_odds import MatchOddsSnapshot
    snap = MatchOddsSnapshot(
        fixture_id=1,
        bookmaker="betfair",
        market_type="totals",
        outcome="over_2.5",
        odds=1.85,
    )
    assert snap.bookmaker == "betfair"
    assert snap.market_type == "totals"
    assert snap.outcome == "over_2.5"
    assert snap.odds == 1.85
