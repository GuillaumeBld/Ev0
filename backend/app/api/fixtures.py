"""Fixtures API endpoints."""

import logging
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.fixtures import Fixture
from app.models.odds import OddsSnapshot

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response models ──────────────────────────────────────────────


class OddsSnapshotOut(BaseModel):
    id: int
    player_name: str
    market_type: str
    bookmaker: str
    odds: float
    implied_probability: float
    snapshot_utc: str


class FixtureOut(BaseModel):
    id: int
    external_id: str
    league: str
    season: str
    matchweek: int | None
    home_team: str
    away_team: str
    kickoff_utc: str
    status: str
    home_score: int | None
    away_score: int | None
    odds_count: int
    odds: list[OddsSnapshotOut]


class OddsCreate(BaseModel):
    player_name: str
    market_type: str = "goalscorer"
    bookmaker: str = "Betclic"
    odds: float


class FixtureCreate(BaseModel):
    date: str
    time: str
    home_team: str
    away_team: str
    league: str = "ligue_1"
    season: str = "2024-25"


class FixturesResponse(BaseModel):
    count: int
    fixtures: list[FixtureOut]


# ── Endpoints ────────────────────────────────────────────────────


def _apply_upcoming_only_filter(stmt, upcoming_only: bool):
    """Filter fixtures to only those with kickoff in the future."""
    if not upcoming_only:
        return stmt
    return stmt.where(Fixture.kickoff_utc > datetime.now(UTC))


@router.get("/fixtures", response_model=FixturesResponse)
async def list_fixtures(
    db: AsyncSession = Depends(get_db),
    league: str | None = Query(None),
    status: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    limit: int = Query(50, le=200),
    upcoming_only: bool = Query(False),
):
    """List fixtures with optional filters."""
    # Default sort: upcoming first (asc), finished last (desc)
    order_col = (
        Fixture.kickoff_utc.asc()
        if (status in ("scheduled", "upcoming") or (not status))
        else Fixture.kickoff_utc.desc()
    )

    # Count odds per fixture in a single subquery — never load all rows
    odds_count_subq = (
        select(OddsSnapshot.fixture_id, func.count().label("cnt"))
        .group_by(OddsSnapshot.fixture_id)
        .subquery()
    )

    stmt = (
        select(Fixture, func.coalesce(odds_count_subq.c.cnt, 0).label("odds_count"))
        .outerjoin(odds_count_subq, Fixture.id == odds_count_subq.c.fixture_id)
        .order_by(order_col)
        .limit(limit)
    )

    if league:
        stmt = stmt.where(Fixture.league == league)
    if status:
        # Map frontend filter values to DB status
        status_map = {"upcoming": "scheduled", "finished": "finished", "live": "live"}
        db_status = status_map.get(status, status)
        stmt = stmt.where(Fixture.status == db_status)
    if from_date:
        stmt = stmt.where(
            Fixture.kickoff_utc >= datetime.combine(from_date, datetime.min.time(), tzinfo=UTC)
        )
    if to_date:
        stmt = stmt.where(
            Fixture.kickoff_utc <= datetime.combine(to_date, datetime.max.time(), tzinfo=UTC)
        )
    stmt = _apply_upcoming_only_filter(stmt, upcoming_only)

    result = await db.execute(stmt)
    rows = result.all()

    items = [
        FixtureOut(
            id=f.id,
            external_id=f.external_id,
            league=f.league,
            season=f.season,
            matchweek=f.matchweek,
            home_team=f.home_team,
            away_team=f.away_team,
            kickoff_utc=str(f.kickoff_utc),
            status=f.status,
            home_score=f.home_score,
            away_score=f.away_score,
            odds_count=count,
            odds=[],
        )
        for f, count in rows
    ]

    return FixturesResponse(count=len(items), fixtures=items)


@router.post("/fixtures", response_model=FixtureOut, status_code=201)
async def create_fixture(
    body: FixtureCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a fixture manually."""
    kickoff = datetime.fromisoformat(f"{body.date}T{body.time}:00+00:00")
    external_id = f"manual_{uuid.uuid4().hex[:12]}"

    fixture = Fixture(
        external_id=external_id,
        league=body.league,
        season=body.season,
        home_team=body.home_team,
        away_team=body.away_team,
        kickoff_utc=kickoff,
        status="scheduled",
    )
    db.add(fixture)
    await db.commit()
    await db.refresh(fixture)

    return FixtureOut(
        id=fixture.id,
        external_id=fixture.external_id,
        league=fixture.league,
        season=fixture.season,
        matchweek=fixture.matchweek,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        kickoff_utc=str(fixture.kickoff_utc),
        status=fixture.status,
        home_score=fixture.home_score,
        away_score=fixture.away_score,
        odds_count=0,
        odds=[],
    )


@router.delete("/fixtures/{fixture_id}", status_code=204)
async def delete_fixture(
    fixture_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a fixture."""
    result = await db.execute(select(Fixture).where(Fixture.id == fixture_id))
    fixture = result.scalar_one_or_none()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    await db.delete(fixture)
    await db.commit()


@router.get("/fixtures/{fixture_id}/odds", response_model=list[OddsSnapshotOut])
async def get_fixture_odds(
    fixture_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get odds snapshots for a fixture."""
    result = await db.execute(
        select(OddsSnapshot)
        .where(OddsSnapshot.fixture_id == fixture_id)
        .order_by(OddsSnapshot.snapshot_utc.desc())
    )
    snapshots = result.scalars().all()
    return [
        OddsSnapshotOut(
            id=o.id,
            player_name=o.player_name,
            market_type=o.market_type,
            bookmaker=o.bookmaker,
            odds=o.odds,
            implied_probability=o.implied_probability,
            snapshot_utc=str(o.snapshot_utc),
        )
        for o in snapshots
    ]


@router.post("/fixtures/{fixture_id}/odds", response_model=OddsSnapshotOut, status_code=201)
async def create_odds(
    fixture_id: int,
    body: OddsCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a manual odds snapshot for a fixture."""
    result = await db.execute(select(Fixture).where(Fixture.id == fixture_id))
    fixture = result.scalar_one_or_none()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    if body.odds <= 1.0:
        raise HTTPException(status_code=400, detail="Odds must be greater than 1.0")

    now = datetime.now(UTC)
    snapshot = OddsSnapshot(
        fixture_id=fixture_id,
        player_name=body.player_name,
        market_type=body.market_type,
        bookmaker=body.bookmaker,
        odds=body.odds,
        implied_probability=round(1.0 / body.odds, 6),
        snapshot_utc=now,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return OddsSnapshotOut(
        id=snapshot.id,
        player_name=snapshot.player_name,
        market_type=snapshot.market_type,
        bookmaker=snapshot.bookmaker,
        odds=snapshot.odds,
        implied_probability=snapshot.implied_probability,
        snapshot_utc=str(snapshot.snapshot_utc),
    )
