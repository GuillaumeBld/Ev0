"""Pricing API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class GoalscorerPriceRequest(BaseModel):
    """Request for goalscorer pricing."""
    
    player_id: str
    fixture_id: str
    xg_per_90: float
    expected_minutes: float = 90.0
    conversion_rate: float = 1.0  # goals/npxG
    opponent_xga_factor: float = 1.0
    form_factor: float = 1.0


class AssistPriceRequest(BaseModel):
    """Request for assist pricing."""
    
    player_id: str
    fixture_id: str
    xa_per_90: float
    expected_minutes: float = 90.0
    creation_score: float = 1.0
    teammate_finishing_factor: float = 1.0
    opponent_defense_factor: float = 1.0
    form_factor: float = 1.0


class PriceResponse(BaseModel):
    """Pricing response."""
    
    player_id: str
    fixture_id: str
    market_type: str  # "goalscorer" or "assist"
    lambda_intensity: float
    probability: float
    fair_odds: float
    explanation: dict


@router.post("/price/goalscorer", response_model=PriceResponse)
async def price_goalscorer(request: GoalscorerPriceRequest) -> PriceResponse:
    """Calculate fair price for anytime goalscorer market."""
    from app.pricing.goalscorer import calculate_goalscorer_price
    
    result = calculate_goalscorer_price(
        xg_per_90=request.xg_per_90,
        expected_minutes=request.expected_minutes,
        conversion_rate=request.conversion_rate,
        opponent_xga_factor=request.opponent_xga_factor,
        form_factor=request.form_factor,
    )
    
    return PriceResponse(
        player_id=request.player_id,
        fixture_id=request.fixture_id,
        market_type="goalscorer",
        **result,
    )


@router.post("/price/assist", response_model=PriceResponse)
async def price_assist(request: AssistPriceRequest) -> PriceResponse:
    """Calculate fair price for anytime assist market."""
    from app.pricing.assist import calculate_assist_price
    
    result = calculate_assist_price(
        xa_per_90=request.xa_per_90,
        expected_minutes=request.expected_minutes,
        creation_score=request.creation_score,
        teammate_finishing_factor=request.teammate_finishing_factor,
        opponent_defense_factor=request.opponent_defense_factor,
        form_factor=request.form_factor,
    )
    
    return PriceResponse(
        player_id=request.player_id,
        fixture_id=request.fixture_id,
        market_type="assist",
        **result,
    )
