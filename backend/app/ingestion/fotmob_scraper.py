"""FotMob scraper using the unofficial JSON API.

FotMob exposes a JSON API that returns player season stats by stat category.
No API keys required — public read-only endpoints.
"""

import httpx


# FotMob league IDs
FOTMOB_LEAGUES = {
    "ligue_1": 53,
    "premier_league": 47,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.fotmob.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_API = "https://www.fotmob.com/api"

# Stat keys to fetch from the leagueseasondeepstats endpoint
_STAT_KEYS = [
    "goals",
    "expected_goals",
    "assists",
    "expected_assists",
    "minutes_played",
    "shots",
]


def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90 stat."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 3)


async def _get_current_season_id(client: httpx.AsyncClient, league_id: int) -> int | None:
    """Return the most recent season ID for the given league."""
    try:
        resp = await client.get(
            f"{_API}/leagues",
            params={"id": league_id},
            headers=_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        seasons = data.get("seasons", [])
        if not seasons:
            return None
        # First entry is the most recent season
        return seasons[0].get("id")
    except Exception as e:
        print(f"  Error fetching FotMob season ID for league {league_id}: {e}")
        return None


async def _fetch_stat(
    client: httpx.AsyncClient,
    league_id: int,
    season_id: int,
    stat: str,
) -> list[dict]:
    """Fetch player records for one stat category."""
    try:
        resp = await client.get(
            f"{_API}/leagueseasondeepstats",
            params={
                "id": league_id,
                "season": season_id,
                "type": "players",
                "stat": stat,
            },
            headers=_HEADERS,
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("players", []) or []
    except Exception as e:
        print(f"  Error fetching FotMob stat={stat} league={league_id}: {e}")
        return []


def _stat_value(record: dict) -> float:
    """Extract the numeric stat value from a player record."""
    for key in ("statValue", "value", "stat", "total"):
        val = record.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


def _player_name(record: dict) -> str:
    """Extract player name from a record."""
    return (
        record.get("playerName")
        or record.get("name")
        or (record.get("player") or {}).get("name", "")
        or ""
    )


def _team_name(record: dict) -> str:
    """Extract team name from a record."""
    return record.get("teamName") or (record.get("team") or {}).get("name", "") or ""


async def fetch_fotmob_league(league: str) -> tuple[list[dict], list[dict]]:
    """Fetch all players and teams from FotMob for a league.

    Args:
        league: ligue_1 or premier_league

    Returns:
        Tuple of (players list, teams list)
    """
    league_id = FOTMOB_LEAGUES.get(league)
    if not league_id:
        raise ValueError(f"Unknown league: {league}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        season_id = await _get_current_season_id(client, league_id)
        if not season_id:
            print(f"  Could not determine FotMob season ID for {league}")
            return [], []

        print(f"  FotMob season_id={season_id} for {league}")

        raw_by_stat: dict[str, list[dict]] = {}
        for stat in _STAT_KEYS:
            records = await _fetch_stat(client, league_id, season_id, stat)
            raw_by_stat[stat] = records
            print(f"    {stat}: {len(records)} records")

    # Merge all stat records by player ID
    player_map: dict[str, dict] = {}

    for stat, records in raw_by_stat.items():
        for r in records:
            pid = str(r.get("id") or r.get("playerId") or "")
            if not pid:
                continue

            if pid not in player_map:
                player_map[pid] = {
                    "fotmob_id": pid,
                    "name": _player_name(r),
                    "team": _team_name(r),
                    "position": r.get("position", ""),
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
            val = _stat_value(r)

            if stat == "goals":
                p["goals"] = int(val)
            elif stat == "expected_goals":
                p["xg"] = round(val, 3)
                p["npxg"] = round(val, 3)  # FotMob does not split npxG separately
            elif stat == "assists":
                p["assists"] = int(val)
            elif stat == "expected_assists":
                p["xa"] = round(val, 3)
            elif stat == "minutes_played":
                p["minutes"] = int(val)
                for apps_key in ("matchesPlayed", "appearances", "apps", "matches"):
                    apps = r.get(apps_key)
                    if apps is not None:
                        p["games"] = int(apps)
                        break
            elif stat == "shots":
                p["shots"] = int(val)

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

    # Derive unique teams from player list
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
