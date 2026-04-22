"""Tests for Bzzoiro sync jobs registered in the background worker."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# job_sync_bzzoiro_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_events_no_key():
    """When bzzoiro_api_key is None, job returns early without calling sync_events."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_events"
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_bzzoiro_events

        await job_sync_bzzoiro_events()

        mock_sync.assert_not_called()


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_events_calls_sync():
    """When key is present, sync_events is called with a session and client."""
    mock_result = {"synced": 5, "errors": 0}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.async_session", return_value=mock_session_ctx
    ), patch("app.worker.BzzoiroClient", return_value=mock_client), patch(
        "app.worker.sync_events", new_callable=AsyncMock, return_value=mock_result
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = "test-key-123"

        from app.worker import job_sync_bzzoiro_events

        await job_sync_bzzoiro_events()

        mock_sync.assert_called_once_with(mock_session, mock_client)


# ---------------------------------------------------------------------------
# job_sync_bzzoiro_reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_reference_no_key():
    """When bzzoiro_api_key is None, job returns early without calling sync_leagues."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_leagues"
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_bzzoiro_reference

        await job_sync_bzzoiro_reference()

        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# job_sync_bzzoiro_players
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_players_no_key():
    """When bzzoiro_api_key is None, job returns early without calling sync_players."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_players"
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_bzzoiro_players

        await job_sync_bzzoiro_players()

        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# job_sync_bzzoiro_player_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_player_stats_no_key():
    """When bzzoiro_api_key is None, job returns early without calling sync_player_stats."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_player_stats"
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_bzzoiro_player_stats

        await job_sync_bzzoiro_player_stats()

        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# job_sync_bzzoiro_predictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_sync_bzzoiro_predictions_no_key():
    """When bzzoiro_api_key is None, job returns early without calling sync_predictions."""
    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker.sync_predictions"
    ) as mock_sync:
        mock_settings.bzzoiro_api_key = None
        from app.worker import job_sync_bzzoiro_predictions

        await job_sync_bzzoiro_predictions()

        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# job_aggregate_season_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_aggregate_season_stats_calls_aggregate():
    """aggregate_all_leagues is called with a session."""
    mock_result = {"ligue_1": {"aggregated": 10}, "premier_league": {"aggregated": 10}}

    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.worker.async_session", return_value=mock_session_ctx
    ), patch(
        "app.worker.aggregate_all_leagues", new_callable=AsyncMock, return_value=mock_result
    ) as mock_aggregate:
        from app.worker import job_aggregate_season_stats

        await job_aggregate_season_stats()

        mock_aggregate.assert_called_once_with(mock_session)


# ---------------------------------------------------------------------------
# sync_fixture_status_from_bzz
# ---------------------------------------------------------------------------


def _make_bzz_team(api_id: int, name: str) -> MagicMock:
    team = MagicMock()
    team.api_id = api_id
    team.name = name
    return team


def _make_bzz_event(
    home_team_api_id: int,
    away_team_api_id: int,
    event_date: datetime,
    status: str = "finished",
    home_score: int | None = None,
    away_score: int | None = None,
) -> MagicMock:
    ev = MagicMock()
    ev.home_team_api_id = home_team_api_id
    ev.away_team_api_id = away_team_api_id
    ev.event_date = event_date
    ev.status = status
    ev.home_score = home_score
    ev.away_score = away_score
    return ev


def _make_fixture(
    home_team: str,
    away_team: str,
    kickoff_utc: datetime,
    status: str = "scheduled",
    home_score: int | None = None,
    away_score: int | None = None,
) -> MagicMock:
    fix = MagicMock()
    fix.home_team = home_team
    fix.away_team = away_team
    fix.kickoff_utc = kickoff_utc
    fix.status = status
    fix.home_score = home_score
    fix.away_score = away_score
    return fix


def _make_session_for_sync(fixtures, bzz_events, bzz_teams) -> MagicMock:
    """Build a mock session whose execute() returns canned data per call order."""
    call_count = {"n": 0}

    def _scalars_result(rows):
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        return result

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Query 1: non-finished fixtures
            return _scalars_result(fixtures)
        elif call_count["n"] == 2:
            # Query 2: BzzEvents in date range
            return _scalars_result(bzz_events)
        elif call_count["n"] == 3:
            # Query 3: BzzTeams
            return _scalars_result(bzz_teams)
        return _scalars_result([])

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_sync_fixture_status_updates_kickoff():
    """BzzEvent with correct date updates stale Fixture.kickoff_utc."""
    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz

    stale_kickoff = datetime(2026, 4, 22, 19, 0, 0, tzinfo=UTC)
    real_kickoff = datetime(2026, 4, 21, 19, 0, 0, tzinfo=UTC)

    home_team = _make_bzz_team(api_id=10, name="Real Madrid")
    away_team = _make_bzz_team(api_id=20, name="Deportivo Alaves")

    fixture = _make_fixture(
        home_team="Real Madrid",
        away_team="Deportivo Alaves",
        kickoff_utc=stale_kickoff,
        status="scheduled",
    )

    bzz_event = _make_bzz_event(
        home_team_api_id=10,
        away_team_api_id=20,
        event_date=real_kickoff,
        status="finished",
        home_score=2,
        away_score=1,
    )

    session = _make_session_for_sync(
        fixtures=[fixture],
        bzz_events=[bzz_event],
        bzz_teams=[home_team, away_team],
    )

    result = await sync_fixture_status_from_bzz(session)

    assert result == 1
    assert fixture.kickoff_utc == real_kickoff
    assert fixture.status == "finished"
    assert fixture.home_score == 2
    assert fixture.away_score == 1
    session.add.assert_called_once_with(fixture)
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_sync_fixture_status_ignores_out_of_window_event():
    """BzzEvent >5 days away from fixture kickoff must not update it.

    The date-window filter is applied at the DB query level (BzzEvent.event_date
    between earliest and latest). The mock simulates this by returning an empty
    list for query 2, as the DB would when no events fall in the ±5-day window.
    """
    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz

    fixture_kickoff = datetime(2026, 4, 21, 19, 0, tzinfo=UTC)
    # BzzEvent is 6 days away — the DB query would exclude it (outside ±5-day window)

    fixture = _make_fixture("Real Madrid", "Alaves", kickoff_utc=fixture_kickoff, status="scheduled")
    home_team = _make_bzz_team(101, "Real Madrid")
    away_team = _make_bzz_team(102, "Alaves")

    # Pass empty bzz_events — simulating DB returning nothing because the event
    # falls outside the ±5-day window used in the BzzEvent date range query.
    session = _make_session_for_sync([fixture], [], [home_team, away_team])

    result = await sync_fixture_status_from_bzz(session)

    assert result == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_sync_fixture_status_updates_status_without_kickoff_change():
    """When kickoff matches (diff < 30min), only status is updated."""
    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz

    kickoff = datetime(2026, 4, 21, 19, 0, tzinfo=UTC)
    # BzzEvent is only 5 minutes off — below the 30-min threshold
    bzz_kickoff = datetime(2026, 4, 21, 19, 5, tzinfo=UTC)

    fixture = _make_fixture("PSG", "Lyon", kickoff_utc=kickoff, status="scheduled")
    home_team = _make_bzz_team(201, "Paris Saint-Germain")  # normalizes to "paris-saint-germain"
    away_team = _make_bzz_team(202, "Lyon")
    event = _make_bzz_event(
        home_team_api_id=201,
        away_team_api_id=202,
        event_date=bzz_kickoff,
        status="inprogress",
        home_score=None,
        away_score=None,
    )

    session = _make_session_for_sync([fixture], [event], [home_team, away_team])

    result = await sync_fixture_status_from_bzz(session)

    assert result == 1
    assert fixture.kickoff_utc == kickoff  # NOT changed (diff < 30 min)
    assert fixture.status == "live"  # "inprogress" → "live"
    session.add.assert_called()


@pytest.mark.asyncio
async def test_sync_fixture_status_no_match_leaves_unchanged():
    """Fixture with no matching BzzEvent is not modified."""
    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz

    kickoff = datetime(2026, 4, 22, 19, 0, 0, tzinfo=UTC)

    fixture = _make_fixture(
        home_team="Team X",
        away_team="Team Y",
        kickoff_utc=kickoff,
        status="scheduled",
    )

    home_team = _make_bzz_team(api_id=30, name="Team A")
    away_team = _make_bzz_team(api_id=40, name="Team B")

    bzz_event = _make_bzz_event(
        home_team_api_id=30,
        away_team_api_id=40,
        event_date=kickoff,
        status="finished",
        home_score=1,
        away_score=0,
    )

    session = _make_session_for_sync(
        fixtures=[fixture],
        bzz_events=[bzz_event],
        bzz_teams=[home_team, away_team],
    )

    result = await sync_fixture_status_from_bzz(session)

    assert result == 0
    session.add.assert_not_called()
    # commit should not be called when nothing changed
    session.commit.assert_not_called()
