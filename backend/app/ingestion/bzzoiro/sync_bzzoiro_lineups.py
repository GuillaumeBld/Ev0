"""Parse lineups from bzz_events.lineups JSONB → TeamLineup / TeamLineupPlayer.

bzz_events.lineups is populated by sync_events from the Bzzoiro events API.
This module reads those JSONB blobs and writes official-quality lineup rows
so lineup_resolver can use them (lineup_type="bzzoiro").

Run every 30 min. Lineups appear in Bzzoiro ~1h before KO.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer

logger = logging.getLogger(__name__)

_POSITION_MAP: dict[str, str] = {
    "G": "GK",
    "D": "DEF",
    "M": "MID",
    "F": "FWD",
}


def _map_position(raw: str | None) -> str:
    if not raw:
        return "MID"
    return _POSITION_MAP.get(raw.strip().upper(), "MID")


async def sync_bzzoiro_lineups(
    session: AsyncSession,
    days_forward: int = 2,
) -> int:
    """Extract lineups from upcoming bzz_events and write to TeamLineup.

    Only events with non-null lineups and a matching Fixture are processed.
    Existing lineup rows with lineup_type="bzzoiro" are overwritten.

    Args:
        session: Async SQLAlchemy session.
        days_forward: Look ahead window in days.

    Returns:
        Number of TeamLineup rows created/updated (one per team per fixture).
    """
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_forward)

    result = await session.execute(
        select(BzzEvent).where(
            BzzEvent.event_date >= now,
            BzzEvent.event_date <= cutoff,
            BzzEvent.lineups.is_not(None),
        )
    )
    events = result.scalars().all()

    count = 0
    for event in events:
        if not event.lineups:
            continue

        fixture_result = await session.execute(
            select(Fixture).where(
                Fixture.external_id == f"bzz_{event.api_id}"
            ).limit(1)
        )
        fixture = fixture_result.scalar_one_or_none()
        if fixture is None:
            continue

        lineups_data: dict[str, Any] = event.lineups
        team_map = {
            "home": fixture.home_team,
            "away": fixture.away_team,
        }

        for side, team_name in team_map.items():
            side_data = lineups_data.get(side) or {}
            players_raw = side_data.get("players") or []
            if not players_raw:
                continue

            # Find or replace existing bzzoiro lineup for this fixture+team
            existing_result = await session.execute(
                select(TeamLineup).where(
                    TeamLineup.fixture_id == fixture.id,
                    TeamLineup.team == team_name,
                    TeamLineup.lineup_type == "bzzoiro",
                ).limit(1)
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.flush()

            lineup = TeamLineup(
                fixture_id=fixture.id,
                team=team_name,
                lineup_type="bzzoiro",
                source="bzzoiro",
                created_by="sync_bzzoiro_lineups",
            )
            session.add(lineup)
            await session.flush()  # get lineup.id

            for p in players_raw:
                player = TeamLineupPlayer(
                    lineup_id=lineup.id,
                    player_name=p.get("name", ""),
                    position=_map_position(p.get("position")),
                    is_starter=p.get("sub_in") is None,
                    jersey_number=int(p["jersey_number"]) if p.get("jersey_number") else None,
                )
                session.add(player)

            count += 1
            logger.info(
                "sync_bzzoiro_lineups: %s — %s (%d players)",
                team_name, fixture.external_id, len(players_raw),
            )

    await session.commit()
    logger.info("sync_bzzoiro_lineups: %d team lineups written", count)
    return count
