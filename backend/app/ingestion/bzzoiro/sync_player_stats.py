"""Sync per-match player statistics from Bzzoiro into bzz_player_match_stats.

The Bzzoiro /api/player-stats/ endpoint never includes player identity in its
response. The only correct approach is to query per-player using the player's
internal_id (the Bzzoiro DB primary key, distinct from api_id). Each response
row contains event.api_id which identifies the match.

Filter: use ``player=<internal_id>`` (NOT ``player_id=``).

Two modes:
  - Regular sync (days_back): processes players from recently finished events.
  - Full backfill (full_season=True): processes ALL players who appeared in any
    finished event across the 6 target leagues in the DB.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_API_ID_LIST
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
    client: BzzoiroClient,  # type: ignore[name-defined]
    player_api_id: int,
    player_internal_id: int,
) -> int:
    """Fetch and upsert all stats for a single player. Returns row count."""
    from app.models.bzzoiro import BzzPlayerMatchStat  # local import avoids circular

    rows = await client.get_all("/api/player-stats/", {"player": player_internal_id})
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
            "team_api_id": None,
            "is_home": None,
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


async def _get_players_for_recent_events(
    session: AsyncSession,
    days_back: int,
) -> list[tuple[int, int]]:
    """Return (player_api_id, player_internal_id) for players from recently finished events."""
    cutoff = datetime.now(UTC) - timedelta(days=days_back)
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
            BzzEvent.league_api_id.in_(TARGET_LEAGUE_API_ID_LIST),
            BzzPlayer.internal_id.is_not(None),
        )
        .distinct()
    )
    return result.fetchall()


async def _get_players_for_full_season(
    session: AsyncSession,
) -> list[tuple[int, int]]:
    """Return (player_api_id, player_internal_id) for ALL players in finished events
    across the 6 target leagues — no date restriction."""
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
            BzzEvent.league_api_id.in_(TARGET_LEAGUE_API_ID_LIST),
            BzzPlayer.internal_id.is_not(None),
        )
        .distinct()
    )
    return result.fetchall()


async def sync_player_stats(
    session: AsyncSession,
    client: Any,
    days_back: int = 14,
    full_season: bool = False,
) -> int:
    """Sync player stats for players who appeared in finished events.

    Args:
        days_back: Days of history to cover (ignored when full_season=True).
        full_season: If True, processes ALL players across the entire season
                     in the 6 target leagues — use for initial backfill.
    """
    if full_season:
        players = await _get_players_for_full_season(session)
        logger.info("Full-season backfill: %d players to sync", len(players))
    else:
        players = await _get_players_for_recent_events(session, days_back)
        logger.info("Incremental sync (days_back=%d): %d players", days_back, len(players))

    if not players:
        logger.info("No players with internal_id found — nothing to sync")
        return 0

    total = 0
    errors = 0
    for i, (player_api_id, player_internal_id) in enumerate(players):
        try:
            count = await sync_player_stats_for_player(
                session, client, player_api_id, player_internal_id
            )
            total += count
        except Exception as exc:
            errors += 1
            logger.warning(
                "Failed stats for player api_id=%d (internal=%d): %s",
                player_api_id, player_internal_id, exc,
            )
        # Throttle every 10 players to avoid hammering the API
        if i % 10 == 9:
            await asyncio.sleep(0.5)
        # Progress log every 100 players during backfill
        if full_season and i % 100 == 99:
            logger.info("  Progress: %d/%d players processed, %d rows so far", i + 1, len(players), total)

    logger.info(
        "Synced %d total player-match stats for %d players (%d errors, full_season=%s)",
        total, len(players), errors, full_season,
    )
    return total
