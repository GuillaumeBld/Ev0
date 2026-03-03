"""Historical backfill script.

Populates the database with past-season fixtures, match events, player stats,
and synthetic odds for backtesting the Poisson pricing model.

Usage::

    python -m app.scripts.backfill
    python -m app.scripts.backfill --league ligue_1 --step fixtures
    python -m app.scripts.backfill --step events --limit 100
    python -m app.scripts.backfill --season 2024-2025
"""

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import async_session
from app.ingestion.fotmob_scraper import (
    discover_season_id,
    fetch_fotmob_fixtures,
    fetch_fotmob_league,
    fetch_match_events,
)
from app.ingestion.storage import (
    store_match_events,
    store_player_stats,
    upsert_fixture,
    upsert_player,
)
from app.ingestion.understat_scraper import fetch_understat_league
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent

LEAGUES = ["ligue_1", "premier_league"]
# Leagues where Understat has no data — use FotMob stats instead
_FOTMOB_ONLY_LEAGUES = {"champions_league"}

# Map season string to Understat season year and FotMob season_year
# "2024-2025" → understat "2024", fotmob season_year 2024
_SEASON_START_YEAR = {
    "2023-2024": 2023,
    "2024-2025": 2024,
    "2025-2026": 2025,
}


def _season_start_year(season: str) -> int:
    """Extract the starting year from a season string like '2024-2025'."""
    if season in _SEASON_START_YEAR:
        return _SEASON_START_YEAR[season]
    return int(season.split("-")[0])


# ── Step 1: Fixtures ─────────────────────────────────────────────


async def backfill_fixtures(leagues: list[str], season: str) -> int:
    """Fetch and store fixtures for the given season."""
    total = 0
    year = _season_start_year(season)

    for league in leagues:
        print(f"\n[fixtures] {league} {season}")

        # Discover season ID for stats (not strictly needed for fixtures, but useful info)
        sid = await discover_season_id(league, season)
        if sid:
            print(f"  FotMob TournamentId: {sid}")

        fixtures = await fetch_fotmob_fixtures(league, season=season, season_year=year)
        print(f"  Got {len(fixtures)} fixtures from FotMob")

        async with async_session() as session:
            for f in fixtures:
                await upsert_fixture(session, f)
            total += len(fixtures)

    print(f"\n[fixtures] Done — {total} fixtures upserted")
    return total


# ── Step 2: Match events ─────────────────────────────────────────


async def backfill_events(leagues: list[str], season: str, limit: int | None = None) -> int:
    """Fetch match events for finished fixtures that don't have events yet."""
    total_stored = 0
    processed = 0

    async with async_session() as session:
        # Find finished fixtures for this season that have no match events
        has_events = select(MatchEvent.fixture_id).distinct().subquery()

        stmt = (
            select(Fixture)
            .where(Fixture.status == "finished")
            .where(Fixture.season == season)
            .where(~Fixture.id.in_(select(has_events.c.fixture_id)))
            .order_by(Fixture.kickoff_utc)
        )

        if leagues != LEAGUES:
            stmt = stmt.where(Fixture.league.in_(leagues))

        result = await session.execute(stmt)
        fixtures = list(result.scalars().all())

    if not fixtures:
        print("[events] No fixtures need event backfill")
        return 0

    target = min(len(fixtures), limit) if limit else len(fixtures)
    print(f"[events] {len(fixtures)} fixtures need events, processing {target}")

    for fixture in fixtures[:target]:
        # Extract FotMob match ID from external_id
        if not fixture.external_id.startswith("fotmob_"):
            continue

        match_id_str = fixture.external_id.removeprefix("fotmob_")
        try:
            match_id = int(match_id_str)
        except (ValueError, TypeError):
            continue

        events = await fetch_match_events(match_id)

        if events:
            async with async_session() as session:
                stored = await store_match_events(session, fixture.id, events)
                total_stored += stored

        processed += 1

        if (processed % 25) == 0:
            print(f"  [{processed}/{target}] {total_stored} events stored so far")

        # Rate-limit: 1 req/sec
        await asyncio.sleep(1.0)

    print(f"\n[events] Done — {total_stored} events from {processed} fixtures")
    return total_stored


# ── Step 3: Player stats ─────────────────────────────────────────


async def backfill_stats(leagues: list[str], season: str) -> int:
    """Fetch player stats from Understat (domestic leagues) or FotMob (UCL)."""
    total = 0
    year = _season_start_year(season)
    # Use season end date as the as_of_utc for stats
    season_end = datetime(year + 1, 6, 30, tzinfo=UTC)

    for league in leagues:
        if league in _FOTMOB_ONLY_LEAGUES:
            print(f"\n[stats] {league} {season} (FotMob — Understat not available)")
            players, _teams = await fetch_fotmob_league(league)
            source = "fotmob"
            id_key = "fotmob_id"
            print(f"  Got {len(players)} players from FotMob")
        else:
            print(f"\n[stats] {league} {season} (Understat season={year})")
            players, _teams = await fetch_understat_league(league, season=str(year))
            source = "understat"
            id_key = "understat_id"
            print(f"  Got {len(players)} players from Understat")

        async with async_session() as session:
            for p in players:
                if not p.get("name"):
                    continue

                # Upsert player
                raw_id = p.get(id_key, p["name"])
                player_data = {
                    "player_id": f"{source}_{raw_id}",
                    "player_name": p["name"],
                    "normalized_name": p["name"].lower(),
                    "team": p.get("team"),
                    "position": p.get("position"),
                }
                db_player = await upsert_player(session, player_data)

                # Store stats
                stats_data = {
                    "matches": p.get("games", 0),
                    "minutes": p.get("minutes", 0),
                    "goals": p.get("goals", 0),
                    "assists": p.get("assists", 0),
                    "xg": p.get("xg", 0.0),
                    "npxg": p.get("npxg", 0.0),
                    "xa": p.get("xa", 0.0),
                    "shots": p.get("shots", 0),
                    "key_passes": p.get("key_passes", 0),
                    "xg_per_90": p.get("xg_per_90"),
                    "xa_per_90": p.get("xa_per_90"),
                }

                ps = await store_player_stats(
                    session,
                    player_id=db_player.id,
                    league=league,
                    season=season,
                    stats=stats_data,
                )
                # Override as_of_utc to season end for historical data
                ps.as_of_utc = season_end
                ps.source = source
                await session.commit()

                total += 1

        # Small delay between leagues
        await asyncio.sleep(2.0)

    print(f"\n[stats] Done — {total} player stats stored")
    return total


# ── Step 4: Synthetic odds ────────────────────────────────────────


async def backfill_odds(leagues: list[str], season: str) -> int:
    """Generate synthetic odds for the backfill season."""
    from app.scripts.generate_synthetic_odds import generate_synthetic_odds_for_season

    total = 0
    for league in leagues:
        count = await generate_synthetic_odds_for_season(league, season)
        total += count

    print(f"\n[odds] Done — {total} synthetic odds generated")
    return total


# ── CLI ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical backfill for backtesting")
    parser.add_argument(
        "--league",
        choices=["ligue_1", "premier_league", "champions_league"],
        default=None,
        help="League to backfill (default: ligue_1 + premier_league)",
    )
    parser.add_argument(
        "--step",
        choices=["fixtures", "events", "stats", "odds", "all"],
        default="all",
        help="Which step to run (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max fixtures to process (events step only)",
    )
    parser.add_argument(
        "--season",
        default="2024-2025",
        help="Season to backfill (default: 2024-2025)",
    )

    args = parser.parse_args()
    leagues = [args.league] if args.league else LEAGUES
    season = args.season
    step = args.step

    print(f"Backfill: leagues={leagues}, season={season}, step={step}")

    async def run() -> None:
        if step in ("fixtures", "all"):
            await backfill_fixtures(leagues, season)

        if step in ("events", "all"):
            await backfill_events(leagues, season, limit=args.limit)

        if step in ("stats", "all"):
            await backfill_stats(leagues, season)

        if step in ("odds", "all"):
            await backfill_odds(leagues, season)

    asyncio.run(run())


if __name__ == "__main__":
    main()
