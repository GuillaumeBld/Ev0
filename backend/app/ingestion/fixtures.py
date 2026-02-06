"""Fixture ingestion from FBref.

Fetches match schedules and results for Ligue 1 and Premier League.
"""

import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

# FBref URLs by league
FBREF_URLS = {
    "ligue1": "https://fbref.com/en/comps/13/schedule/Ligue-1-Scores-and-Fixtures",
    "premier_league": "https://fbref.com/en/comps/9/schedule/Premier-League-Scores-and-Fixtures",
}

# Rate limiting: FBref requires 3+ seconds between requests
_last_request_time: float = 0.0
RATE_LIMIT_SECONDS = 3.5


def normalize_team_name(name: str) -> str:
    """
    Normalize team name for consistent matching.
    
    - Lowercase
    - Remove accents
    - Remove FC/AS suffixes
    - Replace spaces with hyphens
    """
    # Remove accents
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    
    # Lowercase
    normalized = normalized.lower()
    
    # Remove common suffixes/prefixes
    normalized = re.sub(r"\b(fc|as|sc|ac)\b", "", normalized, flags=re.IGNORECASE)
    
    # Clean up whitespace and convert to kebab-case
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.replace(" ", "-")
    
    # Remove trailing/leading hyphens
    normalized = normalized.strip("-")
    
    return normalized


def generate_fixture_id(date_str: str, home_team: str, away_team: str) -> str:
    """
    Generate a stable, unique fixture ID.
    
    Format: {date}_{home}_{away} hashed to avoid special chars.
    """
    raw_id = f"{date_str}_{home_team}_{away_team}"
    return hashlib.md5(raw_id.encode()).hexdigest()[:16]


def _rate_limit() -> None:
    """Enforce rate limiting for FBref requests."""
    global _last_request_time
    
    now = time.time()
    elapsed = now - _last_request_time
    
    if elapsed < RATE_LIMIT_SECONDS:
        sleep_time = RATE_LIMIT_SECONDS - elapsed
        time.sleep(sleep_time)
    
    _last_request_time = time.time()


class FBrefFixtureParser:
    """Parser for FBref fixture tables."""
    
    def parse(self, html: str) -> list[dict[str, Any]]:
        """
        Parse FBref fixtures HTML into structured data.
        
        Returns list of fixture dicts with:
        - date, time
        - home_team, away_team
        - home_score, away_score (if played)
        """
        soup = BeautifulSoup(html, "html.parser")
        fixtures = []
        
        # Find schedule table (id starts with "sched_")
        table = soup.find("table", id=re.compile(r"^sched_"))
        if not table:
            return fixtures
        
        tbody = table.find("tbody")
        if not tbody:
            return fixtures
        
        for row in tbody.find_all("tr"):
            fixture = self._parse_row(row)
            if fixture:
                fixtures.append(fixture)
        
        return fixtures
    
    def _parse_row(self, row) -> dict[str, Any] | None:
        """Parse a single fixture row."""
        try:
            date_cell = row.find("td", {"data-stat": "date"})
            time_cell = row.find("td", {"data-stat": "time"})
            home_cell = row.find("td", {"data-stat": "home_team"})
            away_cell = row.find("td", {"data-stat": "away_team"})
            score_cell = row.find("td", {"data-stat": "score"})
            
            if not all([date_cell, home_cell, away_cell]):
                return None
            
            # Extract text
            date_str = date_cell.get_text(strip=True)
            time_str = time_cell.get_text(strip=True) if time_cell else ""
            home_team = home_cell.get_text(strip=True)
            away_team = away_cell.get_text(strip=True)
            
            # Parse score if available
            home_score = None
            away_score = None
            if score_cell:
                score_text = score_cell.get_text(strip=True)
                score_match = re.match(r"(\d+)[–-](\d+)", score_text)
                if score_match:
                    home_score = int(score_match.group(1))
                    away_score = int(score_match.group(2))
            
            return {
                "date": date_str,
                "time": time_str,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
            }
        except Exception:
            return None


def fetch_league_fixtures(league: str, season: str) -> list[dict[str, Any]]:
    """
    Fetch fixtures for a league from FBref.
    
    Args:
        league: "ligue1" or "premier_league"
        season: e.g., "2024-2025"
    
    Returns:
        List of fixture dicts
    
    Raises:
        ValueError: Unknown league
        Exception: HTTP error
    """
    if league not in FBREF_URLS:
        raise ValueError(f"Unknown league: {league}")
    
    url = FBREF_URLS[league]
    
    # Rate limit
    _rate_limit()
    
    # Fetch
    response = httpx.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Ev0-Bot/1.0; +https://github.com/GuillaumeBld/Ev0)"
        },
        timeout=30.0,
    )
    
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code} fetching {url}")
    
    # Parse
    parser = FBrefFixtureParser()
    fixtures = parser.parse(response.text)
    
    # Enrich with metadata
    for fixture in fixtures:
        fixture["league"] = league
        fixture["season"] = season
        fixture["fixture_id"] = generate_fixture_id(
            fixture["date"],
            normalize_team_name(fixture["home_team"]),
            normalize_team_name(fixture["away_team"]),
        )
    
    return fixtures


async def ingest_all_fixtures(season: str = "2024-2025") -> dict[str, int]:
    """
    Ingest fixtures for all supported leagues.
    
    Returns:
        Dict with count per league
    """
    results = {}
    
    for league in FBREF_URLS:
        fixtures = fetch_league_fixtures(league, season)
        results[league] = len(fixtures)
        # TODO: Store in database
    
    return results
