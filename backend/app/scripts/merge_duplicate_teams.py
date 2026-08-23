"""Fusionne les lignes canoniques en double laissees par la reconstruction.

La reconstruction du 22/08 n'appariait les clubs que sur `name_fr` en exact.
« FC Barcelona » n'a donc pas reconnu « Barcelone », et 14 clubs se sont
retrouves avec deux lignes :

- l'ANCIENNE porte l'identite historique — transfermarkt_club_id,
  api_football_id, alias — et est referencee par 1 860 fixtures ;
- la RECENTE porte le bon bzz_team_id (espace des evenements) et
  l'engagement de la saison.

Consequence observee : le sync Transfermarkt de 04:30 resout vers l'ancienne
ligne et y lit son bzz_team_id perime, qu'il reecrit sur les joueurs. Chaque
nuit il annulait donc le travail du sync des effectifs de 03:00 — les joueurs
du Barca reprenaient `current_team_name = "Saint George"`.

La fusion conserve l'ANCIENNE ligne (referencee, et porteuse des identifiants
externes), lui transfere le bzz_team_id et l'engagement de la recente, puis
supprime cette derniere.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.scripts.rebuild_team_registry import cle_club

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def choisir_paire(lignes: list) -> tuple[object, object] | None:
    """Rend (ancienne a conserver, recente a supprimer), ou None si ambigu.

    L'ancienne est celle qui porte un identifiant externe ; a defaut, la plus
    petite cle primaire. La recente est celle qui porte l'engagement.
    """
    if len(lignes) != 2:
        return None

    avec_engagement = [x for x in lignes if x.league_api_id is not None]
    if len(avec_engagement) != 1:
        return None

    recente = avec_engagement[0]
    ancienne = next(x for x in lignes if x is not recente)
    return ancienne, recente


async def merge(session: AsyncSession) -> tuple[int, int]:
    """Fusionne les doublons. Rend (fusionnes, ignores car ambigus)."""
    from app.models.canonical_teams import CanonicalTeam

    toutes = (await session.execute(select(CanonicalTeam))).scalars().all()

    # Groupement sur TOUS les libelles : la ligne historique porte le nom
    # francais ("Barcelone") et la recente le nom de l'API ("FC Barcelona").
    # Les replier separement ne les rapproche pas — c'est name_en ("Barcelona")
    # qui fait le lien.
    groupes: dict[str, list] = {}
    for ligne in toutes:
        vues: set[str] = set()
        for libelle in (ligne.name_fr, ligne.name_en, *(ligne.aliases or [])):
            cle = cle_club(libelle)
            if cle and cle not in vues:
                vues.add(cle)
                groupes.setdefault(cle, []).append(ligne)

    fusionnes = ambigus = 0
    # Une meme paire peut apparaitre sous plusieurs cles (nom francais et
    # anglais) : on ne la fusionne qu'une fois.
    traitees: set[int] = set()
    for cle, lignes in groupes.items():
        lignes = [x for x in lignes if x.id not in traitees]
        if len(lignes) < 2:
            continue

        paire = choisir_paire(lignes)
        if paire is None:
            ambigus += 1
            logger.warning(
                "%s : %d lignes, appariement ambigu — laissees en l'etat (ids %s)",
                cle, len(lignes), [x.id for x in lignes],
            )
            continue

        ancienne, recente = paire
        logger.info(
            "%s : conserve #%s (%s, tm=%s), reprend bzz_team_id %s -> %s, supprime #%s",
            cle, ancienne.id, ancienne.name_fr, ancienne.transfermarkt_club_id,
            ancienne.bzz_team_id, recente.bzz_team_id, recente.id,
        )

        # La contrainte d'unicite sur bzz_team_id impose de liberer la valeur
        # avant de la reprendre : on supprime la recente d'abord.
        nouveau_id = recente.bzz_team_id
        league = recente.league_api_id
        season = recente.season
        await session.delete(recente)
        await session.flush()

        ancienne.bzz_team_id = nouveau_id
        ancienne.league_api_id = league
        ancienne.season = season
        traitees.update({ancienne.id, recente.id})
        fusionnes += 1

    await session.commit()
    logger.info("Fusion terminee : %d clubs fusionnes, %d ambigus", fusionnes, ambigus)
    return fusionnes, ambigus


async def _main() -> None:
    from app.db import async_session

    async with async_session() as session:
        await merge(session)


if __name__ == "__main__":
    asyncio.run(_main())
