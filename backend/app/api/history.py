"""History & Stats API endpoints."""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
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


class BreakdownItem(BaseModel):
    label: str
    bets: int
    wins: int
    losses: int
    pnl: float
    roi: float


class PnlPoint(BaseModel):
    date: str
    pnl: float
    cumulative: float


class StatsBreakdownResponse(BaseModel):
    by_market: list[BreakdownItem]
    by_league: list[BreakdownItem]
    pnl_trend: list[PnlPoint]


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
        .where(Recommendation.status == "approved")
        .order_by(Recommendation.generated_utc.desc())
        .limit(limit)
    )

    # Filter by result status
    if status and status != "all":
        if status == "running":
            stmt = stmt.where(Recommendation.result.is_(None))
        elif status in ("won", "lost", "void"):
            stmt = stmt.where(Recommendation.result == status)

    result = await db.execute(stmt)
    rows = result.all()

    # Fetch stakes from bankroll entries for these recommendations
    rec_ids = [row[0].id for row in rows]
    stake_map: dict[int, float] = {}
    if rec_ids:
        stake_result = await db.execute(
            select(BankrollEntry.recommendation_id, BankrollEntry.stake).where(
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
        display_status = rec.result if rec.result else "running"

        bets.append(
            HistoryItem(
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
                pnl=rec.pnl if rec.result else None,
                stake=stake_map.get(rec.id),
            )
        )

    return HistoryResponse(count=len(bets), bets=bets)


@router.get("/history/autoflat", response_model=HistoryResponse)
async def get_autoflat_history(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(500, le=1000),
):
    """All recommendations treated as 10€ flat bets, result computed from MatchEvents."""
    from app.models.match_events import MatchEvent

    _market_to_event = {
        "goalscorer": "goal",
        "anytime_score": "goal",
        "assist": "assist",
        "anytime_assist": "assist",
    }

    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .order_by(Recommendation.generated_utc.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    bets = []
    for rec, fixture in rows:
        fixture_name = f"{fixture.home_team} vs {fixture.away_team}"

        computed_result = rec.result
        computed_pnl = rec.pnl

        if computed_result is None and fixture.status == "finished":
            event_type = _market_to_event.get(rec.market_type, rec.market_type)
            ev = await db.execute(
                select(MatchEvent).where(
                    MatchEvent.fixture_id == fixture.id,
                    MatchEvent.player_name == rec.player_name,
                    MatchEvent.event_type == event_type,
                ).limit(1)
            )
            won = ev.scalar_one_or_none() is not None
            computed_result = "won" if won else "lost"
            computed_pnl = round(10.0 * (rec.best_odds - 1) if won else -10.0, 2)

        display_status = computed_result if computed_result else "running"

        bets.append(
            HistoryItem(
                id=rec.id,
                date=str(rec.generated_utc.date()) if rec.generated_utc else "",
                fixture_name=fixture_name,
                player_name=rec.player_name,
                market_type=rec.market_type,
                best_odds=rec.best_odds,
                edge=rec.edge,
                best_bookmaker=rec.best_bookmaker,
                status=display_status,
                result=computed_result,
                pnl=computed_pnl,
                stake=10.0,
            )
        )

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
            select(func.coalesce(func.sum(BankrollEntry.stake), 0.0)).where(
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


@router.get("/stats/breakdown", response_model=StatsBreakdownResponse)
async def get_stats_breakdown(
    db: AsyncSession = Depends(get_db),
):
    """Performance breakdown by market type, league, and P&L trend."""
    # Fetch all settled recommendations with fixture info
    result = await db.execute(
        select(Recommendation, Fixture.home_team, Fixture.away_team, Fixture.league)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(Recommendation.result.in_(["won", "lost"]))
        .order_by(Recommendation.settled_utc.asc())
    )
    rows = result.all()

    # Fetch stakes
    rec_ids = [row[0].id for row in rows]
    stake_map: dict[int, float] = {}
    if rec_ids:
        stake_result = await db.execute(
            select(BankrollEntry.recommendation_id, BankrollEntry.stake).where(
                BankrollEntry.recommendation_id.in_(rec_ids),
                BankrollEntry.entry_type == "bet_placed",
            )
        )
        for rid, stake in stake_result.all():
            if rid is not None:
                stake_map[rid] = stake or 10.0

    # Build breakdowns
    market_buckets: dict[str, dict] = {}
    league_buckets: dict[str, dict] = {}
    pnl_trend: list[PnlPoint] = []
    cumulative = 0.0

    for rec, _home, _away, league in rows:
        pnl = rec.pnl or 0.0
        is_win = rec.result == "won"
        stake = stake_map.get(rec.id, 10.0)

        # Market breakdown
        mt = rec.market_type or "unknown"
        if mt not in market_buckets:
            market_buckets[mt] = {"bets": 0, "wins": 0, "losses": 0, "pnl": 0.0, "staked": 0.0}
        market_buckets[mt]["bets"] += 1
        market_buckets[mt]["wins" if is_win else "losses"] += 1
        market_buckets[mt]["pnl"] += pnl
        market_buckets[mt]["staked"] += stake

        # League breakdown
        lg = league or "unknown"
        if lg not in league_buckets:
            league_buckets[lg] = {"bets": 0, "wins": 0, "losses": 0, "pnl": 0.0, "staked": 0.0}
        league_buckets[lg]["bets"] += 1
        league_buckets[lg]["wins" if is_win else "losses"] += 1
        league_buckets[lg]["pnl"] += pnl
        league_buckets[lg]["staked"] += stake

        # P&L trend
        cumulative += pnl
        dt = (
            str(rec.settled_utc.date())
            if rec.settled_utc
            else str(rec.generated_utc.date())
            if rec.generated_utc
            else ""
        )
        pnl_trend.append(PnlPoint(date=dt, pnl=round(pnl, 2), cumulative=round(cumulative, 2)))

    def _to_breakdown(buckets: dict[str, dict]) -> list[BreakdownItem]:
        items = []
        for label, b in buckets.items():
            staked = b["staked"]
            items.append(
                BreakdownItem(
                    label=label,
                    bets=b["bets"],
                    wins=b["wins"],
                    losses=b["losses"],
                    pnl=round(b["pnl"], 2),
                    roi=round(b["pnl"] / staked, 4) if staked > 0 else 0.0,
                )
            )
        return items

    return StatsBreakdownResponse(
        by_market=_to_breakdown(market_buckets),
        by_league=_to_breakdown(league_buckets),
        pnl_trend=pnl_trend,
    )
