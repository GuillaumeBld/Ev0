# Market-Anchored Team xG Scraper & Scheduler — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Dixon-Coles team xG and The Odds API match odds with an OddsPortal scraper (primary) + Betclic/Unibet fallback chain, feeding a market-implied (λ_h, λ_a) solver that drives all goalscorer/assist pricing.

**Architecture:** A token-bucket scheduler polls OddsPortal (then Betclic, then Unibet) for H2H + O/U 2.5 + BTTS odds at adaptive intervals, writes to `match_odds_snapshots`, and triggers a 3-constraint Poisson solver (L-BFGS-B) in `MarketXgService`. The recommendation service drops Dixon-Coles entirely and uses `MarketXgService` as its sole xG source.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, Alembic, Playwright (async), httpx, scipy.optimize.minimize, APScheduler, pytest + pytest-asyncio (asyncio_mode=auto).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `app/models/poll_state.py` | `OddsPortalPollState` ORM model |
| Create | `app/models/team_xg.py` | `TeamXgEstimate` ORM model |
| Modify | `app/models/match_odds.py` | Add 4 scraper columns |
| Modify | `app/models/recommendations.py` | Add `xg_source` column |
| Modify | `app/models/__init__.py` | Export new models |
| Create | `alembic/versions/016_market_xg_scraper.py` | Migration for all DB changes |
| Create | `app/ingestion/odds_sanity.py` | Validate odds + compute clean probs |
| Create | `app/ingestion/market_scrape_chain.py` | `ScrapeResult` dataclass + fallback chain |
| Create | `app/ingestion/oddsportal_scraper.py` | Playwright scraper for OddsPortal |
| Create | `app/ingestion/betclic_match_scraper.py` | HTTP scraper for Betclic H2H/OU/BTTS |
| Create | `app/ingestion/unibet_match_scraper.py` | HTTP scraper for Unibet H2H/OU/BTTS |
| Modify | `app/services/market_xg.py` | BTTS solver, staleness fix, source preference |
| Create | `app/services/market_scrape_scheduler.py` | Token-bucket tick scheduler |
| Modify | `app/services/recommendation_service.py` | Remove Dixon-Coles, use MarketXgService |
| Modify | `app/worker.py` | Add tick job, remove Odds API match odds job |
| Create | `app/scripts/seed_poll_state.py` | Pre-gameweek CSV seed for poll_state |
| Modify | `app/api/recommendations.py` | Add `xg_source` to response schema |
| Modify | `frontend/src/app/dashboard/recommendations/page.tsx` | xG source badge |
| Create | `tests/ingestion/test_odds_sanity.py` | Sanity checks tests |
| Create | `tests/ingestion/test_market_scrape_chain.py` | Chain + storage tests |
| Create | `tests/services/test_market_xg_enhanced.py` | Solver tests |
| Create | `tests/services/test_market_scrape_scheduler.py` | Scheduler logic tests |

---

## Task 1: New ORM Models

**Files:**
- Create: `backend/app/models/poll_state.py`
- Create: `backend/app/models/team_xg.py`

- [ ] **Step 1: Create `app/models/poll_state.py`**

```python
"""OddsPortal per-fixture polling state."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OddsPortalPollState(Base, TimestampMixin):
    __tablename__ = "oddsportal_poll_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fixtures.id"), nullable=False, unique=True, index=True
    )
    oddsportal_url: Mapped[str] = mapped_column(String(500), nullable=False)
    betclic_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unibet_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_due_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_scraped_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stopped_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

- [ ] **Step 2: Create `app/models/team_xg.py`**

```python
"""Market-implied team xG estimates (append-only time series)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TeamXgEstimate(Base):
    __tablename__ = "team_xg_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fixtures.id"), nullable=False, index=True
    )
    as_of_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lambda_home: Mapped[float] = mapped_column(Float, nullable=False)
    lambda_away: Mapped[float] = mapped_column(Float, nullable=False)
    fit_residual: Mapped[float] = mapped_column(Float, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_source: Mapped[str] = mapped_column(String(20), nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_snapshot_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
```

- [ ] **Step 3: Update `app/models/__init__.py` — add new models**

Add these two lines to the imports block:
```python
from app.models.poll_state import OddsPortalPollState
from app.models.team_xg import TeamXgEstimate
```

Add to `__all__`:
```python
"OddsPortalPollState",
"TeamXgEstimate",
```

- [ ] **Step 4: Commit**

```bash
cd /Users/yohan.resin/Ev0/backend
git add app/models/poll_state.py app/models/team_xg.py app/models/__init__.py
git commit -m "feat: add OddsPortalPollState and TeamXgEstimate models"
```

---

## Task 2: Extend Existing Models

**Files:**
- Modify: `backend/app/models/match_odds.py`
- Modify: `backend/app/models/recommendations.py`

- [ ] **Step 1: Add 4 columns to `MatchOddsSnapshot` in `app/models/match_odds.py`**

After the `snapshot_utc` column definition, add:
```python
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parse_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

Also add the import at the top of the file:
```python
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
```

- [ ] **Step 2: Add `xg_source` to `Recommendation` in `app/models/recommendations.py`**

After the `settled_utc` column definition, add:
```python
    xg_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

- [ ] **Step 3: Commit**

```bash
git add app/models/match_odds.py app/models/recommendations.py
git commit -m "feat: add source/fallback columns to match_odds_snapshots and xg_source to recommendations"
```

---

## Task 3: Alembic Migration 016

**Files:**
- Create: `backend/alembic/versions/016_market_xg_scraper.py`

- [ ] **Step 1: Create the migration file**

```python
"""Market xG scraper: extend match_odds_snapshots, add poll_state and team_xg_estimates

Revision ID: 016
Revises: 015
Create Date: 2026-04-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Extend match_odds_snapshots
    op.add_column(
        "match_odds_snapshots",
        sa.Column("source", sa.String(20), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("source_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("parse_version", sa.String(20), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Add xg_source to recommendations
    op.add_column(
        "recommendations",
        sa.Column("xg_source", sa.String(20), nullable=True),
    )

    # 3. Create oddsportal_poll_state
    op.create_table(
        "oddsportal_poll_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
        ),
        sa.Column("oddsportal_url", sa.String(500), nullable=False),
        sa.Column("betclic_url", sa.String(500), nullable=True),
        sa.Column("unibet_url", sa.String(500), nullable=True),
        sa.Column("next_due_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_scraped_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stopped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("stopped_reason", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", name="uq_poll_state_fixture"),
    )
    op.create_index("ix_poll_state_fixture_id", "oddsportal_poll_state", ["fixture_id"])
    op.create_index(
        "ix_poll_state_next_due", "oddsportal_poll_state", ["next_due_at_utc"]
    )

    # 4. Create team_xg_estimates
    op.create_table(
        "team_xg_estimates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
        ),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lambda_home", sa.Float(), nullable=False),
        sa.Column("lambda_away", sa.Float(), nullable=False),
        sa.Column("fit_residual", sa.Float(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("data_source", sa.String(20), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "input_snapshot_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_xg_fixture_time",
        "team_xg_estimates",
        ["fixture_id", sa.text("as_of_utc DESC")],
    )


def downgrade() -> None:
    op.drop_table("team_xg_estimates")
    op.drop_table("oddsportal_poll_state")
    op.drop_column("recommendations", "xg_source")
    op.drop_column("match_odds_snapshots", "fallback_used")
    op.drop_column("match_odds_snapshots", "parse_version")
    op.drop_column("match_odds_snapshots", "source_url")
    op.drop_column("match_odds_snapshots", "source")
```

- [ ] **Step 2: Apply migration locally**

```bash
cd /Users/yohan.resin/Ev0/backend
alembic upgrade head
```

Expected: `Running upgrade 015 -> 016, Market xG scraper...`

- [ ] **Step 3: Verify tables exist**

```bash
alembic current
# Expected: 016 (head)
```

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/016_market_xg_scraper.py
git commit -m "feat: migration 016 — oddsportal_poll_state, team_xg_estimates, extend match_odds_snapshots"
```

---

## Task 4: Sanity Checks Module (TDD)

**Files:**
- Create: `backend/app/ingestion/odds_sanity.py`
- Create: `backend/tests/ingestion/test_odds_sanity.py`

- [ ] **Step 1: Write failing tests**

Create `tests/ingestion/test_odds_sanity.py`:

```python
"""Tests for odds sanity checks and clean prob computation."""

import pytest

from app.ingestion.odds_sanity import compute_clean_probs, validate_market


class TestValidateMarket:
    def test_valid_h2x(self):
        assert validate_market("h2h", {"home": 2.05, "draw": 3.30, "away": 3.80}) is True

    def test_valid_totals(self):
        assert validate_market("totals", {"over_2.5": 1.95, "under_2.5": 1.95}) is True

    def test_valid_btts(self):
        assert validate_market("btts", {"yes": 1.80, "no": 1.95}) is True

    def test_rejects_odds_below_1_01(self):
        assert validate_market("h2h", {"home": 1.00, "draw": 3.30, "away": 3.80}) is False

    def test_rejects_missing_selection_h2x(self):
        # Missing "draw"
        assert validate_market("h2h", {"home": 2.05, "away": 3.80}) is False

    def test_rejects_missing_selection_totals(self):
        assert validate_market("totals", {"over_2.5": 1.95}) is False

    def test_rejects_none_value(self):
        assert validate_market("btts", {"yes": None, "no": 1.95}) is False

    def test_rejects_nan_value(self):
        import math
        assert validate_market("btts", {"yes": math.nan, "no": 1.95}) is False

    def test_rejects_absurd_margin(self):
        # Sum of implied probs = 3.0 (way too high)
        assert validate_market("h2h", {"home": 1.20, "draw": 1.20, "away": 1.20}) is False

    def test_rejects_unknown_market_type(self):
        assert validate_market("unknown", {"foo": 2.0}) is False


class TestComputeCleanProbs:
    def test_h2x_sums_to_one(self):
        result = compute_clean_probs({"home": 2.05, "draw": 3.30, "away": 3.80})
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_preserves_keys(self):
        odds = {"home": 2.05, "draw": 3.30, "away": 3.80}
        result = compute_clean_probs(odds)
        assert set(result.keys()) == {"home", "draw", "away"}

    def test_higher_odds_lower_prob(self):
        result = compute_clean_probs({"home": 1.50, "away": 4.00})
        assert result["home"] > result["away"]

    def test_even_odds_equal_probs(self):
        result = compute_clean_probs({"yes": 2.00, "no": 2.00})
        assert abs(result["yes"] - 0.5) < 1e-9
        assert abs(result["no"] - 0.5) < 1e-9
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd /Users/yohan.resin/Ev0/backend
python -m pytest tests/ingestion/test_odds_sanity.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.odds_sanity'`

- [ ] **Step 3: Implement `app/ingestion/odds_sanity.py`**

```python
"""Sanity checks and clean probability computation for scraped odds."""

import math

_EXPECTED_SELECTIONS: dict[str, set[str]] = {
    "h2h": {"home", "draw", "away"},
    "totals": {"over_2.5", "under_2.5"},
    "btts": {"yes", "no"},
}

_MIN_ODDS = 1.01
_MAX_MARGIN = 1.50  # sum of implied probs ceiling (50% overround = absurd)
_MIN_MARGIN = 1.00  # sum of implied probs floor


def validate_market(market_type: str, outcomes: dict[str, float | None]) -> bool:
    """
    Return True iff odds dict is valid for use in xG inference.

    Checks:
    - market_type is known
    - all expected selections present
    - all odds > 1.01
    - no NaN / None values
    - sum(1/odds) in [1.0, 1.50]
    """
    expected = _EXPECTED_SELECTIONS.get(market_type)
    if expected is None:
        return False

    if set(outcomes.keys()) != expected:
        return False

    total_implied = 0.0
    for odds in outcomes.values():
        if odds is None:
            return False
        if isinstance(odds, float) and math.isnan(odds):
            return False
        if odds < _MIN_ODDS:
            return False
        total_implied += 1.0 / odds

    return _MIN_MARGIN <= total_implied <= _MAX_MARGIN


def compute_clean_probs(outcomes: dict[str, float]) -> dict[str, float]:
    """
    Normalise raw odds to sum-to-one probabilities (simple margin removal).

    p_implied[i] = 1 / odds[i]
    p_clean[i]   = p_implied[i] / sum(p_implied)
    """
    implied = {k: 1.0 / v for k, v in outcomes.items()}
    total = sum(implied.values())
    return {k: v / total for k, v in implied.items()}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/ingestion/test_odds_sanity.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/odds_sanity.py tests/ingestion/test_odds_sanity.py
git commit -m "feat: odds sanity checks and clean prob normalisation"
```

---

## Task 5: ScrapeResult Dataclass + Chain Skeleton

**Files:**
- Create: `backend/app/ingestion/market_scrape_chain.py`

- [ ] **Step 1: Create `app/ingestion/market_scrape_chain.py`** (skeleton — chain logic completed in Task 9)

```python
"""Market scrape fallback chain: OddsPortal → Betclic → Unibet.

Exported:
    ScrapeResult — dataclass returned by each scraper
    run_scrape_chain — attempt sources in order, return first success
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ScrapeResult:
    """Immutable snapshot of all 3 markets from one source visit."""

    source: str
    """'oddsportal' | 'betclic' | 'unibet'"""

    source_url: str
    parse_version: str

    h2h: dict[str, float] | None
    """{'home': float, 'draw': float, 'away': float} or None if market missing/invalid."""

    totals: dict[str, float] | None
    """{'over_2.5': float, 'under_2.5': float} or None."""

    btts: dict[str, float] | None
    """{'yes': float, 'no': float} or None."""

    ingested_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fallback_used: bool = False
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        """All 3 markets present (required for xG inference)."""
        return self.h2h is not None and self.totals is not None and self.btts is not None
```

- [ ] **Step 2: Commit**

```bash
git add app/ingestion/market_scrape_chain.py
git commit -m "feat: ScrapeResult dataclass"
```

---

## Task 6: OddsPortal Scraper (Playwright)

**Files:**
- Create: `backend/app/ingestion/oddsportal_scraper.py`

OddsPortal is a React SPA — requires Playwright to render JavaScript. The odds structure must be inspected on the live site before selectors can be finalised.

- [ ] **Step 1: Inspect OddsPortal match page to map selectors**

Open any OddsPortal match URL (e.g. `https://www.oddsportal.com/football/france/ligue-1/<slug>/`) in a Playwright-driven browser or manually in Chrome DevTools.

Identify and record:
- CSS selector for H2H odds table rows (home/draw/away odds)
- How to navigate to the "Over/Under" tab and extract O/U 2.5 row
- How to navigate to the "Both Teams to Score" tab and extract yes/no odds
- The `parse_version` string to use (e.g. `"op-v1"`)

Document the selectors at the top of `oddsportal_scraper.py` as constants.

- [ ] **Step 2: Create `app/ingestion/oddsportal_scraper.py`**

```python
"""OddsPortal Playwright scraper — extracts H2H, O/U 2.5, BTTS match odds.

Selectors (verify against live site and update as needed):
    _SEL_H2H_HOME   — best home odds cell
    _SEL_H2H_DRAW   — best draw odds cell
    _SEL_H2H_AWAY   — best away odds cell
    _SEL_TAB_OU     — "Over/Under" tab button
    _SEL_OU_OVER    — Over 2.5 best odds cell
    _SEL_OU_UNDER   — Under 2.5 best odds cell
    _SEL_TAB_BTTS   — "Both Teams to Score" tab button
    _SEL_BTTS_YES   — BTTS Yes best odds cell
    _SEL_BTTS_NO    — BTTS No best odds cell
"""

import logging
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "op-v1"

# --- Selectors (update after live site inspection) ---
_SEL_H2H_HOME = "[data-testid='1x2-home-odds']"   # placeholder — update after inspection
_SEL_H2H_DRAW = "[data-testid='1x2-draw-odds']"
_SEL_H2H_AWAY = "[data-testid='1x2-away-odds']"
_SEL_TAB_OU = "[data-testid='tab-over-under']"
_SEL_OU_ROW_25 = "[data-testid='ou-row-2.5']"     # row containing over/under 2.5
_SEL_TAB_BTTS = "[data-testid='tab-btts']"
_SEL_BTTS_YES = "[data-testid='btts-yes-odds']"
_SEL_BTTS_NO = "[data-testid='btts-no-odds']"

_TIMEOUT_MS = 8_000


async def _parse_float(page: Page, selector: str) -> float | None:
    """Extract and parse a float from a page element. Returns None on failure."""
    try:
        el = await page.wait_for_selector(selector, timeout=_TIMEOUT_MS)
        if el is None:
            return None
        text = (await el.text_content() or "").strip()
        return float(text)
    except (PlaywrightTimeout, ValueError):
        return None


async def scrape_match_markets(url: str, page: Page) -> ScrapeResult:
    """
    Navigate to an OddsPortal match page and extract H2H, O/U 2.5, BTTS.

    Returns ScrapeResult with error set if critical failure occurs.
    All 3 markets must pass sanity checks for is_complete to be True.
    """
    ingested_at = datetime.now(timezone.utc)

    try:
        await page.goto(url, wait_until="networkidle", timeout=20_000)
    except PlaywrightTimeout:
        logger.warning("oddsportal: timeout navigating to %s", url)
        return ScrapeResult(
            source="oddsportal",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None,
            totals=None,
            btts=None,
            ingested_at_utc=ingested_at,
            error="navigation_timeout",
        )

    # --- H2H ---
    home = await _parse_float(page, _SEL_H2H_HOME)
    draw = await _parse_float(page, _SEL_H2H_DRAW)
    away = await _parse_float(page, _SEL_H2H_AWAY)
    h2h_raw = {"home": home, "draw": draw, "away": away}
    h2h = h2h_raw if (home and draw and away and validate_market("h2h", h2h_raw)) else None

    # --- O/U 2.5 ---
    totals = None
    try:
        await page.click(_SEL_TAB_OU, timeout=_TIMEOUT_MS)
        over = await _parse_float(page, f"{_SEL_OU_ROW_25} [data-over]")
        under = await _parse_float(page, f"{_SEL_OU_ROW_25} [data-under]")
        totals_raw = {"over_2.5": over, "under_2.5": under}
        if over and under and validate_market("totals", totals_raw):
            totals = totals_raw
    except PlaywrightTimeout:
        logger.debug("oddsportal: OU tab timeout for %s", url)

    # --- BTTS ---
    btts = None
    try:
        await page.click(_SEL_TAB_BTTS, timeout=_TIMEOUT_MS)
        yes = await _parse_float(page, _SEL_BTTS_YES)
        no = await _parse_float(page, _SEL_BTTS_NO)
        btts_raw = {"yes": yes, "no": no}
        if yes and no and validate_market("btts", btts_raw):
            btts = btts_raw
    except PlaywrightTimeout:
        logger.debug("oddsportal: BTTS tab timeout for %s", url)

    return ScrapeResult(
        source="oddsportal",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=h2h,
        totals=totals,
        btts=btts,
        ingested_at_utc=ingested_at,
    )
```

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/oddsportal_scraper.py
git commit -m "feat: OddsPortal Playwright scraper scaffold (selectors require live inspection)"
```

---

## Task 7: Betclic Match Scraper (H2H + O/U + BTTS)

**Files:**
- Create: `backend/app/ingestion/betclic_match_scraper.py`

The existing `betclic_scraper.py` already parses the SSR `ng-state` JSON and extracts 1X2. This scraper extends that approach for O/U 2.5 and BTTS from the same payload (no Playwright needed).

- [ ] **Step 1: Inspect Betclic match page SSR JSON to identify O/U and BTTS keys**

Fetch any Betclic match URL manually. In the `ng-state` JSON, find:
- The key/path for Over/Under 2.5 market (look for `overUnder`, `totalGoals`, or similar)
- The key/path for Both Teams to Score (look for `bothTeamsToScore`, `btts`, or similar)
- Note the `parse_version` (e.g. `"bc-v1"`)

Document the paths at the top of `betclic_match_scraper.py`.

- [ ] **Step 2: Create `app/ingestion/betclic_match_scraper.py`**

```python
"""Betclic match-level odds scraper — H2H, O/U 2.5, BTTS via SSR JSON.

Extends the pattern from betclic_scraper.py (ng-state SSR payload).
Does NOT require Playwright — pure HTTP + JSON parsing.

Parse version: "bc-v1" — update when Betclic changes their SSR structure.
"""

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "bc-v1"
_NG_STATE_RE = re.compile(r'<script id="ng-state"[^>]*>(.*?)</script>', re.DOTALL)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _extract_ng_state(html: str) -> dict | None:
    m = _NG_STATE_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _find_market(ng_state: dict, market_key: str) -> dict | None:
    """
    Traverse ng_state to find a market by key pattern.
    Betclic SSR structure: update paths after live inspection.
    """
    # TODO after live inspection: replace with actual path traversal
    # Example structure (update based on observed JSON):
    #   ng_state["seoDataMap"][...]["event"]["markets"][i]["label"] == market_key
    return None  # implemented after live site inspection


def _parse_h2h(ng_state: dict) -> dict[str, float] | None:
    """Extract 1X2 home/draw/away odds from ng_state."""
    market = _find_market(ng_state, "1X2")
    if not market:
        return None
    try:
        selections = market["selections"]
        result = {}
        for sel in selections:
            label = sel["label"].lower()
            odds = float(sel["odds"])
            if "1" in label or "home" in label:
                result["home"] = odds
            elif "nul" in label or "draw" in label or "x" in label:
                result["draw"] = odds
            elif "2" in label or "away" in label:
                result["away"] = odds
        return result if validate_market("h2h", result) else None
    except (KeyError, TypeError, ValueError):
        return None


def _parse_totals(ng_state: dict) -> dict[str, float] | None:
    """Extract Over/Under 2.5 odds."""
    market = _find_market(ng_state, "Over/Under 2.5")
    if not market:
        return None
    try:
        selections = market["selections"]
        result = {}
        for sel in selections:
            label = sel["label"].lower()
            odds = float(sel["odds"])
            if "over" in label or "plus" in label:
                result["over_2.5"] = odds
            elif "under" in label or "moins" in label:
                result["under_2.5"] = odds
        return result if validate_market("totals", result) else None
    except (KeyError, TypeError, ValueError):
        return None


def _parse_btts(ng_state: dict) -> dict[str, float] | None:
    """Extract Both Teams to Score yes/no odds."""
    market = _find_market(ng_state, "Les deux équipes marquent")
    if not market:
        return None
    try:
        selections = market["selections"]
        result = {}
        for sel in selections:
            label = sel["label"].lower()
            odds = float(sel["odds"])
            if "oui" in label or "yes" in label:
                result["yes"] = odds
            elif "non" in label or "no" in label:
                result["no"] = odds
        return result if validate_market("btts", result) else None
    except (KeyError, TypeError, ValueError):
        return None


async def scrape_match_markets(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    """Fetch Betclic match page and extract H2H, O/U 2.5, BTTS."""
    ingested_at = datetime.now(timezone.utc)

    try:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("betclic_match: HTTP error for %s: %s", url, exc)
        return ScrapeResult(
            source="betclic",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None,
            totals=None,
            btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error=f"http_error:{type(exc).__name__}",
        )

    ng_state = _extract_ng_state(resp.text)
    if ng_state is None:
        return ScrapeResult(
            source="betclic",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None,
            totals=None,
            btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error="ng_state_not_found",
        )

    return ScrapeResult(
        source="betclic",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=_parse_h2h(ng_state),
        totals=_parse_totals(ng_state),
        btts=_parse_btts(ng_state),
        ingested_at_utc=ingested_at,
        fallback_used=True,
    )
```

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/betclic_match_scraper.py
git commit -m "feat: Betclic match scraper scaffold (ng-state paths require live inspection)"
```

---

## Task 8: Unibet Match Scraper (H2H + O/U + BTTS)

**Files:**
- Create: `backend/app/ingestion/unibet_match_scraper.py`

The existing `unibet_lvs_scraper.py` uses LVS API with market type IDs. H2H, O/U, and BTTS have their own IDs — look them up from the API and add them.

- [ ] **Step 1: Identify Unibet LVS market type IDs for H2H, O/U 2.5, BTTS**

Make a test call to the LVS API for a known match:
```bash
curl "https://eu-offering-api.kambicdn.com/offering/v2018/ub/event/<event_id>/betoffers.json?lang=fr_FR&market=FR" | python -m json.tool | grep -i "marketType"
```

Find the `markettypeId` values for:
- Match Winner (H2H): typically `1` or `2`
- Over/Under 2.5 Goals: typically `18` or similar
- Both Teams to Score: typically `500` or similar

Document the IDs as constants in `unibet_match_scraper.py`.

- [ ] **Step 2: Create `app/ingestion/unibet_match_scraper.py`**

```python
"""Unibet match-level odds scraper — H2H, O/U 2.5, BTTS via LVS API.

Extends the pattern from unibet_lvs_scraper.py (LVS bet offers API).
Does NOT require Playwright — pure HTTP + JSON parsing.

Market type IDs (update after live API inspection):
    _MT_H2H    — Match Winner (1X2)
    _MT_OU_25  — Over/Under 2.5 Goals
    _MT_BTTS   — Both Teams to Score
"""

import logging
from datetime import datetime, timezone

import httpx

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "ub-v1"

# Market type IDs — update after live API inspection
_MT_H2H: int = 2          # Match Winner 1X2 (update if wrong)
_MT_OU_25: int = 18       # Over/Under Goals (update if wrong)
_MT_BTTS: int = 6         # Both Teams to Score (update if wrong)

_BASE_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/ub"
_HEADERS = {"Accept": "application/json", "Accept-Language": "fr-FR"}


async def _fetch_betoffers(event_id: int, client: httpx.AsyncClient) -> list[dict]:
    url = f"{_BASE_URL}/event/{event_id}/betoffers.json"
    params = {"lang": "fr_FR", "market": "FR", "includeParticipants": "true"}
    resp = await client.get(url, params=params, headers=_HEADERS, timeout=10.0)
    resp.raise_for_status()
    return resp.json().get("betOffers", [])


def _extract_market(betoffers: list[dict], market_type_id: int) -> list[dict] | None:
    for bo in betoffers:
        if bo.get("marketTypeId") == market_type_id and bo.get("closed") is False:
            return bo.get("outcomes", [])
    return None


def _parse_h2h(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market(betoffers, _MT_H2H)
    if not outcomes:
        return None
    try:
        result: dict[str, float] = {}
        for o in outcomes:
            label = o.get("label", "").lower()
            odds = o.get("odds", 0) / 1000.0  # LVS stores odds × 1000
            if "1" in label or "home" in label:
                result["home"] = odds
            elif "x" in label or "draw" in label:
                result["draw"] = odds
            elif "2" in label or "away" in label:
                result["away"] = odds
        return result if validate_market("h2h", result) else None
    except (KeyError, TypeError, ValueError):
        return None


def _parse_totals(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market(betoffers, _MT_OU_25)
    if not outcomes:
        return None
    try:
        result: dict[str, float] = {}
        for o in outcomes:
            label = o.get("label", "").lower()
            line = o.get("line", "")
            # Only process 2.5 line
            if "2.5" not in str(line):
                continue
            odds = o.get("odds", 0) / 1000.0
            if "over" in label or "+" in label:
                result["over_2.5"] = odds
            elif "under" in label or "-" in label:
                result["under_2.5"] = odds
        return result if validate_market("totals", result) else None
    except (KeyError, TypeError, ValueError):
        return None


def _parse_btts(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market(betoffers, _MT_BTTS)
    if not outcomes:
        return None
    try:
        result: dict[str, float] = {}
        for o in outcomes:
            label = o.get("label", "").lower()
            odds = o.get("odds", 0) / 1000.0
            if "oui" in label or "yes" in label:
                result["yes"] = odds
            elif "non" in label or "no" in label:
                result["no"] = odds
        return result if validate_market("btts", result) else None
    except (KeyError, TypeError, ValueError):
        return None


async def scrape_match_markets_by_event_id(
    event_id: int, url: str, client: httpx.AsyncClient
) -> ScrapeResult:
    """Fetch Unibet LVS bet offers for event_id and extract H2H, O/U 2.5, BTTS."""
    ingested_at = datetime.now(timezone.utc)

    try:
        betoffers = await _fetch_betoffers(event_id, client)
    except httpx.HTTPError as exc:
        logger.warning("unibet_match: HTTP error for event %s: %s", event_id, exc)
        return ScrapeResult(
            source="unibet",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None,
            totals=None,
            btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error=f"http_error:{type(exc).__name__}",
        )

    return ScrapeResult(
        source="unibet",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=_parse_h2h(betoffers),
        totals=_parse_totals(betoffers),
        btts=_parse_btts(betoffers),
        ingested_at_utc=ingested_at,
        fallback_used=True,
    )
```

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/unibet_match_scraper.py
git commit -m "feat: Unibet match scraper scaffold (market type IDs require live API inspection)"
```

---

## Task 9: Fallback Chain + Storage

**Files:**
- Modify: `backend/app/ingestion/market_scrape_chain.py`
- Create: `backend/tests/ingestion/test_market_scrape_chain.py`

- [ ] **Step 1: Write failing tests**

Create `tests/ingestion/test_market_scrape_chain.py`:

```python
"""Tests for the scrape fallback chain."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.market_scrape_chain import ScrapeResult, run_scrape_chain


def _make_result(source: str, complete: bool = True, fallback: bool = False) -> ScrapeResult:
    return ScrapeResult(
        source=source,
        source_url=f"https://{source}.com/match",
        parse_version=f"{source[:2]}-v1",
        h2h={"home": 2.05, "draw": 3.30, "away": 3.80} if complete else None,
        totals={"over_2.5": 1.95, "under_2.5": 1.95} if complete else None,
        btts={"yes": 1.80, "no": 1.95} if complete else None,
        ingested_at_utc=datetime.now(timezone.utc),
        fallback_used=fallback,
    )


def _make_poll_state(
    op_url: str = "https://oddsportal.com/match",
    bc_url: str | None = "https://betclic.fr/match",
    ub_url: str | None = "https://unibet.fr/match",
) -> MagicMock:
    state = MagicMock()
    state.fixture_id = 42
    state.oddsportal_url = op_url
    state.betclic_url = bc_url
    state.unibet_url = ub_url
    return state


class TestRunScrapeChain:
    async def test_returns_oddsportal_on_success(self):
        poll_state = _make_poll_state()
        op_result = _make_result("oddsportal")

        with patch(
            "app.ingestion.market_scrape_chain.scrape_oddsportal",
            new=AsyncMock(return_value=op_result),
        ):
            result = await run_scrape_chain(poll_state, browser=MagicMock(), http_client=MagicMock())

        assert result is not None
        assert result.source == "oddsportal"
        assert result.fallback_used is False

    async def test_falls_back_to_betclic_on_oddsportal_failure(self):
        poll_state = _make_poll_state()
        failed = _make_result("oddsportal", complete=False)
        bc_result = _make_result("betclic", fallback=True)

        with (
            patch("app.ingestion.market_scrape_chain.scrape_oddsportal", new=AsyncMock(return_value=failed)),
            patch("app.ingestion.market_scrape_chain.scrape_betclic", new=AsyncMock(return_value=bc_result)),
        ):
            result = await run_scrape_chain(poll_state, browser=MagicMock(), http_client=MagicMock())

        assert result is not None
        assert result.source == "betclic"
        assert result.fallback_used is True

    async def test_returns_none_when_all_fail(self):
        poll_state = _make_poll_state()
        fail = lambda src: _make_result(src, complete=False)

        with (
            patch("app.ingestion.market_scrape_chain.scrape_oddsportal", new=AsyncMock(return_value=fail("oddsportal"))),
            patch("app.ingestion.market_scrape_chain.scrape_betclic", new=AsyncMock(return_value=fail("betclic"))),
            patch("app.ingestion.market_scrape_chain.scrape_unibet", new=AsyncMock(return_value=fail("unibet"))),
        ):
            result = await run_scrape_chain(poll_state, browser=MagicMock(), http_client=MagicMock())

        assert result is None

    async def test_skips_betclic_when_no_url(self):
        poll_state = _make_poll_state(bc_url=None, ub_url=None)
        failed_op = _make_result("oddsportal", complete=False)

        with patch("app.ingestion.market_scrape_chain.scrape_oddsportal", new=AsyncMock(return_value=failed_op)):
            result = await run_scrape_chain(poll_state, browser=MagicMock(), http_client=MagicMock())

        assert result is None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python -m pytest tests/ingestion/test_market_scrape_chain.py -v
```

Expected: `ImportError: cannot import name 'run_scrape_chain'`

- [ ] **Step 3: Complete `app/ingestion/market_scrape_chain.py` with chain logic and DB storage**

```python
"""Market scrape fallback chain: OddsPortal → Betclic → Unibet."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from playwright.async_api import Browser
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.market_scrape_chain import ScrapeResult  # self-import ok (same file after merge)

logger = logging.getLogger(__name__)
```

Replace the full file with:

```python
"""Market scrape fallback chain: OddsPortal → Betclic → Unibet.

Exported:
    ScrapeResult — dataclass returned by each scraper
    run_scrape_chain — attempt sources in order, return first success
    store_scrape_result — write 7 MatchOddsSnapshot rows to DB
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from playwright.async_api import Browser
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    source: str
    source_url: str
    parse_version: str
    h2h: dict[str, float] | None
    totals: dict[str, float] | None
    btts: dict[str, float] | None
    ingested_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fallback_used: bool = False
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.h2h is not None and self.totals is not None and self.btts is not None


async def scrape_oddsportal(url: str, browser: Browser) -> ScrapeResult:
    from app.ingestion.oddsportal_scraper import scrape_match_markets
    page = await browser.new_page()
    try:
        return await scrape_match_markets(url, page)
    finally:
        await page.close()


async def scrape_betclic(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    from app.ingestion.betclic_match_scraper import scrape_match_markets
    return await scrape_match_markets(url, client)


async def scrape_unibet(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    from app.ingestion.unibet_match_scraper import scrape_match_markets_by_event_id
    # unibet_url encodes the event ID: extract it or pass whole URL
    # Convention: unibet_url = "https://www.unibet.fr/sport/football/.../event/<event_id>"
    try:
        event_id = int(url.rstrip("/").split("/")[-1])
    except ValueError:
        from app.ingestion.market_scrape_chain import ScrapeResult as SR
        return SR(
            source="unibet", source_url=url, parse_version="ub-v1",
            h2h=None, totals=None, btts=None, fallback_used=True, error="invalid_event_id"
        )
    return await scrape_match_markets_by_event_id(event_id, url, client)


async def run_scrape_chain(
    poll_state,
    browser: Browser,
    http_client: httpx.AsyncClient,
) -> ScrapeResult | None:
    """
    Try OddsPortal → Betclic → Unibet in order.
    Return first ScrapeResult where is_complete is True, or None if all fail.
    """
    # 1. OddsPortal (primary)
    result = await scrape_oddsportal(poll_state.oddsportal_url, browser)
    if result.is_complete:
        logger.info(
            "chain: fixture=%s source=oddsportal success",
            poll_state.fixture_id,
        )
        return result
    logger.debug("chain: fixture=%s oddsportal incomplete (%s)", poll_state.fixture_id, result.error)

    # 2. Betclic fallback
    if poll_state.betclic_url:
        result = await scrape_betclic(poll_state.betclic_url, http_client)
        if result.is_complete:
            logger.info(
                "chain: fixture=%s source=betclic fallback_used=True",
                poll_state.fixture_id,
            )
            return result
        logger.debug("chain: fixture=%s betclic incomplete (%s)", poll_state.fixture_id, result.error)

    # 3. Unibet fallback
    if poll_state.unibet_url:
        result = await scrape_unibet(poll_state.unibet_url, http_client)
        if result.is_complete:
            logger.info(
                "chain: fixture=%s source=unibet fallback_used=True",
                poll_state.fixture_id,
            )
            return result
        logger.debug("chain: fixture=%s unibet incomplete (%s)", poll_state.fixture_id, result.error)

    logger.warning(
        "chain: fixture=%s all sources failed sources_tried=[oddsportal,betclic,unibet]",
        poll_state.fixture_id,
    )
    return None


async def store_scrape_result(
    result: ScrapeResult,
    fixture_id: int,
    session: AsyncSession,
) -> list[int]:
    """
    Write 7 MatchOddsSnapshot rows (3 h2h + 2 totals + 2 btts) to DB.
    Returns list of inserted row IDs.

    Uses INSERT ... ON CONFLICT DO NOTHING to avoid duplicates on re-scrape.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.match_odds import MatchOddsSnapshot

    rows = []
    markets = {
        "h2h": result.h2h or {},
        "totals": result.totals or {},
        "btts": result.btts or {},
    }

    for market_type, outcomes in markets.items():
        for outcome, odds in outcomes.items():
            rows.append({
                "fixture_id": fixture_id,
                "bookmaker": result.source,
                "market_type": market_type,
                "outcome": outcome,
                "odds": odds,
                "snapshot_utc": result.ingested_at_utc,
                "source": result.source,
                "source_url": result.source_url,
                "parse_version": result.parse_version,
                "fallback_used": result.fallback_used,
            })

    if not rows:
        return []

    stmt = pg_insert(MatchOddsSnapshot).values(rows).on_conflict_do_nothing(
        constraint="uq_match_odds_snapshot"
    )
    cursor = await session.execute(stmt)
    await session.commit()

    # Return inserted IDs (approximate — not guaranteed by on_conflict_do_nothing)
    # Caller fetches actual IDs for input_snapshot_ids if needed
    return list(range(cursor.rowcount))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ingestion/test_market_scrape_chain.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/market_scrape_chain.py tests/ingestion/test_market_scrape_chain.py
git commit -m "feat: fallback chain orchestrator and DB storage"
```

---

## Task 10: MarketXgService — Result Type, Staleness Fix, Bookmaker Preference

**Files:**
- Modify: `backend/app/services/market_xg.py`
- Create: `backend/tests/services/test_market_xg_enhanced.py`

- [ ] **Step 1: Write failing tests for updated MarketXgResult and staleness**

Create `tests/services/test_market_xg_enhanced.py`:

```python
"""Tests for enhanced MarketXgService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.market_xg import MAX_SNAPSHOT_AGE, MarketXgResult, _preferred_bookmaker


class TestPreferredBookmaker:
    def test_prefers_oddsportal(self):
        assert _preferred_bookmaker({"oddsportal", "betfair", "pinnacle"}) == "oddsportal"

    def test_prefers_betfair_over_pinnacle(self):
        assert _preferred_bookmaker({"betfair", "pinnacle"}) == "betfair"

    def test_prefers_pinnacle_over_betclic(self):
        assert _preferred_bookmaker({"pinnacle", "betclic"}) == "pinnacle"

    def test_returns_none_for_empty(self):
        assert _preferred_bookmaker(set()) is None

    def test_returns_any_for_unknown(self):
        result = _preferred_bookmaker({"unknown_book"})
        assert result == "unknown_book"


class TestMaxSnapshotAge:
    def test_max_snapshot_age_is_3_hours(self):
        assert MAX_SNAPSHOT_AGE == timedelta(hours=3)


class TestMarketXgResultFields:
    def test_has_data_source_field(self):
        result = MarketXgResult(
            xg_home=1.5,
            xg_away=1.0,
            xg_source="market_implied",
            data_source="oddsportal",
            fallback_used=False,
            fit_residual=0.01,
            flagged=False,
            as_of_utc=datetime.now(timezone.utc),
            input_snapshot_ids=[1, 2, 3],
        )
        assert result.data_source == "oddsportal"
        assert result.fallback_used is False
        assert result.flagged is False
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python -m pytest tests/services/test_market_xg_enhanced.py -v
```

- [ ] **Step 3: Update `MarketXgResult` dataclass in `app/services/market_xg.py`**

Replace the existing `MarketXgResult` dataclass with:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

MAX_SNAPSHOT_AGE = timedelta(hours=3)
FIT_RESIDUAL_FLAG_THRESHOLD = 0.06

@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged"]
    data_source: str           # "oddsportal" | "betclic" | "unibet"
    fallback_used: bool
    fit_residual: float
    flagged: bool              # fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD
    as_of_utc: datetime
    input_snapshot_ids: list[int] = field(default_factory=list)
```

- [ ] **Step 4: Update `_preferred_bookmaker` in `app/services/market_xg.py`**

Replace the existing `_preferred_bookmaker` function:

```python
_BOOKMAKER_PRIORITY = ["oddsportal", "betfair", "pinnacle", "betclic", "unibet"]

def _preferred_bookmaker(available: set[str]) -> str | None:
    if not available:
        return None
    for bm in _BOOKMAKER_PRIORITY:
        if bm in available:
            return bm
    return next(iter(available))
```

- [ ] **Step 5: Update staleness check in `MarketXgService.compute()`**

Find and replace the staleness check (currently uses `_STALENESS_LIMIT = timedelta(hours=24)` relative to kickoff):

```python
# OLD — remove this:
# _STALENESS_LIMIT = timedelta(hours=24)
# if snapshot_utc < fixture.kickoff_utc - _STALENESS_LIMIT:
#     ...

# NEW — add at module level:
MAX_SNAPSHOT_AGE = timedelta(hours=3)

# In compute(), replace the staleness check with:
now = datetime.now(timezone.utc)
if now - latest_snapshot_utc > MAX_SNAPSHOT_AGE:
    logger.warning("market_xg: stale snapshot for fixture %s (age=%s)", fixture_id, now - latest_snapshot_utc)
    return None
```

- [ ] **Step 6: Remove Dixon-Coles fallback from `MarketXgService.compute()`**

Delete the `except` branch that falls back to Dixon-Coles. If solvers fail, return `None` instead:

```python
# In compute(), replace Dixon-Coles fallback with:
except Exception as exc:
    logger.warning("market_xg: solver failed for fixture %s: %s", fixture_id, exc)
    return None
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/services/test_market_xg_enhanced.py -v
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add app/services/market_xg.py tests/services/test_market_xg_enhanced.py
git commit -m "feat: MarketXgService — new result type, staleness fix, oddsportal bookmaker preference, remove dixon fallback"
```

---

## Task 11: MarketXgService — BTTS 2D Solver

**Files:**
- Modify: `backend/app/services/market_xg.py`
- Modify: `backend/tests/services/test_market_xg_enhanced.py`

- [ ] **Step 1: Add solver tests to `tests/services/test_market_xg_enhanced.py`**

Append to the existing test file:

```python
from app.services.market_xg import _fit_lambdas, _p_poisson_btts, _p_poisson_over_2_5


class TestPoissonHelpers:
    def test_btts_zero_when_one_team_cannot_score(self):
        # lambda_home = 0 → P(home scores) = 0 → BTTS = 0
        assert _p_poisson_btts(0.0001, 1.5) < 0.01

    def test_over_2_5_increases_with_lambda(self):
        assert _p_poisson_over_2_5(4.0) > _p_poisson_over_2_5(2.0)

    def test_over_2_5_known_value(self):
        # lambda_t = 2.5: P(over 2.5) ≈ 0.456
        assert abs(_p_poisson_over_2_5(2.5) - 0.456) < 0.01


class TestFitLambdas:
    def test_recovers_known_lambdas(self):
        """Given probs derived from (1.5, 1.0), solver should recover close values."""
        from scipy.stats import poisson
        import math

        lh_true, la_true = 1.5, 1.0
        lt = lh_true + la_true

        p_over = _p_poisson_over_2_5(lt)
        p_btts = _p_poisson_btts(lh_true, la_true)

        # Compute P(home win) and P(draw) via Poisson
        from app.services.market_xg import _p_poisson_home_win, _p_poisson_draw
        p_home = _p_poisson_home_win(lh_true, la_true)
        p_draw = _p_poisson_draw(lh_true, la_true)

        lh_hat, la_hat, residual = _fit_lambdas(p_home, p_draw, p_over, p_btts)

        assert abs(lh_hat - lh_true) < 0.05
        assert abs(la_hat - la_true) < 0.05
        assert residual < 1e-6

    def test_clamps_to_bounds(self):
        # Edge case: very high probabilities → solver stays within [0.05, 4.5]
        lh, la, _ = _fit_lambdas(0.99, 0.005, 0.99, 0.98)
        assert 0.05 <= lh <= 4.5
        assert 0.05 <= la <= 4.5

    def test_flags_high_residual(self):
        # Contradictory market probs → residual > threshold
        _, _, residual = _fit_lambdas(
            p_home_win=0.90,   # home very dominant
            p_draw=0.01,
            p_over_2_5=0.10,   # but very low total goals (contradictory)
            p_btts_yes=0.80,   # and high BTTS (contradictory)
        )
        assert residual > 0.01
```

- [ ] **Step 2: Run tests — expect failure on new solver tests**

```bash
python -m pytest tests/services/test_market_xg_enhanced.py::TestFitLambdas -v
```

Expected: `ImportError: cannot import name '_fit_lambdas'`

- [ ] **Step 3: Add solver functions to `app/services/market_xg.py`**

Add these functions (before the `MarketXgService` class):

```python
import math

from scipy.optimize import brentq, minimize
from scipy.stats import poisson as _poisson_dist


def _p_poisson_home_win(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home > Away) under Poisson(lh) vs Poisson(la)."""
    total = 0.0
    for h in range(1, max_goals + 1):
        ph = _poisson_dist.pmf(h, lh)
        for a in range(0, h):
            total += ph * _poisson_dist.pmf(a, la)
    return total


def _p_poisson_draw(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home == Away) under Poisson."""
    return float(sum(_poisson_dist.pmf(k, lh) * _poisson_dist.pmf(k, la) for k in range(max_goals + 1)))


def _p_poisson_over_2_5(lt: float) -> float:
    """P(Total > 2.5) = 1 - e^{-lt}(1 + lt + lt^2/2)."""
    return 1.0 - math.exp(-lt) * (1.0 + lt + lt ** 2 / 2.0)


def _p_poisson_btts(lh: float, la: float) -> float:
    """P(Both teams score) = (1 - e^{-lh})(1 - e^{-la})."""
    return (1.0 - math.exp(-lh)) * (1.0 - math.exp(-la))


def _solve_lambda_t_approx(p_over_2_5: float) -> float:
    """Rough brentq estimate of total lambda — used as warm start for _fit_lambdas."""
    def eq(lt: float) -> float:
        return _p_poisson_over_2_5(lt) - p_over_2_5
    try:
        return float(brentq(eq, 0.05, 15.0))
    except ValueError:
        return 2.5


def _fit_lambdas(
    p_home_win: float,
    p_draw: float,
    p_over_2_5: float,
    p_btts_yes: float,
) -> tuple[float, float, float]:
    """
    Fit (lambda_home, lambda_away) to 4 market probabilities via L-BFGS-B.

    Returns (lambda_home, lambda_away, fit_residual).
    fit_residual is the sum of squared deviations across all 4 constraints.
    Flag result if fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD (0.06).
    """
    def objective(x: list[float]) -> float:
        lh, la = x
        r1 = (_p_poisson_home_win(lh, la) - p_home_win) ** 2
        r2 = (_p_poisson_draw(lh, la) - p_draw) ** 2
        r3 = (_p_poisson_over_2_5(lh + la) - p_over_2_5) ** 2
        r4 = (_p_poisson_btts(lh, la) - p_btts_yes) ** 2
        return r1 + r2 + r3 + r4

    lt_init = _solve_lambda_t_approx(p_over_2_5)
    lh_init = min(max(lt_init * 0.55, 0.3), 4.0)
    la_init = min(max(lt_init * 0.45, 0.3), 4.0)

    result = minimize(
        objective,
        x0=[lh_init, la_init],
        bounds=[(0.05, 4.5), (0.05, 4.5)],
        method="L-BFGS-B",
        options={"maxiter": 500, "ftol": 1e-12},
    )
    lh, la = float(result.x[0]), float(result.x[1])
    return lh, la, float(result.fun)
```

- [ ] **Step 4: Wire `_fit_lambdas` into `MarketXgService.compute()`**

In `compute()`, replace the existing sequential brentq solve with:

```python
# Load and devig all 3 markets
h2h_clean = compute_clean_probs(h2h_odds)      # {"home": p, "draw": p, "away": p}
totals_clean = compute_clean_probs(totals_odds) # {"over_2.5": p, "under_2.5": p}
btts_clean = compute_clean_probs(btts_odds)     # {"yes": p, "no": p}

lh, la, residual = _fit_lambdas(
    p_home_win=h2h_clean["home"],
    p_draw=h2h_clean["draw"],
    p_over_2_5=totals_clean["over_2.5"],
    p_btts_yes=btts_clean["yes"],
)

flagged = residual > FIT_RESIDUAL_FLAG_THRESHOLD
xg_source = "market_implied_flagged" if flagged else "market_implied"

return MarketXgResult(
    xg_home=lh,
    xg_away=la,
    xg_source=xg_source,
    data_source=snapshot_source,   # "oddsportal" | "betclic" | "unibet"
    fallback_used=snapshot_fallback_used,
    fit_residual=residual,
    flagged=flagged,
    as_of_utc=snapshot_utc,
    input_snapshot_ids=snapshot_ids,
)
```

- [ ] **Step 5: Run all market_xg tests**

```bash
python -m pytest tests/services/test_market_xg_enhanced.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/services/market_xg.py tests/services/test_market_xg_enhanced.py
git commit -m "feat: BTTS 3-constraint Poisson solver (L-BFGS-B) in MarketXgService"
```

---

## Task 12: Adaptive Scheduler

**Files:**
- Create: `backend/app/services/market_scrape_scheduler.py`
- Create: `backend/tests/services/test_market_scrape_scheduler.py`

- [ ] **Step 1: Write failing tests**

Create `tests/services/test_market_scrape_scheduler.py`:

```python
"""Tests for the adaptive scrape scheduler."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.market_scrape_scheduler import (
    _compute_interval_minutes,
    _compute_score,
    _compute_target_rpm,
)


class TestComputeIntervalMinutes:
    def test_beyond_24h(self):
        assert _compute_interval_minutes(t_minutes=1500) == 120

    def test_between_6h_and_24h(self):
        assert _compute_interval_minutes(t_minutes=500) == 60

    def test_between_2h_and_6h(self):
        assert _compute_interval_minutes(t_minutes=200) == 20

    def test_between_30m_and_2h(self):
        assert _compute_interval_minutes(t_minutes=60) == 7

    def test_between_5m_and_30m(self):
        assert _compute_interval_minutes(t_minutes=15) == 3

    def test_at_or_below_5m_returns_none(self):
        assert _compute_interval_minutes(t_minutes=5) is None
        assert _compute_interval_minutes(t_minutes=0) is None
        assert _compute_interval_minutes(t_minutes=-1) is None


class TestComputeScore:
    def test_urgent_match_scores_higher(self):
        score_near = _compute_score(t_minutes=10, error_streak=0)
        score_far = _compute_score(t_minutes=1000, error_streak=0)
        assert score_near > score_far

    def test_error_streak_reduces_score(self):
        score_clean = _compute_score(t_minutes=30, error_streak=0)
        score_errors = _compute_score(t_minutes=30, error_streak=5)
        assert score_clean > score_errors

    def test_penalty_capped_at_0_5(self):
        score_5 = _compute_score(t_minutes=30, error_streak=5)
        score_100 = _compute_score(t_minutes=30, error_streak=100)
        assert score_5 == score_100  # penalty capped


class TestComputeTargetRpm:
    def test_eco_mode_when_no_due(self):
        assert _compute_target_rpm(due_count=0, pressure_count=0) == 1.0

    def test_medium_when_few_due(self):
        assert _compute_target_rpm(due_count=2, pressure_count=0) == 2.0

    def test_high_when_many_due(self):
        assert _compute_target_rpm(due_count=10, pressure_count=0) == 3.0

    def test_boost_when_pressure(self):
        rpm = _compute_target_rpm(due_count=5, pressure_count=15, max_rpm_hard=5.0)
        assert rpm == 5.0

    def test_capped_by_max_rpm_hard(self):
        rpm = _compute_target_rpm(due_count=100, pressure_count=100, max_rpm_hard=5.0)
        assert rpm <= 5.0
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python -m pytest tests/services/test_market_scrape_scheduler.py -v
```

- [ ] **Step 3: Create `app/services/market_scrape_scheduler.py`**

```python
"""Adaptive token-bucket scheduler for market odds scraping.

Public functions used by worker.py:
    MarketScrapeScheduler.tick() — called every ~15s by APScheduler

Pure scheduling helpers (exported for testing):
    _compute_interval_minutes(t_minutes) -> int | None
    _compute_score(t_minutes, error_streak) -> float
    _compute_target_rpm(due_count, pressure_count, max_rpm_hard) -> float
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx
from playwright.async_api import async_playwright
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.poll_state import OddsPortalPollState
from app.models.team_xg import TeamXgEstimate

logger = logging.getLogger(__name__)

MAX_RPM_HARD: float = 5.0
JITTER_FACTOR: float = 0.15          # ± 15% on interval
T_MINUS_STOP_MINUTES: int = 5
PRESSURE_WINDOW_MINUTES: int = 120   # "hot" window for pressure count
PRESSURE_THRESHOLD: int = 10         # matches in hot window to trigger boost
BACKOFF_FREEZE_MINUTES: int = 20
BACKOFF_RECOVERY_RPM_STEP: float = 0.25
BACKOFF_RECOVERY_INTERVAL_MINUTES: int = 10


def _compute_interval_minutes(t_minutes: float) -> int | None:
    """Return polling interval in minutes for a match t_minutes before KO. None = stop."""
    if t_minutes <= T_MINUS_STOP_MINUTES:
        return None
    if t_minutes <= 30:
        return 3
    if t_minutes <= 120:
        return 7
    if t_minutes <= 360:
        return 20
    if t_minutes <= 1440:
        return 60
    return 120


def _compute_score(t_minutes: float, error_streak: int) -> float:
    """Priority score: higher = scrape sooner. urgency = 1/(t+15), penalty capped at 0.5."""
    urgency = 1.0 / (t_minutes + 15.0)
    penalty = min(0.5, 0.1 * error_streak)
    return urgency - penalty


def _compute_target_rpm(
    due_count: int,
    pressure_count: int,
    max_rpm_hard: float = MAX_RPM_HARD,
) -> float:
    """Dynamic target RPM based on queue depth and hot-window pressure."""
    if due_count == 0:
        rpm = 1.0
    elif due_count <= 3:
        rpm = 2.0
    else:
        rpm = 3.0

    if pressure_count > PRESSURE_THRESHOLD and due_count > 0:
        rpm = max_rpm_hard

    return min(rpm, max_rpm_hard)


def _apply_jitter(interval_minutes: int) -> timedelta:
    """Add ±15% jitter to interval."""
    factor = 1.0 + random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
    return timedelta(minutes=interval_minutes * factor)


class MarketScrapeScheduler:
    """Token-bucket scheduler. Instantiated once in worker.py."""

    def __init__(self) -> None:
        self._tokens: float = 1.0
        self._target_rpm: float = 1.0
        self._frozen_until: datetime | None = None
        self._last_tick: datetime = datetime.now(timezone.utc)
        self._recovery_check: datetime = datetime.now(timezone.utc)

    def _refill_tokens(self, now: datetime) -> None:
        elapsed = (now - self._last_tick).total_seconds()
        self._tokens = min(
            self._tokens + self._target_rpm / 60.0 * elapsed,
            MAX_RPM_HARD,
        )
        self._last_tick = now

    def trigger_backoff(self) -> None:
        """Call on HTTP 429 / captcha / persistent errors."""
        self._target_rpm = max(0.5, self._target_rpm * 0.5)
        self._frozen_until = datetime.now(timezone.utc) + timedelta(minutes=BACKOFF_FREEZE_MINUTES)
        logger.warning("scheduler: backoff triggered, target_rpm=%.2f, frozen=%s", self._target_rpm, self._frozen_until)

    def _maybe_recover(self, now: datetime) -> None:
        if self._frozen_until and now > self._frozen_until:
            if now > self._recovery_check + timedelta(minutes=BACKOFF_RECOVERY_INTERVAL_MINUTES):
                self._target_rpm = min(MAX_RPM_HARD, self._target_rpm + BACKOFF_RECOVERY_RPM_STEP)
                self._recovery_check = now
                logger.info("scheduler: recovery step, target_rpm=%.2f", self._target_rpm)

    async def tick(self, session: AsyncSession) -> None:
        """
        Main tick — called every ~15s by APScheduler.
        Refills tokens, selects eligible fixtures, fires scrape chains.
        """
        now = datetime.now(timezone.utc)
        self._refill_tokens(now)
        self._maybe_recover(now)

        if self._frozen_until and now < self._frozen_until:
            logger.debug("scheduler: frozen until %s", self._frozen_until)
            return

        # Load eligible poll states
        eligible_q = (
            select(OddsPortalPollState, Fixture.kickoff_utc)
            .join(Fixture, OddsPortalPollState.fixture_id == Fixture.id)
            .where(
                OddsPortalPollState.stopped.is_(False),
                OddsPortalPollState.next_due_at_utc <= now,
            )
        )
        rows = (await session.execute(eligible_q)).all()

        # Filter: now < KO - 5min
        eligible = []
        for poll_state, kickoff_utc in rows:
            t_minutes = (kickoff_utc - now).total_seconds() / 60.0
            if t_minutes <= T_MINUS_STOP_MINUTES:
                # Stop this fixture
                await session.execute(
                    update(OddsPortalPollState)
                    .where(OddsPortalPollState.id == poll_state.id)
                    .values(stopped=True, stopped_reason="T_MINUS_5")
                )
                continue
            eligible.append((poll_state, kickoff_utc, t_minutes))

        # Compute dynamic target_rpm
        due_count = len(eligible)
        pressure_count = sum(1 for _, _, t in eligible if t <= PRESSURE_WINDOW_MINUTES)
        self._target_rpm = _compute_target_rpm(due_count, pressure_count)

        # Sort by priority score
        eligible.sort(key=lambda x: _compute_score(x[2], x[0].error_streak), reverse=True)

        logger.info(
            "scheduler: tick due=%d pressure=%d tokens=%.2f target_rpm=%.2f",
            due_count, pressure_count, self._tokens, self._target_rpm,
        )

        # Fire scrapes
        tasks = []
        for poll_state, kickoff_utc, t_minutes in eligible:
            if self._tokens < 1.0:
                break
            self._tokens -= 1.0
            tasks.append(self._run_scrape(poll_state, t_minutes, session))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await session.commit()

    async def _run_scrape(
        self,
        poll_state: OddsPortalPollState,
        t_minutes: float,
        session: AsyncSession,
    ) -> None:
        """Run the fallback chain for one fixture and update poll state."""
        from app.ingestion.market_scrape_chain import run_scrape_chain, store_scrape_result
        from app.services.market_xg import MarketXgService

        now = datetime.now(timezone.utc)
        logger.info(
            "scheduler: scrape fixture=%s t_to_ko=%.0f min",
            poll_state.fixture_id, t_minutes,
        )

        # Update last_scraped_at
        await session.execute(
            update(OddsPortalPollState)
            .where(OddsPortalPollState.id == poll_state.id)
            .values(last_scraped_at_utc=now)
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            async with httpx.AsyncClient() as http_client:
                result = await run_scrape_chain(poll_state, browser, http_client)
            await browser.close()

        if result is None:
            # All sources failed
            new_streak = poll_state.error_streak + 1
            await session.execute(
                update(OddsPortalPollState)
                .where(OddsPortalPollState.id == poll_state.id)
                .values(error_streak=new_streak)
            )
            if new_streak >= 3:
                self.trigger_backoff()
            logger.warning(
                "scrape_fail fixture=%s sources_tried=[op,bc,ub] error_streak=%d",
                poll_state.fixture_id, new_streak,
            )
        else:
            # Store snapshots
            await store_scrape_result(result, poll_state.fixture_id, session)

            # Compute and store team xG estimate
            xg = await MarketXgService(session).compute(poll_state.fixture_id, session)
            if xg is not None:
                session.add(TeamXgEstimate(
                    fixture_id=poll_state.fixture_id,
                    as_of_utc=result.ingested_at_utc,
                    lambda_home=xg.xg_home,
                    lambda_away=xg.xg_away,
                    fit_residual=xg.fit_residual,
                    flagged=xg.flagged,
                    data_source=xg.data_source,
                    fallback_used=xg.fallback_used,
                    input_snapshot_ids=xg.input_snapshot_ids,
                ))

            # Schedule next due
            interval = _compute_interval_minutes(t_minutes)
            if interval is None:
                next_due = None
                stopped = True
                stopped_reason = "T_MINUS_5"
            else:
                next_due = now + _apply_jitter(interval)
                stopped = False
                stopped_reason = None

            await session.execute(
                update(OddsPortalPollState)
                .where(OddsPortalPollState.id == poll_state.id)
                .values(
                    last_success_at_utc=now,
                    error_streak=0,
                    next_due_at_utc=next_due or poll_state.next_due_at_utc,
                    stopped=stopped,
                    stopped_reason=stopped_reason,
                )
            )
            logger.info(
                "scrape_success fixture=%s source=%s fallback=%s next_due=%s",
                poll_state.fixture_id, result.source, result.fallback_used, next_due,
            )
```

- [ ] **Step 4: Run scheduler tests**

```bash
python -m pytest tests/services/test_market_scrape_scheduler.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/services/market_scrape_scheduler.py tests/services/test_market_scrape_scheduler.py
git commit -m "feat: adaptive token-bucket scheduler (MarketScrapeScheduler)"
```

---

## Task 13: Recommendation Service — Remove Dixon-Coles

**Files:**
- Modify: `backend/app/services/recommendation_service.py`

- [ ] **Step 1: Remove Dixon-Coles call — find and replace the team xG block**

In `recommendation_service.py`, find the section that calls `compute_team_stats()` and `estimate_team_match_xg()`.

Replace with:

```python
from app.services.market_xg import MarketXgService

# Inside the per-fixture loop:
market_xg = await MarketXgService(session).compute(fixture.id, session)
if market_xg is None:
    logger.warning(
        "rec_service: no market xG for fixture %s — skipping (no market data)",
        fixture.id,
    )
    continue

team_xg_home = market_xg.xg_home
team_xg_away = market_xg.xg_away
xg_source = market_xg.data_source
```

- [ ] **Step 2: Remove `compute_team_stats()` call from the top of the function**

Delete or comment out:
```python
# DELETE:
team_stats = await compute_team_stats(db)
```

- [ ] **Step 3: Store `xg_source` on each generated `Recommendation`**

When constructing the `Recommendation` object, add:
```python
xg_source=xg_source,
```

- [ ] **Step 4: Remove `fixture_strength` Dixon-Coles adjustment**

Find and remove the `fixture_strength` calculation:
```python
# DELETE these lines:
# fixture_strength = team_match_xg / team_season_avg
# fixture_strength = max(0.6, min(1.5, fixture_strength))
```

The market-implied xG is already fixture-specific — no adjustment needed.

- [ ] **Step 5: Run existing recommendation tests**

```bash
python -m pytest tests/ -v -m "not integration" -k "recommendation"
```

Fix any failures from removed Dixon-Coles imports.

- [ ] **Step 6: Commit**

```bash
git add app/services/recommendation_service.py
git commit -m "feat: recommendation service uses MarketXgService exclusively, removes Dixon-Coles"
```

---

## Task 14: Worker Updates

**Files:**
- Modify: `backend/app/worker.py`

- [ ] **Step 1: Remove `job_snapshot_match_odds` job**

Find the block that adds `job_snapshot_match_odds` to the scheduler and delete it entirely. Also delete the `job_snapshot_match_odds` async function definition.

- [ ] **Step 2: Add scheduler instance and tick job**

At module level (near other service instantiations), add:
```python
from app.services.market_scrape_scheduler import MarketScrapeScheduler

_market_scheduler = MarketScrapeScheduler()
```

Add the tick job in the `main()` / scheduler setup block:
```python
async def job_oddsportal_scheduler_tick() -> None:
    """Token-bucket tick — fires scrape chains for due fixtures."""
    async with async_session() as session:
        try:
            await _market_scheduler.tick(session)
        except Exception as exc:
            logger.error("job_oddsportal_scheduler_tick error: %s", exc, exc_info=True)

scheduler.add_job(
    job_oddsportal_scheduler_tick,
    IntervalTrigger(seconds=15, jitter=2),
    id="job_oddsportal_scheduler_tick",
    max_instances=1,  # prevent overlapping ticks
)
```

- [ ] **Step 3: Verify worker starts without errors**

```bash
cd /Users/yohan.resin/Ev0/backend
python -c "from app.worker import main; print('worker imports OK')"
```

Expected: `worker imports OK`

- [ ] **Step 4: Commit**

```bash
git add app/worker.py
git commit -m "feat: worker — add oddsportal scheduler tick job, remove Odds API match odds job"
```

---

## Task 15: Seed Script

**Files:**
- Create: `backend/app/scripts/seed_poll_state.py`

The scheduler needs `oddsportal_poll_state` rows before it can scrape anything. This script seeds them from a CSV file before each gameweek.

- [ ] **Step 1: Create `app/scripts/seed_poll_state.py`**

```python
"""Seed oddsportal_poll_state from a CSV file.

CSV format (header required):
    fixture_id,oddsportal_url,betclic_url,unibet_url

betclic_url and unibet_url are optional (leave blank if not available).

Usage:
    python -m app.scripts.seed_poll_state --csv /path/to/gameweek.csv [--dry-run]

The script upserts rows: existing fixture_id records are updated with new URLs.
next_due_at_utc is set to now() on insert, left unchanged on update.
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session
from app.models.poll_state import OddsPortalPollState


async def seed(csv_path: Path, dry_run: bool) -> None:
    rows = []
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixture_id = int(row["fixture_id"])
            op_url = row["oddsportal_url"].strip()
            if not op_url:
                print(f"SKIP fixture {fixture_id}: no oddsportal_url", file=sys.stderr)
                continue
            rows.append({
                "fixture_id": fixture_id,
                "oddsportal_url": op_url,
                "betclic_url": row.get("betclic_url", "").strip() or None,
                "unibet_url": row.get("unibet_url", "").strip() or None,
                "next_due_at_utc": datetime.now(timezone.utc),
                "error_streak": 0,
                "stopped": False,
                "stopped_reason": None,
            })

    print(f"Seeding {len(rows)} fixtures (dry_run={dry_run})")

    if dry_run:
        for r in rows:
            print(f"  fixture={r['fixture_id']} op={r['oddsportal_url']}")
        return

    async with async_session() as session:
        stmt = pg_insert(OddsPortalPollState).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_poll_state_fixture",
            set_={
                "oddsportal_url": stmt.excluded.oddsportal_url,
                "betclic_url": stmt.excluded.betclic_url,
                "unibet_url": stmt.excluded.unibet_url,
                # Do NOT reset next_due_at_utc or error_streak on update
            },
        )
        await session.execute(stmt)
        await session.commit()
        print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed poll state from CSV")
    parser.add_argument("--csv", required=True, type=Path, help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Print rows without inserting")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"File not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(seed(args.csv, args.dry_run))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test dry-run**

Create a test CSV at `/tmp/test_seed.csv`:
```csv
fixture_id,oddsportal_url,betclic_url,unibet_url
123,https://www.oddsportal.com/football/france/ligue-1/psg-lyon-abc123/,https://www.betclic.fr/football/ligue-1/psg-lyon,
```

Run:
```bash
cd /Users/yohan.resin/Ev0/backend
python -m app.scripts.seed_poll_state --csv /tmp/test_seed.csv --dry-run
```

Expected:
```
Seeding 1 fixtures (dry_run=True)
  fixture=123 op=https://www.oddsportal.com/football/france/ligue-1/psg-lyon-abc123/
```

- [ ] **Step 3: Commit**

```bash
git add app/scripts/seed_poll_state.py
git commit -m "feat: seed_poll_state script — CSV-based pre-gameweek seeding"
```

---

## Task 16: API Schema + Frontend Badge

**Files:**
- Modify: `backend/app/api/recommendations.py`
- Modify: `frontend/src/app/dashboard/recommendations/page.tsx`

- [ ] **Step 1: Add `xg_source` to recommendations API response**

In `app/api/recommendations.py`, find the Pydantic response model for recommendations. Add the field:

```python
class RecommendationOut(BaseModel):
    # ... existing fields ...
    xg_source: str | None = None
```

In the query/serialisation logic, map `Recommendation.xg_source` → `RecommendationOut.xg_source`.

- [ ] **Step 2: Add xG source badge to recommendations page**

In `frontend/src/app/dashboard/recommendations/page.tsx`, find the card/row component for each recommendation. Add a badge after the existing market label:

```tsx
function XgSourceBadge({ source }: { source: string | null }) {
  if (!source) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-500">
        xG · indisponible
      </span>
    );
  }
  const isOddsPortal = source === "oddsportal";
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        isOddsPortal
          ? "bg-green-100 text-green-700"
          : "bg-orange-100 text-orange-700"
      }`}
    >
      {isOddsPortal
        ? "xG · OddsPortal"
        : `xG · ${source.charAt(0).toUpperCase() + source.slice(1)} (fallback)`}
    </span>
  );
}
```

Add `<XgSourceBadge source={rec.xg_source} />` in the recommendation card, next to the market type badge.

- [ ] **Step 3: Verify frontend builds**

```bash
cd /Users/yohan.resin/Ev0/frontend
npm run build
```

Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/yohan.resin/Ev0
git add backend/app/api/recommendations.py frontend/src/app/dashboard/recommendations/page.tsx
git commit -m "feat: xg_source field in API response and frontend badge (OddsPortal green, fallback orange)"
```

---

## Task 17: Full Test Suite + Smoke Verification

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/yohan.resin/Ev0/backend
python -m pytest tests/ -v -m "not integration"
```

Expected: all green. Fix any import errors from removed `compute_team_stats` / `estimate_team_match_xg` references.

- [ ] **Step 2: Verify migration on production DB (VPS)**

SSH to VPS and run:
```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env exec backend alembic upgrade head
```

Expected: `Running upgrade 015 -> 016`.

- [ ] **Step 3: Rebuild and restart backend + worker**

```bash
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker
```

- [ ] **Step 4: Verify scheduler job is running**

```bash
docker logs ev0-compose-z5hvqt-worker-1 --tail=50 | grep oddsportal
```

Expected: `scheduler: tick due=0 pressure=0 tokens=1.00 target_rpm=1.00` (eco mode, no fixtures seeded yet).

- [ ] **Step 5: Seed one test fixture and verify scrape**

```bash
# Create test CSV on VPS
echo "fixture_id,oddsportal_url,betclic_url,unibet_url" > /tmp/test_fixture.csv
echo "1,https://www.oddsportal.com/football/france/ligue-1/<actual-slug>/,," >> /tmp/test_fixture.csv

docker cp /tmp/test_fixture.csv ev0-compose-z5hvqt-backend-1:/tmp/test_fixture.csv
docker exec ev0-compose-z5hvqt-backend-1 python -m app.scripts.seed_poll_state --csv /tmp/test_fixture.csv
```

Wait 30s, then check logs:
```bash
docker logs ev0-compose-z5hvqt-worker-1 --tail=100 | grep "scrape_"
```

Expected: `scrape_success fixture=1 source=oddsportal`.

- [ ] **Step 6: Verify xG estimate was stored**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -c "SELECT fixture_id, lambda_home, lambda_away, data_source, fallback_used FROM team_xg_estimates ORDER BY created_at DESC LIMIT 5;"
```

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: market-anchored xG scraper — implementation complete"
```

---

## Spec Coverage Check

| Spec section | Covered by |
|---|---|
| DB: extend match_odds_snapshots | Task 2, 3 |
| DB: oddsportal_poll_state | Task 1, 3 |
| DB: team_xg_estimates | Task 1, 3 |
| DB: xg_source on recommendations | Task 2, 3 |
| Sanity checks + clean probs | Task 4 |
| ScrapeResult dataclass | Task 5 |
| OddsPortal Playwright scraper | Task 6 |
| Betclic match scraper | Task 7 |
| Unibet match scraper | Task 8 |
| Fallback chain + storage | Task 9 |
| MarketXgResult (data_source, fallback_used) | Task 10 |
| Staleness fix (3h not kickoff-relative) | Task 10 |
| Bookmaker preference (oddsportal first) | Task 10 |
| BTTS 3-constraint solver (L-BFGS-B) | Task 11 |
| Adaptive token-bucket scheduler | Task 12 |
| Backoff + recovery | Task 12 |
| T-5min stop | Task 12 |
| Polling intervals table | Task 12 |
| Remove Dixon-Coles from rec service | Task 13 |
| Store xg_source on Recommendation | Task 13 |
| Remove Odds API match odds job | Task 14 |
| Add scheduler tick job to worker | Task 14 |
| Seed script (pre-gameweek CSV) | Task 15 |
| API xg_source field | Task 16 |
| Frontend xG source badge | Task 16 |
| Smoke verification on VPS | Task 17 |
