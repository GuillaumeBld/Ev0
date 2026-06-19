"""Sync player stats for finished WC2026 matches that have no stats yet.

Called by:
- POST /api/v1/wc2026/matches/sync-stats  (manual trigger via UI button)
- job_sync_wc_match_stats  (worker, every hour)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat

logger = logging.getLogger(__name__)

WC_LEAGUE_API_ID = 27


@dataclass
class WCSyncResult:
    synced: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def sync_wc_match_stats(session: AsyncSession) -> WCSyncResult:
    """Find finished WC2026 matches without player stats, clear cache, fetch detail.

    Returns counts of synced / already-ok matches and any per-match errors.
    """
    from app.api.wc2026_matches import get_match_detail  # lazy import avoids circular

    subq = (
        select(BzzPlayerMatchStat.event_api_id)
        .where(BzzPlayerMatchStat.event_api_id == BzzEvent.api_id)
        .exists()
    )
    result = await session.execute(
        select(BzzEvent.api_id)
        .where(BzzEvent.league_api_id == WC_LEAGUE_API_ID)
        .where(BzzEvent.status == "finished")
        .where(~subq)
        .order_by(BzzEvent.event_date)
    )
    to_sync = [row[0] for row in result.all()]

    if not to_sync:
        return WCSyncResult()

    logger.info("sync_wc_match_stats: %d matches to sync", len(to_sync))
    result_obj = WCSyncResult()

    for bzz_id in to_sync:
        try:
            await session.execute(
                update(BzzEvent)
                .where(BzzEvent.api_id == bzz_id)
                .values(incidents=None, shotmap=None, lineups=None)
            )
            await session.commit()
            await get_match_detail(bzz_id, session)
            result_obj.synced += 1
            logger.info("sync_wc_match_stats: synced match %d", bzz_id)
        except Exception as exc:
            logger.error("sync_wc_match_stats: match %d failed: %s", bzz_id, exc)
            result_obj.errors.append(f"{bzz_id}: {exc}")

    return result_obj
