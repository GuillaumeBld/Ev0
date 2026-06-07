"""Sync Bzzoiro multi-bookmaker odds into match_odds_snapshots.

Used to make Pinnacle (and optionally Bet365) h2h odds available to
MarketXgService, which already has "pinnacle" in _BOOKMAKER_PRIORITY.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.fixtures import Fixture
from app.models.match_odds import MatchOddsSnapshot

logger = logging.getLogger(__name__)

_OUTCOME_MAP = {"HOME": "home", "DRAW": "draw", "AWAY": "away"}


async def sync_bzzoiro_odds(
    session: AsyncSession,
    client: BzzoiroClient,
    bookmakers: list[str] | None = None,
    days_forward: int = 7,
) -> int:
    """Fetch Bzzoiro odds for upcoming fixtures and upsert into match_odds_snapshots.

    Only fixtures with a `bzz_<id>` external_id are processed.
    Only bookmakers listed in `bookmakers` are synced (default: pinnacle).

    Args:
        session: Async SQLAlchemy session.
        client: Authenticated BzzoiroClient.
        bookmakers: Bzzoiro bookmaker_code values to sync. Default: ["pinnacle"].
        days_forward: How many days ahead to look for fixtures.

    Returns:
        Total number of odds rows upserted.
    """
    if bookmakers is None:
        bookmakers = ["pinnacle"]

    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_forward)

    result = await session.execute(
        select(Fixture).where(
            Fixture.kickoff_utc >= now,
            Fixture.kickoff_utc <= cutoff,
            Fixture.external_id.like("bzz_%"),
        )
    )
    fixtures = result.scalars().all()

    count = 0
    for fixture in fixtures:
        try:
            bzz_event_id = int(fixture.external_id.removeprefix("bzz_"))
        except (ValueError, AttributeError):
            continue

        try:
            data = await client.get_page("/api/odds/", params={"event": bzz_event_id})
        except Exception as exc:
            logger.warning(
                "sync_bzzoiro_odds: failed to fetch odds for event %d (fixture %d): %s",
                bzz_event_id, fixture.id, exc,
            )
            continue

        # Remove stale Bzzoiro rows for this fixture before re-inserting fresh ones
        for bm in bookmakers:
            await session.execute(
                delete(MatchOddsSnapshot).where(
                    MatchOddsSnapshot.fixture_id == fixture.id,
                    MatchOddsSnapshot.bookmaker == bm,
                    MatchOddsSnapshot.market_type == "h2h",
                    MatchOddsSnapshot.source == "bzzoiro",
                )
            )

        for odd in data.get("odds", []):
            if odd.get("bookmaker_code") not in bookmakers:
                continue
            if odd.get("market") != "1x2":
                continue

            outcome = _OUTCOME_MAP.get(odd.get("outcome"))
            if not outcome:
                continue

            decimal_odds = odd.get("decimal_odds")
            if not decimal_odds or decimal_odds <= 1.0:
                continue

            stmt = pg_insert(MatchOddsSnapshot).values(
                fixture_id=fixture.id,
                bookmaker=odd["bookmaker_code"],
                market_type="h2h",
                outcome=outcome,
                odds=float(decimal_odds),
                snapshot_utc=now,
                source="bzzoiro",
            ).on_conflict_do_update(
                constraint="uq_match_odds_snapshot",
                set_={"odds": float(decimal_odds)},
            )
            await session.execute(stmt)
            count += 1

    await session.commit()
    logger.info(
        "sync_bzzoiro_odds: upserted %d rows (bookmakers=%s, fixtures=%d)",
        count, bookmakers, len(fixtures),
    )
    return count
