"""Storage helpers for match events, odds snapshots, and recommendations."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MatchEvent, OddsSnapshot, Recommendation


async def store_odds_snapshot(
    session: AsyncSession,
    fixture_id: int,
    player_name: str,
    market_type: str,
    bookmaker: str,
    odds: float,
    raw_data: dict | None = None,
) -> OddsSnapshot:
    snapshot = OddsSnapshot(
        fixture_id=fixture_id,
        player_name=player_name,
        market_type=market_type,
        bookmaker=bookmaker,
        odds=odds,
        implied_probability=1.0 / odds if odds > 0 else 0.0,
        snapshot_utc=datetime.now(UTC),
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
    xg_source: str | None = None,
    is_pen_taker: bool = False,
    confidence: float | None = None,
    classification: str | None = None,
) -> Recommendation:
    if classification is None:
        if edge >= 0.10:
            classification = "VALUE"
        elif edge >= 0.05:
            classification = "VALUE"
        elif edge >= 0.0:
            classification = "NO_VALUE"
        else:
            classification = "AVOID"

    if confidence is None:
        if edge >= 0.10:
            confidence = min(0.95, 0.7 + edge)
        elif edge >= 0.05:
            confidence = 0.6 + edge
        elif edge >= 0.0:
            confidence = 0.5
        else:
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
        generated_utc=datetime.now(UTC),
        xg_source=xg_source,
        is_pen_taker=is_pen_taker,
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
    stored = 0
    for ev in events:
        player_name = ev.get("player_name", "")
        event_type = ev.get("event_type", "")
        minute = ev.get("minute")
        if not player_name or not event_type:
            continue
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
        session.add(MatchEvent(
            fixture_id=fixture_id,
            player_name=player_name,
            event_type=event_type,
            minute=minute,
        ))
        stored += 1
    if stored > 0:
        await session.commit()
    return stored
