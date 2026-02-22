"""History & Stats API endpoints."""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bankroll import BankrollEntry
from app.models.fixtures import Fixture
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response models ──────────────────────────────────────────────

class HistoryItem(BaseModel):
    id: int
    date: str
    fixture_name: str
    player_name: str
    market_type: str
    best_odds: float
    edge: float
    best_bookmaker: str
    status: str
    result: str | None
    pnl: float | None
    stake: float | None


class HistoryResponse(BaseModel):
    count: int
    bets: list[HistoryItem]


class StatsResponse(BaseModel):
    total_bets: int
    wins: int
    losses: int
    pending: int
    total_pnl: float
    win_rate: float
    roi: float


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/history", response_model=HistoryResponse)
async def get_history(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter: won, lost, pending, all"),
    limit: int = Query(200, le=500),
):
    """List betting history from settled/executed recommendations."""
    stmt = (
        select(Recommendation, Fixture.home_team, Fixture.away_team)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(
            or_(
                Recommendation.status.in_(["executed", "approved"]),
                Recommendation.result.isnot(None),
            )
        )
        .order_by(Recommendation.generated_utc.desc())
        .limit(limit)
    )

    # Filter by result status
    if status and status != "all":
        if status == "pending":
            stmt = stmt.where(Recommendation.result.is_(None))
        elif status in ("won", "lost"):
            stmt = stmt.where(Recommendation.result == status)

    result = await db.execute(stmt)
    rows = result.all()

    # Fetch stakes from bankroll entries for these recommendations
    rec_ids = [row[0].id for row in rows]
    stake_map: dict[int, float] = {}
    if rec_ids:
        stake_result = await db.execute(
            select(BankrollEntry.recommendation_id, BankrollEntry.stake)
            .where(
                BankrollEntry.recommendation_id.in_(rec_ids),
                BankrollEntry.entry_type == "bet_placed",
            )
        )
        for rid, stake in stake_result.all():
            if rid is not None:
                stake_map[rid] = stake or 10.0

    bets = []
    for rec, home_team, away_team in rows:
        fixture_name = f"{home_team} vs {away_team}"
        # Determine display status
        if rec.result:
            display_status = rec.result  # won, lost, void, push
        else:
            display_status = "pending"

        bets.append(HistoryItem(
            id=rec.id,
            date=str(rec.generated_utc.date()) if rec.generated_utc else "",
            fixture_name=fixture_name,
            player_name=rec.player_name,
            market_type=rec.market_type,
            best_odds=rec.best_odds,
            edge=rec.edge,
            best_bookmaker=rec.best_bookmaker,
            status=display_status,
            result=rec.result,
            pnl=rec.pnl or 0.0,
            stake=stake_map.get(rec.id),
        ))

    return HistoryResponse(count=len(bets), bets=bets)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
):
    """Aggregate stats from recommendations."""
    # Count settled bets (those with a result)
    base = select(Recommendation).where(
        or_(
            Recommendation.status.in_(["executed", "approved"]),
            Recommendation.result.isnot(None),
        )
    )
    result = await db.execute(base)
    recs = result.scalars().all()

    wins = sum(1 for r in recs if r.result == "won")
    losses = sum(1 for r in recs if r.result == "lost")
    pending = sum(1 for r in recs if r.result is None)
    total = len(recs)
    total_pnl = sum(r.pnl or 0.0 for r in recs)

    settled = wins + losses
    win_rate = (wins / settled) if settled > 0 else 0.0

    # ROI = total_pnl / total_staked (from bankroll entries)
    settled_rec_ids = [r.id for r in recs if r.result in ("won", "lost")]
    total_staked = 0.0
    if settled_rec_ids:
        stake_result = await db.execute(
            select(func.coalesce(func.sum(BankrollEntry.stake), 0.0))
            .where(
                BankrollEntry.recommendation_id.in_(settled_rec_ids),
                BankrollEntry.entry_type == "bet_placed",
            )
        )
        total_staked = float(stake_result.scalar() or 0.0)

    # Fallback to flat 10€ if no bankroll entries exist
    if total_staked == 0.0 and settled > 0:
        total_staked = settled * 10.0

    roi = (total_pnl / total_staked) if total_staked > 0 else 0.0

    return StatsResponse(
        total_bets=total,
        wins=wins,
        losses=losses,
        pending=pending,
        total_pnl=round(total_pnl, 2),
        win_rate=round(win_rate, 4),
        roi=round(roi, 4),
    )
