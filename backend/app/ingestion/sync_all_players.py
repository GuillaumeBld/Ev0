"""Complete player stats synchronization from FBref and Understat.

Uses soccerdata for FBref (handles rate limiting and anti-scraping).
Uses direct HTTP for Understat with proper JSON extraction.
"""

import asyncio
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

try:
    import soccerdata as sd
    HAS_SOCCERDATA = True
except ImportError:
    HAS_SOCCERDATA = False
    print("Warning: soccerdata not installed, FBref sync disabled")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models.players import Player, PlayerStats, Team

# Constants
SEASON = "2025-2026"
SEASON_SD = "2526"  # For soccerdata

# Understat mappings
UNDERSTAT_LEAGUES = {
    "ligue_1": "Ligue_1",
    "premier_league": "EPL",
}


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


# ============ UNDERSTAT FETCHING ============

def _decode_understat_data(encoded: str) -> str:
    """Decode Understat's hex-escaped JSON."""
    def replace_hex(match):
        return chr(int(match.group(1), 16))
    return re.sub(r'\\x([0-9a-fA-F]{2})', replace_hex, encoded)


async def fetch_understat_league(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch all players and teams from Understat league page."""
    league_slug = UNDERSTAT_LEAGUES.get(league)
    if not league_slug:
        return [], []
    
    url = f"https://understat.com/league/{league_slug}/2025"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            })
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"Error fetching Understat {league}: {e}")
            return [], []
    
    players = []
    teams = []
    
    # Extract playersData JSON
    players_match = re.search(r"var\s+playersData\s*=\s*JSON\.parse\('(.+?)'\)", html, re.DOTALL)
    if players_match:
        try:
            decoded = _decode_understat_data(players_match.group(1))
            players_data = json.loads(decoded)
            
            for p in players_data:
                minutes = int(p.get("time", 0))
                xg = float(p.get("xG", 0))
                xa = float(p.get("xA", 0))
                npxg = float(p.get("npxG", 0))
                
                players.append({
                    "understat_id": str(p.get("id")),
                    "name": p.get("player_name", ""),
                    "team": p.get("team_title", ""),
                    "position": p.get("position", ""),
                    "games": int(p.get("games", 0)),
                    "minutes": minutes,
                    "goals": int(p.get("goals", 0)),
                    "assists": int(p.get("assists", 0)),
                    "xg": xg,
                    "xa": xa,
                    "npxg": npxg,
                    "shots": int(p.get("shots", 0)),
                    "key_passes": int(p.get("key_passes", 0)),
                    "xg_per_90": calculate_per_90(xg, minutes),
                    "xa_per_90": calculate_per_90(xa, minutes),
                    "npxg_per_90": calculate_per_90(npxg, minutes),
                })
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error parsing Understat players for {league}: {e}")
    else:
        print(f"No playersData found in HTML for {league}")
    
    # Extract teamsData JSON
    teams_match = re.search(r"var\s+teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html, re.DOTALL)
    if teams_match:
        try:
            decoded = _decode_understat_data(teams_match.group(1))
            teams_data = json.loads(decoded)
            
            for team_id, info in teams_data.items():
                teams.append({
                    "understat_id": team_id,
                    "name": info.get("title", ""),
                })
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error parsing Understat teams for {league}: {e}")
    
    return players, teams


# ============ FBREF FETCHING VIA SOCCERDATA ============

def fetch_fbref_data(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch FBref data using soccerdata library."""
    if not HAS_SOCCERDATA:
        return [], []
    
    league_map = {
        "ligue_1": "FRA-Ligue 1",
        "premier_league": "ENG-Premier League",
    }
    
    sd_league = league_map.get(league)
    if not sd_league:
        return [], []
    
    players = []
    teams = []
    
    try:
        fbref = sd.FBref(leagues=[sd_league], seasons=SEASON_SD)
        
        # Get player stats
        try:
            shooting = fbref.read_player_season_stats(stat_type="shooting")
            passing = fbref.read_player_season_stats(stat_type="passing")
            
            # Process shooting stats
            for idx, row in shooting.iterrows():
                if isinstance(idx, tuple):
                    _, _, team, player = idx
                else:
                    player = str(idx)
                    team = ""
                
                minutes = int(row.get("Min", row.get("minutes", 0)) or 0)
                xg = float(row.get("xG", 0) or 0)
                npxg = float(row.get("npxG", 0) or 0)
                
                players.append({
                    "name": player,
                    "team": team,
                    "minutes": minutes,
                    "goals": int(row.get("Gls", row.get("goals", 0)) or 0),
                    "xg": xg,
                    "npxg": npxg,
                    "shots": int(row.get("Sh", row.get("shots", 0)) or 0),
                    "xg_per_90": calculate_per_90(xg, minutes),
                    "npxg_per_90": calculate_per_90(npxg, minutes),
                })
            
            # Merge passing stats
            passing_by_name = {}
            for idx, row in passing.iterrows():
                if isinstance(idx, tuple):
                    _, _, team, player = idx
                else:
                    player = str(idx)
                
                xa = float(row.get("xA", row.get("xAG", 0)) or 0)
                minutes = int(row.get("Min", row.get("minutes", 0)) or 0)
                
                passing_by_name[normalize_name(player)] = {
                    "assists": int(row.get("Ast", row.get("assists", 0)) or 0),
                    "xa": xa,
                    "xa_per_90": calculate_per_90(xa, minutes),
                }
            
            for p in players:
                norm = normalize_name(p["name"])
                if norm in passing_by_name:
                    p.update(passing_by_name[norm])
        
        except Exception as e:
            print(f"Error reading FBref player stats for {league}: {e}")
        
        # Get teams from schedule
        try:
            schedule = fbref.read_schedule()
            team_names = set()
            for idx, row in schedule.iterrows():
                team_names.add(row.get("home_team", ""))
                team_names.add(row.get("away_team", ""))
            
            for name in team_names:
                if name:
                    teams.append({"name": name})
        except Exception as e:
            print(f"Error reading FBref schedule for {league}: {e}")
    
    except Exception as e:
        print(f"Error initializing FBref for {league}: {e}")
    
    return players, teams


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
    
    if data.get("fbref_id"):
        team.fbref_id = data["fbref_id"]
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
    
    if data.get("fbref_id"):
        player.fbref_id = data["fbref_id"]
    if data.get("understat_id"):
        player.understat_id = data["understat_id"]
    if data.get("position"):
        player.position = data["position"]
    
    await session.flush()
    return player


async def store_stats(session: AsyncSession, player_id: int, league: str, source: str, data: dict):
    """Store player stats for a specific source."""
    now = datetime.now(timezone.utc)
    
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
            key_passes=data.get("key_passes", 0),
            xg_per_90=data.get("xg_per_90"),
            xa_per_90=data.get("xa_per_90"),
            npxg_per_90=data.get("npxg_per_90"),
        )
        session.add(stats)
    
    await session.flush()


async def compute_and_store_averages(session: AsyncSession, player_id: int, league: str):
    """Compute average stats from both sources."""
    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source.in_(["fbref", "understat"]),
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
    """Sync all players for a league from both sources."""
    print(f"\n{'='*50}")
    print(f"Syncing {league.upper()} - Season {SEASON}")
    print(f"{'='*50}")
    
    # 1. Fetch Understat data
    print(f"\nFetching Understat data for {league}...")
    understat_players, understat_teams = await fetch_understat_league(league)
    print(f"  Found {len(understat_players)} players from Understat")
    print(f"  Found {len(understat_teams)} teams from Understat")
    
    # 2. Fetch FBref data
    print(f"\nFetching FBref data for {league}...")
    fbref_players, fbref_teams = fetch_fbref_data(league)
    print(f"  Found {len(fbref_players)} players from FBref")
    print(f"  Found {len(fbref_teams)} teams from FBref")
    
    # 3. Store in database
    print(f"\nStoring in database...")
    async with async_session() as session:
        # Store teams
        team_count = 0
        for t in understat_teams:
            await upsert_team(session, league, {"name": t["name"], "understat_id": t.get("understat_id")})
            team_count += 1
        for t in fbref_teams:
            await upsert_team(session, league, {"name": t["name"], "fbref_id": t.get("fbref_id")})
            team_count += 1
        print(f"  Upserted {team_count} team records")
        
        # Store Understat players
        for p in understat_players:
            player = await upsert_player(session, league, {
                "name": p["name"],
                "team": p["team"],
                "understat_id": p["understat_id"],
                "position": p.get("position"),
            })
            await store_stats(session, player.id, league, "understat", p)
        print(f"  Stored {len(understat_players)} Understat player records")
        
        # Store FBref players
        for p in fbref_players:
            player = await upsert_player(session, league, {
                "name": p["name"],
                "team": p["team"],
            })
            await store_stats(session, player.id, league, "fbref", p)
        print(f"  Stored {len(fbref_players)} FBref player records")
        
        # Compute averages
        print(f"  Computing averages...")
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
    print(f"Sources: FBref + Understat")
    print(f"soccerdata available: {HAS_SOCCERDATA}")
    print("=" * 60)
    
    for league in ["ligue_1", "premier_league"]:
        await sync_league(league)
    
    print("\n" + "=" * 60)
    print("✅ ALL SYNCS COMPLETE!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(sync_all())
