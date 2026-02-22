"""FotMob scraper using the CDN JSON endpoints (data.fotmob.com).

FotMob serves per-stat player lists as gzip-compressed JSON files on their CDN.
These are publicly accessible without Cloudflare Turnstile restrictions.
"""

import json

import httpx

# FotMob league IDs
FOTMOB_LEAGUES = {
    "ligue_1": 53,
    "premier_league": 47,
}

# Season name to match — must match what FotMob calls the current season
FOTMOB_SEASON_NAME = "2025/2026"

# Hardcoded season IDs for 2025/2026 (TournamentId from FotMob's seasonStatLinks)
# Avoids calling www.fotmob.com (can be rate-limited/blocked); only data.fotmob.com CDN is used
FOTMOB_SEASON_IDS = {
    "ligue_1": 27212,
    "premier_league": 27110,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.fotmob.com/",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "application/json, text/plain, */*",
}

_API = "https://www.fotmob.com/api"
_CDN = "https://data.fotmob.com"

# CDN stat file names to fetch and how to map them onto our schema
_STATS = {
    "goals": "goals",
    "goal_assist": "assists",
    "expected_goals": "xg",
    "expected_assists": "xa",
    "mins_played": "minutes",
}


def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90 stat."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 3)


async def _get_season_id(client: httpx.AsyncClient, league_id: int) -> int | None:
    """Return the TournamentId for the current season from the leagues endpoint."""
    try:
        resp = await client.get(f"{_API}/leagues", params={"id": league_id}, headers=_HEADERS)
        resp.raise_for_status()
        # Use explicit UTF-8 to avoid httpx misdetecting encoding from BOM/Content-Type
        data = json.loads(resp.content.decode("utf-8", errors="replace"))
        links = data.get("stats", {}).get("seasonStatLinks", [])
        for link in links:
            if link.get("Name") == FOTMOB_SEASON_NAME:
                return link["TournamentId"]
        # Fallback: use the first (most recent) entry
        if links:
            return links[0]["TournamentId"]
    except Exception as e:
        print(f"  Error fetching FotMob season ID for league {league_id}: {e}")
    return None


async def _fetch_stat_list(
    client: httpx.AsyncClient,
    league_id: int,
    season_id: int,
    stat_name: str,
) -> list[dict]:
    """Download and parse one CDN stat file, returning the StatList."""
    url = f"{_CDN}/stats/{league_id}/season/{season_id}/{stat_name}.json"
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=20.0)
        resp.raise_for_status()
        data = json.loads(resp.content.decode("utf-8", errors="replace"))
        for top_list in data.get("TopLists", []):
            if top_list.get("StatName") == stat_name:
                return top_list.get("StatList", [])
        # If only one TopList, return it regardless of StatName
        top_lists = data.get("TopLists", [])
        if len(top_lists) == 1:
            return top_lists[0].get("StatList", [])
    except Exception as e:
        print(f"  Error fetching FotMob {stat_name} (league={league_id}): {e}")
    return []


async def fetch_fotmob_league(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch all players and teams from FotMob for a league.

    Args:
        league: ligue_1 or premier_league

    Returns:
        Tuple of (players list, teams list) using the standard schema.
    """
    league_id = FOTMOB_LEAGUES.get(league)
    if not league_id:
        raise ValueError(f"Unknown league: {league}")

    # Use hardcoded season ID first; fall back to API discovery
    season_id: int | None = FOTMOB_SEASON_IDS.get(league)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        if season_id is None:
            season_id = await _get_season_id(client, league_id)
        if not season_id:
            print(f"  Could not determine FotMob season ID for {league}")
            return [], []

        print(f"  FotMob season_id={season_id} for {league}")

        # Fetch each stat file
        raw_by_field: dict[str, list[dict]] = {}
        for stat_name in _STATS:
            records = await _fetch_stat_list(client, league_id, season_id, stat_name)
            raw_by_field[stat_name] = records
            print(f"    {stat_name}: {len(records)} records")

    # Merge by player ID
    player_map: dict[int, dict] = {}

    for stat_name, field in _STATS.items():
        for r in raw_by_field.get(stat_name, []):
            pid = r.get("ParticiantId")  # FotMob typo: "Particiant"
            if not pid:
                continue

            if pid not in player_map:
                player_map[pid] = {
                    "fotmob_id": str(pid),
                    "name": r.get("ParticipantName", ""),
                    "team": r.get("TeamName", ""),
                    "position": "",
                    "games": 0,
                    "minutes": 0,
                    "goals": 0,
                    "assists": 0,
                    "xg": 0.0,
                    "xa": 0.0,
                    "npxg": 0.0,
                    "shots": 0,
                    "key_passes": 0,
                }

            p = player_map[pid]
            val = r.get("StatValue", 0) or 0

            if field == "goals":
                p["goals"] = int(val)
            elif field == "assists":
                p["assists"] = int(val)
            elif field == "xg":
                p["xg"] = round(float(val), 3)
                p["npxg"] = round(float(val), 3)  # FotMob doesn't split npxG
            elif field == "xa":
                p["xa"] = round(float(val), 3)
            elif field == "minutes":
                p["minutes"] = int(val)
                p["games"] = int(r.get("MatchesPlayed", 0) or 0)

    # Compute per-90 stats
    players: list[dict] = []
    for p in player_map.values():
        if not p["name"]:
            continue
        minutes = p["minutes"]
        p["xg_per_90"] = calculate_per_90(p["xg"], minutes)
        p["xa_per_90"] = calculate_per_90(p["xa"], minutes)
        p["npxg_per_90"] = calculate_per_90(p["npxg"], minutes)
        players.append(p)

    # Derive unique teams
    seen: set[str] = set()
    teams: list[dict] = []
    for p in players:
        t = p["team"]
        if t and t not in seen:
            seen.add(t)
            teams.append({"name": t})

    print(f"  Extracted {len(players)} players, {len(teams)} teams from FotMob ({league})")
    return players, teams


# Quick test
if __name__ == "__main__":
    import asyncio

    async def test():
        players, teams = await fetch_fotmob_league("ligue_1")
        print(f"\nLigue 1: {len(players)} players, {len(teams)} teams")
        if players:
            print(f"Sample: {players[0]}")

        players, teams = await fetch_fotmob_league("premier_league")
        print(f"\nPremier League: {len(players)} players, {len(teams)} teams")
        if players:
            print(f"Sample: {players[0]}")

    asyncio.run(test())
