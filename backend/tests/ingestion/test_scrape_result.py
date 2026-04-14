from datetime import datetime, timezone
from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds


def test_match_scrape_result_defaults():
    r = MatchScrapeResult(
        fixture_id=1,
        home_team="PSG",
        away_team="Lyon",
        kickoff_utc=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
    )
    assert r.h2h is None
    assert r.totals is None
    assert r.btts is None
    assert r.goalscorer == []
    assert r.assist == []


def test_player_odds_fields():
    p = PlayerOdds(player_name="Mbappé", odds=3.5)
    assert p.player_name == "Mbappé"
    assert p.odds == 3.5


def test_is_complete_all_markets():
    r = MatchScrapeResult(
        fixture_id=1, home_team="A", away_team="B",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
        btts={"yes": 1.75, "no": 2.1},
    )
    assert r.is_complete is True


def test_is_complete_missing_btts():
    r = MatchScrapeResult(
        fixture_id=1, home_team="A", away_team="B",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
    )
    assert r.is_complete is False
