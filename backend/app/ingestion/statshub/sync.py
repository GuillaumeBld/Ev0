"""Gap-fill BzzPlayerMatchStat with StatsHub data.

StatsHub shares the same SofaScore-based ID space as Bzzoiro:
  team_id       → BzzTeam.api_id
  eventId       → BzzEvent.api_id
  player.internalId → BzzPlayer.internal_id
  tournamentId  → BzzLeague.api_id
  seasonId      → BzzLeague.season_id

Fill strategy: COALESCE — existing Bzzoiro values take precedence;
StatsHub values only fill NULL slots. New rows are inserted where
Bzzoiro produced no match stat at all.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_API_ID_LIST
from app.ingestion.bzzoiro.sync_player_stats import compute_derived_metrics
from app.ingestion.statshub.client import StatshubClient

logger = logging.getLogger(__name__)

# StatsHub field name → BzzPlayerMatchStat column name
_FIELD_MAP: dict[str, str] = {
    "onTargetScoringAttempt": "shots_on_target",
    "shots": "total_shots",
    "expectedGoals": "expected_goals",
    "expectedAssists": "expected_assists",
    "keyPass": "key_pass",
    "touches": "touches",
    "rating": "rating",
    "aerialWon": "aerial_won",
    "goalAssist": "goal_assist",
    "goals": "goals",
    "fouls": "fouls",
    "wasFouled": "was_fouled",
    "totalCross": "total_cross",
    "accurateCross": "accurate_cross",
    "dispossessed": "dispossessed",
    "interceptionWon": "interception",
}


def _parse_event_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one StatsHub event entry to BzzPlayerMatchStat column names."""
    row: dict[str, Any] = {}
    for sh_field, bzz_col in _FIELD_MAP.items():
        val = raw.get(sh_field)
        if val is not None:
            row[bzz_col] = val
    return row


async def _upcoming_team_league_triples(
    session: AsyncSession,
    days_ahead: int,
) -> list[tuple[int, int, int]]:
    """Return (team_api_id, tournament_id, season_id) for teams with upcoming fixtures."""
    from app.models.bzzoiro import BzzEvent, BzzLeague

    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_ahead)

    events_result = await session.execute(
        select(
            BzzEvent.home_team_api_id,
            BzzEvent.away_team_api_id,
            BzzEvent.league_api_id,
        )
        .where(
            BzzEvent.event_date >= now,
            BzzEvent.event_date <= cutoff,
            BzzEvent.league_api_id.in_(TARGET_LEAGUE_API_ID_LIST),
            BzzEvent.home_team_api_id.is_not(None),
            BzzEvent.away_team_api_id.is_not(None),
        )
        .distinct()
    )

    team_league: set[tuple[int, int]] = set()
    for home_id, away_id, league_id in events_result.fetchall():
        if home_id:
            team_league.add((home_id, league_id))
        if away_id:
            team_league.add((away_id, league_id))

    if not team_league:
        return []

    return await _resolve_season_ids(session, team_league)


async def _all_team_league_triples(
    session: AsyncSession,
) -> list[tuple[int, int, int]]:
    """Return (team_api_id, tournament_id, season_id) for ALL teams in the 6 target leagues.

    Used by the weekly full-season gap-fill to cover teams not playing in the next 14 days.
    Builds the set from BzzEvent — any team that appeared in at least one event in a
    target league during the current season.
    """
    from app.models.bzzoiro import BzzEvent

    events_result = await session.execute(
        select(
            BzzEvent.home_team_api_id,
            BzzEvent.away_team_api_id,
            BzzEvent.league_api_id,
        )
        .where(
            BzzEvent.league_api_id.in_(TARGET_LEAGUE_API_ID_LIST),
            BzzEvent.home_team_api_id.is_not(None),
            BzzEvent.away_team_api_id.is_not(None),
        )
        .distinct()
    )

    team_league: set[tuple[int, int]] = set()
    for home_id, away_id, league_id in events_result.fetchall():
        if home_id:
            team_league.add((home_id, league_id))
        if away_id:
            team_league.add((away_id, league_id))

    if not team_league:
        return []

    return await _resolve_season_ids(session, team_league)


async def _resolve_season_ids(
    session: AsyncSession,
    team_league: set[tuple[int, int]],
) -> list[tuple[int, int, int]]:
    """Attach season_id from BzzLeague to (team_api_id, league_api_id) pairs."""
    from app.models.bzzoiro import BzzLeague

    league_ids = {lg for _, lg in team_league}
    leagues_result = await session.execute(
        select(BzzLeague.api_id, BzzLeague.season_id)
        .where(BzzLeague.api_id.in_(league_ids))
    )
    season_map: dict[int, int] = {
        row.api_id: row.season_id
        for row in leagues_result
        if row.season_id is not None
    }

    triples = []
    for team_id, league_id in sorted(team_league):
        season_id = season_map.get(league_id)
        if season_id is None:
            logger.warning(
                "statshub: BzzLeague api_id=%d has no season_id — skip team %d",
                league_id, team_id,
            )
            continue
        triples.append((team_id, league_id, season_id))

    return triples


async def sync_statshub_gap_fill(
    session: AsyncSession,
    days_ahead: int = 14,
    full_season: bool = False,
) -> int:
    """Fetch StatsHub stats and COALESCE-upsert into BzzPlayerMatchStat.

    Existing non-NULL Bzzoiro values are preserved; StatsHub fills NULLs.

    Args:
        days_ahead: horizon used when full_season=False (default 14 days).
        full_season: when True, queries ALL teams from the 6 target leagues
                     regardless of upcoming fixtures — used for weekly backfill.

    Returns total rows upserted.
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerMatchStat

    if full_season:
        triples = await _all_team_league_triples(session)
        logger.info("statshub full-season: %d team/league pairs to sync", len(triples))
    else:
        triples = await _upcoming_team_league_triples(session, days_ahead)
        logger.info("statshub incremental: %d team/league pairs to sync", len(triples))

    if not triples:
        logger.info("statshub: nothing to sync")
        return 0

    # Build internal_id → api_id index for all known players
    players_result = await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.internal_id)
        .where(BzzPlayer.internal_id.is_not(None))
    )
    player_by_internal: dict[int, int] = {
        row.internal_id: row.api_id for row in players_result
    }

    table = BzzPlayerMatchStat.__table__
    total = 0
    errors = 0

    async with StatshubClient() as client:
        for i, (team_api_id, tournament_id, season_id) in enumerate(triples):
            try:
                data = await client.get_team_player_performance(
                    team_api_id, tournament_id, season_id
                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    "statshub: fetch error team=%d league=%d: %s",
                    team_api_id, tournament_id, exc,
                )
                continue

            if not data:
                continue

            for player_info in data.get("data", []):
                if not isinstance(player_info, dict):
                    continue

                internal_id = player_info.get("internalId")
                player_api_id = player_by_internal.get(internal_id) if internal_id else None
                if player_api_id is None:
                    logger.debug(
                        "statshub: unknown internalId=%s (team=%d) — skipped",
                        internal_id, team_api_id,
                    )
                    continue

                stats_by_event: dict[str, Any] = player_info.get("stats") or {}
                for event_id_str, event_stats in stats_by_event.items():
                    if not isinstance(event_stats, dict):
                        continue
                    try:
                        event_api_id = int(event_id_str)
                    except (ValueError, TypeError):
                        continue

                    parsed = _parse_event_stats(event_stats)
                    if not parsed:
                        continue

                    derived = compute_derived_metrics(parsed)
                    non_null_derived = {k: v for k, v in derived.items() if v is not None}

                    values: dict[str, Any] = {
                        "player_api_id": player_api_id,
                        "event_api_id": event_api_id,
                        "team_api_id": team_api_id,
                        **parsed,
                        **non_null_derived,
                    }

                    # Build insert statement
                    stmt = pg_insert(table).values(**values)

                    # COALESCE set: keep existing Bzzoiro value; use StatsHub only to fill NULLs
                    conflict_cols = [c for c in values if c not in ("player_api_id", "event_api_id")]
                    coalesce_set = {
                        col: func.coalesce(table.c[col], stmt.excluded[col])
                        for col in conflict_cols
                        if col in table.c
                    }

                    stmt = stmt.on_conflict_do_update(
                        index_elements=["player_api_id", "event_api_id"],
                        set_=coalesce_set,
                    )
                    await session.execute(stmt)
                    total += 1

            # Commit every 200 team-level cycles to avoid long transactions
            await session.commit()

            # Gentle rate-limit: 1 req/s on average
            if i % 3 == 2:
                await asyncio.sleep(1.0)

    await session.commit()
    logger.info(
        "statshub gap-fill complete: %d rows upserted, %d fetch errors",
        total, errors,
    )
    return total
