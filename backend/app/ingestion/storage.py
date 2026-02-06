"""Database storage for ingested data.

Handles storing fixtures, player stats, and odds snapshots.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Fixture, Player, PlayerStats, OddsSnapshot, Recommendation


async def upsert_fixture(session: AsyncSession, data: dict[str, Any]) -> Fixture:
    """
    Insert or update a fixture.
    
    Uses fixture_id (external_id) as the unique key.
    """
    stmt = insert(Fixture).values(
        external_id=data["fixture_id"],
        league=data["league"],
        season=data["season"],
        home_team=data["home_team"],
        away_team=data["away_team"],
        kickoff_utc=datetime.fromisoformat(f"{data['date']}T{data.get('time', '00:00')}:00+00:00"),
        home_score=data.get("home_score"),
        away_score=data.get("away_score"),
        status="finished" if data.get("home_score") is not None else "scheduled",
    )
    
    # On conflict, update scores and status
    stmt = stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_={
            "home_score": stmt.excluded.home_score,
            "away_score": stmt.excluded.away_score,
            "status": stmt.excluded.status,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    
    await session.execute(stmt)
    await session.commit()
    
    # Fetch and return
    result = await session.execute(
        select(Fixture).where(Fixture.external_id == data["fixture_id"])
    )
    return result.scalar_one()


async def upsert_player(session: AsyncSession, data: dict[str, Any]) -> Player:
    """Insert or update a player."""
    stmt = insert(Player).values(
        external_id=data.get("player_id", data["player_name"]),
        name=data["player_name"],
        normalized_name=data.get("normalized_name", data["player_name"].lower()),
        team=data.get("team"),
        position=data.get("position"),
    )
    
    stmt = stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_={
            "name": stmt.excluded.name,
            "team": stmt.excluded.team,
            "position": stmt.excluded.position,
            "updated_at": datetime.now(timezone.utc),
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
) -> PlayerStats:
    """Store a player stats snapshot."""
    now = datetime.now(timezone.utc)
    
    player_stats = PlayerStats(
        player_id=player_id,
        as_of_utc=now,
        league=league,
        season=season,
        matches_played=stats.get("matches", 0),
        minutes_played=stats.get("minutes", 0),
        goals=stats.get("goals", 0),
        npxg=stats.get("npxg", 0.0),
        xg=stats.get("xg", 0.0),
        shots=stats.get("shots", 0),
        shots_on_target=stats.get("shots_on_target", 0),
        assists=stats.get("assists", 0),
        xa=stats.get("xa", 0.0),
        key_passes=stats.get("key_passes", 0),
        sca=stats.get("sca", 0),
        passes_into_penalty_area=stats.get("passes_into_penalty_area", 0),
        progressive_passes=stats.get("progressive_passes", 0),
        crosses=stats.get("crosses", 0),
        xg_per_90=stats.get("xg_per_90"),
        xa_per_90=stats.get("xa_per_90"),
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
    now = datetime.now(timezone.utc)
    
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
    await session.commit()
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
    now = datetime.now(timezone.utc)
    
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


async def get_upcoming_fixtures(
    session: AsyncSession,
    hours_ahead: int = 48,
) -> list[Fixture]:
    """Get fixtures in the next N hours."""
    now = datetime.now(timezone.utc)
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
