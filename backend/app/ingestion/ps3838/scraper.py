"""Scraper PS3838 -> MatchScrapeResult.

Le rattachement se fait EXCLUSIVEMENT par fixtures.ps3838_event_id. Aucun nom
d'equipe n'intervient ici : c'est ce qui rend le mauvais rattachement impossible.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.ps3838.anchor import MAX_KICKOFF_DELTA
from app.ingestion.ps3838.client import Ps3838Event, fetch_events
from app.ingestion.scrape_result import MatchScrapeResult

logger = logging.getLogger(__name__)

BOOKMAKER = "ps3838"


def _kickoff_drifted(fx_ko: datetime | None, ev_ko: datetime | None) -> bool:
    """Filet de securite : l'ancrage est pose une fois pour toutes, mais si
    l'identifiant PS3838 a ete reattribue depuis (ou l'ancrage etait deja
    mauvais), le coup d'envoi ne colle plus. Rejette au-dela de 2h d'ecart,
    la meme tolerance qu'a la pose de l'ancrage dans anchor.py."""
    if fx_ko is None or ev_ko is None:
        return True
    if fx_ko.tzinfo is None:
        fx_ko = fx_ko.replace(tzinfo=UTC)
    if ev_ko.tzinfo is None:
        ev_ko = ev_ko.replace(tzinfo=UTC)
    return abs(ev_ko - fx_ko) > MAX_KICKOFF_DELTA


def build_results(fixtures, events: list[Ps3838Event]) -> list[MatchScrapeResult]:
    """Un resultat par fixture ancree dont l'evenement porte 1X2 ET totals."""
    by_id = {ev.event_id: ev for ev in events}
    now = datetime.now(UTC)
    out: list[MatchScrapeResult] = []

    for fx in fixtures:
        eid = getattr(fx, "ps3838_event_id", None)
        if eid is None:
            continue
        ev = by_id.get(eid)
        if ev is None or not ev.h2h or not ev.totals:
            continue
        if _kickoff_drifted(fx.kickoff_utc, ev.kickoff_utc):
            logger.warning(
                "PS3838: ecart de coup d'envoi > 2h pour fixture %s (event %s) -> rejete",
                fx.id, eid,
            )
            continue
        out.append(
            MatchScrapeResult(
                fixture_id=fx.id,
                home_team=fx.home_team,
                away_team=fx.away_team,
                kickoff_utc=fx.kickoff_utc,
                league=fx.league,
                bookmaker=BOOKMAKER,
                scraped_at=now,
                h2h=dict(ev.h2h),
                totals=dict(ev.totals),
                btts=None,  # PS3838 n'expose pas ce marche dans ce flux
            )
        )
    return out


async def scrape_ps3838(
    session: AsyncSession, fixture_ids: list[int] | None = None
) -> list[MatchScrapeResult]:
    """Lit le flux PS3838 et produit les resultats des fixtures ancrees."""
    from app.models.fixtures import Fixture

    stmt = select(Fixture).where(Fixture.ps3838_event_id.isnot(None))
    if fixture_ids:
        stmt = stmt.where(Fixture.id.in_(fixture_ids))
    fixtures = (await session.execute(stmt)).scalars().all()
    if not fixtures:
        return []

    events = await fetch_events()
    results = build_results(fixtures, events)
    logger.info("PS3838: %d resultats pour %d fixtures ancrees", len(results), len(fixtures))
    return results
