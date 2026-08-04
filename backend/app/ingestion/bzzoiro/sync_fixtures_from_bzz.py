"""Auto-create and update Fixture rows from Bzzoiro BzzEvents.

Replaces job_sync_fixtures (The Odds API) as the sole fixture calendar source.

For each upcoming BzzEvent (status != finished, next N days):
  1. Resolve home/away team names from bzz_teams.
  2. Find an existing Fixture by:
     a. external_id = "bzz_<api_id>"  — direct hit, always preferred
     b. Normalized team names within ±1 day of the BzzEvent date
  3. If found and team names look like placeholders → update home_team / away_team.
  4. If not found → create a new Fixture with correct names.

Returns (created, updated) counts.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_API_IDS,
    INTERNATIONAL_LEAGUE_INTERNAL_IDS,
    TARGET_LEAGUE_API_IDS,
    TARGET_LEAGUE_INTERNAL_IDS,
)
from app.ingestion.fixture_matcher import normalize_team_name
from app.models.bzzoiro import BzzEvent, BzzTeam
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.services.season_service import compute_season

logger = logging.getLogger(__name__)

# Reverse map: bzz league id → our league key.
# Covers both external api_ids (17 for PL) and internal ids (1 for PL) because
# sync_events.py stores league.get("api_id") or league.get("id") — newer BzzAPI
# responses may omit api_id and return only the internal id.
_LEAGUE_API_ID_TO_KEY: dict[int, str] = {v: k for k, v in TARGET_LEAGUE_API_IDS.items()}
for _key, _internal_id in TARGET_LEAGUE_INTERNAL_IDS.items():
    if _internal_id not in _LEAGUE_API_ID_TO_KEY:
        _LEAGUE_API_ID_TO_KEY[_internal_id] = _key

# International competitions
for _key, _id in INTERNATIONAL_LEAGUE_API_IDS.items():
    _LEAGUE_API_ID_TO_KEY[_id] = _key
for _key, _id in INTERNATIONAL_LEAGUE_INTERNAL_IDS.items():
    if _id not in _LEAGUE_API_ID_TO_KEY:
        _LEAGUE_API_ID_TO_KEY[_id] = _key

# How far ahead to look for upcoming fixtures
_DAYS_FORWARD = 30
# How far back to look — lets us fix placeholder names on recently-finished matches
_DAYS_BACK = 7

# A team name is a placeholder when it matches these patterns.
# Generic patterns: winner/loser/tbd/tba/qf/sf/semi-final/quarter-final/match N
# WC2026-specific patterns:
#   - "2E", "1I", "G1", "H2"  → positional group qualifiers (digit+letter or letter+digit)
#   - "W73", "L101"            → winner/loser of match N (Bzzoiro internal codes)
#   - "3C/3D/3F/3G/3H"        → best-third-place combo codes (contain "/")
#   - "R32 TBD 1"             → caught by "tbd" above
_PLACEHOLDER_RE = re.compile(
    r"winner|loser|tbd|tba|qf\d*|sf\d*|semi.?final|quarter.?final|match\s*\d+"
    r"|^\d[A-Z]$"        # e.g. "2E", "1I" — Nth place of group X
    r"|^[A-Z]\d$"        # e.g. "G1", "H2" — group X place N
    r"|^[WL]\d+$"        # e.g. "W73", "L101" — winner/loser of match N
    r"|/",               # e.g. "3C/3D/3F" — best-third-place group combos
    re.IGNORECASE,
)


def _is_placeholder(name: str) -> bool:
    return not name or bool(_PLACEHOLDER_RE.search(name))


async def sync_fixtures_from_bzz(
    session: AsyncSession,
    days_forward: int = _DAYS_FORWARD,
    days_back: int = _DAYS_BACK,
) -> tuple[int, int]:
    """Create missing Fixtures and fix placeholder names from BzzEvents.

    Returns (created, updated).
    """
    now = datetime.now(UTC)
    horizon = now + timedelta(days=days_forward)
    lookback = now - timedelta(days=days_back)
    # NOTE: season is stamped per-event below from the MATCH date (compute_season),
    # never from "today" — a sync running in July for an August fixture must not
    # freeze that fixture into last season (see CORRECTION 2).

    # 1. Load BzzEvents for target leagues (past + future window)
    events_result = await session.execute(
        select(BzzEvent).where(
            BzzEvent.event_date >= lookback,
            BzzEvent.event_date <= horizon,
            BzzEvent.league_api_id.in_(list(_LEAGUE_API_ID_TO_KEY.keys())),
        ).order_by(BzzEvent.event_date)
    )
    bzz_events = list(events_result.scalars().all())

    if not bzz_events:
        logger.info("sync_fixtures_from_bzz: no upcoming BzzEvents found")
        return 0, 0

    # 2. Bulk-load team names for all referenced team api_ids
    team_ids: set[int] = set()
    for ev in bzz_events:
        if ev.home_team_api_id is not None:
            team_ids.add(ev.home_team_api_id)
        if ev.away_team_api_id is not None:
            team_ids.add(ev.away_team_api_id)

    team_map: dict[int, str] = {}
    if team_ids:
        teams_result = await session.execute(
            select(BzzTeam).where(BzzTeam.api_id.in_(team_ids))
        )
        for t in teams_result.scalars().all():
            team_map[t.api_id] = t.name

    # Build bzz_team_id → canonical_team_id lookup
    canonical_map: dict[int, int] = {}
    if team_ids:
        ct_result = await session.execute(
            select(CanonicalTeam.id, CanonicalTeam.bzz_team_id).where(
                CanonicalTeam.bzz_team_id.in_(team_ids)
            )
        )
        for ct_id, bzz_id in ct_result.all():
            canonical_map[bzz_id] = ct_id

    # 3. Load all Fixtures in the same window (including finished — to fix placeholder names)
    fixtures_result = await session.execute(
        select(Fixture).where(
            Fixture.kickoff_utc >= lookback,
            Fixture.kickoff_utc <= horizon,
        )
    )
    existing_fixtures = list(fixtures_result.scalars().all())

    # Index 1: external_id → Fixture
    by_ext_id: dict[str, Fixture] = {f.external_id: f for f in existing_fixtures if f.external_id}

    # Index 2: (norm_home, norm_away) → Fixture  (for team-name fallback)
    by_teams: dict[tuple[str, str], Fixture] = {}
    for f in existing_fixtures:
        key = (normalize_team_name(f.home_team or ""), normalize_team_name(f.away_team or ""))
        by_teams[key] = f

    created = updated = 0

    for ev in bzz_events:
        home_name = team_map.get(ev.home_team_api_id, "") if ev.home_team_api_id else ""
        away_name = team_map.get(ev.away_team_api_id, "") if ev.away_team_api_id else ""

        if not home_name or not away_name:
            logger.debug("sync_fixtures_from_bzz: skipping event %s (missing team names)", ev.api_id)
            continue

        league_key = _LEAGUE_API_ID_TO_KEY.get(ev.league_api_id or -1)
        if not league_key:
            logger.debug("sync_fixtures_from_bzz: skipping event %s (unknown league %s)", ev.api_id, ev.league_api_id)
            continue

        ext_id = f"bzz_{ev.api_id}"

        # Try lookup a: by external_id
        fixture = by_ext_id.get(ext_id)

        # Try lookup b: by normalized team names
        if fixture is None:
            key = (normalize_team_name(home_name), normalize_team_name(away_name))
            fixture = by_teams.get(key)

        if fixture is not None:
            changed = False

            # Re-stamp season from the match date — self-heals fixtures that
            # were created (or last synced) before an Aug-1 season rollover.
            correct_season = compute_season(ev.event_date.date())
            if fixture.season != correct_season:
                logger.info(
                    "sync_fixtures_from_bzz: fix season %r → %r (fixture %s)",
                    fixture.season, correct_season, fixture.id,
                )
                fixture.season = correct_season
                changed = True

            # Update placeholder team names → real names
            if _is_placeholder(fixture.home_team or "") and not _is_placeholder(home_name):
                logger.info(
                    "sync_fixtures_from_bzz: fix home_team %r → %r (fixture %s)",
                    fixture.home_team, home_name, fixture.id,
                )
                fixture.home_team = home_name
                changed = True

            if _is_placeholder(fixture.away_team or "") and not _is_placeholder(away_name):
                logger.info(
                    "sync_fixtures_from_bzz: fix away_team %r → %r (fixture %s)",
                    fixture.away_team, away_name, fixture.id,
                )
                fixture.away_team = away_name
                changed = True

            # Stamp the bzz external_id if missing
            if not fixture.external_id or not fixture.external_id.startswith("bzz_"):
                fixture.external_id = ext_id
                changed = True

            # Backfill bzz_team_ids if not set yet (also fixes pre-migration fixtures)
            if fixture.home_bzz_team_id is None and ev.home_team_api_id:
                fixture.home_bzz_team_id = ev.home_team_api_id
                changed = True
            if fixture.away_bzz_team_id is None and ev.away_team_api_id:
                fixture.away_bzz_team_id = ev.away_team_api_id
                changed = True

            # Resolve canonical team IDs via bzz_team_id (replaces text matching)
            h_bzz = fixture.home_bzz_team_id
            a_bzz = fixture.away_bzz_team_id
            if fixture.home_canonical_team_id is None and h_bzz and h_bzz in canonical_map:
                fixture.home_canonical_team_id = canonical_map[h_bzz]
                changed = True
            if fixture.away_canonical_team_id is None and a_bzz and a_bzz in canonical_map:
                fixture.away_canonical_team_id = canonical_map[a_bzz]
                changed = True

            if changed:
                session.add(fixture)
                updated += 1
                # Keep indexes current so later events don't re-match the same fixture
                by_ext_id[ext_id] = fixture
                new_key = (normalize_team_name(home_name), normalize_team_name(away_name))
                by_teams[new_key] = fixture

        else:
            # Create new Fixture
            new_fixture = Fixture(
                external_id=ext_id,
                league=league_key,
                season=compute_season(ev.event_date.date()),
                home_team=home_name,
                away_team=away_name,
                kickoff_utc=ev.event_date,
                status="scheduled",
                matchweek=ev.round_number,
                home_bzz_team_id=ev.home_team_api_id,
                away_bzz_team_id=ev.away_team_api_id,
                home_canonical_team_id=canonical_map.get(ev.home_team_api_id) if ev.home_team_api_id else None,
                away_canonical_team_id=canonical_map.get(ev.away_team_api_id) if ev.away_team_api_id else None,
            )
            session.add(new_fixture)
            created += 1
            logger.info(
                "sync_fixtures_from_bzz: created fixture %s vs %s (%s, %s)",
                home_name, away_name, league_key, ev.event_date,
            )
            # Register in indexes to avoid duplicate creation within same run
            by_ext_id[ext_id] = new_fixture
            key = (normalize_team_name(home_name), normalize_team_name(away_name))
            by_teams[key] = new_fixture

    if created or updated:
        await session.commit()

    logger.info(
        "sync_fixtures_from_bzz: %d created, %d updated (window: next %d days)",
        created, updated, days_forward,
    )
    return created, updated
