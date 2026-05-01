"""Aggregate per-match player stats into per-season stats for Bzzoiro."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import SEASON_START_DATE
from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat, BzzPlayerSeasonStat

logger = logging.getLogger(__name__)


def _safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if denominator is None or denominator == 0:
        return None
    if numerator is None:
        return None
    return numerator / denominator


async def aggregate_season_stats(
    session: AsyncSession,
    league_api_id: int,
    season: str,
    league_api_id_aliases: list[int] | None = None,
    season_start: str | None = None,
) -> int:
    """Aggregate per-match stats into per-season stats for a league.

    league_api_id_aliases: additional league IDs to include (e.g. old SofaScore IDs
    that map to the same real league as league_api_id post-migration). Stats are
    stored under league_api_id (the canonical ID).

    season_start: ISO date string (e.g. "2025-08-01"). Only events on or after this
    date are included. Defaults to SEASON_START_DATE from constants so historical
    match data from prior seasons never inflates current-season aggregates.

    Aggregates all finished matches for the league in the season window. Returns count of rows upserted.
    """
    all_league_ids = [league_api_id] + (league_api_id_aliases or [])
    cutoff_date = date.fromisoformat(season_start or SEASON_START_DATE)

    # Step 1: Query aggregated stats grouped by player
    agg_stmt = (
        select(
            BzzPlayerMatchStat.player_api_id,
            func.count(BzzPlayerMatchStat.event_api_id.distinct()).label("matches_played"),
            func.sum(BzzPlayerMatchStat.minutes_played).label("minutes_played"),
            # starts: approximated as matches with >= 60 minutes played
            func.count(
                BzzPlayerMatchStat.event_api_id.distinct()
            ).filter(BzzPlayerMatchStat.minutes_played >= 60).label("starts"),
            func.sum(BzzPlayerMatchStat.goals).label("goals"),
            func.sum(BzzPlayerMatchStat.goal_assist).label("goal_assist"),
            func.sum(BzzPlayerMatchStat.expected_goals).label("expected_goals"),
            func.sum(BzzPlayerMatchStat.expected_assists).label("expected_assists"),
            func.sum(BzzPlayerMatchStat.total_shots).label("total_shots"),
            func.sum(BzzPlayerMatchStat.shots_on_target).label("shots_on_target"),
            func.sum(BzzPlayerMatchStat.key_pass).label("key_pass"),
            func.sum(BzzPlayerMatchStat.total_cross).label("total_cross"),
            func.sum(BzzPlayerMatchStat.accurate_cross).label("accurate_cross"),
            func.sum(BzzPlayerMatchStat.total_pass).label("total_pass"),
            func.sum(BzzPlayerMatchStat.accurate_pass).label("accurate_pass"),
            func.sum(BzzPlayerMatchStat.total_long_balls).label("total_long_balls"),
            func.sum(BzzPlayerMatchStat.accurate_long_balls).label("accurate_long_balls"),
            func.sum(BzzPlayerMatchStat.duel_won).label("duel_won"),
            func.sum(BzzPlayerMatchStat.duel_lost).label("duel_lost"),
            func.sum(BzzPlayerMatchStat.aerial_won).label("aerial_won"),
            func.sum(BzzPlayerMatchStat.aerial_lost).label("aerial_lost"),
            func.sum(BzzPlayerMatchStat.total_tackle).label("total_tackle"),
            func.sum(BzzPlayerMatchStat.won_tackle).label("won_tackle"),
            func.sum(BzzPlayerMatchStat.interception).label("interception"),
            func.sum(BzzPlayerMatchStat.ball_recovery).label("ball_recovery"),
            func.sum(BzzPlayerMatchStat.yellow_card).label("yellow_card"),
            func.sum(BzzPlayerMatchStat.red_card).label("red_card"),
            func.sum(BzzPlayerMatchStat.saves).label("saves"),
            # Efficiency averages
            func.avg(BzzPlayerMatchStat.shot_accuracy).label("shot_accuracy"),
            func.avg(BzzPlayerMatchStat.xg_per_shot).label("xg_per_shot"),
            func.avg(BzzPlayerMatchStat.finishing_delta).label("finishing_delta"),
            func.avg(BzzPlayerMatchStat.xa_delta).label("xa_delta"),
            func.avg(BzzPlayerMatchStat.pass_completion).label("pass_completion"),
            func.avg(BzzPlayerMatchStat.long_ball_accuracy).label("long_ball_accuracy"),
            func.avg(BzzPlayerMatchStat.cross_accuracy).label("cross_accuracy"),
            func.avg(BzzPlayerMatchStat.duel_win_rate).label("duel_win_rate"),
            func.avg(BzzPlayerMatchStat.aerial_win_rate).label("aerial_win_rate"),
            func.avg(BzzPlayerMatchStat.tackle_success_rate).label("tackle_success_rate"),
            func.avg(BzzPlayerMatchStat.rating).label("avg_rating"),
            func.avg(BzzPlayerMatchStat.minutes_played).label("avg_minutes_per_match"),
        )
        .join(BzzEvent, BzzPlayerMatchStat.event_api_id == BzzEvent.api_id)
        .where(
            BzzEvent.league_api_id.in_(all_league_ids),
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff_date,
        )
        .group_by(BzzPlayerMatchStat.player_api_id)
    )

    result = await session.execute(agg_stmt)
    rows = result.fetchall()

    if not rows:
        return 0

    # Step 2: Bulk window query — last 5 matches per player (single query, no N+1)
    rn_col = func.row_number().over(
        partition_by=BzzPlayerMatchStat.player_api_id,
        order_by=BzzEvent.event_date.desc(),
    ).label("rn")

    ranked_stmt = (
        select(
            BzzPlayerMatchStat.player_api_id,
            BzzPlayerMatchStat.expected_goals,
            BzzPlayerMatchStat.rating,
            BzzPlayerMatchStat.goals,
            BzzPlayerMatchStat.goal_assist,
            rn_col,
        )
        .join(BzzEvent, BzzPlayerMatchStat.event_api_id == BzzEvent.api_id)
        .where(
            BzzEvent.league_api_id.in_(all_league_ids),
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff_date,
        )
        .subquery()
    )

    last5_stmt = select(ranked_stmt).where(ranked_stmt.c.rn <= 5)
    last5_rows = (await session.execute(last5_stmt)).fetchall()

    form_by_player: dict[int, list] = defaultdict(list)
    for r in last5_rows:
        form_by_player[r.player_api_id].append(r)

    count = 0
    now = datetime.now(UTC)

    for row in rows:
        row_dict: dict[str, Any] = row._mapping  # type: ignore[union-attr]
        player_api_id = row_dict["player_api_id"]
        matches_played = row_dict["matches_played"] or 0
        minutes_played_total = row_dict["minutes_played"] or 0
        starts = row_dict["starts"] or 0

        # Per-90 calculations
        mins_per_90 = minutes_played_total / 90 if minutes_played_total else None

        xg_per_90 = _safe_div(row_dict["expected_goals"], mins_per_90)
        xa_per_90 = _safe_div(row_dict["expected_assists"], mins_per_90)
        shots_per_90 = _safe_div(row_dict["total_shots"], mins_per_90)
        shots_on_target_per_90 = _safe_div(row_dict["shots_on_target"], mins_per_90)
        key_pass_per_90 = _safe_div(row_dict["key_pass"], mins_per_90)
        accurate_cross_per_90 = _safe_div(row_dict["accurate_cross"], mins_per_90)
        recoveries_per_90 = _safe_div(row_dict["ball_recovery"], mins_per_90)
        tackles_per_90 = _safe_div(row_dict["total_tackle"], mins_per_90)
        interceptions_per_90 = _safe_div(row_dict["interception"], mins_per_90)

        starts_pct = _safe_div(starts, matches_played)
        avg_rating = row_dict["avg_rating"]
        if avg_rating is not None:
            avg_rating = float(avg_rating)

        # Step 3: Form stats — look up from pre-fetched bulk form data
        form_rows = form_by_player[player_api_id]

        form_xg_5: float | None = None
        form_rating_5: float | None = None
        form_goals_5: int | None = None
        form_assists_5: int | None = None
        rating_trend: float | None = None

        if form_rows:
            xg_vals = [r.expected_goals for r in form_rows if r.expected_goals is not None]
            rating_vals = [r.rating for r in form_rows if r.rating is not None]
            goals_vals = [r.goals for r in form_rows if r.goals is not None]
            assist_vals = [r.goal_assist for r in form_rows if r.goal_assist is not None]

            form_xg_5 = sum(xg_vals) if xg_vals else None
            form_rating_5 = sum(rating_vals) / len(rating_vals) if rating_vals else None
            form_goals_5 = sum(goals_vals) if goals_vals else None
            form_assists_5 = sum(assist_vals) if assist_vals else None

            if form_rating_5 is not None and avg_rating is not None:
                rating_trend = form_rating_5 - avg_rating

        # Step 4: Upsert
        values: dict[str, Any] = {
            "player_api_id": player_api_id,
            "league_api_id": league_api_id,
            "season": season,
            "as_of_utc": now,
            "matches_played": matches_played,
            "minutes_played": minutes_played_total,
            "starts": starts,
            "goals": row_dict["goals"],
            "goal_assist": row_dict["goal_assist"],
            "expected_goals": row_dict["expected_goals"],
            "expected_assists": row_dict["expected_assists"],
            "total_shots": row_dict["total_shots"],
            "shots_on_target": row_dict["shots_on_target"],
            "key_pass": row_dict["key_pass"],
            "total_cross": row_dict["total_cross"],
            "accurate_cross": row_dict["accurate_cross"],
            "total_pass": row_dict["total_pass"],
            "accurate_pass": row_dict["accurate_pass"],
            "total_long_balls": row_dict["total_long_balls"],
            "accurate_long_balls": row_dict["accurate_long_balls"],
            "duel_won": row_dict["duel_won"],
            "duel_lost": row_dict["duel_lost"],
            "aerial_won": row_dict["aerial_won"],
            "aerial_lost": row_dict["aerial_lost"],
            "total_tackle": row_dict["total_tackle"],
            "won_tackle": row_dict["won_tackle"],
            "interception": row_dict["interception"],
            "ball_recovery": row_dict["ball_recovery"],
            "yellow_card": row_dict["yellow_card"],
            "red_card": row_dict["red_card"],
            "saves": row_dict["saves"],
            # Per-90
            "xg_per_90": xg_per_90,
            "xa_per_90": xa_per_90,
            "shots_per_90": shots_per_90,
            "shots_on_target_per_90": shots_on_target_per_90,
            "key_pass_per_90": key_pass_per_90,
            "accurate_cross_per_90": accurate_cross_per_90,
            "recoveries_per_90": recoveries_per_90,
            "tackles_per_90": tackles_per_90,
            "interceptions_per_90": interceptions_per_90,
            # Efficiency
            "shot_accuracy": row_dict["shot_accuracy"],
            "xg_per_shot": row_dict["xg_per_shot"],
            "finishing_delta": row_dict["finishing_delta"],
            "xa_delta": row_dict["xa_delta"],
            "pass_completion": row_dict["pass_completion"],
            "long_ball_accuracy": row_dict["long_ball_accuracy"],
            "cross_accuracy": row_dict["cross_accuracy"],
            "duel_win_rate": row_dict["duel_win_rate"],
            "aerial_win_rate": row_dict["aerial_win_rate"],
            "tackle_success_rate": row_dict["tackle_success_rate"],
            "avg_rating": avg_rating,
            # Profile
            "avg_minutes_per_match": row_dict["avg_minutes_per_match"],
            "starts_pct": starts_pct,
            # Form
            "form_xg_5": form_xg_5,
            "form_rating_5": form_rating_5,
            "form_goals_5": form_goals_5,
            "form_assists_5": form_assists_5,
            "rating_trend": rating_trend,
        }

        stmt = pg_insert(BzzPlayerSeasonStat).values(**values).on_conflict_do_update(
            index_elements=["player_api_id", "league_api_id", "season"],
            set_={k: v for k, v in values.items() if k not in ("player_api_id", "league_api_id", "season")},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info(
        "Aggregated season stats for %d players in league %d (season %s)",
        count,
        league_api_id,
        season,
    )
    return count


async def aggregate_all_leagues(session: AsyncSession, season: str = "2025-2026") -> int:
    """Aggregate season stats for all 6 target leagues with finished matches.

    Only canonical Bzzoiro internal IDs [1,3,4,5,6,7] are used. The old SofaScore
    api_ids (8,17,23,34,35) now belong to completely different competitions in Bzzoiro
    (Saudi Pro League, Europa League, etc.) and must not be merged with target leagues.

    Returns total count of rows upserted across all leagues.
    """
    from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_ID_LIST

    result = await session.execute(
        select(BzzEvent.league_api_id.distinct()).where(
            BzzEvent.league_api_id.in_(TARGET_LEAGUE_INTERNAL_ID_LIST),
            BzzEvent.status == "finished",
        )
    )
    target_ids = [row[0] for row in result.fetchall()]

    total = 0
    for lid in target_ids:
        total += await aggregate_season_stats(session, lid, season=season)

    logger.info("Aggregated season stats for %d leagues, %d total rows", len(target_ids), total)
    return total
