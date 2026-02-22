"""Understat player stats ingestion.

Fetches xG, xA and other advanced stats from Understat.
Understat embeds JSON data in script tags which we parse.
"""

import json
import re
import time
from typing import Any

import httpx

UNDERSTAT_BASE_URL = "https://understat.com"

# Rate limiting
_last_request_time: float = 0.0
RATE_LIMIT_SECONDS = 2.0

# League mappings
LEAGUE_SLUGS = {
    "ligue_1": "Ligue_1",
    "premier_league": "EPL",
    "la_liga": "La_liga",
    "bundesliga": "Bundesliga",
    "serie_a": "Serie_A",
}


# Season format for Understat (they use single year for start)
def get_understat_season(season: str) -> str:
    """Convert '2025-2026' to '2025' for Understat."""
    return season.split("-")[0]


def _rate_limit() -> None:
    """Enforce rate limiting."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


def _decode_understat_data(encoded: str) -> str:
    """Decode Understat's encoded JSON (they use hex escapes)."""

    # Understat uses \\xHH encoding
    def replace_hex(match):
        return chr(int(match.group(1), 16))

    decoded = re.sub(r"\\x([0-9a-fA-F]{2})", replace_hex, encoded)
    return decoded


def _extract_json_var(html: str, var_name: str) -> Any:
    """Extract JSON from JavaScript variable in HTML."""
    # Pattern: var varName = JSON.parse('...');
    pattern = rf"var\s+{var_name}\s*=\s*JSON\.parse\('(.+?)'\)"
    match = re.search(pattern, html, re.DOTALL)

    if not match:
        return None

    encoded = match.group(1)
    decoded = _decode_understat_data(encoded)

    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return None


async def fetch_league_players(league: str, season: str = "2025-2026") -> list[dict[str, Any]]:
    """
    Fetch all players stats from Understat for a league.

    Args:
        league: League key (e.g., "ligue_1", "premier_league")
        season: Season string (e.g., "2025-2026")

    Returns:
        List of player stats dicts
    """
    _rate_limit()

    league_slug = LEAGUE_SLUGS.get(league.lower())
    if not league_slug:
        raise ValueError(f"Unknown league: {league}")

    understat_season = get_understat_season(season)
    url = f"{UNDERSTAT_BASE_URL}/league/{league_slug}/{understat_season}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        response.raise_for_status()
        html = response.text

    # Extract playersData
    players_data = _extract_json_var(html, "playersData")
    if not players_data:
        return []

    players = []
    for player in players_data:
        players.append(
            {
                "understat_id": player.get("id"),
                "player_name": player.get("player_name", ""),
                "team": player.get("team_title", ""),
                "position": player.get("position", ""),
                "games": int(player.get("games", 0)),
                "minutes": int(player.get("time", 0)),
                "goals": int(player.get("goals", 0)),
                "assists": int(player.get("assists", 0)),
                "xg": float(player.get("xG", 0)),
                "xa": float(player.get("xA", 0)),
                "npxg": float(player.get("npxG", 0)),
                "npg": int(player.get("npg", 0)),  # Non-penalty goals
                "shots": int(player.get("shots", 0)),
                "key_passes": int(player.get("key_passes", 0)),
                "xg_per_90": round(
                    float(player.get("xG", 0)) / max(int(player.get("time", 1)), 1) * 90, 3
                ),
                "xa_per_90": round(
                    float(player.get("xA", 0)) / max(int(player.get("time", 1)), 1) * 90, 3
                ),
                "npxg_per_90": round(
                    float(player.get("npxG", 0)) / max(int(player.get("time", 1)), 1) * 90, 3
                ),
            }
        )

    return players


async def fetch_team_players(team_slug: str, season: str = "2025-2026") -> list[dict[str, Any]]:
    """
    Fetch players from a specific team page on Understat.

    Args:
        team_slug: Team name for URL (e.g., "Paris_Saint_Germain")
        season: Season string

    Returns:
        List of player stats
    """
    _rate_limit()

    understat_season = get_understat_season(season)
    url = f"{UNDERSTAT_BASE_URL}/team/{team_slug}/{understat_season}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        response.raise_for_status()
        html = response.text

    players_data = _extract_json_var(html, "playersData")
    if not players_data:
        return []

    players = []
    for player in players_data:
        minutes = int(player.get("time", 0))
        players.append(
            {
                "understat_id": player.get("id"),
                "player_name": player.get("player_name", ""),
                "position": player.get("position", ""),
                "games": int(player.get("games", 0)),
                "minutes": minutes,
                "goals": int(player.get("goals", 0)),
                "assists": int(player.get("assists", 0)),
                "xg": float(player.get("xG", 0)),
                "xa": float(player.get("xA", 0)),
                "npxg": float(player.get("npxG", 0)),
                "shots": int(player.get("shots", 0)),
                "key_passes": int(player.get("key_passes", 0)),
                "xg_per_90": round(float(player.get("xG", 0)) / max(minutes, 1) * 90, 3)
                if minutes > 0
                else 0,
                "xa_per_90": round(float(player.get("xA", 0)) / max(minutes, 1) * 90, 3)
                if minutes > 0
                else 0,
            }
        )

    return players


async def fetch_all_teams(league: str, season: str = "2025-2026") -> list[dict[str, Any]]:
    """
    Fetch all teams from a league on Understat.

    Returns:
        List of team dicts with id, name, slug
    """
    _rate_limit()

    league_slug = LEAGUE_SLUGS.get(league.lower())
    if not league_slug:
        raise ValueError(f"Unknown league: {league}")

    understat_season = get_understat_season(season)
    url = f"{UNDERSTAT_BASE_URL}/league/{league_slug}/{understat_season}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        response.raise_for_status()
        html = response.text

    # Extract teamsData
    teams_data = _extract_json_var(html, "teamsData")
    if not teams_data:
        return []

    teams = []
    for team_id, team_info in teams_data.items():
        teams.append(
            {
                "understat_id": team_id,
                "name": team_info.get("title", ""),
                "slug": team_info.get("title", "").replace(" ", "_"),
            }
        )

    return teams
