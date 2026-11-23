"""Pricing API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._badges import MatchBadges, badge_fields_for_fixtures
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Top-Down match pricing endpoint ───────────────────────────────

class MatchPriceRequest(BaseModel):
    fixture_id: int
    home_xg_override: float | None = None
    away_xg_override: float | None = None
    home_pen_taker_override: int | None = None  # player_id
    away_pen_taker_override: int | None = None  # player_id
    # Compo : titulaires par identifiant (bzz_players.api_id). Le xG de
    # l'équipe est alors réparti entre ces seuls joueurs, Alpha et Beta.
    home_starter_ids: list[int] | None = None
    away_starter_ids: list[int] | None = None


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
    # Supersub (Alpha — mono-season rhythm)
    p_goal_supersub: float = 0.0
    fair_odds_goal_supersub: float = 99.0
    p_assist_supersub: float = 0.0
    fair_odds_assist_supersub: float = 99.0
    p_sub: float = 0.35
    avg_sub_time: float = 65.0
    sub_premium_goal: float = 0.0
    sub_premium_assist: float = 0.0
    # Beta (career-blended rhythm) — same match context as Alpha, only the
    # goal/assist rate per 90 differs. Equals the Alpha fields above when the
    # player has no imported career (no blended rhythm available).
    # Beta sans sub : sert seulement à juger une cote de book sans sub.
    beta_prob_goal: float = 0.0
    beta_fair_odds_goal: float = 99.0
    beta_prob_assist: float = 0.0
    beta_fair_odds_assist: float = 99.0
    beta_p_goal_supersub: float = 0.0
    beta_fair_odds_goal_supersub: float = 99.0
    beta_p_assist_supersub: float = 0.0
    beta_fair_odds_assist_supersub: float = 99.0
    # Saison d'ou viennent les stats (toutes competitions fusionnees) ;
    # None = joueur sans aucune stat (cote issue du poste).
    stats_season: str | None = None
    # Vrai quand ces stats datent d'une saison anterieure a la saison courante.
    stats_stale: bool = False


class MatchPriceResponse(MatchBadges):
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
    # Signalements de la compo : titulaire inconnu de l'effectif (ignoré),
    # trop peu de titulaires reconnus (compo non appliquée).
    home_lineup_warnings: list[str] = []
    away_lineup_warnings: list[str] = []
    last_scraped_at: str | None = None  # ISO timestamp of freshest bookmaker odds snapshot


@router.post("/price/match", response_model=MatchPriceResponse)
async def price_match(
    request: MatchPriceRequest,
    db: AsyncSession = Depends(get_db),
) -> MatchPriceResponse:
    """Calculate Top-Down pricing for all players in a match.

    Returns fair odds for both Anytime Goalscorer and Anytime Assist
    markets for every player in both squads.
    """
    from app.models.fixtures import Fixture
    from app.models.match_odds import MatchOddsSnapshot
    from app.pricing.beta_pricing import compute_beta_allocations
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
        home_starter_ids=request.home_starter_ids,
        away_starter_ids=request.away_starter_ids,
    )
    if pricing is None:
        snap_result = await db.execute(
            select(MatchOddsSnapshot.snapshot_utc)
            .where(MatchOddsSnapshot.fixture_id == request.fixture_id)
            .order_by(MatchOddsSnapshot.snapshot_utc.desc())
            .limit(1)
        )
        last_ts = snap_result.scalar_one_or_none()
        raise HTTPException(
            status_code=503,
            detail={
                "message": "No market odds available for this fixture",
                "last_scraped_at": last_ts.isoformat() if last_ts else None,
            },
        )

    # Beta (career-blended rhythm) — additive, best-effort. A failure here
    # must never break the (already fully computed) Alpha response: log and
    # fall back to empty maps, which makes every player's beta_* fields
    # mirror their Alpha fields below (same fallback as "no career data").
    try:
        home_beta, away_beta, home_lineup_beta, away_lineup_beta = await compute_beta_allocations(
            db,
            fixture,
            pricing.home_players,
            pricing.away_players,
            pricing.home_match_xg,
            pricing.away_match_xg,
            home_pen_taker_override=request.home_pen_taker_override,
            away_pen_taker_override=request.away_pen_taker_override,
            home_starter_ids=request.home_starter_ids if pricing.home_lineup_players else None,
            away_starter_ids=request.away_starter_ids if pricing.away_lineup_players else None,
            alpha_home_lineup=pricing.home_lineup_players,
            alpha_away_lineup=pricing.away_lineup_players,
        )
    except Exception:
        logger.exception(
            "compute_beta_allocations failed for fixture %s — falling back to Alpha for beta_* fields",
            request.fixture_id,
        )
        home_beta, away_beta, home_lineup_beta, away_lineup_beta = {}, {}, {}, {}

    # Saison courante (season_service) pour signaler des stats d'une saison
    # passee. Lue seulement si au moins un joueur porte une saison de stats.
    all_allocs = [
        *pricing.home_players, *pricing.away_players,
        *(pricing.home_lineup_players or []), *(pricing.away_lineup_players or []),
    ]
    saison_courante: str | None = None
    if any(getattr(a, "stats_season", None) for a in all_allocs):
        from app.services.season_service import current_season

        try:
            saison_courante = await current_season(db)
        except Exception:
            # Jamais d'echec du prix pour un simple signalement : stats_stale
            # reste False pour tout le monde.
            logger.warning(
                "current_season indisponible pour fixture %s — stats_stale=False",
                request.fixture_id, exc_info=True,
            )

    def _to_out(allocs: list, beta_by_id: dict) -> list[PlayerAllocationOut]:
        out = []
        for a in allocs:
            beta = beta_by_id.get(a.player_id)
            stats_season = getattr(a, "stats_season", None)
            out.append(
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
                    p_goal_supersub=getattr(a, "p_goal_supersub", 0.0),
                    fair_odds_goal_supersub=getattr(a, "fair_odds_goal_supersub", 99.0),
                    p_assist_supersub=getattr(a, "p_assist_supersub", 0.0),
                    fair_odds_assist_supersub=getattr(a, "fair_odds_assist_supersub", 99.0),
                    p_sub=getattr(a, "p_sub", 0.35),
                    avg_sub_time=getattr(a, "avg_sub_time", 65.0),
                    sub_premium_goal=getattr(a, "sub_premium_goal", 0.0),
                    sub_premium_assist=getattr(a, "sub_premium_assist", 0.0),
                    beta_prob_goal=getattr(beta, "prob_goal", a.prob_goal),
                    beta_fair_odds_goal=getattr(beta, "fair_odds_goal", a.fair_odds_goal),
                    beta_prob_assist=getattr(beta, "prob_assist", a.prob_assist),
                    beta_fair_odds_assist=getattr(beta, "fair_odds_assist", a.fair_odds_assist),
                    beta_p_goal_supersub=getattr(beta, "p_goal_supersub", a.p_goal_supersub),
                    beta_fair_odds_goal_supersub=getattr(
                        beta, "fair_odds_goal_supersub", a.fair_odds_goal_supersub
                    ),
                    beta_p_assist_supersub=getattr(beta, "p_assist_supersub", a.p_assist_supersub),
                    beta_fair_odds_assist_supersub=getattr(
                        beta, "fair_odds_assist_supersub", a.fair_odds_assist_supersub
                    ),
                    stats_season=stats_season,
                    stats_stale=(
                        stats_season is not None
                        and saison_courante is not None
                        and stats_season < saison_courante
                    ),
                )
            )
        return out

    badge_map = await badge_fields_for_fixtures(db, [fixture])

    return MatchPriceResponse(
        fixture_id=pricing.fixture_id,
        home_team=pricing.home_team,
        away_team=pricing.away_team,
        home_match_xg=pricing.home_match_xg,
        away_match_xg=pricing.away_match_xg,
        xg_source=pricing.xg_source,
        home_players=_to_out(pricing.home_players, home_beta),
        away_players=_to_out(pricing.away_players, away_beta),
        home_lineup_players=(
            _to_out(pricing.home_lineup_players, home_lineup_beta)
            if pricing.home_lineup_players else None
        ),
        away_lineup_players=(
            _to_out(pricing.away_lineup_players, away_lineup_beta)
            if pricing.away_lineup_players else None
        ),
        home_lineup_warnings=list(getattr(pricing, "home_lineup_warnings", []) or []),
        away_lineup_warnings=list(getattr(pricing, "away_lineup_warnings", []) or []),
        last_scraped_at=pricing.last_scraped_at.isoformat() if pricing.last_scraped_at else None,
        **badge_map[fixture.id],
    )
