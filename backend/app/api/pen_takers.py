"""Pen taker override storage per fixture (persisted in app_config)."""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.app_config import AppConfig

router = APIRouter()


def _config_key(fixture_id: int) -> str:
    return f"pen_taker::{fixture_id}"


class PenTakerResponse(BaseModel):
    fixture_id: int
    home_pen_taker_id: int | None = None
    away_pen_taker_id: int | None = None


class PenTakerRequest(BaseModel):
    fixture_id: int
    home_pen_taker_id: int | None = None
    away_pen_taker_id: int | None = None


@router.get("/pen-takers/{fixture_id}", response_model=PenTakerResponse)
async def get_pen_takers(
    fixture_id: int,
    db: AsyncSession = Depends(get_db),
) -> PenTakerResponse:
    """Return persisted pen taker overrides for a fixture."""
    key = _config_key(fixture_id)
    result = await db.execute(select(AppConfig).where(AppConfig.key == key))
    row = result.scalar_one_or_none()
    if not row:
        return PenTakerResponse(fixture_id=fixture_id)
    data = json.loads(row.value)
    return PenTakerResponse(
        fixture_id=fixture_id,
        home_pen_taker_id=data.get("home"),
        away_pen_taker_id=data.get("away"),
    )


@router.post("/pen-takers", response_model=PenTakerResponse)
async def set_pen_takers(
    body: PenTakerRequest,
    db: AsyncSession = Depends(get_db),
) -> PenTakerResponse:
    """Persist pen taker overrides for a fixture."""
    key = _config_key(body.fixture_id)
    value = json.dumps({"home": body.home_pen_taker_id, "away": body.away_pen_taker_id})
    result = await db.execute(select(AppConfig).where(AppConfig.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppConfig(key=key, value=value))
    await db.commit()
    return PenTakerResponse(
        fixture_id=body.fixture_id,
        home_pen_taker_id=body.home_pen_taker_id,
        away_pen_taker_id=body.away_pen_taker_id,
    )
