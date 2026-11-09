"""Tests du jalon 9 : budget du lot d'import carriere pilotable par variable
d'environnement.

La priorisation des vagues (vague 1 avant vague 2) est deja garantie par
l'ORDER BY de `players_needing_career` (Task 4) — rien a reverifier ici. Ce
module verifie seulement que :

(a) `Settings` expose `career_batch_limit` avec le defaut 120 (aligne sur
    `DEFAULT_BATCH_LIMIT`), et qu'une variable d'env `CAREER_BATCH_LIMIT` est
    bien lue par pydantic-settings ;
(b) `job_import_careers` transmet `settings.career_batch_limit` (et non plus
    la constante `DEFAULT_BATCH_LIMIT`) a `_run_career_import_blocking`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.ingestion.career_import import DEFAULT_BATCH_LIMIT


# ---------------------------------------------------------------------------
# (a) Settings.career_batch_limit — defaut + lecture de la variable d'env
# ---------------------------------------------------------------------------


def test_career_batch_limit_default_matches_import_default():
    s = Settings()
    assert s.career_batch_limit == DEFAULT_BATCH_LIMIT == 120


def test_career_batch_limit_reads_env_var(monkeypatch):
    monkeypatch.setenv("CAREER_BATCH_LIMIT", "5")
    s = Settings()
    assert s.career_batch_limit == 5


# ---------------------------------------------------------------------------
# (b) job_import_careers transmet settings.career_batch_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_import_careers_uses_settings_batch_limit():
    fake_run = SimpleNamespace(candidates=0, imported=0, attempted=0, unmatched=0, errors=0)

    with patch("app.worker.settings") as mock_settings, patch(
        "app.worker._run_career_import_blocking", return_value=fake_run
    ) as mock_blocking:
        mock_settings.career_batch_limit = 5

        from app.worker import job_import_careers

        await job_import_careers()

    mock_blocking.assert_called_once_with(5)
