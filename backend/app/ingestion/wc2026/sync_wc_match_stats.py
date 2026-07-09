"""Sync player stats for finished WC2026 matches.

Two modes:
- sync_wc_match_stats(): standard job (every 5 min) — fetches matches with 0 stats
- resync_all_wc_match_stats(): repair job — force re-fetches ALL finished matches

After each successful sync, recomputes team advancement then player pricing,
so the decisive/top-scorer/top-assister rankings and odds follow every
finished match.

Called by:
- POST /api/v1/wc2026/matches/sync-stats  (manual trigger via UI)
- job_sync_wc_match_stats  (worker, every 5 min)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat

logger = logging.getLogger(__name__)

WC_LEAGUE_API_ID = 27


@dataclass
class WCSyncResult:
    synced: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def _fetch_and_save(bzz_id: int, session: AsyncSession) -> None:
    """Clear cache then fetch full match detail (incidents + player stats + lineups)."""
    from app.api.wc2026_matches import get_match_detail

    await session.execute(
        update(BzzEvent)
        .where(BzzEvent.api_id == bzz_id)
        .values(incidents=None, shotmap=None, lineups=None)
    )
    await session.commit()
    await get_match_detail(bzz_id, session)


async def _recompute_pricing(session: AsyncSession) -> None:
    from app.models.wc2026_advancement import WC2026TeamAdvancement
    from app.models.wc2026_pricing import WC2026PlayerPricing
    from app.pricing.wc2026_bracket import compute_wc_advancement
    from app.pricing.wc2026_tournament import compute_tournament_pricing

    # Advancement d'abord : un match terminé change les e_games (élimination /
    # qualification) et le pricing fige les λ restants via cette table.
    try:
        adv_rows = await compute_wc_advancement(session)
        if adv_rows:
            await session.execute(text("TRUNCATE TABLE wc2026_team_advancement RESTART IDENTITY"))
            for row in adv_rows:
                session.add(WC2026TeamAdvancement(**row))
            await session.commit()
            logger.info("advancement recomputed (%d nations)", len(adv_rows))
    except Exception as exc:
        logger.error("advancement recompute failed (pricing continue): %s", exc)
        await session.rollback()

    rows = await compute_tournament_pricing(session)
    await session.execute(text("TRUNCATE TABLE wc2026_player_pricing RESTART IDENTITY"))
    for row in rows:
        session.add(WC2026PlayerPricing(**row))
    await session.commit()
    logger.info("pricing recomputed (%d players)", len(rows))

    # Résultats réels des marchés outright (classements + gagnants dead-heat)
    try:
        from app.models.wc2026_pricing import WC2026MarketResult
        from app.pricing.wc2026_tournament import compute_market_results

        result_rows = await compute_market_results(session)
        await session.execute(text("TRUNCATE TABLE wc2026_market_results RESTART IDENTITY"))
        for row in result_rows:
            session.add(WC2026MarketResult(**row))
        await session.commit()
        logger.info(
            "market results recomputed (%d lignes, finalized=%s)",
            len(result_rows), bool(result_rows and result_rows[0]["finalized"]),
        )
    except Exception as exc:
        logger.error("market results recompute failed: %s", exc)
        await session.rollback()


async def sync_wc_match_stats(session: AsyncSession) -> WCSyncResult:
    """Fetch player stats for finished WC2026 matches that have none yet."""
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

    logger.info("sync_wc_match_stats: %d new matches to sync", len(to_sync))
    result_obj = WCSyncResult()

    for bzz_id in to_sync:
        try:
            await _fetch_and_save(bzz_id, session)
            result_obj.synced += 1
            logger.info("sync_wc_match_stats: synced match %d", bzz_id)
        except Exception as exc:
            logger.error("sync_wc_match_stats: match %d failed: %s", bzz_id, exc)
            result_obj.errors.append(f"{bzz_id}: {exc}")

    if result_obj.synced > 0:
        try:
            await _recompute_pricing(session)
        except Exception as exc:
            logger.error("sync_wc_match_stats: pricing recompute failed: %s", exc)

    return result_obj


async def resync_all_wc_match_stats(session: AsyncSession) -> WCSyncResult:
    """Force re-fetch ALL finished WC2026 matches (repair team_api_id + stale stats)."""
    result = await session.execute(
        select(BzzEvent.api_id)
        .where(BzzEvent.league_api_id == WC_LEAGUE_API_ID)
        .where(BzzEvent.status == "finished")
        .order_by(BzzEvent.event_date)
    )
    to_sync = [row[0] for row in result.all()]

    logger.info("resync_all_wc_match_stats: re-fetching %d matches", len(to_sync))
    result_obj = WCSyncResult()

    for bzz_id in to_sync:
        try:
            await _fetch_and_save(bzz_id, session)
            result_obj.synced += 1
            logger.info("resync_all_wc_match_stats: done match %d", bzz_id)
        except Exception as exc:
            logger.error("resync_all_wc_match_stats: match %d failed: %s", bzz_id, exc)
            result_obj.errors.append(f"{bzz_id}: {exc}")

    if result_obj.synced > 0:
        try:
            await _recompute_pricing(session)
        except Exception as exc:
            logger.error("resync_all_wc_match_stats: pricing recompute failed: %s", exc)

    return result_obj
