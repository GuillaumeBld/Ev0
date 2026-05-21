"""Canonical teams CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.canonical_teams import CanonicalTeam

router = APIRouter()


class CanonicalTeamOut(BaseModel):
    id: int
    name_fr: str
    name_en: str | None
    api_football_id: int | None
    aliases: list[str]


class CanonicalTeamUpdate(BaseModel):
    name_fr: str | None = None
    name_en: str | None = None
    api_football_id: int | None = None
    aliases: list[str] | None = None


@router.get("/canonical-teams", response_model=list[CanonicalTeamOut])
async def list_canonical_teams(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CanonicalTeam).order_by(CanonicalTeam.name_fr)
    )).scalars().all()
    return [
        CanonicalTeamOut(
            id=ct.id,
            name_fr=ct.name_fr,
            name_en=ct.name_en,
            api_football_id=ct.api_football_id,
            aliases=ct.aliases or [],
        )
        for ct in rows
    ]


@router.patch("/canonical-teams/{team_id}", response_model=CanonicalTeamOut)
async def update_canonical_team(
    team_id: int,
    body: CanonicalTeamUpdate,
    db: AsyncSession = Depends(get_db),
):
    ct = (await db.execute(
        select(CanonicalTeam).where(CanonicalTeam.id == team_id)
    )).scalar_one_or_none()
    if not ct:
        raise HTTPException(status_code=404, detail="Team not found")

    if body.name_fr is not None:
        ct.name_fr = body.name_fr
    if body.name_en is not None:
        ct.name_en = body.name_en
    if body.api_football_id is not None:
        ct.api_football_id = body.api_football_id
    if body.aliases is not None:
        ct.aliases = body.aliases

    await db.commit()
    await db.refresh(ct)
    return CanonicalTeamOut(
        id=ct.id,
        name_fr=ct.name_fr,
        name_en=ct.name_en,
        api_football_id=ct.api_football_id,
        aliases=ct.aliases or [],
    )
