"""Market scrape fallback chain: OddsPortal → Betclic → Unibet.

Exported:
    ScrapeResult — immutable snapshot of all 3 markets from one source visit
    run_scrape_chain — attempt sources in order, return first success
    store_scrape_result — write rows to match_odds_snapshots
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    """Immutable snapshot of all 3 markets from one source visit."""

    source: str
    """'oddsportal' | 'betclic' | 'unibet'"""

    source_url: str
    parse_version: str

    h2h: dict[str, float] | None
    """{'home': float, 'draw': float, 'away': float} or None if market missing/invalid."""

    totals: dict[str, float] | None
    """{'over_2.5': float, 'under_2.5': float} or None."""

    btts: dict[str, float] | None
    """{'yes': float, 'no': float} or None."""

    ingested_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fallback_used: bool = False
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        """True if all 3 markets present — required for xG inference."""
        return self.h2h is not None and self.totals is not None and self.btts is not None
