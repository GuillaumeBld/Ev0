"""auto_settle.py — automatic settlement of approved recommendations via MatchEvents.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. If MatchEvents exist for the fixture:
     - Check PMM if available: player absent or 0 minutes → VOID
     - Otherwise: player has matching goal/assist event → WON, else → LOST
  3. If no MatchEvents but PMM exists:
     - Player absent or 0 minutes → VOID
     - Player played but no events → stuck (events not synced yet)
  4. If neither MatchEvents nor PMM → skip (leave running)
  5. Update recommendation: result, pnl, settled_utc
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


def _names_match(a: str, b: str) -> bool:
    """Check if two player names refer to the same person.

    Handles cases where one source drops a middle name:
      'Idrissa Gueye' == 'Idrissa Gana Gueye'
      'N'Diaye' == 'Ndiaye'
    """
    na, nb = _normalize_name(a), _normalize_name(b)
    if na == nb:
        return True
    # One normalized form contains the other (covers suffix/prefix middle names)
    if na in nb or nb in na:
        return True
    # First name + last name match even if middle name differs
    a_words = [w for w in a.lower().split() if w]
    b_words = [w for w in b.lower().split() if w]
    if len(a_words) >= 2 and len(b_words) >= 2:
        if (
            _normalize_name(a_words[0]) == _normalize_name(b_words[0])
            and _normalize_name(a_words[-1]) == _normalize_name(b_words[-1])
        ):
            return True
    return False


def _find_pmm_by_name(pmm_list: list, player_name: str):
    """Find a PlayerMatchMinutes object by normalized player name."""
    for pmm in pmm_list:
        if _names_match(pmm.player_name, player_name):
            return pmm
    return None


# market_type → list of valid MatchEvent.event_type values
_MARKET_TO_EVENTS: dict[str, list[str]] = {
    "goalscorer": ["goal", "penalty_goal"],
    "anytime_score": ["goal", "penalty_goal"],
    "assist": ["assist"],
    "anytime_assist": ["assist"],
}

# bet_type → event types (for standard/supersub recs that use bet_type, not market_type)
_BET_TYPE_TO_EVENTS: dict[str, list[str]] = {
    "goal": ["goal", "penalty_goal"],
    "assist": ["assist"],
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
        market = str(rec.market_type)

        # ── H2H: settle against match score ────────────────────────────────────
        if market == "h2h":
            if fixture.home_score is None or fixture.away_score is None:
                stuck_fixture_ids.add(fixture.id)
                logger.debug(
                    "auto_settle: h2h rec %d — fixture %d has no score yet, skipping",
                    rec.id, fixture.id,
                )
                continue

            outcome = rec.player_name  # team name or "Nul"
            hs, as_ = fixture.home_score, fixture.away_score

            if _normalize_name(outcome) in ("nul", "draw", "x"):
                won = hs == as_
            elif _names_match(outcome, fixture.home_team or ""):
                won = hs > as_
            elif _names_match(outcome, fixture.away_team or ""):
                won = as_ > hs
            else:
                logger.warning(
                    "auto_settle: h2h rec %d — cannot match outcome '%s' to '%s' vs '%s'",
                    rec.id, outcome, fixture.home_team, fixture.away_team,
                )
                continue

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
                "auto_settle: h2h rec %d (%s) → %s (score %d-%d) pnl=%.2f",
                rec.id, outcome, result, hs, as_, pnl,
            )
            continue

        # ── Standard / Supersub: use bet_type to find event types ──────────────
        if market in ("standard", "supersub"):
            event_types = _BET_TYPE_TO_EVENTS.get(str(rec.bet_type), ["goal", "penalty_goal"])
        else:
            event_types = _MARKET_TO_EVENTS.get(market)

        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # --- Load PMM (optional, used for VOID detection) ---
        if fixture.id not in _pmm_cache:
            all_pmm = await db.execute(
                select(PlayerMatchMinutes)
                .where(PlayerMatchMinutes.fixture_id == fixture.id)
            )
            _pmm_cache[fixture.id] = list(all_pmm.scalars().all())

        pmm_rows = _pmm_cache[fixture.id]

        # --- Load MatchEvents ---
        if fixture.id not in _events_cache:
            any_event = await db.execute(
                select(MatchEvent).where(MatchEvent.fixture_id == fixture.id).limit(1)
            )
            _events_cache[fixture.id] = any_event.scalar_one_or_none() is not None

        has_events = _events_cache[fixture.id]

        if has_events:
            # MatchEvents available: check PMM for VOID first (if available)
            if pmm_rows:
                pmm = _find_pmm_by_name(pmm_rows, rec.player_name)
                if pmm is not None and pmm.minutes_played <= 0:
                    rec.result = "void"
                    rec.pnl = 0.0
                    rec.settled_utc = datetime.now(UTC)
                    settled += 1
                    void_count += 1
                    logger.info(
                        "auto_settle: rec %d (%s %s) → VOID (minutes=0, PMM available)",
                        rec.id, rec.player_name, rec.market_type,
                    )
                    continue

            # Settle WON/LOST from MatchEvents
            all_events_result = await db.execute(
                select(MatchEvent).where(
                    MatchEvent.fixture_id == fixture.id,
                    MatchEvent.event_type.in_(event_types),
                )
            )
            fixture_events = all_events_result.scalars().all()
            won = any(_names_match(ev.player_name, rec.player_name) for ev in fixture_events)

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

        elif pmm_rows:
            # No events but PMM exists: VOID if player didn't play, else stuck
            pmm = _find_pmm_by_name(pmm_rows, rec.player_name)
            if pmm is None or pmm.minutes_played <= 0:
                rec.result = "void"
                rec.pnl = 0.0
                rec.settled_utc = datetime.now(UTC)
                settled += 1
                void_count += 1
                logger.info(
                    "auto_settle: rec %d (%s %s) → VOID (minutes=%s, no events)",
                    rec.id, rec.player_name, rec.market_type,
                    pmm.minutes_played if pmm else "absent",
                )
            else:
                # Player played but no events yet — wait for event sync
                logger.debug(
                    "auto_settle: fixture %d has PMM but no events yet — skipping rec %d",
                    fixture.id, rec.id,
                )
                stuck_fixture_ids.add(fixture.id)

        else:
            # Neither events nor PMM — leave running
            logger.debug(
                "auto_settle: no data for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            stuck_fixture_ids.add(fixture.id)

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return {
        "settled": settled,
        "won": won_count,
        "lost": lost_count,
        "void": void_count,
        "stuck_fixture_ids": list(stuck_fixture_ids),
    }
