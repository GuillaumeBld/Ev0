"""Reconstruit le referentiel des equipes sur l'identifiant qui fait foi.

Bzzoiro expose ses equipes sous plusieurs espaces d'identifiants. Un seul
fonctionne partout — celui de /api/events/ (home_team_obj.id), qui sert aussi
a /api/players/?team= et a /api/player-stats/?event=. Verifie le 22/08/2026 :
63 = AC Milan, 77 = Inter, 62 = Napoli, 203 = Coventry City.

La base stockait un autre espace, herite : d'ou 6 clubs reconnus sur 20 en
Serie A, l'absence des promus (Coventry City, Hull City, Paris FC, Le Mans,
Troyes), et des filtres qui melangeaient les championnats.

SEGMENTATION STRICTE : chaque championnat doit rendre son effectif
reglementaire exact, et un club ne peut etre engage que dans un seul. Un ecart
interrompt la reconstruction sans rien commettre — une segmentation
approximative donne l'illusion d'etre juste.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import EFFECTIFS_REGLEMENTAIRES
from app.services.season_service import current_season, season_end, season_start

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class SegmentationError(RuntimeError):
    """L'effectif d'un championnat ne correspond pas a son format."""


async def enumerer_engages(
    client: Any, league_api_id: int, season: str
) -> dict[int, str]:
    """Rend {identifiant: nom} des clubs engages dans ce championnat.

    L'enumeration passe par une fenetre de dates : le parametre season= de
    l'API est inoperant (il rend 408 110 evenements remontant a 1930).
    """
    events = await client.get_all(
        "/api/events/",
        {
            "league": league_api_id,
            "date_from": season_start(season).isoformat(),
            "date_to": season_end(season).isoformat(),
        },
    )
    engages: dict[int, str] = {}
    for e in events:
        for cle in ("home_team_obj", "away_team_obj"):
            obj = e.get(cle) or {}
            if obj.get("id"):
                engages[obj["id"]] = obj.get("name") or ""
    return engages


async def rebuild(
    session: AsyncSession, client: Any, season: str | None = None
) -> dict[int, int]:
    """Reconstruit le referentiel. Rend {championnat: nombre d'engages}.

    Leve SegmentationError avant toute ecriture si un effectif est hors format
    ou si un club est engage dans deux championnats.
    """
    from app.models.canonical_teams import CanonicalTeam

    if season is None:
        season = await current_season(session)

    # --- Phase 1 : enumerer et CONTROLER, sans rien ecrire ---
    par_championnat: dict[int, dict[int, str]] = {}
    for league_api_id, attendu in EFFECTIFS_REGLEMENTAIRES.items():
        engages = await enumerer_engages(client, league_api_id, season)
        if len(engages) != attendu:
            raise SegmentationError(
                f"championnat {league_api_id} : {len(engages)} clubs engages, "
                f"{attendu} attendus — reconstruction interrompue, "
                f"rien n'a ete ecrit"
            )
        par_championnat[league_api_id] = engages
        logger.info("championnat %s : %d engages", league_api_id, len(engages))

    vus: dict[int, int] = {}
    for league_api_id, engages in par_championnat.items():
        for club_id in engages:
            if club_id in vus:
                raise SegmentationError(
                    f"club {club_id} engage dans deux championnats "
                    f"({vus[club_id]} et {league_api_id}) — "
                    f"reconstruction interrompue, rien n'a ete ecrit"
                )
            vus[club_id] = league_api_id

    # --- Phase 2 : ecrire ---
    # Remplacement en bloc : un club absent de la nouvelle liste perd son
    # engagement du seul fait de son absence. Sans cela un relegue resterait
    # engage indefiniment et polluerait le filtre de son ancien championnat.
    await session.execute(
        update(CanonicalTeam)
        .where(CanonicalTeam.season == season)
        .values(league_api_id=None, season=None)
    )

    comptes: dict[int, int] = {}
    for league_api_id, engages in par_championnat.items():
        for club_id, nom in engages.items():
            existant = (await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == club_id)
            )).scalar_one_or_none()

            if existant is None:
                existant = (await session.execute(
                    select(CanonicalTeam).where(CanonicalTeam.name_fr == nom)
                )).scalar_one_or_none()

            if existant is None:
                session.add(CanonicalTeam(
                    name_fr=nom,
                    name_en=nom,
                    bzz_team_id=club_id,
                    league_api_id=league_api_id,
                    season=season,
                ))
            else:
                existant.bzz_team_id = club_id
                existant.league_api_id = league_api_id
                existant.season = season
        comptes[league_api_id] = len(engages)

    await session.commit()
    logger.info("Referentiel reconstruit : %s", comptes)
    return comptes


async def _main() -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await rebuild(session, client)


if __name__ == "__main__":
    asyncio.run(_main())
