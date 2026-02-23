"""Outcome resolver for backtesting.

Determines whether a player scored or assisted in a given fixture
by querying the MatchEvent table.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import normalize_selection_name
from app.models.match_events import MatchEvent

logger = logging.getLogger(__name__)

# Market type → qualifying event types
_MARKET_EVENT_MAP: dict[str, set[str]] = {
    "goalscorer": {"goal", "penalty_goal"},
    "assist": {"assist"},
}


def resolve_outcome(
    player_name: str,
    market_type: str,
    events: list[dict[str, Any]],
) -> int:
    """Determine if a player's bet would have won.

    Args:
        player_name: Player name (from odds/recommendation)
        market_type: ``"goalscorer"`` or ``"assist"``
        events: List of match event dicts with ``player_name`` and ``event_type``

    Returns:
        ``1`` if the player scored/assisted (bet won), ``0`` otherwise.
    """
    qualifying_types = _MARKET_EVENT_MAP.get(market_type, set())
    if not qualifying_types:
        return 0

    norm_player = normalize_selection_name(player_name)

    for ev in events:
        ev_type = ev.get("event_type", "")
        if ev_type not in qualifying_types:
            continue

        ev_name = ev.get("player_name", "")
        if normalize_selection_name(ev_name) == norm_player:
            return 1

    return 0


async def load_outcomes_for_fixtures(
    db: AsyncSession,
    fixture_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Load all match events for the given fixture IDs.

    Args:
        db: Database session
        fixture_ids: List of fixture DB IDs

    Returns:
        Dict mapping ``fixture_id`` → list of event dicts
        (with ``player_name``, ``event_type``, ``minute``).
    """
    if not fixture_ids:
        return {}

    result = await db.execute(
        select(MatchEvent).where(MatchEvent.fixture_id.in_(fixture_ids))
    )

    events_by_fixture: dict[int, list[dict[str, Any]]] = {}
    for ev in result.scalars().all():
        events_by_fixture.setdefault(ev.fixture_id, []).append(
            {
                "player_name": ev.player_name,
                "event_type": ev.event_type,
                "minute": ev.minute,
            }
        )

    return events_by_fixture
