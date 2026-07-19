"""Saison courante — source unique de vérité (spec 2026-07-18, §3.5).

La saison bascule le 1er août. Résolution :
1. override manuel via app_config (clé "current_season", ex. "2026-2027") ;
2. sinon calcul depuis la date du jour.
Un override invalide est ignoré avec warning — jamais d'échec silencieux.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SEASON_CONFIG_KEY = "current_season"
SEASON_ROLLOVER_MONTH = 8  # 1er août
_SEASON_RE = re.compile(r"^(\d{4})-(\d{4})$")


def compute_season(today: date) -> str:
    """Saison au format "NNNN-NNNN" pour une date donnée (bascule au 1er août)."""
    if today.month >= SEASON_ROLLOVER_MONTH:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


def _match_continuous_season(season: str) -> re.Match[str] | None:
    """Valide le format "NNNN-NNNN" ET la continuité (deuxième année = première + 1).

    Retourne le match si la saison est valide, sinon None.
    """
    match = _SEASON_RE.match(season)
    if match and int(match.group(2)) == int(match.group(1)) + 1:
        return match
    return None


def season_start(season: str) -> date:
    """Date de début (1er août de la première année) d'une saison "NNNN-NNNN"."""
    match = _match_continuous_season(season)
    if not match:
        raise ValueError(
            f"Format de saison invalide: {season!r} "
            "(attendu NNNN-NNNN avec continuité — deuxième année = première + 1)"
        )
    return date(int(match.group(1)), SEASON_ROLLOVER_MONTH, 1)


async def current_season(session: AsyncSession, today: date | None = None) -> str:
    """Saison courante : override app_config si valide, sinon calcul par date."""
    from app.models.app_config import AppConfig

    result = await session.execute(
        select(AppConfig).where(AppConfig.key == SEASON_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        value = row.value.strip()
        if _match_continuous_season(value):
            return value
        logger.warning(
            "app_config[%s]=%r invalide (attendu NNNN-NNNN avec continuité — "
            "deuxième année = première + 1) — fallback calcul par date",
            SEASON_CONFIG_KEY, value,
        )
    return compute_season(today or date.today())
