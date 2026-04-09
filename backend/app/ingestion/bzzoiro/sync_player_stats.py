"""Sync per-match player statistics from Bzzoiro into bzz_player_match_stats.

The Bzzoiro /api/player-stats/ endpoint never includes player identity in its
response. The only correct approach is to query per-player using the player's
internal_id (the Bzzoiro DB primary key, distinct from api_id). Each response
row contains event.api_id which identifies the match.

We limit the scope to players who appeared in recently finished events to keep
the sync fast.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzTeam

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

    duel_sum: int | None = None
    if duel_won is not None and duel_lost is not None:
        duel_sum = duel_won + duel_lost

    aerial_sum: int | None = None
    if aerial_won is not None and aerial_lost is not None:
        aerial_sum = aerial_won + aerial_lost

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


async def sync_player_stats_for_player(
    session: AsyncSession,
    client: BzzoiroClient,
    player_api_id: int,
    player_internal_id: int,
) -> int:
    """Fetch and upsert all stats for a single player. Returns row count."""
    from app.models.bzzoiro import BzzPlayerMatchStat  # local import avoids circular

    rows = await client.get_all("/api/player-stats/", {"player_id": player_internal_id})
    count = 0
    for row in rows:
        event = row.get("event") or {}
        event_api_id = event.get("api_id")
        if event_api_id is None:
            continue

        derived = compute_derived_metrics(row)

        values: dict[str, Any] = {
            "player_api_id": player_api_id,
            "event_api_id": event_api_id,
            "team_api_id": None,  # not in response
            "is_home": None,  # not in response
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

    if count:
        await session.commit()

    return count


async def sync_player_stats(
    session: AsyncSession,
    client: BzzoiroClient,
    days_back: int = 7,
) -> int:
    """Sync player stats for players who appeared in recently finished events.

    Only syncs players whose current team played in a finished event within
    the last `days_back` days. This limits the scope to ~500-1000 players
    per run rather than the full 23K global player database.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days_back)

    # Find players from recently finished events (via team membership)
    result = await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.internal_id)
        .join(BzzTeam, BzzPlayer.current_team_api_id == BzzTeam.api_id)
        .join(
            BzzEvent,
            or_(
                BzzEvent.home_team_api_id == BzzTeam.api_id,
                BzzEvent.away_team_api_id == BzzTeam.api_id,
            ),
        )
        .where(
            BzzEvent.status == "finished",
            BzzEvent.event_date >= cutoff,
            BzzPlayer.internal_id.is_not(None),
        )
        .distinct()
    )
    players = result.fetchall()

    if not players:
        logger.info("No players with internal_id found for recently finished events")
        return 0

    total = 0
    for player_api_id, player_internal_id in players:
        count = await sync_player_stats_for_player(
            session, client, player_api_id, player_internal_id
        )
        total += count

    logger.info(
        "Synced %d total player-match stats for %d players (days_back=%d)",
        total,
        len(players),
        days_back,
    )
    return total
