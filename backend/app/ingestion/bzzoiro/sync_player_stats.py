"""Sync per-match player statistics from Bzzoiro into bzz_player_match_stats."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat

logger = logging.getLogger(__name__)


def compute_derived_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    """Compute derived efficiency metrics from a raw Bzzoiro player-stat row."""

    def safe_div(numerator: Any, denominator: Any) -> float | None:
        if denominator is None or denominator == 0:
            return None
        if numerator is None:
            return None
        return numerator / denominator

    total_shots = row.get("total_shots")
    shots_on_target = row.get("shots_on_target")
    expected_goals = row.get("expected_goals")
    goals = row.get("goals")
    goal_assist = row.get("goal_assist")
    expected_assists = row.get("expected_assists")
    total_pass = row.get("total_pass")
    accurate_pass = row.get("accurate_pass")
    total_long_balls = row.get("total_long_balls")
    accurate_long_balls = row.get("accurate_long_balls")
    total_cross = row.get("total_cross")
    accurate_cross = row.get("accurate_cross")
    duel_won = row.get("duel_won")
    duel_lost = row.get("duel_lost")
    aerial_won = row.get("aerial_won")
    aerial_lost = row.get("aerial_lost")
    total_tackle = row.get("total_tackle")
    won_tackle = row.get("won_tackle")

    # Duel win rate: need at least one non-None value; denominator = sum
    duel_sum: int | None = None
    if duel_won is not None or duel_lost is not None:
        duel_sum = (duel_won or 0) + (duel_lost or 0)

    aerial_sum: int | None = None
    if aerial_won is not None or aerial_lost is not None:
        aerial_sum = (aerial_won or 0) + (aerial_lost or 0)

    finishing_delta: float | None = None
    if goals is not None and expected_goals is not None:
        finishing_delta = goals - expected_goals

    xa_delta: float | None = None
    if goal_assist is not None and expected_assists is not None:
        xa_delta = goal_assist - expected_assists

    return {
        "shot_accuracy": safe_div(shots_on_target, total_shots),
        "xg_per_shot": safe_div(expected_goals, total_shots),
        "finishing_delta": finishing_delta,
        "xa_delta": xa_delta,
        "pass_completion": safe_div(accurate_pass, total_pass),
        "long_ball_accuracy": safe_div(accurate_long_balls, total_long_balls),
        "cross_accuracy": safe_div(accurate_cross, total_cross),
        "duel_win_rate": safe_div(duel_won, duel_sum),
        "aerial_win_rate": safe_div(aerial_won, aerial_sum),
        "tackle_success_rate": safe_div(won_tackle, total_tackle),
    }


async def sync_player_stats_for_event(
    session: AsyncSession,
    client: BzzoiroClient,
    event_api_id: int,
) -> int:
    """Fetch and upsert all player stats for a given event. Returns row count."""
    rows = await client.get_all("/api/player-stats/", {"event": event_api_id})
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        player = row.get("player") or {}
        event = row.get("event") or {}
        team = row.get("team") or {}

        derived = compute_derived_metrics(row)

        values: dict[str, Any] = {
            "player_api_id": player.get("api_id"),
            "event_api_id": event.get("api_id") or event_api_id,
            "team_api_id": team.get("api_id"),
            "is_home": row.get("is_home"),
            "minutes_played": row.get("minutes_played"),
            "rating": row.get("rating"),
            "touches": row.get("touches"),
            "goals": row.get("goals"),
            "goal_assist": row.get("goal_assist"),
            "expected_goals": row.get("expected_goals"),
            "expected_assists": row.get("expected_assists"),
            "total_shots": row.get("total_shots"),
            "shots_on_target": row.get("shots_on_target"),
            "total_pass": row.get("total_pass"),
            "accurate_pass": row.get("accurate_pass"),
            "key_pass": row.get("key_pass"),
            "total_long_balls": row.get("total_long_balls"),
            "accurate_long_balls": row.get("accurate_long_balls"),
            "total_cross": row.get("total_cross"),
            "accurate_cross": row.get("accurate_cross"),
            "duel_won": row.get("duel_won"),
            "duel_lost": row.get("duel_lost"),
            "aerial_won": row.get("aerial_won"),
            "aerial_lost": row.get("aerial_lost"),
            "total_tackle": row.get("total_tackle"),
            "won_tackle": row.get("won_tackle"),
            "total_clearance": row.get("total_clearance"),
            "interception": row.get("interception"),
            "ball_recovery": row.get("ball_recovery"),
            "yellow_card": row.get("yellow_card"),
            "red_card": row.get("red_card"),
            "fouls": row.get("fouls"),
            "was_fouled": row.get("was_fouled"),
            "dispossessed": row.get("dispossessed"),
            "possession_lost": row.get("possession_lost"),
            "saves": row.get("saves"),
            "goals_conceded": row.get("goals_conceded"),
            **derived,
        }

        stmt = pg_insert(BzzPlayerMatchStat).values(**values).on_conflict_do_update(
            index_elements=["player_api_id", "event_api_id"],
            set_={k: v for k, v in values.items() if k not in ("player_api_id", "event_api_id")},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d player stats for event %d", count, event_api_id)
    return count


async def sync_player_stats(
    session: AsyncSession,
    client: BzzoiroClient,
    days_back: int = 7,
) -> int:
    """Sync player stats for all finished events in the last `days_back` days."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days_back)

    result = await session.execute(
        select(BzzEvent.api_id).where(
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff,
        )
    )
    event_ids = [row[0] for row in result.fetchall()]

    total = 0
    for event_api_id in event_ids:
        total += await sync_player_stats_for_event(session, client, event_api_id)

    logger.info("Synced %d total player stats across %d events", total, len(event_ids))
    return total
