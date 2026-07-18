"""Écriture et gel des snapshots de pricing par modèle (spec 2026-07-18, §3.1)."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_pricing import ModelPricingSnapshot
from app.pricing.model_registry import KNOWN_MARKETS, KNOWN_MODELS

logger = logging.getLogger(__name__)


async def upsert_snapshot(
    session: AsyncSession,
    *,
    model_name: str,
    fixture_id: int,
    player_api_id: int,
    player_name: str,
    market: str,
    probability: float,
    fair_odds: float,
    as_of_utc: datetime,
) -> bool:
    """Crée ou met à jour le snapshot. Retourne False (sans rien toucher) si figé."""
    if model_name not in KNOWN_MODELS:
        raise ValueError(f"Modèle inconnu: {model_name!r} (admis: {KNOWN_MODELS})")
    if market not in KNOWN_MARKETS:
        raise ValueError(f"Marché inconnu: {market!r} (admis: {KNOWN_MARKETS})")

    result = await session.execute(
        select(ModelPricingSnapshot).where(
            ModelPricingSnapshot.fixture_id == fixture_id,
            ModelPricingSnapshot.player_api_id == player_api_id,
            ModelPricingSnapshot.market == market,
            ModelPricingSnapshot.model_name == model_name,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(
            ModelPricingSnapshot(
                model_name=model_name,
                fixture_id=fixture_id,
                player_api_id=player_api_id,
                player_name=player_name,
                market=market,
                probability=probability,
                fair_odds=fair_odds,
                as_of_utc=as_of_utc,
            )
        )
        return True
    if row.frozen:
        logger.debug(
            "Snapshot figé, upsert ignoré: fixture=%d player=%d market=%s model=%s",
            fixture_id, player_api_id, market, model_name,
        )
        return False
    row.player_name = player_name
    row.probability = probability
    row.fair_odds = fair_odds
    row.as_of_utc = as_of_utc
    return True


async def freeze_fixture(session: AsyncSession, fixture_id: int) -> int:
    """Fige tous les snapshots du match (appelé au coup d'envoi). Retourne le nombre figé."""
    result = await session.execute(
        update(ModelPricingSnapshot)
        .where(
            ModelPricingSnapshot.fixture_id == fixture_id,
            ModelPricingSnapshot.frozen.is_(False),
        )
        .values(frozen=True)
    )
    frozen_count = result.rowcount or 0
    logger.info("Fixture %d: %d snapshots figés au coup d'envoi", fixture_id, frozen_count)
    return frozen_count
