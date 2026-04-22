"""Sync bzz_players from Bzzoiro API."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzPlayer

logger = logging.getLogger(__name__)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


async def sync_players(session: AsyncSession, client: BzzoiroClient) -> int:
    rows = await client.get_all("/api/players/")
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        api_id = row.get("api_id") or row.get("id")
        if not api_id:
            continue
        team = row.get("current_team") or {}
        nat_team = row.get("national_team") or {}
        values = {
            "api_id": api_id,
            "name": row.get("name", ""),
            "short_name": row.get("short_name"),
            "nationality": row.get("nationality"),
            "date_of_birth": _parse_date(row.get("date_of_birth")),
            "height": row.get("height"),
            "jersey_number": row.get("jersey_number"),
            "position": row.get("position"),
            "market_value": row.get("market_value"),
            "current_team_api_id": team.get("api_id") or team.get("id"),
            "current_team_name": team.get("name"),
            "national_team_api_id": nat_team.get("api_id") or nat_team.get("id"),
            "synced_at": now,
        }
        stmt = pg_insert(BzzPlayer).values(**values).on_conflict_do_update(
            index_elements=["api_id"],
            set_={k: v for k, v in values.items() if k != "api_id"},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    logger.info("Synced %d players", count)
    return count
