# backend/app/ingestion/scrape_result.py
"""Shared output types for all bookmaker scrapers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PlayerOdds:
    """One player with their decimal odds for a market."""
    player_name: str
    odds: float


@dataclass
class MatchScrapeResult:
    """Unified output from any bookmaker scraper — one object per match per book."""
    fixture_id: int
    home_team: str
    away_team: str
    kickoff_utc: datetime | None
    league: str
    bookmaker: str
    scraped_at: datetime
    # Match-level odds
    h2h: dict | None = None       # {home, draw, away}
    totals: dict | None = None    # {over_1.5, under_1.5, over_2.5, under_2.5, over_3.5, under_3.5}
    btts: dict | None = None      # {yes, no}
    # Player props
    goalscorer: list[PlayerOdds] = field(default_factory=list)
    assist: list[PlayerOdds] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True when all 3 match markets are present — required for xG solver."""
        return self.h2h is not None and self.totals is not None and self.btts is not None
