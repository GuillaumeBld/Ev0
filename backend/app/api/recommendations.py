"""Recommendations API endpoints."""

import logging
from datetime import UTC, date, datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bankroll import BankrollEntry
from app.models.recommendations import Recommendation as RecommendationModel
from app.rate_limit import limiter
from app.services.recommendation_service import get_recommendations_for_date
from app.strategy.selector import RecommendationFilter

logger = logging.getLogger(__name__)

router = APIRouter()


class MarketType(StrEnum):
    """Market type enum."""

    GOALSCORER = "goalscorer"
    ASSIST = "assist"


class Classification(StrEnum):
    """Recommendation classification."""

    VALUE = "VALUE"
    NO_VALUE = "NO_VALUE"
    AVOID = "AVOID"


class Recommendation(BaseModel):
    """A betting recommendation."""

    id: str
    fixture_id: str
    fixture_name: str
    kickoff_utc: str
    player_name: str
    team: str
    market_type: MarketType
    fair_odds: float
    best_bookmaker: str
    best_odds: float
    edge: float
    classification: Classification
    confidence: float
    explanation: dict


class RecommendationsResponse(BaseModel):
    """Response with list of recommendations."""

    date: str
    count: int
    recommendations: list[Recommendation]
    error: str | None = None


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None, description="Date for recommendations (default: today)"),
    market_type: MarketType | None = Query(None, description="Filter by market type"),
    league: str | None = Query(None, description="Filter by league (ligue_1, premier_league)"),
    min_edge: float = Query(0.05, description="Minimum edge threshold"),
) -> RecommendationsResponse:
    """Get betting recommendations for a given date."""
    effective_date = target_date or date.today()
    dt = datetime.combine(effective_date, datetime.min.time(), tzinfo=UTC)

    error_msg = None
    try:
        filter_config = RecommendationFilter(min_edge=min_edge)
        if market_type:
            filter_config.markets = [market_type.value]
        if league:
            filter_config.leagues = [league]
        raw_recs, _ = await get_recommendations_for_date(dt, db, filter_config)
    except Exception as exc:
        logger.error("Failed to generate recommendations: %s", exc, exc_info=True)
        error_msg = "Failed to generate recommendations. Please try again later."
        raw_recs = []

    # Transform to response models
    recommendations = []
    for rec in raw_recs:
        recommendations.append(
            Recommendation(
                id=rec.get("id", ""),
                fixture_id=rec.get("fixture_id", ""),
                fixture_name=rec.get("fixture_name", ""),
                kickoff_utc=str(rec.get("kickoff_utc", "")),
                player_name=rec.get("player_name", ""),
                team=rec.get("team", ""),
                market_type=rec.get("market_type", "goalscorer"),
                fair_odds=rec.get("fair_odds", 0.0),
                best_bookmaker=rec.get("best_bookmaker", ""),
                best_odds=rec.get("market_odds", 0.0),
                edge=rec.get("edge", 0.0),
                classification=rec.get("classification", "NO_VALUE"),
                confidence=rec.get("confidence", 0.5),
                explanation=rec.get("explanation", {}),
            )
        )

    return RecommendationsResponse(
        date=str(effective_date),
        count=len(recommendations),
        recommendations=recommendations,
        error=error_msg,
    )


@router.get("/recommendations/{recommendation_id}")
async def get_recommendation_detail(
    recommendation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific recommendation."""
    result = await db.execute(
        select(RecommendationModel).where(RecommendationModel.id == recommendation_id)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    return {
        "id": rec.id,
        "fixture_id": rec.fixture_id,
        "player_name": rec.player_name,
        "market_type": rec.market_type,
        "fair_odds": rec.fair_odds,
        "best_bookmaker": rec.best_bookmaker,
        "best_odds": rec.best_odds,
        "edge": rec.edge,
        "classification": rec.classification,
        "confidence": rec.confidence,
        "explanation": rec.explanation,
        "status": rec.status,
        "result": rec.result,
        "pnl": rec.pnl,
        "generated_utc": str(rec.generated_utc) if rec.generated_utc else None,
        "decided_utc": str(rec.decided_utc) if rec.decided_utc else None,
        "settled_utc": str(rec.settled_utc) if rec.settled_utc else None,
    }


# ── PATCH: Approve / Reject / Record Result ──────────────────────


class RecommendationUpdate(BaseModel):
    """Update a recommendation's status or result."""

    status: str | None = None  # approved, rejected, executed
    result: str | None = None  # won, lost, void, push
    stake: float | None = None  # stake amount (for bankroll tracking)
    operator_notes: str | None = None


class RecommendationUpdateResponse(BaseModel):
    id: int
    status: str
    result: str | None
    pnl: float | None
    decided_utc: str | None
    settled_utc: str | None


@router.patch("/recommendations/{recommendation_id}", response_model=RecommendationUpdateResponse)
@limiter.limit("30/minute")
async def update_recommendation(
    request: Request,
    recommendation_id: int,
    body: RecommendationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a recommendation: approve/reject or record result.

    - Set status to 'approved' or 'rejected' to decide on a bet.
    - Set result to 'won' or 'lost' to settle and compute P&L.
    - Optionally provide stake for bankroll tracking.
    """
    result = await db.execute(
        select(RecommendationModel).where(RecommendationModel.id == recommendation_id)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    now = datetime.now(UTC)

    # Update status (approve/reject)
    if body.status:
        valid_statuses = {"pending", "approved", "rejected", "executed"}
        if body.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
        rec.status = body.status
        rec.decided_utc = now

        # When approving with a stake, record in bankroll
        if body.status in ("approved", "executed") and body.stake and body.stake > 0:
            await _record_bet_placed(db, rec, body.stake, now)

    # Update operator notes
    if body.operator_notes is not None:
        rec.operator_notes = body.operator_notes

    # Record result (settle the bet)
    if body.result:
        valid_results = {"won", "lost", "void", "push"}
        if body.result not in valid_results:
            raise HTTPException(status_code=400, detail=f"Invalid result: {body.result}")

        rec.result = body.result
        rec.settled_utc = now

        # Compute P&L
        stake = body.stake or 10.0  # Default to 10€ if no stake provided
        if body.result == "won":
            rec.pnl = round(stake * (rec.best_odds - 1), 2)
        elif body.result == "lost":
            rec.pnl = round(-stake, 2)
        else:  # void, push
            rec.pnl = 0.0

        # Record settlement in bankroll
        await _record_bet_settled(db, rec, stake, now)

    await db.commit()
    await db.refresh(rec)

    return RecommendationUpdateResponse(
        id=rec.id,
        status=rec.status,
        result=rec.result,
        pnl=rec.pnl,
        decided_utc=str(rec.decided_utc) if rec.decided_utc else None,
        settled_utc=str(rec.settled_utc) if rec.settled_utc else None,
    )


async def _get_current_balance(db: AsyncSession) -> float:
    """Get the current bankroll balance."""
    result = await db.execute(
        select(BankrollEntry).order_by(BankrollEntry.transacted_utc.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()
    return latest.balance_after if latest else 0.0


async def _record_bet_placed(
    db: AsyncSession,
    rec: RecommendationModel,
    stake: float,
    now: datetime,
) -> None:
    """Record a bet placement in the bankroll."""
    balance = await _get_current_balance(db)
    entry = BankrollEntry(
        entry_type="bet_placed",
        amount=-stake,
        balance_after=round(balance - stake, 2),
        recommendation_id=rec.id,
        stake=stake,
        notes=f"{rec.player_name} {rec.market_type} @{rec.best_odds}",
        transacted_utc=now,
    )
    db.add(entry)
    await db.flush()


async def _record_bet_settled(
    db: AsyncSession,
    rec: RecommendationModel,
    stake: float,
    now: datetime,
) -> None:
    """Record a bet settlement in the bankroll."""
    balance = await _get_current_balance(db)

    if rec.result == "won":
        amount = round(stake * rec.best_odds, 2)  # Return stake + profit
    elif rec.result == "lost":
        amount = 0.0  # Already deducted at placement
    else:  # void, push
        amount = stake  # Return stake

    entry = BankrollEntry(
        entry_type="bet_settled",
        amount=amount,
        balance_after=round(balance + amount, 2),
        recommendation_id=rec.id,
        stake=stake,
        notes=f"{rec.result}: {rec.player_name} pnl={rec.pnl}",
        transacted_utc=now,
    )
    db.add(entry)
    await db.flush()
