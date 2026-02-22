"""Recommendations API endpoints."""

import logging
from datetime import date, datetime, timezone
from enum import Enum

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class MarketType(str, Enum):
    """Market type enum."""

    GOALSCORER = "goalscorer"
    ASSIST = "assist"


class Classification(str, Enum):
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
    player_id: str
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


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None, description="Date for recommendations (default: today)"),
    market_type: MarketType | None = Query(None, description="Filter by market type"),
    league: str | None = Query(None, description="Filter by league (ligue1, premier_league)"),
    min_edge: float = Query(0.05, description="Minimum edge threshold"),
) -> RecommendationsResponse:
    """Get betting recommendations for a given date."""
    effective_date = target_date or date.today()
    dt = datetime.combine(effective_date, datetime.min.time(), tzinfo=timezone.utc)

    try:
        from app.services.recommendation_service import get_recommendations_for_date
        from app.strategy.selector import RecommendationFilter as _RF
        filter_config = _RF(min_edge=min_edge)
        if market_type:
            filter_config.markets = [market_type.value]
        if league:
            filter_config.leagues = [league]
        raw_recs = await get_recommendations_for_date(dt, db, filter_config)
    except Exception as exc:
        logger.error("Failed to generate recommendations: %s", exc, exc_info=True)
        raw_recs = []

    # Transform to response models
    recommendations = []
    for rec in raw_recs:
        recommendations.append(Recommendation(
            id=rec.get("id", ""),
            fixture_id=rec.get("fixture_id", ""),
            fixture_name=rec.get("fixture_name", ""),
            kickoff_utc=str(rec.get("kickoff_utc", "")),
            player_id="",
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
        ))

    return RecommendationsResponse(
        date=str(effective_date),
        count=len(recommendations),
        recommendations=recommendations,
    )


@router.get("/recommendations-debug")
async def debug_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None),
):
    """Debug endpoint — returns raw error if pipeline fails."""
    import traceback
    effective_date = target_date or date.today()
    dt = datetime.combine(effective_date, datetime.min.time(), tzinfo=timezone.utc)
    try:
        from app.services.recommendation_service import get_recommendations_for_date as _get_recs
        raw_recs = await _get_recs(dt, db)
        return {"status": "ok", "count": len(raw_recs), "sample": raw_recs[:2]}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "traceback": traceback.format_exc()}


@router.get("/recommendations-ping")
async def ping_recommendations():
    """Minimal test to check if router loads."""
    return {"status": "router_ok"}


@router.get("/recommendations/{recommendation_id}", response_model=Recommendation)
async def get_recommendation_detail(recommendation_id: str) -> Recommendation:
    """Get detailed information about a specific recommendation."""
    # TODO: Implement
    raise NotImplementedError("Not yet implemented")
