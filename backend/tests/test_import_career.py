"""Tests for the career import script (parsing/matching, DB mocked)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scripts.import_career import (
    ImportStats,
    _normalize_name,
    find_player_api_id,
    import_career_data,
)


def _make_session_with_rows(rows: list[tuple[int, str]]) -> MagicMock:
    """Mock AsyncSession whose execute().all() returns the given (api_id, name) rows."""
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


# --------------------------------------------------------------------------
# _normalize_name
# --------------------------------------------------------------------------

def test_normalize_name_strips_accents_and_case():
    assert _normalize_name("Dro Fernández") == _normalize_name("dro fernandez")


def test_normalize_name_collapses_whitespace():
    assert _normalize_name("  Lucas   Chevalier ") == _normalize_name("Lucas Chevalier")


# --------------------------------------------------------------------------
# find_player_api_id
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_player_api_id_matches_by_name_and_dob():
    session = _make_session_with_rows([(555, "Lucas Chevalier")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id == 555


@pytest.mark.asyncio
async def test_find_player_api_id_matches_accent_insensitive():
    session = _make_session_with_rows([(777, "Dro Fernandez")])
    api_id = await find_player_api_id(session, "Dro Fernández", dt.date(2008, 1, 12))
    assert api_id == 777


@pytest.mark.asyncio
async def test_find_player_api_id_no_dob_match_returns_none():
    session = _make_session_with_rows([])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


@pytest.mark.asyncio
async def test_find_player_api_id_dob_matches_but_name_differs_returns_none():
    # Same birthdate, different person -> must not guess.
    session = _make_session_with_rows([(1, "Someone Else")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


@pytest.mark.asyncio
async def test_find_player_api_id_ambiguous_multiple_name_matches_returns_none():
    # Two bzz_players rows share the dob AND normalized name -> too ambiguous, skip.
    session = _make_session_with_rows([(1, "Lucas Chevalier"), (2, "Lucas Chevalier")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


# --------------------------------------------------------------------------
# import_career_data
# --------------------------------------------------------------------------

def _matched_player(seasons: list[dict] | None = None) -> dict:
    return {
        "input_name": "Lucas Chevalier",
        "input_nationality": "France",
        "input_dob": "2001-11-06",
        "input_club": "Paris Saint-Germain",
        "matched": True,
        "tm_id": "463600",
        "matched_name": "Lucas Chevalier",
        "seasons": seasons
        if seasons is not None
        else [
            {
                "appearances": 17,
                "goals": 0,
                "assists": 0,
                "minutes": 1530,
                "season": "25/26",
                "season_start_year": 2025,
                "competition_code": "FR1",
                "competition": "Ligue 1",
            }
        ],
    }


def _unmatched_player() -> dict:
    return {
        "input_name": "Dro Fernández",
        "input_nationality": "Spain",
        "input_dob": "2008-01-12",
        "input_club": "Paris Saint-Germain",
        "matched": False,
        "tm_id": None,
        "matched_name": None,
        "seasons": [],
    }


@pytest.mark.asyncio
async def test_import_career_data_maps_matched_player_and_upserts_seasons():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock(return_value=42)
    ) as mock_find:
        stats = await import_career_data(session, [_matched_player()])

    mock_find.assert_awaited_once_with(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert stats.players_matched == 1
    assert stats.seasons_upserted == 1
    assert stats.players_skipped_not_matched == 0
    assert stats.players_skipped_unresolved == 0
    session.execute.assert_awaited_once()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_import_career_data_skips_unmatched_player():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock()
    ) as mock_find:
        stats = await import_career_data(session, [_unmatched_player()])

    mock_find.assert_not_awaited()
    session.execute.assert_not_awaited()
    assert stats.players_skipped_not_matched == 1
    assert stats.players_matched == 0
    assert stats.seasons_upserted == 0


@pytest.mark.asyncio
async def test_import_career_data_skips_when_no_bzz_player_found():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock(return_value=None)
    ):
        stats = await import_career_data(session, [_matched_player()])

    session.execute.assert_not_awaited()
    assert stats.players_skipped_unresolved == 1
    assert stats.players_matched == 0
    assert stats.seasons_upserted == 0


@pytest.mark.asyncio
async def test_import_career_data_skips_matched_player_missing_dob():
    player = _matched_player()
    player["input_dob"] = None
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock()
    ) as mock_find:
        stats = await import_career_data(session, [player])

    mock_find.assert_not_awaited()
    assert stats.players_skipped_unresolved == 1


@pytest.mark.asyncio
async def test_import_career_data_is_idempotent_stats_type():
    assert isinstance(ImportStats(), ImportStats)
