"""Tests for sync_bzzoiro_lineups module."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.ingestion.bzzoiro.sync_bzzoiro_lineups import (
    _map_position,
    sync_bzzoiro_lineups,
)


def test_map_position():
    assert _map_position("G") == "GK"
    assert _map_position("D") == "DEF"
    assert _map_position("M") == "MID"
    assert _map_position("F") == "FWD"
    assert _map_position("X") == "MID"  # unknown → default MID
    assert _map_position(None) == "MID"


@pytest.mark.asyncio
async def test_sync_bzzoiro_lineups_creates_lineup():
    """A bzz_event with lineups creates TeamLineup + players for both teams."""
    lineups_json = {
        "home": {
            "players": [
                {"name": "K. Mbappé",  "position": "F", "jersey_number": "7",  "sub_in": None},
                {"name": "T. Hernandez","position": "D", "jersey_number": "21", "sub_in": None},
            ]
        },
        "away": {
            "players": [
                {"name": "B. Saka",    "position": "F", "jersey_number": "7",  "sub_in": None},
                {"name": "M. Ødegaard","position": "M", "jersey_number": "8",  "sub_in": "65"},
            ]
        }
    }

    bzz_event = MagicMock()
    bzz_event.api_id = 206718
    bzz_event.lineups = lineups_json

    fixture = MagicMock()
    fixture.id = 42
    fixture.home_team = "Paris Saint-Germain"
    fixture.away_team = "Arsenal"
    fixture.external_id = "bzz_206718"

    # Session returns: bzz_events query, fixture query, two existing-lineup queries
    bzz_result = MagicMock()
    bzz_result.scalars.return_value.all.return_value = [bzz_event]

    fixture_result = MagicMock()
    fixture_result.scalar_one_or_none.return_value = fixture

    no_lineup_result = MagicMock()
    no_lineup_result.scalar_one_or_none.return_value = None  # no existing lineup

    added_objects = []
    call_count = {"n": 0}

    async def execute_side(stmt, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return bzz_result
        if call_count["n"] == 2:
            return fixture_result
        return no_lineup_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side)
    session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_bzzoiro_lineups(session)

    assert count == 2  # two teams processed
    assert session.commit.called
    # Two TeamLineup objects added (home + away)
    from app.models.lineups import TeamLineup
    lineups_added = [o for o in added_objects if isinstance(o, TeamLineup)]
    assert len(lineups_added) == 2


@pytest.mark.asyncio
async def test_sync_bzzoiro_lineups_skips_null_lineups():
    """bzz_events with null lineups are silently skipped."""
    bzz_event = MagicMock()
    bzz_event.api_id = 999
    bzz_event.lineups = None

    bzz_result = MagicMock()
    bzz_result.scalars.return_value.all.return_value = [bzz_event]

    session = MagicMock()
    session.execute = AsyncMock(return_value=bzz_result)
    session.commit = AsyncMock()

    count = await sync_bzzoiro_lineups(session)

    assert count == 0
