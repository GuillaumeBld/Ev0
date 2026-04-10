"""Sync bzz_events — matches with scores, odds, lineups, shotmap, momentum, xG."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent

logger = logging.getLogger(__name__)


def _extract_odds(event: dict[str, Any]) -> tuple[dict | None, dict | None, dict | None]:
    odds = event.get("odds") or {}
    odds_1x2 = odds.get("1x2") or odds.get("home_draw_away")
    odds_ou = odds.get("over_under") or odds.get("totals")
    odds_btts = odds.get("btts") or odds.get("both_teams_to_score")
    return odds_1x2, odds_ou, odds_btts


# Internal Bzzoiro league IDs for the 5 major leagues + Champions League.
# These match the `id` column in bzz_leagues (not api_id).
TARGET_LEAGUE_IDS = [5, 8, 16, 21, 25, 29]  # Bundesliga, UCL, La Liga, Ligue 1, PL, Serie A


async def sync_events(
    session: AsyncSession,
    client: BzzoiroClient,
    days_back: int = 7,
    days_forward: int = 14,
    league_ids: list[int] | None = None,
) -> int:
    """Sync events for the given leagues within the date window.

    If ``league_ids`` is None, defaults to the 5 major leagues + Champions League.
    One API call per league keeps result sets small and avoids global noise.
    """
    if league_ids is None:
        league_ids = TARGET_LEAGUE_IDS

    now = datetime.now(UTC)
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%d")

    all_rows: list[dict] = []
    for league_id in league_ids:
        league_rows = await client.get_all(
            "/api/events/",
            params={"date_from": date_from, "date_to": date_to, "league": league_id},
        )
        all_rows.extend(league_rows)

    rows = all_rows
    count = 0
    for row in rows:
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
            "api_id": row["api_id"],
            "league_api_id": league.get("api_id"),
            "home_team_api_id": home_team.get("api_id"),
            "away_team_api_id": away_team.get("api_id"),
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
    logger.info("Synced %d events", count)
    return count
