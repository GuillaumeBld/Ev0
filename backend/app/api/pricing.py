"""Pricing API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

router = APIRouter()


# ── Top-Down match pricing endpoint ───────────────────────────────

class MatchPriceRequest(BaseModel):
    fixture_id: int
    home_xg_override: float | None = None
    away_xg_override: float | None = None
    home_pen_taker_override: int | None = None  # player_id
    away_pen_taker_override: int | None = None  # player_id
    # Optional: redistribute xG among these starters only
    home_starters: list[str] | None = None
    away_starters: list[str] | None = None


class PlayerAllocationOut(BaseModel):
    player_id: int
    player_name: str
    team: str
    position: str | None
    expected_minutes: float
    is_pen_taker: bool
    npxg_share: float
    xa_share: float
    lambda_open_play: float
    lambda_penalty: float
    lambda_total: float
    prob_goal: float
    fair_odds_goal: float
    lambda_assist: float
    prob_assist: float
    fair_odds_assist: float


class MatchPriceResponse(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    xg_source: str = "dixon_coles"
    home_players: list[PlayerAllocationOut]
    away_players: list[PlayerAllocationOut]
    # Populated only when starters were supplied in the request
    home_lineup_players: list[PlayerAllocationOut] | None = None
    away_lineup_players: list[PlayerAllocationOut] | None = None


@router.post("/price/match", response_model=MatchPriceResponse)
async def price_match(
    request: MatchPriceRequest,
    db: AsyncSession = Depends(get_db),
) -> MatchPriceResponse:
    """Calculate Top-Down pricing for all players in a match.

    Returns fair odds for both Anytime Goalscorer and Anytime Assist
    markets for every player in both squads.
    """
    from sqlalchemy import select

    from app.models.fixtures import Fixture
    from app.pricing.team_xg import load_match_pricing

    result = await db.execute(select(Fixture).where(Fixture.id == request.fixture_id))
    fixture = result.scalar_one_or_none()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    pricing = await load_match_pricing(
        db,
        fixture,
        home_xg_override=request.home_xg_override,
        away_xg_override=request.away_xg_override,
        home_pen_taker_override=request.home_pen_taker_override,
        away_pen_taker_override=request.away_pen_taker_override,
        home_starters=request.home_starters,
        away_starters=request.away_starters,
    )
    if pricing is None:
        raise HTTPException(status_code=503, detail="No market odds available for this fixture")

    def _to_out(allocs: list) -> list[PlayerAllocationOut]:
        return [
            PlayerAllocationOut(
                player_id=a.player_id,
                player_name=a.player_name,
                team=a.team,
                position=a.position,
                expected_minutes=a.expected_minutes,
                is_pen_taker=a.is_pen_taker,
                npxg_share=a.npxg_share,
                xa_share=a.xa_share,
                lambda_open_play=a.lambda_open_play,
                lambda_penalty=a.lambda_penalty,
                lambda_total=a.lambda_total,
                prob_goal=a.prob_goal,
                fair_odds_goal=a.fair_odds_goal,
                lambda_assist=a.lambda_assist,
                prob_assist=a.prob_assist,
                fair_odds_assist=a.fair_odds_assist,
            )
            for a in allocs
        ]

    return MatchPriceResponse(
        fixture_id=pricing.fixture_id,
        home_team=pricing.home_team,
        away_team=pricing.away_team,
        home_match_xg=pricing.home_match_xg,
        away_match_xg=pricing.away_match_xg,
        xg_source=pricing.xg_source,
        home_players=_to_out(pricing.home_players),
        away_players=_to_out(pricing.away_players),
        home_lineup_players=_to_out(pricing.home_lineup_players) if pricing.home_lineup_players else None,
        away_lineup_players=_to_out(pricing.away_lineup_players) if pricing.away_lineup_players else None,
    )
