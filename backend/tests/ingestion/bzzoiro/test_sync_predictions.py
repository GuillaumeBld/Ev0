"""Tests for sync_predictions module."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.sync_predictions import sync_predictions


def _make_session(event_ids: list[int]) -> MagicMock:
    """Return a mock session whose first execute() returns the given event IDs.

    Subsequent execute() calls (the upsert statements) return a plain mock so
    that ``await session.execute(stmt)`` succeeds.
    """
    # Build a fake result for the event-ID query
    event_result = MagicMock()
    event_result.fetchall.return_value = [(eid,) for eid in event_ids]

    upsert_result = MagicMock()

    call_count = {"n": 0}

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return event_result
        return upsert_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_sync_predictions_empty():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])

    # No upcoming events in DB — but no prediction rows either
    session = _make_session([])

    count = await sync_predictions(session, client)
    assert count == 0
    client.get_all.assert_called_once_with("/api/predictions/")
    # Only the event-ID query should have been executed (no upsert rows)
    assert session.execute.call_count == 1
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_sync_predictions_basic():
    prediction_rows = [
        {
            "event": {"api_id": 1001},
            "created_at": "2026-04-09T12:00:00Z",
            "prob_home_win": 0.55,
            "prob_draw": 0.25,
            "prob_away_win": 0.20,
            "predicted_result": "H",
            "expected_home_goals": 1.8,
            "expected_away_goals": 1.1,
            "prob_over_15": 0.82,
            "prob_over_25": 0.61,
            "prob_over_35": 0.35,
            "prob_btts_yes": 0.48,
            "confidence": 0.72,
            "model_version": "v2.1",
            "most_likely_score": "2-1",
            "favorite": "H",
            "favorite_prob": 0.55,
            "favorite_recommend": True,
            "over_15_recommend": True,
            "over_25_recommend": False,
            "over_35_recommend": False,
            "btts_recommend": False,
            "winner_recommend": True,
        },
        {
            "event": {"api_id": 1002},
            "created_at": None,
            "prob_home_win": 0.30,
            "prob_draw": 0.30,
            "prob_away_win": 0.40,
            "predicted_result": "A",
            "expected_home_goals": 1.0,
            "expected_away_goals": 1.5,
            "prob_over_15": 0.75,
            "prob_over_25": 0.55,
            "prob_over_35": 0.28,
            "prob_btts_yes": 0.42,
            "confidence": 0.60,
            "model_version": "v2.1",
            "most_likely_score": "1-2",
            "favorite": "A",
            "favorite_prob": 0.40,
            "favorite_recommend": True,
            "over_15_recommend": True,
            "over_25_recommend": True,
            "over_35_recommend": False,
            "btts_recommend": False,
            "winner_recommend": True,
        },
    ]

    client = MagicMock()
    client.get_all = AsyncMock(return_value=prediction_rows)

    # Simulate both events being in the upcoming window
    session = _make_session([1001, 1002])

    count = await sync_predictions(session, client)
    assert count == 2
    client.get_all.assert_called_once_with("/api/predictions/")
    # 1 event-ID query + 2 upserts
    assert session.execute.call_count == 3
    session.commit.assert_called_once()
