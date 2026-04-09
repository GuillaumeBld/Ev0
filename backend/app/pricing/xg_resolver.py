"""xG source resolver — chooses between Bzzoiro live xG and model-based xG.

Global mode toggle is stored in the `app_config` table under key="xg_source".
Default mode: BZZOIRO (use live xG from BzzEvent when available, fall back to model).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_XG_SOURCE_KEY = "xg_source"


class XgMode(StrEnum):
    BZZOIRO = "bzzoiro"
    MODEL = "model"


async def get_global_xg_mode(session: AsyncSession) -> XgMode:
    """Read the global xG mode from app_config.

    Returns XgMode.BZZOIRO if key is missing or value is "bzzoiro".
    Returns XgMode.MODEL if value is "model".
    Defaults to XgMode.BZZOIRO for any unknown value.
    """
    from app.models.app_config import AppConfig

    result = await session.execute(
        select(AppConfig).where(AppConfig.key == _XG_SOURCE_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return XgMode.BZZOIRO
    try:
        return XgMode(row.value)
    except ValueError:
        logger.warning("Unknown xg_source value %r in app_config, defaulting to BZZOIRO", row.value)
        return XgMode.BZZOIRO


async def set_global_xg_mode(session: AsyncSession, mode: XgMode) -> None:
    """Upsert the global xG mode into app_config."""
    from app.models.app_config import AppConfig

    result = await session.execute(
        select(AppConfig).where(AppConfig.key == _XG_SOURCE_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(AppConfig(key=_XG_SOURCE_KEY, value=mode.value))
    else:
        row.value = mode.value
    await session.commit()


async def resolve_xg_source(
    session: AsyncSession,
    event_api_id: int,
    global_mode: XgMode | None = None,
) -> tuple[float | None, float | None, str]:
    """Resolve the xG values for a given event.

    Returns (home_xg, away_xg, source_label) where source_label is
    "bzzoiro" or "model".

    Logic:
    1. If global_mode is None, fetch it from DB.
    2. If mode is BZZOIRO:
       - Query BzzEvent by api_id.
       - If both home_xg and away_xg are not None → return them with label "bzzoiro".
       - Otherwise fall through to model.
    3. If mode is MODEL (or BZZOIRO fallback):
       - Compute model xG from team stats.
       - If that fails, return (None, None, "model").
    """
    from app.models.bzzoiro import BzzEvent

    if global_mode is None:
        global_mode = await get_global_xg_mode(session)

    if global_mode == XgMode.BZZOIRO:
        result = await session.execute(
            select(BzzEvent).where(BzzEvent.api_id == event_api_id)
        )
        bzz_event = result.scalar_one_or_none()
        if bzz_event is not None and bzz_event.home_xg is not None and bzz_event.away_xg is not None:
            return (bzz_event.home_xg, bzz_event.away_xg, "bzzoiro")
        # Silent fallback to model
        logger.debug(
            "BzzEvent api_id=%d has no xG data, falling back to model", event_api_id
        )

    # MODEL mode (or BZZOIRO fallback)
    try:
        home_xg, away_xg = await _compute_model_xg(session, event_api_id)
        return (home_xg, away_xg, "model")
    except Exception:
        logger.exception("Model xG computation failed for event_api_id=%d", event_api_id)
        return (None, None, "model")


async def _compute_model_xg(
    session: AsyncSession,
    event_api_id: int,
) -> tuple[float | None, float | None]:
    """Compute model-based xG using team stats from team_xg.compute_team_stats().

    Looks up the BzzEvent to find home/away team api_ids, then maps them to
    team names via BzzTeam, and computes Dixon-Coles style match xG.
    """
    from app.models.bzzoiro import BzzEvent, BzzTeam
    from app.pricing.team_xg import compute_team_stats, estimate_team_match_xg

    result = await session.execute(
        select(BzzEvent).where(BzzEvent.api_id == event_api_id)
    )
    bzz_event = result.scalar_one_or_none()
    if bzz_event is None:
        return (None, None)

    # Resolve home/away team names from BzzTeam
    home_team_name: str | None = None
    away_team_name: str | None = None

    if bzz_event.home_team_api_id is not None:
        home_team_res = await session.execute(
            select(BzzTeam).where(BzzTeam.api_id == bzz_event.home_team_api_id)
        )
        home_team_obj = home_team_res.scalar_one_or_none()
        if home_team_obj:
            home_team_name = home_team_obj.name

    if bzz_event.away_team_api_id is not None:
        away_team_res = await session.execute(
            select(BzzTeam).where(BzzTeam.api_id == bzz_event.away_team_api_id)
        )
        away_team_obj = away_team_res.scalar_one_or_none()
        if away_team_obj:
            away_team_name = away_team_obj.name

    if home_team_name is None or away_team_name is None:
        logger.warning(
            "Could not resolve team names for BzzEvent api_id=%d", event_api_id
        )
        return (None, None)

    team_stats = await compute_team_stats(session)
    if not team_stats:
        return (None, None)

    home_stats = team_stats.get(home_team_name)
    away_stats = team_stats.get(away_team_name)

    if home_stats is None or away_stats is None:
        logger.debug(
            "Team stats missing for %r or %r", home_team_name, away_team_name
        )
        return (None, None)

    league_avg_xg = sum(s.attack_xg_per_match for s in team_stats.values()) / len(team_stats)
    league_avg_xga = sum(s.defense_xga_per_match for s in team_stats.values()) / len(team_stats)

    home_xg = estimate_team_match_xg(
        attack_xg=home_stats.attack_xg_per_match,
        opponent_xga=away_stats.defense_xga_per_match,
        league_avg_xg=league_avg_xg,
        league_avg_xga=league_avg_xga,
        is_home=True,
    )
    away_xg = estimate_team_match_xg(
        attack_xg=away_stats.attack_xg_per_match,
        opponent_xga=home_stats.defense_xga_per_match,
        league_avg_xg=league_avg_xg,
        league_avg_xga=league_avg_xga,
        is_home=False,
    )
    return (home_xg, away_xg)
