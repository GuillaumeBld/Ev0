"""Database storage for ingested data.

Handles storing fixtures, player stats, and odds snapshots.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

import unicodedata

from app.models import Fixture, MatchEvent, OddsSnapshot, Player, PlayerStats, Recommendation
from app.models.canonical_teams import CanonicalTeam

# Legacy short-name map — kept for backward compat, superseded by canonical_teams
TEAM_NAME_MAP: dict[str, str] = {
    "Bournemouth": "AFC Bournemouth",
    "Brighton": "Brighton & Hove Albion",
    "Brighton and Hove Albion": "Brighton & Hove Albion",
    "Tottenham": "Tottenham Hotspur",
    "West Ham": "West Ham United",
    "Nott'm Forest": "Nottingham Forest",
    "Newcastle": "Newcastle United",
    "Paris Saint Germain": "Paris Saint-Germain",
    "PSG": "Paris Saint-Germain",
}

# In-process cache: alias_norm → canonical_team_id
_alias_cache: dict[str, int] = {}
_cache_loaded = False


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


async def _ensure_alias_cache(session: AsyncSession) -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    teams = (await session.execute(select(CanonicalTeam))).scalars().all()
    for ct in teams:
        _alias_cache[_norm(ct.name_fr)] = ct.id
        for alias in (ct.aliases or []):
            _alias_cache[_norm(alias)] = ct.id
    _cache_loaded = True


async def resolve_canonical_team(session: AsyncSession, raw_name: str | None) -> int | None:
    """Return canonical_team_id for a raw team name, or None if unknown."""
    if not raw_name:
        return None
    await _ensure_alias_cache(session)
    return _alias_cache.get(_norm(raw_name))


def normalize_team_name(team: str | None) -> str | None:
    """Return the canonical team name via legacy map (sync fallback)."""
    if not team:
        return team
    return TEAM_NAME_MAP.get(team, team)


async def upsert_fixture(session: AsyncSession, data: dict[str, Any]) -> Fixture:
    """
    Insert or update a fixture.

    Uses fixture_id (external_id) as the unique key.
    """
    home_ct_id = await resolve_canonical_team(session, data.get("home_team"))
    away_ct_id = await resolve_canonical_team(session, data.get("away_team"))

    stmt = insert(Fixture).values(
        external_id=data["fixture_id"],
        league=data["league"],
        season=data["season"],
        matchweek=data.get("matchweek"),
        home_team=data["home_team"],
        away_team=data["away_team"],
        home_canonical_team_id=home_ct_id,
        away_canonical_team_id=away_ct_id,
        kickoff_utc=datetime.fromisoformat(f"{data['date']}T{data.get('time', '00:00')}:00+00:00"),
        home_score=data.get("home_score"),
        away_score=data.get("away_score"),
        status="finished" if data.get("home_score") is not None else "scheduled",
    )

    # On conflict, update scores, status, and matchweek
    stmt = stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_={
            "home_score": stmt.excluded.home_score,
            "away_score": stmt.excluded.away_score,
            "status": stmt.excluded.status,
            "matchweek": stmt.excluded.matchweek,
            "updated_at": datetime.now(UTC),
        },
    )

    await session.execute(stmt)
    await session.commit()

    # Fetch and return
    result = await session.execute(select(Fixture).where(Fixture.external_id == data["fixture_id"]))
    return result.scalar_one()


async def upsert_player(session: AsyncSession, data: dict[str, Any]) -> Player:
    """Insert or update a player."""
    raw_team = data.get("team")
    team_name = normalize_team_name(raw_team)
    ct_id = await resolve_canonical_team(session, raw_team)

    stmt = insert(Player).values(
        external_id=data.get("player_id", data["player_name"]),
        name=data["player_name"],
        normalized_name=data.get("normalized_name", data["player_name"].lower()),
        team=team_name,
        position=data.get("position"),
        canonical_team_id=ct_id,
    )

    stmt = stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_={
            "name": stmt.excluded.name,
            "team": stmt.excluded.team,
            "position": stmt.excluded.position,
            "canonical_team_id": stmt.excluded.canonical_team_id,
            "updated_at": datetime.now(UTC),
        },
    )

    await session.execute(stmt)
    await session.commit()

    result = await session.execute(
        select(Player).where(Player.external_id == data.get("player_id", data["player_name"]))
    )
    return result.scalar_one()


async def store_player_stats(
    session: AsyncSession,
    player_id: int,
    league: str,
    season: str,
    stats: dict[str, Any],
) -> PlayerStats | None:
    """Store a player stats snapshot.

    Returns None and skips insertion if the row fails sanity checks
    (e.g. season-total minutes stored with matches_played=1).
    """
    now = datetime.now(UTC)

    matches = stats.get("matches_played", stats.get("matches", 0)) or 0
    minutes = stats.get("minutes_played", stats.get("minutes", 0)) or 0
    if matches > 0 and minutes / matches > 120:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Skipping corrupt player_stats for player_id=%s league=%s: "
            "%d min / %d matches = %.0f min/game (max 120)",
            player_id, league, minutes, matches, minutes / matches,
        )
        return None

    # Skip if a row already exists for this player/league on today's UTC date.
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = await session.execute(
        select(PlayerStats.id).where(
            PlayerStats.player_id == player_id,
            PlayerStats.league == league,
            PlayerStats.as_of_utc >= today_start,
        )
    )
    if existing.first() is not None:
        return None

    player_stats = PlayerStats(
        player_id=player_id,
        as_of_utc=now,
        league=league,
        season=season,
        matches_played=stats.get("matches_played", stats.get("matches", 0)),
        minutes_played=stats.get("minutes_played", stats.get("minutes", 0)),
        goals=stats.get("goals", 0),
        npxg=stats.get("npxg", 0.0),
        xg=stats.get("xg", 0.0),
        shots=stats.get("shots", 0),
        shots_on_target=stats.get("shots_on_target", 0),
        assists=stats.get("assists", 0),
        xa=stats.get("xa", 0.0),
        key_passes=stats.get("key_passes", 0),
        # Model C — Understat
        xgchain=stats.get("xgchain", 0.0),
        xgbuildup=stats.get("xgbuildup", 0.0),
        # Model C — Sofascore
        touches_attack_pen_area=stats.get("touches_attack_pen_area", 0),
        big_chances_created=stats.get("big_chances_created", 0),
        accurate_crosses=stats.get("accurate_crosses", 0),
        total_crosses=stats.get("total_crosses", 0),
        through_balls=stats.get("through_balls", 0),
        sofascore_rating=stats.get("sofascore_rating"),
        # Per-90 rates
        xg_per_90=stats.get("xg_per_90"),
        npxg_per_90=stats.get("npxg_per_90"),
        xa_per_90=stats.get("xa_per_90"),
        xgchain_per_90=stats.get("xgchain_per_90"),
        shots_on_target_per_90=stats.get("shots_on_target_per_90"),
        touches_attack_pen_area_per_90=stats.get("touches_attack_pen_area_per_90"),
        bcc_per_90=stats.get("bcc_per_90"),
        accurate_crosses_per_90=stats.get("accurate_crosses_per_90"),
        through_balls_per_90=stats.get("through_balls_per_90"),
    )

    session.add(player_stats)
    await session.commit()
    await session.refresh(player_stats)

    return player_stats


async def store_odds_snapshot(
    session: AsyncSession,
    fixture_id: int,
    player_name: str,
    market_type: str,
    bookmaker: str,
    odds: float,
    raw_data: dict | None = None,
) -> OddsSnapshot:
    """Store an odds snapshot."""
    now = datetime.now(UTC)

    snapshot = OddsSnapshot(
        fixture_id=fixture_id,
        player_name=player_name,
        market_type=market_type,
        bookmaker=bookmaker,
        odds=odds,
        implied_probability=1.0 / odds if odds > 0 else 0.0,
        snapshot_utc=now,
        raw_data=raw_data,
    )

    session.add(snapshot)
    await session.flush()
    await session.refresh(snapshot)

    return snapshot


async def store_recommendation(
    session: AsyncSession,
    fixture_id: int,
    player_name: str,
    market_type: str,
    pricing_result: dict[str, Any],
    best_bookmaker: str,
    best_odds: float,
    edge: float,
) -> Recommendation:
    """Store a generated recommendation."""
    now = datetime.now(UTC)

    # Classify based on edge
    if edge >= 0.10:
        classification = "VALUE"
        confidence = min(0.95, 0.7 + edge)
    elif edge >= 0.05:
        classification = "VALUE"
        confidence = 0.6 + edge
    elif edge >= 0.0:
        classification = "NO_VALUE"
        confidence = 0.5
    else:
        classification = "AVOID"
        confidence = 0.3

    rec = Recommendation(
        fixture_id=fixture_id,
        player_name=player_name,
        market_type=market_type,
        lambda_intensity=pricing_result["lambda_intensity"],
        fair_probability=pricing_result["probability"],
        fair_odds=pricing_result["fair_odds"],
        best_bookmaker=best_bookmaker,
        best_odds=best_odds,
        edge=edge,
        classification=classification,
        confidence=confidence,
        explanation=pricing_result["explanation"],
        generated_utc=now,
    )

    session.add(rec)
    await session.commit()
    await session.refresh(rec)

    return rec


async def store_match_events(
    session: AsyncSession,
    fixture_id: int,
    events: list[dict[str, Any]],
) -> int:
    """Store match events for a fixture, skipping duplicates.

    Args:
        session: DB session
        fixture_id: DB fixture ID
        events: List of event dicts with player_name, event_type, minute

    Returns:
        Number of events stored
    """
    stored = 0
    for ev in events:
        player_name = ev.get("player_name", "")
        event_type = ev.get("event_type", "")
        minute = ev.get("minute")

        if not player_name or not event_type:
            continue

        # Check for existing event (avoid unique constraint violation)
        existing = await session.execute(
            select(MatchEvent).where(
                MatchEvent.fixture_id == fixture_id,
                MatchEvent.player_name == player_name,
                MatchEvent.event_type == event_type,
                MatchEvent.minute == minute,
            )
        )
        if existing.scalar_one_or_none():
            continue

        match_event = MatchEvent(
            fixture_id=fixture_id,
            player_name=player_name,
            event_type=event_type,
            minute=minute,
        )
        session.add(match_event)
        stored += 1

    if stored > 0:
        await session.commit()

    return stored


async def get_upcoming_fixtures(
    session: AsyncSession,
    hours_ahead: int = 48,
) -> list[Fixture]:
    """Get fixtures in the next N hours."""
    now = datetime.now(UTC)
    cutoff = now + timedelta(hours=hours_ahead)

    result = await session.execute(
        select(Fixture)
        .where(Fixture.kickoff_utc >= now)
        .where(Fixture.kickoff_utc <= cutoff)
        .where(Fixture.status == "scheduled")
        .order_by(Fixture.kickoff_utc)
    )

    return list(result.scalars().all())


async def get_latest_player_stats(
    session: AsyncSession,
    player_id: int,
    league: str,
) -> PlayerStats | None:
    """Get most recent stats snapshot for a player."""
    result = await session.execute(
        select(PlayerStats)
        .where(PlayerStats.player_id == player_id)
        .where(PlayerStats.league == league)
        .order_by(PlayerStats.as_of_utc.desc())
        .limit(1)
    )

    return result.scalar_one_or_none()


async def get_best_odds_for_fixture(
    session: AsyncSession,
    fixture_id: int,
    market_type: str,
) -> dict[str, OddsSnapshot]:
    """Get best odds per player for a fixture."""
    from sqlalchemy import func

    # Subquery to get max odds per player
    subq = (
        select(
            OddsSnapshot.player_name,
            func.max(OddsSnapshot.odds).label("max_odds"),
        )
        .where(OddsSnapshot.fixture_id == fixture_id)
        .where(OddsSnapshot.market_type == market_type)
        .group_by(OddsSnapshot.player_name)
        .subquery()
    )

    # Join to get full snapshot with best odds
    result = await session.execute(
        select(OddsSnapshot)
        .join(
            subq,
            (OddsSnapshot.player_name == subq.c.player_name)
            & (OddsSnapshot.odds == subq.c.max_odds),
        )
        .where(OddsSnapshot.fixture_id == fixture_id)
        .where(OddsSnapshot.market_type == market_type)
    )

    return {snap.player_name: snap for snap in result.scalars().all()}
