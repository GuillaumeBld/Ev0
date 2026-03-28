"""Complete player stats synchronization from Understat and FotMob."""

import asyncio
import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models.players import Player, PlayerStats, Team

# Constants
SEASON = "2025-2026"

from app.ingestion.fotmob_scraper import fetch_fotmob_league  # noqa: E402
from app.ingestion.understat_scraper import fetch_understat_league  # noqa: E402


def normalize_name(name: str) -> str:
    """Normalize player/team name for matching."""
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = re.sub(r"[^\w\s]", "", normalized.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90 stat."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 3)


# ============ DATABASE OPERATIONS ============


async def upsert_team(session: AsyncSession, league: str, data: dict) -> Team:
    """Insert or update a team."""
    normalized = normalize_name(data["name"])
    external_id = f"{league}:{normalized}"

    stmt = select(Team).where(Team.external_id == external_id)
    result = await session.execute(stmt)
    team = result.scalar_one_or_none()

    if not team:
        team = Team(
            external_id=external_id,
            name=data["name"],
            normalized_name=normalized,
            league=league,
            season=SEASON,
        )
        session.add(team)

    if data.get("understat_id"):
        team.understat_id = data["understat_id"]

    await session.flush()
    return team


async def upsert_player(session: AsyncSession, league: str, data: dict) -> Player:
    """Insert or update a player."""
    normalized = normalize_name(data["name"])
    external_id = f"{league}:{normalized}"

    stmt = select(Player).where(Player.external_id == external_id)
    result = await session.execute(stmt)
    player = result.scalar_one_or_none()

    if not player:
        player = Player(
            external_id=external_id,
            name=data["name"],
            normalized_name=normalized,
            team=data.get("team"),
            league=league,
        )
        session.add(player)
    else:
        player.team = data.get("team") or player.team

    if data.get("understat_id"):
        player.understat_id = data["understat_id"]
    if data.get("position"):
        player.position = data["position"]

    await session.flush()
    return player


async def store_stats(session: AsyncSession, player_id: int, league: str, source: str, data: dict):
    """Store player stats for a specific source.

    For source="average", always INSERTs a new snapshot row so that temporal
    deltas can be computed for form factor and rolling conversion rate.
    For other sources (understat, fotmob), upserts the single row.
    """
    now = datetime.now(UTC)

    # "average" snapshots accumulate over time (INSERT only)
    if source == "average":
        stats = PlayerStats(
            player_id=player_id,
            league=league,
            season=SEASON,
            source=source,
            as_of_utc=now,
            minutes_played=data.get("minutes", 0),
            matches_played=data.get("games", 0),
            goals=data.get("goals", 0),
            xg=data.get("xg", 0.0),
            npxg=data.get("npxg", 0.0),
            assists=data.get("assists", 0),
            xa=data.get("xa", 0.0),
            shots=data.get("shots", 0),
            shots_on_target=data.get("shots_on_target", 0),
            key_passes=data.get("key_passes", 0),
            xg_per_90=data.get("xg_per_90"),
            xa_per_90=data.get("xa_per_90"),
            npxg_per_90=data.get("npxg_per_90"),
        )
        session.add(stats)
        await session.flush()
        return

    # Per-source stats: upsert (one row per player/league/season/source)
    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source == source,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.minutes_played = data.get("minutes", 0)
        existing.matches_played = data.get("games", 0)
        existing.goals = data.get("goals", 0)
        existing.xg = data.get("xg", 0.0)
        existing.npxg = data.get("npxg", 0.0)
        existing.assists = data.get("assists", 0)
        existing.xa = data.get("xa", 0.0)
        existing.shots = data.get("shots", 0)
        existing.shots_on_target = data.get("shots_on_target", 0)
        existing.key_passes = data.get("key_passes", 0)
        existing.xg_per_90 = data.get("xg_per_90")
        existing.xa_per_90 = data.get("xa_per_90")
        existing.npxg_per_90 = data.get("npxg_per_90")
        existing.as_of_utc = now
    else:
        stats = PlayerStats(
            player_id=player_id,
            league=league,
            season=SEASON,
            source=source,
            as_of_utc=now,
            minutes_played=data.get("minutes", 0),
            matches_played=data.get("games", 0),
            goals=data.get("goals", 0),
            xg=data.get("xg", 0.0),
            npxg=data.get("npxg", 0.0),
            assists=data.get("assists", 0),
            xa=data.get("xa", 0.0),
            shots=data.get("shots", 0),
            shots_on_target=data.get("shots_on_target", 0),
            key_passes=data.get("key_passes", 0),
            xg_per_90=data.get("xg_per_90"),
            xa_per_90=data.get("xa_per_90"),
            npxg_per_90=data.get("npxg_per_90"),
        )
        session.add(stats)

    await session.flush()


async def compute_and_store_averages(session: AsyncSession, player_id: int, league: str):
    """Compute average stats from Understat + FotMob sources."""
    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source.in_(["understat", "fotmob"]),
    )
    result = await session.execute(stmt)
    sources = result.scalars().all()

    if not sources:
        return

    avg_data = {}
    fields = ["xg", "xa", "npxg", "xg_per_90", "xa_per_90", "npxg_per_90"]

    for field in fields:
        values = [getattr(s, field) or 0.0 for s in sources]
        avg_data[field] = round(sum(values) / len(values), 3) if values else 0.0

    avg_data["minutes"] = max(s.minutes_played for s in sources)
    avg_data["games"] = max(s.matches_played for s in sources)
    avg_data["goals"] = max(s.goals for s in sources)
    avg_data["assists"] = max(s.assists for s in sources)
    avg_data["shots"] = max(s.shots for s in sources)

    await store_stats(session, player_id, league, "average", avg_data)


# ============ MAIN SYNC ============


async def sync_league(league: str):
    """Sync all players for a league from Understat + FotMob."""
    print(f"\n{'=' * 50}")
    print(f"Syncing {league.upper()} - Season {SEASON}")
    print(f"{'=' * 50}")

    # 1. Fetch Understat data
    print(f"\nFetching Understat data for {league}...")
    understat_players, understat_teams = await fetch_understat_league(league)
    print(f"  Extracted {len(understat_players)} players, {len(understat_teams)} teams from Understat ({league})")
    print(f"  Found {len(understat_players)} players from Understat")
    print(f"  Found {len(understat_teams)} teams from Understat")

    # 2. Fetch FotMob data
    print(f"\nFetching FotMob data for {league}...")
    fotmob_players, fotmob_teams = await fetch_fotmob_league(league)
    print(f"  Extracted {len(fotmob_players)} players, {len(fotmob_teams)} teams from FotMob ({league})")
    print(f"  Found {len(fotmob_players)} players from FotMob")
    print(f"  Found {len(fotmob_teams)} teams from FotMob")

    # 3. Store in database
    print("\nStoring in database...")
    async with async_session() as session:
        # Store teams
        team_count = 0
        for t in understat_teams:
            await upsert_team(
                session, league, {"name": t["name"], "understat_id": t.get("understat_id")}
            )
            team_count += 1
        for t in fotmob_teams:
            await upsert_team(session, league, {"name": t["name"]})
            team_count += 1
        print(f"  Upserted {team_count} team records")

        # Store Understat players
        for p in understat_players:
            player = await upsert_player(
                session,
                league,
                {
                    "name": p["name"],
                    "team": p["team"],
                    "understat_id": p["understat_id"],
                    "position": p.get("position"),
                },
            )
            await store_stats(session, player.id, league, "understat", p)
        print(f"  Stored {len(understat_players)} Understat player records")

        # Store FotMob players
        for p in fotmob_players:
            player = await upsert_player(
                session,
                league,
                {
                    "name": p["name"],
                    "team": p["team"],
                    "position": p.get("position"),
                },
            )
            await store_stats(session, player.id, league, "fotmob", p)
        print(f"  Stored {len(fotmob_players)} FotMob player records")

        # Compute averages
        print("  Computing averages...")
        stmt = select(Player).where(Player.league == league)
        result = await session.execute(stmt)
        all_players = result.scalars().all()

        for player in all_players:
            await compute_and_store_averages(session, player.id, league)
        print(f"  Computed averages for {len(all_players)} players")

        await session.commit()

    print(f"\n✅ {league} sync complete!")


async def sync_all():
    """Sync all leagues."""
    print("=" * 60)
    print("EV0 - Full Player Stats Sync")
    print(f"Season: {SEASON}")
    print("Sources: Understat + FotMob")
    print("=" * 60)

    for league in ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]:
        await sync_league(league)

    print("\n" + "=" * 60)
    print("✅ ALL SYNCS COMPLETE!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(sync_all())
