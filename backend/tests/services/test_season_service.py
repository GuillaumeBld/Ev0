"""Tests du service de saison courante — rollover au 1er août."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.season_service import (
    compute_season,
    current_season,
    season_start,
)


def _mock_session(config_value: str | None) -> MagicMock:
    """Session async mockée : renvoie une ligne AppConfig (ou None)."""
    session = MagicMock()
    row = None
    if config_value is not None:
        row = MagicMock()
        row.value = config_value
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


class TestComputeSeason:
    def test_juillet_reste_saison_precedente(self):
        assert compute_season(date(2026, 7, 18)) == "2025-2026"

    def test_premier_aout_bascule(self):
        assert compute_season(date(2026, 8, 1)) == "2026-2027"

    def test_janvier_milieu_de_saison(self):
        assert compute_season(date(2027, 1, 15)) == "2026-2027"


class TestSeasonStart:
    def test_debut_de_saison(self):
        assert season_start("2026-2027") == date(2026, 8, 1)

    def test_saison_precedente(self):
        assert season_start("2025-2026") == date(2025, 8, 1)

    def test_annees_non_continues_leve_valueerror(self):
        with pytest.raises(ValueError):
            season_start("2026-2099")


class TestCurrentSeason:
    @pytest.mark.asyncio
    async def test_sans_config_calcule_depuis_la_date(self):
        session = _mock_session(None)
        assert await current_season(session, today=date(2026, 9, 1)) == "2026-2027"

    @pytest.mark.asyncio
    async def test_override_config_prioritaire(self):
        session = _mock_session("2026-2027")
        assert await current_season(session, today=date(2026, 7, 1)) == "2026-2027"

    @pytest.mark.asyncio
    async def test_override_invalide_fallback_avec_warning(self, caplog):
        session = _mock_session("n_importe_quoi")
        with caplog.at_level("WARNING"):
            season = await current_season(session, today=date(2026, 7, 1))
        assert season == "2025-2026"
        assert "current_season" in caplog.text

    @pytest.mark.asyncio
    async def test_override_annees_non_continues_fallback_avec_warning(self, caplog):
        session = _mock_session("2026-2099")
        with caplog.at_level("WARNING"):
            season = await current_season(session, today=date(2026, 7, 1))
        assert season == "2025-2026"
        assert "current_season" in caplog.text
