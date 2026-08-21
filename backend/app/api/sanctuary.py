"""API lecture seule de la bibliotheque des xG.

La table team_xg_estimates archive, pour chaque match, l'ouverture et la
cloture : les lambdas des deux equipes et les cotes brutes qui les ont produits.
Elle n'avait aucun acces en lecture -- d'ou son invisibilite.

Cette API ne calcule rien d'autre que l'amplitude du mouvement. Elle ne touche
pas aux snapshots de cotes et ne recalcule aucun lambda : elle montre ce qui est
archive, ni plus ni moins.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.ps3838.anchor import _fold as _fold_anchor
from app.models.fixtures import Fixture
from app.models.team_xg import TeamXgEstimate

router = APIRouter(tags=["sanctuary"])

_ISSUES = ("home", "draw", "away")


def _fold(name: str) -> str:
    """Nom plie pour la recherche par equipe.

    Delegue a anchor._fold, qui traite les lettres non decomposables
    (o barre, l barre, thorn...) que l'encodage ascii supprimerait sinon.
    On compacte en plus les espaces pour que 'man utd' trouve 'Man. Utd'.
    """
    return re.sub(r"\s+", " ", _fold_anchor(name)).strip()


def max_move_pct(opening_odds: dict | None, closing_odds: dict | None) -> float | None:
    """Plus grand mouvement relatif parmi les trois cotes du 1X2, en pourcent.

    On retient le MAXIMUM et non la moyenne : un seul camp qui decroche est
    precisement le signal recherche, une moyenne le diluerait avec les issues
    restees immobiles.

    None si l'une des deux phases manque, si le 1X2 est incomplet, ou si une
    cote d'ouverture est nulle (donnee aberrante -- on ne divise pas par zero).
    """
    if not opening_odds or not closing_odds:
        return None
    ouv, clo = opening_odds.get("h2h") or {}, closing_odds.get("h2h") or {}
    if not all(k in ouv and k in clo for k in _ISSUES):
        return None

    ecarts = []
    for k in _ISSUES:
        try:
            a, b = float(ouv[k]), float(clo[k])
        except (TypeError, ValueError):
            return None
        if a <= 0:
            return None
        ecarts.append(abs(b - a) / a * 100)
    return round(max(ecarts), 2)


class PhaseOut(BaseModel):
    as_of_utc: str
    odds: dict
    xg_home: float
    xg_away: float


class SanctuaryMatchOut(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    league: str | None
    kickoff_utc: str
    opening: PhaseOut | None
    closing: PhaseOut | None
    max_move_pct: float | None


def _phase(est: TeamXgEstimate) -> PhaseOut:
    return PhaseOut(
        as_of_utc=est.as_of_utc.isoformat(),
        odds=est.odds or {},
        xg_home=est.lambda_home,
        xg_away=est.lambda_away,
    )


@router.get("/sanctuary/leagues", response_model=list[str])
async def list_leagues(db: AsyncSession = Depends(get_db)) -> list[str]:
    """Ligues reellement presentes dans la bibliotheque, pas une liste figee."""
    rows = (await db.execute(
        select(Fixture.league)
        .join(TeamXgEstimate, TeamXgEstimate.fixture_id == Fixture.id)
        .where(Fixture.league.isnot(None))
        .distinct()
        .order_by(Fixture.league)
    )).scalars().all()
    return [r for r in rows if r]


@router.get("/sanctuary/matches", response_model=list[SanctuaryMatchOut])
async def list_matches(
    team: str | None = Query(None, description="Nom d'equipe, les deux cotes"),
    league: str | None = Query(None),
    with_closing: bool = Query(False, description="Seulement les archives completes"),
    min_move: float | None = Query(None, ge=0, description="Amplitude minimale en %"),
    db: AsyncSession = Depends(get_db),
) -> list[SanctuaryMatchOut]:
    """Matchs archives, du plus recent au plus ancien.

    team et league filtrent en SQL ; with_closing et min_move s'appliquent apres
    regroupement des deux phases, qui est necessaire pour les evaluer.
    """
    stmt = (
        select(TeamXgEstimate, Fixture)
        .join(Fixture, Fixture.id == TeamXgEstimate.fixture_id)
        .order_by(Fixture.kickoff_utc.desc())
    )
    if league:
        stmt = stmt.where(Fixture.league == league)

    rows = (await db.execute(stmt)).all()

    # Un seuil d'amplitude n'a de sens que sur un match ayant sa cloture.
    exiger_cloture = with_closing or min_move is not None

    besoin = _fold(team) if team else None
    par_match: dict[int, dict] = {}
    ordre: list[int] = []
    for est, fx in rows:
        if besoin and besoin not in _fold(fx.home_team) and besoin not in _fold(fx.away_team):
            continue
        if fx.id not in par_match:
            par_match[fx.id] = {"fixture": fx, "opening": None, "closing": None}
            ordre.append(fx.id)
        par_match[fx.id][est.phase] = est

    sortie: list[SanctuaryMatchOut] = []
    for fid in ordre:
        bloc = par_match[fid]
        fx, ouv, clo = bloc["fixture"], bloc["opening"], bloc["closing"]
        if exiger_cloture and clo is None:
            continue
        move = max_move_pct(ouv.odds if ouv else None, clo.odds if clo else None)
        if min_move is not None and (move is None or move < min_move):
            continue
        sortie.append(SanctuaryMatchOut(
            fixture_id=fx.id,
            home_team=fx.home_team,
            away_team=fx.away_team,
            league=fx.league,
            kickoff_utc=fx.kickoff_utc.isoformat(),
            opening=_phase(ouv) if ouv else None,
            closing=_phase(clo) if clo else None,
            max_move_pct=move,
        ))
    return sortie
