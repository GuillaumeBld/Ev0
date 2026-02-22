"""Settings API endpoints."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.settings import UserSettings

logger = logging.getLogger(__name__)

router = APIRouter()


class SettingsUpdateRequest(BaseModel):
    """Dict of key-value pairs to upsert."""
    settings: dict[str, str]


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Get all user settings as a key-value dict."""
    result = await db.execute(select(UserSettings))
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


@router.put("/settings")
async def save_settings(
    body: SettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Upsert user settings. Accepts a dict of key-value pairs."""
    for key, value in body.settings.items():
        result = await db.execute(
            select(UserSettings).where(UserSettings.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(UserSettings(key=key, value=value))

    await db.commit()

    # Return updated settings
    result = await db.execute(select(UserSettings))
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}
