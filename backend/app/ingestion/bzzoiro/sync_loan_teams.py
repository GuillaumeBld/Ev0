"""Detect and persist loan team overrides for bzz_players.

For each player, compute the team they appeared for most in the current
season (from bzz_player_match_stats). If that team differs from
current_team_api_id (i.e., the player is on loan), set loan_team_api_id
and loan_team_name. Clear those fields when the dominant team matches the
parent club again.

This runs after aggregate_all_leagues so match stats are fresh.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_ID_LIST
from app.services.season_service import current_season, season_start

logger = logging.getLogger(__name__)

_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # [1, 3, 4, 5, 6, 7]


async def sync_loan_teams(session: AsyncSession) -> int:
    """Update loan_team_* for all on-loan players. Returns number of rows changed."""

    # Step 1 — find each player's dominant team this season from match stats.
    #
    # team_api_id on bzz_player_match_stats is often NULL (Bzzoiro doesn't always
    # return it). Instead we derive the player's team from bzz_events: UNION of
    # home_team_api_id and away_team_api_id across the player's matches. The
    # player's own team appears in EVERY match (as home OR away); each opponent
    # appears only once. So the highest-count team = the player's actual team.
    dominant_stmt = text("""
        WITH event_teams AS (
            -- Each match contributes both its home and away team for the player
            SELECT pms.player_api_id, e.home_team_api_id AS team_api_id
            FROM bzz_player_match_stats pms
            JOIN bzz_events e ON e.api_id = pms.event_api_id
            WHERE e.event_date >= :season_start
              AND e.league_api_id = ANY(:league_ids)
              AND pms.minutes_played > 0
            UNION ALL
            SELECT pms.player_api_id, e.away_team_api_id
            FROM bzz_player_match_stats pms
            JOIN bzz_events e ON e.api_id = pms.event_api_id
            WHERE e.event_date >= :season_start
              AND e.league_api_id = ANY(:league_ids)
              AND pms.minutes_played > 0
        ),
        team_counts AS (
            SELECT player_api_id, team_api_id, COUNT(*) AS cnt
            FROM event_teams
            WHERE team_api_id IS NOT NULL
            GROUP BY player_api_id, team_api_id
        )
        SELECT DISTINCT ON (player_api_id)
            player_api_id,
            team_api_id,
            cnt
        FROM team_counts
        ORDER BY player_api_id, cnt DESC
    """)
    season_start_date = season_start(await current_season(session))
    result = await session.execute(
        dominant_stmt,
        {"season_start": season_start_date, "league_ids": _LEAGUE_IDS},
    )
    dominant_rows = result.fetchall()

    if not dominant_rows:
        logger.info("sync_loan_teams: no match stats found, nothing to update")
        return 0

    # Step 2 — split into loan (dominant ≠ parent club) and non-loan buckets.
    # Also fetch current_team_api_id to compare.
    current_teams_stmt = text("""
        SELECT api_id, current_team_api_id FROM bzz_players
        WHERE api_id = ANY(:player_ids)
    """)
    player_ids = [r[0] for r in dominant_rows]
    ct_result = await session.execute(current_teams_stmt, {"player_ids": player_ids})
    current_team_by_player = {row[0]: row[1] for row in ct_result.fetchall()}

    # Fetch team names in one query.
    team_ids = list({r[1] for r in dominant_rows})
    team_names_stmt = text("""
        SELECT api_id, name FROM bzz_teams WHERE api_id = ANY(:team_ids)
    """)
    tn_result = await session.execute(team_names_stmt, {"team_ids": team_ids})
    team_name_by_id = {row[0]: row[1] for row in tn_result.fetchall()}

    on_loan: list[tuple[int, int, str]] = []   # (player_api_id, loan_team_api_id, loan_team_name)
    back_home: list[int] = []                  # player_api_id

    for player_api_id, dominant_team_api_id, _ in dominant_rows:
        parent_team_api_id = current_team_by_player.get(player_api_id)
        team_name = team_name_by_id.get(dominant_team_api_id, "")
        if dominant_team_api_id != parent_team_api_id:
            on_loan.append((player_api_id, dominant_team_api_id, team_name))
        else:
            back_home.append(player_api_id)

    changed = 0

    # Step 3 — set loan fields for on-loan players.
    if on_loan:
        set_stmt = text("""
            UPDATE bzz_players
            SET loan_team_api_id = :loan_team_api_id,
                loan_team_name    = :loan_team_name
            WHERE api_id = :player_api_id
              AND (
                  loan_team_api_id IS DISTINCT FROM :loan_team_api_id
                  OR loan_team_name IS DISTINCT FROM :loan_team_name
              )
        """)
        for player_api_id, loan_team_api_id, loan_team_name in on_loan:
            r = await session.execute(set_stmt, {
                "player_api_id": player_api_id,
                "loan_team_api_id": loan_team_api_id,
                "loan_team_name": loan_team_name,
            })
            changed += r.rowcount

    # Step 4 — clear loan fields for players now back at parent club.
    if back_home:
        clear_stmt = text("""
            UPDATE bzz_players
            SET loan_team_api_id = NULL,
                loan_team_name    = NULL
            WHERE api_id = ANY(:player_ids)
              AND (loan_team_api_id IS NOT NULL OR loan_team_name IS NOT NULL)
        """)
        r = await session.execute(clear_stmt, {"player_ids": back_home})
        changed += r.rowcount

    await session.commit()
    logger.info(
        "sync_loan_teams: %d on loan, %d back home, %d rows changed",
        len(on_loan), len(back_home), changed,
    )
    return changed
