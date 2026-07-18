"""Registre Alpha/Beta + snapshots pré-match : upsert jusqu'au gel, figé ensuite."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.model_pricing import ModelPricingSnapshot
from app.pricing.model_registry import (
    DEFAULT_MODEL,
    KNOWN_MARKETS,
    KNOWN_MODELS,
    MODEL_ALPHA,
    MODEL_BETA,
)
from app.services.model_snapshot_service import upsert_snapshot


def test_registre_expose_alpha_et_beta():
    assert MODEL_ALPHA == "alpha"
    assert MODEL_BETA == "beta"
    assert KNOWN_MODELS == ("alpha", "beta")
    assert DEFAULT_MODEL == "alpha"
    assert "goal_with_sub" in KNOWN_MARKETS


def test_modele_orm_colonnes_et_contrainte():
    assert ModelPricingSnapshot.__tablename__ == "model_pricing_snapshots"
    for col in ("model_name", "fixture_id", "player_api_id", "market",
                "probability", "fair_odds", "as_of_utc", "frozen"):
        assert hasattr(ModelPricingSnapshot, col)
    constraint_names = {c.name for c in ModelPricingSnapshot.__table__.constraints}
    assert "uq_model_pricing_snapshot" in constraint_names


def _session_returning(row):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


_KWARGS = dict(
    model_name="alpha", fixture_id=1, player_api_id=42, player_name="Mbappé",
    market="goal_with_sub", probability=0.31, fair_odds=3.23,
    as_of_utc=datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc),
)


class TestUpsertSnapshot:
    @pytest.mark.asyncio
    async def test_cree_la_ligne_si_absente(self):
        session = _session_returning(None)
        assert await upsert_snapshot(session, **_KWARGS) is True
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_met_a_jour_si_non_figee(self):
        row = MagicMock()
        row.frozen = False
        session = _session_returning(row)
        assert await upsert_snapshot(session, **_KWARGS) is True
        assert row.probability == 0.31
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuse_si_figee(self):
        row = MagicMock()
        row.frozen = True
        row.probability = 0.28
        session = _session_returning(row)
        assert await upsert_snapshot(session, **_KWARGS) is False
        assert row.probability == 0.28  # intacte
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejette_modele_inconnu(self):
        session = _session_returning(None)
        with pytest.raises(ValueError, match="gamma"):
            await upsert_snapshot(session, **{**_KWARGS, "model_name": "gamma"})

    @pytest.mark.asyncio
    async def test_rejette_marche_inconnu(self):
        session = _session_returning(None)
        with pytest.raises(ValueError, match="first_goal"):
            await upsert_snapshot(session, **{**_KWARGS, "market": "first_goal"})
