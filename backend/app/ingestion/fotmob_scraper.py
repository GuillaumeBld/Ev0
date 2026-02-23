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
    "total_scoring_att": "shots",
    "ontarget_scoring_att": "shots_on_target",
    "big_chance_created": "key_passes",
}


# FotMob position ID → canonical position mapping
# 11 = GK, 30s = DF, 50s-60s-70s = MF, 80s+ = FW/wing, 100s+ = ST
_POSITION_MAP: dict[int, str] = {}
_POSITION_MAP[11] = "GK"
for _pid in range(30, 40):
    _POSITION_MAP[_pid] = "DF"
for _pid in range(50, 80):
    _POSITION_MAP[_pid] = "MF"
for _pid in range(80, 120):
    _POSITION_MAP[_pid] = "FW"

def _fotmob_position(position_ids: list[int]) -> str:
    """Map FotMob position IDs to canonical FW/MF/DF/GK."""
    if not position_ids:
        return ""
    # Use the first (primary) position
    primary = position_ids[0]
    return _POSITION_MAP.get(primary, "")


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
                    "shots_on_target": 0,
                    "key_passes": 0,
                }

            p = player_map[pid]
            val = r.get("StatValue", 0) or 0
            minutes_r = int(r.get("MinutesPlayed", 0) or 0)

            # Extract position from any stat entry that has it
            if not p["position"]:
                pos_ids = r.get("Positions", [])
                if pos_ids:
                    p["position"] = _fotmob_position(pos_ids)

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
            elif field == "shots":
                # StatValue is per-90 rate; convert to total
                total = round(float(val) * minutes_r / 90) if minutes_r > 0 else 0
                p["shots"] = int(total)
            elif field == "shots_on_target":
                total = round(float(val) * minutes_r / 90) if minutes_r > 0 else 0
                p["shots_on_target"] = int(total)
            elif field == "key_passes":
                # big_chance_created: StatValue is total count
                p["key_passes"] = int(val)

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


async def fetch_fotmob_fixtures(league: str, season: str = "2025-2026") -> list[dict]:
    """Fetch all fixtures (matches) for a league from FotMob API.

    Uses the ``/api/leagues`` endpoint which returns all season matches
    including scheduled, live, and finished. Requires ``gzip`` encoding
    (not brotli) to avoid Turnstile issues.

    Args:
        league: ``ligue_1`` or ``premier_league``
        season: Season string for metadata (e.g. ``"2025-2026"``)

    Returns:
        List of fixture dicts compatible with ``upsert_fixture()``:
        ``fixture_id``, ``league``, ``season``, ``date``, ``time``,
        ``home_team``, ``away_team``, ``home_score``, ``away_score``,
        ``matchweek``.
    """
    league_id = FOTMOB_LEAGUES.get(league)
    if not league_id:
        raise ValueError(f"Unknown league: {league}")

    # Use gzip only — brotli responses from www.fotmob.com need extra
    # decompression that httpx doesn't handle natively.
    headers = {**_HEADERS, "Accept-Encoding": "gzip, deflate"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(
                f"{_API}/leagues",
                params={"id": league_id},
                headers=headers,
                timeout=20.0,
            )
            resp.raise_for_status()
            data = json.loads(resp.content.decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  Error fetching FotMob fixtures for {league}: {e}")
            return []

    all_matches = data.get("fixtures", {}).get("allMatches", [])
    if not all_matches:
        print(f"  No matches found in FotMob response for {league}")
        return []

    fixtures: list[dict] = []
    for m in all_matches:
        status = m.get("status", {})
        utc_time = status.get("utcTime", "")

        # Parse UTC time → date + time
        if not utc_time:
            continue
        # utcTime format: "2025-08-15T19:00:00Z" or "2025-08-15T19:00:00.000Z"
        date_str = utc_time[:10]  # YYYY-MM-DD
        time_str = utc_time[11:16]  # HH:MM

        home = m.get("home", {})
        away = m.get("away", {})

        # Parse score from scoreStr (e.g. "4 - 2")
        home_score = None
        away_score = None
        score_str = status.get("scoreStr", "")
        if score_str and status.get("finished"):
            parts = score_str.split(" - ")
            if len(parts) == 2:
                try:
                    home_score = int(parts[0].strip())
                    away_score = int(parts[1].strip())
                except ValueError:
                    pass

        fixture_id = f"fotmob_{m.get('id', '')}"

        fixtures.append(
            {
                "fixture_id": fixture_id,
                "league": league,
                "season": season,
                "date": date_str,
                "time": time_str,
                "home_team": home.get("name", ""),
                "away_team": away.get("name", ""),
                "home_score": home_score,
                "away_score": away_score,
                "matchweek": int(m.get("round", 0) or 0) or None,
            }
        )

    scheduled = sum(1 for f in fixtures if f["home_score"] is None)
    finished = sum(1 for f in fixtures if f["home_score"] is not None)
    print(f"  FotMob fixtures for {league}: {len(fixtures)} total ({finished} finished, {scheduled} upcoming)")
    return fixtures


async def fetch_match_events(match_id: int) -> list[dict]:
    """Fetch individual goal/assist events for a finished match from FotMob.

    Uses the ``/api/matchDetails`` endpoint which returns full match data
    including an event timeline with goal scorers and assisters.

    Args:
        match_id: FotMob numeric match ID (from ``fotmob_<id>`` external_id)

    Returns:
        List of event dicts: ``{"player_name", "event_type", "minute"}``.
        ``event_type`` is one of: ``"goal"``, ``"assist"``, ``"own_goal"``,
        ``"penalty_goal"``.
    """
    headers = {**_HEADERS, "Accept-Encoding": "gzip, deflate"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(
                f"{_API}/matchDetails",
                params={"matchId": match_id},
                headers=headers,
                timeout=20.0,
            )
            resp.raise_for_status()
            data = json.loads(resp.content.decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  Error fetching FotMob match events for match {match_id}: {e}")
            return []

    return _parse_match_events(data)


def _parse_match_events(data: dict) -> list[dict]:
    """Parse goal/assist events from FotMob matchDetails response.

    FotMob matchDetails can contain events in multiple locations:
    - ``content.matchFacts.events.events`` — detailed event list
    - ``header.events`` — simplified list (fallback)
    """
    events: list[dict] = []

    # Try detailed event list first
    match_events = (
        data.get("content", {})
        .get("matchFacts", {})
        .get("events", {})
        .get("events", [])
    )

    if match_events:
        for ev in match_events:
            event_type_raw = ev.get("type", "")
            player_name = ev.get("nameStr") or ev.get("name", "")
            minute = ev.get("time")

            if not player_name:
                continue

            # Parse minute (can be "45+2" format or just int)
            parsed_minute = _parse_minute(minute)

            if event_type_raw == "Goal":
                is_own_goal = ev.get("isOwnGoal", False) or ev.get("ownGoal", False)
                is_penalty = ev.get("isPenalty", False) or ev.get("penaltyGoal", False)

                if is_own_goal:
                    events.append({
                        "player_name": player_name,
                        "event_type": "own_goal",
                        "minute": parsed_minute,
                    })
                else:
                    events.append({
                        "player_name": player_name,
                        "event_type": "penalty_goal" if is_penalty else "goal",
                        "minute": parsed_minute,
                    })

                    # Check for assist
                    assist_name = ev.get("assistStr") or ev.get("assist", "")
                    if assist_name:
                        events.append({
                            "player_name": assist_name,
                            "event_type": "assist",
                            "minute": parsed_minute,
                        })

        return events

    # Fallback: header events
    header_events = data.get("header", {}).get("events", [])
    if not header_events:
        # Try another common location
        header_events = data.get("header", {}).get("teams", [])
        all_scorers: list[dict] = []
        for team in header_events:
            for scorer in team.get("scorers", []):
                name = scorer.get("name", "")
                if name:
                    all_scorers.append({
                        "player_name": name,
                        "event_type": "goal",
                        "minute": _parse_minute(scorer.get("minute")),
                    })
        return all_scorers

    for ev in header_events:
        name = ev.get("name") or ev.get("nameStr", "")
        if not name:
            continue
        events.append({
            "player_name": name,
            "event_type": "goal",
            "minute": _parse_minute(ev.get("time") or ev.get("minute")),
        })

    return events


def _parse_minute(raw: object) -> int | None:
    """Parse minute from FotMob event (handles '45+2', int, str, None)."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    raw_str = str(raw).strip()
    if not raw_str:
        return None
    # Handle "45+2" → 45
    if "+" in raw_str:
        raw_str = raw_str.split("+")[0]
    try:
        return int(raw_str)
    except (ValueError, TypeError):
        return None


async def fetch_events_for_finished_fixtures(
    fixtures: list[dict],
) -> dict[str, list[dict]]:
    """Fetch match events for a list of finished fixtures.

    Args:
        fixtures: List of fixture dicts with ``fixture_id`` (e.g. ``"fotmob_12345"``).

    Returns:
        Dict mapping ``fixture_id`` → list of event dicts.
    """
    import asyncio

    results: dict[str, list[dict]] = {}

    for f in fixtures:
        fixture_id = f.get("fixture_id", "")
        if not fixture_id.startswith("fotmob_"):
            continue

        match_id_str = fixture_id.removeprefix("fotmob_")
        try:
            match_id = int(match_id_str)
        except (ValueError, TypeError):
            continue

        events = await fetch_match_events(match_id)
        if events:
            results[fixture_id] = events

        # Rate-limit: 1 req/sec
        await asyncio.sleep(1.0)

    return results


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

        print("\n--- Fixtures ---")
        for league in ["premier_league", "ligue_1"]:
            fixtures = await fetch_fotmob_fixtures(league)
            upcoming = [f for f in fixtures if f["home_score"] is None]
            if upcoming:
                print(f"\nNext 3 {league} fixtures:")
                for f in upcoming[:3]:
                    print(f"  {f['date']} {f['time']} | {f['home_team']} vs {f['away_team']} (MW {f['matchweek']})")

    asyncio.run(test())
