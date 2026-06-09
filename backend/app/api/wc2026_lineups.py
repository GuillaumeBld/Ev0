# backend/app/api/wc2026_lineups.py
"""WC2026 expected lineups CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.wc2026.formations import FORMATIONS, default_minutes_for_role, parse_formation, validate_lineup_formation
from app.models.wc2026 import WC2026SquadPlayer
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

router = APIRouter(prefix="/wc2026/lineups", tags=["wc2026"])

_VALID_CONTEXTS = {
    "default", "matchday_1", "matchday_2", "matchday_3",
    "r16", "qf", "sf", "final",
}


def _context_valid(ctx: str) -> bool:
    return ctx in _VALID_CONTEXTS


def _role_to_minutes(role: str) -> int:
    return default_minutes_for_role(role)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LineupPlayerIn(BaseModel):
    player_name: str
    position: str       # GK / DEF / MID / FWD
    line_index: int
    slot_index: int
    is_starter: bool = True
    role: str           # starter | sub_planned | sub_tactical | reserve
    expected_minutes: int


class LineupUpsertIn(BaseModel):
    formation: str
    players: list[LineupPlayerIn]


class LineupPlayerOut(BaseModel):
    player_name: str
    position: str
    line_index: int
    slot_index: int
    is_starter: bool
    role: str
    expected_minutes: int


class LineupOut(BaseModel):
    nation: str
    context: str
    formation: str
    source: str
    players: list[LineupPlayerOut]


class NationStatusOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    complete: bool     # True if a "default" lineup with 11 starters exists
    starters_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NationStatusOut])
async def list_nations(session: AsyncSession = Depends(get_db)) -> list[NationStatusOut]:
    """List all WC2026 nations with their lineup completion status."""
    # Distinct nations from squad
    nations_result = await session.execute(
        select(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
        ).distinct(WC2026SquadPlayer.nation).order_by(
            WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation
        )
    )
    nations = nations_result.all()

    # Load default lineups
    lineups_result = await session.execute(
        select(WC2026ExpectedLineup).where(WC2026ExpectedLineup.context == "default")
    )
    lineups_by_nation: dict[str, WC2026ExpectedLineup] = {
        l.nation: l for l in lineups_result.scalars().all()
    }

    # Load starter counts for default lineups
    starters_count: dict[str, int] = {}
    if lineups_by_nation:
        for nation, lineup in lineups_by_nation.items():
            players_result = await session.execute(
                select(WC2026ExpectedLineupPlayer).where(
                    WC2026ExpectedLineupPlayer.lineup_id == lineup.id,
                    WC2026ExpectedLineupPlayer.is_starter.is_(True),
                )
            )
            starters_count[nation] = len(players_result.scalars().all())

    out = []
    for row in nations:
        nation = row.nation
        count = starters_count.get(nation, 0)
        out.append(NationStatusOut(
            nation=nation,
            group_letter=row.group_letter,
            flag_emoji=row.flag_emoji,
            complete=count == 11,
            starters_count=count,
        ))
    return out


@router.get("/{nation}", response_model=dict)
async def get_nation_lineups(
    nation: str,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return all lineups for a nation (default + matchday overrides) and the full squad."""
    # Squad players for the panel
    squad_result = await session.execute(
        select(WC2026SquadPlayer).where(WC2026SquadPlayer.nation == nation).order_by(
            WC2026SquadPlayer.position, WC2026SquadPlayer.shirt_number
        )
    )
    squad = squad_result.scalars().all()
    if not squad:
        raise HTTPException(status_code=404, detail=f"Nation not found: {nation}")

    # All lineups for this nation
    lineups_result = await session.execute(
        select(WC2026ExpectedLineup).where(WC2026ExpectedLineup.nation == nation)
    )
    lineups = lineups_result.scalars().all()

    # Load players for each lineup
    lineups_out: dict[str, LineupOut] = {}
    for lineup in lineups:
        players_result = await session.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            ).order_by(
                WC2026ExpectedLineupPlayer.line_index,
                WC2026ExpectedLineupPlayer.slot_index,
            )
        )
        players = players_result.scalars().all()
        lineups_out[lineup.context] = LineupOut(
            nation=lineup.nation,
            context=lineup.context,
            formation=lineup.formation,
            source=lineup.source,
            players=[
                LineupPlayerOut(
                    player_name=p.player_name,
                    position=p.position,
                    line_index=p.line_index,
                    slot_index=p.slot_index,
                    is_starter=p.is_starter,
                    role=p.role,
                    expected_minutes=p.expected_minutes,
                )
                for p in players
            ],
        )

    return {
        "nation": nation,
        "flag_emoji": squad[0].flag_emoji,
        "squad": [
            {
                "player_name": p.player_name,
                "position": p.position,
                "shirt_number": p.shirt_number,
            }
            for p in squad
        ],
        "lineups": {ctx: lo.model_dump() for ctx, lo in lineups_out.items()},
    }


@router.put("/{nation}/{context}", response_model=LineupOut)
async def upsert_lineup(
    nation: str,
    context: str,
    body: LineupUpsertIn,
    session: AsyncSession = Depends(get_db),
) -> LineupOut:
    """Create or replace a lineup for a nation+context. Replaces all players atomically."""
    if not _context_valid(context):
        raise HTTPException(status_code=422, detail=f"Invalid context: {context!r}")

    if body.formation not in FORMATIONS:
        raise HTTPException(status_code=422, detail=f"Unknown formation: {body.formation!r}")

    # Validate starter count (outfield players with line_index > 0)
    try:
        validate_lineup_formation(
            body.formation,
            [p.model_dump() for p in body.players if p.is_starter and p.line_index > 0],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Upsert lineup header
    result = await session.execute(
        select(WC2026ExpectedLineup).where(
            WC2026ExpectedLineup.nation == nation,
            WC2026ExpectedLineup.context == context,
        )
    )
    lineup = result.scalar_one_or_none()
    if lineup is None:
        lineup = WC2026ExpectedLineup(nation=nation, context=context, formation=body.formation, source="manual")
        session.add(lineup)
        await session.flush()
    else:
        lineup.formation = body.formation
        lineup.source = "manual"
        lineup.updated_at = datetime.now(timezone.utc)
        # Delete existing players
        for player in lineup.players:
            await session.delete(player)
        await session.flush()

    # Insert new players
    for p in body.players:
        session.add(WC2026ExpectedLineupPlayer(
            lineup_id=lineup.id,
            player_name=p.player_name,
            position=p.position,
            line_index=p.line_index,
            slot_index=p.slot_index,
            is_starter=p.is_starter,
            role=p.role,
            expected_minutes=p.expected_minutes,
        ))

    await session.commit()
    await session.refresh(lineup)

    players_result = await session.execute(
        select(WC2026ExpectedLineupPlayer).where(
            WC2026ExpectedLineupPlayer.lineup_id == lineup.id
        ).order_by(
            WC2026ExpectedLineupPlayer.line_index,
            WC2026ExpectedLineupPlayer.slot_index,
        )
    )
    players = players_result.scalars().all()

    return LineupOut(
        nation=lineup.nation,
        context=lineup.context,
        formation=lineup.formation,
        source=lineup.source,
        players=[
            LineupPlayerOut(
                player_name=p.player_name,
                position=p.position,
                line_index=p.line_index,
                slot_index=p.slot_index,
                is_starter=p.is_starter,
                role=p.role,
                expected_minutes=p.expected_minutes,
            )
            for p in players
        ],
    )
