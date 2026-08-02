"""Tests for sync_fixtures_from_bzz — CORRECTION 2 (season stamping).

Bug: fixtures were stamped with `current_season()` — the season for TODAY,
computed at sync time — instead of the season implied by the match's own
kickoff date. A fixture for the August 2026 restart, synced in July 2026,
therefore got stuck with season='2025-2026' forever (the upsert never fixed
it either).

Fix: stamp `season = compute_season(event_date.date())` — driven by the
MATCH date, not by "today" — for both newly created fixtures AND existing
ones (so a later sync self-heals any mislabeled row).

These tests fully mock AsyncSession (no real DB), following the pattern
already used in tests/ingestion/bzzoiro/test_sync_bzzoiro_odds.py.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.sync_fixtures_from_bzz import sync_fixtures_from_bzz

_PL_LEAGUE_API_ID = 17  # premier_league, see app/ingestion/bzzoiro/constants.py


def _bzz_event(
    api_id: int,
    event_date: datetime,
    home_team_api_id: int = 100,
    away_team_api_id: int = 200,
    league_api_id: int = _PL_LEAGUE_API_ID,
):
    ev = MagicMock()
    ev.api_id = api_id
    ev.event_date = event_date
    ev.home_team_api_id = home_team_api_id
    ev.away_team_api_id = away_team_api_id
    ev.league_api_id = league_api_id
    ev.round_number = 1
    return ev


def _bzz_team(api_id: int, name: str):
    t = MagicMock()
    t.api_id = api_id
    t.name = name
    return t


def _existing_fixture(
    id: int,
    external_id: str,
    home_team: str,
    away_team: str,
    season: str,
    home_bzz_team_id: int | None = 100,
    away_bzz_team_id: int | None = 200,
    home_canonical_team_id: int | None = None,
    away_canonical_team_id: int | None = None,
):
    f = MagicMock()
    f.id = id
    f.external_id = external_id
    f.home_team = home_team
    f.away_team = away_team
    f.season = season
    f.home_bzz_team_id = home_bzz_team_id
    f.away_bzz_team_id = away_bzz_team_id
    f.home_canonical_team_id = home_canonical_team_id
    f.away_canonical_team_id = away_canonical_team_id
    return f


def _make_session(bzz_events, bzz_teams, canonical_rows, existing_fixtures):
    """Mock AsyncSession returning, in order:
    1. BzzEvent select   -> bzz_events
    2. BzzTeam select    -> bzz_teams   (skipped if no team_ids)
    3. CanonicalTeam select -> canonical_rows (skipped if no team_ids)
    4. Fixture select    -> existing_fixtures
    """
    results = []

    events_result = MagicMock()
    events_result.scalars.return_value.all.return_value = bzz_events
    results.append(events_result)

    if bzz_teams is not None:
        teams_result = MagicMock()
        teams_result.scalars.return_value.all.return_value = bzz_teams
        results.append(teams_result)

        ct_result = MagicMock()
        ct_result.all.return_value = canonical_rows or []
        results.append(ct_result)

    fixtures_result = MagicMock()
    fixtures_result.scalars.return_value.all.return_value = existing_fixtures
    results.append(fixtures_result)

    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_new_fixture_gets_season_from_match_date_not_today():
    """An event kicking off in August 2026 must be stamped '2026-2027' —
    regardless of what 'today' is when the sync runs."""
    event_date = datetime(2026, 8, 15, 19, 0, tzinfo=UTC)
    ev = _bzz_event(api_id=555, event_date=event_date)
    session = _make_session(
        bzz_events=[ev],
        bzz_teams=[_bzz_team(100, "Team Home"), _bzz_team(200, "Team Away")],
        canonical_rows=[],
        existing_fixtures=[],
    )

    created, updated = await sync_fixtures_from_bzz(session)

    assert created == 1
    assert updated == 0
    added_fixture = session.add.call_args[0][0]
    assert added_fixture.season == "2026-2027"


@pytest.mark.asyncio
async def test_new_fixture_before_rollover_gets_previous_season():
    """A July 2026 event must be stamped '2025-2026', proving the season is
    derived from the match date and NOT from the real wall-clock 'today'
    (which, in this environment, is already past the Aug-1 rollover)."""
    event_date = datetime(2026, 7, 20, 19, 0, tzinfo=UTC)
    ev = _bzz_event(api_id=556, event_date=event_date)
    session = _make_session(
        bzz_events=[ev],
        bzz_teams=[_bzz_team(100, "Team Home"), _bzz_team(200, "Team Away")],
        canonical_rows=[],
        existing_fixtures=[],
    )

    created, updated = await sync_fixtures_from_bzz(session)

    assert created == 1
    added_fixture = session.add.call_args[0][0]
    assert added_fixture.season == "2025-2026"


@pytest.mark.asyncio
async def test_upsert_corrects_season_on_existing_mislabeled_fixture():
    """A fixture created back in July (season stamped '2025-2026') for a match
    that actually kicks off in August 2026 must be self-healed to '2026-2027'
    on the next sync."""
    event_date = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
    ev = _bzz_event(api_id=777, event_date=event_date)
    existing = _existing_fixture(
        id=42,
        external_id="bzz_777",
        home_team="Team Home",
        away_team="Team Away",
        season="2025-2026",  # mislabeled — stamped when synced pre-rollover
    )
    session = _make_session(
        bzz_events=[ev],
        bzz_teams=[_bzz_team(100, "Team Home"), _bzz_team(200, "Team Away")],
        canonical_rows=[],
        existing_fixtures=[existing],
    )

    created, updated = await sync_fixtures_from_bzz(session)

    assert created == 0
    assert updated == 1
    assert existing.season == "2026-2027"


@pytest.mark.asyncio
async def test_upsert_leaves_correctly_labeled_season_untouched():
    """No spurious 'changed' when the season is already correct."""
    event_date = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
    ev = _bzz_event(api_id=778, event_date=event_date)
    existing = _existing_fixture(
        id=43,
        external_id="bzz_778",
        home_team="Team Home",
        away_team="Team Away",
        season="2026-2027",  # already correct
    )
    session = _make_session(
        bzz_events=[ev],
        bzz_teams=[_bzz_team(100, "Team Home"), _bzz_team(200, "Team Away")],
        canonical_rows=[],
        existing_fixtures=[existing],
    )

    created, updated = await sync_fixtures_from_bzz(session)

    assert created == 0
    assert updated == 0
    assert existing.season == "2026-2027"
