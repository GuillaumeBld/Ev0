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
