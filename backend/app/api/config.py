"""App config API endpoints — global toggles (e.g. xG source mode)."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.pricing.xg_resolver import XgMode, get_global_xg_mode, set_global_xg_mode

logger = logging.getLogger(__name__)

router = APIRouter()


class XgSourceResponse(BaseModel):
    mode: str


class XgSourceRequest(BaseModel):
    mode: str


@router.get("/config/xg-source", response_model=XgSourceResponse)
async def get_xg_source(
    db: AsyncSession = Depends(get_db),
) -> XgSourceResponse:
    """Get the global xG source mode (bzzoiro or model)."""
    mode = await get_global_xg_mode(db)
    return XgSourceResponse(mode=mode.value)


@router.patch("/config/xg-source", response_model=XgSourceResponse)
async def patch_xg_source(
    body: XgSourceRequest,
    db: AsyncSession = Depends(get_db),
) -> XgSourceResponse:
    """Set the global xG source mode. Accepts {"mode": "bzzoiro"} or {"mode": "model"}."""
    try:
        mode = XgMode(body.mode)
    except ValueError:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=f"Invalid mode {body.mode!r}. Must be 'bzzoiro' or 'model'.",
        ) from None
    await set_global_xg_mode(db, mode)
    return XgSourceResponse(mode=mode.value)
