"""Player stats API endpoints."""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.understat_scraper import fetch_understat_league, UNDERSTAT_LEAGUES
from app.models.players import Player, PlayerStats, Team

router = APIRouter(prefix="/players", tags=["players"])

CSV_FIELDS = [
    "name", "team", "position", "league",
    "games", "minutes", "goals", "assists",
    "xg", "npxg", "xa",
    "shots", "key_passes",
    "xg_per_90", "xa_per_90", "npxg_per_90",
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
        [league] if league and league in UNDERSTAT_LEAGUES
        else list(UNDERSTAT_LEAGUES.keys())
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
    average: PlayerStatsResponse | None
    
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


@router.get("/", response_model=list[PlayerWithStats])
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
        stmt = stmt.where(Player.team.ilike(f"%{team}%"))
    if search:
        stmt = stmt.where(Player.name.ilike(f"%{search}%"))
    
    stmt = stmt.order_by(Player.name).offset(offset).limit(limit)
    
    result = await session.execute(stmt)
    players = result.scalars().all()
    
    response = []
    for player in players:
        # Get stats for each source
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
        average = stats_by_source.get("average")
        
        # Filter by minutes if specified
        max_minutes = max(
            (api_football.minutes_played if api_football else 0),
            (fbref.minutes_played if fbref else 0),
            (understat.minutes_played if understat else 0),
        )
        if max_minutes < min_minutes:
            continue
        
        # Compute EV0 values (prefer average, fallback to available source)
        ev0_source = average or fbref or understat or api_football
        
        response.append({
            "id": player.id,
            "name": player.name,
            "team": player.team,
            "position": player.position,
            "league": player.league,
            "api_football": stats_to_response(api_football),
            "fbref": stats_to_response(fbref),
            "understat": stats_to_response(understat),
            "average": stats_to_response(average),
            "ev0_xg_per_90": ev0_source.xg_per_90 if ev0_source and ev0_source.xg_per_90 else 0.0,
            "ev0_xa_per_90": ev0_source.xa_per_90 if ev0_source and ev0_source.xa_per_90 else 0.0,
            "ev0_npxg_per_90": ev0_source.npxg_per_90 if ev0_source and ev0_source.npxg_per_90 else 0.0,
        })
    
    return response


@router.get("/teams", response_model=list[TeamResponse])
async def list_teams(
    session: AsyncSession = Depends(get_db),
    league: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List all teams."""
    stmt = select(Team)
    if league:
        stmt = stmt.where(Team.league == league.lower())
    stmt = stmt.order_by(Team.name)
    
    result = await session.execute(stmt)
    teams = result.scalars().all()
    
    response = []
    for team in teams:
        # Count players
        count_stmt = select(func.count(Player.id)).where(
            Player.team.ilike(f"%{team.name}%"),
            Player.league == team.league,
        )
        count_result = await session.execute(count_stmt)
        player_count = count_result.scalar() or 0
        
        response.append({
            "id": team.id,
            "name": team.name,
            "league": team.league,
            "player_count": player_count,
        })
    
    return response


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get sync status."""
    
    # Count L1
    l1_players = await session.execute(
        select(func.count(Player.id)).where(Player.league == "ligue_1")
    )
    l1_teams = await session.execute(
        select(func.count(Team.id)).where(Team.league == "ligue_1")
    )
    
    # Count PL
    pl_players = await session.execute(
        select(func.count(Player.id)).where(Player.league == "premier_league")
    )
    pl_teams = await session.execute(
        select(func.count(Team.id)).where(Team.league == "premier_league")
    )
    
    # Get last sync time
    last_sync_stmt = select(func.max(PlayerStats.as_of_utc))
    last_sync = await session.execute(last_sync_stmt)
    
    return {
        "ligue_1_players": l1_players.scalar() or 0,
        "ligue_1_teams": l1_teams.scalar() or 0,
        "premier_league_players": pl_players.scalar() or 0,
        "premier_league_teams": pl_teams.scalar() or 0,
        "last_sync": last_sync.scalar(),
    }


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
    average = stats_by_source.get("average")
    ev0_source = average or fbref or understat or api_football
    
    return {
        "id": player.id,
        "name": player.name,
        "team": player.team,
        "position": player.position,
        "league": player.league,
        "api_football": stats_to_response(api_football),
        "fbref": stats_to_response(fbref),
        "understat": stats_to_response(understat),
        "average": stats_to_response(average),
        "ev0_xg_per_90": ev0_source.xg_per_90 if ev0_source and ev0_source.xg_per_90 else 0.0,
        "ev0_xa_per_90": ev0_source.xa_per_90 if ev0_source and ev0_source.xa_per_90 else 0.0,
        "ev0_npxg_per_90": ev0_source.npxg_per_90 if ev0_source and ev0_source.npxg_per_90 else 0.0,
    }


@router.post("/sync")
async def trigger_sync(
    strategy: str = Query("smart", description="Sync strategy: smart, main, or fallback"),
):
    """Trigger a player stats sync (runs in background).

    Strategies:
    - smart: Auto-selects best available strategy
    - main: Force Firecrawl + LLM + API-Football
    - fallback: Force Firecrawl + LLM only
    """
    import asyncio
    from app.ingestion.smart_sync import smart_sync_all

    # Run in background
    asyncio.create_task(smart_sync_all())

    return {
        "status": "sync_started",
        "strategy": strategy,
        "message": "Smart sync started in background. Check /players/sync-status for progress.",
    }


@router.post("/sync-understat", summary="Sync players from Understat only (no API keys needed)")
async def trigger_understat_sync() -> dict:
    """Fetch all players from Understat AJAX API and store to database.

    Uses the working AJAX endpoint — no Firecrawl/LLM/API-Football keys needed.
    Runs in background (~30s). Check /players/sync-status for progress.
    """
    import asyncio
    from app.ingestion.sync_all_players import sync_all

    asyncio.create_task(sync_all())

    return {
        "status": "sync_started",
        "message": "Understat sync started. Check /players/sync-status for progress.",
    }
