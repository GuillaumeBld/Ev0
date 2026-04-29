"""Unit tests for StatsHub lineup scraper (sync_lineups.py)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.statshub.sync_lineups import _extract_starters, _map_position


# ── _map_position ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("GK", "GK"),
    ("gk", "GK"),
    ("CB", "DEF"),
    ("LB", "DEF"),
    ("RWB", "DEF"),
    ("CDM", "MID"),
    ("CAM", "MID"),
    ("LCM", "MID"),
    ("ST", "FWD"),
    ("LW", "FWD"),
    ("CF", "FWD"),
    ("F", "FWD"),
    ("AM", "FWD"),
    ("UNKNOWN", "MID"),   # default
    ("", "MID"),          # empty → default
    (None, "MID"),        # None → default
])
def test_map_position(raw, expected):
    assert _map_position(raw) == expected


# ── _extract_starters: currentFixturePlayerStats path ─────────────

def test_extract_starters_current_fixture():
    team_data = {
        "currentFixturePlayerStats": [
            {"name": "Player A", "position": "ST", "jerseyNumber": 9, "eventId": 12345},
            {"name": "Player B", "position": "CB", "jerseyNumber": 5, "eventId": 12345},
            {"name": "Player C", "position": "GK", "jerseyNumber": 1, "eventId": 99999},  # other match
        ],
        "data": [],
    }
    starters = _extract_starters(team_data, event_api_id=12345)
    assert len(starters) == 2
    names = [s["player_name"] for s in starters]
    assert "Player A" in names
    assert "Player B" in names
    assert all(s["position"] in ("GK", "DEF", "MID", "FWD") for s in starters)
    # Player C is for a different event, must not appear
    assert "Player C" not in names


def test_extract_starters_jersey_numbers_carried():
    team_data = {
        "currentFixturePlayerStats": [
            {"name": "Keeper", "position": "GK", "jerseyNumber": 1, "eventId": 42},
        ],
        "data": [],
    }
    starters = _extract_starters(team_data, event_api_id=42)
    assert starters[0]["jersey_number"] == 1


# ── _extract_starters: fallback path ─────────────────────────────

def test_extract_starters_fallback_no_current():
    """When currentFixturePlayerStats is empty, use per-event stats."""
    team_data = {
        "currentFixturePlayerStats": [],
        "data": [
            {
                "name": "Starter X",
                "stats": {
                    "77777": {
                        "minutesPlayed": 90,
                        "substitutedIn": None,
                        "position": "CM",
                    }
                },
            },
            {
                "name": "Sub Y",
                "stats": {
                    "77777": {
                        "minutesPlayed": 30,
                        "substitutedIn": 60,  # came on as sub
                        "position": "LW",
                    }
                },
            },
            {
                "name": "Unused Z",
                "stats": {
                    "77777": {
                        "minutesPlayed": 0,
                        "substitutedIn": None,
                        "position": "GK",
                    }
                },
            },
        ],
    }
    starters = _extract_starters(team_data, event_api_id=77777)
    assert len(starters) == 1
    assert starters[0]["player_name"] == "Starter X"
    assert starters[0]["position"] == "MID"
    assert starters[0]["jersey_number"] is None


def test_extract_starters_fallback_missing_event():
    """Players with no stats for the target event are ignored."""
    team_data = {
        "currentFixturePlayerStats": [],
        "data": [
            {
                "name": "Player",
                "stats": {
                    "11111": {"minutesPlayed": 90, "substitutedIn": None, "position": "ST"},
                },
            },
        ],
    }
    starters = _extract_starters(team_data, event_api_id=99999)
    assert starters == []


def test_extract_starters_empty_response():
    assert _extract_starters({}, event_api_id=1) == []
    assert _extract_starters({"data": [], "currentFixturePlayerStats": []}, event_api_id=1) == []


# ── StatshubClient.get_lineup_status ─────────────────────────────

@pytest.mark.asyncio
async def test_get_lineup_status_returns_parsed_dict():
    from app.ingestion.statshub.client import StatshubClient

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "data": {
            "14023940": "confirmed",
            "15342062": "none",
            "14167984": "predicted",
        }
    })

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        async with StatshubClient() as client:
            result = await client.get_lineup_status([14023940, 15342062, 14167984])

    assert result["14023940"] == "confirmed"
    assert result["15342062"] == "none"
    assert result["14167984"] == "predicted"


@pytest.mark.asyncio
async def test_get_lineup_status_empty_ids():
    from app.ingestion.statshub.client import StatshubClient

    async with StatshubClient() as client:
        result = await client.get_lineup_status([])

    assert result == {}


@pytest.mark.asyncio
async def test_get_lineup_status_404_returns_empty():
    from app.ingestion.statshub.client import StatshubClient

    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        async with StatshubClient() as client:
            result = await client.get_lineup_status([12345])

    assert result == {}
