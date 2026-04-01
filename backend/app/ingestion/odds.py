"""Odds ingestion from The Odds API.

Fetches player props odds (goalscorer, assist) from multiple bookmakers.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# The Odds API endpoints
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Sport keys mapping
SPORT_KEYS = {
    "ligue_1":          "soccer_france_ligue_one",
    "premier_league":   "soccer_epl",
    "bundesliga":       "soccer_germany_bundesliga",
    "la_liga":          "soccer_spain_la_liga",
    "serie_a":          "soccer_italy_serie_a",
    "champions_league": "soccer_uefa_champs_league",
}

# Legacy league key aliases (e.g. user settings may still have "ligue1")
_LEAGUE_ALIASES = {"ligue1": "ligue_1", "ligue-1": "ligue_1"}


def normalize_league_key(key: str) -> str:
    """Normalize a league key, resolving legacy aliases."""
    return _LEAGUE_ALIASES.get(key, key)

# Market keys for player props (The Odds API v4 naming)
MARKET_KEYS = {
    "goalscorer": "player_goal_scorer_anytime",
    "assist": "player_assist",  # May not be available on all bookmakers
}

# Regions to fetch odds from
REGIONS = ["eu", "uk", "us"]

# Supported bookmakers (French + international)
BOOKMAKERS = [
    "betclic",
    "betclic_fr",
    "unibet_eu",
    "unibet_fr",
    "winamax",
    "pmufr",
    "parionssport",
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
        fair_odds = [1 / ((1 / o) - margin_per_selection) for o in odds_list]

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


_QUOTA_REDIS_KEY = "odds_api:requests_remaining"
_QUOTA_LOW_THRESHOLD = 5  # stop calling when fewer than this remain


class QuotaExhaustedError(Exception):
    """Raised when The Odds API monthly quota is too low to continue."""


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

    def _update_quota(self, response: httpx.Response) -> None:
        """Persist remaining request count from response headers to Redis."""
        remaining = response.headers.get("x-requests-remaining")
        if remaining is None:
            return
        try:
            import redis as _redis
            r = _redis.from_url(settings.redis_url, decode_responses=True)
            r.set(_QUOTA_REDIS_KEY, remaining, ex=86400 * 32)  # 32-day TTL
            remaining_int = int(remaining)
            if remaining_int <= _QUOTA_LOW_THRESHOLD:
                logger.warning(
                    "Odds API quota low: %d requests remaining — pausing calls",
                    remaining_int,
                )
        except Exception:
            pass

    def _check_quota(self) -> None:
        """Raise QuotaExhaustedError if saved quota is below threshold."""
        try:
            import redis as _redis
            r = _redis.from_url(settings.redis_url, decode_responses=True)
            val = r.get(_QUOTA_REDIS_KEY)
            if val is not None and int(val) <= _QUOTA_LOW_THRESHOLD:
                raise QuotaExhaustedError(
                    f"Odds API quota too low ({val} remaining) — skipping"
                )
        except QuotaExhaustedError:
            raise
        except Exception:
            pass  # Redis unavailable → allow the call

    async def get_events(self, sport_key: str) -> list[dict[str, Any]]:
        """
        Get upcoming events for a sport.

        Returns list of events with id, home_team, away_team, commence_time.
        Uses Redis cache to avoid hammering the API.
        """
        from app.cache import cache_odds_events, get_cached_odds_events

        cached = await get_cached_odds_events(sport_key)
        if cached is not None:
            logger.debug("Cache HIT for events %s (%d events)", sport_key, len(cached))
            return cached

        self._check_quota()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/sports/{sport_key}/events",
                params={"apiKey": self.api_key},
                timeout=30.0,
            )

            self._update_quota(response)

            if response.status_code != 200:
                raise Exception(f"Odds API error: {response.status_code}")

            events = response.json()
            await cache_odds_events(sport_key, events)
            return events

    async def get_player_props(
        self,
        sport_key: str,
        event_id: str,
        market: str,
        regions: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get player props odds for an event.

        Args:
            sport_key: Sport key (e.g. "soccer_france_ligue_one")
            event_id: Event ID from get_events
            market: Market key (e.g. "player_goal_scorer_anytime")
            regions: List of region keys to fetch (default: eu, uk, us)

        Returns:
            List of odds dicts with player_name, bookmaker, odds.
            Uses Redis cache to avoid hammering the API.
        """
        from app.cache import cache_player_props, get_cached_player_props

        cached = await get_cached_player_props(sport_key, event_id, market)
        if cached is not None:
            logger.debug("Cache HIT for props %s/%s/%s", sport_key, event_id, market)
            return cached

        self._check_quota()

        if regions is None:
            regions = REGIONS

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/sports/{sport_key}/events/{event_id}/odds",
                params={
                    "apiKey": self.api_key,
                    "markets": market,
                    "regions": ",".join(regions),
                },
                timeout=30.0,
            )

            self._update_quota(response)

            if response.status_code != 200:
                raise Exception(f"Odds API error: {response.status_code}")

            data = response.json()
            props = self._parse_player_props(data)
            await cache_player_props(sport_key, event_id, market, props)
            return props

    def _parse_player_props(self, data: dict) -> list[dict[str, Any]]:
        """Parse player props from API response.

        The Odds API v4 player props format:
        - outcome.name = "Yes" (always)
        - outcome.description = "Player Name"
        - outcome.price = decimal odds
        """
        results = []

        bookmakers = data.get("bookmakers", [])
        for bm in bookmakers:
            bookmaker_key = bm.get("key", "")
            if bookmaker_key not in BOOKMAKERS:
                continue

            for market in bm.get("markets", []):
                for outcome in market.get("outcomes", []):
                    # Player name is in 'description', not 'name'
                    player_name = outcome.get("description") or outcome.get("name", "")
                    if not player_name or player_name == "Yes":
                        continue
                    results.append(
                        {
                            "player_name": player_name,
                            "bookmaker": bookmaker_key,
                            "odds": outcome.get("price", 0.0),
                            "market_key": market.get("key", ""),
                        }
                    )

        return results


async def ingest_odds_for_league(
    league: str,
    market_type: str,
    api_key: str | None = None,
) -> tuple[list[OddsSnapshot], list[dict]]:
    """
    Ingest odds for all upcoming events in a league.

    Args:
        league: "ligue_1" or "premier_league"
        market_type: "goalscorer" or "assist"
        api_key: Optional API key override

    Returns:
        Tuple of (list of OddsSnapshot objects, list of raw event dicts)
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
    now = datetime.now(UTC)

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        try:
            odds_data = await client.get_player_props(sport_key, event_id, market_key)

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
            logger.warning("Error fetching odds for %s: %s", event_id, e)
            continue

    return snapshots, events


def find_best_odds(snapshots: list[OddsSnapshot]) -> dict[str, OddsSnapshot]:
    """
    Find best odds per player from multiple bookmakers.

    Returns:
        Dict mapping normalized player name to best OddsSnapshot
    """
    best: dict[str, OddsSnapshot] = {}

    for snap in snapshots:
        key = normalize_selection_name(snap.player_name)

        if key not in best or snap.odds > best[key].odds:
            best[key] = snap

    return best
