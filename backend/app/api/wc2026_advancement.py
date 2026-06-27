"""WC2026 team advancement / ELO endpoint."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.wc2026_advancement import WC2026TeamAdvancement

router = APIRouter(prefix="/wc2026/advancement", tags=["wc2026"])


class TeamAdvancementOut(BaseModel):
    nation: str
    elo: float
    p_r32: float
    p_r16: float
    p_qf: float
    p_sf: float
    p_finalist: float
    p_winner: float
    e_games: float
    n_sim: int
    computed_at: datetime


@router.get("", response_model=list[TeamAdvancementOut])
async def get_wc_advancement(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(WC2026TeamAdvancement).order_by(WC2026TeamAdvancement.elo.desc())
    )).scalars().all()
    return rows
