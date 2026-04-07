"""Market scrape fallback chain: OddsPortal → Betclic → Unibet.

Exported:
    ScrapeResult — immutable snapshot of all 3 markets from one source visit
    run_scrape_chain — attempt sources in order, return first success
    store_scrape_result — write rows to match_odds_snapshots
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from playwright.async_api import Browser
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    """Immutable snapshot of all 3 markets from one source visit."""

    source: str
    """'oddsportal' | 'betclic' | 'unibet'"""

    source_url: str
    parse_version: str

    h2h: dict[str, float] | None
    """{'home': float, 'draw': float, 'away': float} or None if market missing/invalid."""

    totals: dict[str, float] | None
    """{'over_2.5': float, 'under_2.5': float} or None."""

    btts: dict[str, float] | None
    """{'yes': float, 'no': float} or None."""

    ingested_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fallback_used: bool = False
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        """True if all 3 markets present — required for xG inference."""
        return self.h2h is not None and self.totals is not None and self.btts is not None


async def _scrape_oddsportal(url: str, browser: Browser) -> ScrapeResult:
    from app.ingestion.oddsportal_scraper import scrape_match_markets

    page = await browser.new_page()
    try:
        return await scrape_match_markets(url, page)
    finally:
        await page.close()


async def _scrape_betclic(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    from app.ingestion.betclic_match_scraper import scrape_match_markets

    return await scrape_match_markets(url, client)


async def _scrape_unibet(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    from app.ingestion.unibet_match_scraper import scrape_match_markets_by_event_id

    # Convention: unibet_url ends with the LVS event ID
    # e.g. https://www.unibet.fr/sport/football/.../1003754321
    try:
        event_id = int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return ScrapeResult(
            source="unibet",
            source_url=url,
            parse_version="ub-v1",
            h2h=None,
            totals=None,
            btts=None,
            fallback_used=True,
            error="invalid_event_id_in_url",
        )
    return await scrape_match_markets_by_event_id(event_id, url, client)


async def run_scrape_chain(
    poll_state: object,
    browser: Browser,
    http_client: httpx.AsyncClient,
) -> ScrapeResult | None:
    """
    Attempt OddsPortal → Betclic → Unibet in order.
    Return first ScrapeResult where is_complete is True, or None if all fail.

    poll_state must have: fixture_id, oddsportal_url, betclic_url (nullable),
    unibet_url (nullable).
    """
    # 1. OddsPortal (primary)
    result = await _scrape_oddsportal(poll_state.oddsportal_url, browser)  # type: ignore[attr-defined]
    if result.is_complete:
        logger.info("chain: fixture=%s source=oddsportal success", poll_state.fixture_id)  # type: ignore[attr-defined]
        return result
    logger.debug(
        "chain: fixture=%s oddsportal incomplete error=%s",
        poll_state.fixture_id,  # type: ignore[attr-defined]
        result.error,
    )

    # 2. Betclic fallback
    if poll_state.betclic_url:  # type: ignore[attr-defined]
        result = await _scrape_betclic(poll_state.betclic_url, http_client)  # type: ignore[attr-defined]
        if result.is_complete:
            logger.info(
                "chain: fixture=%s source=betclic fallback_used=True",
                poll_state.fixture_id,  # type: ignore[attr-defined]
            )
            return result
        logger.debug(
            "chain: fixture=%s betclic incomplete error=%s",
            poll_state.fixture_id,  # type: ignore[attr-defined]
            result.error,
        )

    # 3. Unibet fallback
    if poll_state.unibet_url:  # type: ignore[attr-defined]
        result = await _scrape_unibet(poll_state.unibet_url, http_client)  # type: ignore[attr-defined]
        if result.is_complete:
            logger.info(
                "chain: fixture=%s source=unibet fallback_used=True",
                poll_state.fixture_id,  # type: ignore[attr-defined]
            )
            return result
        logger.debug(
            "chain: fixture=%s unibet incomplete error=%s",
            poll_state.fixture_id,  # type: ignore[attr-defined]
            result.error,
        )

    logger.warning(
        "chain: fixture=%s all_sources_failed sources_tried=[oddsportal,betclic,unibet]",
        poll_state.fixture_id,  # type: ignore[attr-defined]
    )
    return None


async def store_scrape_result(
    result: ScrapeResult,
    fixture_id: int,
    session: AsyncSession,
) -> list[int]:
    """
    Write up to 7 MatchOddsSnapshot rows (3 h2h + 2 totals + 2 btts) atomically.

    Uses INSERT ... ON CONFLICT DO NOTHING to handle re-scrapes gracefully.
    Returns count of rows inserted.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.match_odds import MatchOddsSnapshot

    rows = []
    markets: dict[str, dict[str, float] | None] = {
        "h2h": result.h2h,
        "totals": result.totals,
        "btts": result.btts,
    }

    for market_type, outcomes in markets.items():
        if outcomes is None:
            continue
        for outcome_key, odds in outcomes.items():
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "bookmaker": result.source,
                    "market_type": market_type,
                    "outcome": outcome_key,
                    "odds": odds,
                    "snapshot_utc": result.ingested_at_utc,
                    "source": result.source,
                    "source_url": result.source_url,
                    "parse_version": result.parse_version,
                    "fallback_used": result.fallback_used,
                }
            )

    if not rows:
        return []

    stmt = pg_insert(MatchOddsSnapshot).values(rows).on_conflict_do_nothing(
        constraint="uq_match_odds_snapshot"
    )
    result_proxy = await session.execute(stmt)
    await session.commit()
    return list(range(result_proxy.rowcount or 0))
