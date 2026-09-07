"""Ingestion des donnees de match depuis l'API Bzzoiro v2.

Les colonnes shotmap / incidents / momentum / average_positions / lineups de
bzz_events existent depuis la CDM 2026, mais sont restees vides pour le
football de clubs : sync_events lit `row.get("shotmap")` sur /api/events/, qui
ne renvoie pas ce champ. Mesure du 25/08/2026 sur les cinq championnats :
0 compo et 0 carte de tirs sur 8 965 matchs termines.

L'API v2 les fournit toutes. Deux regimes, dictes par la nature de la donnee :

- AVANT match, seules les compos evoluent. Bzzoiro publie une compo
  "predicted" un a deux jours avant (mesure : Real Madrid-Real Sociedad a
  J-23h, Barcelone-Athletic a J-47h), puis la passe a "confirmed" peu avant le
  coup d'envoi.
- APRES match, tout devient definitif : tirs, momentum, positions moyennes,
  incidents, et la compo officielle. On interroge une fois, et on n'y
  revient plus.

La compo des matchs PASSES n'est pas un agrement d'affichage. Sans elle, le
titulaire se devine par son temps de jeu : un titulaire remplace a la 60e
compte pour un remplacant, un entrant de la 20e compte pour un titulaire.
Toute estimation d'un rythme "sur 90 minutes" hérite de cette confusion.

Aucun de ces points d'acces n'est pagine : get_page, jamais get_all.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS
from app.ingestion.bzzoiro.sync_bzzoiro_lineups import _POSITION_MAP

logger = logging.getLogger(__name__)

# Championnats du perimetre : les cinq domestiques. La Ligue des champions est
# exclue jusqu'aux tirages de la phase de ligue.
LEAGUES_FICHE_MATCH: list[int] = [
    v for k, v in TARGET_LEAGUE_INTERNAL_IDS.items() if k != "champions_league"
]

# Rythme de veille sur un match a venir, une fois la compo probable captee.
# Interroger toutes les 5 minutes pendant deux jours ne rapporte rien : deux
# points de controle, puis une veille serree quand l'officielle va paraitre.
CONTROLES_INTERMEDIAIRES = (timedelta(hours=24), timedelta(hours=6))
VEILLE_SERREE = timedelta(minutes=90)


def sans_donnees():
    """Clause selectionnant les matchs dont la carte des tirs manque.

    PIEGE JSONB : sync_events ecrivait `row.get("shotmap")` -- soit None --
    dans une colonne JSONB, ce qui stocke `null` au sens JSON et non NULL au
    sens SQL. Un filtre `shotmap IS NULL` ne correspond alors a RIEN : le
    rattrapage annoncait 0 traite indefiniment, sans que rien ne l'explique.

    Mesure du 25/08/2026 : sur les 8 965 matchs termines du perimetre,
    jsonb_typeof(shotmap) vaut 'null' pour les 8 965.

    Une liste VIDE n'est PAS reprise : elle signifie "verifie, Bzzoiro n'a
    pas de tirs pour ce match". Les donnees de tirs ne remontent pas avant
    2025 ; sans cette marque, le job horaire reinterrogerait 6 200 matchs
    anciens indefiniment, 600 requetes par heure pour rien.
    """
    from app.models.bzzoiro import BzzEvent

    return or_(
        BzzEvent.shotmap.is_(None),
        func.jsonb_typeof(BzzEvent.shotmap) == "null",
    )


def sans_compos():
    """Clause selectionnant les matchs termines dont la compo manque.

    Meme piege JSONB que ci-dessus, et meme convention : un objet VIDE
    signifie "verifie, Bzzoiro n'a pas la compo" et sort le match de la file.

    Pourquoi les compos des matchs PASSES comptent : sans elles, on devine le
    titulaire par son temps de jeu. Un titulaire remplace a la 60e passe alors
    pour un remplacant, et un entrant de la 20e passe pour un titulaire. Cette
    approximation contamine toute estimation d'un rythme "sur 90 minutes".

    Verifie le 31/08/2026 : Bzzoiro rend une compo confirmee sur les cinq
    saisons, jusqu'au 31/12/2021 -- titulaires, remplacants, formation,
    numeros et capitaine.
    """
    from app.models.bzzoiro import BzzEvent

    return or_(
        BzzEvent.lineups.is_(None),
        func.jsonb_typeof(BzzEvent.lineups) == "null",
    )


# ── Recuperation ────────────────────────────────────────────────────────────


async def fetch_lineups(client: Any, event_api_id: int) -> dict[str, Any] | None:
    """Compos publiees pour ce match, ou None si aucune."""
    try:
        return await client.get_page(f"/api/v2/events/{event_api_id}/lineups/")
    except Exception as exc:
        logger.debug("compos indisponibles pour %s : %s", event_api_id, exc)
        return None


async def fetch_match_stats(client: Any, event_api_id: int) -> dict[str, Any] | None:
    """Statistiques, carte des tirs, momentum et positions moyennes."""
    try:
        return await client.get_page(f"/api/v2/events/{event_api_id}/stats/")
    except Exception as exc:
        logger.debug("stats indisponibles pour %s : %s", event_api_id, exc)
        return None


async def fetch_incidents(client: Any, event_api_id: int) -> list[dict] | None:
    """Buts, cartons et periodes."""
    try:
        data = await client.get_page(f"/api/v2/events/{event_api_id}/incidents/")
    except Exception as exc:
        logger.debug("incidents indisponibles pour %s : %s", event_api_id, exc)
        return None
    return data.get("incidents") if isinstance(data, dict) else data


# ── Statut de la compo ──────────────────────────────────────────────────────


def _a_des_titulaires(brut: dict[str, Any] | None) -> bool:
    """Vrai si la reponse porte au moins un onze de depart.

    Bzzoiro repond parfois une enveloppe sans joueur : c'est un "il n'y a pas
    de compo", pas un echec. Sans ce test on archiverait une coquille vide en
    croyant avoir la donnee.
    """
    blocs = (brut or {}).get("lineups") or {}
    return any((blocs.get(cote) or {}).get("players") for cote in ("home", "away"))


def est_confirmee(brut: dict[str, Any] | None) -> bool:
    """Vrai si Bzzoiro declare la compo officielle.

    Valeurs observees le 25/08/2026 : "predicted" pour une compo probable
    publiee un a deux jours avant, "confirmed" pour l'officielle.
    """
    return bool(brut) and brut.get("lineup_status") == "confirmed"


def type_de_compo(brut: dict[str, Any] | None) -> str:
    """Type attendu par lineup_resolver.PRIORITY.

    official (0) quand Bzzoiro confirme, bzzoiro (1) sinon. Les deux
    coexistent : c'est ce qui historise la compo probable une fois
    l'officielle publiee, sans table supplementaire.
    """
    return "official" if est_confirmee(brut) else "bzzoiro"


# ── Regle d'interrogation ───────────────────────────────────────────────────


async def doit_interroger(
    session: AsyncSession,
    fixture_id: int,
    coup_envoi: datetime | None,
    maintenant: datetime,
) -> bool:
    """Faut-il interroger l'API pour ce match, maintenant ?

    - compo officielle en base -> non, elle ne changera plus ;
    - aucune compo -> oui, on cherche la probable ;
    - probable en base -> une requete a H-24, une a H-6, puis veille serree
      dans les 90 dernieres minutes.

    Les deux controles intermediaires rattrapent une compo revisee entre-temps
    (blessure a l'entrainement) sans payer deux jours de veille.
    """
    from app.models.lineups import TeamLineup

    lignes = (await session.execute(
        select(TeamLineup.lineup_type, TeamLineup.updated_at)
        .where(TeamLineup.fixture_id == fixture_id)
    )).all()

    if any(t == "official" for t, _ in lignes):
        return False
    if not lignes:
        return True
    if coup_envoi is None:
        # Sans coup d'envoi connu, mieux vaut une requete de trop qu'une compo
        # manquee.
        return True

    reste = coup_envoi - maintenant
    if reste <= VEILLE_SERREE:
        return True

    derniere = max((u for _, u in lignes if u is not None), default=None)
    if derniere is None:
        return True

    # Un controle par fenetre : on n'interroge que si la derniere capture date
    # d'avant l'entree dans cette fenetre.
    for seuil in CONTROLES_INTERMEDIAIRES:
        if reste <= seuil and derniere < coup_envoi - seuil:
            return True

    return False


# ── Ecriture des compos ─────────────────────────────────────────────────────


def _entier(v: Any) -> int | None:
    """Le numero de maillot est parfois rendu comme une chaine."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def ecrire_compos(
    session: AsyncSession,
    fixture_id: int,
    equipes: dict[str, str],
    brut: dict[str, Any] | None,
) -> int:
    """Ecrit une ligne team_lineups par camp pourvu. Rend le nombre ecrit.

    Ne remplace que la ligne du MEME type : une compo probable deja en base
    survit a la publication de l'officielle, ce qui historise sans table
    supplementaire.
    """
    from app.models.lineups import TeamLineup, TeamLineupPlayer

    if not brut:
        return 0

    blocs = brut.get("lineups") or {}
    statut = brut.get("lineup_status")
    publie = _parse_date(brut.get("updated_at"))
    type_compo = type_de_compo(brut)

    ecrites = 0
    for cote, nom_equipe in equipes.items():
        bloc = blocs.get(cote) or {}
        titulaires = bloc.get("players") or []
        if not titulaires:
            continue

        existante = (await session.execute(
            select(TeamLineup).where(
                TeamLineup.fixture_id == fixture_id,
                TeamLineup.team == nom_equipe,
                TeamLineup.lineup_type == type_compo,
            )
        )).scalar_one_or_none()

        if existante is not None:
            await session.delete(existante)
            await session.flush()

        compo = TeamLineup(
            fixture_id=fixture_id,
            team=nom_equipe,
            lineup_type=type_compo,
            source="bzzoiro_v2",
            created_by="system",
            lineup_status=statut,
            published_at=publie,
        )
        session.add(compo)
        await session.flush()

        remplacants = bloc.get("substitutes") or []
        tous = [(x, True) for x in titulaires] + [(x, False) for x in remplacants]
        for j, titulaire in tous:
            session.add(TeamLineupPlayer(
                lineup_id=compo.id,
                player_name=j.get("name") or "",
                # L'API rend G/D/M/F, le modele attend GK/DEF/MID/FWD.
                position=_POSITION_MAP.get(j.get("position") or "", "MID"),
                is_starter=titulaire,
                jersey_number=_entier(j.get("jersey_number")),
            ))
        ecrites += 1

    if ecrites:
        await session.commit()
    return ecrites


# ── Orchestration ───────────────────────────────────────────────────────────


async def sync_avant_match(
    session: AsyncSession, client: Any, heures: int = 72
) -> tuple[int, int]:
    """Compos des matchs a venir. Rend (matchs interroges, compos ecrites).

    Fenetre de 72 h : Bzzoiro publie une compo probable jusqu'a deux jours
    avant le match, ce qui permet de pricer tot.
    """
    from app.models.bzzoiro import BzzEvent
    from app.models.fixtures import Fixture

    maintenant = datetime.now(UTC)
    evenements = (await session.execute(
        select(BzzEvent).where(
            BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH),
            BzzEvent.event_date > maintenant,
            BzzEvent.event_date <= maintenant + timedelta(hours=heures),
        )
    )).scalars().all()

    interroges = ecrites = 0
    for ev in evenements:
        # Le lien se fait par external_id, pas par une cle etrangere :
        # convention deja utilisee par sync_bzzoiro_lineups.
        fixture = (await session.execute(
            select(Fixture).where(Fixture.external_id == f"bzz_{ev.api_id}")
        )).scalar_one_or_none()
        if fixture is None:
            continue

        if not await doit_interroger(session, fixture.id, ev.event_date, maintenant):
            continue

        interroges += 1
        brut = await fetch_lineups(client, ev.api_id)
        ecrites += await ecrire_compos(
            session, fixture.id,
            {"home": fixture.home_team, "away": fixture.away_team},
            brut,
        )

    logger.info(
        "Compos avant match : %d matchs interroges, %d compos ecrites",
        interroges, ecrites,
    )
    return interroges, ecrites


async def sync_apres_match(
    session: AsyncSession, client: Any, limite: int = 200
) -> tuple[int, int]:
    """Tirs et compos des matchs termines. Rend (traites tirs, sans tirs).

    Deux donnees independantes, deux files : un match dont la carte des tirs
    est deja prise peut rester en attente de sa compo, et reciproquement.
    Chacune connait les trois memes issues :
      - donnee presente -> ecrite ;
      - Bzzoiro ne l'a pas -> marque vide, le match sort de CETTE file ;
      - l'appel echoue -> laisse a null, il sera retente.

    La distinction compte : sans elle, les 6 200 matchs anterieurs a 2025 --
    pour lesquels Bzzoiro n'a aucun tir -- seraient reinterroges a chaque
    passage horaire, indefiniment.
    """
    from app.models.bzzoiro import BzzEvent

    evenements = (await session.execute(
        select(BzzEvent).where(
            BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH),
            BzzEvent.status == "finished",
            or_(sans_donnees(), sans_compos()),
        ).order_by(BzzEvent.event_date.desc()).limit(limite)
    )).scalars().all()

    traites = sans_tirs = echecs = 0
    compos = sans_compo = echecs_compos = 0
    for ev in evenements:
        # JSONB null se relit en None cote Python : meme test que la clause SQL.
        if ev.shotmap is None:
            stats = await fetch_match_stats(client, ev.api_id)

            if stats is None:
                # L'appel a echoue : on laisse le match a null pour le retenter.
                echecs += 1
            elif not stats.get("shotmap"):
                # Bzzoiro connait le match mais n'a pas de tirs -- le cas de
                # tous les matchs anterieurs a 2025. La liste vide marque
                # "verifie, rien a prendre" et sort le match de la file.
                ev.shotmap = []
                sans_tirs += 1
            else:
                ev.shotmap = stats.get("shotmap")
                ev.momentum = stats.get("momentum")
                ev.average_positions = stats.get("average_positions")

                incidents = await fetch_incidents(client, ev.api_id)
                if incidents:
                    ev.incidents = incidents

                traites += 1

        if ev.lineups is None:
            brut = await fetch_lineups(client, ev.api_id)
            if brut is None:
                echecs_compos += 1
            elif not _a_des_titulaires(brut):
                ev.lineups = {}
                sans_compo += 1
            else:
                # On archive la reponse entiere, statut et horodatage compris,
                # et non le seul bloc "lineups" : sync_bzzoiro_lineups sait
                # deballer les deux formes.
                ev.lineups = brut
                compos += 1

    if traites or sans_tirs or compos or sans_compo:
        await session.commit()

    logger.info(
        "Apres match : tirs %d traites / %d sans tirs / %d echecs ; "
        "compos %d ecrites / %d absentes / %d echecs",
        traites, sans_tirs, echecs, compos, sans_compo, echecs_compos,
    )
    return traites, sans_tirs
