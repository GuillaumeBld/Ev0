"""Résolution de la composition active pour un (fixture_id, team).

Priorité : official > probable_manual > last_known
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer

logger = logging.getLogger(__name__)

# Compos enregistrées par nom (saisie manuelle, ancien import) : elles ne sont
# utilisables que si chaque titulaire porte son identifiant Atlas
# (team_lineup_players.player_api_id, migration 069). Sinon on passe à la
# source suivante, jamais de rapprochement par nom.
MANUAL_TYPES: frozenset[str] = frozenset({"probable_manual", "probable_statshub"})

PRIORITY: dict[str, int] = {
    "official": 0,
    "bzzoiro": 1,
    "probable_manual": 2,
    "probable_statshub": 3,  # legacy — no longer created, kept for existing DB rows
    "last_known": 4,
}


def manual_lineup_has_ids(players: list) -> bool:
    """Vrai si chaque titulaire de la compo porte son identifiant Atlas."""
    return all(
        getattr(p, "player_api_id", None) is not None
        for p in players
        if getattr(p, "is_starter", True)
    )


def _usable(lineup_type: str, players: list, team: str, fixture_id: int) -> bool:
    if lineup_type not in MANUAL_TYPES or manual_lineup_has_ids(players):
        return True
    logger.warning(
        "resolve_lineup: compo %s de %s (match %s) enregistrée sans identifiants "
        "— ignorée, source suivante",
        lineup_type, team, fixture_id,
    )
    return False


@dataclass
class ResolvedLineup:
    lineup_type: str
    team: str
    fixture_id: int
    players: list[TeamLineupPlayer]
    lineup_id: int | None = None


async def resolve_lineup(
    fixture_id: int,
    team: str,
    session: AsyncSession,
    _overrides: list | None = None,  # hook pour les tests
) -> ResolvedLineup | None:
    """Retourne la compo de priorité la plus haute pour ce fixture+team."""
    if _overrides is not None:
        candidates = [
            lu for lu in _overrides
            if _usable(lu.lineup_type, list(lu.players), team, fixture_id)
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda lu: PRIORITY.get(lu.lineup_type, 99))
        return ResolvedLineup(
            lineup_type=best.lineup_type,
            team=team,
            fixture_id=fixture_id,
            players=best.players,
            lineup_id=getattr(best, "id", None),
        )

    # 1. Chercher official ou probable_manual pour ce fixture précis
    result = await session.execute(
        select(TeamLineup).where(
            TeamLineup.fixture_id == fixture_id,
            TeamLineup.team == team,
            TeamLineup.lineup_type.in_(["official", "bzzoiro", "probable_manual", "probable_statshub"]),
        )
    )
    rows = result.scalars().all()
    # Use .get(..., 99) for consistency with the _overrides path
    for best in sorted(rows, key=lambda lu: PRIORITY.get(lu.lineup_type, 99)):
        players_result = await session.execute(
            select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == best.id)
        )
        players = players_result.scalars().all()
        if not _usable(best.lineup_type, players, team, fixture_id):
            continue
        return ResolvedLineup(
            lineup_type=best.lineup_type,
            team=team,
            fixture_id=fixture_id,
            players=players,
            lineup_id=best.id,
        )

    # 2. Fallback : dernière compo officielle connue pour cette équipe
    fx_result = await session.execute(
        select(Fixture.kickoff_utc).where(Fixture.id == fixture_id)
    )
    kickoff = fx_result.scalar_one_or_none()
    if kickoff is None:
        return None

    prev_result = await session.execute(
        select(TeamLineup)
        .join(Fixture, TeamLineup.fixture_id == Fixture.id)
        .where(
            TeamLineup.team == team,
            TeamLineup.lineup_type == "official",
            Fixture.kickoff_utc < kickoff,
        )
        .order_by(Fixture.kickoff_utc.desc())
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()
    if prev is None:
        return None

    players_result = await session.execute(
        select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == prev.id)
    )
    return ResolvedLineup(
        lineup_type="last_known",
        team=team,
        fixture_id=fixture_id,
        players=players_result.scalars().all(),
        lineup_id=prev.id,
    )
