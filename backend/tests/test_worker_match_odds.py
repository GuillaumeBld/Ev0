"""Tests for match-level odds ingestion integration in job_snapshot_odds."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.match_odds import MatchOddsRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(event_id: str = "evt_1", market_type: str = "h2h", outcome: str = "home") -> MatchOddsRow:
    return MatchOddsRow(
        event_id=event_id,
        bookmaker="betfair",
        market_type=market_type,
        outcome=outcome,
        odds=1.9,
        snapshot_utc=datetime(2026, 4, 2, 12, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_odds_rows_upserted_into_db():
    """job_snapshot_odds calls ingest_match_odds_for_league and upserts rows."""
    fake_rows = [
        _make_row("evt_1", "h2h", "home"),
        _make_row("evt_1", "h2h", "draw"),
        _make_row("evt_1", "h2h", "away"),
    ]

    # Fake fixture with odds_api_event_id matching the rows
    fake_fixture = MagicMock()
    fake_fixture.id = 42
    fake_fixture.odds_api_event_id = "evt_1"
    fake_fixture.league = "ligue_1"

    # We capture what gets executed on the session
    executed_stmts = []

    async def mock_execute(stmt, *args, **kwargs):
        executed_stmts.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [fake_fixture]
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()

    # Context manager that returns mock_session
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.worker.settings") as mock_settings, \
         patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
         patch("app.worker.ingest_odds_for_league", new=AsyncMock(return_value=([], []))), \
         patch("app.worker.ingest_match_odds_for_league", new=AsyncMock(return_value=(fake_rows, []))) as mock_ingest, \
         patch("app.worker.async_session", return_value=mock_session_ctx):

        mock_settings.odds_api_key = "test_key"

        from app.worker import job_snapshot_odds
        await job_snapshot_odds()

    # ingest_match_odds_for_league should have been called for each active league
    assert mock_ingest.called
    call_leagues = [c.args[0] for c in mock_ingest.call_args_list]
    assert "ligue_1" in call_leagues

    # session.execute should have been called (for fixture lookup + insert)
    assert len(executed_stmts) >= 2, "Expected at least fixture lookup + INSERT statement"

    # session.commit should have been called
    mock_session.commit.assert_called()


@pytest.mark.asyncio
async def test_match_odds_no_rows_skips_insert():
    """When ingest_match_odds_for_league returns empty rows, no DB insert happens."""
    executed_stmts = []

    async def mock_execute(stmt, *args, **kwargs):
        executed_stmts.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.worker.settings") as mock_settings, \
         patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
         patch("app.worker.ingest_odds_for_league", new=AsyncMock(return_value=([], []))), \
         patch("app.worker.ingest_match_odds_for_league", new=AsyncMock(return_value=([], []))), \
         patch("app.worker.async_session", return_value=mock_session_ctx):

        mock_settings.odds_api_key = "test_key"

        from app.worker import job_snapshot_odds
        await job_snapshot_odds()

    # No insert should have been executed
    # (execute is called once for fixture lookup when rows present, but here rows=[] so none)
    insert_stmts = [s for s in executed_stmts if hasattr(s, "is_insert") and s.is_insert]
    assert len(insert_stmts) == 0


@pytest.mark.asyncio
async def test_match_odds_errors_logged_but_job_continues():
    """Errors returned by ingest_match_odds_for_league are logged and job continues."""
    errors = [{"fixture_id": 99, "error": "HTTP 404"}]

    async def mock_execute(stmt, *args, **kwargs):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.worker.settings") as mock_settings, \
         patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
         patch("app.worker.ingest_odds_for_league", new=AsyncMock(return_value=([], []))), \
         patch("app.worker.ingest_match_odds_for_league", new=AsyncMock(return_value=([], errors))), \
         patch("app.worker.async_session", return_value=mock_session_ctx):

        mock_settings.odds_api_key = "test_key"

        from app.worker import job_snapshot_odds
        # Should not raise even with errors
        await job_snapshot_odds()


@pytest.mark.asyncio
async def test_match_odds_skips_rows_without_fixture_match():
    """Rows whose event_id has no matching fixture are silently skipped."""
    fake_rows = [_make_row("evt_orphan", "h2h", "home")]

    executed_stmts = []

    async def mock_execute(stmt, *args, **kwargs):
        executed_stmts.append(stmt)
        result = MagicMock()
        # No fixture found for "evt_orphan"
        result.scalars.return_value.all.return_value = []
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.worker.settings") as mock_settings, \
         patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
         patch("app.worker.ingest_odds_for_league", new=AsyncMock(return_value=([], []))), \
         patch("app.worker.ingest_match_odds_for_league", new=AsyncMock(return_value=(fake_rows, []))), \
         patch("app.worker.async_session", return_value=mock_session_ctx):

        mock_settings.odds_api_key = "test_key"

        from app.worker import job_snapshot_odds
        await job_snapshot_odds()

    # No commit should have been called (no rows to insert)
    mock_session.commit.assert_not_called()
