# backend/app/ingestion/odds_storage.py
"""Persist MatchScrapeResult to match_odds_snapshots + player_odds_snapshots."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.scrape_result import MatchScrapeResult
from app.models.match_odds import MatchOddsSnapshot
from app.models.player_odds_snapshot import PlayerOddsSnapshot

logger = logging.getLogger(__name__)


async def store_match_scrape_result(
    result: MatchScrapeResult,
    session: AsyncSession,
) -> tuple[int, int]:
    """Write MatchScrapeResult to both snapshot tables.

    Returns (match_odds_rows_inserted, player_odds_rows_upserted).
    Uses ON CONFLICT DO NOTHING for match odds (immutable per-timestamp snapshots).
    Uses ON CONFLICT DO UPDATE for player odds (keep latest odds per player).
    """
    now = result.scraped_at

    # ── 1. match_odds_snapshots ───────────────────────────────────────────────
    match_rows: list[dict] = []
    if result.h2h:
        for outcome, odds in result.h2h.items():
            match_rows.append(_match_row(result, "h2h", outcome, odds, now))
    if result.totals:
        for outcome, odds in result.totals.items():
            match_rows.append(_match_row(result, "totals", outcome, odds, now))
    if result.btts:
        for outcome, odds in result.btts.items():
            match_rows.append(_match_row(result, "btts", outcome, odds, now))

    match_inserted = 0
    if match_rows:
        stmt = (
            pg_insert(MatchOddsSnapshot)
            .values(match_rows)
            .on_conflict_do_nothing(constraint="uq_match_odds_snapshot")
        )
        res = await session.execute(stmt)
        match_inserted = res.rowcount or 0

    # ── 2. player_odds_snapshots ──────────────────────────────────────────────
    player_rows: list[dict] = []
    for p in result.goalscorer:
        player_rows.append(_player_row(result, "goalscorer", p.player_name, p.odds, now))
    for p in result.assist:
        player_rows.append(_player_row(result, "assist", p.player_name, p.odds, now))

    player_upserted = 0
    if player_rows:
        insert_stmt = pg_insert(PlayerOddsSnapshot).values(player_rows)
        excluded = insert_stmt.excluded
        stmt2 = insert_stmt.on_conflict_do_update(
            constraint="uq_player_odds",
            set_={"odds": excluded.odds, "scraped_at": excluded.scraped_at},
        )
        res2 = await session.execute(stmt2)
        player_upserted = res2.rowcount or 0

    await session.commit()
    logger.debug(
        "odds_storage: fixture=%d %s match_rows=%d player_rows=%d",
        result.fixture_id, result.bookmaker, match_inserted, player_upserted,
    )
    return match_inserted, player_upserted


def _match_row(
    r: MatchScrapeResult,
    market: str,
    outcome: str,
    odds: float,
    now: datetime,
) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "outcome": outcome,
        "odds": odds,
        "snapshot_utc": now,
        "source": r.bookmaker,
        "source_url": None,
        "parse_version": "v2",
        "fallback_used": False,
    }


def _player_row(
    r: MatchScrapeResult,
    market: str,
    player: str,
    odds: float,
    now: datetime,
) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "player_name": player,
        "odds": odds,
        "scraped_at": now,
    }
