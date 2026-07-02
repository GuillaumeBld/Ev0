"""Fixtures API endpoints."""

import logging
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.player_odds_snapshot import PlayerOddsSnapshot

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
    snapshot_utc: str  # maps to scraped_at


class FixtureOut(BaseModel):
    id: int
    external_id: str
    league: str
    season: str
    matchweek: int | None
    home_team: str
    away_team: str
    home_team_id: str | None
    away_team_id: str | None
    home_api_football_id: int | None
    away_api_football_id: int | None
    kickoff_utc: str
    status: str
    home_score: int | None
    away_score: int | None
    odds_count: int
    goalscorer_count: int
    assist_count: int
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

    # Count odds per fixture — total, goalscorer, and assist — in parallel subqueries
    odds_count_subq = (
        select(PlayerOddsSnapshot.fixture_id, func.count().label("cnt"))
        .group_by(PlayerOddsSnapshot.fixture_id)
        .subquery()
    )
    goalscorer_subq = (
        select(PlayerOddsSnapshot.fixture_id, func.count().label("gcnt"))
        .where(PlayerOddsSnapshot.market_type == "goalscorer")
        .group_by(PlayerOddsSnapshot.fixture_id)
        .subquery()
    )
    assist_subq = (
        select(PlayerOddsSnapshot.fixture_id, func.count().label("acnt"))
        .where(PlayerOddsSnapshot.market_type == "assist")
        .group_by(PlayerOddsSnapshot.fixture_id)
        .subquery()
    )

    stmt = (
        select(
            Fixture,
            func.coalesce(odds_count_subq.c.cnt, 0).label("odds_count"),
            func.coalesce(goalscorer_subq.c.gcnt, 0).label("goalscorer_count"),
            func.coalesce(assist_subq.c.acnt, 0).label("assist_count"),
        )
        .outerjoin(odds_count_subq, Fixture.id == odds_count_subq.c.fixture_id)
        .outerjoin(goalscorer_subq, Fixture.id == goalscorer_subq.c.fixture_id)
        .outerjoin(assist_subq, Fixture.id == assist_subq.c.fixture_id)
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

    # Exclure les fixtures dont un nom d'équipe est un placeholder
    # Patterns : W83, L101, 1A, 2B, R32 TBD 7, R16 TBD 1, QF TBD…
    stmt = stmt.where(
        ~Fixture.home_team.op("~*")(r"^[WL][0-9]|^[0-9][A-Z]$|^[A-Z][0-9]$|TBD"),
        ~Fixture.away_team.op("~*")(r"^[WL][0-9]|^[0-9][A-Z]$|^[A-Z][0-9]$|TBD"),
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Resolve canonical team data (name_fr + api_football_id)
    ct_ids = {
        ct_id
        for row in rows
        for ct_id in (row.Fixture.home_canonical_team_id, row.Fixture.away_canonical_team_id)
        if ct_id
    }
    canonical_data: dict[int, CanonicalTeam] = {}
    if ct_ids:
        ct_rows = (await db.execute(
            select(CanonicalTeam).where(CanonicalTeam.id.in_(ct_ids))
        )).scalars().all()
        canonical_data = {ct.id: ct for ct in ct_rows}

    def _team_name(raw: str, ct_id: int | None) -> str:
        if ct_id and ct_id in canonical_data:
            return canonical_data[ct_id].name_fr
        return raw

    def _api_football_id(ct_id: int | None) -> int | None:
        if ct_id and ct_id in canonical_data:
            return canonical_data[ct_id].api_football_id
        return None

    items = [
        FixtureOut(
            id=row.Fixture.id,
            external_id=row.Fixture.external_id,
            league=row.Fixture.league,
            season=row.Fixture.season,
            matchweek=row.Fixture.matchweek,
            home_team=_team_name(row.Fixture.home_team, row.Fixture.home_canonical_team_id),
            away_team=_team_name(row.Fixture.away_team, row.Fixture.away_canonical_team_id),
            home_team_id=row.Fixture.home_team_id,
            away_team_id=row.Fixture.away_team_id,
            home_api_football_id=_api_football_id(row.Fixture.home_canonical_team_id),
            away_api_football_id=_api_football_id(row.Fixture.away_canonical_team_id),
            kickoff_utc=str(row.Fixture.kickoff_utc),
            status=row.Fixture.status,
            home_score=row.Fixture.home_score,
            away_score=row.Fixture.away_score,
            odds_count=row.odds_count,
            goalscorer_count=row.goalscorer_count,
            assist_count=row.assist_count,
            odds=[],
        )
        for row in rows
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
        home_team_id=fixture.home_team_id,
        away_team_id=fixture.away_team_id,
        home_api_football_id=None,
        away_api_football_id=None,
        kickoff_utc=str(fixture.kickoff_utc),
        status=fixture.status,
        home_score=fixture.home_score,
        away_score=fixture.away_score,
        odds_count=0,
        goalscorer_count=0,
        assist_count=0,
        odds=[],
    )


@router.delete("/fixtures/{fixture_id}", status_code=204)
async def delete_fixture(
    fixture_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a fixture and all related records (odds, recommendations, lineups, etc.)."""
    from sqlalchemy import delete as sa_delete
    from app.models.lineups import TeamLineup
    from app.models.match_events import MatchEvent
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.odds_scrape_state import OddsScrapeState
    from app.models.player_match_minutes import PlayerMatchMinutes
    from app.models.recommendations import Recommendation
    from app.models.team_xg import TeamXgEstimate

    result = await db.execute(select(Fixture).where(Fixture.id == fixture_id))
    fixture = result.scalar_one_or_none()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    for model in (
        Recommendation, MatchOddsSnapshot, PlayerOddsSnapshot,
        MatchEvent, TeamLineup, PlayerMatchMinutes, TeamXgEstimate, OddsScrapeState,
    ):
        await db.execute(sa_delete(model).where(model.fixture_id == fixture_id))

    await db.delete(fixture)
    await db.commit()


@router.get("/fixtures/{fixture_id}/odds", response_model=list[OddsSnapshotOut])
async def get_fixture_odds(
    fixture_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get odds snapshots for a fixture."""
    result = await db.execute(
        select(PlayerOddsSnapshot)
        .where(PlayerOddsSnapshot.fixture_id == fixture_id)
        .order_by(PlayerOddsSnapshot.scraped_at.desc())
    )
    snapshots = result.scalars().all()
    return [
        OddsSnapshotOut(
            id=o.id,
            player_name=o.player_name,
            market_type=o.market_type,
            bookmaker=o.bookmaker,
            odds=o.odds,
            implied_probability=round(1.0 / o.odds, 6),
            snapshot_utc=str(o.scraped_at),
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
    snapshot = PlayerOddsSnapshot(
        fixture_id=fixture_id,
        player_name=body.player_name,
        market_type=body.market_type,
        bookmaker=body.bookmaker,
        odds=body.odds,
        scraped_at=now,
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
        implied_probability=round(1.0 / snapshot.odds, 6),
        snapshot_utc=str(snapshot.scraped_at),
    )


@router.post("/admin/sync-fixtures", response_model=dict)
async def admin_sync_fixtures(
    db: AsyncSession = Depends(get_db),
):
    """Trigger sync_fixtures_from_bzz immediately (create missing + fix placeholders)."""
    from app.ingestion.bzzoiro.sync_fixtures_from_bzz import sync_fixtures_from_bzz
    created, updated = await sync_fixtures_from_bzz(db)
    return {"created": created, "updated": updated}
