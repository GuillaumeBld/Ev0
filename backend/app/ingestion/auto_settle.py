"""auto_settle.py — automatic settlement of approved recommendations via MatchEvents.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. Check if MatchEvents exist for that fixture
  3. Determine result: WON (goal or assist event found), LOST (played but no event)
     VOID must be set manually (no lineup/minutes data available)
  4. Update recommendation: result, pnl, settled_utc
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
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

    Uses the MatchEvents table (goals/assists) to determine WON or LOST.
    Returns the number of recommendations settled.

    Note: VOID (player didn't play) cannot be determined from MatchEvents
    and must be set manually.
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

    # 2. For each rec, look up MatchEvents
    settled = 0
    for rec, fixture in rows:
        event_types = _MARKET_TO_EVENTS.get(rec.market_type)
        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # Check if any MatchEvents exist for this fixture at all
        any_event = await db.execute(
            select(MatchEvent).where(MatchEvent.fixture_id == fixture.id).limit(1)
        )
        if any_event.scalar_one_or_none() is None:
            logger.info(
                "auto_settle: no MatchEvents for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            continue

        # Check if player scored/assisted
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
