"""Smart player stats sync with Main + Fallback strategy.

Main Strategy: Firecrawl + LLM + API-Football
- API-Football for reliable base data (names, teams, basic stats)
- Firecrawl to scrape FBref/Understat for xG/xA data
- LLM to parse scraped HTML into structured data

Fallback Strategy: Firecrawl + LLM only
- When API-Football is unavailable
- Scrape both sources and merge data
"""

import asyncio
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.ingestion.api_football import APIFootballClient, get_api_football_client
from app.ingestion.firecrawl_client import FirecrawlClient, get_firecrawl_client
from app.ingestion.llm_parser import LLMParser, get_llm_parser
from app.models.players import Player, PlayerStats, Team

# Constants
SEASON = "2025-2026"
LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]


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

    # Update external IDs if provided
    if data.get("api_football_id"):
        team.api_football_id = data["api_football_id"]
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

    if data.get("api_football_id"):
        player.api_football_id = data["api_football_id"]
    if data.get("understat_id"):
        player.understat_id = data["understat_id"]
    if data.get("position"):
        player.position = data["position"]

    await session.flush()
    return player


async def store_player_stats(
    session: AsyncSession,
    player_id: int,
    league: str,
    source: str,
    data: dict,
):
    """Store player stats for a specific source."""
    now = datetime.now(UTC)

    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source == source,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    stats_data = {
        "minutes_played": data.get("minutes", 0),
        "matches_played": data.get("games", 0),
        "goals": data.get("goals", 0),
        "xg": data.get("xg", 0.0),
        "npxg": data.get("npxg", 0.0),
        "assists": data.get("assists", 0),
        "xa": data.get("xa", 0.0),
        "shots": data.get("shots", 0),
        "key_passes": data.get("key_passes", 0),
        "xg_per_90": data.get("xg_per_90"),
        "xa_per_90": data.get("xa_per_90"),
        "npxg_per_90": data.get("npxg_per_90"),
        "as_of_utc": now,
    }

    if existing:
        for key, value in stats_data.items():
            setattr(existing, key, value)
    else:
        stats = PlayerStats(
            player_id=player_id,
            league=league,
            season=SEASON,
            source=source,
            **stats_data,
        )
        session.add(stats)

    await session.flush()


async def compute_and_store_averages(session: AsyncSession, player_id: int, league: str):
    """Compute average stats from all sources."""
    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source.in_(["api_football", "fbref", "understat"]),
    )
    result = await session.execute(stmt)
    sources = result.scalars().all()

    if not sources:
        return

    # Average per-90 stats
    avg_data = {}
    per_90_fields = ["xg_per_90", "xa_per_90", "npxg_per_90"]

    for field in per_90_fields:
        values = [getattr(s, field) or 0.0 for s in sources if getattr(s, field) is not None]
        avg_data[field] = round(sum(values) / len(values), 3) if values else 0.0

    # For counting stats, use max from sources
    avg_data["minutes"] = max((s.minutes_played or 0) for s in sources)
    avg_data["games"] = max((s.matches_played or 0) for s in sources)
    avg_data["goals"] = max((s.goals or 0) for s in sources)
    avg_data["assists"] = max((s.assists or 0) for s in sources)
    avg_data["shots"] = max((s.shots or 0) for s in sources)
    avg_data["key_passes"] = max((s.key_passes or 0) for s in sources)

    # Recalculate raw xG/xA from per-90 and minutes
    minutes = avg_data["minutes"]
    if minutes > 0:
        avg_data["xg"] = round(avg_data["xg_per_90"] * minutes / 90, 3)
        avg_data["xa"] = round(avg_data["xa_per_90"] * minutes / 90, 3)
        avg_data["npxg"] = round(avg_data["npxg_per_90"] * minutes / 90, 3)
    else:
        avg_data["xg"] = 0.0
        avg_data["xa"] = 0.0
        avg_data["npxg"] = 0.0

    await store_player_stats(session, player_id, league, "average", avg_data)


# ============ MAIN STRATEGY: Firecrawl + LLM + API-Football ============


async def sync_with_main_strategy(
    league: str,
    api_client: APIFootballClient,
    firecrawl: FirecrawlClient,
    llm: LLMParser,
) -> dict[str, Any]:
    """Sync using main strategy: API-Football + Firecrawl + LLM.

    Returns:
        Summary of sync results
    """
    print(f"\n[MAIN STRATEGY] Syncing {league}")

    results: dict[str, Any] = {
        "league": league,
        "strategy": "main",
        "api_football_players": 0,
        "fbref_players": 0,
        "understat_players": 0,
        "errors": [],
    }

    # 1. Get base data from API-Football
    print("  1/4 Fetching API-Football data...")
    try:
        api_players = await api_client.get_all_players(league)
        api_teams = await api_client.get_teams(league)
        results["api_football_players"] = len(api_players)
        print(f"      Found {len(api_players)} players, {len(api_teams)} teams")
    except Exception as e:
        error = f"API-Football error: {e}"
        print(f"      ERROR: {error}")
        results["errors"].append(error)
        api_players = []
        api_teams = []

    # 2. Scrape FBref for xG data
    print("  2/4 Scraping FBref via Firecrawl...")
    try:
        fbref_html = await firecrawl.scrape_fbref_players(league)
        fbref_players = await llm.parse_fbref_html(fbref_html, league)
        results["fbref_players"] = len(fbref_players)
        print(f"      Parsed {len(fbref_players)} players from FBref")
    except Exception as e:
        error = f"FBref scrape error: {e}"
        print(f"      ERROR: {error}")
        results["errors"].append(error)
        fbref_players = []

    # 3. Scrape Understat for xG data
    print("  3/4 Scraping Understat via Firecrawl...")
    try:
        understat_html = await firecrawl.scrape_understat_league(league)
        understat_players = await llm.parse_understat_html(understat_html, league)
        results["understat_players"] = len(understat_players)
        print(f"      Parsed {len(understat_players)} players from Understat")
    except Exception as e:
        error = f"Understat scrape error: {e}"
        print(f"      ERROR: {error}")
        results["errors"].append(error)
        understat_players = []

    # 4. Store all data
    print("  4/4 Storing to database...")
    async with async_session() as session:
        # Store teams from API-Football
        for t in api_teams:
            await upsert_team(session, league, t)

        # Create player name index for matching
        fbref_by_name = {normalize_name(p["name"]): p for p in fbref_players}
        understat_by_name = {normalize_name(p["name"]): p for p in understat_players}

        player_ids = []

        # Process API-Football players (primary source)
        for p in api_players:
            player = await upsert_player(
                session,
                league,
                {
                    "name": p["name"],
                    "team": p["team"],
                    "api_football_id": p["api_football_id"],
                    "position": p.get("position"),
                },
            )
            player_ids.append(player.id)

            # Store API-Football stats
            await store_player_stats(session, player.id, league, "api_football", p)

            # Match and store FBref stats
            norm = normalize_name(p["name"])
            if norm in fbref_by_name:
                fbref_data = fbref_by_name[norm]
                await store_player_stats(session, player.id, league, "fbref", fbref_data)

            # Match and store Understat stats
            if norm in understat_by_name:
                understat_data = understat_by_name[norm]
                await upsert_player(
                    session,
                    league,
                    {
                        "name": p["name"],
                        "understat_id": understat_data.get("understat_id"),
                    },
                )
                await store_player_stats(session, player.id, league, "understat", understat_data)

        # Add any FBref-only players
        for _norm, p in fbref_by_name.items():
            existing = normalize_name(p["name"]) in {
                normalize_name(ap["name"]) for ap in api_players
            }
            if not existing:
                player = await upsert_player(session, league, p)
                player_ids.append(player.id)
                await store_player_stats(session, player.id, league, "fbref", p)

        # Add any Understat-only players
        for norm, p in understat_by_name.items():
            existing = normalize_name(p["name"]) in {
                normalize_name(ap["name"]) for ap in api_players
            }
            if not existing and norm not in fbref_by_name:
                player = await upsert_player(
                    session,
                    league,
                    {
                        "name": p["name"],
                        "team": p["team"],
                        "understat_id": p.get("understat_id"),
                        "position": p.get("position"),
                    },
                )
                player_ids.append(player.id)
                await store_player_stats(session, player.id, league, "understat", p)

        # Compute averages for all players
        for player_id in set(player_ids):
            await compute_and_store_averages(session, player_id, league)

        await session.commit()
        print(f"      Stored {len(set(player_ids))} unique players")

    return results


# ============ FALLBACK STRATEGY: Firecrawl + LLM only ============


async def sync_with_fallback_strategy(
    league: str,
    firecrawl: FirecrawlClient,
    llm: LLMParser,
) -> dict[str, Any]:
    """Sync using fallback strategy: Firecrawl + LLM only (no API-Football).

    Returns:
        Summary of sync results
    """
    print(f"\n[FALLBACK STRATEGY] Syncing {league}")

    results: dict[str, Any] = {
        "league": league,
        "strategy": "fallback",
        "fbref_players": 0,
        "understat_players": 0,
        "errors": [],
    }

    # 1. Scrape FBref
    print("  1/3 Scraping FBref via Firecrawl...")
    try:
        fbref_html = await firecrawl.scrape_fbref_players(league)
        fbref_players = await llm.parse_fbref_html(fbref_html, league)
        results["fbref_players"] = len(fbref_players)
        print(f"      Parsed {len(fbref_players)} players from FBref")
    except Exception as e:
        error = f"FBref scrape error: {e}"
        print(f"      ERROR: {error}")
        results["errors"].append(error)
        fbref_players = []

    # 2. Scrape Understat
    print("  2/3 Scraping Understat via Firecrawl...")
    try:
        understat_html = await firecrawl.scrape_understat_league(league)
        understat_players = await llm.parse_understat_html(understat_html, league)
        results["understat_players"] = len(understat_players)
        print(f"      Parsed {len(understat_players)} players from Understat")
    except Exception as e:
        error = f"Understat scrape error: {e}"
        print(f"      ERROR: {error}")
        results["errors"].append(error)
        understat_players = []

    # 3. Store data
    print("  3/3 Storing to database...")
    async with async_session() as session:
        fbref_by_name = {normalize_name(p["name"]): p for p in fbref_players}
        understat_by_name = {normalize_name(p["name"]): p for p in understat_players}

        all_names = set(fbref_by_name.keys()) | set(understat_by_name.keys())
        player_ids = []

        for norm in all_names:
            fbref_data = fbref_by_name.get(norm)
            understat_data = understat_by_name.get(norm)

            # Use whichever source has the name
            source = fbref_data or understat_data or {}
            name = source["name"]
            team = source.get("team", "")

            player = await upsert_player(
                session,
                league,
                {
                    "name": name,
                    "team": team,
                    "understat_id": understat_data.get("understat_id") if understat_data else None,
                    "position": (understat_data or fbref_data or {}).get("position"),
                },
            )
            player_ids.append(player.id)

            if fbref_data:
                await store_player_stats(session, player.id, league, "fbref", fbref_data)

            if understat_data:
                await store_player_stats(session, player.id, league, "understat", understat_data)

            await compute_and_store_averages(session, player.id, league)

        await session.commit()
        print(f"      Stored {len(player_ids)} unique players")

    return results


# ============ SMART SYNC ORCHESTRATOR ============


async def smart_sync_league(league: str) -> dict[str, Any]:
    """Smart sync with automatic strategy selection.

    Tries Main strategy first, falls back if API-Football unavailable.
    """
    print(f"\n{'=' * 60}")
    print(f"SMART SYNC: {league.upper()}")
    print(f"Season: {SEASON}")
    print(f"{'=' * 60}")

    # Check available clients
    api_client = await get_api_football_client()
    firecrawl = await get_firecrawl_client()
    llm = await get_llm_parser()

    print("\nAvailable services:")
    print(f"  API-Football: {'✓' if api_client else '✗'}")
    print(f"  Firecrawl:    {'✓' if firecrawl else '✗'}")
    print(f"  LLM Parser:   {'✓' if llm else '✗'}")

    # Must have at least Firecrawl + LLM
    if not firecrawl or not llm:
        return {
            "league": league,
            "strategy": "none",
            "error": "Missing required services (Firecrawl + LLM)",
            "success": False,
        }

    # Try main strategy if API-Football available
    if api_client:
        print("\nUsing MAIN strategy (API-Football + Firecrawl + LLM)")
        try:
            result = await sync_with_main_strategy(league, api_client, firecrawl, llm)
            result["success"] = True
            return result
        except Exception as e:
            print(f"\nMain strategy failed: {e}")
            print("Falling back to Firecrawl + LLM only...")

    # Fallback strategy
    print("\nUsing FALLBACK strategy (Firecrawl + LLM only)")
    try:
        result = await sync_with_fallback_strategy(league, firecrawl, llm)
        result["success"] = True
        return result
    except Exception as e:
        return {
            "league": league,
            "strategy": "fallback",
            "error": str(e),
            "success": False,
        }


async def smart_sync_all() -> list[dict[str, Any]]:
    """Smart sync all leagues."""
    print("\n" + "=" * 60)
    print("EV0 - SMART SYNC")
    print("Main: Firecrawl + LLM + API-Football")
    print("Fallback: Firecrawl + LLM")
    print("=" * 60)

    results = []
    for league in LEAGUES:
        result = await smart_sync_league(league)
        results.append(result)

    print("\n" + "=" * 60)
    print("SYNC COMPLETE")
    for r in results:
        status = "✓" if r.get("success") else "✗"
        print(f"  {status} {r['league']}: {r['strategy']} strategy")
        if r.get("errors"):
            for err in r["errors"]:
                print(f"      ⚠ {err}")
    print("=" * 60)

    return results


if __name__ == "__main__":
    asyncio.run(smart_sync_all())
