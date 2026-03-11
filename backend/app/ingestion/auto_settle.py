"""auto_settle.py — automatic settlement of approved recommendations via MatchEvents.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. If no PlayerMatchMinutes data for the fixture → skip (leave running)
     We need minutes data to distinguish LOST from VOID.
  3. If PlayerMatchMinutes data is available:
     - Player absent or 0 minutes → VOID
     - Player played (>0 min) + goal/assist in MatchEvents → WON
     - Player played (>0 min) + no event → LOST
  4. Update recommendation: result, pnl, settled_utc
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

# market_type → list of valid MatchEvent.event_type values
_MARKET_TO_EVENTS: dict[str, list[str]] = {
    "goalscorer": ["goal", "penalty_goal"],
    "anytime_score": ["goal", "penalty_goal"],
    "assist": ["assist"],
    "anytime_assist": ["assist"],
}


async def settle_approved_recommendations(db: AsyncSession) -> int:
    """Settle all unsettled approved recommendations for finished fixtures.

    Uses PlayerMatchMinutes for VOID detection when available.
    Uses MatchEvents (goals/assists) for WON/LOST.
    Returns the number of recommendations settled.
    """
    # 1. Find all approved recs with result=None + finished fixture
    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(
            Recommendation.status == "approved",
            Recommendation.result.is_(None),
            Fixture.status == "finished",
        )
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        logger.info("auto_settle: no unsettled approved recs with finished fixtures")
        return 0

    logger.info("auto_settle: %d recs to settle", len(rows))

    # Cache has_minutes_data per fixture_id to avoid redundant probes
    _pmm_cache: dict[int, bool] = {}
    _events_cache: dict[int, bool] = {}

    settled = 0
    for rec, fixture in rows:
        event_types = _MARKET_TO_EVENTS.get(rec.market_type)
        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # --- VOID detection via PlayerMatchMinutes ---
        # Check if we have minutes data for this fixture
        if fixture.id not in _pmm_cache:
            any_pmm = await db.execute(
                select(PlayerMatchMinutes)
                .where(PlayerMatchMinutes.fixture_id == fixture.id)
                .limit(1)
            )
            _pmm_cache[fixture.id] = any_pmm.scalar_one_or_none() is not None
        has_minutes_data = _pmm_cache[fixture.id]

        if not has_minutes_data:
            # No minutes data yet — can't distinguish LOST from VOID → leave running
            logger.debug(
                "auto_settle: no PlayerMatchMinutes for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            continue

        # Look up this specific player's minutes
        pmm_row = await db.execute(
            select(PlayerMatchMinutes).where(
                PlayerMatchMinutes.fixture_id == fixture.id,
                PlayerMatchMinutes.player_name == rec.player_name,
            )
        )
        pmm = pmm_row.scalar_one_or_none()

        if pmm is None or pmm.minutes_played <= 0:
                # Player didn't play (not in squad or 0 minutes) → VOID
                rec.result = "void"
                rec.pnl = 0.0
                rec.settled_utc = datetime.now(UTC)
                settled += 1
                logger.info(
                    "auto_settle: rec %d (%s %s) → VOID (minutes=%s)",
                    rec.id, rec.player_name, rec.market_type,
                    pmm.minutes_played if pmm else "absent",
                )
                continue
        # Player played — fall through to MatchEvents check

        # --- WON/LOST via MatchEvents ---
        if fixture.id not in _events_cache:
            any_event = await db.execute(
                select(MatchEvent).where(MatchEvent.fixture_id == fixture.id).limit(1)
            )
            _events_cache[fixture.id] = any_event.scalar_one_or_none() is not None
        if not _events_cache[fixture.id]:
            logger.info(
                "auto_settle: no MatchEvents for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            continue

        player_event = await db.execute(
            select(MatchEvent).where(
                MatchEvent.fixture_id == fixture.id,
                MatchEvent.player_name == rec.player_name,
                MatchEvent.event_type.in_(event_types),
            ).limit(1)
        )
        won = player_event.scalar_one_or_none() is not None

        result = "won" if won else "lost"
        pnl = round(10.0 * (rec.best_odds - 1), 2) if won else -10.0

        rec.result = result
        rec.pnl = pnl
        rec.settled_utc = datetime.now(UTC)
        settled += 1

        logger.info(
            "auto_settle: rec %d (%s %s) → %s pnl=%.2f",
            rec.id, rec.player_name, rec.market_type, result, pnl,
        )

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return settled
