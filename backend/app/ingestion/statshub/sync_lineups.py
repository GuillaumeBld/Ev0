"""Scraper de compositions officielles — StatsHub lineup poller.

Poll strategy:
  - job_poll_statshub_lineups runs every 15 minutes
  - Only considers fixtures with kickoff in [now+10min, now+2h]
  - For each fixture, calls lineup-status in bulk; fetches team performance
    data when status is "confirmed" or "predicted"
  - Upserts team_lineups (official / probable_statshub) + team_lineup_players
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.statshub.client import StatshubClient

logger = logging.getLogger(__name__)

_POSITION_MAP: dict[str, str] = {
    "GK": "GK",
    "CB": "DEF", "LCB": "DEF", "RCB": "DEF",
    "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "WB": "DEF",
    "CDM": "MID", "CM": "MID", "CAM": "MID",
    "LM": "MID", "RM": "MID", "LCM": "MID", "RCM": "MID", "DM": "MID",
    "LW": "FWD", "RW": "FWD", "SS": "FWD",
    "CF": "FWD", "ST": "FWD", "F": "FWD", "AM": "FWD",
}


def _map_position(raw_pos: str | None) -> str:
    if not raw_pos:
        return "MID"
    return _POSITION_MAP.get(raw_pos.upper(), "MID")


def _extract_starters(team_data: dict[str, Any], event_api_id: int) -> list[dict[str, Any]]:
    """Extract starting lineup from StatsHub team performance response.

    Primary path: currentFixturePlayerStats (pre-match data, filtered by eventId).
    Fallback path: per-event stats where minutesPlayed > 0 and substitutedIn is None
                   (post-match only — safe to use after the match is played).
    """
    event_id_str = str(event_api_id)

    current = team_data.get("currentFixturePlayerStats") or []
    if current:
        starters = [
            {
                "player_name": p.get("name", ""),
                "position": _map_position(p.get("position")),
                "jersey_number": p.get("jerseyNumber"),
            }
            for p in current
            if isinstance(p, dict) and str(p.get("eventId", "")) == event_id_str
        ]
        if starters:
            return starters

    # Fallback: derive starters from post-match per-event stats
    starters = []
    for player in team_data.get("data", []):
        if not isinstance(player, dict):
            continue
        event_stats = (player.get("stats") or {}).get(event_id_str)
        if not isinstance(event_stats, dict):
            continue
        if event_stats.get("minutesPlayed", 0) > 0 and event_stats.get("substitutedIn") is None:
            starters.append({
                "player_name": player.get("name", ""),
                "position": _map_position(event_stats.get("position")),
                "jersey_number": None,
            })

    return starters


async def _upsert_lineup(
    session: AsyncSession,
    fixture_id: int,
    team: str,
    starters: list[dict[str, Any]],
    lineup_type: str,
) -> None:
    from app.models.lineups import TeamLineup, TeamLineupPlayer

    if not starters:
        logger.warning(
            "statshub lineups: no starters for fixture=%d team=%s type=%s — skipped",
            fixture_id, team, lineup_type,
        )
        return

    stmt = (
        pg_insert(TeamLineup.__table__)
        .values(
            fixture_id=fixture_id,
            team=team,
            lineup_type=lineup_type,
            source="statshub",
            created_by="system",
        )
        .on_conflict_do_update(
            constraint="uq_team_lineup",
            set_={
                "source": "statshub",
                "updated_at": datetime.now(UTC),
            },
        )
        .returning(TeamLineup.__table__.c.id)
    )
    result = await session.execute(stmt)
    lineup_id = result.scalar_one()

    # Replace existing players for this lineup (idempotent repoll)
    await session.execute(
        delete(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == lineup_id)
    )
    for player in starters:
        session.add(TeamLineupPlayer(
            lineup_id=lineup_id,
            player_name=player["player_name"],
            position=player["position"],
            is_starter=True,
            jersey_number=player.get("jersey_number"),
        ))

    logger.info(
        "statshub lineups: upserted %s (%d players, type=%s, fixture=%d)",
        team, len(starters), lineup_type, fixture_id,
    )


async def sync_statshub_lineups(session: AsyncSession) -> int:
    """Poll StatsHub for confirmed/predicted lineups for fixtures in the next 2h.

    Skips fixtures that already have an official lineup stored.
    Returns number of lineup rows upserted (2 per match: home + away).
    """
    from app.models.bzzoiro import BzzEvent, BzzLeague
    from app.models.fixtures import Fixture
    from app.models.lineups import TeamLineup

    now = datetime.now(UTC)
    window_start = now + timedelta(minutes=10)
    window_end = now + timedelta(hours=2)

    # Fixtures already having an official lineup are excluded
    already_official = (
        select(TeamLineup.fixture_id)
        .where(TeamLineup.lineup_type == "official")
        .distinct()
        .scalar_subquery()
    )

    result = await session.execute(
        select(Fixture)
        .where(
            Fixture.status == "scheduled",
            Fixture.kickoff_utc >= window_start,
            Fixture.kickoff_utc <= window_end,
            Fixture.external_id.like("bzz_%"),
            Fixture.id.notin_(already_official),
        )
        .order_by(Fixture.kickoff_utc)
    )
    fixtures = list(result.scalars().all())

    if not fixtures:
        logger.debug("statshub lineups: no fixtures in polling window")
        return 0

    logger.info("statshub lineups: polling %d fixtures in window", len(fixtures))

    # Build event_id → fixture mapping
    event_id_to_fixture: dict[int, Fixture] = {}
    event_ids: list[int] = []
    for fixture in fixtures:
        try:
            event_api_id = int(fixture.external_id.removeprefix("bzz_"))
        except (ValueError, AttributeError):
            logger.warning("statshub lineups: unparseable external_id=%s", fixture.external_id)
            continue
        event_id_to_fixture[event_api_id] = fixture
        event_ids.append(event_api_id)

    if not event_ids:
        return 0

    # Preload BzzEvents + BzzLeague season_ids for all relevant event_ids
    bzz_events_result = await session.execute(
        select(BzzEvent).where(BzzEvent.api_id.in_(event_ids))
    )
    bzz_event_map: dict[int, BzzEvent] = {
        ev.api_id: ev for ev in bzz_events_result.scalars().all()
    }

    league_ids = {ev.league_api_id for ev in bzz_event_map.values() if ev.league_api_id}
    leagues_result = await session.execute(
        select(BzzLeague.api_id, BzzLeague.season_id).where(BzzLeague.api_id.in_(league_ids))
    )
    season_map: dict[int, int] = {
        row.api_id: row.season_id
        for row in leagues_result
        if row.season_id is not None
    }

    total_upserted = 0

    async with StatshubClient() as client:
        statuses = await client.get_lineup_status(event_ids)
        logger.info("statshub lineups: received status for %d events", len(statuses))

        for event_id_str, status in statuses.items():
            if status not in ("confirmed", "predicted"):
                continue

            try:
                event_api_id = int(event_id_str)
            except (ValueError, TypeError):
                continue

            fixture = event_id_to_fixture.get(event_api_id)
            if not fixture:
                continue

            bzz_ev = bzz_event_map.get(event_api_id)
            if not bzz_ev:
                logger.warning(
                    "statshub lineups: no BzzEvent for api_id=%d (fixture=%d) — skip",
                    event_api_id, fixture.id,
                )
                continue

            if not bzz_ev.home_team_api_id or not bzz_ev.away_team_api_id or not bzz_ev.league_api_id:
                logger.warning(
                    "statshub lineups: BzzEvent=%d missing team/league IDs — skip",
                    event_api_id,
                )
                continue

            season_id = season_map.get(bzz_ev.league_api_id)
            if season_id is None:
                logger.warning(
                    "statshub lineups: BzzLeague api_id=%d has no season_id — skip event=%d",
                    bzz_ev.league_api_id, event_api_id,
                )
                continue

            lineup_type = "official" if status == "confirmed" else "probable_statshub"

            home_data = await client.get_team_player_performance(
                bzz_ev.home_team_api_id, bzz_ev.league_api_id, season_id
            )
            away_data = await client.get_team_player_performance(
                bzz_ev.away_team_api_id, bzz_ev.league_api_id, season_id
            )

            home_starters = _extract_starters(home_data, event_api_id)
            away_starters = _extract_starters(away_data, event_api_id)

            if not home_starters and not away_starters:
                logger.warning(
                    "statshub lineups: empty starters for event=%d status=%s — skip",
                    event_api_id, status,
                )
                continue

            await _upsert_lineup(session, fixture.id, fixture.home_team, home_starters, lineup_type)
            await _upsert_lineup(session, fixture.id, fixture.away_team, away_starters, lineup_type)
            await session.commit()

            logger.info(
                "StatsHub lineup: upserted official %s vs %s (event=%d type=%s)",
                fixture.home_team, fixture.away_team, event_api_id, lineup_type,
            )
            total_upserted += 2

    return total_upserted
