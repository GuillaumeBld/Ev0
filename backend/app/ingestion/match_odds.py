"""Match-level odds ingestion (h2h / totals / btts) from The Odds API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def parse_match_odds_event(event: dict[str, Any]) -> list[MatchOddsRow]:
    """Parse a single The Odds API event dict into flat outcome rows.

    Returns:
        List of MatchOddsRow objects.
        Only includes bookmakers in MATCH_BOOKMAKERS.
        For totals, only includes the 2.5-point line, and only when both
        over_2.5 and under_2.5 are present.
    """
    event_id = event.get("id", "")
    home_team = event.get("home_team", "")
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
                    else:
                        outcome = "away"
                    rows.append(MatchOddsRow(
                        event_id=event_id,
                        bookmaker=bm_key,
                        market_type="h2h",
                        outcome=outcome,
                        odds=float(price),
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
                    ))

    return rows


async def ingest_match_odds_for_league(
    league: str,
    session: AsyncSession,
    api_key: str | None = None,
) -> tuple[list[MatchOddsRow], list[dict]]:
    """Fetch and parse match-level odds for upcoming fixtures in a league.

    Queries the fixtures table for fixtures in the next 7 days whose
    odds_api_event_id is set, then fetches odds from The Odds API for each.

    Args:
        league: League identifier (e.g. 'ligue_1').
        session: SQLAlchemy async session for DB queries.
        api_key: Optional Odds API key override.

    Returns:
        (rows, errors) — rows ready for DB insert, list of error messages.
    """
    from app.models.fixtures import Fixture

    client = OddsAPIClient(api_key)
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        logger.warning("ingest_match_odds_for_league: unknown league %s", league)
        return [], []

    now = datetime.now(UTC)
    window_end = now + timedelta(days=7)

    result = await session.execute(
        select(Fixture).where(
            Fixture.league == league,
            Fixture.kickoff_utc >= now,
            Fixture.kickoff_utc <= window_end,
            Fixture.odds_api_event_id.isnot(None),
        )
    )
    fixtures = list(result.scalars().all())

    all_rows: list[MatchOddsRow] = []
    errors: list[dict] = []

    async with httpx.AsyncClient() as http:
        for fixture in fixtures:
            event_id = fixture.odds_api_event_id
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
                    msg = f"Match odds fetch failed for {event_id}: HTTP {response.status_code}"
                    logger.warning(msg)
                    errors.append({"fixture_id": fixture.id, "error": msg})
                    continue

                data = response.json()
                full_event = {
                    "id": event_id,
                    "home_team": fixture.home_team,
                    "away_team": fixture.away_team,
                    "bookmakers": data.get("bookmakers", []),
                }
                parsed = parse_match_odds_event(full_event)
                all_rows.extend(parsed)
            except Exception as exc:
                msg = f"Error fetching match odds for event {event_id}: {exc}"
                logger.warning(msg)
                errors.append({"fixture_id": fixture.id, "error": msg})

    return all_rows, errors
