"""Background worker for scheduled data ingestion.

Runs periodic jobs:
- Fixtures sync (daily)
- Player stats update (daily)
- Odds snapshots (hourly for upcoming matches)
"""

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.ingestion.fixtures import fetch_league_fixtures
from app.ingestion.odds import ingest_odds_for_league

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Leagues to ingest
LEAGUES = ["ligue1", "premier_league"]
CURRENT_SEASON = "2024-2025"


async def job_sync_fixtures():
    """Sync fixtures for all leagues."""
    logger.info("Starting fixtures sync...")
    
    for league in LEAGUES:
        try:
            fixtures = fetch_league_fixtures(league, CURRENT_SEASON)
            logger.info(f"Fetched {len(fixtures)} fixtures for {league}")
            
            # TODO: Store in database
            # await store_fixtures(fixtures)
            
        except Exception as e:
            logger.error(f"Error syncing {league} fixtures: {e}")
    
    logger.info("Fixtures sync complete")


async def job_sync_player_stats():
    """Sync player stats for all teams."""
    logger.info("Starting player stats sync...")
    
    # TODO: Get list of teams from fixtures
    # For each team, fetch and store stats
    
    logger.info("Player stats sync complete")


async def job_snapshot_odds():
    """Snapshot odds for upcoming matches."""
    logger.info("Starting odds snapshot...")
    
    for league in LEAGUES:
        for market in ["goalscorer"]:  # "assist" often not available
            try:
                snapshots = await ingest_odds_for_league(league, market)
                logger.info(f"Got {len(snapshots)} odds for {league} {market}")
                
                # TODO: Store in database
                # await store_odds_snapshots(snapshots)
                
            except Exception as e:
                logger.error(f"Error snapshotting {league} {market} odds: {e}")
    
    logger.info("Odds snapshot complete")


async def job_generate_recommendations():
    """Generate betting recommendations for upcoming matches."""
    logger.info("Starting recommendations generation...")
    
    # TODO: For each upcoming match in next 48h:
    # 1. Load player stats
    # 2. Load latest odds
    # 3. Calculate fair prices
    # 4. Compare to market, find edges
    # 5. Store recommendations
    
    logger.info("Recommendations generation complete")


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
        name="Sync player stats from FBref",
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


async def main():
    """Main worker entry point."""
    logger.info("Starting Ev0 worker...")
    
    scheduler = create_scheduler()
    scheduler.start()
    
    logger.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")
    
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


if __name__ == "__main__":
    asyncio.run(main())
