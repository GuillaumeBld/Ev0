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
import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY
from app.ingestion.ps3838.anchor import _fold
from app.services.season_service import current_season, season_end, season_start

# Championnats a segmentation stricte : chaque championnat round_robin de 1re
# ou 2e division doit rendre son effectif reglementaire exact. Derive du
# registre (Task 1) plutot que code en dur — l'UEFA, les selections et les
# formats groups/knockout n'engagent pas un club dans un championnat et
# restent hors de cette boucle (traites en Task 4).
EFFECTIFS_STRICTS: dict[int, int] = {
    league_id: meta.expected_count
    for league_id, meta in LEAGUE_REGISTRY.items()
    if meta.kind in ("domestic_1", "domestic_2") and meta.format == "round_robin"
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# Affixes qui ne distinguent pas deux clubs : "FC Barcelona" et "Barcelone"
# designent le meme club. Leur oubli a produit 14 doublons le 22/08.
_AFFIXES = {
    "fc", "ac", "as", "sc", "cf", "ud", "sd", "rc", "cd", "sv", "vfb", "vfl",
    "tsg", "rb", "sl", "ss", "us", "afc", "bsc", "fsv", "spvgg", "club",
}
_ORDINAL_RE = re.compile(r"^\d+$")


def cle_club(nom: str | None) -> str:
    """Cle d'appariement d'un nom de club : replie, sans affixes.

    Sert a reconnaitre qu'une ligne canonique existe deja sous un autre
    libelle, plutot que d'en creer une seconde.
    """
    tokens = [t for t in _fold(nom or "").split() if t]
    garde = [t for t in tokens if t not in _AFFIXES and not _ORDINAL_RE.match(t)]
    # Un club dont le nom n'est QUE des affixes garde ses tokens d'origine.
    return " ".join(garde or tokens)


# Les 4 competitions UEFA clubs (groups/knockout, country=None dans le
# registre) : elles ne segmentent jamais un championnat (Task 2 les exclut
# volontairement d'EFFECTIFS_STRICTS). Elles fournissent des matchs a un club
# qui a par ailleurs un championnat domestique, ou revelent un club qui n'en
# a aucun dans le perimetre (qualif UEFA seulement) — c'est tout l'objet
# d'attach_european ci-dessous.
UEFA_CLUB_COMPETITIONS: tuple[int, ...] = tuple(
    league_id
    for league_id, meta in LEAGUE_REGISTRY.items()
    if meta.kind == "uefa_club"
)


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
    for league_api_id, attendu in EFFECTIFS_STRICTS.items():
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
        .values(league_api_id=None, season=None, country=None)
    )

    # Index des lignes existantes par cle d'appariement : une ligne canonique
    # peut porter un tout autre libelle que l'API ("Barcelone" / "FC
    # Barcelona"). Sans cet index, la reconstruction cree un doublon, et le
    # sync Transfermarkt — qui resout vers l'ancienne ligne — reecrit ensuite
    # le mauvais identifiant sur les joueurs chaque nuit.
    toutes = (await session.execute(select(CanonicalTeam))).scalars().all()
    par_cle: dict[str, CanonicalTeam] = {}
    for ligne in toutes:
        for libelle in (ligne.name_fr, ligne.name_en, *(ligne.aliases or [])):
            cle = cle_club(libelle)
            if cle:
                par_cle.setdefault(cle, ligne)

    comptes: dict[int, int] = {}
    for league_api_id, engages in par_championnat.items():
        pays = LEAGUE_REGISTRY[league_api_id].country
        for club_id, nom in engages.items():
            existant = (await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == club_id)
            )).scalar_one_or_none()

            if existant is None:
                existant = par_cle.get(cle_club(nom))

            if existant is None:
                logger.info("nouveau club au referentiel : %s (id %s)", nom, club_id)
                session.add(CanonicalTeam(
                    name_fr=nom,
                    name_en=nom,
                    bzz_team_id=club_id,
                    league_api_id=league_api_id,
                    season=season,
                    country=pays,
                ))
            else:
                existant.bzz_team_id = club_id
                existant.league_api_id = league_api_id
                existant.season = season
                existant.country = pays
        comptes[league_api_id] = len(engages)

    await session.commit()
    logger.info("Referentiel reconstruit : %s", comptes)
    return comptes


async def attach_european(
    session: AsyncSession, client: Any, season: str | None = None
) -> dict[str, int]:
    """Rattache les clubs engages en UEFA (7/8/83/90) — passe additive,
    APRES la segmentation domestique de `rebuild`.

    Un club deja segmente dans un championnat domestique (league_api_id pose
    par `rebuild`) n'est pas touche : la competition UEFA lui fournit des
    matchs supplementaires, pas un engagement championnat.

    Un club vu UNIQUEMENT via l'UEFA (qualif, pas de championnat domestique
    dans le perimetre) est enregistre pour exister au referentiel — apparie
    par bzz_team_id, puis par cle_club(nom) — avec league_api_id=NULL et
    country=NULL. Jamais de pays ni de championnat devine : le cas est
    consigne explicitement.

    Ne relance pas la segmentation domestique. N'ecrit jamais league_api_id
    dans {7, 8, 83, 90} : ces identifiants ne designent qu'une competition
    UEFA, jamais un championnat engageant un club.
    """
    from app.models.canonical_teams import CanonicalTeam

    if season is None:
        season = await current_season(session)

    # Union des clubs engages sur les 4 competitions UEFA (espace A).
    engages: dict[int, str] = {}
    for league_api_id in UEFA_CLUB_COMPETITIONS:
        for club_id, nom in (
            await enumerer_engages(client, league_api_id, season)
        ).items():
            engages.setdefault(club_id, nom)

    # Meme index d'appariement par nom replie que `rebuild`, pour reconnaitre
    # une ligne canonique existante sous un libelle different de celui rendu
    # par l'API plutot que d'en creer un doublon.
    toutes = (await session.execute(select(CanonicalTeam))).scalars().all()
    par_cle: dict[str, CanonicalTeam] = {}
    for ligne in toutes:
        for libelle in (ligne.name_fr, ligne.name_en, *(ligne.aliases or [])):
            cle = cle_club(libelle)
            if cle:
                par_cle.setdefault(cle, ligne)

    sans_championnat = 0
    for club_id, nom in engages.items():
        existant = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == club_id)
        )).scalar_one_or_none()

        if existant is None:
            existant = par_cle.get(cle_club(nom))

        if existant is not None and existant.league_api_id is not None:
            # Deja engage dans un championnat domestique (Task 2) : on ne
            # touche a rien — ni league_api_id, ni country, ni season.
            continue

        logger.info("club sans championnat domestique : %s (id %s)", nom, club_id)
        sans_championnat += 1

        if existant is None:
            session.add(CanonicalTeam(
                name_fr=nom,
                name_en=nom,
                bzz_team_id=club_id,
                league_api_id=None,
                season=None,
                country=None,
            ))
        else:
            existant.bzz_team_id = club_id
            existant.league_api_id = None
            existant.season = None
            existant.country = None

    await session.commit()
    logger.info(
        "Rattachement UEFA : %d clubs engages, %d sans championnat domestique",
        len(engages), sans_championnat,
    )
    return {"engages": len(engages), "sans_championnat": sans_championnat}


async def _main() -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await rebuild(session, client)
        await attach_european(session, client)


if __name__ == "__main__":
    asyncio.run(_main())
