# backend/tests/test_worker_wc_outrights.py
"""Test que job_sync_wc_outright_odds est correctement enregistré dans le scheduler."""
from unittest.mock import AsyncMock, patch

import pytest

from app.worker import create_scheduler, job_sync_wc_outright_odds


@pytest.mark.asyncio
async def test_job_sync_wc_outright_odds_exists():
    """job_sync_wc_outright_odds est appelable sans crash (avec sync patché)."""
    with patch(
        "app.worker.sync_all_wc_outrights",
        AsyncMock(return_value=42),
    ):
        with patch("app.worker.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session
            await job_sync_wc_outright_odds()


def test_wc_outright_job_in_scheduler():
    """Le scheduler contient un job avec id='sync_wc_outright_odds'."""
    scheduler = create_scheduler()
    job_ids = [job.id for job in scheduler.get_jobs()]
    assert "sync_wc_outright_odds" in job_ids
    # Le scheduler n'est pas démarré dans les tests — shutdown serait une erreur
    # On vérifie simplement la présence du job sans tenter de l'arrêter.
