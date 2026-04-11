"""One-shot backfill script — populates the full 2025-2026 season from Bzzoiro.

Run on the VPS inside the backend container:

    python -m app.scripts.bzzoiro_backfill

Steps (in order):
  1. reference   — sync leagues + teams
  2. players     — sync full player roster
  3. events      — sync all events for the full season (2025-08-01 → today+14)
  4. stats       — sync per-match stats for ALL players in target leagues
  5. aggregate   — aggregate per-match stats into per-season totals

Each step is idempotent (upsert). Run again at any time to refresh.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bzzoiro_backfill")


async def run(steps: list[str]) -> None:
    # Import here so env is loaded first
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.aggregate import aggregate_all_leagues
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.constants import CURRENT_SEASON
    from app.ingestion.bzzoiro.sync_events import sync_events
    from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats
    from app.ingestion.bzzoiro.sync_players import sync_players
    from app.ingestion.bzzoiro.sync_reference import sync_leagues, sync_teams

    if not settings.bzzoiro_api_key:
        logger.error("BZZOIRO_API_KEY is not configured — aborting")
        sys.exit(1)

    async with BzzoiroClient(settings.bzzoiro_api_key) as client:

        # ── Step 1: Reference data ──────────────────────────────────────────
        if "reference" in steps:
            logger.info("=" * 60)
            logger.info("STEP 1 — Syncing leagues and teams reference data")
            logger.info("=" * 60)
            t0 = time.monotonic()
            async with async_session() as session:
                n_leagues = await sync_leagues(session, client)
                n_teams = await sync_teams(session, client)
            logger.info(
                "Reference done: %d leagues, %d teams (%.1fs)",
                n_leagues, n_teams, time.monotonic() - t0,
            )

        # ── Step 2: Players roster ──────────────────────────────────────────
        if "players" in steps:
            logger.info("=" * 60)
            logger.info("STEP 2 — Syncing full player roster")
            logger.info("=" * 60)
            t0 = time.monotonic()
            async with async_session() as session:
                n_players = await sync_players(session, client)
            logger.info("Players done: %d synced (%.1fs)", n_players, time.monotonic() - t0)

        # ── Step 3: Full-season events ──────────────────────────────────────
        if "events" in steps:
            logger.info("=" * 60)
            logger.info("STEP 3 — Syncing full-season events (2025-08-01 → today+14)")
            logger.info("=" * 60)
            t0 = time.monotonic()
            async with async_session() as session:
                n_events = await sync_events(session, client, full_season=True)
            logger.info("Events done: %d synced (%.1fs)", n_events, time.monotonic() - t0)

        # ── Step 4: Per-match player stats (full season) ────────────────────
        if "stats" in steps:
            logger.info("=" * 60)
            logger.info("STEP 4 — Syncing player stats (full season, all target leagues)")
            logger.info("This step is slow — ~1 API call per player with 0.5s throttle every 10")
            logger.info("=" * 60)
            t0 = time.monotonic()
            async with async_session() as session:
                n_stats = await sync_player_stats(session, client, full_season=True)
            elapsed = time.monotonic() - t0
            logger.info(
                "Stats done: %d rows synced in %.1fs (%.0f rows/min)",
                n_stats, elapsed, n_stats / elapsed * 60 if elapsed > 0 else 0,
            )

        # ── Step 5: Aggregate season totals ────────────────────────────────
        if "aggregate" in steps:
            logger.info("=" * 60)
            logger.info("STEP 5 — Aggregating season stats from per-match data")
            logger.info("=" * 60)
            t0 = time.monotonic()
            async with async_session() as session:
                n_agg = await aggregate_all_leagues(session, season=CURRENT_SEASON)
            logger.info("Aggregation done: %d player-league rows (%.1fs)", n_agg, time.monotonic() - t0)

    logger.info("=" * 60)
    logger.info("Backfill complete.")
    logger.info("=" * 60)


def main() -> None:
    all_steps = ["reference", "players", "events", "stats", "aggregate"]

    parser = argparse.ArgumentParser(description="Bzzoiro full-season backfill")
    parser.add_argument(
        "--step",
        choices=all_steps + ["all"],
        default="all",
        help="Which step to run (default: all)",
    )
    args = parser.parse_args()

    steps = all_steps if args.step == "all" else [args.step]
    logger.info("Running steps: %s", steps)
    asyncio.run(run(steps))


if __name__ == "__main__":
    main()
