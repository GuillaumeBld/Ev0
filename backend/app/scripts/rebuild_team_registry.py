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

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.league_registry import EXTERNAL_LEAGUE_BASE as PORTAL_LEAGUE_BASE, LEAGUE_REGISTRY
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


def ecarter_barragistes(
    engages: dict[int, str], matchs: dict[int, int], attendu: int
) -> dict[int, str] | None:
    """Plus de clubs que l'effectif : les barrages de fin de saison sont
    rangés dans le championnat (Eliteserien, Allsvenskan : 18 pour 16).
    On garde les `attendu` clubs qui ont le plus de matchs si la coupure est
    nette — le dernier gardé a au moins 3 fois plus de matchs que le premier
    écarté (≈ 30 contre 2). Sinon None : rien n'est deviné."""
    if len(engages) <= attendu:
        return None
    ordre = sorted(engages, key=lambda c: matchs.get(c, 0), reverse=True)
    garde, ecarte = ordre[:attendu], ordre[attendu:]
    if matchs.get(garde[-1], 0) < 3 * max(matchs.get(c, 0) for c in ecarte):
        return None
    return {c: engages[c] for c in garde}


async def enumerer_engages(
    client: Any, league_api_id: int, season: str, attendu: int | None = None
) -> dict[int, str]:
    """Rend {identifiant: nom} des clubs engages dans ce championnat.

    L'enumeration passe par une fenetre de dates : le parametre season= de
    l'API est inoperant (il rend 408 110 evenements remontant a 1930).
    Avec `attendu`, les barragistes en surnombre sont ecartes quand la
    coupure est nette (voir `ecarter_barragistes`).
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
    matchs: dict[int, int] = {}
    for e in events:
        for cle in ("home_team_obj", "away_team_obj"):
            obj = e.get(cle) or {}
            if obj.get("id"):
                engages[obj["id"]] = obj.get("name") or ""
                matchs[obj["id"]] = matchs.get(obj["id"], 0) + 1
    if attendu is not None and len(engages) > attendu:
        filtres = ecarter_barragistes(engages, matchs, attendu)
        if filtres is not None:
            logger.info(
                "championnat %s : %d barragiste(s) ecarte(s) : %s", league_api_id,
                len(engages) - attendu, sorted(engages[c] for c in engages if c not in filtres),
            )
            return filtres
    return engages


async def rebuild(
    session: AsyncSession,
    client: Any,
    season: str | None = None,
    league_ids: list[int] | None = None,
    ecarts: dict[int, str] | None = None,
) -> dict[int, int]:
    """Reconstruit le referentiel. Rend {championnat: nombre d'engages}.

    `league_ids` : restreint la segmentation stricte a ce sous-ensemble de
    `EFFECTIFS_STRICTS` (intersection — un id hors `EFFECTIFS_STRICTS`, par
    exemple une competition UEFA, est silencieusement ignore). `None` (par
    defaut) = comportement HISTORIQUE INCHANGE : toute la boucle porte sur
    `EFFECTIFS_STRICTS` dans son integralite (big-5 inclus) — c'est ce
    qu'utilisent l'orchestrateur one-shot `build_full_referentiel.run()` et
    un run manuel (`python -m app.scripts.rebuild_team_registry`).

    Avec `league_ids` fourni, le reset en bloc (Phase 2) et la detection
    "club engage dans deux championnats" (Phase 1) sont eux aussi restreints
    a ce sous-ensemble : un club deja engage dans une ligue HORS scope n'est
    touche ni par le reset, ni par la detection de double engagement — sinon
    un rebuild scope (ex : les seules ligues d'expansion) desengagerait les
    clubs big-5 que cet appel ne retraite pas.

    LIMITE CONNUE (scope partiel) : la detection de double-engagement ne
    recoupe que les ligues DANS le scope. Un club engage a la fois dans une
    ligue scopee et une ligue non-scopee (par ex. un club big-5 egalement
    engage dans une ligue d'expansion) n'est pas detecte comme double
    engagement par un appel scope — seul `rebuild(league_ids=None)` verifie
    l'ensemble complet. Risque juge faible en pratique : les pays couverts
    par big-5/C1 et par les ligues d'expansion sont disjoints.

    Leve SegmentationError avant toute ecriture si un effectif (du scope) est
    hors format ou si un club est engage dans deux championnats (du scope).
    """
    from app.models.canonical_teams import CanonicalTeam

    if season is None:
        season = await current_season(session)

    effectifs = EFFECTIFS_STRICTS
    if league_ids is not None:
        scope = set(league_ids)
        effectifs = {
            lid: attendu for lid, attendu in EFFECTIFS_STRICTS.items() if lid in scope
        }

    # --- Phase 1 : enumerer et CONTROLER, sans rien ecrire ---
    # Le controle est PAR CHAMPIONNAT : un championnat douteux n'ecrit rien
    # (ses engagements d'hier restent en place) et est signale dans
    # `ecarts` ; il ne bloque plus les autres. Le tout-ou-rien global
    # laissait 20 nouveaux championnats sans clubs pour deux ligues
    # scandinaves en barrages (18/11/2026).
    if ecarts is None:
        ecarts = {}
    par_championnat: dict[int, dict[int, str]] = {}
    for league_api_id, attendu in effectifs.items():
        engages = await enumerer_engages(client, league_api_id, season, attendu)
        if len(engages) != attendu:
            ecarts[league_api_id] = f"{len(engages)} clubs engages, {attendu} attendus"
            logger.error("championnat %s : %s — non reecrit", league_api_id, ecarts[league_api_id])
            continue
        par_championnat[league_api_id] = engages
        logger.info("championnat %s : %d engages", league_api_id, len(engages))

    # Un club dans deux championnats : les deux sont douteux, aucun n'est ecrit.
    vus: dict[int, int] = {}
    doublons: set[int] = set()
    for league_api_id, engages in par_championnat.items():
        for club_id in engages:
            if club_id in vus and vus[club_id] != league_api_id:
                doublons |= {vus[club_id], league_api_id}
                ecarts.setdefault(vus[club_id], f"club {club_id} engage dans deux championnats")
                ecarts.setdefault(league_api_id, f"club {club_id} engage dans deux championnats")
            vus[club_id] = league_api_id
    for league_api_id in doublons:
        par_championnat.pop(league_api_id, None)
    if not par_championnat:
        raise SegmentationError(
            f"aucun championnat conforme — reconstruction interrompue, rien n'a ete ecrit : {ecarts}"
        )

    # --- Phase 2 : ecrire ---
    # Remplacement en bloc : un club absent de la nouvelle liste perd son
    # engagement du seul fait de son absence. Sans cela un relegue resterait
    # engage indefiniment et polluerait le filtre de son ancien championnat.
    # Scope restreint aux championnats CONFORMES de cette passe : un rebuild
    # ne desengage jamais un club d'une ligue qu'il ne reecrit pas (big-5 lors
    # d'un rebuild scope expansion, ligue ecartee pour ecart d'effectif,
    # championnats hors Bzzoiro >= 9000 tenus par `build_universe`).
    await session.execute(
        update(CanonicalTeam)
        .where(CanonicalTeam.season == season)
        .where(CanonicalTeam.league_api_id.in_(par_championnat.keys()))
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

    Le repli par cle_club(nom) n'adopte JAMAIS une ligne qui porte deja un
    bzz_team_id (donc un AUTRE club identifie) ni une ligne engagee en
    domestique : des noms generiques ("Dynamo", "Union", "Victoria"...) sont
    partages entre pays, et un faux match par nom fusionnerait deux clubs
    distincts. Seule une ligne "libre" (sans bzz_team_id, non engagee) peut
    etre adoptee par nom — c'est un vrai doublon de libelle du MEME club pas
    encore identifie.

    Une collision de name_fr entre deux clubs qualif-only reellement
    distincts (bzz_team_id differents) decouverts dans la MEME passe est
    desambiguisee a l'insertion plutot que de laisser la contrainte unique
    faire echouer tout le commit.

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

    # Index par bzz_team_id et par name_fr pris, mis a jour au fil des
    # creations de cette passe. Sans cela, deux clubs qualif-only distincts
    # au meme nom replie prendraient tous deux la branche creation et une
    # collision de name_fr (contrainte unique) ferait echouer tout le
    # commit — perte de TOUTE la passe, pas seulement du second club.
    par_bzz_id: dict[int, CanonicalTeam] = {
        ligne.bzz_team_id: ligne for ligne in toutes if ligne.bzz_team_id is not None
    }
    noms_pris: set[str] = {ligne.name_fr for ligne in toutes if ligne.name_fr}

    sans_championnat = 0
    for club_id, nom in engages.items():
        existant = par_bzz_id.get(club_id)

        if existant is None:
            candidat = par_cle.get(cle_club(nom))
            if (
                candidat is not None
                and candidat.bzz_team_id is None
                and candidat.league_api_id is None
            ):
                # Ligne "libre" : vrai doublon de libelle du MEME club pas
                # encore identifie par bzz_team_id. Une ligne deja engagee
                # (league_api_id pose) ou deja appariee a un AUTRE club
                # (bzz_team_id different) n'est jamais adoptee par nom.
                existant = candidat

        if existant is not None and existant.league_api_id is not None:
            # Deja engage dans un championnat domestique (quelle que soit la
            # saison inscrite) : on ne touche a rien — ni league_api_id, ni
            # country, ni season. Aucun job quotidien ne reconstruit le
            # big-5 : a la bascule de saison, un club big-5 encore sur
            # l'ancienne saison serait sinon desengage par ce job
            # (regression corrigee le 03/11). Le nettoyage des engagements
            # perimes est le role de `rebuild()` (remplacement en bloc par
            # ligue), pas celui d'`attach_european`, dont le seul role est
            # d'enregistrer les clubs qualif-UEFA sans championnat
            # (league_api_id IS NULL).
            continue

        logger.info("club sans championnat domestique : %s (id %s)", nom, club_id)
        sans_championnat += 1

        if existant is None:
            nom_fr = nom
            if nom_fr in noms_pris:
                nom_fr = f"{nom} (id {club_id})"
                logger.info(
                    "name_fr desambigue pour eviter une collision "
                    "entre deux clubs distincts : %s -> %s (id %s)",
                    nom, nom_fr, club_id,
                )
            noms_pris.add(nom_fr)
            nouvelle = CanonicalTeam(
                name_fr=nom_fr,
                name_en=nom,
                bzz_team_id=club_id,
                league_api_id=None,
                season=None,
                country=None,
            )
            session.add(nouvelle)
            par_bzz_id[club_id] = nouvelle
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


# Championnats rattachés SANS segmentation stricte : grands championnats hors
# Europe (`world`, pays renseigné) et championnats féminins (`women`). Ni coupe
# (n'engage pas un club), ni compétition internationale (pays absent).
SANS_SEGMENTATION: tuple[int, ...] = tuple(
    league_id
    for league_id, meta in LEAGUE_REGISTRY.items()
    if meta.country is not None and (
        meta.kind in ("world", "women")
        # Championnat domestique en poules (Liga 3, Campeonato de Portugal) :
        # pas d'effectif fixe, donc hors segmentation stricte.
        or (meta.kind in ("domestic_1", "domestic_2") and meta.format == "groups")
    )
)


async def attach_league_clubs(
    session: AsyncSession,
    client: Any,
    league_ids: list[int] | tuple[int, ...] = SANS_SEGMENTATION,
    season: str | None = None,
) -> dict[int, dict[str, int]]:
    """Rattache les clubs engagés dans des championnats non stricts.

    Rattachement par identifiant Bzzoiro UNIQUEMENT, jamais par le nom : un
    club féminin « Real Madrid » ou un homonyme d'un autre pays ne doit jamais
    écraser une ligne canonique existante. Un club déjà engagé dans un AUTRE
    championnat n'est pas déplacé (championnats scindés Apertura/Clausura,
    double appartenance). Rend {ligue: {"crees": n, "rattaches": n, "deja_ailleurs": n}}.
    """
    from app.models.canonical_teams import CanonicalTeam

    if season is None:
        season = await current_season(session)
    # `name_fr` est unique : « Real Madrid » (féminin) ou un homonyme d'un autre
    # pays reçoit un suffixe plutôt que de faire échouer tout le lot.
    noms_pris = set((await session.execute(select(CanonicalTeam.name_fr))).scalars().all())
    rapport: dict[int, dict[str, int]] = {}
    for league_api_id in league_ids:
        meta = LEAGUE_REGISTRY[league_api_id]
        engages = await enumerer_engages(client, league_api_id, season)
        compte = {"crees": 0, "rattaches": 0, "deja_ailleurs": 0}
        for club_id, nom in engages.items():
            existant = (await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == club_id)
            )).scalar_one_or_none()
            if existant is None:
                libelle = f"{nom} (F)" if meta.kind == "women" else nom
                if libelle in noms_pris:
                    libelle = f"{nom} ({'F' if meta.kind == 'women' else meta.country})"
                if libelle in noms_pris:
                    libelle = f"{libelle} #{club_id}"
                noms_pris.add(libelle)
                session.add(CanonicalTeam(
                    name_fr=libelle, name_en=nom, bzz_team_id=club_id,
                    league_api_id=league_api_id, season=season, country=meta.country,
                ))
                compte["crees"] += 1
            elif existant.league_api_id in (None, league_api_id) or existant.league_api_id >= 9000:
                # >= 9000 : championnat hors registre (portail, « Autres clubs »),
                # supplanté par un championnat Bzzoiro suivi.
                existant.league_api_id = league_api_id
                existant.season = season
                existant.country = meta.country
                # Équipe féminine : toujours « Nom (F) », sans confusion possible
                # avec le club masculin homonyme.
                if meta.kind == "women" and existant.name_en and not existant.name_fr.endswith(" (F)"):
                    libelle = f"{existant.name_en} (F)"
                    if libelle not in noms_pris:
                        noms_pris.discard(existant.name_fr)
                        existant.name_fr = libelle
                        noms_pris.add(libelle)
                compte["rattaches"] += 1
            else:
                compte["deja_ailleurs"] += 1
        await session.commit()
        rapport[league_api_id] = compte
        logger.info("championnat %s (%s) : %s", league_api_id, meta.name, compte)
    return rapport


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
