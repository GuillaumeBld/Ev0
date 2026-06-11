"""Résolution de la composition active pour un (fixture_id, team).

Priorité : official > probable_manual > last_known
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer

PRIORITY: dict[str, int] = {
    "official": 0,
    "bzzoiro": 1,
    "probable_manual": 2,
    "probable_statshub": 3,  # legacy — no longer created, kept for existing DB rows
    "last_known": 4,
}


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
        if not _overrides:
            return None
        best = min(_overrides, key=lambda lu: PRIORITY.get(lu.lineup_type, 99))
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
    if rows:
        # Use .get(..., 99) for consistency with the _overrides path
        best = min(rows, key=lambda lu: PRIORITY.get(lu.lineup_type, 99))
        players_result = await session.execute(
            select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == best.id)
        )
        return ResolvedLineup(
            lineup_type=best.lineup_type,
            team=team,
            fixture_id=fixture_id,
            players=players_result.scalars().all(),
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
