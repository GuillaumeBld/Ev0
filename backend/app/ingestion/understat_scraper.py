"""Understat scraper using the AJAX API.

Understat loads player data via POST to main/getPlayersStats/ (not embedded in HTML).
No scraping or LLM needed — clean JSON from the API.
"""

import json

import httpx

# Understat league slugs
UNDERSTAT_LEAGUES = {
    "ligue_1": "Ligue_1",
    "premier_league": "EPL",
}

# Current season (starting year)
UNDERSTAT_SEASON = "2025"

_BASE_URL = "https://understat.com/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90 stat."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 3)


def _parse_player(p: dict) -> dict:
    """Normalize a raw Understat player record into the standard schema."""
    minutes = int(p.get("time", 0) or 0)
    xg = float(p.get("xG", 0) or 0)
    xa = float(p.get("xA", 0) or 0)
    npxg = float(p.get("npxG", 0) or 0)
    return {
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
    }


async def fetch_understat_league(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch all players and teams from Understat via the AJAX API.

    Args:
        league: ligue_1 or premier_league

    Returns:
        Tuple of (players list, teams list)
    """
    slug = UNDERSTAT_LEAGUES.get(league)
    if not slug:
        raise ValueError(f"Unknown league: {league}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, base_url=_BASE_URL) as client:
        try:
            resp = await client.post(
                "main/getPlayersStats/",
                data={"league": slug, "season": UNDERSTAT_SEASON},
                headers=_HEADERS,
                cookies={"beget": "begetok"},
            )
            resp.raise_for_status()
            data = json.loads(resp.text)
        except Exception as e:
            print(f"Error fetching Understat {league}: {e}")
            return [], []

    if not data.get("success") or "players" not in data:
        print(f"  Unexpected response for {league}: {list(data.keys())}")
        return [], []

    raw_players = data["players"]
    players = [_parse_player(p) for p in raw_players]

    # Derive teams from the player list (avoids a separate API call)
    seen: set[str] = set()
    teams: list[dict] = []
    for p in players:
        t = p["team"]
        if t and t not in seen:
            seen.add(t)
            teams.append({"name": t})

    print(f"  Extracted {len(players)} players, {len(teams)} teams from Understat ({league})")
    return players, teams


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
