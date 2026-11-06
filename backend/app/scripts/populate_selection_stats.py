"""Peuple player_selection_season_stats depuis les stats de match Bzzoiro.

Reutilise aggregate_selection_stats : aucune logique d'agregation n'est
dupliquee ici. Reexecutable (upsert) -- un lancement interrompu ou repete
ne cree jamais de doublons.

Par defaut, agrege 2025-2026 ET 2026-2027 : les matchs de selection (Coupe
du Monde 2026, Ligue des Nations) peuvent tomber de part et d'autre de la
bascule de saison, et les deux doivent rester a jour en continu.

    PYTHONPATH=/app python -m app.scripts.populate_selection_stats
    PYTHONPATH=/app python -m app.scripts.populate_selection_stats 2025-2026
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.db import async_session
from app.ingestion.bzzoiro.aggregate_selection import aggregate_selection_stats

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_SEASONS = ["2025-2026", "2026-2027"]


async def _run(seasons: list[str]) -> None:
    async with async_session() as session:
        total = 0
        for season in seasons:
            total += await aggregate_selection_stats(session, season)
        logger.info("Total lignes selection upsertees : %d", total)


def main() -> None:
    seasons = sys.argv[1:] or _DEFAULT_SEASONS
    asyncio.run(_run(seasons))


if __name__ == "__main__":
    main()
