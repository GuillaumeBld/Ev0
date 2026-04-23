"""Player stats API endpoints — powered by Bzzoiro tables."""

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import asc, desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_API_ID_LIST, TARGET_LEAGUE_INTERNAL_ID_LIST
from app.models.bzzoiro import (
    BzzEvent,
    BzzLeague,
    BzzPlayer,
    BzzPlayerMatchStat,
    BzzPlayerSeasonStat,
    BzzTeam,
)

router = APIRouter(prefix="/players", tags=["players"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_INTERNAL_IDS = [1, 3, 4, 5, 6, 7]  # PL=1, LaLiga=3, SerieA=4, BL=5, L1=6, UCL=7

# Canonical Bzzoiro internal IDs (post-migration) used for all new data.
# Old SofaScore IDs kept for recent_matches query (old events still carry those IDs).
TARGET_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # PL=1, LL=3, SA=4, BL=5, L1=6, UCL=7
_ALL_LEAGUE_IDS = list(set(TARGET_LEAGUE_API_ID_LIST + TARGET_LEAGUE_INTERNAL_ID_LIST))

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

_SORTABLE_COLUMNS: dict[str, Any] = {
    # Identity
    "name": BzzPlayer.name,
    "team": BzzTeam.name,
    # Totals
    "matches_played": BzzPlayerSeasonStat.matches_played,
    "minutes_played": BzzPlayerSeasonStat.minutes_played,
    "goals": BzzPlayerSeasonStat.goals,
    "goal_assist": BzzPlayerSeasonStat.goal_assist,
    "total_shots": BzzPlayerSeasonStat.total_shots,
    "shots_on_target": BzzPlayerSeasonStat.shots_on_target,
    "key_pass": BzzPlayerSeasonStat.key_pass,
    "expected_goals": BzzPlayerSeasonStat.expected_goals,
    "expected_assists": BzzPlayerSeasonStat.expected_assists,
    "yellow_card": BzzPlayerSeasonStat.yellow_card,
    "red_card": BzzPlayerSeasonStat.red_card,
    "saves": BzzPlayerSeasonStat.saves,
    # Per-90
    "xg_per_90": BzzPlayerSeasonStat.xg_per_90,
    "xa_per_90": BzzPlayerSeasonStat.xa_per_90,
    "shots_per_90": BzzPlayerSeasonStat.shots_per_90,
    "shots_on_target_per_90": BzzPlayerSeasonStat.shots_on_target_per_90,
    "key_pass_per_90": BzzPlayerSeasonStat.key_pass_per_90,
    "accurate_cross_per_90": BzzPlayerSeasonStat.accurate_cross_per_90,
    "recoveries_per_90": BzzPlayerSeasonStat.recoveries_per_90,
    "tackles_per_90": BzzPlayerSeasonStat.tackles_per_90,
    "interceptions_per_90": BzzPlayerSeasonStat.interceptions_per_90,
    # Efficiency
    "avg_rating": BzzPlayerSeasonStat.avg_rating,
    "shot_accuracy": BzzPlayerSeasonStat.shot_accuracy,
    "xg_per_shot": BzzPlayerSeasonStat.xg_per_shot,
    "finishing_delta": BzzPlayerSeasonStat.finishing_delta,
    "xa_delta": BzzPlayerSeasonStat.xa_delta,
    "pass_completion": BzzPlayerSeasonStat.pass_completion,
    "long_ball_accuracy": BzzPlayerSeasonStat.long_ball_accuracy,
    "cross_accuracy": BzzPlayerSeasonStat.cross_accuracy,
    "duel_win_rate": BzzPlayerSeasonStat.duel_win_rate,
    "aerial_win_rate": BzzPlayerSeasonStat.aerial_win_rate,
    "tackle_success_rate": BzzPlayerSeasonStat.tackle_success_rate,
    "avg_minutes_per_match": BzzPlayerSeasonStat.avg_minutes_per_match,
    "starts_pct": BzzPlayerSeasonStat.starts_pct,
    # Form
    "form_xg_5": BzzPlayerSeasonStat.form_xg_5,
    "form_rating_5": BzzPlayerSeasonStat.form_rating_5,
    "form_goals_5": BzzPlayerSeasonStat.form_goals_5,
    "form_assists_5": BzzPlayerSeasonStat.form_assists_5,
    "rating_trend": BzzPlayerSeasonStat.rating_trend,
}


class PlayerSummary(BaseModel):
    """Player summary for list endpoint."""

    player_api_id: int
    name: str
    short_name: str | None
    position: str | None
    team_name: str | None
    nationality: str | None
    goals: int | None
    goal_assist: int | None
    xg_per_90: float | None
    xa_per_90: float | None
    avg_rating: float | None
    shots_on_target_per_90: float | None
    form_xg_5: float | None
    matches_played: int | None
    minutes_played: int | None
    season: str


class RecentMatch(BaseModel):
    """Single match entry in player detail — all available stats."""

    event_api_id: int
    event_date: datetime | None
    opponent: str | None
    is_home: bool | None
    minutes_played: int | None
    rating: float | None
    touches: int | None
    goals: int | None
    goal_assist: int | None
    expected_goals: float | None
    expected_assists: float | None
    total_shots: int | None
    shots_on_target: int | None
    total_pass: int | None
    accurate_pass: int | None
    key_pass: int | None
    total_long_balls: int | None
    accurate_long_balls: int | None
    total_cross: int | None
    accurate_cross: int | None
    duel_won: int | None
    duel_lost: int | None
    aerial_won: int | None
    aerial_lost: int | None
    total_tackle: int | None
    won_tackle: int | None
    total_clearance: int | None
    interception: int | None
    ball_recovery: int | None
    yellow_card: int | None
    red_card: int | None
    fouls: int | None
    was_fouled: int | None
    dispossessed: int | None
    possession_lost: int | None
    saves: int | None
    goals_conceded: int | None
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    xa_delta: float | None
    pass_completion: float | None
    long_ball_accuracy: float | None
    cross_accuracy: float | None
    duel_win_rate: float | None
    aerial_win_rate: float | None
    tackle_success_rate: float | None


class SeasonStatsOut(BaseModel):
    """All fields from bzz_player_season_stats."""

    season: str
    league_api_id: int | None
    matches_played: int | None
    minutes_played: int | None
    starts: int | None
    goals: int | None
    goal_assist: int | None
    total_shots: int | None
    shots_on_target: int | None
    key_pass: int | None
    expected_goals: float | None
    expected_assists: float | None
    xg_per_90: float | None
    xa_per_90: float | None
    shots_per_90: float | None
    shots_on_target_per_90: float | None
    key_pass_per_90: float | None
    avg_rating: float | None
    form_xg_5: float | None
    form_rating_5: float | None
    form_goals_5: int | None
    form_assists_5: int | None
    rating_trend: float | None
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    xa_delta: float | None
    yellow_card: int | None
    red_card: int | None
    saves: int | None
    accurate_cross_per_90: float | None
    recoveries_per_90: float | None
    tackles_per_90: float | None
    interceptions_per_90: float | None
    long_ball_accuracy: float | None
    cross_accuracy: float | None
    pass_completion: float | None
    duel_win_rate: float | None
    aerial_win_rate: float | None
    tackle_success_rate: float | None
    avg_minutes_per_match: float | None
    starts_pct: float | None


class PlayerDetail(BaseModel):
    """Full player detail."""

    player_api_id: int
    name: str
    short_name: str | None
    position: str | None
    date_of_birth: Any | None
    nationality: str | None
    height: int | None
    jersey_number: int | None
    market_value: int | None
    team_name: str | None
    season_stats: SeasonStatsOut | None
    recent_matches: list[RecentMatch]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_team_dominant_leagues(
    session: AsyncSession,
    season: str = "2025-2026",
) -> dict[str, int]:
    """Return {team_name: dominant_league_api_id} — league with most stats rows per team name."""
    stmt = text("""
        WITH per_name_league AS (
            SELECT bp.current_team_name, bpss.league_api_id, COUNT(*) AS cnt
            FROM bzz_players bp
            JOIN bzz_player_season_stats bpss ON bpss.player_api_id = bp.api_id
            WHERE bpss.season = :season AND bp.current_team_name IS NOT NULL
            GROUP BY bp.current_team_name, bpss.league_api_id
        ),
        ranked AS (
            SELECT current_team_name, league_api_id,
                   ROW_NUMBER() OVER (PARTITION BY current_team_name ORDER BY cnt DESC) AS rn
            FROM per_name_league
        )
        SELECT current_team_name, league_api_id FROM ranked WHERE rn = 1
    """)
    result = await session.execute(stmt, {"season": season})
    return {row[0]: row[1] for row in result.all()}


# ---------------------------------------------------------------------------
# GET /players/leagues  — championnats disponibles
# ---------------------------------------------------------------------------


@router.get("/leagues", response_model=list[dict])
async def list_player_leagues(
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the 6 target leagues (hardcoded IDs)."""
    result = await session.execute(
        select(BzzLeague.api_id, BzzLeague.name)
        .where(BzzLeague.api_id.in_(TARGET_LEAGUE_IDS))
        .order_by(BzzLeague.name)
    )
    return [{"api_id": row[0], "name": row[1]} for row in result.all()]


# ---------------------------------------------------------------------------
# GET /players/teams  — équipes du championnat (ou toutes)
# ---------------------------------------------------------------------------


@router.get("/teams", response_model=list[dict])
async def list_player_teams(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(None, description="Filter by league api_id; -1 = Autres"),
) -> list[dict[str, Any]]:
    """Return teams deduplicated by name, filtered by dominant league.

    league_api_id=None → all teams; league_api_id=-1 → non-Big5/UCL teams; 1-7 → specific league.
    """
    dominant = await _get_team_dominant_leagues(session)

    if league_api_id is not None:
        if league_api_id == -1:
            valid_names: set[str] | None = {n for n, lg in dominant.items() if lg not in TARGET_INTERNAL_IDS}
        else:
            valid_names = {n for n, lg in dominant.items() if lg == league_api_id}
    else:
        valid_names = None

    stmt = text("""
        WITH team_counts AS (
            SELECT bp.current_team_api_id, bp.current_team_name, COUNT(*) AS cnt
            FROM bzz_players bp
            JOIN bzz_player_season_stats bpss ON bpss.player_api_id = bp.api_id
            WHERE bpss.season = '2025-2026'
              AND bp.current_team_api_id IS NOT NULL
              AND bp.current_team_name IS NOT NULL
            GROUP BY bp.current_team_api_id, bp.current_team_name
        ),
        ranked AS (
            SELECT current_team_api_id, current_team_name,
                   ROW_NUMBER() OVER (PARTITION BY current_team_name ORDER BY cnt DESC) AS rn
            FROM team_counts
        )
        SELECT current_team_api_id, current_team_name FROM ranked WHERE rn = 1
        ORDER BY current_team_name
    """)
    result = await session.execute(stmt)
    rows = result.all()

    output = []
    for api_id, name in rows:
        if valid_names is not None and name not in valid_names:
            continue
        output.append({"api_id": api_id, "name": name})
    return output


# ---------------------------------------------------------------------------
# Legacy export endpoint — kept for backwards compatibility
# ---------------------------------------------------------------------------


@router.get("/export", summary="Export player stats as CSV")
async def export_players_csv(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(
        None,
        description="League API ID to export, or omit for all",
    ),
    season: str = Query("2025-2026"),
) -> StreamingResponse:
    """Stream all player stats as a UTF-8 CSV file from Bzzoiro."""
    stmt = (
        select(
            BzzPlayer.name,
            BzzTeam.name.label("team"),
            BzzPlayer.position,
            BzzLeague.name.label("league"),
            BzzPlayerSeasonStat.matches_played.label("games"),
            BzzPlayerSeasonStat.minutes_played.label("minutes"),
            BzzPlayerSeasonStat.goals,
            BzzPlayerSeasonStat.goal_assist.label("assists"),
            BzzPlayerSeasonStat.expected_goals.label("xg"),
            BzzPlayerSeasonStat.expected_goals.label("npxg"),
            BzzPlayerSeasonStat.expected_assists.label("xa"),
            BzzPlayerSeasonStat.total_shots.label("shots"),
            BzzPlayerSeasonStat.key_pass.label("key_passes"),
            BzzPlayerSeasonStat.xg_per_90,
            BzzPlayerSeasonStat.xa_per_90,
            BzzPlayerSeasonStat.xg_per_90.label("npxg_per_90"),
        )
        .select_from(BzzPlayerSeasonStat)
        .join(BzzPlayer, BzzPlayerSeasonStat.player_api_id == BzzPlayer.api_id)
        .outerjoin(BzzTeam, BzzPlayer.current_team_api_id == BzzTeam.api_id)
        .outerjoin(BzzLeague, BzzPlayerSeasonStat.league_api_id == BzzLeague.api_id)
        .where(BzzPlayerSeasonStat.season == season)
    )

    if league_api_id:
        stmt = stmt.where(BzzPlayerSeasonStat.league_api_id == league_api_id)

    result = await session.execute(stmt)
    rows = [dict(zip(CSV_FIELDS, r)) for r in result.all()]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)

    filename = f"ev0_players_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /players/  — list
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PlayerSummary])
async def list_players(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(None, description="Filter by league API id"),
    team_api_id: int | None = Query(None, description="Filter by team API id"),
    position: str | None = Query(None, description="Filter by position: G/D/M/F"),
    min_minutes: int = Query(0, description="Minimum minutes played"),
    season: str = Query("2025-2026"),
    sort_by: str = Query("xg_per_90"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """List players with season stats from Bzzoiro tables."""

    sort_col = _SORTABLE_COLUMNS.get(sort_by, BzzPlayerSeasonStat.xg_per_90)
    order_fn = desc if sort_order.lower() == "desc" else asc

    stmt = (
        select(
            BzzPlayer,
            BzzPlayerSeasonStat,
        )
        .join(BzzPlayerSeasonStat, BzzPlayerSeasonStat.player_api_id == BzzPlayer.api_id)
        .where(BzzPlayerSeasonStat.season == season)
    )

    if league_api_id is not None:
        dominant = await _get_team_dominant_leagues(session, season)
        if league_api_id == -1:
            team_names = [n for n, lg in dominant.items() if lg not in TARGET_INTERNAL_IDS]
        else:
            team_names = [n for n, lg in dominant.items() if lg == league_api_id]
        if not team_names:
            return []
        stmt = stmt.where(BzzPlayer.current_team_name.in_(team_names))
    if team_api_id is not None:
        stmt = stmt.where(BzzPlayer.current_team_api_id == team_api_id)
    if position is not None:
        stmt = stmt.where(BzzPlayer.position == position.upper())
    if min_minutes > 0:
        stmt = stmt.where(
            BzzPlayerSeasonStat.minutes_played >= min_minutes,
        )

    stmt = stmt.order_by(order_fn(sort_col).nulls_last()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "player_api_id": player.api_id,
            "name": player.name,
            "short_name": player.short_name,
            "position": player.position,
            "team_name": player.current_team_name,
            "nationality": player.nationality,
            "goals": stats.goals,
            "goal_assist": stats.goal_assist,
            "xg_per_90": stats.xg_per_90,
            "xa_per_90": stats.xa_per_90,
            "avg_rating": stats.avg_rating,
            "shots_on_target_per_90": stats.shots_on_target_per_90,
            "form_xg_5": stats.form_xg_5,
            "matches_played": stats.matches_played,
            "minutes_played": stats.minutes_played,
            "season": stats.season,
        }
        for player, stats in rows
    ]


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}  — detail
# ---------------------------------------------------------------------------


@router.get("/{player_api_id}", response_model=PlayerDetail)
async def get_player(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
    season: str = Query("2025-2026"),
) -> dict[str, Any]:
    """Get a single player with season stats and recent matches."""

    # 1. Fetch player
    player_result = await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id == player_api_id)
    )
    player = player_result.scalars().first()
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    # 2. Season stats
    stats_result = await session.execute(
        select(BzzPlayerSeasonStat).where(
            BzzPlayerSeasonStat.player_api_id == player_api_id,
            BzzPlayerSeasonStat.season == season,
        )
    )
    season_stat = stats_result.scalars().first()

    season_stats_out: SeasonStatsOut | None = None
    if season_stat is not None:
        season_stats_out = SeasonStatsOut(
            season=season_stat.season,
            league_api_id=season_stat.league_api_id,
            matches_played=season_stat.matches_played,
            minutes_played=season_stat.minutes_played,
            starts=season_stat.starts,
            goals=season_stat.goals,
            goal_assist=season_stat.goal_assist,
            total_shots=season_stat.total_shots,
            shots_on_target=season_stat.shots_on_target,
            key_pass=season_stat.key_pass,
            expected_goals=season_stat.expected_goals,
            expected_assists=season_stat.expected_assists,
            xg_per_90=season_stat.xg_per_90,
            xa_per_90=season_stat.xa_per_90,
            shots_per_90=season_stat.shots_per_90,
            shots_on_target_per_90=season_stat.shots_on_target_per_90,
            key_pass_per_90=season_stat.key_pass_per_90,
            avg_rating=season_stat.avg_rating,
            form_xg_5=season_stat.form_xg_5,
            form_rating_5=season_stat.form_rating_5,
            form_goals_5=season_stat.form_goals_5,
            form_assists_5=season_stat.form_assists_5,
            rating_trend=season_stat.rating_trend,
            shot_accuracy=season_stat.shot_accuracy,
            xg_per_shot=season_stat.xg_per_shot,
            finishing_delta=season_stat.finishing_delta,
            xa_delta=season_stat.xa_delta,
            yellow_card=season_stat.yellow_card,
            red_card=season_stat.red_card,
            saves=season_stat.saves,
            accurate_cross_per_90=season_stat.accurate_cross_per_90,
            recoveries_per_90=season_stat.recoveries_per_90,
            tackles_per_90=season_stat.tackles_per_90,
            interceptions_per_90=season_stat.interceptions_per_90,
            long_ball_accuracy=season_stat.long_ball_accuracy,
            cross_accuracy=season_stat.cross_accuracy,
            pass_completion=season_stat.pass_completion,
            duel_win_rate=season_stat.duel_win_rate,
            aerial_win_rate=season_stat.aerial_win_rate,
            tackle_success_rate=season_stat.tackle_success_rate,
            avg_minutes_per_match=season_stat.avg_minutes_per_match,
            starts_pct=season_stat.starts_pct,
        )

    # 3. Recent matches (all available) — join to BzzEvent for date and teams
    home_team_alias = BzzTeam.__table__.alias("ht")
    away_team_alias = BzzTeam.__table__.alias("at_")

    recent_result = await session.execute(
        select(
            BzzPlayerMatchStat,
            BzzEvent.event_date,
            BzzEvent.home_team_api_id,
            BzzEvent.away_team_api_id,
            home_team_alias.c.name.label("home_team_name"),
            away_team_alias.c.name.label("away_team_name"),
        )
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .outerjoin(home_team_alias, home_team_alias.c.api_id == BzzEvent.home_team_api_id)
        .outerjoin(away_team_alias, away_team_alias.c.api_id == BzzEvent.away_team_api_id)
        .where(
            BzzPlayerMatchStat.player_api_id == player_api_id,
            BzzEvent.league_api_id.in_(_ALL_LEAGUE_IDS),
        )
        .order_by(desc(BzzEvent.event_date))
    )
    recent_rows = recent_result.all()

    recent_matches: list[RecentMatch] = []
    for ms, event_date, home_api_id, away_api_id, home_name, away_name in recent_rows:
        # Use team_api_id from the stat row; fall back to the player's current team
        player_team_id = ms.team_api_id or player.current_team_api_id
        if player_team_id is not None:
            if player_team_id == home_api_id:
                is_home: bool | None = True
            elif player_team_id == away_api_id:
                is_home = False
            else:
                is_home = ms.is_home
        else:
            is_home = ms.is_home

        # Only show opponent when we can determine side; avoids showing player's own team
        if is_home is True:
            opponent: str | None = away_name
        elif is_home is False:
            opponent = home_name
        else:
            opponent = None

        recent_matches.append(
            RecentMatch(
                event_api_id=ms.event_api_id,
                event_date=event_date,
                opponent=opponent,
                is_home=is_home,
                minutes_played=ms.minutes_played,
                rating=ms.rating,
                touches=ms.touches,
                goals=ms.goals,
                goal_assist=ms.goal_assist,
                expected_goals=ms.expected_goals,
                expected_assists=ms.expected_assists,
                total_shots=ms.total_shots,
                shots_on_target=ms.shots_on_target,
                total_pass=ms.total_pass,
                accurate_pass=ms.accurate_pass,
                key_pass=ms.key_pass,
                total_long_balls=ms.total_long_balls,
                accurate_long_balls=ms.accurate_long_balls,
                total_cross=ms.total_cross,
                accurate_cross=ms.accurate_cross,
                duel_won=ms.duel_won,
                duel_lost=ms.duel_lost,
                aerial_won=ms.aerial_won,
                aerial_lost=ms.aerial_lost,
                total_tackle=ms.total_tackle,
                won_tackle=ms.won_tackle,
                total_clearance=ms.total_clearance,
                interception=ms.interception,
                ball_recovery=ms.ball_recovery,
                yellow_card=ms.yellow_card,
                red_card=ms.red_card,
                fouls=ms.fouls,
                was_fouled=ms.was_fouled,
                dispossessed=ms.dispossessed,
                possession_lost=ms.possession_lost,
                saves=ms.saves,
                goals_conceded=ms.goals_conceded,
                shot_accuracy=ms.shot_accuracy,
                xg_per_shot=ms.xg_per_shot,
                finishing_delta=ms.finishing_delta,
                xa_delta=ms.xa_delta,
                pass_completion=ms.pass_completion,
                long_ball_accuracy=ms.long_ball_accuracy,
                cross_accuracy=ms.cross_accuracy,
                duel_win_rate=ms.duel_win_rate,
                aerial_win_rate=ms.aerial_win_rate,
                tackle_success_rate=ms.tackle_success_rate,
            )
        )

    return {
        "player_api_id": player.api_id,
        "name": player.name,
        "short_name": player.short_name,
        "position": player.position,
        "date_of_birth": player.date_of_birth,
        "nationality": player.nationality,
        "height": player.height,
        "jersey_number": player.jersey_number,
        "market_value": player.market_value,
        "team_name": player.current_team_name,
        "season_stats": season_stats_out,
        "recent_matches": recent_matches,
    }
