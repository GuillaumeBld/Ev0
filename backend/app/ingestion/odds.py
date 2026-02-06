"""Odds ingestion from The Odds API.

Fetches player props odds (goalscorer, assist) from multiple bookmakers.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.config import settings

# The Odds API endpoints
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Sport keys mapping
SPORT_KEYS = {
    "ligue1": "soccer_france_ligue_one",
    "premier_league": "soccer_epl",
}

# Market keys for player props
MARKET_KEYS = {
    "goalscorer": "player_anytime_goalscorer",
    "assist": "player_assist",  # May not be available on all bookmakers
}

# Supported bookmakers (French + international)
BOOKMAKERS = [
    "betclic",
    "unibet_eu",
    "winamax",
    "pmufr",
    "betfair",
    "pinnacle",
]


def normalize_selection_name(name: str) -> str:
    """
    Normalize player selection name for matching.
    
    Handles various bookmaker formats:
    - "Kylian Mbappe"
    - "K. Mbappe"
    - "MBAPPE K."
    - "Mbappé, Kylian"
    """
    # Remove accents
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    
    # Lowercase
    normalized = normalized.lower()
    
    # Remove punctuation except hyphens
    normalized = re.sub(r"[.,']", "", normalized)
    
    # Replace whitespace with single hyphen
    normalized = re.sub(r"\s+", "-", normalized.strip())
    
    return normalized


def remove_margin(odds_list: list[float], method: str = "proportional") -> list[float]:
    """
    Remove bookmaker margin from odds.
    
    Args:
        odds_list: List of decimal odds for all selections
        method: "proportional" (default) or "equal"
    
    Returns:
        Fair odds with margin removed
    """
    if not odds_list:
        return []
    
    # Calculate overround (total implied probability)
    total_prob = sum(1 / o for o in odds_list)
    
    if method == "proportional":
        # Remove margin proportionally
        fair_odds = [o * total_prob for o in odds_list]
    else:
        # Equal margin removal (less common)
        margin = total_prob - 1
        margin_per_selection = margin / len(odds_list)
        fair_odds = [
            1 / ((1 / o) - margin_per_selection)
            for o in odds_list
        ]
    
    return fair_odds


@dataclass
class OddsSnapshot:
    """A snapshot of odds for a player prop."""
    
    fixture_id: str
    player_name: str
    market_type: str  # "goalscorer" or "assist"
    bookmaker: str
    odds: float
    snapshot_utc: datetime
    raw_data: dict = field(default_factory=dict)
    
    @property
    def implied_probability(self) -> float:
        """Calculate implied probability from odds."""
        return 1 / self.odds if self.odds > 0 else 0.0


class OddsAPIClient:
    """Client for The Odds API."""
    
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.odds_api_key
        if not self.api_key:
            raise ValueError("ODDS_API_KEY not configured")
        
        self.base_url = ODDS_API_BASE
    
    def get_sport_key(self, league: str) -> str:
        """Map league name to Odds API sport key."""
        if league not in SPORT_KEYS:
            raise ValueError(f"Unknown league: {league}")
        return SPORT_KEYS[league]
    
    async def get_events(self, sport_key: str) -> list[dict[str, Any]]:
        """
        Get upcoming events for a sport.
        
        Returns list of events with id, home_team, away_team, commence_time.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/sports/{sport_key}/events",
                params={"apiKey": self.api_key},
                timeout=30.0,
            )
            
            if response.status_code != 200:
                raise Exception(f"Odds API error: {response.status_code}")
            
            return response.json()
    
    async def get_player_props(
        self,
        event_id: str,
        market: str,
        bookmakers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get player props odds for an event.
        
        Args:
            event_id: Event ID from get_events
            market: "player_anytime_goalscorer" or "player_assist"
            bookmakers: List of bookmaker keys to fetch
        
        Returns:
            List of odds dicts with player_name, bookmaker, odds
        """
        if bookmakers is None:
            bookmakers = BOOKMAKERS
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/sports/{event_id}/odds",
                params={
                    "apiKey": self.api_key,
                    "markets": market,
                    "bookmakers": ",".join(bookmakers),
                },
                timeout=30.0,
            )
            
            if response.status_code != 200:
                raise Exception(f"Odds API error: {response.status_code}")
            
            data = response.json()
            return self._parse_player_props(data)
    
    def _parse_player_props(self, data: dict) -> list[dict[str, Any]]:
        """Parse player props from API response."""
        results = []
        
        bookmakers = data.get("bookmakers", [])
        for bm in bookmakers:
            bookmaker_key = bm.get("key", "")
            
            for market in bm.get("markets", []):
                for outcome in market.get("outcomes", []):
                    results.append({
                        "player_name": outcome.get("name", ""),
                        "bookmaker": bookmaker_key,
                        "odds": outcome.get("price", 0.0),
                        "market_key": market.get("key", ""),
                    })
        
        return results


async def ingest_odds_for_league(
    league: str,
    market_type: str,
    api_key: str | None = None,
) -> list[OddsSnapshot]:
    """
    Ingest odds for all upcoming events in a league.
    
    Args:
        league: "ligue1" or "premier_league"
        market_type: "goalscorer" or "assist"
        api_key: Optional API key override
    
    Returns:
        List of OddsSnapshot objects
    """
    client = OddsAPIClient(api_key)
    sport_key = client.get_sport_key(league)
    
    # Get events
    events = await client.get_events(sport_key)
    
    # Get market key
    market_key = MARKET_KEYS.get(market_type)
    if not market_key:
        raise ValueError(f"Unknown market type: {market_type}")
    
    snapshots = []
    now = datetime.utcnow()
    
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        
        try:
            odds_data = await client.get_player_props(event_id, market_key)
            
            for od in odds_data:
                snapshot = OddsSnapshot(
                    fixture_id=event_id,
                    player_name=od["player_name"],
                    market_type=market_type,
                    bookmaker=od["bookmaker"],
                    odds=od["odds"],
                    snapshot_utc=now,
                    raw_data=od,
                )
                snapshots.append(snapshot)
        
        except Exception as e:
            # Log but continue with other events
            print(f"Error fetching odds for {event_id}: {e}")
            continue
    
    return snapshots


def find_best_odds(snapshots: list[OddsSnapshot]) -> dict[str, OddsSnapshot]:
    """
    Find best odds per player from multiple bookmakers.
    
    Returns:
        Dict mapping normalized player name to best OddsSnapshot
    """
    best = {}
    
    for snap in snapshots:
        key = normalize_selection_name(snap.player_name)
        
        if key not in best or snap.odds > best[key].odds:
            best[key] = snap
    
    return best
