"""Rattrapage historique des statistiques joueur, par match.

Reutilise sync_player_stats_for_event : aucune logique d'ingestion n'est
dupliquee ici. Ce script ne fait qu'enumerer les matchs a traiter.

Volume mesure le 21/08/2026 sur les six competitions du perimetre :
2 042 matchs pour la saison 2024-2025, soit environ 10 200 appels pour les
cinq saisons.

L'enumeration passe par league + date_from + date_to. Le parametre season=
de l'API est inoperant : ?season=2024-2025 rend 408 110 evenements remontant
a 1930.

Les identifiants d'equipe viennent de home_team_obj.id / away_team_obj.id --
/api/events/ n'expose pas de home_team_api_id. Ces identifiants relevent du
meme espace que canonical_teams.bzz_team_id (le PSG y vaut 114 des deux
cotes).

Reprenable : les matchs deja presents dans bzz_player_match_stats sont
ignores, une execution interrompue redemarre donc sans retraiter l'existant.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import (
    BACKFILL_SEASONS,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats_for_event

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def season_window(season: str) -> tuple[str, str]:
    """Rend (date_from, date_to) pour une saison 'AAAA-AAAA'."""
    debut, fin = season.split("-")
    return f"{debut}-07-01", f"{fin}-06-30"


async def _events_deja_ingeres(session: AsyncSession) -> set[int]:
    """Identifiants des matchs portant deja des statistiques."""
    from app.models.bzzoiro import BzzPlayerMatchStat

    result = await session.execute(
        select(BzzPlayerMatchStat.event_api_id).distinct()
    )
    return set(result.scalars().all())


async def backfill(
    session: AsyncSession,
    client: Any,
    seasons: list[str] | None = None,
    leagues: list[int] | None = None,
) -> tuple[int, int]:
    """Rattrape les statistiques manquantes.

    Retourne (matchs traites, matchs ignores).
    """
    seasons = seasons or BACKFILL_SEASONS
    leagues = leagues or TARGET_LEAGUE_INTERNAL_ID_LIST

    deja = await _events_deja_ingeres(session)
    logger.info("%d matchs deja ingeres, ils seront ignores", len(deja))

    traites = ignores = 0

    for season in seasons:
        date_from, date_to = season_window(season)
        for league in leagues:
            events = await client.get_all(
                "/api/events/",
                {"league": league, "date_from": date_from, "date_to": date_to},
            )
            logger.info(
                "Saison %s / competition %s : %d matchs", season, league, len(events)
            )

            for event in events:
                event_api_id = event.get("id")
                if event_api_id is None or event.get("status") != "finished":
                    continue
                if event_api_id in deja:
                    ignores += 1
                    continue

                domicile = event.get("home_team_obj") or {}
                exterieur = event.get("away_team_obj") or {}

                try:
                    lignes = await sync_player_stats_for_event(
                        session, client, event_api_id,
                        domicile.get("id"),
                        exterieur.get("id"),
                    )
                    if lignes:
                        deja.add(event_api_id)
                    traites += 1
                except Exception as exc:
                    logger.warning("Echec match %s : %s", event_api_id, exc)

            logger.info("  Cumul : %d matchs traites, %d ignores", traites, ignores)

    logger.info("Rattrapage termine : %d traites, %d ignores", traites, ignores)
    return traites, ignores


async def _main() -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await backfill(session, client)


if __name__ == "__main__":
    asyncio.run(_main())
