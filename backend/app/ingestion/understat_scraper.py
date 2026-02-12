"""Direct Understat scraper using worldfootballR technique.

Uses the beget=begetok cookie and proper Unicode unescaping.
No need for Firecrawl or LLM for Understat - direct HTTP works.
"""

import json
import re
from typing import Any

import httpx


# Understat league slugs
UNDERSTAT_LEAGUES = {
    "ligue_1": "Ligue_1",
    "premier_league": "EPL",
}


def _unescape_understat(raw: str) -> str:
    """Unescape Understat's encoded JSON (equivalent to stri_unescape_unicode).
    
    Handles:
    - \\xNN hex escapes
    - \\uNNNN unicode escapes
    - Escaped quotes and backslashes
    """
    # Handle \xNN hex escapes
    decoded = re.sub(
        r'\\x([0-9a-fA-F]{2})',
        lambda m: chr(int(m.group(1), 16)),
        raw
    )
    
    # Handle \uNNNN unicode escapes
    decoded = re.sub(
        r'\\u([0-9a-fA-F]{4})',
        lambda m: chr(int(m.group(1), 16)),
        decoded
    )
    
    # Handle escaped quotes and backslashes
    decoded = decoded.replace("\\'", "'")
    decoded = decoded.replace('\\"', '"')
    decoded = decoded.replace('\\\\', '\\')
    
    return decoded


def _extract_json_var(html: str, var_name: str) -> Any | None:
    """Extract JSON data from a JavaScript variable in HTML.
    
    Looks for patterns like:
    - var playersData = JSON.parse('...');
    """
    patterns = [
        rf"var\s+{var_name}\s*=\s*JSON\.parse\('(.+?)'\)\s*;",
        rf"{var_name}\s*=\s*JSON\.parse\('(.+?)'\)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                raw = match.group(1)
                decoded = _unescape_understat(raw)
                return json.loads(decoded)
            except (json.JSONDecodeError, Exception) as e:
                print(f"  Failed to parse {var_name}: {e}")
                continue
    
    return None


def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90 stat."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 3)


async def fetch_understat_league(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch all players and teams from Understat league page.
    
    Uses the beget=begetok cookie technique from worldfootballR.
    
    Args:
        league: ligue_1 or premier_league
        
    Returns:
        Tuple of (players list, teams list)
    """
    slug = UNDERSTAT_LEAGUES.get(league)
    if not slug:
        raise ValueError(f"Unknown league: {league}")
    
    url = f"https://understat.com/league/{slug}/2025"
    
    # Use the beget cookie from worldfootballR
    cookies = {"beget": "begetok"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=headers, cookies=cookies)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"Error fetching Understat {league}: {e}")
            return [], []
    
    players = []
    teams = []
    
    # Extract playersData
    players_data = _extract_json_var(html, "playersData")
    if players_data:
        for p in players_data:
            minutes = int(p.get("time", 0) or 0)
            xg = float(p.get("xG", 0) or 0)
            xa = float(p.get("xA", 0) or 0)
            npxg = float(p.get("npxG", 0) or 0)
            
            players.append({
                "understat_id": str(p.get("id", "")),
                "name": p.get("player_name", ""),
                "team": p.get("team_title", ""),
                "position": p.get("position", ""),
                "games": int(p.get("games", 0) or 0),
                "minutes": minutes,
                "goals": int(p.get("goals", 0) or 0),
                "assists": int(p.get("assists", 0) or 0),
                "xg": xg,
                "npxg": npxg,
                "xa": xa,
                "shots": int(p.get("shots", 0) or 0),
                "key_passes": int(p.get("key_passes", 0) or 0),
                "xg_per_90": calculate_per_90(xg, minutes),
                "xa_per_90": calculate_per_90(xa, minutes),
                "npxg_per_90": calculate_per_90(npxg, minutes),
            })
        print(f"  Extracted {len(players)} players from Understat")
    else:
        print(f"  No playersData found for {league}")
    
    # Extract teamsData
    teams_data = _extract_json_var(html, "teamsData")
    if teams_data:
        if isinstance(teams_data, dict):
            for team_id, info in teams_data.items():
                teams.append({
                    "understat_id": str(team_id),
                    "name": info.get("title", ""),
                })
        print(f"  Extracted {len(teams)} teams from Understat")
    
    return players, teams


async def fetch_understat_team(team_url: str) -> list[dict]:
    """Fetch player stats for a specific team.
    
    Args:
        team_url: Full Understat team URL
        
    Returns:
        List of player dicts
    """
    cookies = {"beget": "begetok"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(team_url, headers=headers, cookies=cookies)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"Error fetching team {team_url}: {e}")
            return []
    
    players_data = _extract_json_var(html, "playersData")
    if not players_data:
        return []
    
    players = []
    for p in players_data:
        minutes = int(p.get("time", 0) or 0)
        xg = float(p.get("xG", 0) or 0)
        xa = float(p.get("xA", 0) or 0)
        npxg = float(p.get("npxG", 0) or 0)
        
        players.append({
            "understat_id": str(p.get("id", "")),
            "name": p.get("player_name", ""),
            "team": p.get("team_title", ""),
            "position": p.get("position", ""),
            "games": int(p.get("games", 0) or 0),
            "minutes": minutes,
            "goals": int(p.get("goals", 0) or 0),
            "assists": int(p.get("assists", 0) or 0),
            "xg": xg,
            "npxg": npxg,
            "xa": xa,
            "shots": int(p.get("shots", 0) or 0),
            "key_passes": int(p.get("key_passes", 0) or 0),
            "xg_per_90": calculate_per_90(xg, minutes),
            "xa_per_90": calculate_per_90(xa, minutes),
            "npxg_per_90": calculate_per_90(npxg, minutes),
        })
    
    return players


# Quick test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        players, teams = await fetch_understat_league("ligue_1")
        print(f"\nLigue 1: {len(players)} players, {len(teams)} teams")
        if players:
            print(f"Sample: {players[0]}")
        
        players, teams = await fetch_understat_league("premier_league")
        print(f"\nPremier League: {len(players)} players, {len(teams)} teams")
        if players:
            print(f"Sample: {players[0]}")
    
    asyncio.run(test())
