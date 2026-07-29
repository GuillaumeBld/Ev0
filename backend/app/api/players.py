"""Player stats API endpoints — powered by Bzzoiro tables."""

import csv
import io
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.bzzoiro.constants import (
    TARGET_LEAGUE_API_ID_LIST,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.models.bzzoiro import (
    BzzEvent,
    BzzLeague,
    BzzPlayer,
    BzzPlayerMatchStat,
    BzzPlayerSeasonStat,
    BzzTeam,
)
from app.models.player_career import PlayerCareerSeason
from app.pricing.career_blend import blended_rhythm
from app.services.season_service import current_season

router = APIRouter(prefix="/players", tags=["players"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_INTERNAL_IDS = [1, 3, 4, 5, 6, 7]  # PL=1, LaLiga=3, SerieA=4, BL=5, L1=6, UCL=7

# Canonical Bzzoiro internal IDs (post-migration) used for all new data.
# Old SofaScore IDs kept for recent_matches query (old events still carry those IDs).
TARGET_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # PL=1, LL=3, SA=4, BL=5, L1=6, UCL=7
_ALL_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # canonical Bzzoiro IDs only: [1,3,4,5,6,7]

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
    "xa",
    "shots",
    "key_passes",
    "xg_per_90",
    "xa_per_90",
]

# Valid sort_by keys → result dict field name
_SORT_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "team": "team_name",
    "matches_played": "matches_played",
    "minutes_played": "minutes_played",
    "goals": "goals",
    "goal_assist": "goal_assist",
    "total_shots": "total_shots",
    "shots_on_target": "shots_on_target",
    "key_pass": "key_pass",
    "expected_goals": "expected_goals",
    "expected_assists": "expected_assists",
    "yellow_card": "yellow_card",
    "red_card": "red_card",
    "saves": "saves",
    "xg_per_90": "xg_per_90",
    "xa_per_90": "xa_per_90",
    "shots_per_90": "shots_per_90",
    "shots_on_target_per_90": "shots_on_target_per_90",
    "key_pass_per_90": "key_pass_per_90",
    "accurate_cross_per_90": "accurate_cross_per_90",
    "recoveries_per_90": "recoveries_per_90",
    "tackles_per_90": "tackles_per_90",
    "interceptions_per_90": "interceptions_per_90",
    "avg_rating": "avg_rating",
    "shot_accuracy": "shot_accuracy",
    "xg_per_shot": "xg_per_shot",
    "finishing_delta": "finishing_delta",
    "xa_delta": "xa_delta",
    "pass_completion": "pass_completion",
    "long_ball_accuracy": "long_ball_accuracy",
    "cross_accuracy": "cross_accuracy",
    "duel_win_rate": "duel_win_rate",
    "aerial_win_rate": "aerial_win_rate",
    "tackle_success_rate": "tackle_success_rate",
    "avg_minutes_per_match": "avg_minutes_per_match",
    "starts_pct": "starts_pct",
    "form_xg_5": "form_xg_5",
    "form_rating_5": "form_rating_5",
    "form_goals_5": "form_goals_5",
    "form_assists_5": "form_assists_5",
    "rating_trend": "rating_trend",
}

_STRING_SORT_FIELDS = {"name", "team_name"}


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
    """Aggregated season stats across all competitions."""

    season: str
    league_api_id: int | None  # None when stats span multiple leagues
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


class CareerCompetitionOut(BaseModel):
    """One (season, competition) row from player_career_seasons."""

    competition: str | None
    competition_code: str | None
    appearances: int
    goals: int
    assists: int
    minutes: int


class CareerSeasonOut(BaseModel):
    """One season of career, aggregated across competitions, with per-competition detail."""

    season: str
    season_start_year: int | None
    appearances: int
    goals: int
    assists: int
    minutes: int
    competitions: list[CareerCompetitionOut]


class BlendedRhythmOut(BaseModel):
    """Beta blended reference rhythm (Bzzoiro xG/xA + career goals/assists)."""

    goal_rate_per_90: float
    assist_rate_per_90: float
    seasons_used: int
    has_career: bool


class PlayerCareerIdentityOut(BaseModel):
    """Minimal player identity for the career endpoint."""

    player_api_id: int
    name: str


class PlayerCareerOut(BaseModel):
    """Response of GET /players/{player_api_id}/career."""

    player: PlayerCareerIdentityOut
    career: list[CareerSeasonOut]
    blended_rhythm: BlendedRhythmOut | None


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

_MIN_PLAYERS_FOR_TARGET_LEAGUE = 10


def _safe_div(num: Any, den: Any) -> float | None:
    if den is None or den == 0:
        return None
    if num is None:
        return None
    return num / den


def _merge_season_stats(rows: list[Any], season: str) -> dict[str, Any] | None:
    """Merge per-league BzzPlayerSeasonStat rows into a single combined season dict.

    Totals are summed across all leagues. Per-90 and efficiency ratios are
    recalculated from the aggregated totals so they remain mathematically
    consistent. Rating is weighted by matches played. Form stats come from
    the league with the most matches (best proxy for current form).
    """
    if not rows:
        return None

    def _sum(attr: str) -> int | float | None:
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return sum(vals) if vals else None

    # Integer totals
    matches_played = _sum("matches_played")
    minutes_played = _sum("minutes_played")
    starts = _sum("starts")
    goals = _sum("goals")
    goal_assist = _sum("goal_assist")
    expected_goals = _sum("expected_goals")
    expected_assists = _sum("expected_assists")
    total_shots = _sum("total_shots")
    shots_on_target = _sum("shots_on_target")
    key_pass = _sum("key_pass")
    total_cross = _sum("total_cross")
    accurate_cross = _sum("accurate_cross")
    total_pass = _sum("total_pass")
    accurate_pass = _sum("accurate_pass")
    total_long_balls = _sum("total_long_balls")
    accurate_long_balls = _sum("accurate_long_balls")
    duel_won = _sum("duel_won")
    duel_lost = _sum("duel_lost")
    aerial_won = _sum("aerial_won")
    aerial_lost = _sum("aerial_lost")
    total_tackle = _sum("total_tackle")
    won_tackle = _sum("won_tackle")
    interception = _sum("interception")
    ball_recovery = _sum("ball_recovery")
    yellow_card = _sum("yellow_card")
    red_card = _sum("red_card")
    saves = _sum("saves")

    # Per-90 — recalculated from aggregated totals
    mins_per_90 = minutes_played / 90 if minutes_played else None
    xg_per_90 = _safe_div(expected_goals, mins_per_90)
    xa_per_90 = _safe_div(expected_assists, mins_per_90)
    shots_per_90 = _safe_div(total_shots, mins_per_90)
    shots_on_target_per_90 = _safe_div(shots_on_target, mins_per_90)
    key_pass_per_90 = _safe_div(key_pass, mins_per_90)
    accurate_cross_per_90 = _safe_div(accurate_cross, mins_per_90)
    recoveries_per_90 = _safe_div(ball_recovery, mins_per_90)
    tackles_per_90 = _safe_div(total_tackle, mins_per_90)
    interceptions_per_90 = _safe_div(interception, mins_per_90)

    # Efficiency ratios — recalculated from aggregated totals
    duel_sum = ((duel_won or 0) + (duel_lost or 0)) if (duel_won is not None or duel_lost is not None) else None
    aerial_sum = ((aerial_won or 0) + (aerial_lost or 0)) if (aerial_won is not None or aerial_lost is not None) else None

    shot_accuracy = _safe_div(shots_on_target, total_shots)
    xg_per_shot = _safe_div(expected_goals, total_shots)
    finishing_delta = (goals - expected_goals) if goals is not None and expected_goals is not None else None
    xa_delta = (goal_assist - expected_assists) if goal_assist is not None and expected_assists is not None else None
    pass_completion = _safe_div(accurate_pass, total_pass)
    long_ball_accuracy = _safe_div(accurate_long_balls, total_long_balls)
    cross_accuracy = _safe_div(accurate_cross, total_cross)
    duel_win_rate = _safe_div(duel_won, duel_sum)
    aerial_win_rate = _safe_div(aerial_won, aerial_sum)
    tackle_success_rate = _safe_div(won_tackle, total_tackle)

    # Profile
    avg_minutes_per_match = _safe_div(minutes_played, matches_played)
    starts_pct = _safe_div(starts, matches_played)

    # avg_rating: weighted average by matches played per league row
    rating_pairs = [
        (r.avg_rating, r.matches_played or 0)
        for r in rows
        if r.avg_rating is not None and r.matches_played
    ]
    if rating_pairs:
        total_weighted = sum(rating * mp for rating, mp in rating_pairs)
        total_mp_w = sum(mp for _, mp in rating_pairs)
        avg_rating: float | None = total_weighted / total_mp_w if total_mp_w else None
    else:
        avg_rating = None

    # Form: use the row with the most matches as the best proxy for current form
    dominant_row = max(rows, key=lambda r: r.matches_played or 0)
    form_xg_5 = dominant_row.form_xg_5
    form_rating_5 = dominant_row.form_rating_5
    form_goals_5 = dominant_row.form_goals_5
    form_assists_5 = dominant_row.form_assists_5
    rating_trend = dominant_row.rating_trend

    # league_api_id: single value when player has stats in only one competition
    league_api_id: int | None = rows[0].league_api_id if len(rows) == 1 else None

    return {
        "season": season,
        "league_api_id": league_api_id,
        "matches_played": matches_played,
        "minutes_played": minutes_played,
        "starts": starts,
        "goals": goals,
        "goal_assist": goal_assist,
        "expected_goals": expected_goals,
        "expected_assists": expected_assists,
        "total_shots": total_shots,
        "shots_on_target": shots_on_target,
        "key_pass": key_pass,
        "total_cross": total_cross,
        "accurate_cross": accurate_cross,
        "total_pass": total_pass,
        "accurate_pass": accurate_pass,
        "total_long_balls": total_long_balls,
        "accurate_long_balls": accurate_long_balls,
        "duel_won": duel_won,
        "duel_lost": duel_lost,
        "aerial_won": aerial_won,
        "aerial_lost": aerial_lost,
        "total_tackle": total_tackle,
        "won_tackle": won_tackle,
        "interception": interception,
        "ball_recovery": ball_recovery,
        "yellow_card": yellow_card,
        "red_card": red_card,
        "saves": saves,
        "xg_per_90": xg_per_90,
        "xa_per_90": xa_per_90,
        "shots_per_90": shots_per_90,
        "shots_on_target_per_90": shots_on_target_per_90,
        "key_pass_per_90": key_pass_per_90,
        "accurate_cross_per_90": accurate_cross_per_90,
        "recoveries_per_90": recoveries_per_90,
        "tackles_per_90": tackles_per_90,
        "interceptions_per_90": interceptions_per_90,
        "shot_accuracy": shot_accuracy,
        "xg_per_shot": xg_per_shot,
        "finishing_delta": finishing_delta,
        "xa_delta": xa_delta,
        "pass_completion": pass_completion,
        "long_ball_accuracy": long_ball_accuracy,
        "cross_accuracy": cross_accuracy,
        "duel_win_rate": duel_win_rate,
        "aerial_win_rate": aerial_win_rate,
        "tackle_success_rate": tackle_success_rate,
        "avg_rating": avg_rating,
        "avg_minutes_per_match": avg_minutes_per_match,
        "starts_pct": starts_pct,
        "form_xg_5": form_xg_5,
        "form_rating_5": form_rating_5,
        "form_goals_5": form_goals_5,
        "form_assists_5": form_assists_5,
        "rating_trend": rating_trend,
    }


def _compute_form_from_matches(recent_matches: list[RecentMatch], overall_avg_rating: float | None) -> dict[str, Any]:
    """Derive form stats from the 5 most recent matches (already sorted desc by date)."""
    last5 = recent_matches[:5]

    xg_vals = [m.expected_goals for m in last5 if m.expected_goals is not None]
    rating_vals = [m.rating for m in last5 if m.rating is not None]
    goals_vals = [m.goals for m in last5 if m.goals is not None]
    assist_vals = [m.goal_assist for m in last5 if m.goal_assist is not None]

    form_xg_5: float | None = sum(xg_vals) if xg_vals else None
    form_rating_5: float | None = sum(rating_vals) / len(rating_vals) if rating_vals else None
    form_goals_5: int | None = sum(goals_vals) if goals_vals else None
    form_assists_5: int | None = sum(assist_vals) if assist_vals else None

    rating_trend: float | None = None
    if form_rating_5 is not None and overall_avg_rating is not None:
        rating_trend = form_rating_5 - overall_avg_rating

    return {
        "form_xg_5": form_xg_5,
        "form_rating_5": form_rating_5,
        "form_goals_5": form_goals_5,
        "form_assists_5": form_assists_5,
        "rating_trend": rating_trend,
    }


def _python_sort(results: list[dict[str, Any]], sort_by: str, sort_order: str) -> None:
    """Sort results in-place by sort_by field. None values always go to the end."""
    field = _SORT_FIELD_MAP.get(sort_by, "xg_per_90")
    reverse = sort_order.lower() == "desc"

    if field in _STRING_SORT_FIELDS:
        results.sort(key=lambda r: (r.get(field) or "").lower(), reverse=reverse)
    else:
        # Tuple sort: (is_none, value) — None always last regardless of direction
        if reverse:
            results.sort(key=lambda r: (r.get(field) is None, -(r.get(field) or 0)))
        else:
            results.sort(key=lambda r: (r.get(field) is None, r.get(field) or 0))


async def _get_team_dominant_leagues(
    session: AsyncSession,
    season: str | None = None,
) -> dict[str, int]:
    """Return {team_name: effective_league_api_id}.

    Effective league = dominant league if it is a target league with >= MIN_PLAYERS_FOR_TARGET_LEAGUE
    stats rows, else -1 (Autres). This prevents 1-2-row data contamination from miscategorizing
    non-Big5/UCL teams into target leagues.
    """
    if season is None:
        season = await current_season(session)
    stmt = text("""
        WITH per_name_league AS (
            SELECT COALESCE(bp.loan_team_name, bp.current_team_name) AS eff_team_name,
                   bpss.league_api_id, COUNT(*) AS cnt
            FROM bzz_players bp
            JOIN bzz_player_season_stats bpss ON bpss.player_api_id = bp.api_id
            WHERE bpss.season = :season
              AND COALESCE(bp.loan_team_name, bp.current_team_name) IS NOT NULL
            GROUP BY eff_team_name, bpss.league_api_id
        ),
        -- Best domestic Big5 league per team (excluding UCL so UCL rows don't shadow domestic)
        domestic_ranked AS (
            SELECT eff_team_name, league_api_id, cnt,
                   ROW_NUMBER() OVER (PARTITION BY eff_team_name ORDER BY cnt DESC) AS rn
            FROM per_name_league WHERE league_api_id IN (1,3,4,5,6)
        ),
        domestic_best AS (
            SELECT eff_team_name, league_api_id AS domestic_league, cnt AS domestic_cnt
            FROM domestic_ranked WHERE rn = 1
        ),
        ucl_stats AS (
            SELECT eff_team_name, cnt AS ucl_cnt
            FROM per_name_league WHERE league_api_id = 7
        ),
        all_teams AS (
            SELECT DISTINCT eff_team_name FROM per_name_league
        )
        SELECT
            t.eff_team_name,
            CASE
                WHEN db.domestic_league IS NOT NULL AND db.domestic_cnt >= :min_cnt
                    THEN db.domestic_league
                WHEN u.ucl_cnt >= :min_cnt
                    THEN 7
                ELSE -1
            END AS effective_league
        FROM all_teams t
        LEFT JOIN domestic_best db ON db.eff_team_name = t.eff_team_name
        LEFT JOIN ucl_stats u ON u.eff_team_name = t.eff_team_name
    """)
    result = await session.execute(stmt, {"season": season, "min_cnt": _MIN_PLAYERS_FOR_TARGET_LEAGUE})
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
    season: str | None = Query(None),
) -> list[dict[str, Any]]:
    """Return teams deduplicated by name, filtered by dominant league.

    league_api_id=None → all teams; league_api_id=-1 → non-Big5/UCL teams; 1-7 → specific league.
    """
    if season is None:
        season = await current_season(session)

    dominant = await _get_team_dominant_leagues(session, season)

    if league_api_id is not None:
        valid_names: set[str] | None = {n for n, lg in dominant.items() if lg == league_api_id}
    else:
        valid_names = None

    stmt = text("""
        WITH team_counts AS (
            SELECT
                COALESCE(bp.loan_team_api_id, bp.current_team_api_id) AS eff_team_api_id,
                COALESCE(bp.loan_team_name, bp.current_team_name)      AS eff_team_name,
                COUNT(*) AS cnt
            FROM bzz_players bp
            JOIN bzz_player_season_stats bpss ON bpss.player_api_id = bp.api_id
            WHERE bpss.season = :season
              AND COALESCE(bp.loan_team_api_id, bp.current_team_api_id) IS NOT NULL
              AND COALESCE(bp.loan_team_name, bp.current_team_name) IS NOT NULL
            GROUP BY eff_team_api_id, eff_team_name
        ),
        ranked AS (
            SELECT eff_team_api_id, eff_team_name,
                   ROW_NUMBER() OVER (PARTITION BY eff_team_name ORDER BY cnt DESC) AS rn
            FROM team_counts
        )
        SELECT eff_team_api_id, eff_team_name FROM ranked WHERE rn = 1
        ORDER BY eff_team_name
    """)
    result = await session.execute(stmt, {"season": season})
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
    season: str | None = Query(None),
) -> StreamingResponse:
    """Stream aggregated player stats as a UTF-8 CSV — one row per player."""
    if season is None:
        season = await current_season(session)
    # Fetch players with their season stats (all leagues), then aggregate per player
    player_id_subq = select(BzzPlayer.api_id).where(BzzPlayer.internal_id.is_not(None))
    if league_api_id:
        dominant = await _get_team_dominant_leagues(session, season)
        team_names = [n for n, lg in dominant.items() if lg == league_api_id]
        if team_names:
            eff_team = func.coalesce(BzzPlayer.loan_team_name, BzzPlayer.current_team_name)
            player_id_subq = player_id_subq.where(eff_team.in_(team_names))

    players = (await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id.in_(player_id_subq))
    )).scalars().all()
    if not players:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        buf.seek(0)
        filename = f"ev0_players_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    stats_rows = (await session.execute(
        select(BzzPlayerSeasonStat)
        .where(BzzPlayerSeasonStat.player_api_id.in_(player_id_subq), BzzPlayerSeasonStat.season == season)
    )).scalars().all()

    stats_by_player: dict[int, list] = defaultdict(list)
    for s in stats_rows:
        stats_by_player[s.player_api_id].append(s)

    player_by_id = {p.api_id: p for p in players}

    rows = []
    for pid, player in player_by_id.items():
        merged = _merge_season_stats(stats_by_player.get(pid, []), season)
        if not merged:
            continue
        rows.append({
            "name": player.name,
            "team": player.loan_team_name or player.current_team_name or "",
            "position": player.position or "",
            "league": "",  # multi-league; no single league label
            "games": merged["matches_played"],
            "minutes": merged["minutes_played"],
            "goals": merged["goals"],
            "assists": merged["goal_assist"],
            "xg": merged["expected_goals"],
            "xa": merged["expected_assists"],
            "shots": merged["total_shots"],
            "key_passes": merged["key_pass"],
            "xg_per_90": merged["xg_per_90"],
            "xa_per_90": merged["xa_per_90"],
        })

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
    season: str | None = Query(None),
    sort_by: str = Query("xg_per_90"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """List players — one row per player, stats aggregated across all competitions."""
    if season is None:
        season = await current_season(session)

    # Step 1: build player API-ID subquery with all identity filters.
    # Using a subquery (not a Python list) avoids asyncpg's 32 767 parameter limit
    # when the unfiltered player set is large.
    player_id_subq = select(BzzPlayer.api_id).where(BzzPlayer.internal_id.is_not(None))

    if league_api_id is not None:
        dominant = await _get_team_dominant_leagues(session, season)
        team_names = [n for n, lg in dominant.items() if lg == league_api_id]
        if not team_names:
            return []
        eff_team = func.coalesce(BzzPlayer.loan_team_name, BzzPlayer.current_team_name)
        player_id_subq = player_id_subq.where(eff_team.in_(team_names))
    if team_api_id is not None:
        player_id_subq = player_id_subq.where(
            or_(
                BzzPlayer.current_team_api_id == team_api_id,
                BzzPlayer.loan_team_api_id == team_api_id,
            )
        )
    if position is not None:
        player_id_subq = player_id_subq.where(BzzPlayer.position == position.upper())

    players = (await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id.in_(player_id_subq))
    )).scalars().all()
    if not players:
        return []

    # Step 2: fetch ALL season stat rows using the same subquery (no parameter list)
    all_stats = (await session.execute(
        select(BzzPlayerSeasonStat)
        .where(
            BzzPlayerSeasonStat.player_api_id.in_(player_id_subq),
            BzzPlayerSeasonStat.season == season,
        )
    )).scalars().all()

    # Step 3: group by player
    stats_by_player: dict[int, list] = defaultdict(list)
    for s in all_stats:
        stats_by_player[s.player_api_id].append(s)

    # Deduplicate by internal_id — same real player may have multiple api_ids
    # (sync_players.py uses api_id=row.get("api_id") or row.get("id"))
    _by_internal: dict[int, list] = defaultdict(list)
    for _p in players:
        if _p.internal_id is not None:
            _by_internal[_p.internal_id].append(_p)
    player_by_id: dict[int, Any] = {}
    for _group in _by_internal.values():
        if len(_group) == 1:
            _best = _group[0]
        else:
            _best = max(_group, key=lambda _p: len(stats_by_player.get(_p.api_id, [])))
        player_by_id[_best.api_id] = _best

    # Step 4: aggregate and build output — one dict per player
    results: list[dict[str, Any]] = []
    for pid, player in player_by_id.items():
        rows = stats_by_player.get(pid)
        if not rows:
            continue  # no season stats yet

        merged = _merge_season_stats(rows, season)
        if not merged:
            continue

        if min_minutes > 0 and (merged["minutes_played"] or 0) < min_minutes:
            continue

        results.append({
            "player_api_id": player.api_id,
            "name": player.name,
            "short_name": player.short_name,
            "position": player.position,
            "team_name": player.loan_team_name or player.current_team_name,
            "nationality": player.nationality,
            "goals": merged["goals"],
            "goal_assist": merged["goal_assist"],
            "xg_per_90": merged["xg_per_90"],
            "xa_per_90": merged["xa_per_90"],
            "avg_rating": merged["avg_rating"],
            "shots_on_target_per_90": merged["shots_on_target_per_90"],
            "form_xg_5": merged["form_xg_5"],
            "matches_played": merged["matches_played"],
            "minutes_played": merged["minutes_played"],
            "season": merged["season"],
            # Extra numeric fields needed for sort but not in PlayerSummary schema
            "total_shots": merged["total_shots"],
            "shots_on_target": merged["shots_on_target"],
            "key_pass": merged["key_pass"],
            "expected_goals": merged["expected_goals"],
            "expected_assists": merged["expected_assists"],
            "yellow_card": merged["yellow_card"],
            "red_card": merged["red_card"],
            "saves": merged["saves"],
            "shots_per_90": merged["shots_per_90"],
            "key_pass_per_90": merged["key_pass_per_90"],
            "accurate_cross_per_90": merged["accurate_cross_per_90"],
            "recoveries_per_90": merged["recoveries_per_90"],
            "tackles_per_90": merged["tackles_per_90"],
            "interceptions_per_90": merged["interceptions_per_90"],
            "shot_accuracy": merged["shot_accuracy"],
            "xg_per_shot": merged["xg_per_shot"],
            "finishing_delta": merged["finishing_delta"],
            "xa_delta": merged["xa_delta"],
            "pass_completion": merged["pass_completion"],
            "long_ball_accuracy": merged["long_ball_accuracy"],
            "cross_accuracy": merged["cross_accuracy"],
            "duel_win_rate": merged["duel_win_rate"],
            "aerial_win_rate": merged["aerial_win_rate"],
            "tackle_success_rate": merged["tackle_success_rate"],
            "avg_minutes_per_match": merged["avg_minutes_per_match"],
            "starts_pct": merged["starts_pct"],
            "form_rating_5": merged["form_rating_5"],
            "form_goals_5": merged["form_goals_5"],
            "form_assists_5": merged["form_assists_5"],
            "rating_trend": merged["rating_trend"],
        })

    # Step 5: sort in Python, then paginate
    _python_sort(results, sort_by, sort_order)
    return results[offset: offset + limit]


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}  — detail
# ---------------------------------------------------------------------------


@router.get("/{player_api_id}", response_model=PlayerDetail)
async def get_player(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
    season: str | None = Query(None),
) -> dict[str, Any]:
    """Get a single player with season stats and recent matches."""
    if season is None:
        season = await current_season(session)

    # 1. Fetch player
    player_result = await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id == player_api_id)
    )
    player = player_result.scalars().first()
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    # 2. Season stats — aggregate ALL league rows (prevents partial data from first() pick)
    season_stat_rows = (await session.execute(
        select(BzzPlayerSeasonStat).where(
            BzzPlayerSeasonStat.player_api_id == player_api_id,
            BzzPlayerSeasonStat.season == season,
        )
    )).scalars().all()

    merged = _merge_season_stats(season_stat_rows, season)

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

    # 4. Build SeasonStatsOut — override form stats with values derived from actual recent matches
    season_stats_out: SeasonStatsOut | None = None
    if merged:
        form = _compute_form_from_matches(recent_matches, merged.get("avg_rating"))
        merged.update(form)
        season_stats_out = SeasonStatsOut(**merged)

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


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}/career  — full career history + Beta blended rhythm
# ---------------------------------------------------------------------------


_UNKNOWN_COMPETITION_LABEL = "Autre"


def _build_career_seasons(rows: list[PlayerCareerSeason]) -> list[CareerSeasonOut]:
    """Group career rows by season (summed across competitions), keep per-competition detail.

    Sorted by season_start_year descending; seasons with no season_start_year
    (malformed/unparsable source data) sort last, never first.

    `competition_code` is always "" (never NULL, see app.scripts.import_career)
    for matches without a Transfermarkt competitionId (friendlies) — such rows
    get a readable fallback label instead of a raw empty string.
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for row in rows:
        bucket = grouped.get(row.season)
        if bucket is None:
            bucket = {
                "season": row.season,
                "season_start_year": row.season_start_year,
                "appearances": 0,
                "goals": 0,
                "assists": 0,
                "minutes": 0,
                "competitions": [],
            }
            grouped[row.season] = bucket
            order.append(row.season)
        elif bucket["season_start_year"] is None and row.season_start_year is not None:
            bucket["season_start_year"] = row.season_start_year

        bucket["appearances"] += row.appearances
        bucket["goals"] += row.goals
        bucket["assists"] += row.assists
        bucket["minutes"] += row.minutes
        competition_label = row.competition
        if row.competition_code == "" and not competition_label:
            competition_label = _UNKNOWN_COMPETITION_LABEL

        bucket["competitions"].append(
            CareerCompetitionOut(
                competition=competition_label,
                competition_code=row.competition_code,
                appearances=row.appearances,
                goals=row.goals,
                assists=row.assists,
                minutes=row.minutes,
            )
        )

    seasons = [CareerSeasonOut(**grouped[season]) for season in order]
    seasons.sort(key=lambda s: (s.season_start_year is None, -(s.season_start_year or 0)))
    return seasons


@router.get("/{player_api_id}/career", response_model=PlayerCareerOut)
async def get_player_career(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full career history (detail per season, toutes compétitions) + Beta blended rhythm.

    404 only when the player itself is unknown — consistent with GET
    /players/{player_api_id}. A player that exists but has no career rows
    (not yet backfilled, or entirely covered by Bzzoiro) is a normal state:
    career=[] and blended_rhythm=None, not a 404 — blended_rhythm() already
    returns None gracefully when there is no usable data.
    """
    player_result = await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.name).where(BzzPlayer.api_id == player_api_id)
    )
    player_row = player_result.first()
    if player_row is None:
        raise HTTPException(status_code=404, detail="Player not found")

    career_rows = (await session.execute(
        select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == player_api_id)
    )).scalars().all()

    career = _build_career_seasons(career_rows)

    rhythm = await blended_rhythm(session, player_api_id)
    blended_rhythm_out = (
        BlendedRhythmOut(
            goal_rate_per_90=rhythm.goal_rate_per_90,
            assist_rate_per_90=rhythm.assist_rate_per_90,
            seasons_used=rhythm.seasons_used,
            has_career=rhythm.has_career,
        )
        if rhythm is not None
        else None
    )

    return {
        "player": {"player_api_id": player_row.api_id, "name": player_row.name},
        "career": career,
        "blended_rhythm": blended_rhythm_out,
    }
