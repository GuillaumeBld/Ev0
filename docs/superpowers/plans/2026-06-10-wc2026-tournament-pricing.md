# WC2026 Tournament Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price player goal/assist cuts (≥1/2/3/4) and top scorer/assister outrights for WC2026 by distributing pre-tournament team BM via the existing per-match pricing engine, storing results in DB, and surfacing edges against scraped bookmaker outrights.

**Architecture:** Reuse `compute_player_shares` + `allocate_player` from `backend/app/pricing/team_xg.py` with `lambda_team = team_BM`. Monte Carlo (50k sims, numpy) for top scorer/assister. Results stored in `wc2026_player_pricing` (truncate+reinsert on recompute). Edges computed at query time via join on `wc2026_outright_odds`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL, numpy, Next.js 14, Tailwind, clsx.

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `backend/app/ingestion/wc2026/team_bm.py` | Create | TEAM_BM dict (48 nations) + nation name aliases |
| `backend/app/models/wc2026_pricing.py` | Create | SQLAlchemy model for `wc2026_player_pricing` |
| `backend/alembic/versions/039_wc2026_player_pricing.py` | Create | Migration: create table |
| `backend/app/pricing/wc2026_tournament.py` | Create | `compute_tournament_pricing()` + `poisson_ge()` + Monte Carlo |
| `backend/app/api/wc2026_pricing.py` | Create | FastAPI router: POST compute + GET players |
| `backend/app/main.py` | Modify | Register new router |
| `backend/tests/pricing/test_wc2026_tournament.py` | Create | Unit tests for pricing math |
| `frontend/src/lib/api.ts` | Modify | Add `WCPlayerPricing` interface + `computeWCPricing()` + `getWCPricingPlayers()` |
| `frontend/src/components/wc2026/PricingTable.tsx` | Create | Sortable table: cuts + top scorer/assister + edge |
| `frontend/src/app/dashboard/wc2026/pricing/page.tsx` | Create | Page with two tabs (Buts / Passes) + Recalculer button |

---

## Task 1: Team BM dict

**Files:**
- Create: `backend/app/ingestion/wc2026/team_bm.py`
- Test: `backend/tests/ingestion/wc2026/test_team_bm.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/ingestion/wc2026/test_team_bm.py
from app.ingestion.wc2026.team_bm import TEAM_BM, WC2026_NATION_NAME_ALIASES


def test_team_bm_has_48_nations():
    assert len(TEAM_BM) == 48


def test_team_bm_values_positive():
    for nation, bm in TEAM_BM.items():
        assert bm > 0, f"BM for {nation} is {bm}"


def test_team_bm_spain_is_top():
    assert TEAM_BM["Spain"] == max(TEAM_BM.values())


def test_aliases_values_not_in_team_bm():
    # Aliases map FROM TEAM_BM names to Bzzoiro names — keys should not be in each other
    for key in WC2026_NATION_NAME_ALIASES:
        assert key in TEAM_BM, f"Alias key {key!r} not in TEAM_BM"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/ingestion/wc2026/test_team_bm.py -v
```
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create the file**

```python
# backend/app/ingestion/wc2026/team_bm.py
"""Pre-tournament expected goals (BM) per nation for WC2026.

Keys must match wc2026_squad_players.nation (English names from DB).
WC2026_NATION_NAME_ALIASES maps TEAM_BM keys to Bzzoiro national_team_name
when they differ (used when resolving national_team_api_id in bzz_players).
"""

TEAM_BM: dict[str, float] = {
    "Spain":                13.03,
    "Brazil":               12.33,
    "Germany":              11.78,
    "England":              11.74,
    "France":               10.90,
    "Argentina":            10.83,
    "Portugal":             10.23,
    "Belgium":               9.56,
    "Switzerland":           8.27,
    "Netherlands":           7.97,
    "Colombia":              7.69,
    "Norway":                7.15,
    "Mexico":                7.08,
    "Ecuador":               6.63,
    "Uruguay":               6.51,
    "Canada":                6.23,
    "United States":         6.20,
    "Croatia":               6.06,
    "Morocco":               5.98,
    "Ivory Coast":           5.85,
    "Austria":               5.78,
    "Turkey":                5.73,
    "Japan":                 5.38,
    "Senegal":               5.31,
    "Egypt":                 4.96,
    "Scotland":              4.64,
    "South Korea":           4.48,
    "Czechia":               4.29,
    "Sweden":                4.21,
    "Bosnia-Herzegovina":    4.15,
    "Algeria":               4.08,
    "Paraguay":              3.95,
    "Iran":                  3.80,
    "Ghana":                 3.22,
    "Australia":             3.19,
    "Congo DR":              2.94,
    "Panama":                2.94,
    "New Zealand":           2.74,
    "South Africa":          2.64,
    "Uzbekistan":            2.62,
    "Tunisia":               2.56,
    "Cape Verde Islands":    2.51,
    "Saudi Arabia":          2.35,
    "Curaçao":               2.17,
    "Haiti":                 2.08,
    "Jordan":                2.05,
    "Qatar":                 1.99,
    "Iraq":                  1.53,
}

# Maps TEAM_BM nation names → Bzzoiro national_team_name when they diverge.
# Add entries here if compute_tournament_pricing logs "no Bzzoiro national team for X".
WC2026_NATION_NAME_ALIASES: dict[str, str] = {
    "United States":      "USA",
    "Ivory Coast":        "Côte d'Ivoire",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Congo DR":           "DR Congo",
    "Cape Verde Islands": "Cabo Verde",
    "Czechia":            "Czech Republic",
    "South Korea":        "Korea Republic",
}
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/ingestion/wc2026/test_team_bm.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/wc2026/team_bm.py backend/tests/ingestion/wc2026/test_team_bm.py
git commit -m "feat(wc2026): add team BM dict for tournament pricing"
```

---

## Task 2: SQLAlchemy model + Alembic migration

**Files:**
- Create: `backend/app/models/wc2026_pricing.py`
- Create: `backend/alembic/versions/039_wc2026_player_pricing.py`
- Modify: `backend/app/models/__init__.py` (if it imports models explicitly)

- [ ] **Step 1: Create the model**

```python
# backend/app/models/wc2026_pricing.py
"""WC2026 per-player tournament pricing results."""
from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WC2026PlayerPricing(Base):
    __tablename__ = "wc2026_player_pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[str | None] = mapped_column(String(10), nullable=True)

    lambda_goals: Mapped[float] = mapped_column(Float, nullable=False)
    lambda_assists: Mapped[float] = mapped_column(Float, nullable=False)

    # Cuts — goals
    p_1g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_2g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_3g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_4g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_1g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_2g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_3g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_4g: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cuts — assists
    p_1a: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_2a: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_3a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_1a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_2a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_3a: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Outrights
    p_top_scorer: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_top_assister: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_top_scorer: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_top_assister: Mapped[float | None] = mapped_column(Float, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Create the migration**

```python
# backend/alembic/versions/039_wc2026_player_pricing.py
"""Create wc2026_player_pricing table.

Revision ID: 039
Revises: 038
Create Date: 2026-06-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_player_pricing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=False),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(10), nullable=True),
        sa.Column("lambda_goals", sa.Float(), nullable=False),
        sa.Column("lambda_assists", sa.Float(), nullable=False),
        sa.Column("p_1g", sa.Float(), nullable=True),
        sa.Column("p_2g", sa.Float(), nullable=True),
        sa.Column("p_3g", sa.Float(), nullable=True),
        sa.Column("p_4g", sa.Float(), nullable=True),
        sa.Column("fair_1g", sa.Float(), nullable=True),
        sa.Column("fair_2g", sa.Float(), nullable=True),
        sa.Column("fair_3g", sa.Float(), nullable=True),
        sa.Column("fair_4g", sa.Float(), nullable=True),
        sa.Column("p_1a", sa.Float(), nullable=True),
        sa.Column("p_2a", sa.Float(), nullable=True),
        sa.Column("p_3a", sa.Float(), nullable=True),
        sa.Column("fair_1a", sa.Float(), nullable=True),
        sa.Column("fair_2a", sa.Float(), nullable=True),
        sa.Column("fair_3a", sa.Float(), nullable=True),
        sa.Column("p_top_scorer", sa.Float(), nullable=True),
        sa.Column("p_top_assister", sa.Float(), nullable=True),
        sa.Column("fair_top_scorer", sa.Float(), nullable=True),
        sa.Column("fair_top_assister", sa.Float(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wc2026_player_pricing_nation", "wc2026_player_pricing", ["nation"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_player_pricing_nation", "wc2026_player_pricing")
    op.drop_table("wc2026_player_pricing")
```

- [ ] **Step 3: Run the migration**

```bash
cd backend && alembic upgrade 039
```
Expected: `Running upgrade 038 -> 039, Create wc2026_player_pricing table`

- [ ] **Step 4: Verify the table exists**

```bash
cd backend && python -c "
import asyncio
from app.db import get_engine
from sqlalchemy import text

async def check():
    from app.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(\"SELECT COUNT(*) FROM wc2026_player_pricing\"))
        print('rows:', r.scalar())

asyncio.run(check())
"
```
Expected: `rows: 0`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/wc2026_pricing.py backend/alembic/versions/039_wc2026_player_pricing.py
git commit -m "feat(wc2026): add wc2026_player_pricing model and migration"
```

---

## Task 3: Tournament pricing engine

**Files:**
- Create: `backend/app/pricing/wc2026_tournament.py`
- Create: `backend/tests/pricing/test_wc2026_tournament.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/pricing/test_wc2026_tournament.py
import math
import pytest
from app.pricing.wc2026_tournament import poisson_ge, run_monte_carlo


def test_poisson_ge_k1_lambda1():
    # P(X >= 1) = 1 - e^(-1) for lambda=1
    assert abs(poisson_ge(1.0, 1) - (1 - math.exp(-1))) < 1e-10


def test_poisson_ge_k2_lambda1():
    # P(X >= 2) = 1 - e^(-1)(1 + 1) for lambda=1
    expected = 1 - math.exp(-1) * (1 + 1)
    assert abs(poisson_ge(1.0, 2) - expected) < 1e-10


def test_poisson_ge_k1_lambda0():
    # P(X >= 1) for lambda=0 should be 0
    assert poisson_ge(0.0, 1) == pytest.approx(0.0, abs=1e-10)


def test_poisson_ge_k4_lambda3():
    # P(X >= 4) for lambda=3: 1 - P(0)-P(1)-P(2)-P(3)
    lam = 3.0
    cdf3 = math.exp(-lam) * (1 + lam + lam**2 / 2 + lam**3 / 6)
    assert abs(poisson_ge(lam, 4) - (1 - cdf3)) < 1e-10


def test_monte_carlo_top_scorer_sums_to_one():
    lambdas_g = [3.0, 2.0, 1.5, 1.0, 0.5, 0.2]
    lambdas_a = [1.5, 1.0, 0.8, 0.5, 0.3, 0.1]
    results = run_monte_carlo(lambdas_g, lambdas_a, n_sim=20_000, seed=42)
    assert abs(sum(r["p_top_scorer"]   for r in results) - 1.0) < 0.02
    assert abs(sum(r["p_top_assister"] for r in results) - 1.0) < 0.02


def test_monte_carlo_highest_lambda_wins_most():
    lambdas_g = [5.0, 1.0, 0.5]
    lambdas_a = [3.0, 1.0, 0.5]
    results = run_monte_carlo(lambdas_g, lambdas_a, n_sim=20_000, seed=42)
    assert results[0]["p_top_scorer"]   > results[1]["p_top_scorer"]
    assert results[0]["p_top_assister"] > results[1]["p_top_assister"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/pricing/test_wc2026_tournament.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pricing.wc2026_tournament'`

- [ ] **Step 3: Create the pricing engine**

```python
# backend/app/pricing/wc2026_tournament.py
"""WC2026 tournament-level player pricing.

Top-down: team BM (total expected goals for WC2026) is distributed among
lineup players via the existing compute_player_shares / allocate_player
engine (same logic as per-match pricing).

Monte Carlo (numpy) prices top scorer / top assister outrights.
"""
from __future__ import annotations

import logging
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

N_MONTE_CARLO = 50_000
_MC_SEED = 42


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def poisson_ge(lam: float, k: int) -> float:
    """P(X >= k) where X ~ Poisson(lam). Pure math, no scipy dependency."""
    if lam <= 0:
        return 0.0
    cdf = sum(
        math.exp(-lam) * (lam ** j) / math.factorial(j)
        for j in range(k)
    )
    return max(0.0, min(1.0, 1.0 - cdf))


def _fair_odds(p: float) -> float | None:
    return round(1.0 / p, 2) if p > 0.001 else None


def run_monte_carlo(
    lambdas_goals: list[float],
    lambdas_assists: list[float],
    n_sim: int = N_MONTE_CARLO,
    seed: int = _MC_SEED,
) -> list[dict[str, float]]:
    """Simulate n_sim tournaments, return p_top_scorer + p_top_assister per player."""
    rng = np.random.default_rng(seed)
    n = len(lambdas_goals)
    lg = np.array(lambdas_goals, dtype=float)
    la = np.array(lambdas_assists, dtype=float)

    goals_sim   = rng.poisson(lg[:, None], size=(n, n_sim))   # (n_players, n_sim)
    assists_sim = rng.poisson(la[:, None], size=(n, n_sim))

    top_scorer_idx   = goals_sim.argmax(axis=0)    # (n_sim,)
    top_assister_idx = assists_sim.argmax(axis=0)

    results = []
    for i in range(n):
        results.append({
            "p_top_scorer":   float((top_scorer_idx   == i).sum()) / n_sim,
            "p_top_assister": float((top_assister_idx == i).sum()) / n_sim,
        })
    return results


async def compute_tournament_pricing(db: AsyncSession) -> list[dict[str, Any]]:
    """Compute per-player tournament pricing for all 48 WC2026 nations.

    Returns list of dicts ready to bulk-insert into wc2026_player_pricing.
    Nations without a default lineup or Bzzoiro match are skipped (warning logged).
    """
    from app.ingestion.wc2026.team_bm import TEAM_BM, WC2026_NATION_NAME_ALIASES
    from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer
    from app.models.bzzoiro import BzzPlayer
    from app.pricing.assist import ASSIST_GOAL_RATE
    from app.pricing.team_xg import (
        _load_national_team_players,
        allocate_player,
        compute_player_shares,
        detect_penalty_taker,
    )

    computed_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for nation, bm in TEAM_BM.items():
        # 1. Load default lineup
        lineup_res = await db.execute(
            select(WC2026ExpectedLineup).where(
                WC2026ExpectedLineup.nation == nation,
                WC2026ExpectedLineup.context == "default",
            )
        )
        lineup = lineup_res.scalar_one_or_none()
        if lineup is None:
            logger.warning("wc2026_tournament: no default lineup for %s — skipping", nation)
            continue

        lp_res = await db.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            )
        )
        lineup_minutes: dict[str, float] = {
            _norm_name(p.player_name): float(p.expected_minutes)
            for p in lp_res.scalars().all()
        }

        # 2. Resolve nation → Bzzoiro national_team_api_id
        bzzoiro_name = WC2026_NATION_NAME_ALIASES.get(nation, nation)
        nat_id_res = await db.execute(
            select(BzzPlayer.national_team_api_id)
            .where(func.lower(BzzPlayer.national_team_name) == func.lower(bzzoiro_name))
            .where(BzzPlayer.national_team_api_id.isnot(None))
            .limit(1)
        )
        nat_id = nat_id_res.scalar_one_or_none()
        if nat_id is None:
            logger.warning(
                "wc2026_tournament: no Bzzoiro national team for %s (bzzoiro=%r) — skipping",
                nation, bzzoiro_name,
            )
            continue

        # 3. Load Bzzoiro players + override avg_minutes_per_match from lineup
        bzz_players = await _load_national_team_players(db, nat_id)
        matched: list[dict[str, Any]] = []
        for p in bzz_players:
            key = _norm_name(p["player_name"])
            mins = lineup_minutes.get(key)
            if mins is None:
                continue
            p = dict(p)
            p["avg_minutes_per_match"] = mins
            matched.append(p)

        if len(matched) < 5:
            logger.warning(
                "wc2026_tournament: only %d matched players for %s — skipping", len(matched), nation
            )
            continue

        # 4. Compute shares + per-player allocation
        shares = compute_player_shares(matched, nation, lambda_team=bm)
        pen_id = detect_penalty_taker(matched)
        budget_assists = bm * ASSIST_GOAL_RATE

        for share in shares:
            share.is_pen_taker = share.player_id == pen_id
            alloc = allocate_player(share, bm, share.is_pen_taker, budget_assists)
            rows.append({
                "nation":         nation,
                "player_name":    share.player_name,
                "position":       share.position,
                "lambda_goals":   alloc.lambda_total,
                "lambda_assists": alloc.lambda_assist,
                "_player_idx":    len(rows),   # temp — used for Monte Carlo alignment
                "computed_at":    computed_at,
            })

    if not rows:
        return []

    # 5. Monte Carlo across all players simultaneously
    mc_results = run_monte_carlo(
        [r["lambda_goals"]   for r in rows],
        [r["lambda_assists"] for r in rows],
        n_sim=N_MONTE_CARLO,
        seed=_MC_SEED,
    )
    for row, mc in zip(rows, mc_results):
        row["p_top_scorer"]     = mc["p_top_scorer"]
        row["p_top_assister"]   = mc["p_top_assister"]
        row["fair_top_scorer"]  = _fair_odds(mc["p_top_scorer"])
        row["fair_top_assister"]= _fair_odds(mc["p_top_assister"])

    # 6. Compute Poisson cuts
    for row in rows:
        lg = row["lambda_goals"]
        la = row["lambda_assists"]
        for k in range(1, 5):
            p = poisson_ge(lg, k)
            row[f"p_{k}g"]    = p
            row[f"fair_{k}g"] = _fair_odds(p)
        for k in range(1, 4):
            p = poisson_ge(la, k)
            row[f"p_{k}a"]    = p
            row[f"fair_{k}a"] = _fair_odds(p)

        del row["_player_idx"]  # remove temp field

    logger.info("wc2026_tournament: priced %d players across %d nations", len(rows), len(TEAM_BM))
    return rows
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/pricing/test_wc2026_tournament.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/pricing/wc2026_tournament.py backend/tests/pricing/test_wc2026_tournament.py
git commit -m "feat(wc2026): add tournament pricing engine with Monte Carlo"
```

---

## Task 4: API router + registration

**Files:**
- Create: `backend/app/api/wc2026_pricing.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create the router**

```python
# backend/app/api/wc2026_pricing.py
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


# ── Pydantic output schemas ───────────────────────────────────────────────────

class PlayerPricingOut(BaseModel):
    nation: str
    player_name: str
    position: str | None
    lambda_goals: float
    lambda_assists: float
    # cuts — goals
    p_1g: float | None
    p_2g: float | None
    p_3g: float | None
    p_4g: float | None
    fair_1g: float | None
    fair_2g: float | None
    fair_3g: float | None
    fair_4g: float | None
    # cuts — assists
    p_1a: float | None
    p_2a: float | None
    p_3a: float | None
    fair_1a: float | None
    fair_2a: float | None
    fair_3a: float | None
    # outrights
    p_top_scorer: float | None
    p_top_assister: float | None
    fair_top_scorer: float | None
    fair_top_assister: float | None
    # bookmaker comparison (computed at query time)
    bk_top_scorer: float | None = None
    bk_top_assister: float | None = None
    edge_top_scorer: float | None = None
    edge_top_assister: float | None = None


class ComputeResult(BaseModel):
    players_computed: int
    nations_computed: int
    duration_s: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

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

    # Load all relevant bookmaker outrights in two queries (not N+1)
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
            bk_assister[key] = odds   # keep best (highest) odds for bettor

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
```

- [ ] **Step 2: Register the router in main.py**

In `backend/app/main.py`, add after the existing wc2026 imports:

```python
from app.api import wc2026_pricing as wc2026_pricing_api
```

And after line `app.include_router(wc2026_lineups_api.router, ...)`:

```python
app.include_router(wc2026_pricing_api.router, prefix="/api/v1", tags=["wc2026"])
```

- [ ] **Step 3: Verify the router is registered**

```bash
cd backend && python -c "from app.main import app; routes = [r.path for r in app.routes]; print([r for r in routes if 'pricing' in r])"
```
Expected output contains: `['/api/v1/wc2026/pricing/compute', '/api/v1/wc2026/pricing/players']`

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/wc2026_pricing.py backend/app/main.py
git commit -m "feat(wc2026): add tournament pricing API endpoints"
```

---

## Task 5: Frontend API types + functions

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add types and functions**

Append to the end of `frontend/src/lib/api.ts`:

```typescript
// ── WC2026 Tournament Pricing ────────────────────────────────────────────────

export interface WCPlayerPricing {
  nation: string
  player_name: string
  position: string | null
  lambda_goals: number
  lambda_assists: number
  // cuts — goals
  p_1g: number | null
  p_2g: number | null
  p_3g: number | null
  p_4g: number | null
  fair_1g: number | null
  fair_2g: number | null
  fair_3g: number | null
  fair_4g: number | null
  // cuts — assists
  p_1a: number | null
  p_2a: number | null
  p_3a: number | null
  fair_1a: number | null
  fair_2a: number | null
  fair_3a: number | null
  // outrights
  p_top_scorer: number | null
  p_top_assister: number | null
  fair_top_scorer: number | null
  fair_top_assister: number | null
  // bookmaker edge
  bk_top_scorer: number | null
  bk_top_assister: number | null
  edge_top_scorer: number | null
  edge_top_assister: number | null
}

export interface WCComputeResult {
  players_computed: number
  nations_computed: number
  duration_s: number
}

export async function computeWCPricing(): Promise<WCComputeResult> {
  const { data } = await api.post('/api/v1/wc2026/pricing/compute')
  return data
}

export async function getWCPricingPlayers(params?: {
  nation?: string
  position?: string
  min_lambda?: number
}): Promise<WCPlayerPricing[]> {
  const { data } = await api.get('/api/v1/wc2026/pricing/players', { params })
  return data
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(wc2026): add tournament pricing API types and functions"
```

---

## Task 6: PricingTable component

**Files:**
- Create: `frontend/src/components/wc2026/PricingTable.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/wc2026/PricingTable.tsx
'use client'

import { clsx } from 'clsx'
import { type WCPlayerPricing } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

type Mode = 'goals' | 'assists'

interface PricingTableProps {
  players: WCPlayerPricing[]
  mode: Mode
  nationFlags: Record<string, string | null>  // nation → flag_emoji
}

function EdgeBadge({ edge }: { edge: number | null }) {
  if (edge === null) return <span className="text-gray-600">—</span>
  const pct = (edge * 100).toFixed(1)
  return (
    <span className={clsx('font-medium', edge > 0 ? 'text-green-400' : 'text-red-400')}>
      {edge > 0 ? '+' : ''}{pct}%
    </span>
  )
}

function OddsCell({ value }: { value: number | null }) {
  if (!value) return <span className="text-gray-600">—</span>
  return <span>{value.toFixed(2)}</span>
}

export function PricingTable({ players, mode, nationFlags }: PricingTableProps) {
  const isGoals = mode === 'goals'

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-gray-300">
        <thead>
          <tr className="border-b border-gray-700 text-gray-500 uppercase tracking-wider">
            <th className="text-left py-2 px-2 font-medium">Joueur</th>
            <th className="text-left py-2 px-2 font-medium">Nat.</th>
            <th className="text-left py-2 px-2 font-medium">Pos</th>
            <th className="text-right py-2 px-2 font-medium">λ</th>
            <th className="text-right py-2 px-2 font-medium">≥1</th>
            <th className="text-right py-2 px-2 font-medium">≥2</th>
            <th className="text-right py-2 px-2 font-medium">≥3</th>
            {isGoals && <th className="text-right py-2 px-2 font-medium">≥4</th>}
            <th className="text-right py-2 px-2 font-medium">
              {isGoals ? 'Top buteur' : 'Top passeur'}
            </th>
            <th className="text-right py-2 px-2 font-medium">BK</th>
            <th className="text-right py-2 px-2 font-medium">Edge</th>
          </tr>
        </thead>
        <tbody>
          {players.map((p) => {
            const lambda     = isGoals ? p.lambda_goals   : p.lambda_assists
            const cut1       = isGoals ? p.fair_1g        : p.fair_1a
            const cut2       = isGoals ? p.fair_2g        : p.fair_2a
            const cut3       = isGoals ? p.fair_3g        : p.fair_3a
            const cut4       = isGoals ? p.fair_4g        : null
            const fairOut    = isGoals ? p.fair_top_scorer   : p.fair_top_assister
            const bkOut      = isGoals ? p.bk_top_scorer     : p.bk_top_assister
            const edgeOut    = isGoals ? p.edge_top_scorer   : p.edge_top_assister
            const flag       = nationFlags[p.nation]

            return (
              <tr key={`${p.nation}-${p.player_name}`} className="border-b border-gray-800 hover:bg-gray-800/40">
                <td className="py-1.5 px-2 font-medium text-white">{p.player_name}</td>
                <td className="py-1.5 px-2">
                  <span className="flex items-center gap-1">
                    <FlagImg emoji={flag} size={14} />
                    <span className="text-gray-400 text-[10px]">{p.nation}</span>
                  </span>
                </td>
                <td className="py-1.5 px-2 text-gray-500">{p.position ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-orange-300">{lambda.toFixed(2)}</td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut1} /></td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut2} /></td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut3} /></td>
                {isGoals && <td className="py-1.5 px-2 text-right"><OddsCell value={cut4} /></td>}
                <td className="py-1.5 px-2 text-right"><OddsCell value={fairOut} /></td>
                <td className="py-1.5 px-2 text-right text-gray-400"><OddsCell value={bkOut} /></td>
                <td className="py-1.5 px-2 text-right"><EdgeBadge edge={edgeOut} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {players.length === 0 && (
        <p className="text-center text-gray-600 text-sm py-8">Aucune donnée — clique Recalculer</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/wc2026/PricingTable.tsx
git commit -m "feat(wc2026): add PricingTable component"
```

---

## Task 7: Pricing page

**Files:**
- Create: `frontend/src/app/dashboard/wc2026/pricing/page.tsx`

- [ ] **Step 1: Create the page**

```tsx
// frontend/src/app/dashboard/wc2026/pricing/page.tsx
'use client'

import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import {
  type WCPlayerPricing,
  type WCNationStatus,
  computeWCPricing,
  getWCPricingPlayers,
  getWCLineupNations,
} from '@/lib/api'
import { PricingTable } from '@/components/wc2026/PricingTable'

type Tab = 'goals' | 'assists'
type PosFilter = '' | 'FW' | 'MF' | 'DF'

export default function WC2026PricingPage() {
  const [players, setPlayers] = useState<WCPlayerPricing[]>([])
  const [nations, setNations] = useState<WCNationStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)
  const [computeMsg, setComputeMsg] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('goals')
  const [nationFilter, setNationFilter] = useState('')
  const [posFilter, setPosFilter] = useState<PosFilter>('')
  const [minLambda, setMinLambda] = useState('')

  const loadPlayers = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getWCPricingPlayers({
        ...(nationFilter ? { nation: nationFilter } : {}),
        ...(posFilter     ? { position: posFilter }  : {}),
        ...(minLambda && !isNaN(parseFloat(minLambda))
          ? { min_lambda: parseFloat(minLambda) } : {}),
      })
      setPlayers(data)
    } finally {
      setLoading(false)
    }
  }, [nationFilter, posFilter, minLambda])

  useEffect(() => {
    getWCLineupNations().then(setNations)
    loadPlayers()
  }, [loadPlayers])

  async function handleCompute() {
    setComputing(true)
    setComputeMsg(null)
    try {
      const res = await computeWCPricing()
      setComputeMsg(`${res.players_computed} joueurs calculés (${res.duration_s}s)`)
      await loadPlayers()
    } catch {
      setComputeMsg('Erreur de calcul')
    } finally {
      setComputing(false)
    }
  }

  const nationFlags = Object.fromEntries(
    nations.map((n) => [n.nation, n.flag_emoji])
  )

  // Filter displayed players by tab lambda so goals tab is sorted by lambda_goals desc
  const displayed = [...players].sort((a, b) =>
    tab === 'goals'
      ? b.lambda_goals - a.lambda_goals
      : b.lambda_assists - a.lambda_assists
  )

  return (
    <div className="p-4 flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-white">Pricing CDM 2026</h2>

        {/* Filters */}
        <select
          value={nationFilter}
          onChange={(e) => setNationFilter(e.target.value)}
          className="px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
        >
          <option value="">Toutes les nations</option>
          {nations.map((n) => (
            <option key={n.nation} value={n.nation}>{n.nation}</option>
          ))}
        </select>

        <div className="flex gap-1">
          {(['', 'FW', 'MF', 'DF'] as PosFilter[]).map((pos) => (
            <button
              key={pos || 'all'}
              onClick={() => setPosFilter(pos)}
              className={clsx(
                'px-2 py-1 text-xs rounded border transition-colors',
                posFilter === pos
                  ? 'bg-orange-500/20 border-orange-500/50 text-orange-300'
                  : 'border-gray-600 text-gray-400 hover:text-white',
              )}
            >
              {pos || 'Tous'}
            </button>
          ))}
        </div>

        <input
          type="number"
          placeholder="λ min"
          value={minLambda}
          onChange={(e) => setMinLambda(e.target.value)}
          className="w-20 px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
        />

        <div className="ml-auto flex items-center gap-2">
          {computeMsg && <span className="text-xs text-gray-400">{computeMsg}</span>}
          <button
            onClick={handleCompute}
            disabled={computing}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
          >
            <RefreshCw className={clsx('w-3 h-3', computing && 'animate-spin')} />
            {computing ? 'Calcul…' : 'Recalculer'}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-700 pb-0">
        {(['goals', 'assists'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-xs font-medium border-b-2 transition-colors -mb-px',
              tab === t
                ? 'border-orange-500 text-orange-300'
                : 'border-transparent text-gray-400 hover:text-white',
            )}
          >
            {t === 'goals' ? 'Buts' : 'Passes'}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <p className="text-gray-500 text-sm p-4">Chargement…</p>
        ) : (
          <PricingTable players={displayed} mode={tab} nationFlags={nationFlags} />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```
Expected: no errors

- [ ] **Step 3: Deploy and smoke test**

```bash
# On VPS:
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && git pull origin main
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps frontend backend
```

Then navigate to `https://ev0-213-130-144-204.sslip.io/dashboard/wc2026/pricing`.
- Click **Recalculer** — spinner should appear, then "N joueurs calculés (Xs)"
- Switch between **Buts** and **Passes** tabs
- Try nation and position filters

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/dashboard/wc2026/pricing/page.tsx
git commit -m "feat(wc2026): add tournament pricing page"
```
