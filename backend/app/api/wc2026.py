"""WC 2026 squad endpoints."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.wc2026 import WC2026SquadPlayer

router = APIRouter(prefix="/wc2026", tags=["wc2026"])


def _normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", ascii_str.lower())


def _row_to_player_dict(row: dict) -> dict:
    return {
        "player_name": row["player_name"],
        "club": row["club"],
        "position": row["position"],
        "shirt_number": row["shirt_number"],
        "matches_played": row["matches_played"],
        "minutes_played": row["minutes_played"],
        "goals": row["goals"],
        "assists": row["assists"],
        "xg": row["xg"],
        "xa": row["xa"],
        "xg_per90": row["xg_per90"],
        "xa_per90": row["xa_per90"],
        "avg_rating": row["avg_rating"],
        "saves": row["saves"],
        "form_goals_5": row["form_goals_5"],
        "form_xg_5": row["form_xg_5"],
        "form_rating_5": row["form_rating_5"],
    }


class WCPlayerOut(BaseModel):
    player_name: str
    club: str | None
    position: str
    shirt_number: int | None


class WCNationOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    player_count: int


class WCSquadOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    gk: list[WCPlayerOut]
    def_: list[WCPlayerOut]
    mid: list[WCPlayerOut]
    fwd: list[WCPlayerOut]

    model_config = {"populate_by_name": True}


def _group_by_position(players: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"gk": [], "def_": [], "mid": [], "fwd": []}
    pos_map = {"GK": "gk", "DEF": "def_", "MID": "mid", "FWD": "fwd"}
    for p in players:
        key = pos_map.get(p.position, "mid")
        grouped[key].append({
            "player_name": p.player_name,
            "club": p.club,
            "position": p.position,
            "shirt_number": p.shirt_number,
        })
    return grouped


def _build_squad_response(
    nation: str,
    group_letter: str,
    players: list[Any],
) -> dict[str, Any]:
    grouped = _group_by_position(players)
    flag = players[0].flag_emoji if players else None
    return {
        "nation": nation,
        "group_letter": group_letter,
        "flag_emoji": flag,
        "gk": grouped["gk"],
        "def_": grouped["def_"],
        "mid": grouped["mid"],
        "fwd": grouped["fwd"],
    }


@router.get("/nations", response_model=list[WCNationOut])
async def get_nations(session: AsyncSession = Depends(get_db)) -> list[WCNationOut]:
    """List all WC nations sorted by group then name."""
    rows = await session.execute(
        select(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
            sa_func.count(WC2026SquadPlayer.id).label("player_count"),
        )
        .group_by(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
        )
        .order_by(WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation)
    )
    return [
        WCNationOut(
            nation=r.nation,
            group_letter=r.group_letter,
            flag_emoji=r.flag_emoji,
            player_count=r.player_count,
        )
        for r in rows
    ]


@router.get("/squads", response_model=WCSquadOut)
async def get_squad(
    nation: str = Query(..., description="Nation name (French), e.g. 'France'"),
    session: AsyncSession = Depends(get_db),
) -> WCSquadOut:
    """Return squad grouped by position for a given nation."""
    rows = await session.execute(
        select(WC2026SquadPlayer)
        .where(WC2026SquadPlayer.nation == nation)
        .order_by(WC2026SquadPlayer.shirt_number)
    )
    players = list(rows.scalars().all())
    if not players:
        raise HTTPException(status_code=404, detail=f"Nation '{nation}' not found")

    data = _build_squad_response(nation, players[0].group_letter, players)
    return WCSquadOut(
        nation=data["nation"],
        group_letter=data["group_letter"],
        flag_emoji=data["flag_emoji"],
        gk=[WCPlayerOut(**p) for p in data["gk"]],
        def_=[WCPlayerOut(**p) for p in data["def_"]],
        mid=[WCPlayerOut(**p) for p in data["mid"]],
        fwd=[WCPlayerOut(**p) for p in data["fwd"]],
    )
