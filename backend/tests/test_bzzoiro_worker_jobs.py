"""Tests for Bzzoiro sync jobs registered in the background worker."""

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
