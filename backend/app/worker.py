"""Background worker for scheduled data ingestion.

Runs periodic jobs via APScheduler:
- Fixtures sync (daily at 06:00 UTC)
- Player stats update (daily at 07:00 UTC)
- Odds snapshots (hourly for upcoming matches)
- Recommendation generation (every 2 hours)
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.cache import close_redis
from app.config import settings
from app.db import async_session, engine
from app.ingestion.fixtures import fetch_league_fixtures
from app.ingestion.odds import ingest_odds_for_league
from app.ingestion.storage import (
    store_odds_snapshot,
    store_recommendation,
    upsert_fixture,
)
from app.models.settings import UserSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Defaults (used when no user settings exist)
DEFAULT_LEAGUES = ["ligue1", "premier_league"]
CURRENT_SEASON = "2024-2025"


async def _load_user_settings() -> dict[str, str]:
    """Load all user settings from the database."""
    try:
        async with async_session() as session:
            result = await session.execute(select(UserSettings))
            rows = result.scalars().all()
            return {row.key: row.value for row in rows}
    except Exception as exc:
        logger.warning("Failed to load user settings, using defaults: %s", exc)
        return {}


def _get_leagues(user_settings: dict[str, str]) -> list[str]:
    """Get active leagues from user settings or defaults."""
    raw = user_settings.get("active_leagues", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            # Comma-separated fallback
            leagues = [lg.strip() for lg in raw.split(",") if lg.strip()]
            if leagues:
                return leagues
    return DEFAULT_LEAGUES


# ── Job 1: Fixtures Sync ─────────────────────────────────────────


async def job_sync_fixtures():
    """Sync fixtures from FBref for all leagues and store in DB."""
    logger.info("=== Starting fixtures sync ===")

    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)
    logger.info("Active leagues: %s", leagues)

    for league in leagues:
        try:
            # fetch_league_fixtures is synchronous (HTTP + BS4)
            fixtures = await asyncio.to_thread(fetch_league_fixtures, league, CURRENT_SEASON)
            logger.info("Fetched %d fixtures for %s", len(fixtures), league)

            stored = 0
            async with async_session() as session:
                for f in fixtures:
                    try:
                        await upsert_fixture(session, f)
                        stored += 1
                    except Exception as exc:
                        logger.warning("Failed to upsert fixture %s: %s", f.get("fixture_id"), exc)
                        await session.rollback()

            logger.info("Stored %d/%d fixtures for %s", stored, len(fixtures), league)

        except Exception as exc:
            logger.error("Error syncing %s fixtures: %s", league, exc, exc_info=True)

    logger.info("=== Fixtures sync complete ===")


# ── Job 2: Player Stats Sync ─────────────────────────────────────


async def job_sync_player_stats():
    """Sync player stats from Understat + FBref.

    Uses sync_all_players which fetches from both sources,
    stores per-source stats, and computes averages.
    """
    logger.info("=== Starting player stats sync ===")

    try:
        # Try the smart_sync first (Firecrawl+LLM+API-Football)
        # Falls back gracefully if API keys missing
        try:
            from app.ingestion.smart_sync import smart_sync_all

            results = await smart_sync_all()
            for r in results:
                status = "OK" if r.get("success") else "FAIL"
                logger.info(
                    "Smart sync %s: %s (strategy=%s)",
                    r.get("league"),
                    status,
                    r.get("strategy"),
                )
            logger.info("=== Player stats sync complete (smart) ===")
            return
        except Exception as exc:
            logger.warning("Smart sync failed, falling back to direct sync: %s", exc)

        # Fallback: direct FBref + Understat sync
        from app.ingestion.sync_all_players import sync_all

        await sync_all()
        logger.info("=== Player stats sync complete (direct) ===")

    except Exception as exc:
        logger.error("Error syncing player stats: %s", exc, exc_info=True)


# ── Job 3: Odds Snapshot ─────────────────────────────────────────


async def job_snapshot_odds():
    """Snapshot odds from The Odds API for upcoming matches and store in DB."""
    logger.info("=== Starting odds snapshot ===")

    if not settings.odds_api_key:
        logger.warning("ODDS_API_KEY not configured, skipping odds snapshot")
        return

    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)
    logger.info("Active leagues for odds: %s", leagues)

    for league in leagues:
        for market in ["goalscorer", "assist"]:
            try:
                snapshots = await ingest_odds_for_league(league, market)
                logger.info("Got %d odds for %s %s", len(snapshots), league, market)

                if not snapshots:
                    continue

                stored = 0
                async with async_session() as session:
                    from app.models.fixtures import Fixture

                    result = await session.execute(select(Fixture))
                    fixtures_by_ext = {f.external_id: f for f in result.scalars().all()}

                    for snap in snapshots:
                        # Match snapshot fixture_id (external) to DB fixture
                        fixture = fixtures_by_ext.get(snap.fixture_id)
                        if not fixture:
                            continue

                        try:
                            await store_odds_snapshot(
                                session,
                                fixture_id=fixture.id,
                                player_name=snap.player_name,
                                market_type=snap.market_type,
                                bookmaker=snap.bookmaker,
                                odds=snap.odds,
                                raw_data=snap.raw_data,
                            )
                            stored += 1
                        except Exception as exc:
                            logger.debug("Odds upsert skip (likely duplicate): %s", exc)
                            await session.rollback()

                logger.info("Stored %d/%d odds for %s %s", stored, len(snapshots), league, market)

            except Exception as exc:
                logger.error(
                    "Error snapshotting %s %s odds: %s", league, market, exc, exc_info=True
                )

    logger.info("=== Odds snapshot complete ===")


# ── Job 4: Recommendation Generation ─────────────────────────────


async def job_generate_recommendations():
    """Generate betting recommendations for upcoming matches.

    Pipeline:
    1. Load upcoming fixtures (next 48h)
    2. For each fixture, load latest player stats + best odds
    3. Run recommendation engine
    4. Store results in DB
    """
    logger.info("=== Starting recommendation generation ===")

    if not settings.odds_api_key:
        logger.warning("ODDS_API_KEY not configured, skipping recommendation generation")
        return

    try:
        from app.services.recommendation_service import get_recommendations_for_date
        from app.strategy.selector import RecommendationFilter

        user_settings = await _load_user_settings()

        # Build filter from user settings (fall back to defaults)
        filter_config = RecommendationFilter(
            min_edge=float(user_settings.get("min_edge", settings.min_edge_threshold)),
            min_confidence=float(user_settings.get("min_confidence", 0.50)),
            min_odds=float(user_settings.get("min_odds", 1.3)),
            max_odds=float(user_settings.get("max_odds", 15.0)),
            leagues=_get_leagues(user_settings),
        )
        logger.info(
            "Recommendation filter: min_edge=%.2f, min_conf=%.2f, odds=[%.1f-%.1f], leagues=%s",
            filter_config.min_edge,
            filter_config.min_confidence,
            filter_config.min_odds,
            filter_config.max_odds,
            filter_config.leagues,
        )

        # Generate for today
        now = datetime.now(UTC)

        recs, metadata = await async_session_scoped_call(
            get_recommendations_for_date, now, filter_config
        )

        logger.info(
            "Generated %d recommendations (fixtures=%s, players=%s, odds=%s)",
            len(recs),
            metadata.get("fixtures_count", 0),
            metadata.get("player_stats_count", 0),
            metadata.get("total_odds_entries", 0),
        )

        # Store recommendations in DB
        if recs:
            stored = 0
            async with async_session() as session:
                from app.models.fixtures import Fixture

                result = await session.execute(select(Fixture))
                fixtures_by_ext = {f.external_id: f for f in result.scalars().all()}

                for rec in recs:
                    fixture_ext_id = rec.get("fixture_id", "")
                    fixture = fixtures_by_ext.get(fixture_ext_id)

                    try:
                        await store_recommendation(
                            session,
                            fixture_id=fixture.id if fixture else 0,
                            player_name=rec.get("player_name", ""),
                            market_type=rec.get("market_type", ""),
                            pricing_result={
                                "lambda_intensity": rec.get("lambda_intensity", 0),
                                "probability": rec.get("fair_probability", 0),
                                "fair_odds": rec.get("fair_odds", 0),
                                "explanation": rec.get("explanation", {}),
                            },
                            best_bookmaker=rec.get("best_bookmaker", ""),
                            best_odds=rec.get("market_odds", 0),
                            edge=rec.get("edge", 0),
                        )
                        stored += 1
                    except Exception as exc:
                        logger.warning("Failed to store recommendation: %s", exc)
                        await session.rollback()

            logger.info("Stored %d/%d recommendations", stored, len(recs))

    except Exception as exc:
        logger.error("Error generating recommendations: %s", exc, exc_info=True)

    logger.info("=== Recommendation generation complete ===")


async def async_session_scoped_call(func, dt, filter_config):
    """Call get_recommendations_for_date with a fresh DB session."""
    async with async_session() as session:
        return await func(dt, session, filter_config)


# ── Scheduler Setup ───────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler()

    # Fixtures: Daily at 06:00 UTC
    scheduler.add_job(
        job_sync_fixtures,
        CronTrigger(hour=6, minute=0),
        id="sync_fixtures",
        name="Sync fixtures from FBref",
        replace_existing=True,
    )

    # Player stats: Daily at 07:00 UTC
    scheduler.add_job(
        job_sync_player_stats,
        CronTrigger(hour=7, minute=0),
        id="sync_player_stats",
        name="Sync player stats from FBref + Understat",
        replace_existing=True,
    )

    # Odds: Every hour
    scheduler.add_job(
        job_snapshot_odds,
        IntervalTrigger(hours=1),
        id="snapshot_odds",
        name="Snapshot odds from bookmakers",
        replace_existing=True,
    )

    # Recommendations: Every 2 hours
    scheduler.add_job(
        job_generate_recommendations,
        IntervalTrigger(hours=2),
        id="generate_recommendations",
        name="Generate betting recommendations",
        replace_existing=True,
    )

    return scheduler


# ── Main Entry Point ──────────────────────────────────────────────


async def main():
    """Main worker entry point."""
    logger.info("Starting Ev0 worker...")

    scheduler = create_scheduler()
    scheduler.start()

    logger.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        logger.info("  - %s: %s", job.name, job.trigger)

    # Run initial sync on startup
    logger.info("Running initial sync...")
    await job_sync_fixtures()
    await job_snapshot_odds()

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down worker...")
        scheduler.shutdown()
        await close_redis()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
