"""Sync bzz_events — matches with scores, odds, lineups, shotmap, momentum, xG."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.constants import (
    CURRENT_SEASON,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.models.bzzoiro import BzzEvent
from app.services.season_service import current_season, season_start

logger = logging.getLogger(__name__)


def _extract_odds(event: dict[str, Any]) -> tuple[dict | None, dict | None, dict | None]:
    odds = event.get("odds") or {}
    odds_1x2 = odds.get("1x2") or odds.get("home_draw_away")
    odds_ou = odds.get("over_under") or odds.get("totals")
    odds_btts = odds.get("btts") or odds.get("both_teams_to_score")
    return odds_1x2, odds_ou, odds_btts


async def sync_events(
    session: AsyncSession,
    client: BzzoiroClient,
    days_back: int = 3,
    days_forward: int = 14,
    league_internal_ids: list[int] | None = None,
    full_season: bool = False,
) -> int:
    """Sync events for the given leagues within the date window.

    Args:
        days_back: How many past days to fetch (ignored if full_season=True).
        days_forward: How many future days to fetch.
        league_internal_ids: Bzzoiro internal IDs to pass as ?league= filter.
                             Defaults to all 6 target leagues.
                             NOTE: these are internal_ids, NOT api_ids.
        full_season: If True, fetches the entire current season from its start date
                     (resolved via season_service).
    """
    if league_internal_ids is None:
        league_internal_ids = TARGET_LEAGUE_INTERNAL_ID_LIST

    now = datetime.now(UTC)
    if full_season:
        date_from = season_start(await current_season(session)).isoformat()
    else:
        date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%d")

    logger.info(
        "Syncing events: %s → %s for %d leagues (full_season=%s)",
        date_from, date_to, len(league_internal_ids), full_season,
    )

    all_rows: list[dict] = []
    for internal_id in league_internal_ids:
        league_rows = await client.get_all(
            "/api/events/",
            params={"date_from": date_from, "date_to": date_to, "league": internal_id},
        )
        logger.info("  League internal_id=%d: %d events fetched", internal_id, len(league_rows))
        all_rows.extend(league_rows)

    count = 0
    for row in all_rows:
        api_id = row.get("api_id") or row.get("id")
        if not api_id:
            logger.warning("Skipping event row without id: %s", list(row.keys()))
            continue
        league = row.get("league") or {}
        home_team = row.get("home_team_obj") or {}
        away_team = row.get("away_team_obj") or {}
        odds_1x2, odds_ou, odds_btts = _extract_odds(row)
        event_date_raw = row.get("event_date")
        event_date = (
            datetime.fromisoformat(event_date_raw.replace("Z", "+00:00"))
            if event_date_raw else None
        )
        values = {
            "api_id": api_id,
            "league_api_id": league.get("api_id") or league.get("id"),
            "home_team_api_id": home_team.get("api_id") or home_team.get("id"),
            "away_team_api_id": away_team.get("api_id") or away_team.get("id"),
            "event_date": event_date,
            "status": row.get("status"),
            "period": row.get("period"),
            "current_minute": row.get("current_minute"),
            "round_number": row.get("round_number"),
            "home_score": row.get("home_score"),
            "away_score": row.get("away_score"),
            "home_score_ht": row.get("home_score_ht"),
            "away_score_ht": row.get("away_score_ht"),
            "home_xg": row["actual_home_xg"] if "actual_home_xg" in row else row.get("home_xg"),
            "away_xg": row["actual_away_xg"] if "actual_away_xg" in row else row.get("away_xg"),
            "shotmap": row.get("shotmap"),
            "incidents": row.get("incidents"),
            "momentum": row.get("momentum"),
            "average_positions": row.get("average_positions"),
            "lineups": row.get("lineups"),
            "odds_1x2": odds_1x2,
            "odds_over_under": odds_ou,
            "odds_btts": odds_btts,
            "synced_at": now,
        }
        stmt = pg_insert(BzzEvent).values(**values).on_conflict_do_update(
            index_elements=["api_id"],
            set_={k: v for k, v in values.items() if k != "api_id"},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d events total", count)
    return count
