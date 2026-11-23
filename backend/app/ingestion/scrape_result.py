# backend/app/ingestion/scrape_result.py
"""Shared output types for all bookmaker scrapers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# Variante d'un pari joueur. « avec sub » = le pari reste valable sur le
# remplaçant du joueur s'il sort (Betclic « ou son remplaçant », Unibet
# « Coaching gagnant », PMU « Garantie Coaching »). Règle du 23/11/2026 : on
# prend la cote avec sub quand le book la propose ; la cote sans sub n'est
# qu'un second recours. Une cote n'est comparée qu'à la cote Ev0 de la même
# variante.
AVEC_SUB = "avec_sub"
SANS_SUB = "sans_sub"


@dataclass
class PlayerOdds:
    """One player with their decimal odds for a market."""
    player_name: str
    odds: float
    variant: str = SANS_SUB


def prefer_avec_sub(odds: list[PlayerOdds]) -> list[PlayerOdds]:
    """Une cote par joueur : avec sub si elle existe, sinon sans sub.

    À variante égale, la première rencontrée est gardée. Le nom est comparé
    sans tenir compte de la casse.
    """
    best: dict[str, PlayerOdds] = {}
    for o in odds:
        key = o.player_name.strip().lower()
        cur = best.get(key)
        if cur is None or (cur.variant != AVEC_SUB and o.variant == AVEC_SUB):
            best[key] = o
    return list(best.values())


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
