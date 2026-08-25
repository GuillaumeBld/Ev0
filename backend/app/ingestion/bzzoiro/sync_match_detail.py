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
- APRES match, tout devient definitif. On interroge une fois, et on n'y
  revient plus.

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
    """
    from app.models.bzzoiro import BzzEvent

    return or_(
        BzzEvent.shotmap.is_(None),
        func.jsonb_typeof(BzzEvent.shotmap) == "null",
        # Liste VIDE seulement : une liste pourvue est deja traitee, la
        # reprendre la retraiterait a chaque passage.
        # Comparaison directe plutot que jsonb_array_length : cette fonction
        # leve sur une valeur qui n'est pas une liste, et PostgreSQL ne
        # garantit pas l'evaluation paresseuse d'un OR.
        # La liste Python vide se lie en '[]'::jsonb ; cast("[]", JSONB)
        # produirait '"[]"', une chaine JSON qui ne correspond a rien.
        BzzEvent.shotmap == [],
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
    """Stats et carte des tirs des matchs termines. Rend (traites, incomplets).

    Un match dont la carte des tirs revient vide n'est PAS marque traite : il
    sera retente. Ecrire du vide sans le signaler est ce qui a laisse 8 965
    matchs sans donnees pendant des mois.
    """
    from app.models.bzzoiro import BzzEvent

    evenements = (await session.execute(
        select(BzzEvent).where(
            BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH),
            BzzEvent.status == "finished",
            sans_donnees(),
        ).order_by(BzzEvent.event_date.desc()).limit(limite)
    )).scalars().all()

    traites = incomplets = 0
    for ev in evenements:
        stats = await fetch_match_stats(client, ev.api_id)
        if not stats or not stats.get("shotmap"):
            incomplets += 1
            continue

        ev.shotmap = stats.get("shotmap")
        ev.momentum = stats.get("momentum")
        ev.average_positions = stats.get("average_positions")

        incidents = await fetch_incidents(client, ev.api_id)
        if incidents:
            ev.incidents = incidents

        traites += 1

    if traites:
        await session.commit()

    logger.info("Apres match : %d traites, %d incomplets", traites, incomplets)
    return traites, incomplets
