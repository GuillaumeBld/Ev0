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


def _normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching.

    Removes hyphens, apostrophes, spaces, lowercases.
    Examples: 'Al-Tamari' == 'Al Tamari' == 'Altamari'
              "N'Diaye" == 'Ndiaye' == 'N Diaye'
    """
    normalized = name.lower().strip()
    normalized = normalized.replace("-", "").replace("'", "").replace("\u2019", "").replace(" ", "")
    return normalized


def _find_pmm_by_name(pmm_list: list, player_name: str):
    """Find a PlayerMatchMinutes object by normalized player name.

    Returns the first match or None.
    """
    norm = _normalize_name(player_name)
    for pmm in pmm_list:
        if _normalize_name(pmm.player_name) == norm:
            return pmm
    return None


# market_type → list of valid MatchEvent.event_type values
_MARKET_TO_EVENTS: dict[str, list[str]] = {
    "goalscorer": ["goal", "penalty_goal"],
    "anytime_score": ["goal", "penalty_goal"],
    "assist": ["assist"],
    "anytime_assist": ["assist"],
}


async def settle_approved_recommendations(db: AsyncSession) -> dict:
    """Settle all unsettled approved recommendations for finished fixtures.

    Uses PlayerMatchMinutes for VOID detection when available.
    Uses MatchEvents (goals/assists) for WON/LOST.
    Returns a dict with keys: settled, won, lost, void, stuck_fixture_ids.
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
        return {"settled": 0, "won": 0, "lost": 0, "void": 0, "stuck_fixture_ids": []}

    logger.info("auto_settle: %d recs to settle", len(rows))

    # Cache all PMM rows per fixture_id (None = not loaded yet, [] = loaded but empty)
    _pmm_cache: dict[int, list] = {}
    _events_cache: dict[int, bool] = {}

    settled = 0
    won_count = 0
    lost_count = 0
    void_count = 0
    stuck_fixture_ids: set[int] = set()
    for rec, fixture in rows:
        event_types = _MARKET_TO_EVENTS.get(rec.market_type)
        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # --- VOID detection via PlayerMatchMinutes ---
        # Load all PMM rows for fixture once, then match by normalized name
        if fixture.id not in _pmm_cache:
            all_pmm = await db.execute(
                select(PlayerMatchMinutes)
                .where(PlayerMatchMinutes.fixture_id == fixture.id)
            )
            _pmm_cache[fixture.id] = list(all_pmm.scalars().all())

        pmm_rows = _pmm_cache[fixture.id]

        if not pmm_rows:
            # No minutes data yet — can't distinguish LOST from VOID → leave running
            logger.debug(
                "auto_settle: no PlayerMatchMinutes for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            stuck_fixture_ids.add(fixture.id)
            continue

        pmm = _find_pmm_by_name(pmm_rows, rec.player_name)

        if pmm is None or pmm.minutes_played <= 0:
            # Player didn't play (not in squad or 0 minutes) → VOID
            rec.result = "void"
            rec.pnl = 0.0
            rec.settled_utc = datetime.now(UTC)
            settled += 1
            void_count += 1
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
            stuck_fixture_ids.add(fixture.id)
            continue

        all_events_result = await db.execute(
            select(MatchEvent).where(
                MatchEvent.fixture_id == fixture.id,
                MatchEvent.event_type.in_(event_types),
            )
        )
        fixture_events = all_events_result.scalars().all()
        norm_rec_name = _normalize_name(rec.player_name)
        won = any(_normalize_name(ev.player_name) == norm_rec_name for ev in fixture_events)

        result = "won" if won else "lost"
        pnl = round(10.0 * (rec.best_odds - 1), 2) if won else -10.0

        rec.result = result
        rec.pnl = pnl
        rec.settled_utc = datetime.now(UTC)
        settled += 1
        if won:
            won_count += 1
        else:
            lost_count += 1

        logger.info(
            "auto_settle: rec %d (%s %s) → %s pnl=%.2f",
            rec.id, rec.player_name, rec.market_type, result, pnl,
        )

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return {
        "settled": settled,
        "won": won_count,
        "lost": lost_count,
        "void": void_count,
        "stuck_fixture_ids": list(stuck_fixture_ids),
    }
