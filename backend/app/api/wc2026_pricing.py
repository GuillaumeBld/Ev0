"""WC2026 tournament pricing endpoints."""
from __future__ import annotations

import time
import unicodedata

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.wc2026_pricing import WC2026PlayerPricing
from app.pricing.wc2026_tournament import compute_tournament_pricing

router = APIRouter(prefix="/wc2026/pricing", tags=["wc2026"])


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", (name or "").lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


class PlayerPricingOut(BaseModel):
    nation: str
    player_name: str
    position: str | None
    lambda_goals: float
    lambda_assists: float
    p_1g: float | None
    p_2g: float | None
    p_3g: float | None
    p_4g: float | None
    fair_1g: float | None
    fair_2g: float | None
    fair_3g: float | None
    fair_4g: float | None
    p_1a: float | None
    p_2a: float | None
    p_3a: float | None
    fair_1a: float | None
    fair_2a: float | None
    fair_3a: float | None
    p_top_scorer: float | None
    p_top_assister: float | None
    fair_top_scorer: float | None
    fair_top_assister: float | None
    bk_top_scorer: float | None = None
    bk_top_assister: float | None = None
    edge_top_scorer: float | None = None
    edge_top_assister: float | None = None


class ComputeResult(BaseModel):
    players_computed: int
    nations_computed: int
    duration_s: float


@router.post("/compute", response_model=ComputeResult)
async def compute_pricing(session: AsyncSession = Depends(get_db)) -> ComputeResult:
    """Recompute all WC2026 player tournament pricing. Truncates and reinserts the table."""
    t0 = time.monotonic()
    rows = await compute_tournament_pricing(session)
    await session.execute(text("TRUNCATE TABLE wc2026_player_pricing RESTART IDENTITY"))
    for row in rows:
        session.add(WC2026PlayerPricing(**row))
    await session.commit()
    nations = len({r["nation"] for r in rows})
    return ComputeResult(
        players_computed=len(rows),
        nations_computed=nations,
        duration_s=round(time.monotonic() - t0, 2),
    )


@router.get("/players", response_model=list[PlayerPricingOut])
async def get_pricing_players(
    nation: str | None = None,
    position: str | None = None,
    min_lambda: float | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[PlayerPricingOut]:
    """Return priced players ordered by lambda_goals desc, enriched with bookmaker edge."""
    q = select(WC2026PlayerPricing)
    if nation:
        q = q.where(WC2026PlayerPricing.nation == nation)
    if position:
        q = q.where(WC2026PlayerPricing.position == position)
    if min_lambda is not None:
        q = q.where(WC2026PlayerPricing.lambda_goals >= min_lambda)
    q = q.order_by(WC2026PlayerPricing.lambda_goals.desc())

    result = await session.execute(q)
    players = result.scalars().all()

    # Load bookmaker outright odds in two queries (not N+1)
    from app.models.wc2026_odds import WC2026OutrightOdd

    ts_res = await session.execute(
        select(WC2026OutrightOdd.player_name, WC2026OutrightOdd.odds)
        .where(WC2026OutrightOdd.market_type == "top_scorer")
        .where(WC2026OutrightOdd.player_name.isnot(None))
    )
    bk_scorer: dict[str, float] = {}
    for name, odds in ts_res.all():
        key = _norm_name(name)
        if key not in bk_scorer or odds > bk_scorer[key]:
            bk_scorer[key] = odds   # keep best (highest) odds for bettor

    ta_res = await session.execute(
        select(WC2026OutrightOdd.player_name, WC2026OutrightOdd.odds)
        .where(WC2026OutrightOdd.market_type == "top_assister")
        .where(WC2026OutrightOdd.player_name.isnot(None))
    )
    bk_assister: dict[str, float] = {}
    for name, odds in ta_res.all():
        key = _norm_name(name)
        if key not in bk_assister or odds > bk_assister[key]:
            bk_assister[key] = odds

    out = []
    for p in players:
        key = _norm_name(p.player_name)
        bk_ts = bk_scorer.get(key)
        bk_ta = bk_assister.get(key)
        edge_ts = round((bk_ts / p.fair_top_scorer) - 1, 4) if bk_ts and p.fair_top_scorer else None
        edge_ta = round((bk_ta / p.fair_top_assister) - 1, 4) if bk_ta and p.fair_top_assister else None
        out.append(PlayerPricingOut(
            nation=p.nation,
            player_name=p.player_name,
            position=p.position,
            lambda_goals=p.lambda_goals,
            lambda_assists=p.lambda_assists,
            p_1g=p.p_1g, p_2g=p.p_2g, p_3g=p.p_3g, p_4g=p.p_4g,
            fair_1g=p.fair_1g, fair_2g=p.fair_2g, fair_3g=p.fair_3g, fair_4g=p.fair_4g,
            p_1a=p.p_1a, p_2a=p.p_2a, p_3a=p.p_3a,
            fair_1a=p.fair_1a, fair_2a=p.fair_2a, fair_3a=p.fair_3a,
            p_top_scorer=p.p_top_scorer,
            p_top_assister=p.p_top_assister,
            fair_top_scorer=p.fair_top_scorer,
            fair_top_assister=p.fair_top_assister,
            bk_top_scorer=bk_ts,
            bk_top_assister=bk_ta,
            edge_top_scorer=edge_ts,
            edge_top_assister=edge_ta,
        ))
    return out
