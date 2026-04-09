"""Sync bzz_leagues and bzz_teams from Bzzoiro API."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzLeague, BzzTeam

logger = logging.getLogger(__name__)


async def sync_leagues(session: AsyncSession, client: BzzoiroClient) -> int:
    rows = await client.get_all("/api/leagues/")
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        stmt = pg_insert(BzzLeague).values(
            api_id=row["api_id"],
            name=row.get("name", ""),
            country=row.get("country"),
            season_id=row.get("season_id"),
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={"name": row.get("name", ""), "country": row.get("country"),
                  "season_id": row.get("season_id"), "synced_at": now},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    logger.info("Synced %d leagues", count)
    return count


async def sync_teams(session: AsyncSession, client: BzzoiroClient) -> int:
    rows = await client.get_all("/api/teams/")
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        stmt = pg_insert(BzzTeam).values(
            api_id=row["api_id"],
            name=row.get("name", ""),
            short_name=row.get("short_name"),
            country=row.get("country"),
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={"name": row.get("name", ""), "short_name": row.get("short_name"),
                  "country": row.get("country"), "synced_at": now},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    logger.info("Synced %d teams", count)
    return count
