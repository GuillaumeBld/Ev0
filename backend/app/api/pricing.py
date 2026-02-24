"""Pricing API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

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


class MatchPriceRequest(BaseModel):
    """Request for match-level xG pricing."""

    fixture_id: str  # external_id (e.g. "fotmob_4813749")


class MatchPriceResponse(BaseModel):
    """Match-level xG response with source attribution."""

    fixture_id: str
    home_team: str
    away_team: str
    home_xg: float
    away_xg: float
    home_xg_source: str  # "implied" | "dixon"
    away_xg_source: str


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


@router.post("/price/match", response_model=MatchPriceResponse)
async def price_match(
    request: MatchPriceRequest,
    db: AsyncSession = Depends(get_db),
) -> MatchPriceResponse:
    """Return implied team xG for a fixture using the hybrid model.

    Uses bookmaker goalscorer odds to reverse-engineer team xG when ≥3
    players per team have valid odds.  Falls back to sum-of-player Dixon
    style xG otherwise.
    """
    from app.models.fixtures import Fixture
    from app.models.odds import OddsSnapshot as OddsSnapshotModel
    from app.models.players import Player, PlayerStats
    from app.pricing.team_xg import compute_team_xg_scale
    from app.services.recommendation_service import (
        DEFAULT_POSITION_FALLBACK,
        POSITION_DEFAULTS,
        _normalize_position,
    )

    # Load fixture
    fixture_result = await db.execute(
        select(Fixture).where(Fixture.external_id == request.fixture_id)
    )
    fixture = fixture_result.scalar_one_or_none()
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")

    # Load latest player stats
    latest_subq = (
        select(
            PlayerStats.player_id,
            func.max(PlayerStats.as_of_utc).label("max_date"),
        )
        .group_by(PlayerStats.player_id)
        .subquery()
    )
    stats_result = await db.execute(
        select(PlayerStats, Player.name, Player.team, Player.position)
        .join(Player, Player.id == PlayerStats.player_id)
        .join(
            latest_subq,
            (PlayerStats.player_id == latest_subq.c.player_id)
            & (PlayerStats.as_of_utc == latest_subq.c.max_date),
        )
        .where(Player.team.in_([fixture.home_team, fixture.away_team]))
    )

    player_stats: dict[str, dict] = {}
    for row in stats_result.all():
        ps: PlayerStats = row[0]
        name: str = row[1]
        team: str | None = row[2]
        position = _normalize_position(row[3])
        if position == "GK":
            continue
        minutes = ps.minutes_played or 0
        matches = ps.matches_played or 1
        pos_defaults = (
            POSITION_DEFAULTS.get(position, DEFAULT_POSITION_FALLBACK)
            if position
            else DEFAULT_POSITION_FALLBACK
        )
        player_stats[name] = {
            "team": team,
            "xg_per_90": ps.xg_per_90 if ps.xg_per_90 is not None else pos_defaults["xg_per_90"],
            "expected_minutes": minutes / matches if matches > 0 else 75.0,
        }

    # Load best goalscorer odds per normalized player name
    from app.ingestion.odds import normalize_selection_name

    odds_result = await db.execute(
        select(OddsSnapshotModel)
        .where(OddsSnapshotModel.fixture_id == fixture.id)
        .where(OddsSnapshotModel.market_type == "goalscorer")
    )
    best_goalscorer: dict[str, float] = {}
    for o in odds_result.scalars().all():
        norm = normalize_selection_name(o.player_name)
        if o.odds > best_goalscorer.get(norm, 0.0):
            best_goalscorer[norm] = o.odds

    # Build team player lists and compute scales
    home_players = [(n, s) for n, s in player_stats.items() if s.get("team") == fixture.home_team]
    away_players = [(n, s) for n, s in player_stats.items() if s.get("team") == fixture.away_team]

    home_xg_scale, home_xg_source = compute_team_xg_scale(home_players, best_goalscorer)
    away_xg_scale, away_xg_source = compute_team_xg_scale(away_players, best_goalscorer)

    home_dixon_xg = sum(
        s.get("xg_per_90", 0.0) * s.get("expected_minutes", 75.0) / 90.0
        for _, s in home_players
    )
    away_dixon_xg = sum(
        s.get("xg_per_90", 0.0) * s.get("expected_minutes", 75.0) / 90.0
        for _, s in away_players
    )

    home_xg = home_dixon_xg * home_xg_scale
    away_xg = away_dixon_xg * away_xg_scale

    return MatchPriceResponse(
        fixture_id=request.fixture_id,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        home_xg=round(home_xg, 3),
        away_xg=round(away_xg, 3),
        home_xg_source=home_xg_source,
        away_xg_source=away_xg_source,
    )
