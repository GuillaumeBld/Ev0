"""Recommendations API endpoints."""

from datetime import date
from enum import Enum

from fastapi import APIRouter, Query
from pydantic import BaseModel

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
    target_date: date | None = Query(None, description="Date for recommendations (default: today)"),
    market_type: MarketType | None = Query(None, description="Filter by market type"),
    league: str | None = Query(None, description="Filter by league (ligue1, premier_league)"),
    min_edge: float = Query(0.05, description="Minimum edge threshold"),
) -> RecommendationsResponse:
    """Get betting recommendations for a given date."""
    # TODO: Implement actual recommendation logic
    return RecommendationsResponse(
        date=str(target_date or date.today()),
        count=0,
        recommendations=[],
    )


@router.get("/recommendations/{recommendation_id}", response_model=Recommendation)
async def get_recommendation_detail(recommendation_id: str) -> Recommendation:
    """Get detailed information about a specific recommendation."""
    # TODO: Implement
    raise NotImplementedError("Not yet implemented")
