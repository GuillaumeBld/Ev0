"""Match-level odds ingestion (h2h / totals) from The Odds API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import ODDS_API_BASE, SPORT_KEYS, OddsAPIClient, QuotaExhaustedError

logger = logging.getLogger(__name__)

# Bookmakers priority: betfair first (exchange = no overround), pinnacle fallback
MATCH_BOOKMAKERS = {"betfair", "pinnacle"}

# Markets to fetch for market-implied xG
# Note: btts/both_teams_to_score is not supported in batch soccer requests by The Odds API
MATCH_MARKET_KEYS = "h2h,totals"


@dataclass
class MatchOddsRow:
    """A single outcome row ready to insert into match_odds_snapshots."""

    event_id: str  # Odds API event ID for fixture matching
    bookmaker: str
    market_type: str  # 'h2h' | 'totals' | 'btts'
    outcome: str       # 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    odds: float
    snapshot_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_match_odds_event(
    event: dict[str, Any],
    snapshot_utc: datetime | None = None,
) -> list[MatchOddsRow]:
    """Parse a single The Odds API event dict into flat outcome rows.

    Returns:
        List of MatchOddsRow objects.
        Only includes bookmakers in MATCH_BOOKMAKERS.
        For totals, only includes the 2.5-point line, and only when both
        over_2.5 and under_2.5 are present.
    """
    ts = snapshot_utc if snapshot_utc is not None else datetime.now(UTC)
    event_id = event.get("id", "")
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    rows: list[MatchOddsRow] = []

    for bm in event.get("bookmakers", []):
        bm_key = bm.get("key", "")
        if bm_key not in MATCH_BOOKMAKERS:
            continue

        for market in bm.get("markets", []):
            mkey = market.get("key", "")

            if mkey == "h2h":
                for oc in market.get("outcomes", []):
                    name = oc.get("name", "")
                    price = oc.get("price")
                    if price is None:
                        continue
                    if name == "Draw":
                        outcome = "draw"
                    elif name == home_team:
                        outcome = "home"
                    elif name == away_team:
                        outcome = "away"
                    else:
                        logger.debug("Unknown h2h outcome: %s", name)
                        continue
                    rows.append(MatchOddsRow(
                        event_id=event_id,
                        bookmaker=bm_key,
                        market_type="h2h",
                        outcome=outcome,
                        odds=float(price),
                        snapshot_utc=ts,
                    ))

            elif mkey == "totals":
                # Collect both sides first; only emit if both are present.
                totals_collected: dict[str, float] = {}
                for oc in market.get("outcomes", []):
                    point = oc.get("point")
                    if point != 2.5:
                        continue
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None:
                        continue
                    key = "over_2.5" if name == "over" else "under_2.5"
                    totals_collected[key] = float(price)

                if "over_2.5" in totals_collected and "under_2.5" in totals_collected:
                    for outcome_key, odds_val in totals_collected.items():
                        rows.append(MatchOddsRow(
                            event_id=event_id,
                            bookmaker=bm_key,
                            market_type="totals",
                            outcome=outcome_key,
                            odds=odds_val,
                            snapshot_utc=ts,
                        ))

            elif mkey == "both_teams_to_score":
                for oc in market.get("outcomes", []):
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None or name not in ("yes", "no"):
                        continue
                    rows.append(MatchOddsRow(
                        event_id=event_id,
                        bookmaker=bm_key,
                        market_type="btts",
                        outcome=name,
                        odds=float(price),
                        snapshot_utc=ts,
                    ))

    return rows


async def ingest_match_odds_for_league(
    league: str,
    session: AsyncSession,
    api_key: str | None = None,
) -> tuple[list[MatchOddsRow], list[dict]]:
    """Fetch and parse match-level odds for a league using a single batch call.

    Uses the /sports/{sport}/odds batch endpoint (1 API credit per league)
    rather than the per-event endpoint to avoid quota exhaustion.

    Args:
        league: League identifier (e.g. 'ligue_1').
        session: SQLAlchemy async session (unused — kept for API compatibility).
        api_key: Optional Odds API key override.

    Returns:
        (rows, errors) — rows ready for DB insert, list of error messages.
    """
    client = OddsAPIClient(api_key)
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        logger.warning("ingest_match_odds_for_league: unknown league %s", league)
        return [], []

    client._check_quota()

    snapshot_utc = datetime.now(UTC)
    async with httpx.AsyncClient() as http:
        response = await http.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey": client.api_key,
                "markets": MATCH_MARKET_KEYS,
                "regions": "eu,uk",
                "oddsFormat": "decimal",
            },
            timeout=30.0,
        )

    client._update_quota(response)

    if response.status_code != 200:
        msg = f"Match odds batch fetch failed for {league}: HTTP {response.status_code} — {response.text[:200]}"
        logger.warning(msg)
        return [], [{"league": league, "error": msg}]

    events = response.json()
    if not isinstance(events, list):
        logger.warning("Unexpected response format for %s match odds: %s", league, events)
        return [], []

    all_rows: list[MatchOddsRow] = []
    errors: list[dict] = []

    for event in events:
        try:
            parsed = parse_match_odds_event(event, snapshot_utc=snapshot_utc)
            all_rows.extend(parsed)
        except Exception as exc:
            errors.append({"event_id": event.get("id"), "error": str(exc)})

    return all_rows, errors
