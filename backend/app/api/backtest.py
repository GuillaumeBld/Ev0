"""Backtest API endpoint."""

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response models ────────────────────────────────────


class BacktestConfig(BaseModel):
    period: str = "6m"  # 6m, 12m, season_2023_24
    min_edge: float = 0.05  # 5%
    stake_method: str = "flat_10"  # flat_10, kelly_25, kelly_10
    markets: str = "all"  # all, goalscorer, assist


class PnlPoint(BaseModel):
    date: str
    cumPnl: float


class CalibrationPoint(BaseModel):
    predicted: float
    actual: float
    count: int


class EdgeBucket(BaseModel):
    bucket: str
    count: int
    roi: float


class BacktestResponse(BaseModel):
    roi: float
    brierScore: float
    winRate: float
    wins: int
    losses: int
    totalBets: int
    pnlCurve: list[PnlPoint]
    calibration: list[CalibrationPoint]
    edgeDistribution: list[EdgeBucket]


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/backtest", response_model=BacktestResponse)
async def run_backtest(
    config: BacktestConfig,
    db: AsyncSession = Depends(get_db),
):
    """Run a backtest on historical settled recommendations."""
    # Determine date range
    now = datetime.now(UTC)
    if config.period == "12m":
        start = now - timedelta(days=365)
    elif config.period == "season_2023_24":
        start = datetime(2023, 8, 1, tzinfo=UTC)
        now = datetime(2024, 6, 30, tzinfo=UTC)
    else:  # default 6m
        start = now - timedelta(days=180)

    # Query settled recommendations
    stmt = (
        select(Recommendation)
        .where(
            Recommendation.result.isnot(None),
            Recommendation.generated_utc >= start,
            Recommendation.generated_utc <= now,
            Recommendation.edge >= config.min_edge,
        )
        .order_by(Recommendation.generated_utc.asc())
    )

    if config.markets == "goalscorer":
        stmt = stmt.where(Recommendation.market_type == "goalscorer")
    elif config.markets == "assist":
        stmt = stmt.where(Recommendation.market_type == "assist")

    result = await db.execute(stmt)
    recs = result.scalars().all()

    # If no data, return zeroed results
    if not recs:
        return BacktestResponse(
            roi=0,
            brierScore=0,
            winRate=0,
            wins=0,
            losses=0,
            totalBets=0,
            pnlCurve=[],
            calibration=[],
            edgeDistribution=[],
        )

    # Compute stake per bet
    def compute_stake(rec: Recommendation) -> float:
        if config.stake_method == "kelly_25":
            kelly = (rec.edge * rec.best_odds - 1) / (rec.best_odds - 1) if rec.best_odds > 1 else 0
            return max(0, kelly * 0.25 * 100)  # 25% Kelly on 100€ bankroll
        elif config.stake_method == "kelly_10":
            kelly = (rec.edge * rec.best_odds - 1) / (rec.best_odds - 1) if rec.best_odds > 1 else 0
            return max(0, kelly * 0.10 * 100)
        return 10.0  # flat 10€

    # Process bets
    wins = 0
    losses = 0
    total_pnl = 0.0
    total_staked = 0.0
    monthly_pnl: dict[str, float] = defaultdict(float)
    brier_sum = 0.0

    # Edge distribution buckets
    edge_buckets = {
        "5-8%": {"count": 0, "pnl": 0.0, "staked": 0.0},
        "8-12%": {"count": 0, "pnl": 0.0, "staked": 0.0},
        "12-15%": {"count": 0, "pnl": 0.0, "staked": 0.0},
        "15%+": {"count": 0, "pnl": 0.0, "staked": 0.0},
    }

    # Calibration bins
    cal_bins: dict[int, dict] = defaultdict(
        lambda: {"predicted_sum": 0.0, "actual_sum": 0, "count": 0}
    )

    for rec in recs:
        stake = compute_stake(rec)
        is_win = rec.result == "won"

        if is_win:
            pnl = stake * (rec.best_odds - 1)
            wins += 1
        else:
            pnl = -stake
            losses += 1

        total_pnl += pnl
        total_staked += stake

        # Monthly P&L
        month_key = rec.generated_utc.strftime("%Y-%m") if rec.generated_utc else "unknown"
        monthly_pnl[month_key] += pnl

        # Brier score: (predicted_prob - outcome)^2
        predicted_prob = (
            rec.fair_probability
            if rec.fair_probability
            else (1.0 / rec.fair_odds if rec.fair_odds > 0 else 0.5)
        )
        outcome = 1.0 if is_win else 0.0
        brier_sum += (predicted_prob - outcome) ** 2

        # Edge bucket
        edge_pct = rec.edge * 100
        if edge_pct >= 15:
            bucket_key = "15%+"
        elif edge_pct >= 12:
            bucket_key = "12-15%"
        elif edge_pct >= 8:
            bucket_key = "8-12%"
        else:
            bucket_key = "5-8%"

        edge_buckets[bucket_key]["count"] += 1
        edge_buckets[bucket_key]["pnl"] += pnl
        edge_buckets[bucket_key]["staked"] += stake

        # Calibration bin (round predicted prob to nearest 0.1)
        bin_idx = min(9, max(0, round(predicted_prob * 10)))
        cal_bins[bin_idx]["predicted_sum"] += predicted_prob
        cal_bins[bin_idx]["actual_sum"] += 1 if is_win else 0
        cal_bins[bin_idx]["count"] += 1

    total_bets = wins + losses
    win_rate = wins / total_bets if total_bets > 0 else 0.0
    roi = total_pnl / total_staked if total_staked > 0 else 0.0
    brier_score = brier_sum / total_bets if total_bets > 0 else 0.0

    # Build P&L curve (cumulative)
    pnl_curve = []
    cum = 0.0
    for month in sorted(monthly_pnl.keys()):
        cum += monthly_pnl[month]
        pnl_curve.append(PnlPoint(date=month, cumPnl=round(cum, 2)))

    # Build calibration data
    calibration = []
    for bin_idx in sorted(cal_bins.keys()):
        b = cal_bins[bin_idx]
        if b["count"] > 0:
            calibration.append(
                CalibrationPoint(
                    predicted=round(b["predicted_sum"] / b["count"], 3),
                    actual=round(b["actual_sum"] / b["count"], 3),
                    count=b["count"],
                )
            )

    # Build edge distribution
    edge_dist = []
    for bucket_name in ["5-8%", "8-12%", "12-15%", "15%+"]:
        b = edge_buckets[bucket_name]
        bucket_roi = (b["pnl"] / b["staked"] * 100) if b["staked"] > 0 else 0.0
        edge_dist.append(
            EdgeBucket(
                bucket=bucket_name,
                count=int(b["count"]),
                roi=round(bucket_roi, 1),
            )
        )

    return BacktestResponse(
        roi=round(roi, 4),
        brierScore=round(brier_score, 4),
        winRate=round(win_rate, 4),
        wins=wins,
        losses=losses,
        totalBets=total_bets,
        pnlCurve=pnl_curve,
        calibration=calibration,
        edgeDistribution=edge_dist,
    )
