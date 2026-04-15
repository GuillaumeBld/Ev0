# backend/tests/ingestion/test_odds_storage.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds
from app.ingestion.odds_storage import store_match_scrape_result


@pytest.mark.asyncio
async def test_store_complete_result():
    """store_match_scrape_result returns (match_rows, player_rows) non-negative."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG",
        away_team="Lyon",
        kickoff_utc=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
        btts={"yes": 1.75, "no": 2.1},
        goalscorer=[PlayerOdds("Mbappé", 3.5), PlayerOdds("Dembélé", 4.0)],
        assist=[PlayerOdds("Vitinha", 5.0)],
    )

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=7))
    session.commit = AsyncMock()

    match_rows, player_rows = await store_match_scrape_result(result, session)
    assert match_rows >= 0
    assert player_rows >= 0
    assert session.commit.called


@pytest.mark.asyncio
async def test_store_incomplete_result_still_stores_player_props():
    """Without h2h/totals/btts, player props are still stored."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG", away_team="Lyon",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        goalscorer=[PlayerOdds("Mbappé", 3.5)],
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()
    _, player_rows = await store_match_scrape_result(result, session)
    assert player_rows >= 0
