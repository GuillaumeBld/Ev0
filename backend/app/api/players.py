"""Player stats API endpoints."""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.understat_scraper import UNDERSTAT_LEAGUES, fetch_understat_league
from app.models.canonical_teams import CanonicalTeam
from app.models.players import Player, PlayerStats, Team

router = APIRouter(prefix="/players", tags=["players"])

CSV_FIELDS = [
    "name",
    "team",
    "position",
    "league",
    "games",
    "minutes",
    "goals",
    "assists",
    "xg",
    "npxg",
    "xa",
    "shots",
    "key_passes",
    "xg_per_90",
    "xa_per_90",
    "npxg_per_90",
]


@router.get("/export", summary="Export player stats as CSV")
async def export_players_csv(
    league: str | None = Query(
        None,
        description="League to export: ligue_1, premier_league, or omit for all",
    ),
) -> StreamingResponse:
    """Stream all player stats as a UTF-8 CSV file.

    Fetches live data from Understat — no database required.
    """
    leagues_to_fetch = (
        [league] if league and league in UNDERSTAT_LEAGUES else list(UNDERSTAT_LEAGUES.keys())
    )

    rows: list[dict] = []
    for lg in leagues_to_fetch:
        players, _ = await fetch_understat_league(lg)
        for p in players:
            p["league"] = lg
            rows.append(p)

    # Build CSV in memory
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)

    filename = f"ev0_players_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PlayerStatsResponse(BaseModel):
    """Player stats from a specific source."""

    source: str
    minutes: int
    goals: int
    assists: int
    xg: float
    xa: float
    npxg: float
    xg_per_90: float | None
    xa_per_90: float | None
    npxg_per_90: float | None
    shots: int
    key_passes: int
    as_of: datetime | None


class PlayerWithStats(BaseModel):
    """Player with stats from all sources."""

    id: int
    name: str
    team: str | None
    position: str | None
    league: str | None

    # Stats by source
    api_football: PlayerStatsResponse | None
    fbref: PlayerStatsResponse | None
    understat: PlayerStatsResponse | None
    fotmob: PlayerStatsResponse | None
    average: PlayerStatsResponse | None

    is_striker: bool

    # Computed EV0 values (using average or best available)
    ev0_xg_per_90: float
    ev0_xa_per_90: float
    ev0_npxg_per_90: float


class TeamResponse(BaseModel):
    """Team info."""

    id: int
    name: str
    league: str
    player_count: int


class SyncStatusResponse(BaseModel):
    """Sync status."""

    ligue_1_players: int
    ligue_1_teams: int
    premier_league_players: int
    premier_league_teams: int
    bundesliga_players: int
    bundesliga_teams: int
    la_liga_players: int
    la_liga_teams: int
    serie_a_players: int
    serie_a_teams: int
    last_sync: datetime | None


def stats_to_response(stats: PlayerStats | None) -> PlayerStatsResponse | None:
    """Convert PlayerStats to response model."""
    if not stats:
        return None
    return PlayerStatsResponse(
        source=stats.source,
        minutes=stats.minutes_played,
        goals=stats.goals,
        assists=stats.assists,
        xg=stats.xg,
        xa=stats.xa,
        npxg=stats.npxg,
        xg_per_90=stats.xg_per_90,
        xa_per_90=stats.xa_per_90,
        npxg_per_90=stats.npxg_per_90,
        shots=stats.shots,
        key_passes=stats.key_passes,
        as_of=stats.as_of_utc,
    )


@router.get("", response_model=list[PlayerWithStats])
async def list_players(
    session: AsyncSession = Depends(get_db),
    league: str | None = Query(None, description="Filter by league (ligue_1, premier_league)"),
    team: str | None = Query(None, description="Filter by team name"),
    search: str | None = Query(None, description="Search by player name"),
    min_minutes: int = Query(0, description="Minimum minutes played"),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """List players with stats from all sources."""

    # Build query
    stmt = select(Player)

    if league:
        stmt = stmt.where(Player.league == league.lower())
    if team:
        # Try to match by canonical team name first, fallback to raw text
        from app.models.canonical_teams import CanonicalTeam as _CT
        ct_row = (await session.execute(
            select(_CT).where(_CT.name_fr.ilike(f"%{team}%"))
        )).scalars().first()
        if ct_row:
            stmt = stmt.where(Player.canonical_team_id == ct_row.id)
        else:
            stmt = stmt.where(Player.team.ilike(f"%{team}%"))
    if search:
        stmt = stmt.where(Player.name.ilike(f"%{search}%"))

    stmt = stmt.order_by(Player.name).offset(offset).limit(limit)

    result = await session.execute(stmt)
    players = result.scalars().all()

    if not players:
        return []

    # Build canonical team name map (id → name_fr)
    ct_ids = {p.canonical_team_id for p in players if p.canonical_team_id}
    canonical_names: dict[int, str] = {}
    if ct_ids:
        ct_rows = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.id.in_(ct_ids))
        )).scalars().all()
        canonical_names = {ct.id: ct.name_fr for ct in ct_rows}

    # Batch-fetch all stats for these players in ONE query (avoid N+1)
    player_ids = [p.id for p in players]
    stats_stmt = select(PlayerStats).where(
        PlayerStats.player_id.in_(player_ids),
        PlayerStats.season == "2025-2026",
    )
    stats_result = await session.execute(stats_stmt)
    all_stats = stats_result.scalars().all()

    # Group stats by player_id and source
    stats_map: dict[int, dict[str, PlayerStats]] = {}
    for s in all_stats:
        stats_map.setdefault(s.player_id, {})[s.source] = s

    response = []
    for player in players:
        player_stats = stats_map.get(player.id, {})

        api_football = player_stats.get("api_football")
        fbref = player_stats.get("fbref")
        understat = player_stats.get("understat")
        fotmob = player_stats.get("fotmob")
        average = player_stats.get("average")

        # Filter by minutes if specified
        max_minutes = max(
            (api_football.minutes_played if api_football else 0),
            (fbref.minutes_played if fbref else 0),
            (understat.minutes_played if understat else 0),
            (fotmob.minutes_played if fotmob else 0),
        )
        if max_minutes < min_minutes:
            continue

        # Compute EV0 values (prefer average, fallback to available source)
        ev0_source = average or fbref or fotmob or understat or api_football

        response.append(
            {
                "id": player.id,
                "name": player.name,
                "team": canonical_names.get(player.canonical_team_id) if player.canonical_team_id else player.team,
                "position": player.position,
                "league": player.league,
                "is_striker": player.is_striker,
                "api_football": stats_to_response(api_football),
                "fbref": stats_to_response(fbref),
                "understat": stats_to_response(understat),
                "fotmob": stats_to_response(fotmob),
                "average": stats_to_response(average),
                "ev0_xg_per_90": ev0_source.xg_per_90
                if ev0_source and ev0_source.xg_per_90
                else 0.0,
                "ev0_xa_per_90": ev0_source.xa_per_90
                if ev0_source and ev0_source.xa_per_90
                else 0.0,
                "ev0_npxg_per_90": ev0_source.npxg_per_90
                if ev0_source and ev0_source.npxg_per_90
                else 0.0,
            }
        )

    return response


@router.get("/teams", response_model=list[TeamResponse])
async def list_teams(
    session: AsyncSession = Depends(get_db),
    league: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List all teams using canonical team names (deduplicated, French names)."""
    from app.models.canonical_teams import CanonicalTeam

    # Count players per canonical team
    count_subq = (
        select(Player.canonical_team_id, func.count(Player.id).label("cnt"))
        .where(Player.canonical_team_id.isnot(None))
        .group_by(Player.canonical_team_id)
        .subquery()
    )

    stmt = (
        select(CanonicalTeam, func.coalesce(count_subq.c.cnt, 0).label("player_count"))
        .outerjoin(count_subq, CanonicalTeam.id == count_subq.c.canonical_team_id)
        .where(func.coalesce(count_subq.c.cnt, 0) > 0)
        .order_by(CanonicalTeam.name_fr)
    )

    # Filter by league: keep only canonical teams that have players in the given league
    if league:
        league_ct_subq = (
            select(Player.canonical_team_id)
            .where(Player.canonical_team_id.isnot(None), Player.league == league.lower())
            .distinct()
            .subquery()
        )
        stmt = stmt.where(CanonicalTeam.id.in_(select(league_ct_subq)))

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "id": ct.id,
            "name": ct.name_fr,
            "league": league or "all",
            "player_count": player_count,
        }
        for ct, player_count in rows
    ]


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get sync status."""

    counts: dict[str, int] = {}
    for league in ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]:
        p = await session.execute(select(func.count(Player.id)).where(Player.league == league))
        t = await session.execute(select(func.count(Team.id)).where(Team.league == league))
        counts[f"{league}_players"] = p.scalar() or 0
        counts[f"{league}_teams"] = t.scalar() or 0

    last_sync = await session.execute(select(func.max(PlayerStats.as_of_utc)))

    return {**counts, "last_sync": last_sync.scalar()}


@router.get("/{player_id}", response_model=PlayerWithStats)
async def get_player(
    player_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get a single player with all stats."""
    stmt = select(Player).where(Player.id == player_id)
    result = await session.execute(stmt)
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Get stats
    stats_stmt = select(PlayerStats).where(
        PlayerStats.player_id == player.id,
        PlayerStats.season == "2025-2026",
    )
    stats_result = await session.execute(stats_stmt)
    all_stats = stats_result.scalars().all()

    stats_by_source = {s.source: s for s in all_stats}

    api_football = stats_by_source.get("api_football")
    fbref = stats_by_source.get("fbref")
    understat = stats_by_source.get("understat")
    fotmob = stats_by_source.get("fotmob")
    average = stats_by_source.get("average")
    ev0_source = average or fbref or fotmob or understat or api_football

    return {
        "id": player.id,
        "name": player.name,
        "team": player.team,
        "position": player.position,
        "league": player.league,
        "is_striker": player.is_striker,
        "api_football": stats_to_response(api_football),
        "fbref": stats_to_response(fbref),
        "understat": stats_to_response(understat),
        "fotmob": stats_to_response(fotmob),
        "average": stats_to_response(average),
        "ev0_xg_per_90": ev0_source.xg_per_90 if ev0_source and ev0_source.xg_per_90 else 0.0,
        "ev0_xa_per_90": ev0_source.xa_per_90 if ev0_source and ev0_source.xa_per_90 else 0.0,
        "ev0_npxg_per_90": ev0_source.npxg_per_90 if ev0_source and ev0_source.npxg_per_90 else 0.0,
    }


class PlayerStrikerOut(BaseModel):
    id: int
    is_striker: bool


@router.patch("/{player_id}/striker", response_model=PlayerStrikerOut)
async def toggle_striker(player_id: int, session: AsyncSession = Depends(get_db)):
    """Toggle le flag is_striker du joueur."""
    player = await session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    new_value = not player.is_striker
    player.is_striker = new_value
    await session.commit()
    return PlayerStrikerOut(id=player_id, is_striker=new_value)


@router.post("/sync")
async def trigger_sync(
    strategy: str = Query(
        "direct", description="Sync strategy: direct (Understat API), or smart (Firecrawl+LLM)"
    ),
):
    """Trigger a player stats sync.

    Strategies:
    - direct: Uses Understat API directly (no API keys needed, fast)
    - smart: Uses Firecrawl + LLM + API-Football (requires API keys)
    """
    import asyncio

    if strategy == "smart":
        from app.ingestion.smart_sync import smart_sync_all

        asyncio.create_task(smart_sync_all())
    else:
        from app.ingestion.sync_all_players import sync_all

        asyncio.create_task(sync_all())

    return {
        "status": "sync_started",
        "strategy": strategy,
        "message": f"Sync ({strategy}) started in background. Check /players/sync-status for progress.",
    }
