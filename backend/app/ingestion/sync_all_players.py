"""Complete player stats synchronization from FBref and Understat.

Fetches all players from Ligue 1 and Premier League for 2025-2026 season.
Stores stats from both sources and computes averages.
"""

import asyncio
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_maker
from app.models.players import Player, PlayerStats, Team

# Constants
SEASON = "2025-2026"
SEASON_SHORT = "2526"  # For FBref

# FBref league and team mappings
FBREF_LEAGUES = {
    "ligue_1": {
        "id": "13",
        "name": "Ligue-1",
        "teams_url": "https://fbref.com/en/comps/13/Ligue-1-Stats",
    },
    "premier_league": {
        "id": "9",
        "name": "Premier-League", 
        "teams_url": "https://fbref.com/en/comps/9/Premier-League-Stats",
    },
}

# Understat mappings
UNDERSTAT_LEAGUES = {
    "ligue_1": "Ligue_1",
    "premier_league": "EPL",
}


def normalize_name(name: str) -> str:
    """Normalize player/team name for matching."""
    # Remove accents
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    # Lowercase, remove punctuation
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


async def fetch_understat_league(client: httpx.AsyncClient, league: str) -> list[dict]:
    """Fetch all players from Understat league page."""
    league_slug = UNDERSTAT_LEAGUES.get(league)
    if not league_slug:
        return []
    
    url = f"https://understat.com/league/{league_slug}/2025"
    
    try:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"Error fetching Understat {league}: {e}")
        return []
    
    # Extract playersData JSON
    match = re.search(r"var\s+playersData\s*=\s*JSON\.parse\('(.+?)'\)", html, re.DOTALL)
    if not match:
        print(f"No playersData found for {league}")
        return []
    
    import json
    try:
        decoded = _decode_understat_data(match.group(1))
        players_data = json.loads(decoded)
    except json.JSONDecodeError as e:
        print(f"JSON decode error for {league}: {e}")
        return []
    
    players = []
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
    
    return players


async def fetch_understat_teams(client: httpx.AsyncClient, league: str) -> list[dict]:
    """Fetch all teams from Understat league page."""
    league_slug = UNDERSTAT_LEAGUES.get(league)
    if not league_slug:
        return []
    
    url = f"https://understat.com/league/{league_slug}/2025"
    
    try:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return []
    
    # Extract teamsData JSON
    match = re.search(r"var\s+teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html, re.DOTALL)
    if not match:
        return []
    
    import json
    try:
        decoded = _decode_understat_data(match.group(1))
        teams_data = json.loads(decoded)
    except:
        return []
    
    teams = []
    for team_id, info in teams_data.items():
        teams.append({
            "understat_id": team_id,
            "name": info.get("title", ""),
        })
    
    return teams


# ============ FBREF FETCHING ============

async def fetch_fbref_teams(client: httpx.AsyncClient, league: str) -> list[dict]:
    """Fetch team list from FBref."""
    league_info = FBREF_LEAGUES.get(league)
    if not league_info:
        return []
    
    try:
        resp = await client.get(league_info["teams_url"], headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"Error fetching FBref teams {league}: {e}")
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    teams = []
    
    # Find team links in the standings table
    table = soup.find("table", id=re.compile(r"results.*overall", re.IGNORECASE))
    if not table:
        table = soup.find("table", class_=re.compile("stats_table"))
    
    if table:
        for row in table.find_all("tr"):
            team_cell = row.find("td", {"data-stat": "team"})
            if team_cell:
                link = team_cell.find("a")
                if link and "href" in link.attrs:
                    name = link.get_text(strip=True)
                    href = link["href"]
                    # Extract team ID from URL
                    match = re.search(r"/squads/([^/]+)/", href)
                    if match:
                        teams.append({
                            "fbref_id": match.group(1),
                            "name": name,
                        })
    
    return teams


async def fetch_fbref_player_stats(client: httpx.AsyncClient, team_fbref_id: str, team_name: str) -> list[dict]:
    """Fetch player stats for a team from FBref."""
    url = f"https://fbref.com/en/squads/{team_fbref_id}/all_comps/{SEASON}/all_comps"
    
    try:
        await asyncio.sleep(3.5)  # Rate limiting
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"Error fetching FBref stats for {team_name}: {e}")
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    players = []
    
    # Parse shooting stats table
    shooting_table = soup.find("table", id=re.compile(r"stats.*shooting", re.IGNORECASE))
    if not shooting_table:
        shooting_table = soup.find("table", id="stats_shooting")
    
    if not shooting_table:
        return []
    
    tbody = shooting_table.find("tbody")
    if not tbody:
        return []
    
    for row in tbody.find_all("tr"):
        if "thead" in row.get("class", []):
            continue
        
        player_cell = row.find("td", {"data-stat": "player"})
        if not player_cell:
            continue
        
        name = player_cell.get_text(strip=True)
        link = player_cell.find("a")
        fbref_id = None
        if link and "href" in link.attrs:
            match = re.search(r"/players/([^/]+)/", link["href"])
            if match:
                fbref_id = match.group(1)
        
        # Extract stats
        def get_int(stat_name):
            cell = row.find("td", {"data-stat": stat_name})
            if not cell:
                return 0
            try:
                return int(cell.get_text(strip=True).replace(",", ""))
            except:
                return 0
        
        def get_float(stat_name):
            cell = row.find("td", {"data-stat": stat_name})
            if not cell:
                return 0.0
            try:
                return float(cell.get_text(strip=True).replace(",", ""))
            except:
                return 0.0
        
        minutes = get_int("minutes")
        goals = get_int("goals")
        xg = get_float("xg")
        npxg = get_float("npxg")
        shots = get_int("shots")
        shots_on_target = get_int("shots_on_target")
        
        players.append({
            "fbref_id": fbref_id,
            "name": name,
            "team": team_name,
            "minutes": minutes,
            "goals": goals,
            "xg": xg,
            "npxg": npxg,
            "shots": shots,
            "shots_on_target": shots_on_target,
            "xg_per_90": calculate_per_90(xg, minutes),
            "npxg_per_90": calculate_per_90(npxg, minutes),
        })
    
    # Parse passing stats for xA
    passing_table = soup.find("table", id=re.compile(r"stats.*passing$", re.IGNORECASE))
    if passing_table:
        tbody = passing_table.find("tbody")
        if tbody:
            passing_by_name = {}
            for row in tbody.find_all("tr"):
                player_cell = row.find("td", {"data-stat": "player"})
                if not player_cell:
                    continue
                name = player_cell.get_text(strip=True)
                
                def get_float_p(stat_name):
                    cell = row.find("td", {"data-stat": stat_name})
                    if not cell:
                        return 0.0
                    try:
                        return float(cell.get_text(strip=True).replace(",", ""))
                    except:
                        return 0.0
                
                def get_int_p(stat_name):
                    cell = row.find("td", {"data-stat": stat_name})
                    if not cell:
                        return 0
                    try:
                        return int(cell.get_text(strip=True).replace(",", ""))
                    except:
                        return 0
                
                passing_by_name[normalize_name(name)] = {
                    "assists": get_int_p("assists"),
                    "xa": get_float_p("xa"),
                    "key_passes": get_int_p("key_passes"),
                }
            
            # Merge with players
            for p in players:
                norm = normalize_name(p["name"])
                if norm in passing_by_name:
                    passing = passing_by_name[norm]
                    p["assists"] = passing.get("assists", 0)
                    p["xa"] = passing.get("xa", 0.0)
                    p["key_passes"] = passing.get("key_passes", 0)
                    p["xa_per_90"] = calculate_per_90(p["xa"], p["minutes"])
    
    return players


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
    
    # Update IDs
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
    
    # Update source IDs
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
    
    # Check for existing stats today
    stmt = select(PlayerStats).where(
        PlayerStats.player_id == player_id,
        PlayerStats.league == league,
        PlayerStats.season == SEASON,
        PlayerStats.source == source,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing
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
        # Insert new
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
    # Get stats from both sources
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
    
    # Calculate averages
    avg_data = {}
    fields = ["xg", "xa", "npxg", "xg_per_90", "xa_per_90", "npxg_per_90"]
    
    for field in fields:
        values = [getattr(s, field) or 0.0 for s in sources]
        avg_data[field] = round(sum(values) / len(values), 3) if values else 0.0
    
    # Use max for counting stats
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
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Fetch Understat data (faster, no rate limit issues)
        print(f"\nFetching Understat data for {league}...")
        understat_players = await fetch_understat_league(client, league)
        print(f"  Found {len(understat_players)} players from Understat")
        
        understat_teams = await fetch_understat_teams(client, league)
        print(f"  Found {len(understat_teams)} teams from Understat")
        
        # 2. Fetch FBref teams
        print(f"\nFetching FBref teams for {league}...")
        fbref_teams = await fetch_fbref_teams(client, league)
        print(f"  Found {len(fbref_teams)} teams from FBref")
        
        # 3. Fetch FBref player stats (with rate limiting)
        print(f"\nFetching FBref player stats (this takes a while due to rate limits)...")
        fbref_players = []
        for i, team in enumerate(fbref_teams):
            print(f"  [{i+1}/{len(fbref_teams)}] {team['name']}...", end=" ", flush=True)
            team_players = await fetch_fbref_player_stats(client, team["fbref_id"], team["name"])
            fbref_players.extend(team_players)
            print(f"{len(team_players)} players")
        
        print(f"\nTotal: {len(fbref_players)} players from FBref")
    
    # 4. Store in database
    print(f"\nStoring in database...")
    async with async_session_maker() as session:
        # Store teams
        team_count = 0
        for t in understat_teams:
            await upsert_team(session, league, {"name": t["name"], "understat_id": t["understat_id"]})
            team_count += 1
        for t in fbref_teams:
            await upsert_team(session, league, {"name": t["name"], "fbref_id": t["fbref_id"]})
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
                "fbref_id": p.get("fbref_id"),
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
    print("=" * 60)
    
    for league in ["ligue_1", "premier_league"]:
        await sync_league(league)
    
    print("\n" + "=" * 60)
    print("✅ ALL SYNCS COMPLETE!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(sync_all())
