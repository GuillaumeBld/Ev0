# backend/app/api/wc2026_lineups.py
"""WC2026 expected lineups CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import unicodedata

from app.db import get_db
from app.ingestion.wc2026.formations import FORMATIONS, default_minutes_for_role, parse_formation, validate_lineup_formation
from app.ingestion.wc2026.sync_rotowire_lineups import seed_from_rotowire


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def _lookup_shirt(
    lineup_name: str,
    lineup_position: str,
    squad: list,
) -> int | None:
    """Find shirt number with exact-norm match, then prefix+position fallback.

    Handles Brazilian-style single-name players (e.g. 'Douglas' → 'Douglas Santos').
    """
    key = _norm_name(lineup_name)
    # 1. Exact normalized match
    for sp in squad:
        if _norm_name(sp.player_name) == key:
            return sp.shirt_number
    # 2. Prefix match: squad name starts with lineup name (word boundary)
    candidates = [
        sp for sp in squad
        if _norm_name(sp.player_name).startswith(key + " ")
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].shirt_number
    # 3. Disambiguate by position
    pos_filtered = [sp for sp in candidates if sp.position == lineup_position]
    if len(pos_filtered) == 1:
        return pos_filtered[0].shirt_number
    # 4. Still ambiguous: pick lowest shirt number (more established player)
    best = min(pos_filtered or candidates, key=lambda sp: sp.shirt_number or 999)
    return best.shirt_number
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
    shirt_number: int | None = None


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
        ).distinct(WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation).order_by(
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

    # Load starter counts for default lineups in one query
    starters_count: dict[str, int] = {}
    if lineups_by_nation:
        lineup_ids = [l.id for l in lineups_by_nation.values()]
        id_to_nation = {l.id: n for n, l in lineups_by_nation.items()}
        counts_result = await session.execute(
            select(WC2026ExpectedLineupPlayer.lineup_id, func.count())
            .where(
                WC2026ExpectedLineupPlayer.lineup_id.in_(lineup_ids),
                WC2026ExpectedLineupPlayer.is_starter.is_(True),
            )
            .group_by(WC2026ExpectedLineupPlayer.lineup_id)
        )
        for lineup_id, count in counts_result.all():
            nation = id_to_nation[lineup_id]
            starters_count[nation] = count

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
                    shirt_number=_lookup_shirt(p.player_name, p.position, squad),
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

    # Validate starter count — 10 outfield + exactly 1 GK
    starters = [p for p in body.players if p.is_starter]
    gk_count = sum(1 for p in starters if p.line_index == 0)
    if gk_count != 1:
        raise HTTPException(status_code=422, detail=f"Expected exactly 1 GK (line_index=0), got {gk_count}")
    try:
        validate_lineup_formation(
            body.formation,
            [p.model_dump() for p in starters if p.line_index > 0],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Upsert lineup header — eager-load players to avoid MissingGreenlet in async
    result = await session.execute(
        select(WC2026ExpectedLineup)
        .options(selectinload(WC2026ExpectedLineup.players))
        .where(
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

    squad_result = await session.execute(
        select(WC2026SquadPlayer).where(WC2026SquadPlayer.nation == nation)
    )
    squad = squad_result.scalars().all()

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
                shirt_number=_lookup_shirt(p.player_name, p.position, squad),
            )
            for p in players
        ],
    )


@router.post("/sync-rotowire")
async def sync_rotowire(session: AsyncSession = Depends(get_db)) -> dict:
    """Scrape Rotowire and pre-populate missing lineups (skips manual lineups)."""
    statuses = await seed_from_rotowire(session)
    seeded = sum(1 for s in statuses.values() if s == "seeded")
    skipped = sum(1 for s in statuses.values() if s == "skipped_manual")
    no_match = sum(1 for s in statuses.values() if s == "no_match")
    return {"seeded": seeded, "skipped_manual": skipped, "no_match": no_match, "detail": statuses}
