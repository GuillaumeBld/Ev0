"""Sync match events (goals, assists) from Bzzoiro incidents into match_events.

Replaces the ESPN-based job_sync_match_events. Works for all leagues covered by
Bzzoiro, including WC2026 (league_id=27).

Flow:
  1. Find finished Fixtures with external_id="bzz_{api_id}" that have no match_events yet.
  2. Call /api/v2/events/{api_id}/incidents/ for each.
  3. Parse goals/assists/own_goals and store into match_events.
  4. Store a sentinel row for 0-0 matches so the fixture isn't reprocessed.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent

logger = logging.getLogger(__name__)

# Sentinel player_name used to mark a fixture as processed when it had 0 goals.
_SENTINEL = "__processed__"
_SENTINEL_TYPE = "match_processed"

# Sentinel stocké quand Bzzoiro renvoie un 404 permanent (événement supprimé
# upstream) : la fixture sort de la sélection au lieu d'être re-fetchée en
# boucle. auto_settle et le settle autopilot traitent ce type en VOID.
SENTINEL_UNAVAILABLE = "__incidents_unavailable__"
SENTINEL_UNAVAILABLE_TYPE = "incidents_unavailable"


def _parse_incidents(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract goal/assist rows from Bzzoiro incident list.

    Returns list of dicts compatible with MatchEvent fields:
        player_name, event_type, minute
    """
    rows: list[dict[str, Any]] = []

    for inc in raw:
        inc_type = inc.get("incidentType", "")
        if inc_type not in ("goal", "addedGoal"):
            continue

        scorer = inc.get("player") or {}
        scorer_name = scorer.get("name") or scorer.get("shortName") or ""
        if not scorer_name:
            continue

        minute: int | None = inc.get("time") or inc.get("minute")
        is_own_goal: bool = inc.get("isOwnGoal", False)

        rows.append({
            "player_name": scorer_name,
            "event_type": "own_goal" if is_own_goal else "goal",
            "minute": minute,
        })

        if not is_own_goal:
            assist = inc.get("assist1") or {}
            assist_name = assist.get("name") or assist.get("shortName") or ""
            if assist_name:
                rows.append({
                    "player_name": assist_name,
                    "event_type": "assist",
                    "minute": minute,
                })

    return rows


async def _store_events(
    session: AsyncSession,
    fixture_id: int,
    events: list[dict[str, Any]],
) -> int:
    """Upsert events into match_events. Returns number of rows inserted/updated."""
    count = 0
    for ev in events:
        stmt = (
            pg_insert(MatchEvent)
            .values(
                fixture_id=fixture_id,
                player_name=ev["player_name"],
                event_type=ev["event_type"],
                minute=ev.get("minute"),
            )
            .on_conflict_do_nothing(constraint="uq_match_event")
        )
        result = await session.execute(stmt)
        count += result.rowcount
    return count


async def sync_incidents(
    session: AsyncSession,
    client: BzzoiroClient,
    limit: int = 100,
) -> int:
    """Fetch and store incidents for finished fixtures that have no events yet.

    Args:
        limit: Max fixtures to process per run (prevents runaway on large backlogs).

    Returns:
        Number of fixtures successfully processed.
    """
    # Fixtures already processed (have at least one match_events row)
    processed_subq = select(MatchEvent.fixture_id).distinct().subquery()

    result = await session.execute(
        select(Fixture)
        .where(
            Fixture.status == "finished",
            Fixture.external_id.like("bzz_%"),
            Fixture.id.notin_(select(processed_subq.c.fixture_id)),
        )
        .order_by(Fixture.kickoff_utc.desc())
        .limit(limit)
    )
    fixtures = list(result.scalars().all())

    if not fixtures:
        logger.info("sync_incidents: no finished fixtures missing events")
        return 0

    logger.info("sync_incidents: %d fixtures to process", len(fixtures))
    processed = 0

    for fixture in fixtures:
        # external_id is "bzz_<api_id>"
        try:
            bzz_api_id = int(fixture.external_id.removeprefix("bzz_"))
        except (ValueError, AttributeError):
            logger.warning("sync_incidents: cannot parse external_id=%s", fixture.external_id)
            continue

        try:
            data = await client.get_page(f"/api/v2/events/{bzz_api_id}/incidents/")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # 404 permanent (événement supprimé côté Bzzoiro) : sentinel
                # pour sortir la fixture de la sélection — fin de la boucle.
                await _store_events(session, fixture.id, [{
                    "player_name": SENTINEL_UNAVAILABLE,
                    "event_type": SENTINEL_UNAVAILABLE_TYPE,
                    "minute": None,
                }])
                await session.commit()
                logger.warning(
                    "sync_incidents: fixture %d (bzz_id=%d) → 404 permanent, "
                    "sentinel 'incidents_unavailable' stocké — plus de retry",
                    fixture.id, bzz_api_id,
                )
                processed += 1
            else:
                logger.warning(
                    "sync_incidents: failed to fetch incidents for fixture %d (bzz_id=%d): %s",
                    fixture.id, bzz_api_id, exc,
                )
            continue
        except Exception as exc:
            logger.warning(
                "sync_incidents: failed to fetch incidents for fixture %d (bzz_id=%d): %s",
                fixture.id, bzz_api_id, exc,
            )
            continue

        raw_incidents = data if isinstance(data, list) else data.get("incidents", [])
        events = _parse_incidents(raw_incidents)

        if events:
            stored = await _store_events(session, fixture.id, events)
            logger.info(
                "sync_incidents: %s vs %s → %d events stored",
                fixture.home_team, fixture.away_team, stored,
            )
        else:
            # 0-0 or no scoringPlay — store sentinel so we don't retry
            await _store_events(session, fixture.id, [{
                "player_name": _SENTINEL,
                "event_type": _SENTINEL_TYPE,
                "minute": None,
            }])
            logger.debug(
                "sync_incidents: %s vs %s → 0 goals, sentinel stored",
                fixture.home_team, fixture.away_team,
            )

        processed += 1
        await session.commit()

    logger.info("sync_incidents: processed %d/%d fixtures", processed, len(fixtures))
    return processed
