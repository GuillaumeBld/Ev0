"""Bibliotheque des xG de reference : ouverture et closing.

Le closing est l'estimation la plus affutee que le marche produise : c'est la
reference contre laquelle un modele se juge. L'ouverture, comparee au closing,
mesure de combien le marche a bouge.

La purge efface match_odds_snapshots au-dela de 45 jours : passe ce delai, plus
aucun moyen de recalculer un closing. D'ou l'archivage definitif ici.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.market_xg import (
    XG_BOOKMAKER,
    cross_validate_line,
    multiplicative_devig,
    solve_lambda_home_from_h2h,
    solve_lambda_t_from_line,
)

logger = logging.getLogger(__name__)


async def _snapshot_group(session: AsyncSession, fixture_id: int, snapshot_utc):
    """Cotes PS3838 d'un instant precis, groupees par marche."""
    from app.models.match_odds import MatchOddsSnapshot

    rows = (await session.execute(
        select(MatchOddsSnapshot).where(
            MatchOddsSnapshot.fixture_id == fixture_id,
            MatchOddsSnapshot.bookmaker == XG_BOOKMAKER,
            MatchOddsSnapshot.snapshot_utc == snapshot_utc,
        )
    )).scalars().all()

    markets: dict[str, dict[str, float]] = {}
    ids: list[int] = []
    for r in rows:
        markets.setdefault(r.market_type, {})[r.outcome] = r.odds
        ids.append(r.id)
    return markets, ids


def _solve(markets: dict) -> tuple[float, float, float] | None:
    """(lambda_dom, lambda_ext, residu) ou None si les cotes sont inexploitables."""
    h2h, totals = markets.get("h2h"), markets.get("totals")
    if not h2h or not totals:
        return None
    if not all(k in h2h for k in ("home", "draw", "away")):
        return None

    over_key = next((k for k in totals if k.startswith("over_")), None)
    if over_key is None:
        return None
    suffix = over_key.removeprefix("over_")
    under_key = f"under_{suffix}"
    if under_key not in totals:
        return None

    try:
        p_home, _, _ = multiplicative_devig([h2h["home"], h2h["draw"], h2h["away"]])
        p_over, _ = multiplicative_devig([totals[over_key], totals[under_key]])
        lt = solve_lambda_t_from_line(p_over, float(suffix))
        lh = solve_lambda_home_from_h2h(lt, p_home)
        la = lt - lh
        ok, _ = cross_validate_line(lh, la, p_over, p_home, float(suffix))
        return max(0.05, lh), max(0.05, la), 0.0 if ok else 0.07
    except (ValueError, ZeroDivisionError) as exc:
        logger.warning("xg_library: solveur echoue: %s", exc)
        return None


async def _archive(session: AsyncSession, fixture_id: int, phase: str, snapshot_utc) -> bool:
    """Ecrit une ligne. Idempotent : un doublon (fixture, phase) est ignore."""
    from app.models.team_xg import TeamXgEstimate

    markets, ids = await _snapshot_group(session, fixture_id, snapshot_utc)
    solved = _solve(markets)
    if solved is None:
        return False
    lh, la, residual = solved

    stmt = (
        pg_insert(TeamXgEstimate)
        .values(
            fixture_id=fixture_id,
            phase=phase,
            as_of_utc=snapshot_utc,
            lambda_home=round(lh, 4),
            lambda_away=round(la, 4),
            fit_residual=residual,
            flagged=residual > 0.06,
            data_source=XG_BOOKMAKER,
            fallback_used=False,
            input_snapshot_ids=ids,
            odds=markets,
        )
        .on_conflict_do_nothing(constraint="uq_team_xg_estimates_fixture_phase")
    )
    res = await session.execute(stmt)
    return bool(res.rowcount)


async def capture_opening(session: AsyncSession) -> int:
    """Premiere estimation publiee, pour toute fixture qui n'en a pas encore."""
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    done = set((await session.execute(
        select(TeamXgEstimate.fixture_id).where(TeamXgEstimate.phase == "opening")
    )).scalars().all())

    rows = (await session.execute(
        select(
            MatchOddsSnapshot.fixture_id,
            func.min(MatchOddsSnapshot.snapshot_utc).label("first_utc"),
        )
        .where(MatchOddsSnapshot.bookmaker == XG_BOOKMAKER)
        .group_by(MatchOddsSnapshot.fixture_id)
    )).all()

    written = 0
    for fixture_id, first_utc in rows:
        if fixture_id in done:
            continue
        if await _archive(session, fixture_id, "opening", first_utc):
            written += 1
    await session.commit()
    logger.info("xg_library: %d ouverture(s) archivee(s)", written)
    return written


async def capture_closing(session: AsyncSession) -> int:
    """Derniere estimation avant le coup d'envoi, pour les matchs commences."""
    from app.models.fixtures import Fixture
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    now = datetime.now(UTC)
    done = set((await session.execute(
        select(TeamXgEstimate.fixture_id).where(TeamXgEstimate.phase == "closing")
    )).scalars().all())

    fixtures = (await session.execute(
        select(Fixture.id, Fixture.kickoff_utc).where(
            Fixture.kickoff_utc.isnot(None),
            Fixture.kickoff_utc < now,
            Fixture.ps3838_event_id.isnot(None),
        )
    )).all()

    written = 0
    for fixture_id, kickoff in fixtures:
        if fixture_id in done:
            continue
        last_utc = (await session.execute(
            select(func.max(MatchOddsSnapshot.snapshot_utc)).where(
                MatchOddsSnapshot.fixture_id == fixture_id,
                MatchOddsSnapshot.bookmaker == XG_BOOKMAKER,
                MatchOddsSnapshot.snapshot_utc < kickoff,
            )
        )).scalar_one_or_none()
        if last_utc is None:
            continue
        if await _archive(session, fixture_id, "closing", last_utc):
            written += 1
    await session.commit()
    logger.info("xg_library: %d closing(s) archive(s)", written)
    return written
