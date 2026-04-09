"""Tests for sync_predictions module."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.sync_predictions import sync_predictions


@pytest.mark.asyncio
async def test_sync_predictions_empty():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_predictions(session, client)
    assert count == 0
    client.get_all.assert_called_once_with("/api/predictions/")
    session.execute.assert_not_called()


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

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_predictions(session, client)
    assert count == 2
    client.get_all.assert_called_once_with("/api/predictions/")
    assert session.execute.call_count == 2
    session.commit.assert_called_once()
