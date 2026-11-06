"""Aggregate per-match player stats for national-team competitions into
player_selection_season_stats (isolated from bzz_player_season_stats, which
holds club stats only).

Mirrors the structure of aggregate.py::aggregate_season_stats (same
sums/averages, same 5-match form window) but:
- groups by (player_api_id, BzzEvent.league_api_id) instead of player alone,
  since a player can accumulate selection stats across several competitions
  (World Cup 27, UEFA Nations League 64, CONCACAF Nations League 65);
- restricts to SELECTION_STATS_LEAGUE_IDS;
- resolves the national team via nationality (national_team_id_by_player),
  never via bzz_player_match_stats.team_api_id (empty on 59% of World Cup
  rows) — a player who cannot be resolved to a national team is skipped
  (DEBUG log), never inserted without a team.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import SELECTION_STATS_LEAGUE_IDS
from app.ingestion.bzzoiro.selection_nationality import national_team_id_by_player
from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat
from app.models.selection_stats import PlayerSelectionSeasonStat
from app.services.season_service import season_end as season_end_of
from app.services.season_service import season_start as season_start_of

logger = logging.getLogger(__name__)


def _safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if denominator is None or denominator == 0:
        return None
    if numerator is None:
        return None
    return numerator / denominator


async def aggregate_selection_stats(session: AsyncSession, season: str) -> int:
    """Aggregate finished selection matches (World Cup / Nations League) into
    per-(player, national team, league, season) rows.

    Returns the count of rows upserted. Players whose nationality cannot be
    resolved to a canonical national team are skipped entirely — never
    inserted without a team (no guessed/fake rattachement).
    """
    league_ids = sorted(SELECTION_STATS_LEAGUE_IDS)
    cutoff_date = season_start_of(season)
    cutoff_end = season_end_of(season)
    mapping = await national_team_id_by_player(session)

    agg_stmt = (
        select(
            BzzPlayerMatchStat.player_api_id,
            BzzEvent.league_api_id.label("league_api_id"),
            func.count(BzzPlayerMatchStat.event_api_id.distinct()).label("matches_played"),
            func.sum(BzzPlayerMatchStat.minutes_played).label("minutes_played"),
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
            BzzEvent.league_api_id.in_(league_ids),
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff_date,
            BzzEvent.event_date < cutoff_end,
        )
        .group_by(BzzPlayerMatchStat.player_api_id, BzzEvent.league_api_id)
    )
    rows = (await session.execute(agg_stmt)).fetchall()
    if not rows:
        return 0

    # Form : 5 derniers matchs par (joueur, ligue) — fenetre identique.
    rn_col = func.row_number().over(
        partition_by=(BzzPlayerMatchStat.player_api_id, BzzEvent.league_api_id),
        order_by=BzzEvent.event_date.desc(),
    ).label("rn")
    ranked = (
        select(
            BzzPlayerMatchStat.player_api_id,
            BzzEvent.league_api_id.label("league_api_id"),
            BzzPlayerMatchStat.expected_goals,
            BzzPlayerMatchStat.rating,
            BzzPlayerMatchStat.goals,
            BzzPlayerMatchStat.goal_assist,
            rn_col,
        )
        .join(BzzEvent, BzzPlayerMatchStat.event_api_id == BzzEvent.api_id)
        .where(
            BzzEvent.league_api_id.in_(league_ids),
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff_date,
            BzzEvent.event_date < cutoff_end,
        )
        .subquery()
    )
    last5 = (await session.execute(select(ranked).where(ranked.c.rn <= 5))).fetchall()
    form_by: dict[tuple[int, int], list] = defaultdict(list)
    for r in last5:
        form_by[(r.player_api_id, r.league_api_id)].append(r)

    now = datetime.now(UTC)
    count = 0
    for row in rows:
        d: dict[str, Any] = row._mapping  # type: ignore[union-attr]
        pid = d["player_api_id"]
        national_team_api_id = mapping.get(pid)
        if national_team_api_id is None:
            logger.debug("sélection: joueur %s sans équipe nationale résolue, ignoré", pid)
            continue
        lid = d["league_api_id"]
        minutes = d["minutes_played"] or 0
        mins90 = minutes / 90 if minutes else None
        matches = d["matches_played"] or 0
        starts = d["starts"] or 0
        avg_rating = float(d["avg_rating"]) if d["avg_rating"] is not None else None

        form_rows = form_by[(pid, lid)]
        xg_vals = [r.expected_goals for r in form_rows if r.expected_goals is not None]
        rating_vals = [r.rating for r in form_rows if r.rating is not None]
        goals_vals = [r.goals for r in form_rows if r.goals is not None]
        assist_vals = [r.goal_assist for r in form_rows if r.goal_assist is not None]
        form_rating_5 = sum(rating_vals) / len(rating_vals) if rating_vals else None
        form_xg_5 = sum(xg_vals) if xg_vals else None
        form_goals_5 = sum(goals_vals) if goals_vals else None
        form_assists_5 = sum(assist_vals) if assist_vals else None
        rating_trend = (
            form_rating_5 - avg_rating
            if (form_rating_5 is not None and avg_rating is not None)
            else None
        )

        values: dict[str, Any] = {
            "player_api_id": pid,
            "national_team_api_id": national_team_api_id,
            "league_api_id": lid,
            "season": season,
            "source": "bzzoiro",
            "as_of_utc": now,
            "matches_played": matches,
            "minutes_played": minutes,
            "starts": starts,
            "goals": d["goals"],
            "goal_assist": d["goal_assist"],
            "expected_goals": d["expected_goals"],
            "expected_assists": d["expected_assists"],
            "total_shots": d["total_shots"],
            "shots_on_target": d["shots_on_target"],
            "key_pass": d["key_pass"],
            "total_cross": d["total_cross"],
            "accurate_cross": d["accurate_cross"],
            "total_pass": d["total_pass"],
            "accurate_pass": d["accurate_pass"],
            "total_long_balls": d["total_long_balls"],
            "accurate_long_balls": d["accurate_long_balls"],
            "duel_won": d["duel_won"],
            "duel_lost": d["duel_lost"],
            "aerial_won": d["aerial_won"],
            "aerial_lost": d["aerial_lost"],
            "total_tackle": d["total_tackle"],
            "won_tackle": d["won_tackle"],
            "interception": d["interception"],
            "ball_recovery": d["ball_recovery"],
            "yellow_card": d["yellow_card"],
            "red_card": d["red_card"],
            "saves": d["saves"],
            "xg_per_90": _safe_div(d["expected_goals"], mins90),
            "xa_per_90": _safe_div(d["expected_assists"], mins90),
            "shots_per_90": _safe_div(d["total_shots"], mins90),
            "shots_on_target_per_90": _safe_div(d["shots_on_target"], mins90),
            "key_pass_per_90": _safe_div(d["key_pass"], mins90),
            "accurate_cross_per_90": _safe_div(d["accurate_cross"], mins90),
            "recoveries_per_90": _safe_div(d["ball_recovery"], mins90),
            "tackles_per_90": _safe_div(d["total_tackle"], mins90),
            "interceptions_per_90": _safe_div(d["interception"], mins90),
            "shot_accuracy": d["shot_accuracy"],
            "xg_per_shot": d["xg_per_shot"],
            "finishing_delta": d["finishing_delta"],
            "xa_delta": d["xa_delta"],
            "pass_completion": d["pass_completion"],
            "long_ball_accuracy": d["long_ball_accuracy"],
            "cross_accuracy": d["cross_accuracy"],
            "duel_win_rate": d["duel_win_rate"],
            "aerial_win_rate": d["aerial_win_rate"],
            "tackle_success_rate": d["tackle_success_rate"],
            "avg_rating": avg_rating,
            "avg_minutes_per_match": d["avg_minutes_per_match"],
            "starts_pct": _safe_div(starts, matches),
            "form_xg_5": form_xg_5,
            "form_rating_5": form_rating_5,
            "form_goals_5": form_goals_5,
            "form_assists_5": form_assists_5,
            "rating_trend": rating_trend,
        }

        stmt = pg_insert(PlayerSelectionSeasonStat).values(**values).on_conflict_do_update(
            index_elements=["player_api_id", "national_team_api_id", "league_api_id", "season", "source"],
            set_={
                k: v for k, v in values.items()
                if k not in ("player_api_id", "national_team_api_id", "league_api_id", "season", "source")
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Agrégé %d lignes de stats de sélection (saison %s)", count, season)
    return count
