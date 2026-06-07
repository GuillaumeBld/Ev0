"""Tests for sync_bzzoiro_odds module."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.ingestion.bzzoiro.sync_bzzoiro_odds import sync_bzzoiro_odds


def _make_fixture(id: int, external_id: str, kickoff_utc):
    f = MagicMock()
    f.id = id
    f.external_id = external_id
    f.kickoff_utc = kickoff_utc
    return f


def _make_session(fixtures: list) -> MagicMock:
    fixture_result = MagicMock()
    fixture_result.scalars.return_value.all.return_value = fixtures

    upsert_result = MagicMock()
    call_count = {"n": 0}

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return fixture_result
        return upsert_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_sync_bzzoiro_odds_writes_pinnacle_h2h():
    """Pinnacle 1x2 odds are written to match_odds_snapshots."""
    from datetime import UTC, datetime, timedelta

    fixture = _make_fixture(
        id=42,
        external_id="bzz_206718",
        kickoff_utc=datetime.now(UTC) + timedelta(hours=3),
    )
    session = _make_session([fixture])

    pinnacle_odds = [
        {"bookmaker_code": "pinnacle", "market": "1x2", "outcome": "HOME", "decimal_odds": 2.52},
        {"bookmaker_code": "pinnacle", "market": "1x2", "outcome": "DRAW", "decimal_odds": 3.32},
        {"bookmaker_code": "pinnacle", "market": "1x2", "outcome": "AWAY", "decimal_odds": 3.11},
        {"bookmaker_code": "bet365",   "market": "1x2", "outcome": "HOME", "decimal_odds": 2.45},  # ignored
    ]
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"odds": pinnacle_odds, "event": {}, "count": 4})

    count = await sync_bzzoiro_odds(session, client, bookmakers=["pinnacle"])

    assert count == 3
    assert session.commit.called
    client.get_page.assert_called_once_with("/api/odds/", params={"event": 206718})


@pytest.mark.asyncio
async def test_sync_bzzoiro_odds_skips_non_bzz_fixtures():
    """No non-bzz fixtures exist — the SQL filter excludes them, get_page is never called."""
    session = MagicMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()

    client = MagicMock()
    client.get_page = AsyncMock()

    count = await sync_bzzoiro_odds(session, client, bookmakers=["pinnacle"])

    assert count == 0
    client.get_page.assert_not_called()
    assert session.commit.called


@pytest.mark.asyncio
async def test_sync_bzzoiro_odds_handles_api_error_gracefully():
    """An API error for one fixture does not abort the whole sync."""
    from datetime import UTC, datetime, timedelta

    fixture = _make_fixture(
        id=1,
        external_id="bzz_999",
        kickoff_utc=datetime.now(UTC) + timedelta(hours=2),
    )
    session = _make_session([fixture])
    client = MagicMock()
    client.get_page = AsyncMock(side_effect=Exception("network error"))

    count = await sync_bzzoiro_odds(session, client, bookmakers=["pinnacle"])

    assert count == 0
    assert session.commit.called


@pytest.mark.asyncio
async def test_sync_bzzoiro_odds_skips_unparseable_external_id():
    """A fixture with a non-numeric bzz_ suffix is skipped via ValueError."""
    from datetime import UTC, datetime, timedelta

    # The SQL LIKE filter passes, but int("bzz_abc") fails
    fixture = _make_fixture(
        id=77,
        external_id="bzz_abc",  # non-numeric suffix
        kickoff_utc=datetime.now(UTC) + timedelta(hours=3),
    )
    session = _make_session([fixture])
    client = MagicMock()
    client.get_page = AsyncMock()

    count = await sync_bzzoiro_odds(session, client, bookmakers=["pinnacle"])

    assert count == 0
    client.get_page.assert_not_called()
    assert session.commit.called
