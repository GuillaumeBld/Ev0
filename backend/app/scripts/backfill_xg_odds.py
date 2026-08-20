"""Rattrapage : remplit team_xg_estimates.odds sur les lignes deja archivees.

Les estimations ecrites avant la migration 053 portent odds = NULL. Elles
designent leurs snapshots par input_snapshot_ids, et ces lignes existent encore
tant que job_purge_old_snapshots (45 jours) ne les a pas effacees.

FENETRE LIMITEE : passe ce delai, ces estimations restent definitivement sans
les cotes qui les ont produites. A executer des le deploiement.

Le script ne fabrique jamais rien : une ligne dont les snapshots ont disparu
reste a NULL et est comptee comme impossible.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def rebuild_markets(rows) -> dict[str, dict[str, float]]:
    """Reconstruit {marche: {issue: cote}} depuis des lignes de snapshot."""
    markets: dict[str, dict[str, float]] = {}
    for r in rows:
        markets.setdefault(r.market_type, {})[r.outcome] = r.odds
    return markets


async def backfill(session: AsyncSession) -> tuple[int, int]:
    """Remplit odds la ou il manque. Retourne (rattrapees, impossibles)."""
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    cibles = (await session.execute(
        select(TeamXgEstimate).where(TeamXgEstimate.odds.is_(None))
    )).scalars().all()

    if not cibles:
        logger.info("Aucune estimation a rattraper.")
        return 0, 0

    rattrapees = impossibles = 0
    for est in cibles:
        ids = est.input_snapshot_ids or []
        if not ids:
            impossibles += 1
            logger.warning(
                "fixture %s / %s : aucun snapshot reference", est.fixture_id, est.phase
            )
            continue

        rows = (await session.execute(
            select(MatchOddsSnapshot).where(MatchOddsSnapshot.id.in_(ids))
        )).scalars().all()

        markets = rebuild_markets(rows)
        if not markets:
            impossibles += 1
            logger.warning(
                "fixture %s / %s : snapshots purges, cotes irrecuperables",
                est.fixture_id, est.phase,
            )
            continue

        est.odds = markets
        rattrapees += 1

    await session.commit()
    logger.info("Rattrapage : %d remplies, %d impossibles", rattrapees, impossibles)
    return rattrapees, impossibles


async def _main() -> None:
    from app.db import async_session

    async with async_session() as session:
        await backfill(session)


if __name__ == "__main__":
    asyncio.run(_main())
