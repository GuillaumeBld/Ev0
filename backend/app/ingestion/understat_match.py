"""understat_match.py — fetch per-match player data from Understat.

Provides:
  - fetch_league_match_ids(league, season) → list of MatchRef
  - fetch_match_roster(understat_id) → list of PlayerMatchRow
"""

import asyncio
from dataclasses import dataclass
from datetime import date

import httpx

from app.ingestion.understat import LEAGUE_SLUGS, _extract_json_var, get_understat_season

UNDERSTAT_BASE_URL = "https://understat.com"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
RATE_LIMIT = 2.0  # seconds between requests

# ── Team name normalization ────────────────────────────────────────
# Maps Understat team names (lowercase) → DB team names (lowercase)
UNDERSTAT_TEAM_MAP: dict[str, str] = {
    # Ligue 1
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    # Premier League
    "tottenham": "tottenham hotspur",
    "bournemouth": "afc bournemouth",
    "leeds": "leeds united",
    "brighton": "brighton & hove albion",
    "west ham": "west ham united",
    "leicester": "leicester city",
    "ipswich": "ipswich town",
    # Bundesliga
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz 05",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "fc heidenheim",
    "sv werder bremen": "werder bremen",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    "sc freiburg": "freiburg",
    # Serie A
    "ac milan": "milan",
    # La Liga
    "atletico madrid": "atlético madrid",
    "real betis balompie": "real betis",
    "deportivo alaves": "alavés",
    "rcd espanyol": "espanyol",
    "athletic club": "athletic bilbao",
    "cd leganes": "leganés",
}


def norm_understat_team(name: str) -> str:
    """Normalize a team name from Understat to match DB storage."""
    n = name.lower().strip()
    return UNDERSTAT_TEAM_MAP.get(n, n)


@dataclass
class MatchRef:
    understat_id: str
    home_team: str
    away_team: str
    match_date: date  # UTC date of kickoff


@dataclass
class PlayerMatchRow:
    player_name: str
    team_side: str       # "h" or "a"
    minutes: int
    goals: int
    assists: int


def _roster_to_events(roster: list[PlayerMatchRow]) -> list[dict]:
    """Convert Understat roster rows to MatchEvent dicts.

    Stores one event per player per event type (minute=None).
    Sufficient for settlement: we only need to know IF a player scored/assisted.
    """
    events: list[dict] = []
    for row in roster:
        if not row.player_name:
            continue
        if row.goals > 0:
            events.append({"player_name": row.player_name, "event_type": "goal", "minute": None})
        if row.assists > 0:
            events.append({"player_name": row.player_name, "event_type": "assist", "minute": None})
    return events


async def fetch_league_match_ids(
    league: str,
    season: str = "2025-2026",
) -> list[MatchRef]:
    """Fetch all finished match refs for a league+season from Understat.

    Returns only matches where isResult=True (finished).
    """
    slug = LEAGUE_SLUGS.get(league)
    if not slug:
        raise ValueError(f"Unknown league: {league}")

    season_year = get_understat_season(season)
    url = f"{UNDERSTAT_BASE_URL}/league/{slug}/{season_year}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        html = resp.text

    dates_data = _extract_json_var(html, "datesData")
    if not dates_data:
        return []

    matches: list[MatchRef] = []
    for match_id, m in dates_data.items():
        if not m.get("isResult"):
            continue
        dt_str = m.get("datetime", "")  # "2025-08-17 18:00:00"
        try:
            match_date = date.fromisoformat(dt_str[:10])
        except ValueError:
            continue
        matches.append(
            MatchRef(
                understat_id=str(match_id),
                home_team=m["h"]["title"],
                away_team=m["a"]["title"],
                match_date=match_date,
            )
        )

    return matches


async def fetch_match_roster(understat_id: str) -> list[PlayerMatchRow]:
    """Fetch per-player stats for a single finished match from Understat.

    Returns both home and away squads (players listed in rostersData).
    Minutes=0 means player did not play (unused sub or not in squad).
    """
    url = f"{UNDERSTAT_BASE_URL}/match/{understat_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        html = resp.text

    rosters_data = _extract_json_var(html, "rostersData")
    if not rosters_data:
        return []

    rows: list[PlayerMatchRow] = []
    for side in ("h", "a"):
        side_data = rosters_data.get(side, {})
        for _pid, p in side_data.items():
            rows.append(
                PlayerMatchRow(
                    player_name=p.get("player", ""),
                    team_side=side,
                    minutes=int(p.get("time", 0) or 0),
                    goals=int(p.get("goals", 0) or 0),
                    assists=int(p.get("assists", 0) or 0),
                )
            )

    return rows


async def fetch_league_match_events(
    league: str,
    season: str = "2025-2026",
) -> list[dict]:
    """Fetch match events (goals, assists) for all finished matches in a league.

    Makes one HTTP call to fetch match list, then one call per finished match.
    Rate-limited to RATE_LIMIT seconds between requests.

    Returns:
        List of dicts: {
            "understat_id": str,
            "home_team": str,   # as returned by Understat
            "away_team": str,
            "match_date": str,  # "YYYY-MM-DD"
            "events": list[dict]  # {player_name, event_type, minute}
        }
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    match_refs = await fetch_league_match_ids(league, season)
    results: list[dict] = []

    for ref in match_refs:
        try:
            roster = await fetch_match_roster(ref.understat_id)
            events = _roster_to_events(roster)
            results.append({
                "understat_id": ref.understat_id,
                "home_team": ref.home_team,
                "away_team": ref.away_team,
                "match_date": ref.match_date.isoformat(),
                "events": events,
            })
        except Exception as exc:
            _logger.warning(
                "understat_match: failed to fetch roster for match %s (%s vs %s): %s",
                ref.understat_id, ref.home_team, ref.away_team, exc,
            )
        await asyncio.sleep(RATE_LIMIT)

    return results
