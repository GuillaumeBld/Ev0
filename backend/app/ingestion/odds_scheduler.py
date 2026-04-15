# backend/app/ingestion/odds_scheduler.py
"""OddsScheduler — adaptive scraping frequency based on time-to-KO.

Frequency table:
  > 6h before KO   : every 2h   (7200s)
  2h–6h before KO  : every 30m  (1800s)
  5min–2h before KO: every 2min (120s)
  < 5min before KO : stop
  after KO         : stop
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Thresholds
_STOP_BEFORE_KO = timedelta(minutes=5)
_HIGH_FREQ_THRESHOLD = timedelta(hours=2)
_MID_FREQ_THRESHOLD = timedelta(hours=6)

# Intervals (seconds)
_INTERVAL_HIGH = 120    # 2min
_INTERVAL_MID = 1800    # 30min
_INTERVAL_LOW = 7200    # 2h


def scrape_interval_seconds(kickoff_utc: datetime) -> int:
    """Return the required scrape interval in seconds for a given KO time."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    if delta <= _HIGH_FREQ_THRESHOLD:
        return _INTERVAL_HIGH
    if delta <= _MID_FREQ_THRESHOLD:
        return _INTERVAL_MID
    return _INTERVAL_LOW


def should_scrape(kickoff_utc: datetime, last_scraped_at: datetime | None) -> bool:
    """Return True if this fixture is due for a scrape."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    # Stop window: < 5min before KO, or past KO
    if delta <= _STOP_BEFORE_KO:
        return False
    # Never scraped → scrape now
    if last_scraped_at is None:
        return True
    if last_scraped_at.tzinfo is None:
        last_scraped_at = last_scraped_at.replace(tzinfo=UTC)
    interval = scrape_interval_seconds(kickoff_utc)
    return (now - last_scraped_at).total_seconds() >= interval


def _league_key(league_name: str | None) -> str | None:
    """Map fixture league name to scraper league key."""
    if not league_name:
        return None
    mapping = {
        "ligue 1": "ligue_1", "ligue_1": "ligue_1",
        "premier league": "premier_league", "premier_league": "premier_league",
        "bundesliga": "bundesliga",
        "la liga": "la_liga", "la_liga": "la_liga",
        "serie a": "serie_a", "serie_a": "serie_a",
        "champions league": "champions_league", "champions_league": "champions_league",
    }
    return mapping.get(league_name.lower())


class OddsScheduler:
    """Drives adaptive scraping of Betclic + Unibet for upcoming fixtures."""

    async def tick(self, session: AsyncSession) -> int:
        """Process all fixtures due for a scrape. Returns count of fixtures scraped."""
        from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
        from app.ingestion.odds_storage import store_match_scrape_result
        from app.ingestion.unibet_lvs_scraper import scrape_all_unibet
        from app.models.fixtures import Fixture
        from app.models.odds_scrape_state import OddsScrapeState

        now = datetime.now(UTC)
        cutoff = now + timedelta(days=10)

        # Load upcoming fixtures
        result = await session.execute(
            select(Fixture).where(
                Fixture.kickoff_utc.isnot(None),
                Fixture.kickoff_utc <= cutoff,
                Fixture.kickoff_utc > now - timedelta(minutes=5),
                Fixture.status.notin_(["finished", "cancelled", "postponed"]),
            )
        )
        fixtures = result.scalars().all()
        if not fixtures:
            return 0

        # Load scrape states
        states_result = await session.execute(
            select(OddsScrapeState).where(
                OddsScrapeState.fixture_id.in_([f.id for f in fixtures])
            )
        )
        states: dict[int, OddsScrapeState] = {
            s.fixture_id: s for s in states_result.scalars().all()
        }

        # Determine which fixtures are due
        due = [
            f for f in fixtures
            if should_scrape(
                f.kickoff_utc,
                states[f.id].last_scraped_at if f.id in states else None,
            )
        ]

        if not due:
            logger.debug("OddsScheduler.tick: 0 fixtures due")
            return 0

        logger.info("OddsScheduler.tick: %d fixtures due for scraping", len(due))

        # Collect leagues needed
        leagues_needed: set[str] = set()
        fixture_by_teams: dict[tuple[str, str], int] = {}
        for f in due:
            league = _league_key(f.league)
            if league:
                leagues_needed.add(league)
            fixture_by_teams[(f.home_team.lower(), f.away_team.lower())] = f.id

        if not leagues_needed:
            logger.warning("OddsScheduler.tick: no recognized leagues in due fixtures")
            return 0

        # Scrape both books in parallel
        betclic_results, unibet_results = await asyncio.gather(
            scrape_betclic_leagues(list(leagues_needed)),
            scrape_all_unibet(list(leagues_needed)),
            return_exceptions=True,
        )

        all_results = []
        if isinstance(betclic_results, list):
            all_results.extend(betclic_results)
        if isinstance(unibet_results, list):
            all_results.extend(unibet_results)

        # Match scraped results to fixture_ids and store
        scraped = 0
        for r in all_results:
            key = (r.home_team.lower(), r.away_team.lower())
            fixture_id = fixture_by_teams.get(key)
            if not fixture_id:
                key_rev = (r.away_team.lower(), r.home_team.lower())
                fixture_id = fixture_by_teams.get(key_rev)
            if not fixture_id:
                logger.debug(
                    "OddsScheduler: no fixture match for %s vs %s",
                    r.home_team, r.away_team,
                )
                continue
            r.fixture_id = fixture_id
            await store_match_scrape_result(r, session)
            scraped += 1

        # Update odds_scrape_state for all due fixtures
        for f in due:
            betclic_ok = any(
                r.fixture_id == f.id and r.bookmaker == "betclic"
                for r in all_results
            )
            unibet_ok = any(
                r.fixture_id == f.id and r.bookmaker == "unibet"
                for r in all_results
            )
            interval = scrape_interval_seconds(f.kickoff_utc)
            stmt = (
                pg_insert(OddsScrapeState)
                .values(
                    fixture_id=f.id,
                    last_scraped_at=now,
                    next_scrape_at=now + timedelta(seconds=interval),
                    betclic_ok=betclic_ok,
                    unibet_ok=unibet_ok,
                )
                .on_conflict_do_update(
                    index_elements=["fixture_id"],
                    set_={
                        "last_scraped_at": now,
                        "next_scrape_at": now + timedelta(seconds=interval),
                        "betclic_ok": betclic_ok,
                        "unibet_ok": unibet_ok,
                    },
                )
            )
            await session.execute(stmt)

        await session.commit()
        logger.info(
            "OddsScheduler.tick: stored %d results for %d fixtures",
            scraped, len(due),
        )
        return len(due)
