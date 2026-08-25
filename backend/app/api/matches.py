"""Fiche match — lecture de la base.

Contrairement a wc2026_matches.get_match_detail, qui interroge Bzzoiro en
direct quand le cache est vide, cette fiche lit EXCLUSIVEMENT ce qui est
archive. Elle montre l'etat de la base, comme le Sanctuaire : si l'archive est
incomplete, elle le dit plutot que de masquer le trou.

C'est le role de `blocs_manquants` : une carte des tirs absente et un match
sans aucun tir ne veulent pas dire la meme chose.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.bzzoiro.sync_match_detail import LEAGUES_FICHE_MATCH
from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat
from app.models.fixtures import Fixture
from app.models.canonical_teams import CanonicalTeam
from app.models.lineups import TeamLineup

router = APIRouter(prefix="/matches", tags=["matches"])

# Blocs attendus sur un match termine. Leur absence est signalee, pas masquee.
BLOCS = ("shotmap", "incidents", "momentum", "average_positions")


def _parse_shotmap(brut: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Aplatit la carte des tirs au format attendu par le composant ShotMap.

    Bzzoiro rend `pos: {x, y}` et `home: bool` ; le composant partage avec la
    CDM attend `x`, `y` et `is_home` a plat. On normalise ici plutot que de
    dupliquer le composant.
    """
    out: list[dict[str, Any]] = []
    for t in brut or []:
        pos = t.get("pos") or {}
        out.append({
            "x": pos.get("x", 0),
            "y": pos.get("y", 0),
            "xg": t.get("xg") or 0,
            "xgot": t.get("xgot"),
            "type": t.get("type", "miss"),
            "body": t.get("body"),
            "sit": t.get("sit"),
            "player_id": t.get("player_id"),
            "is_home": bool(t.get("home")),
            "minute": t.get("min"),
        })
    return out


async def _noms_equipes(
    session: AsyncSession, *ids: int | None
) -> dict[int, str]:
    """Noms des clubs, resolus par identifiant via le referentiel.

    bzz_events ne porte que des identifiants. canonical_teams fait autorite :
    c'est le seul chemin fiable depuis la reconstruction du 22/08.
    """
    voulus = [i for i in ids if i is not None]
    if not voulus:
        return {}
    lignes = (await session.execute(
        select(CanonicalTeam.bzz_team_id, CanonicalTeam.name_fr).where(
            CanonicalTeam.bzz_team_id.in_(voulus)
        )
    )).all()
    return {tid: nom for tid, nom in lignes}


def _compo_out(compo: TeamLineup | None) -> dict[str, Any] | None:
    """Compo avec son origine.

    lineup_type dit d'ou elle vient — official, bzzoiro, probable_manual,
    last_known. Pricer sur la derniere compo connue d'une equipe n'est pas
    pricer sur la compo du jour, et cela doit se voir.
    """
    if compo is None:
        return None
    return {
        "team": compo.team,
        "lineup_type": compo.lineup_type,
        "lineup_status": compo.lineup_status,
        "published_at": compo.published_at,
        "players": [
            {
                "player_name": j.player_name,
                "position": j.position,
                "is_starter": j.is_starter,
                "jersey_number": j.jersey_number,
            }
            for j in (compo.players or [])
        ],
    }


@router.get("", response_model=list[dict])
async def list_matches(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(None, description="Filtrer par championnat"),
    team: str | None = Query(None, description="Filtrer par equipe"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """Matchs du perimetre, du plus recent au plus ancien."""
    conditions = [BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH)]
    if league_api_id is not None:
        conditions = [BzzEvent.league_api_id == league_api_id]
    if team:
        conditions.append(
            (BzzEvent.home_team_api_id.is_not(None))
            | (BzzEvent.away_team_api_id.is_not(None))
        )

    rows = (await session.execute(
        select(BzzEvent)
        .where(*conditions)
        .order_by(BzzEvent.event_date.desc())
        .limit(limit)
        .offset(offset)
    )).scalars().all()

    noms = await _noms_equipes(
        session, *[e.home_team_api_id for e in rows],
        *[e.away_team_api_id for e in rows],
    )

    return [
        {
            "event_api_id": e.api_id,
            "event_date": e.event_date,
            "status": e.status,
            "league_api_id": e.league_api_id,
            "home_team": noms.get(e.home_team_api_id) or f"Club {e.home_team_api_id}",
            "away_team": noms.get(e.away_team_api_id) or f"Club {e.away_team_api_id}",
            "home_score": e.home_score,
            "away_score": e.away_score,
            # Un match dont la carte des tirs est absente n'a pas de fiche
            # exploitable : l'interface peut le signaler dans la liste.
            "a_des_donnees": e.shotmap is not None,
        }
        for e in rows
    ]


@router.get("/{event_api_id}", response_model=dict)
async def get_match_detail(
    event_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fiche complete d'un match, lue depuis la base."""
    event = (await session.execute(
        select(BzzEvent).where(BzzEvent.api_id == event_api_id)
    )).scalar_one_or_none()

    if event is None:
        raise HTTPException(status_code=404, detail="Match introuvable")

    manquants = [b for b in BLOCS if not getattr(event, b, None)]

    noms = await _noms_equipes(
        session, event.home_team_api_id, event.away_team_api_id
    )
    nom_dom = noms.get(event.home_team_api_id) or f"Club {event.home_team_api_id}"
    nom_ext = noms.get(event.away_team_api_id) or f"Club {event.away_team_api_id}"

    # Compos : celle de plus haute priorite pour chaque camp.
    compos: dict[str, TeamLineup] = {}
    fixture = (await session.execute(
        select(Fixture).where(Fixture.external_id == f"bzz_{event_api_id}")
    )).scalar_one_or_none()

    if fixture is not None:
        from app.ingestion.lineup_resolver import PRIORITY

        lignes = (await session.execute(
            select(TeamLineup).where(TeamLineup.fixture_id == fixture.id)
        )).scalars().all()
        for ligne in lignes:
            actuelle = compos.get(ligne.team)
            if actuelle is None or PRIORITY.get(ligne.lineup_type, 99) < PRIORITY.get(
                actuelle.lineup_type, 99
            ):
                compos[ligne.team] = ligne

    stats = (await session.execute(
        select(BzzPlayerMatchStat).where(
            BzzPlayerMatchStat.event_api_id == event_api_id
        )
    )).scalars().all()

    return {
        "event_api_id": event.api_id,
        "event_date": event.event_date,
        "status": event.status,
        "league_api_id": event.league_api_id,
        "round_number": event.round_number,
        "home_team": nom_dom,
        "away_team": nom_ext,
        "home_team_api_id": event.home_team_api_id,
        "away_team_api_id": event.away_team_api_id,
        "home_score": event.home_score,
        "away_score": event.away_score,
        "home_score_ht": event.home_score_ht,
        "away_score_ht": event.away_score_ht,
        "home_xg": event.home_xg,
        "away_xg": event.away_xg,
        "shotmap": _parse_shotmap(event.shotmap),
        "incidents": event.incidents or [],
        "momentum": event.momentum or [],
        "average_positions": event.average_positions or [],
        "home_lineup": _compo_out(compos.get(nom_dom)),
        "away_lineup": _compo_out(compos.get(nom_ext)),
        "player_stats": [
            {
                "player_api_id": s.player_api_id,
                "is_home": s.is_home,
                "minutes_played": s.minutes_played,
                "rating": s.rating,
                "goals": s.goals,
                "goal_assist": s.goal_assist,
                "expected_goals": s.expected_goals,
                "total_shots": s.total_shots,
            }
            for s in stats
        ],
        # Distingue un bloc ABSENT d'un bloc vide : zero tir et pas de donnees
        # de tirs ne veulent pas dire la meme chose.
        "blocs_manquants": manquants,
    }
