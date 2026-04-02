"""Match-level odds ingestion (h2h / totals / btts) from The Odds API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ingestion.odds import ODDS_API_BASE, SPORT_KEYS, OddsAPIClient

logger = logging.getLogger(__name__)

# Bookmakers priority: betfair first (exchange = no overround), pinnacle fallback
MATCH_BOOKMAKERS = {"betfair", "pinnacle"}

# Markets to fetch for market-implied xG
MATCH_MARKET_KEYS = "h2h,totals,both_teams_to_score"


@dataclass
class MatchOddsRow:
    """A single outcome row ready to insert into match_odds_snapshots."""

    event_id: str  # Odds API event ID for fixture matching
    bookmaker: str
    market_type: str  # 'h2h' | 'totals' | 'btts'
    outcome: str       # 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    odds: float
    snapshot_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_match_odds_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a single The Odds API event dict into flat outcome rows.

    Returns:
        List of dicts with keys: bookmaker, market_type, outcome, odds.
        Only includes bookmakers in MATCH_BOOKMAKERS.
        For totals, only includes the 2.5-point line.
    """
    home_team = event.get("home_team", "")
    rows: list[dict[str, Any]] = []

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
                    else:
                        outcome = "away"
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "h2h",
                        "outcome": outcome,
                        "odds": float(price),
                    })

            elif mkey == "totals":
                for oc in market.get("outcomes", []):
                    point = oc.get("point")
                    if point != 2.5:
                        continue
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None:
                        continue
                    outcome = "over_2.5" if name == "over" else "under_2.5"
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "totals",
                        "outcome": outcome,
                        "odds": float(price),
                    })

            elif mkey == "both_teams_to_score":
                for oc in market.get("outcomes", []):
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None or name not in ("yes", "no"):
                        continue
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "btts",
                        "outcome": name,
                        "odds": float(price),
                    })

    return rows


async def ingest_match_odds_for_league(
    league: str,
    api_key: str | None = None,
) -> tuple[list[MatchOddsRow], list[dict[str, Any]]]:
    """Fetch and parse match-level odds for all upcoming events in a league.

    Returns:
        (rows, events) — rows ready for DB insert, raw events for fixture matching.
    """
    client = OddsAPIClient(api_key)
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        logger.warning("ingest_match_odds_for_league: unknown league %s", league)
        return [], []

    events = await client.get_events(sport_key)
    now = datetime.now(UTC)
    all_rows: list[MatchOddsRow] = []

    async with httpx.AsyncClient() as http:
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                # Respect quota guard before each call
                client._check_quota()

                response = await http.get(
                    f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds",
                    params={
                        "apiKey": client.api_key,
                        "markets": MATCH_MARKET_KEYS,
                        "regions": "eu,uk",
                        "bookmakers": ",".join(MATCH_BOOKMAKERS),
                    },
                    timeout=30.0,
                )

                # Update quota counter from response headers
                client._update_quota(response)

                if response.status_code != 200:
                    logger.warning(
                        "Match odds fetch failed for %s: HTTP %d",
                        event_id,
                        response.status_code,
                    )
                    continue
                data = response.json()
                full_event = {**event, "bookmakers": data.get("bookmakers", [])}
                parsed = parse_match_odds_event(full_event)
                for row_dict in parsed:
                    all_rows.append(
                        MatchOddsRow(
                            event_id=event_id,
                            bookmaker=row_dict["bookmaker"],
                            market_type=row_dict["market_type"],
                            outcome=row_dict["outcome"],
                            odds=row_dict["odds"],
                            snapshot_utc=now,
                        )
                    )
            except Exception as exc:
                logger.warning("Error fetching match odds for event %s: %s", event_id, exc)

    # Note: get_events() returns only upcoming fixtures by default (Odds API behaviour).
    return all_rows, events
