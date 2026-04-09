# Bzzoiro API Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Understat + Sofascore scraping stack with the Bzzoiro Sports API as the primary data source, delivering a complete player data library, a switchable xG pricing mode, and xG source badges across all UI surfaces.

**Architecture:** Three-phase delivery — (1) core data layer: 7 new `bzz_*` tables + ingestion modules + worker jobs; (2) pricing engine update: Bzzoiro fields replace Sofascore/Understat inputs, xG resolver selects mode per match; (3) frontend refonte: players page with season + match-level drill-down, xG badges on dashboard/recommendations/history.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, PostgreSQL (JSONB for shotmap/momentum/lineups), httpx, Alembic, APScheduler, Next.js 14 / Recharts.

---

## File Map

**Create:**
```
backend/app/models/bzzoiro.py
backend/app/ingestion/bzzoiro/__init__.py
backend/app/ingestion/bzzoiro/client.py
backend/app/ingestion/bzzoiro/sync_reference.py     # leagues + teams
backend/app/ingestion/bzzoiro/sync_players.py
backend/app/ingestion/bzzoiro/sync_events.py
backend/app/ingestion/bzzoiro/sync_player_stats.py  # match stats + derived metrics
backend/app/ingestion/bzzoiro/aggregate.py           # season stats from match rows
backend/app/ingestion/bzzoiro/sync_predictions.py
backend/app/pricing/xg_resolver.py
backend/alembic/versions/017_bzzoiro_tables.py
backend/tests/ingestion/bzzoiro/test_client.py
backend/tests/ingestion/bzzoiro/test_sync_player_stats.py
backend/tests/ingestion/bzzoiro/test_aggregate.py
backend/tests/pricing/test_xg_resolver.py
frontend/src/components/XgBadge.tsx
frontend/src/components/players/PlayerMatchChart.tsx
```

**Modify:**
```
backend/app/config.py                        # add bzzoiro_api_key
backend/app/worker.py                        # register 5 new bzzoiro jobs
backend/app/pricing/goalscorer.py            # new bzzoiro quality multiplier
backend/app/pricing/assist.py                # new bzzoiro creation multiplier
backend/app/pricing/team_xg.py              # _load_team_players reads bzz_player_season_stats
backend/app/api/players.py                   # replace with bzz-backed implementation
backend/app/api/pricing.py                   # add xg_source_label to response
backend/app/api/recommendations.py          # add bzzoiro_prediction column
frontend/src/app/dashboard/players/page.tsx # full refonte
frontend/src/app/dashboard/page.tsx         # add XgBadge
frontend/src/app/dashboard/recommendations/page.tsx  # add bzzoiro ML column + XgBadge
frontend/src/app/dashboard/history/page.tsx # add XgBadge
```

---

## Task 1: Add BZZOIRO_API_KEY to config

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add field to Settings**

```python
# In class Settings, after api_football_key line:
bzzoiro_api_key: str | None = None  # Bzzoiro Sports Data API
```

- [ ] **Step 2: Add to .env on VPS (manual — run on VPS)**

```bash
echo "BZZOIRO_API_KEY=<your_token>" >> /etc/dokploy/compose/ev0-compose-z5hvqt/code/.env
```

Also update Dokploy DB:
```sql
-- Run on VPS via:
-- docker exec -it dokploy-postgres.1.* psql -U dokploy -d dokploy
UPDATE compose SET env = replace(env, 'BZZOIRO_API_KEY=', '') || E'\nBZZOIRO_API_KEY=<your_token>'
WHERE "composeId" = 'bpQY8Yr986JiwJRR_b0sk';
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add bzzoiro_api_key to config"
```

---

## Task 2: SQLAlchemy models for all bzz_* tables

**Files:**
- Create: `backend/app/models/bzzoiro.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bzzoiro_models.py
from app.models.bzzoiro import (
    BzzLeague, BzzTeam, BzzPlayer, BzzEvent,
    BzzPlayerMatchStat, BzzPlayerSeasonStat, BzzPrediction,
)

def test_models_have_expected_columns():
    assert hasattr(BzzPlayerMatchStat, "expected_goals")
    assert hasattr(BzzPlayerMatchStat, "shot_accuracy")    # derived
    assert hasattr(BzzPlayerMatchStat, "finishing_delta")  # derived
    assert hasattr(BzzPlayerSeasonStat, "xg_per_90")
    assert hasattr(BzzPlayerSeasonStat, "form_xg_5")
    assert hasattr(BzzEvent, "shotmap")
    assert hasattr(BzzPrediction, "prob_over_25")
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/test_bzzoiro_models.py -v
# Expected: ImportError — module not found
```

- [ ] **Step 3: Create `backend/app/models/bzzoiro.py`**

```python
"""Bzzoiro Sports API — SQLAlchemy models (primary data layer)."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BzzLeague(Base, TimestampMixin):
    __tablename__ = "bzz_leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    season_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzTeam(Base, TimestampMixin):
    __tablename__ = "bzz_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzPlayer(Base, TimestampMixin):
    __tablename__ = "bzz_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)  # cm
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[str | None] = mapped_column(String(5), nullable=True)   # G/D/M/F
    market_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # EUR
    current_team_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True, index=True
    )
    national_team_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzEvent(Base, TimestampMixin):
    __tablename__ = "bzz_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    league_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"), nullable=True, index=True
    )
    home_team_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True, index=True
    )
    away_team_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)   # notstarted/inprogress/finished
    period: Mapped[str | None] = mapped_column(String(5), nullable=True)    # 1T/HT/2T/FT
    current_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Scores
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # xG (actual — from Bzzoiro)
    home_xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_xg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Spatial data (JSONB — visualisation only)
    shotmap: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    incidents: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    momentum: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    average_positions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lineups: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Odds (JSONB)
    odds_1x2: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    odds_over_under: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    odds_btts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzPlayerMatchStat(Base, TimestampMixin):
    __tablename__ = "bzz_player_match_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_api_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bzz_players.api_id", ondelete="CASCADE"), index=True
    )
    event_api_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bzz_events.api_id", ondelete="CASCADE"), index=True
    )
    team_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True
    )
    is_home: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Presence
    minutes_played: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    touches: Mapped[int] = mapped_column(Integer, default=0)

    # Attacking
    goals: Mapped[int] = mapped_column(Integer, default=0)
    goal_assist: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float | None] = mapped_column(Float, nullable=True)    # xG
    expected_assists: Mapped[float | None] = mapped_column(Float, nullable=True)  # xA
    total_shots: Mapped[int] = mapped_column(Integer, default=0)
    shots_on_target: Mapped[int] = mapped_column(Integer, default=0)

    # Passing
    total_pass: Mapped[int] = mapped_column(Integer, default=0)
    accurate_pass: Mapped[int] = mapped_column(Integer, default=0)
    key_pass: Mapped[int] = mapped_column(Integer, default=0)
    total_long_balls: Mapped[int] = mapped_column(Integer, default=0)
    accurate_long_balls: Mapped[int] = mapped_column(Integer, default=0)

    # Crossing
    total_cross: Mapped[int] = mapped_column(Integer, default=0)
    accurate_cross: Mapped[int] = mapped_column(Integer, default=0)

    # Duels
    duel_won: Mapped[int] = mapped_column(Integer, default=0)
    duel_lost: Mapped[int] = mapped_column(Integer, default=0)
    aerial_won: Mapped[int] = mapped_column(Integer, default=0)
    aerial_lost: Mapped[int] = mapped_column(Integer, default=0)

    # Defense
    total_tackle: Mapped[int] = mapped_column(Integer, default=0)
    won_tackle: Mapped[int] = mapped_column(Integer, default=0)
    total_clearance: Mapped[int] = mapped_column(Integer, default=0)
    interception: Mapped[int] = mapped_column(Integer, default=0)
    ball_recovery: Mapped[int] = mapped_column(Integer, default=0)

    # Discipline / possession
    yellow_card: Mapped[int] = mapped_column(Integer, default=0)
    red_card: Mapped[int] = mapped_column(Integer, default=0)
    fouls: Mapped[int] = mapped_column(Integer, default=0)
    was_fouled: Mapped[int] = mapped_column(Integer, default=0)
    dispossessed: Mapped[int] = mapped_column(Integer, default=0)
    possession_lost: Mapped[int] = mapped_column(Integer, default=0)

    # Goalkeeper
    saves: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)

    # ── Derived (computed on insert) ─────────────────────────────────
    shot_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)          # SoT / shots
    xg_per_shot: Mapped[float | None] = mapped_column(Float, nullable=True)            # xG / shots
    finishing_delta: Mapped[float | None] = mapped_column(Float, nullable=True)        # goals − xG
    xa_delta: Mapped[float | None] = mapped_column(Float, nullable=True)               # assists − xA
    pass_completion: Mapped[float | None] = mapped_column(Float, nullable=True)        # acc / total
    long_ball_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    duel_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerial_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackle_success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("player_api_id", "event_api_id", name="uq_bzz_player_match"),
    )


class BzzPlayerSeasonStat(Base, TimestampMixin):
    __tablename__ = "bzz_player_season_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_api_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bzz_players.api_id", ondelete="CASCADE"), index=True
    )
    league_api_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"), nullable=True, index=True
    )
    season: Mapped[str] = mapped_column(String(10))     # "2025-2026"
    as_of_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Totals
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    minutes_played: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    goal_assist: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    total_shots: Mapped[int] = mapped_column(Integer, default=0)
    shots_on_target: Mapped[int] = mapped_column(Integer, default=0)
    key_pass: Mapped[int] = mapped_column(Integer, default=0)
    total_cross: Mapped[int] = mapped_column(Integer, default=0)
    accurate_cross: Mapped[int] = mapped_column(Integer, default=0)
    total_pass: Mapped[int] = mapped_column(Integer, default=0)
    accurate_pass: Mapped[int] = mapped_column(Integer, default=0)
    total_long_balls: Mapped[int] = mapped_column(Integer, default=0)
    accurate_long_balls: Mapped[int] = mapped_column(Integer, default=0)
    duel_won: Mapped[int] = mapped_column(Integer, default=0)
    duel_lost: Mapped[int] = mapped_column(Integer, default=0)
    aerial_won: Mapped[int] = mapped_column(Integer, default=0)
    aerial_lost: Mapped[int] = mapped_column(Integer, default=0)
    total_tackle: Mapped[int] = mapped_column(Integer, default=0)
    won_tackle: Mapped[int] = mapped_column(Integer, default=0)
    interception: Mapped[int] = mapped_column(Integer, default=0)
    ball_recovery: Mapped[int] = mapped_column(Integer, default=0)
    yellow_card: Mapped[int] = mapped_column(Integer, default=0)
    red_card: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)

    # Per-90 rates (computed)
    xg_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots_on_target_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_pass_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    accurate_cross_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    recoveries_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackles_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    interceptions_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Efficiency metrics (computed)
    shot_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_per_shot: Mapped[float | None] = mapped_column(Float, nullable=True)
    finishing_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_completion: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_ball_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    duel_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerial_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackle_success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Playing time profile
    avg_minutes_per_match: Mapped[float | None] = mapped_column(Float, nullable=True)
    starts_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Form (rolling 5 matches, recomputed each sync)
    form_xg_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    form_rating_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    form_goals_5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    form_assists_5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_trend: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("player_api_id", "league_api_id", "season", name="uq_bzz_player_season"),
    )


class BzzPrediction(Base, TimestampMixin):
    __tablename__ = "bzz_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_api_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bzz_events.api_id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at_bzz: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    prob_home_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_away_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_result: Mapped[str | None] = mapped_column(String(1), nullable=True)  # H/D/A

    expected_home_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_away_goals: Mapped[float | None] = mapped_column(Float, nullable=True)

    prob_over_15: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_over_25: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_over_35: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_btts_yes: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    most_likely_score: Mapped[str | None] = mapped_column(String(10), nullable=True)
    favorite: Mapped[str | None] = mapped_column(String(1), nullable=True)
    favorite_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Recommendation flags
    favorite_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_15_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_25_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_35_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    btts_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    winner_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_bzzoiro_models.py -v
# Expected: PASS
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/bzzoiro.py backend/tests/test_bzzoiro_models.py
git commit -m "feat: add bzz_* SQLAlchemy models (7 tables)"
```

---

## Task 3: Alembic migration 017 — create bzz_* tables

**Files:**
- Create: `backend/alembic/versions/017_bzzoiro_tables.py`

- [ ] **Step 1: Create migration file**

```python
# backend/alembic/versions/017_bzzoiro_tables.py
"""bzzoiro tables (7 new tables — primary data layer)

Revision ID: 017
Revises: 016
Create Date: 2026-04-09
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bzz_leagues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("season_id", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bzz_leagues_api_id", "bzz_leagues", ["api_id"])

    op.create_table(
        "bzz_teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(50), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bzz_teams_api_id", "bzz_teams", ["api_id"])

    op.create_table(
        "bzz_players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("position", sa.String(5), nullable=True),
        sa.Column("market_value", sa.BigInteger(), nullable=True),
        sa.Column("current_team_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("national_team_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bzz_players_api_id", "bzz_players", ["api_id"])
    op.create_index("ix_bzz_players_name", "bzz_players", ["name"])
    op.create_index("ix_bzz_players_current_team_api_id", "bzz_players", ["current_team_api_id"])

    op.create_table(
        "bzz_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("league_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("home_team_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("away_team_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("period", sa.String(5), nullable=True),
        sa.Column("current_minute", sa.Integer(), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("home_score_ht", sa.Integer(), nullable=True),
        sa.Column("away_score_ht", sa.Integer(), nullable=True),
        sa.Column("home_xg", sa.Float(), nullable=True),
        sa.Column("away_xg", sa.Float(), nullable=True),
        sa.Column("shotmap", JSONB(), nullable=True),
        sa.Column("incidents", JSONB(), nullable=True),
        sa.Column("momentum", JSONB(), nullable=True),
        sa.Column("average_positions", JSONB(), nullable=True),
        sa.Column("lineups", JSONB(), nullable=True),
        sa.Column("odds_1x2", JSONB(), nullable=True),
        sa.Column("odds_over_under", JSONB(), nullable=True),
        sa.Column("odds_btts", JSONB(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bzz_events_api_id", "bzz_events", ["api_id"])
    op.create_index("ix_bzz_events_event_date", "bzz_events", ["event_date"])
    op.create_index("ix_bzz_events_league_api_id", "bzz_events", ["league_api_id"])

    op.create_table(
        "bzz_player_match_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_players.api_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_events.api_id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_home", sa.Boolean(), nullable=True),
        sa.Column("minutes_played", sa.Integer(), server_default="0"),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("touches", sa.Integer(), server_default="0"),
        sa.Column("goals", sa.Integer(), server_default="0"),
        sa.Column("goal_assist", sa.Integer(), server_default="0"),
        sa.Column("expected_goals", sa.Float(), nullable=True),
        sa.Column("expected_assists", sa.Float(), nullable=True),
        sa.Column("total_shots", sa.Integer(), server_default="0"),
        sa.Column("shots_on_target", sa.Integer(), server_default="0"),
        sa.Column("total_pass", sa.Integer(), server_default="0"),
        sa.Column("accurate_pass", sa.Integer(), server_default="0"),
        sa.Column("key_pass", sa.Integer(), server_default="0"),
        sa.Column("total_long_balls", sa.Integer(), server_default="0"),
        sa.Column("accurate_long_balls", sa.Integer(), server_default="0"),
        sa.Column("total_cross", sa.Integer(), server_default="0"),
        sa.Column("accurate_cross", sa.Integer(), server_default="0"),
        sa.Column("duel_won", sa.Integer(), server_default="0"),
        sa.Column("duel_lost", sa.Integer(), server_default="0"),
        sa.Column("aerial_won", sa.Integer(), server_default="0"),
        sa.Column("aerial_lost", sa.Integer(), server_default="0"),
        sa.Column("total_tackle", sa.Integer(), server_default="0"),
        sa.Column("won_tackle", sa.Integer(), server_default="0"),
        sa.Column("total_clearance", sa.Integer(), server_default="0"),
        sa.Column("interception", sa.Integer(), server_default="0"),
        sa.Column("ball_recovery", sa.Integer(), server_default="0"),
        sa.Column("yellow_card", sa.Integer(), server_default="0"),
        sa.Column("red_card", sa.Integer(), server_default="0"),
        sa.Column("fouls", sa.Integer(), server_default="0"),
        sa.Column("was_fouled", sa.Integer(), server_default="0"),
        sa.Column("dispossessed", sa.Integer(), server_default="0"),
        sa.Column("possession_lost", sa.Integer(), server_default="0"),
        sa.Column("saves", sa.Integer(), server_default="0"),
        sa.Column("goals_conceded", sa.Integer(), server_default="0"),
        # Derived
        sa.Column("shot_accuracy", sa.Float(), nullable=True),
        sa.Column("xg_per_shot", sa.Float(), nullable=True),
        sa.Column("finishing_delta", sa.Float(), nullable=True),
        sa.Column("xa_delta", sa.Float(), nullable=True),
        sa.Column("pass_completion", sa.Float(), nullable=True),
        sa.Column("long_ball_accuracy", sa.Float(), nullable=True),
        sa.Column("cross_accuracy", sa.Float(), nullable=True),
        sa.Column("duel_win_rate", sa.Float(), nullable=True),
        sa.Column("aerial_win_rate", sa.Float(), nullable=True),
        sa.Column("tackle_success_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("player_api_id", "event_api_id", name="uq_bzz_player_match"),
    )
    op.create_index("ix_bzz_pms_player_api_id", "bzz_player_match_stats", ["player_api_id"])
    op.create_index("ix_bzz_pms_event_api_id", "bzz_player_match_stats", ["event_api_id"])

    op.create_table(
        "bzz_player_season_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_players.api_id", ondelete="CASCADE"), nullable=False),
        sa.Column("league_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"), nullable=True),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matches_played", sa.Integer(), server_default="0"),
        sa.Column("minutes_played", sa.Integer(), server_default="0"),
        sa.Column("starts", sa.Integer(), server_default="0"),
        sa.Column("goals", sa.Integer(), server_default="0"),
        sa.Column("goal_assist", sa.Integer(), server_default="0"),
        sa.Column("expected_goals", sa.Float(), server_default="0"),
        sa.Column("expected_assists", sa.Float(), server_default="0"),
        sa.Column("total_shots", sa.Integer(), server_default="0"),
        sa.Column("shots_on_target", sa.Integer(), server_default="0"),
        sa.Column("key_pass", sa.Integer(), server_default="0"),
        sa.Column("total_cross", sa.Integer(), server_default="0"),
        sa.Column("accurate_cross", sa.Integer(), server_default="0"),
        sa.Column("total_pass", sa.Integer(), server_default="0"),
        sa.Column("accurate_pass", sa.Integer(), server_default="0"),
        sa.Column("total_long_balls", sa.Integer(), server_default="0"),
        sa.Column("accurate_long_balls", sa.Integer(), server_default="0"),
        sa.Column("duel_won", sa.Integer(), server_default="0"),
        sa.Column("duel_lost", sa.Integer(), server_default="0"),
        sa.Column("aerial_won", sa.Integer(), server_default="0"),
        sa.Column("aerial_lost", sa.Integer(), server_default="0"),
        sa.Column("total_tackle", sa.Integer(), server_default="0"),
        sa.Column("won_tackle", sa.Integer(), server_default="0"),
        sa.Column("interception", sa.Integer(), server_default="0"),
        sa.Column("ball_recovery", sa.Integer(), server_default="0"),
        sa.Column("yellow_card", sa.Integer(), server_default="0"),
        sa.Column("red_card", sa.Integer(), server_default="0"),
        sa.Column("saves", sa.Integer(), server_default="0"),
        # Per-90
        sa.Column("xg_per_90", sa.Float(), nullable=True),
        sa.Column("xa_per_90", sa.Float(), nullable=True),
        sa.Column("shots_per_90", sa.Float(), nullable=True),
        sa.Column("shots_on_target_per_90", sa.Float(), nullable=True),
        sa.Column("key_pass_per_90", sa.Float(), nullable=True),
        sa.Column("accurate_cross_per_90", sa.Float(), nullable=True),
        sa.Column("recoveries_per_90", sa.Float(), nullable=True),
        sa.Column("tackles_per_90", sa.Float(), nullable=True),
        sa.Column("interceptions_per_90", sa.Float(), nullable=True),
        # Efficiency
        sa.Column("shot_accuracy", sa.Float(), nullable=True),
        sa.Column("xg_per_shot", sa.Float(), nullable=True),
        sa.Column("finishing_delta", sa.Float(), nullable=True),
        sa.Column("xa_delta", sa.Float(), nullable=True),
        sa.Column("pass_completion", sa.Float(), nullable=True),
        sa.Column("long_ball_accuracy", sa.Float(), nullable=True),
        sa.Column("cross_accuracy", sa.Float(), nullable=True),
        sa.Column("duel_win_rate", sa.Float(), nullable=True),
        sa.Column("aerial_win_rate", sa.Float(), nullable=True),
        sa.Column("tackle_success_rate", sa.Float(), nullable=True),
        sa.Column("avg_rating", sa.Float(), nullable=True),
        sa.Column("avg_minutes_per_match", sa.Float(), nullable=True),
        sa.Column("starts_pct", sa.Float(), nullable=True),
        # Form
        sa.Column("form_xg_5", sa.Float(), nullable=True),
        sa.Column("form_rating_5", sa.Float(), nullable=True),
        sa.Column("form_goals_5", sa.Integer(), nullable=True),
        sa.Column("form_assists_5", sa.Integer(), nullable=True),
        sa.Column("rating_trend", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("player_api_id", "league_api_id", "season", name="uq_bzz_player_season"),
    )
    op.create_index("ix_bzz_pss_player_api_id", "bzz_player_season_stats", ["player_api_id"])
    op.create_index("ix_bzz_pss_league_api_id", "bzz_player_season_stats", ["league_api_id"])

    op.create_table(
        "bzz_predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_api_id", sa.Integer(),
                  sa.ForeignKey("bzz_events.api_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("created_at_bzz", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prob_home_win", sa.Float(), nullable=True),
        sa.Column("prob_draw", sa.Float(), nullable=True),
        sa.Column("prob_away_win", sa.Float(), nullable=True),
        sa.Column("predicted_result", sa.String(1), nullable=True),
        sa.Column("expected_home_goals", sa.Float(), nullable=True),
        sa.Column("expected_away_goals", sa.Float(), nullable=True),
        sa.Column("prob_over_15", sa.Float(), nullable=True),
        sa.Column("prob_over_25", sa.Float(), nullable=True),
        sa.Column("prob_over_35", sa.Float(), nullable=True),
        sa.Column("prob_btts_yes", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("most_likely_score", sa.String(10), nullable=True),
        sa.Column("favorite", sa.String(1), nullable=True),
        sa.Column("favorite_prob", sa.Float(), nullable=True),
        sa.Column("favorite_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_15_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_25_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_35_recommend", sa.Boolean(), nullable=True),
        sa.Column("btts_recommend", sa.Boolean(), nullable=True),
        sa.Column("winner_recommend", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bzz_predictions_event_api_id", "bzz_predictions", ["event_api_id"])


def downgrade() -> None:
    op.drop_table("bzz_predictions")
    op.drop_table("bzz_player_season_stats")
    op.drop_table("bzz_player_match_stats")
    op.drop_table("bzz_events")
    op.drop_table("bzz_players")
    op.drop_table("bzz_teams")
    op.drop_table("bzz_leagues")
```

- [ ] **Step 2: Apply migration on VPS**

```bash
# On VPS inside backend container:
docker exec ev0-compose-z5hvqt-backend-1 alembic upgrade head
# Expected: Running upgrade 016 -> 017 ... done
```

- [ ] **Step 3: Verify tables exist**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "\dt bzz_*"
# Expected: 7 rows — bzz_events, bzz_leagues, bzz_player_match_stats,
#           bzz_player_season_stats, bzz_players, bzz_predictions, bzz_teams
```

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/017_bzzoiro_tables.py
git commit -m "feat: migration 017 — create bzz_* tables"
```

---

## Task 4: BzzoiroClient (HTTP + pagination)

**Files:**
- Create: `backend/app/ingestion/bzzoiro/__init__.py`
- Create: `backend/app/ingestion/bzzoiro/client.py`
- Create: `backend/tests/ingestion/bzzoiro/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/ingestion/bzzoiro/test_client.py
from unittest.mock import AsyncMock, patch
import pytest
from app.ingestion.bzzoiro.client import BzzoiroClient


@pytest.mark.asyncio
async def test_get_page_returns_results():
    mock_response = AsyncMock()
    mock_response.raise_for_status = AsyncMock()
    mock_response.json = AsyncMock(return_value={
        "count": 2,
        "next": None,
        "results": [{"id": 1}, {"id": 2}],
    })

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        client = BzzoiroClient(api_key="test-key")
        async with client:
            results = await client.get_all("/api/players/")
    assert results == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_get_all_follows_pagination():
    responses = [
        {"count": 3, "next": "http://x/api/players/?page=2", "results": [{"id": 1}]},
        {"count": 3, "next": None, "results": [{"id": 2}, {"id": 3}]},
    ]
    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        r = AsyncMock()
        r.raise_for_status = AsyncMock()
        r.json = AsyncMock(return_value=responses[call_count])
        call_count += 1
        return r

    with patch("httpx.AsyncClient.get", side_effect=fake_get):
        client = BzzoiroClient(api_key="test-key")
        async with client:
            results = await client.get_all("/api/players/")
    assert len(results) == 3
    assert call_count == 2
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_client.py -v
# Expected: ImportError
```

- [ ] **Step 3: Create `__init__.py` and `client.py`**

```python
# backend/app/ingestion/bzzoiro/__init__.py
```

```python
# backend/app/ingestion/bzzoiro/client.py
"""Bzzoiro Sports Data API — authenticated async HTTP client with pagination."""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://sports.bzzoiro.com"
DEFAULT_TIMEOUT = 30.0


class BzzoiroClient:
    """Async HTTP client for the Bzzoiro API.

    Usage:
        async with BzzoiroClient(api_key="...") as client:
            leagues = await client.get_all("/api/leagues/")
    """

    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BzzoiroClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Token {self._api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    async def get_page(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch a single paginated page. Returns the raw JSON dict."""
        assert self._client, "Use BzzoiroClient as async context manager"
        response = await self._client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 500,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated endpoint and return combined results list."""
        all_results: list[dict[str, Any]] = []
        next_url: str | None = path
        page_params = dict(params or {})
        pages = 0

        while next_url and pages < max_pages:
            data = await self.get_page(next_url, page_params)
            results = data.get("results") or data  # handle both paginated and list responses
            if isinstance(results, list):
                all_results.extend(results)
            raw_next = data.get("next")
            if raw_next:
                # Strip base URL so we always pass a path
                next_url = raw_next.replace(self._base_url, "")
                page_params = {}  # next URL already has params baked in
            else:
                next_url = None
            pages += 1

        return all_results
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_client.py -v
# Expected: 2 PASS
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/bzzoiro/ backend/tests/ingestion/bzzoiro/
git commit -m "feat: BzzoiroClient — async HTTP client with pagination"
```

---

## Task 5: Sync leagues and teams

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_reference.py`

- [ ] **Step 1: Create sync_reference.py**

```python
# backend/app/ingestion/bzzoiro/sync_reference.py
"""Sync bzz_leagues and bzz_teams from Bzzoiro API."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzLeague, BzzTeam

logger = logging.getLogger(__name__)


async def sync_leagues(session: AsyncSession, client: BzzoiroClient) -> int:
    """Upsert all leagues. Returns count synced."""
    rows = await client.get_all("/api/leagues/")
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        stmt = pg_insert(BzzLeague).values(
            api_id=row["api_id"],
            name=row.get("name", ""),
            country=row.get("country"),
            season_id=row.get("season_id"),
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={"name": row.get("name", ""), "country": row.get("country"),
                  "season_id": row.get("season_id"), "synced_at": now},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    logger.info("Synced %d leagues", count)
    return count


async def sync_teams(session: AsyncSession, client: BzzoiroClient) -> int:
    """Upsert all teams. Returns count synced."""
    rows = await client.get_all("/api/teams/")
    now = datetime.now(UTC)
    count = 0
    for row in rows:
        stmt = pg_insert(BzzTeam).values(
            api_id=row["api_id"],
            name=row.get("name", ""),
            short_name=row.get("short_name"),
            country=row.get("country"),
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={"name": row.get("name", ""), "short_name": row.get("short_name"),
                  "country": row.get("country"), "synced_at": now},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    logger.info("Synced %d teams", count)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_reference.py
git commit -m "feat: sync_reference — upsert bzz_leagues + bzz_teams"
```

---

## Task 6: Sync players

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_players.py`

- [ ] **Step 1: Create sync_players.py**

```python
# backend/app/ingestion/bzzoiro/sync_players.py
"""Sync bzz_players (profiles + current team) from Bzzoiro API."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, date

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzPlayer

logger = logging.getLogger(__name__)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


async def sync_players(session: AsyncSession, client: BzzoiroClient) -> int:
    """Upsert all player profiles. Returns count synced."""
    rows = await client.get_all("/api/players/")
    now = datetime.now(UTC)
    count = 0

    for row in rows:
        team = row.get("current_team") or {}
        nat_team = row.get("national_team") or {}

        stmt = pg_insert(BzzPlayer).values(
            api_id=row["api_id"],
            name=row.get("name", ""),
            short_name=row.get("short_name"),
            nationality=row.get("nationality"),
            date_of_birth=_parse_date(row.get("date_of_birth")),
            height=row.get("height"),
            jersey_number=row.get("jersey_number"),
            position=row.get("position"),
            market_value=row.get("market_value"),
            current_team_api_id=team.get("api_id"),
            national_team_api_id=nat_team.get("api_id") if nat_team else None,
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={
                "name": row.get("name", ""),
                "short_name": row.get("short_name"),
                "nationality": row.get("nationality"),
                "date_of_birth": _parse_date(row.get("date_of_birth")),
                "height": row.get("height"),
                "jersey_number": row.get("jersey_number"),
                "position": row.get("position"),
                "market_value": row.get("market_value"),
                "current_team_api_id": team.get("api_id"),
                "national_team_api_id": nat_team.get("api_id") if nat_team else None,
                "synced_at": now,
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d players", count)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_players.py
git commit -m "feat: sync_players — upsert bzz_players from Bzzoiro"
```

---

## Task 7: Sync events (matches, odds, lineups, xG)

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_events.py`

- [ ] **Step 1: Create sync_events.py**

```python
# backend/app/ingestion/bzzoiro/sync_events.py
"""Sync bzz_events — matches with scores, odds, lineups, shotmap, momentum, xG."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent

logger = logging.getLogger(__name__)


def _extract_odds(event: dict[str, Any]) -> tuple[dict | None, dict | None, dict | None]:
    """Extract 1x2, over/under, and BTTS odds from event response."""
    odds = event.get("odds") or {}
    odds_1x2 = odds.get("1x2") or odds.get("home_draw_away")
    odds_ou = odds.get("over_under") or odds.get("totals")
    odds_btts = odds.get("btts") or odds.get("both_teams_to_score")
    return odds_1x2, odds_ou, odds_btts


async def sync_events(
    session: AsyncSession,
    client: BzzoiroClient,
    days_back: int = 7,
    days_forward: int = 14,
) -> int:
    """Sync events within [now - days_back, now + days_forward]. Returns count synced."""
    now = datetime.now(UTC)
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%d")

    rows = await client.get_all(
        "/api/events/",
        params={"date_from": date_from, "date_to": date_to},
    )
    count = 0

    for row in rows:
        league = row.get("league") or {}
        home_team = row.get("home_team_obj") or {}
        away_team = row.get("away_team_obj") or {}
        odds_1x2, odds_ou, odds_btts = _extract_odds(row)

        event_date_raw = row.get("event_date")
        event_date = datetime.fromisoformat(event_date_raw.replace("Z", "+00:00")) if event_date_raw else None

        stmt = pg_insert(BzzEvent).values(
            api_id=row["api_id"],
            league_api_id=league.get("api_id"),
            home_team_api_id=home_team.get("api_id"),
            away_team_api_id=away_team.get("api_id"),
            event_date=event_date,
            status=row.get("status"),
            period=row.get("period"),
            current_minute=row.get("current_minute"),
            round_number=row.get("round_number"),
            home_score=row.get("home_score"),
            away_score=row.get("away_score"),
            home_score_ht=row.get("home_score_ht"),
            away_score_ht=row.get("away_score_ht"),
            home_xg=row.get("actual_home_xg") or row.get("home_xg"),
            away_xg=row.get("actual_away_xg") or row.get("away_xg"),
            shotmap=row.get("shotmap"),
            incidents=row.get("incidents"),
            momentum=row.get("momentum"),
            average_positions=row.get("average_positions"),
            lineups=row.get("lineups"),
            odds_1x2=odds_1x2,
            odds_over_under=odds_ou,
            odds_btts=odds_btts,
            synced_at=now,
        ).on_conflict_do_update(
            index_elements=["api_id"],
            set_={
                "status": row.get("status"),
                "period": row.get("period"),
                "current_minute": row.get("current_minute"),
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "home_score_ht": row.get("home_score_ht"),
                "away_score_ht": row.get("away_score_ht"),
                "home_xg": row.get("actual_home_xg") or row.get("home_xg"),
                "away_xg": row.get("actual_away_xg") or row.get("away_xg"),
                "shotmap": row.get("shotmap"),
                "incidents": row.get("incidents"),
                "momentum": row.get("momentum"),
                "average_positions": row.get("average_positions"),
                "lineups": row.get("lineups"),
                "odds_1x2": odds_1x2,
                "odds_over_under": odds_ou,
                "odds_btts": odds_btts,
                "synced_at": now,
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d events (from %s to %s)", count, date_from, date_to)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_events.py
git commit -m "feat: sync_events — upsert bzz_events with odds, lineups, xG"
```

---

## Task 8: Sync player match stats + derived metrics

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_player_stats.py`
- Create: `backend/tests/ingestion/bzzoiro/test_sync_player_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/ingestion/bzzoiro/test_sync_player_stats.py
from app.ingestion.bzzoiro.sync_player_stats import compute_derived_metrics


def test_derived_metrics_shot_accuracy():
    row = {"total_shots": 4, "shots_on_target": 2, "expected_goals": 0.8,
           "goals": 1, "goal_assist": 0, "expected_assists": 0.3,
           "total_pass": 40, "accurate_pass": 34, "total_long_balls": 5,
           "accurate_long_balls": 3, "total_cross": 3, "accurate_cross": 2,
           "duel_won": 6, "duel_lost": 4, "aerial_won": 3, "aerial_lost": 1,
           "total_tackle": 5, "won_tackle": 4}
    metrics = compute_derived_metrics(row)
    assert metrics["shot_accuracy"] == 0.5
    assert metrics["xg_per_shot"] == 0.2
    assert abs(metrics["finishing_delta"] - 0.2) < 0.001   # 1 - 0.8
    assert abs(metrics["xa_delta"] - (-0.3)) < 0.001        # 0 - 0.3
    assert abs(metrics["pass_completion"] - 0.85) < 0.001
    assert abs(metrics["duel_win_rate"] - 0.6) < 0.001
    assert abs(metrics["aerial_win_rate"] - 0.75) < 0.001
    assert abs(metrics["tackle_success_rate"] - 0.8) < 0.001


def test_derived_metrics_zero_denominator():
    row = {"total_shots": 0, "shots_on_target": 0, "expected_goals": None,
           "goals": 0, "goal_assist": 0, "expected_assists": None,
           "total_pass": 0, "accurate_pass": 0, "total_long_balls": 0,
           "accurate_long_balls": 0, "total_cross": 0, "accurate_cross": 0,
           "duel_won": 0, "duel_lost": 0, "aerial_won": 0, "aerial_lost": 0,
           "total_tackle": 0, "won_tackle": 0}
    metrics = compute_derived_metrics(row)
    # All None when denominator is 0
    assert metrics["shot_accuracy"] is None
    assert metrics["xg_per_shot"] is None
    assert metrics["duel_win_rate"] is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_player_stats.py -v
# Expected: ImportError
```

- [ ] **Step 3: Create sync_player_stats.py**

```python
# backend/app/ingestion/bzzoiro/sync_player_stats.py
"""Sync bzz_player_match_stats — per-match stats with computed derived metrics."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzPlayerMatchStat

logger = logging.getLogger(__name__)


def _safe_div(numerator: float | int, denominator: float | int) -> float | None:
    """Divide, return None if denominator is 0."""
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def compute_derived_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    """Compute all derived efficiency metrics from raw Bzzoiro player stat fields."""
    shots = row.get("total_shots") or 0
    xg = row.get("expected_goals")
    goals = row.get("goals") or 0
    xa = row.get("expected_assists")
    assists = row.get("goal_assist") or 0

    return {
        "shot_accuracy":       _safe_div(row.get("shots_on_target") or 0, shots),
        "xg_per_shot":         _safe_div(xg or 0, shots),
        "finishing_delta":     round(goals - (xg or 0), 4) if xg is not None else None,
        "xa_delta":            round(assists - (xa or 0), 4) if xa is not None else None,
        "pass_completion":     _safe_div(row.get("accurate_pass") or 0, row.get("total_pass") or 0),
        "long_ball_accuracy":  _safe_div(row.get("accurate_long_balls") or 0, row.get("total_long_balls") or 0),
        "cross_accuracy":      _safe_div(row.get("accurate_cross") or 0, row.get("total_cross") or 0),
        "duel_win_rate":       _safe_div(
            row.get("duel_won") or 0,
            (row.get("duel_won") or 0) + (row.get("duel_lost") or 0),
        ),
        "aerial_win_rate":     _safe_div(
            row.get("aerial_won") or 0,
            (row.get("aerial_won") or 0) + (row.get("aerial_lost") or 0),
        ),
        "tackle_success_rate": _safe_div(row.get("won_tackle") or 0, row.get("total_tackle") or 0),
    }


async def sync_player_stats_for_event(
    session: AsyncSession,
    client: BzzoiroClient,
    event_api_id: int,
) -> int:
    """Sync all player stats for a single event. Returns count inserted/updated."""
    rows = await client.get_all("/api/player-stats/", params={"event": event_api_id})
    count = 0

    for row in rows:
        player = row.get("player") or {}
        team = row.get("team") or {}
        event = row.get("event") or {}
        derived = compute_derived_metrics(row)

        stmt = pg_insert(BzzPlayerMatchStat).values(
            player_api_id=player.get("api_id") or row.get("player_api_id"),
            event_api_id=event.get("api_id") or event_api_id,
            team_api_id=team.get("api_id"),
            is_home=row.get("is_home"),
            minutes_played=row.get("minutes_played") or 0,
            rating=row.get("rating"),
            touches=row.get("touches") or 0,
            goals=row.get("goals") or 0,
            goal_assist=row.get("goal_assist") or 0,
            expected_goals=row.get("expected_goals"),
            expected_assists=row.get("expected_assists"),
            total_shots=row.get("total_shots") or 0,
            shots_on_target=row.get("shots_on_target") or 0,
            total_pass=row.get("total_pass") or 0,
            accurate_pass=row.get("accurate_pass") or 0,
            key_pass=row.get("key_pass") or 0,
            total_long_balls=row.get("total_long_balls") or 0,
            accurate_long_balls=row.get("accurate_long_balls") or 0,
            total_cross=row.get("total_cross") or 0,
            accurate_cross=row.get("accurate_cross") or 0,
            duel_won=row.get("duel_won") or 0,
            duel_lost=row.get("duel_lost") or 0,
            aerial_won=row.get("aerial_won") or 0,
            aerial_lost=row.get("aerial_lost") or 0,
            total_tackle=row.get("total_tackle") or 0,
            won_tackle=row.get("won_tackle") or 0,
            total_clearance=row.get("total_clearance") or 0,
            interception=row.get("interception") or 0,
            ball_recovery=row.get("ball_recovery") or 0,
            yellow_card=row.get("yellow_card") or 0,
            red_card=row.get("red_card") or 0,
            fouls=row.get("fouls") or 0,
            was_fouled=row.get("was_fouled") or 0,
            dispossessed=row.get("dispossessed") or 0,
            possession_lost=row.get("possession_lost") or 0,
            saves=row.get("saves") or 0,
            goals_conceded=row.get("goals_conceded") or 0,
            **derived,
        ).on_conflict_do_update(
            constraint="uq_bzz_player_match",
            set_={
                "minutes_played": row.get("minutes_played") or 0,
                "rating": row.get("rating"),
                "goals": row.get("goals") or 0,
                "goal_assist": row.get("goal_assist") or 0,
                "expected_goals": row.get("expected_goals"),
                "expected_assists": row.get("expected_assists"),
                "total_shots": row.get("total_shots") or 0,
                "shots_on_target": row.get("shots_on_target") or 0,
                "key_pass": row.get("key_pass") or 0,
                "total_cross": row.get("total_cross") or 0,
                "accurate_cross": row.get("accurate_cross") or 0,
                "total_pass": row.get("total_pass") or 0,
                "accurate_pass": row.get("accurate_pass") or 0,
                "duel_won": row.get("duel_won") or 0,
                "duel_lost": row.get("duel_lost") or 0,
                "aerial_won": row.get("aerial_won") or 0,
                "aerial_lost": row.get("aerial_lost") or 0,
                "total_tackle": row.get("total_tackle") or 0,
                "won_tackle": row.get("won_tackle") or 0,
                "interception": row.get("interception") or 0,
                "ball_recovery": row.get("ball_recovery") or 0,
                **derived,
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    return count


async def sync_player_stats_batch(
    session: AsyncSession,
    client: BzzoiroClient,
    event_api_ids: list[int],
) -> int:
    """Sync player stats for a batch of events. Returns total rows synced."""
    total = 0
    for event_id in event_api_ids:
        try:
            n = await sync_player_stats_for_event(session, client, event_id)
            total += n
        except Exception as exc:
            logger.warning("Failed to sync stats for event %d: %s", event_id, exc)
    logger.info("Synced player stats for %d events (%d rows total)", len(event_api_ids), total)
    return total
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_player_stats.py -v
# Expected: 2 PASS
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_player_stats.py \
        backend/tests/ingestion/bzzoiro/test_sync_player_stats.py
git commit -m "feat: sync_player_stats — match-level stats + derived metrics"
```

---

## Task 9: Season aggregation

**Files:**
- Create: `backend/app/ingestion/bzzoiro/aggregate.py`
- Create: `backend/tests/ingestion/bzzoiro/test_aggregate.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/ingestion/bzzoiro/test_aggregate.py
from app.ingestion.bzzoiro.aggregate import compute_per90, compute_form


def test_per90_basic():
    assert compute_per90(9.0, 270) == 3.0   # 9 goals / 270 min × 90 = 3.0


def test_per90_zero_minutes():
    assert compute_per90(5.0, 0) is None


def test_form_last5_returns_sum_and_avg():
    # 7 match xG values, last 5 = [0.1, 0.2, 0.3, 0.4, 0.5]
    vals = [0.5, 0.4, 0.3, 0.2, 0.1, 0.0, 0.0]
    xg_sum, rating_avg = compute_form(xg_vals=vals, rating_vals=None)
    assert abs(xg_sum - 1.5) < 0.001
    assert rating_avg is None


def test_form_fewer_than_5_matches():
    vals = [0.3, 0.2]
    xg_sum, _ = compute_form(xg_vals=vals, rating_vals=None)
    assert abs(xg_sum - 0.5) < 0.001
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_aggregate.py -v
```

- [ ] **Step 3: Create aggregate.py**

```python
# backend/app/ingestion/bzzoiro/aggregate.py
"""Compute bzz_player_season_stats by aggregating bzz_player_match_stats."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat, BzzPlayerSeasonStat

logger = logging.getLogger(__name__)

CURRENT_SEASON = "2025-2026"


def compute_per90(value: float, minutes: int) -> float | None:
    """Return per-90-minute rate, or None if minutes == 0."""
    if not minutes:
        return None
    return round((value / minutes) * 90, 4)


def compute_form(
    xg_vals: list[float],
    rating_vals: list[float | None] | None,
    n: int = 5,
) -> tuple[float, float | None]:
    """Return (sum_xg_last_n, avg_rating_last_n). Uses first n items (most recent first)."""
    last = xg_vals[:n]
    xg_sum = round(sum(last), 4) if last else 0.0

    rating_avg: float | None = None
    if rating_vals:
        ratings = [r for r in rating_vals[:n] if r is not None]
        if ratings:
            rating_avg = round(sum(ratings) / len(ratings), 3)

    return xg_sum, rating_avg


async def aggregate_season_stats(
    session: AsyncSession,
    season: str = CURRENT_SEASON,
) -> int:
    """
    Recompute bzz_player_season_stats from bzz_player_match_stats.

    Groups by player_api_id + league_api_id.
    Returns count of season rows upserted.
    """
    now = datetime.now(UTC)

    # Aggregate totals per (player, league) via SQL for efficiency
    agg_stmt = text("""
        SELECT
            pms.player_api_id,
            e.league_api_id,
            COUNT(*)                            AS matches_played,
            COALESCE(SUM(pms.minutes_played), 0)   AS minutes_played,
            COUNT(*) FILTER (WHERE pms.minutes_played >= 60) AS starts,
            COALESCE(SUM(pms.goals), 0)            AS goals,
            COALESCE(SUM(pms.goal_assist), 0)      AS goal_assist,
            COALESCE(SUM(pms.expected_goals), 0)   AS expected_goals,
            COALESCE(SUM(pms.expected_assists), 0) AS expected_assists,
            COALESCE(SUM(pms.total_shots), 0)      AS total_shots,
            COALESCE(SUM(pms.shots_on_target), 0)  AS shots_on_target,
            COALESCE(SUM(pms.key_pass), 0)         AS key_pass,
            COALESCE(SUM(pms.total_cross), 0)      AS total_cross,
            COALESCE(SUM(pms.accurate_cross), 0)   AS accurate_cross,
            COALESCE(SUM(pms.total_pass), 0)       AS total_pass,
            COALESCE(SUM(pms.accurate_pass), 0)    AS accurate_pass,
            COALESCE(SUM(pms.total_long_balls), 0) AS total_long_balls,
            COALESCE(SUM(pms.accurate_long_balls), 0) AS accurate_long_balls,
            COALESCE(SUM(pms.duel_won), 0)         AS duel_won,
            COALESCE(SUM(pms.duel_lost), 0)        AS duel_lost,
            COALESCE(SUM(pms.aerial_won), 0)       AS aerial_won,
            COALESCE(SUM(pms.aerial_lost), 0)      AS aerial_lost,
            COALESCE(SUM(pms.total_tackle), 0)     AS total_tackle,
            COALESCE(SUM(pms.won_tackle), 0)       AS won_tackle,
            COALESCE(SUM(pms.interception), 0)     AS interception,
            COALESCE(SUM(pms.ball_recovery), 0)    AS ball_recovery,
            COALESCE(SUM(pms.yellow_card), 0)      AS yellow_card,
            COALESCE(SUM(pms.red_card), 0)         AS red_card,
            COALESCE(SUM(pms.saves), 0)            AS saves,
            AVG(pms.rating) FILTER (WHERE pms.rating IS NOT NULL) AS avg_rating
        FROM bzz_player_match_stats pms
        JOIN bzz_events e ON e.api_id = pms.event_api_id
        GROUP BY pms.player_api_id, e.league_api_id
    """)

    result = await session.execute(agg_stmt)
    rows = result.mappings().all()
    count = 0

    for row in rows:
        mins = int(row["minutes_played"])
        shots = int(row["total_shots"])
        xg = float(row["expected_goals"])
        goals = int(row["goals"])
        xa = float(row["expected_assists"])
        assists = int(row["goal_assist"])
        passes = int(row["total_pass"])
        acc_passes = int(row["accurate_pass"])
        long_balls = int(row["total_long_balls"])
        acc_long = int(row["accurate_long_balls"])
        crosses = int(row["total_cross"])
        acc_crosses = int(row["accurate_cross"])
        duels_total = int(row["duel_won"]) + int(row["duel_lost"])
        aerials_total = int(row["aerial_won"]) + int(row["aerial_lost"])
        tackles = int(row["total_tackle"])
        matches = int(row["matches_played"])

        def _div(a: float, b: float) -> float | None:
            return round(a / b, 4) if b else None

        stmt = pg_insert(BzzPlayerSeasonStat).values(
            player_api_id=row["player_api_id"],
            league_api_id=row["league_api_id"],
            season=season,
            as_of_utc=now,
            matches_played=matches,
            minutes_played=mins,
            starts=int(row["starts"]),
            goals=goals,
            goal_assist=assists,
            expected_goals=xg,
            expected_assists=xa,
            total_shots=shots,
            shots_on_target=int(row["shots_on_target"]),
            key_pass=int(row["key_pass"]),
            total_cross=crosses,
            accurate_cross=int(row["accurate_cross"]),
            total_pass=passes,
            accurate_pass=acc_passes,
            total_long_balls=long_balls,
            accurate_long_balls=acc_long,
            duel_won=int(row["duel_won"]),
            duel_lost=int(row["duel_lost"]),
            aerial_won=int(row["aerial_won"]),
            aerial_lost=int(row["aerial_lost"]),
            total_tackle=tackles,
            won_tackle=int(row["won_tackle"]),
            interception=int(row["interception"]),
            ball_recovery=int(row["ball_recovery"]),
            yellow_card=int(row["yellow_card"]),
            red_card=int(row["red_card"]),
            saves=int(row["saves"]),
            avg_rating=float(row["avg_rating"]) if row["avg_rating"] else None,
            avg_minutes_per_match=_div(mins, matches),
            starts_pct=_div(int(row["starts"]), matches),
            # Per-90
            xg_per_90=compute_per90(xg, mins),
            xa_per_90=compute_per90(xa, mins),
            shots_per_90=compute_per90(shots, mins),
            shots_on_target_per_90=compute_per90(int(row["shots_on_target"]), mins),
            key_pass_per_90=compute_per90(int(row["key_pass"]), mins),
            accurate_cross_per_90=compute_per90(int(row["accurate_cross"]), mins),
            recoveries_per_90=compute_per90(int(row["ball_recovery"]), mins),
            tackles_per_90=compute_per90(tackles, mins),
            interceptions_per_90=compute_per90(int(row["interception"]), mins),
            # Efficiency
            shot_accuracy=_div(int(row["shots_on_target"]), shots),
            xg_per_shot=_div(xg, shots),
            finishing_delta=round(goals - xg, 4),
            xa_delta=round(assists - xa, 4),
            pass_completion=_div(acc_passes, passes),
            long_ball_accuracy=_div(acc_long, long_balls),
            cross_accuracy=_div(int(row["accurate_cross"]), crosses),
            duel_win_rate=_div(int(row["duel_won"]), duels_total),
            aerial_win_rate=_div(int(row["aerial_won"]), aerials_total),
            tackle_success_rate=_div(int(row["won_tackle"]), tackles),
            # Form will be patched in a separate pass below
            form_xg_5=None,
            form_rating_5=None,
            form_goals_5=None,
            form_assists_5=None,
            rating_trend=None,
        ).on_conflict_do_update(
            constraint="uq_bzz_player_season",
            set_={
                "as_of_utc": now,
                "matches_played": matches,
                "minutes_played": mins,
                "goals": goals,
                "goal_assist": assists,
                "expected_goals": xg,
                "expected_assists": xa,
                "xg_per_90": compute_per90(xg, mins),
                "xa_per_90": compute_per90(xa, mins),
                "shots_on_target_per_90": compute_per90(int(row["shots_on_target"]), mins),
                "key_pass_per_90": compute_per90(int(row["key_pass"]), mins),
                "accurate_cross_per_90": compute_per90(int(row["accurate_cross"]), mins),
                "shot_accuracy": _div(int(row["shots_on_target"]), shots),
                "xg_per_shot": _div(xg, shots),
                "finishing_delta": round(goals - xg, 4),
                "xa_delta": round(assists - xa, 4),
                "pass_completion": _div(acc_passes, passes),
                "duel_win_rate": _div(int(row["duel_won"]), duels_total),
                "avg_rating": float(row["avg_rating"]) if row["avg_rating"] else None,
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()

    # Patch form stats in a second pass
    await _patch_form_stats(session, season, now)

    logger.info("Aggregated %d player season rows for season %s", count, season)
    return count


async def _patch_form_stats(session: AsyncSession, season: str, now: datetime) -> None:
    """Compute rolling 5-match form per player and update bzz_player_season_stats."""
    form_stmt = text("""
        WITH ranked AS (
            SELECT
                pms.player_api_id,
                e.league_api_id,
                pms.expected_goals,
                pms.goals,
                pms.goal_assist,
                pms.rating,
                ROW_NUMBER() OVER (
                    PARTITION BY pms.player_api_id, e.league_api_id
                    ORDER BY e.event_date DESC
                ) AS rn
            FROM bzz_player_match_stats pms
            JOIN bzz_events e ON e.api_id = pms.event_api_id
        ),
        form AS (
            SELECT
                player_api_id,
                league_api_id,
                SUM(expected_goals) FILTER (WHERE rn <= 5)       AS form_xg_5,
                AVG(rating)         FILTER (WHERE rn <= 5 AND rating IS NOT NULL) AS form_rating_5,
                SUM(goals)          FILTER (WHERE rn <= 5)        AS form_goals_5,
                SUM(goal_assist)    FILTER (WHERE rn <= 5)        AS form_assists_5
            FROM ranked
            GROUP BY player_api_id, league_api_id
        )
        UPDATE bzz_player_season_stats pss
        SET
            form_xg_5      = form.form_xg_5,
            form_rating_5  = form.form_rating_5,
            form_goals_5   = form.form_goals_5,
            form_assists_5 = form.form_assists_5,
            rating_trend   = CASE
                WHEN pss.avg_rating IS NOT NULL AND form.form_rating_5 IS NOT NULL
                THEN form.form_rating_5 - pss.avg_rating
                ELSE NULL
            END,
            updated_at = :now
        FROM form
        WHERE pss.player_api_id = form.player_api_id
          AND pss.league_api_id = form.league_api_id
          AND pss.season = :season
    """)
    await session.execute(form_stmt, {"now": now, "season": season})
    await session.commit()
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/ingestion/bzzoiro/test_aggregate.py -v
# Expected: 4 PASS
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/bzzoiro/aggregate.py \
        backend/tests/ingestion/bzzoiro/test_aggregate.py
git commit -m "feat: aggregate — season stats + form from match rows"
```

---

## Task 10: Sync predictions

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_predictions.py`

- [ ] **Step 1: Create sync_predictions.py**

```python
# backend/app/ingestion/bzzoiro/sync_predictions.py
"""Sync bzz_predictions from Bzzoiro ML predictions endpoint."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzPrediction

logger = logging.getLogger(__name__)


async def sync_predictions(
    session: AsyncSession,
    client: BzzoiroClient,
    days_forward: int = 3,
) -> int:
    """Sync ML predictions for upcoming matches. Returns count synced."""
    now = datetime.now(UTC)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%d")

    rows = await client.get_all(
        "/api/predictions/",
        params={"date_from": date_from, "date_to": date_to, "upcoming": "true"},
    )
    count = 0

    for row in rows:
        event = row.get("event") or {}
        event_api_id = event.get("api_id") or row.get("event_api_id")
        if not event_api_id:
            continue

        created_at_raw = row.get("created_at")
        created_at = (
            datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            if created_at_raw else None
        )

        stmt = pg_insert(BzzPrediction).values(
            event_api_id=event_api_id,
            created_at_bzz=created_at,
            prob_home_win=row.get("prob_home_win"),
            prob_draw=row.get("prob_draw"),
            prob_away_win=row.get("prob_away_win"),
            predicted_result=row.get("predicted_result"),
            expected_home_goals=row.get("expected_home_goals"),
            expected_away_goals=row.get("expected_away_goals"),
            prob_over_15=row.get("prob_over_15"),
            prob_over_25=row.get("prob_over_25"),
            prob_over_35=row.get("prob_over_35"),
            prob_btts_yes=row.get("prob_btts_yes"),
            confidence=row.get("confidence"),
            model_version=row.get("model_version"),
            most_likely_score=row.get("most_likely_score"),
            favorite=row.get("favorite"),
            favorite_prob=row.get("favorite_prob"),
            favorite_recommend=row.get("favorite_recommend"),
            over_15_recommend=row.get("over_15_recommend"),
            over_25_recommend=row.get("over_25_recommend"),
            over_35_recommend=row.get("over_35_recommend"),
            btts_recommend=row.get("btts_recommend"),
            winner_recommend=row.get("winner_recommend"),
        ).on_conflict_do_update(
            index_elements=["event_api_id"],
            set_={
                "prob_home_win": row.get("prob_home_win"),
                "prob_draw": row.get("prob_draw"),
                "prob_away_win": row.get("prob_away_win"),
                "prob_over_25": row.get("prob_over_25"),
                "prob_btts_yes": row.get("prob_btts_yes"),
                "expected_home_goals": row.get("expected_home_goals"),
                "expected_away_goals": row.get("expected_away_goals"),
                "confidence": row.get("confidence"),
                "model_version": row.get("model_version"),
                "most_likely_score": row.get("most_likely_score"),
                "over_25_recommend": row.get("over_25_recommend"),
                "btts_recommend": row.get("btts_recommend"),
            },
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d predictions", count)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_predictions.py
git commit -m "feat: sync_predictions — upsert bzz_predictions"
```

---

## Task 11: Worker — register all Bzzoiro jobs

**Files:**
- Modify: `backend/app/worker.py`

- [ ] **Step 1: Add 5 new job functions to worker.py**

Add after the existing imports and before the scheduler setup:

```python
# ── Bzzoiro jobs ─────────────────────────────────────────────────

async def job_sync_bzzoiro_reference() -> None:
    """Sync leagues + teams (daily at 03:00 UTC)."""
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.sync_reference import sync_leagues, sync_teams
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not set — skipping reference sync")
        return
    async with async_session() as session:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as client:
            await sync_leagues(session, client)
            await sync_teams(session, client)


async def job_sync_bzzoiro_players() -> None:
    """Sync player profiles (daily at 03:30 UTC)."""
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.sync_players import sync_players
    if not settings.bzzoiro_api_key:
        return
    async with async_session() as session:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as client:
            await sync_players(session, client)


async def job_sync_bzzoiro_events() -> None:
    """Sync upcoming + recent events (every 6h)."""
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.sync_events import sync_events
    if not settings.bzzoiro_api_key:
        return
    async with async_session() as session:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as client:
            await sync_events(session, client)


async def job_sync_bzzoiro_player_stats() -> None:
    """Sync player match stats for finished events (every 6h)."""
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats_batch
    from app.models.bzzoiro import BzzEvent
    if not settings.bzzoiro_api_key:
        return
    async with async_session() as session:
        result = await session.execute(
            select(BzzEvent.api_id)
            .where(BzzEvent.status == "finished")
            .order_by(BzzEvent.event_date.desc())
            .limit(50)
        )
        event_ids = [r[0] for r in result.all()]
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as client:
            await sync_player_stats_batch(session, client, event_ids)


async def job_aggregate_bzzoiro_season_stats() -> None:
    """Recompute season stats from match rows (daily at 04:00 UTC)."""
    from app.ingestion.bzzoiro.aggregate import aggregate_season_stats
    async with async_session() as session:
        await aggregate_season_stats(session)


async def job_sync_bzzoiro_predictions() -> None:
    """Sync ML predictions for next 3 days (daily at 07:00 UTC)."""
    from app.ingestion.bzzoiro.client import BzzoiroClient
    from app.ingestion.bzzoiro.sync_predictions import sync_predictions
    if not settings.bzzoiro_api_key:
        return
    async with async_session() as session:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as client:
            await sync_predictions(session, client)
```

- [ ] **Step 2: Register jobs in the scheduler**

In the `start_scheduler()` function, add after existing job registrations:

```python
    # ── Bzzoiro jobs ──────────────────────────────────────────────
    scheduler.add_job(
        job_sync_bzzoiro_reference,
        CronTrigger(hour=3, minute=0),
        id="bzzoiro_reference",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sync_bzzoiro_players,
        CronTrigger(hour=3, minute=30),
        id="bzzoiro_players",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sync_bzzoiro_events,
        IntervalTrigger(hours=6),
        id="bzzoiro_events",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sync_bzzoiro_player_stats,
        IntervalTrigger(hours=6),
        id="bzzoiro_player_stats",
        replace_existing=True,
    )
    scheduler.add_job(
        job_aggregate_bzzoiro_season_stats,
        CronTrigger(hour=4, minute=0),
        id="bzzoiro_aggregate",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sync_bzzoiro_predictions,
        CronTrigger(hour=7, minute=0),
        id="bzzoiro_predictions",
        replace_existing=True,
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/worker.py
git commit -m "feat: worker — register 6 bzzoiro sync jobs"
```

---

## Task 12: xG resolver + mode toggle

**Files:**
- Create: `backend/app/pricing/xg_resolver.py`
- Create: `backend/tests/pricing/test_xg_resolver.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/pricing/test_xg_resolver.py
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.pricing.xg_resolver import XgMode, resolve_xg_source


@pytest.mark.asyncio
async def test_bzzoiro_mode_returns_event_xg():
    session = AsyncMock()
    mock_event = MagicMock()
    mock_event.home_xg = 1.4
    mock_event.away_xg = 0.9
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_event)))

    home_xg, away_xg, label = await resolve_xg_source(
        session=session,
        event_api_id=42,
        global_mode=XgMode.BZZOIRO,
        model_home_xg=1.1,
        model_away_xg=0.8,
    )
    assert home_xg == 1.4
    assert away_xg == 0.9
    assert label == "bzzoiro"


@pytest.mark.asyncio
async def test_model_mode_ignores_event():
    session = AsyncMock()
    home_xg, away_xg, label = await resolve_xg_source(
        session=session,
        event_api_id=42,
        global_mode=XgMode.MODEL,
        model_home_xg=1.1,
        model_away_xg=0.8,
    )
    assert home_xg == 1.1
    assert away_xg == 0.8
    assert label == "model"


@pytest.mark.asyncio
async def test_bzzoiro_mode_falls_back_if_no_xg():
    session = AsyncMock()
    mock_event = MagicMock()
    mock_event.home_xg = None   # Bzzoiro has no xG yet
    mock_event.away_xg = None
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_event)))

    _, _, label = await resolve_xg_source(
        session=session,
        event_api_id=42,
        global_mode=XgMode.BZZOIRO,
        model_home_xg=1.1,
        model_away_xg=0.8,
    )
    assert label == "model"  # silent fallback
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/pricing/test_xg_resolver.py -v
```

- [ ] **Step 3: Create xg_resolver.py**

```python
# backend/app/pricing/xg_resolver.py
"""xG source resolver — switches between Bzzoiro API xG and the internal model.

Global mode is stored in user_settings with key 'xg_source'.
Per-match fallback: if Bzzoiro mode selected but event has no xG, silently use model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent

XgLabel = Literal["bzzoiro", "model"]

# CSS colors used by the frontend badge
XG_BADGE_COLORS: dict[XgLabel, str] = {
    "bzzoiro": "#3B82F6",  # blue
    "model":   "#F97316",  # orange
}


class XgMode(StrEnum):
    BZZOIRO = "bzzoiro"
    MODEL = "model"


async def resolve_xg_source(
    session: AsyncSession,
    event_api_id: int | None,
    global_mode: XgMode | str,
    model_home_xg: float,
    model_away_xg: float,
) -> tuple[float, float, XgLabel]:
    """
    Return (home_xg, away_xg, label).

    If global_mode == bzzoiro and the event has actual xG → use it.
    Otherwise → model values, regardless of mode.
    """
    mode = XgMode(global_mode) if isinstance(global_mode, str) else global_mode

    if mode == XgMode.BZZOIRO and event_api_id is not None:
        result = await session.execute(
            select(BzzEvent).where(BzzEvent.api_id == event_api_id)
        )
        event = result.scalar_one_or_none()
        if event and event.home_xg is not None and event.away_xg is not None:
            return event.home_xg, event.away_xg, "bzzoiro"

    return model_home_xg, model_away_xg, "model"


async def get_global_xg_mode(session: AsyncSession) -> XgMode:
    """Read current xg_source from user_settings, default to bzzoiro."""
    from app.models.settings import UserSettings
    result = await session.execute(
        select(UserSettings).where(UserSettings.key == "xg_source")
    )
    row = result.scalar_one_or_none()
    if row and row.value in (XgMode.BZZOIRO, XgMode.MODEL):
        return XgMode(row.value)
    return XgMode.BZZOIRO
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/pricing/test_xg_resolver.py -v
# Expected: 3 PASS
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/pricing/xg_resolver.py backend/tests/pricing/test_xg_resolver.py
git commit -m "feat: xg_resolver — bzzoiro/model mode with per-match fallback"
```

---

## Task 13: Update pricing engine — goalscorer + assist multipliers

**Files:**
- Modify: `backend/app/pricing/goalscorer.py`
- Modify: `backend/app/pricing/assist.py`
- Modify: `backend/app/pricing/team_xg.py`

- [ ] **Step 1: Add Bzzoiro quality multiplier to goalscorer.py**

After the existing `QUALITY_WEIGHTS` block, add:

```python
# Bzzoiro-native quality multiplier weights (must sum to 1.0)
QUALITY_WEIGHTS_BZZ = {
    "shot_accuracy": 0.40,  # SoT / total shots        [Bzzoiro]
    "xg_per_shot":   0.35,  # xG / total shots          [Bzzoiro]
    "rating_norm":   0.25,  # sofascore-style rating/10 [Bzzoiro]
}

LEAGUE_AVG_BZZ_GOALSCORER = {
    "shot_accuracy": 0.38,   # avg SoT rate across outfield players
    "xg_per_shot":   0.10,   # avg xG per shot
    "rating_norm":   0.69,   # avg rating / 10
}


def calculate_quality_multiplier_bzzoiro(
    shot_accuracy: float = 0.0,
    xg_per_shot: float = 0.0,
    rating: float | None = None,
    league_averages: dict[str, float] | None = None,
) -> tuple[float, dict]:
    """
    Bzzoiro-native quality multiplier.
    Replaces SOT+TAP+xGChain with shot_accuracy + xg_per_shot + rating_norm.
    """
    avgs = {**LEAGUE_AVG_BZZ_GOALSCORER, **(league_averages or {})}
    rating_norm = (rating / 10.0) if rating is not None else avgs["rating_norm"]

    def norm(val: float, key: str) -> float:
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 1.0

    raw_vals = {
        "shot_accuracy": shot_accuracy,
        "xg_per_shot":   xg_per_shot,
        "rating_norm":   rating_norm,
    }
    components = {k: norm(v, k) for k, v in raw_vals.items()}
    raw_mult = sum(components[k] * QUALITY_WEIGHTS_BZZ[k] for k in QUALITY_WEIGHTS_BZZ)
    multiplier = max(CLAMP_MULTIPLIER_MIN, min(raw_mult, CLAMP_MULTIPLIER_MAX))

    breakdown = {
        k: {
            "raw": round(raw_vals[k], 4),
            "league_avg": round(avgs[k], 4),
            "normalized": round(components[k], 3),
            "weight": QUALITY_WEIGHTS_BZZ[k],
        }
        for k in QUALITY_WEIGHTS_BZZ
    }
    return multiplier, breakdown
```

- [ ] **Step 2: Add Bzzoiro creation multiplier to assist.py**

After the existing `CREATION_WEIGHTS_DEFAULT` block, add:

```python
CREATION_WEIGHTS_BZZ: dict[str, float] = {
    "key_pass_per_90":      0.40,   # key passes per 90    [Bzzoiro]
    "xa_per_90":            0.35,   # xA per 90             [Bzzoiro]
    "accurate_cross_per_90": 0.25,  # accurate crosses/90  [Bzzoiro]
}

LEAGUE_AVG_BZZ_ASSIST: dict[str, float] = {
    "key_pass_per_90":      1.20,
    "xa_per_90":            0.18,
    "accurate_cross_per_90": 0.55,
}


def calculate_creation_multiplier_bzzoiro(
    key_pass_per_90: float = 0.0,
    xa_per_90: float = 0.0,
    accurate_cross_per_90: float = 0.0,
    league_averages: dict[str, float] | None = None,
) -> tuple[float, dict]:
    """
    Bzzoiro-native creation multiplier.
    Replaces BCC+xGChain+Crosses+TB with key_pass + xA + accurate_cross per 90.
    """
    avgs = {**LEAGUE_AVG_BZZ_ASSIST, **(league_averages or {})}

    def norm(val: float, key: str) -> float:
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 1.0

    raw_vals = {
        "key_pass_per_90":      key_pass_per_90,
        "xa_per_90":            xa_per_90,
        "accurate_cross_per_90": accurate_cross_per_90,
    }
    components = {k: norm(v, k) for k, v in raw_vals.items()}
    raw_mult = sum(components[k] * CREATION_WEIGHTS_BZZ[k] for k in CREATION_WEIGHTS_BZZ)
    multiplier = max(0.5, min(raw_mult, 2.0))

    breakdown = {
        k: {
            "raw": round(raw_vals[k], 4),
            "league_avg": round(avgs[k], 4),
            "normalized": round(components[k], 3),
            "weight": CREATION_WEIGHTS_BZZ[k],
        }
        for k in CREATION_WEIGHTS_BZZ
    }
    return multiplier, breakdown
```

- [ ] **Step 3: Update `_load_team_players` in team_xg.py**

Replace the entire `_load_team_players` function (lines 395-468) with:

```python
async def _load_team_players(db: AsyncSession, team_api_id: int) -> list[dict[str, Any]]:
    """Load latest season stats from bzz_player_season_stats for a team."""
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat

    res = await db.execute(
        select(BzzPlayerSeasonStat, BzzPlayer.name, BzzPlayer.position)
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .where(BzzPlayer.current_team_api_id == team_api_id)
        .where(BzzPlayerSeasonStat.season == CURRENT_SEASON)
    )

    players = []
    for row in res.all():
        ss, name, raw_pos = row[0], row[1], row[2]
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue
        xg = ss.expected_goals or 0.0
        xa = ss.expected_assists or 0.0
        goals = ss.goals or 0
        npxg = xg  # Bzzoiro has no explicit npxG; use xG as anchor
        mins = ss.minutes_played or 0
        matches = ss.matches_played or 0

        players.append({
            "player_id": ss.player_api_id,
            "player_name": name,
            "position": position,
            "matches_played": matches,
            "minutes_played": mins,
            "goals": goals,
            "xg": xg,
            "npxg": npxg,
            "xa": xa,
            # Pricing engine anchors (per-90)
            "npxg_per_90": ss.xg_per_90 or 0.0,
            "xa_per_90": ss.xa_per_90 or 0.0,
            # Bzzoiro quality multiplier inputs
            "shot_accuracy": ss.shot_accuracy or 0.0,
            "xg_per_shot": ss.xg_per_shot or 0.0,
            "avg_rating": ss.avg_rating,
            # Bzzoiro creation multiplier inputs
            "key_pass_per_90": ss.key_pass_per_90 or 0.0,
            "accurate_cross_per_90": ss.accurate_cross_per_90 or 0.0,
            # Form (replaces decay-based form_factor)
            "form_xg_5": ss.form_xg_5,
            "rating_trend": ss.rating_trend,
            # Finishing correction
            "finishing_delta": ss.finishing_delta or 0.0,
        })
    return players
```

Also update `compute_player_shares` to read the new field names. Replace `sot_per_90`, `tap_per_90`, `bcc_per_90`, `accurate_crosses_per_90`, `through_balls_per_90`, `xgchain_per_90` assignments with:

```python
        shares.append(PlayerShare(
            player_id=p["player_id"],
            player_name=p["player_name"],
            team=team,
            position=pos,
            npxg_share=npxg_share,
            xa_share=xa_share,
            expected_minutes=exp_mins,
            matches_played=matches,
            npxg_per_90=p.get("npxg_per_90", 0.0) or 0.0,
            xa_per_90=p.get("xa_per_90", 0.0) or 0.0,
            xgchain_per_90=0.0,               # not used in bzzoiro path
            conversion_rate=conversion_rate,
            sot_per_90=p.get("shot_accuracy", 0.0) or 0.0,   # repurposed slot
            tap_per_90=p.get("xg_per_shot", 0.0) or 0.0,     # repurposed slot
            bcc_per_90=p.get("key_pass_per_90", 0.0) or 0.0, # repurposed slot
            accurate_crosses_per_90=p.get("accurate_cross_per_90", 0.0) or 0.0,
            through_balls_per_90=0.0,          # not used in bzzoiro path
        ))
```

Also update `allocate_player` to use the Bzzoiro multipliers when available. After the existing `q_mult` call, add:

```python
    # Use Bzzoiro multipliers when available (keys set by _load_team_players)
    from app.pricing.goalscorer import calculate_quality_multiplier_bzzoiro
    from app.pricing.assist import calculate_creation_multiplier_bzzoiro

    q_mult, _ = calculate_quality_multiplier_bzzoiro(
        shot_accuracy=share.sot_per_90,    # repurposed slot
        xg_per_shot=share.tap_per_90,      # repurposed slot
        rating=None,                        # avg_rating not in PlayerShare — acceptable
    )
    c_mult, _ = calculate_creation_multiplier_bzzoiro(
        key_pass_per_90=share.bcc_per_90,  # repurposed slot
        xa_per_90=share.xa_per_90,
        accurate_cross_per_90=share.accurate_crosses_per_90,
    )
```

- [ ] **Step 4: Run existing pricing tests**

```bash
cd backend && uv run pytest tests/ -k "pricing or goalscorer or assist" -v
# Expected: all PASS (existing tests)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/pricing/goalscorer.py \
        backend/app/pricing/assist.py \
        backend/app/pricing/team_xg.py
git commit -m "feat: pricing — bzzoiro-native quality/creation multipliers + _load_team_players from bzz"
```

---

## Task 14: Players API — replace with Bzzoiro-backed endpoint

**Files:**
- Modify: `backend/app/api/players.py`

- [ ] **Step 1: Replace `backend/app/api/players.py` entirely**

```python
"""Player API endpoints — backed by Bzzoiro data (bzz_* tables)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzPlayerSeasonStat

router = APIRouter(prefix="/players", tags=["players"])


# ── Response models ───────────────────────────────────────────────

class PlayerSeasonStatsResponse(BaseModel):
    matches_played: int
    minutes_played: int
    goals: int
    goal_assist: int
    expected_goals: float
    expected_assists: float
    total_shots: int
    shots_on_target: int
    key_pass: int
    total_cross: int
    accurate_cross: int
    total_pass: int
    accurate_pass: int
    duel_won: int
    duel_lost: int
    aerial_won: int
    aerial_lost: int
    total_tackle: int
    won_tackle: int
    interception: int
    ball_recovery: int
    yellow_card: int
    red_card: int
    saves: int
    avg_rating: float | None
    # Per-90
    xg_per_90: float | None
    xa_per_90: float | None
    shots_on_target_per_90: float | None
    key_pass_per_90: float | None
    accurate_cross_per_90: float | None
    # Efficiency
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    xa_delta: float | None
    pass_completion: float | None
    duel_win_rate: float | None
    aerial_win_rate: float | None
    tackle_success_rate: float | None
    # Profile
    avg_minutes_per_match: float | None
    starts_pct: float | None
    # Form
    form_xg_5: float | None
    form_rating_5: float | None
    form_goals_5: int | None
    form_assists_5: int | None
    rating_trend: float | None


class PlayerMatchStatResponse(BaseModel):
    event_api_id: int
    event_date: datetime | None
    home_team: str | None
    away_team: str | None
    home_score: int | None
    away_score: int | None
    minutes_played: int
    rating: float | None
    goals: int
    goal_assist: int
    expected_goals: float | None
    expected_assists: float | None
    total_shots: int
    shots_on_target: int
    key_pass: int
    accurate_cross: int
    total_pass: int
    accurate_pass: int
    duel_won: int
    duel_lost: int
    total_tackle: int
    interception: int
    ball_recovery: int
    yellow_card: int
    red_card: int
    # Derived
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    pass_completion: float | None
    duel_win_rate: float | None


class PlayerDetailResponse(BaseModel):
    id: int
    api_id: int
    name: str
    short_name: str | None
    nationality: str | None
    date_of_birth: str | None
    height: int | None
    jersey_number: int | None
    position: str | None
    market_value: int | None
    current_team: str | None
    season_stats: PlayerSeasonStatsResponse | None
    match_stats: list[PlayerMatchStatResponse]


class PlayerListItem(BaseModel):
    id: int
    api_id: int
    name: str
    position: str | None
    current_team: str | None
    nationality: str | None
    # Key stats for list view
    xg_per_90: float | None
    xa_per_90: float | None
    avg_rating: float | None
    shots_on_target_per_90: float | None
    form_xg_5: float | None
    minutes_played: int


@router.get("", response_model=list[PlayerListItem])
async def list_players(
    session: AsyncSession = Depends(get_db),
    team_api_id: int | None = Query(None, description="Filter by Bzzoiro team api_id"),
    position: str | None = Query(None, description="Filter by position: G/D/M/F"),
    search: str | None = Query(None, description="Search by player name"),
    min_minutes: int = Query(0),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """List players with season stats (list view)."""
    stmt = (
        select(BzzPlayer, BzzPlayerSeasonStat)
        .outerjoin(
            BzzPlayerSeasonStat,
            (BzzPlayerSeasonStat.player_api_id == BzzPlayer.api_id)
            & (BzzPlayerSeasonStat.season == "2025-2026"),
        )
    )
    if team_api_id:
        stmt = stmt.where(BzzPlayer.current_team_api_id == team_api_id)
    if position:
        stmt = stmt.where(BzzPlayer.position == position.upper())
    if search:
        stmt = stmt.where(BzzPlayer.name.ilike(f"%{search}%"))
    if min_minutes:
        stmt = stmt.where(BzzPlayerSeasonStat.minutes_played >= min_minutes)

    stmt = stmt.order_by(BzzPlayer.name).offset(offset).limit(limit)
    result = await session.execute(stmt)

    # Resolve team names
    team_ids = set()
    rows_raw = result.all()
    for p, _ in rows_raw:
        if p.current_team_api_id:
            team_ids.add(p.current_team_api_id)

    from app.models.bzzoiro import BzzTeam
    team_names: dict[int, str] = {}
    if team_ids:
        t_res = await session.execute(
            select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(team_ids))
        )
        team_names = {r[0]: r[1] for r in t_res.all()}

    return [
        {
            "id": p.id,
            "api_id": p.api_id,
            "name": p.name,
            "position": p.position,
            "current_team": team_names.get(p.current_team_api_id) if p.current_team_api_id else None,
            "nationality": p.nationality,
            "xg_per_90": ss.xg_per_90 if ss else None,
            "xa_per_90": ss.xa_per_90 if ss else None,
            "avg_rating": ss.avg_rating if ss else None,
            "shots_on_target_per_90": ss.shots_on_target_per_90 if ss else None,
            "form_xg_5": ss.form_xg_5 if ss else None,
            "minutes_played": ss.minutes_played if ss else 0,
        }
        for p, ss in rows_raw
    ]


@router.get("/{player_api_id}", response_model=PlayerDetailResponse)
async def get_player(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get player detail: season stats + all match stats for chart."""
    from fastapi import HTTPException

    result = await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id == player_api_id)
    )
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Season stats
    ss_res = await session.execute(
        select(BzzPlayerSeasonStat)
        .where(BzzPlayerSeasonStat.player_api_id == player_api_id)
        .where(BzzPlayerSeasonStat.season == "2025-2026")
    )
    ss = ss_res.scalar_one_or_none()

    # Match stats (joined with event for dates + scores)
    match_res = await session.execute(
        select(BzzPlayerMatchStat, BzzEvent)
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .where(BzzPlayerMatchStat.player_api_id == player_api_id)
        .order_by(BzzEvent.event_date.desc())
    )
    match_rows = match_res.all()

    # Resolve team names for matches
    from app.models.bzzoiro import BzzTeam
    team_api_ids = {e.home_team_api_id for _, e in match_rows} | {e.away_team_api_id for _, e in match_rows}
    team_api_ids.discard(None)
    t_res = await session.execute(
        select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(team_api_ids))
    )
    team_map = {r[0]: r[1] for r in t_res.all()}

    # Current team name
    current_team_name: str | None = None
    if player.current_team_api_id:
        ct = await session.get(BzzTeam, player.current_team_api_id)
        current_team_name = ct.name if ct else None

    match_stats = [
        {
            "event_api_id": e.api_id,
            "event_date": e.event_date,
            "home_team": team_map.get(e.home_team_api_id),
            "away_team": team_map.get(e.away_team_api_id),
            "home_score": e.home_score,
            "away_score": e.away_score,
            "minutes_played": ms.minutes_played,
            "rating": ms.rating,
            "goals": ms.goals,
            "goal_assist": ms.goal_assist,
            "expected_goals": ms.expected_goals,
            "expected_assists": ms.expected_assists,
            "total_shots": ms.total_shots,
            "shots_on_target": ms.shots_on_target,
            "key_pass": ms.key_pass,
            "accurate_cross": ms.accurate_cross,
            "total_pass": ms.total_pass,
            "accurate_pass": ms.accurate_pass,
            "duel_won": ms.duel_won,
            "duel_lost": ms.duel_lost,
            "total_tackle": ms.total_tackle,
            "interception": ms.interception,
            "ball_recovery": ms.ball_recovery,
            "yellow_card": ms.yellow_card,
            "red_card": ms.red_card,
            "shot_accuracy": ms.shot_accuracy,
            "xg_per_shot": ms.xg_per_shot,
            "finishing_delta": ms.finishing_delta,
            "pass_completion": ms.pass_completion,
            "duel_win_rate": ms.duel_win_rate,
        }
        for ms, e in match_rows
    ]

    def _ss_dict(s: BzzPlayerSeasonStat) -> dict[str, Any]:
        return {
            f: getattr(s, f)
            for f in PlayerSeasonStatsResponse.model_fields
        }

    return {
        "id": player.id,
        "api_id": player.api_id,
        "name": player.name,
        "short_name": player.short_name,
        "nationality": player.nationality,
        "date_of_birth": str(player.date_of_birth) if player.date_of_birth else None,
        "height": player.height,
        "jersey_number": player.jersey_number,
        "position": player.position,
        "market_value": player.market_value,
        "current_team": current_team_name,
        "season_stats": _ss_dict(ss) if ss else None,
        "match_stats": match_stats,
    }
```

- [ ] **Step 2: Run lint**

```bash
cd backend && uv run ruff check app/api/players.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/players.py
git commit -m "feat: players API — rewrite with bzz_ tables (season + match detail)"
```

---

## Task 15: Frontend — XgBadge component

**Files:**
- Create: `frontend/src/components/XgBadge.tsx`

- [ ] **Step 1: Create XgBadge.tsx**

```tsx
// frontend/src/components/XgBadge.tsx
"use client";

interface XgBadgeProps {
  source: "bzzoiro" | "model";
  size?: "sm" | "md";
}

export function XgBadge({ source, size = "sm" }: XgBadgeProps) {
  const isApi = source === "bzzoiro";
  const label = isApi ? "API" : "MODEL";
  const color = isApi ? "#3B82F6" : "#F97316";
  const padding = size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-xs";

  return (
    <span
      className={`inline-flex items-center rounded font-mono font-semibold ${padding}`}
      style={{ backgroundColor: `${color}20`, color, border: `1px solid ${color}40` }}
    >
      {label}
    </span>
  );
}
```

- [ ] **Step 2: Add XgBadge to dashboard match cards**

In `frontend/src/app/dashboard/page.tsx`, find each place that shows xG values (search for `xg` or `xG`) and add `<XgBadge source={match.xg_source ?? "model"} />` next to the xG value.

- [ ] **Step 3: Add XgBadge to recommendations**

In `frontend/src/app/dashboard/recommendations/page.tsx`, find each recommendation card that shows a probability or xG and add `<XgBadge source={rec.xg_source_label ?? "model"} />`.

- [ ] **Step 4: Add XgBadge to history**

In `frontend/src/app/dashboard/history/page.tsx`, find each history row with xG or probability values and add `<XgBadge source={bet.xg_source_label ?? "model"} size="sm" />`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/XgBadge.tsx \
        frontend/src/app/dashboard/page.tsx \
        frontend/src/app/dashboard/recommendations/page.tsx \
        frontend/src/app/dashboard/history/page.tsx
git commit -m "feat: XgBadge — blue API / orange MODEL indicator on all xG surfaces"
```

---

## Task 16: Frontend — Players page refonte

**Files:**
- Modify: `frontend/src/app/dashboard/players/page.tsx`
- Create: `frontend/src/components/players/PlayerMatchChart.tsx`

- [ ] **Step 1: Install recharts if not present**

```bash
cd frontend && grep -q recharts package.json || npm install recharts
```

- [ ] **Step 2: Create PlayerMatchChart.tsx**

```tsx
// frontend/src/components/players/PlayerMatchChart.tsx
"use client";

import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

interface MatchPoint {
  event_date: string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  expected_goals: number | null;
  rating: number | null;
  goals: number;
  goal_assist: number;
  shots_on_target: number;
  key_pass: number;
}

interface PlayerMatchChartProps {
  data: MatchPoint[];
  metrics: ("xg" | "rating" | "goals" | "assists" | "sot" | "key_pass")[];
}

const METRIC_CONFIG = {
  xg:      { key: "expected_goals", label: "xG",      color: "#3B82F6" },
  rating:  { key: "rating",         label: "Rating",   color: "#F59E0B" },
  goals:   { key: "goals",          label: "Goals",    color: "#10B981" },
  assists: { key: "goal_assist",    label: "Assists",  color: "#8B5CF6" },
  sot:     { key: "shots_on_target",label: "SoT",      color: "#EC4899" },
  key_pass:{ key: "key_pass",       label: "Key Pass", color: "#F97316" },
};

export function PlayerMatchChart({ data, metrics }: PlayerMatchChartProps) {
  const chartData = [...data].reverse().map((m) => ({
    ...m,
    label: `${m.home_team?.split(" ").pop() ?? "?"} vs ${m.away_team?.split(" ").pop() ?? "?"}`,
    result: m.home_score !== null ? `${m.home_score}-${m.away_score}` : "–",
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <XAxis dataKey="label" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip
          contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
          formatter={(value: number, name: string) => [value?.toFixed(2) ?? "–", name]}
          labelFormatter={(label, payload) => {
            const p = payload?.[0]?.payload;
            return `${label} (${p?.result ?? "–"})`;
          }}
        />
        <Legend />
        {metrics.map((m) => {
          const cfg = METRIC_CONFIG[m];
          return (
            <Line
              key={m}
              type="monotone"
              dataKey={cfg.key}
              name={cfg.label}
              stroke={cfg.color}
              dot={{ r: 3 }}
              strokeWidth={2}
              connectNulls
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 3: Rewrite players/page.tsx**

Replace the full content of `frontend/src/app/dashboard/players/page.tsx` with:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { PlayerMatchChart } from "@/components/players/PlayerMatchChart";

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

interface PlayerListItem {
  id: number;
  api_id: number;
  name: string;
  position: string | null;
  current_team: string | null;
  nationality: string | null;
  xg_per_90: number | null;
  xa_per_90: number | null;
  avg_rating: number | null;
  shots_on_target_per_90: number | null;
  form_xg_5: number | null;
  minutes_played: number;
}

interface PlayerDetail {
  api_id: number;
  name: string;
  short_name: string | null;
  nationality: string | null;
  date_of_birth: string | null;
  height: number | null;
  jersey_number: number | null;
  position: string | null;
  market_value: number | null;
  current_team: string | null;
  season_stats: Record<string, number | null> | null;
  match_stats: {
    event_api_id: number;
    event_date: string;
    home_team: string;
    away_team: string;
    home_score: number | null;
    away_score: number | null;
    minutes_played: number;
    rating: number | null;
    goals: number;
    goal_assist: number;
    expected_goals: number | null;
    expected_assists: number | null;
    total_shots: number;
    shots_on_target: number;
    key_pass: number;
    accurate_cross: number;
    total_pass: number;
    accurate_pass: number;
    duel_won: number;
    duel_lost: number;
    total_tackle: number;
    interception: number;
    ball_recovery: number;
    yellow_card: number;
    red_card: number;
    shot_accuracy: number | null;
    xg_per_shot: number | null;
    finishing_delta: number | null;
    pass_completion: number | null;
    duel_win_rate: number | null;
  }[];
}

type ChartMetric = "xg" | "rating" | "goals" | "assists" | "sot" | "key_pass";

const CHART_METRICS: { key: ChartMetric; label: string }[] = [
  { key: "xg", label: "xG" },
  { key: "rating", label: "Rating" },
  { key: "goals", label: "Goals" },
  { key: "assists", label: "Assists" },
  { key: "sot", label: "SoT" },
  { key: "key_pass", label: "Key Pass" },
];

const SEASON_STAT_LABELS: Record<string, string> = {
  matches_played: "Matches", minutes_played: "Minutes", goals: "Goals",
  goal_assist: "Assists", expected_goals: "xG", expected_assists: "xA",
  total_shots: "Shots", shots_on_target: "SoT", key_pass: "Key Pass",
  accurate_cross: "Acc. Crosses", total_pass: "Passes", accurate_pass: "Acc. Passes",
  duel_won: "Duels Won", aerial_won: "Aerials Won", total_tackle: "Tackles",
  won_tackle: "Won Tackles", interception: "Interceptions", ball_recovery: "Recoveries",
  yellow_card: "Yellow", red_card: "Red", avg_rating: "Avg Rating",
  xg_per_90: "xG/90", xa_per_90: "xA/90", shots_on_target_per_90: "SoT/90",
  key_pass_per_90: "KeyPass/90", accurate_cross_per_90: "Cross/90",
  shot_accuracy: "Shot Acc.", xg_per_shot: "xG/Shot", finishing_delta: "Finishing Δ",
  xa_delta: "xA Δ", pass_completion: "Pass %", duel_win_rate: "Duel Win %",
  form_xg_5: "Form xG (5)", form_rating_5: "Form Rating (5)",
  form_goals_5: "Form Goals (5)", form_assists_5: "Form Assists (5)",
};

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return "–";
  return typeof v === "number" ? v.toFixed(decimals) : String(v);
}

export default function PlayersPage() {
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState("");
  const [minMinutes, setMinMinutes] = useState(0);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [activeMetrics, setActiveMetrics] = useState<ChartMetric[]>(["xg", "rating"]);

  const fetchPlayers = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (position) params.set("position", position);
    if (minMinutes > 0) params.set("min_minutes", String(minMinutes));
    params.set("limit", "200");
    const res = await fetch(`${API}/players?${params}`);
    const data = await res.json();
    setPlayers(Array.isArray(data) ? data : []);
    setLoading(false);
  }, [search, position, minMinutes]);

  useEffect(() => { fetchPlayers(); }, [fetchPlayers]);

  const openPlayer = async (apiId: number) => {
    setDetailLoading(true);
    const res = await fetch(`${API}/players/${apiId}`);
    const data = await res.json();
    setSelectedPlayer(data);
    setDetailLoading(false);
  };

  const toggleMetric = (m: ChartMetric) => {
    setActiveMetrics((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
    );
  };

  if (selectedPlayer) {
    const ss = selectedPlayer.season_stats;
    return (
      <div className="p-6 space-y-6">
        <button
          onClick={() => setSelectedPlayer(null)}
          className="text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] flex items-center gap-1"
        >
          ← Retour
        </button>

        {/* Header */}
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-full bg-[var(--bg-card)] border border-[var(--border)] flex items-center justify-center text-lg font-bold text-[var(--text-primary)]">
            {selectedPlayer.jersey_number ?? selectedPlayer.position ?? "?"}
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[var(--text-primary)]">{selectedPlayer.name}</h1>
            <p className="text-sm text-[var(--text-muted)]">
              {selectedPlayer.current_team} · {selectedPlayer.position} · {selectedPlayer.nationality}
              {selectedPlayer.height ? ` · ${selectedPlayer.height} cm` : ""}
              {selectedPlayer.date_of_birth ? ` · ${selectedPlayer.date_of_birth.slice(0, 4)}` : ""}
              {selectedPlayer.market_value ? ` · €${(selectedPlayer.market_value / 1_000_000).toFixed(1)}M` : ""}
            </p>
          </div>
        </div>

        {/* Season stats grid */}
        {ss && (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4">
            <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-3">Saison 2025-2026</h2>
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-3">
              {Object.entries(SEASON_STAT_LABELS)
                .filter(([k]) => ss[k] !== undefined && ss[k] !== null)
                .map(([k, label]) => (
                  <div key={k} className="text-center">
                    <div className="text-lg font-semibold text-[var(--text-primary)]">
                      {fmt(ss[k] as number)}
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)] mt-0.5">{label}</div>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Chart */}
        {selectedPlayer.match_stats.length > 0 && (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Évolution par match</h2>
              <div className="flex flex-wrap gap-1">
                {CHART_METRICS.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => toggleMetric(key)}
                    className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                      activeMetrics.includes(key)
                        ? "bg-[var(--accent)] border-[var(--accent)] text-white"
                        : "border-[var(--border)] text-[var(--text-muted)]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <PlayerMatchChart data={selectedPlayer.match_stats} metrics={activeMetrics} />
          </div>
        )}

        {/* Match history table */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)]">
                {["Match", "Score", "Min", "Rating", "Goals", "Ast", "xG", "xA", "Shots", "SoT", "KP", "xG/Shot", "Pass %", "Duel %"].map((h) => (
                  <th key={h} className="px-3 py-2 text-left text-[11px] text-[var(--text-muted)] font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {selectedPlayer.match_stats.map((m) => (
                <tr key={m.event_api_id} className="border-b border-[var(--border)] hover:bg-[var(--bg-hover)]">
                  <td className="px-3 py-2 whitespace-nowrap text-[var(--text-primary)]">
                    {m.home_team} vs {m.away_team}
                  </td>
                  <td className="px-3 py-2 text-[var(--text-muted)]">{m.home_score ?? "–"}–{m.away_score ?? "–"}</td>
                  <td className="px-3 py-2">{m.minutes_played}'</td>
                  <td className="px-3 py-2 font-medium" style={{ color: m.rating && m.rating >= 7 ? "#10B981" : m.rating && m.rating < 6 ? "#EF4444" : undefined }}>
                    {fmt(m.rating, 1)}
                  </td>
                  <td className="px-3 py-2">{m.goals}</td>
                  <td className="px-3 py-2">{m.goal_assist}</td>
                  <td className="px-3 py-2">{fmt(m.expected_goals)}</td>
                  <td className="px-3 py-2">{fmt(m.expected_assists)}</td>
                  <td className="px-3 py-2">{m.total_shots}</td>
                  <td className="px-3 py-2">{m.shots_on_target}</td>
                  <td className="px-3 py-2">{m.key_pass}</td>
                  <td className="px-3 py-2">{fmt(m.xg_per_shot)}</td>
                  <td className="px-3 py-2">{m.pass_completion !== null ? `${(m.pass_completion * 100).toFixed(0)}%` : "–"}</td>
                  <td className="px-3 py-2">{m.duel_win_rate !== null ? `${(m.duel_win_rate * 100).toFixed(0)}%` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-[var(--text-primary)]">Joueurs</h1>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <input
          type="text"
          placeholder="Rechercher..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg border border-[var(--border)] bg-[var(--bg-input)] text-[var(--text-primary)] w-48"
        />
        <select
          value={position}
          onChange={(e) => setPosition(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg border border-[var(--border)] bg-[var(--bg-input)] text-[var(--text-primary)]"
        >
          <option value="">Tous postes</option>
          <option value="F">Attaquant</option>
          <option value="M">Milieu</option>
          <option value="D">Défenseur</option>
          <option value="G">Gardien</option>
        </select>
        <select
          value={minMinutes}
          onChange={(e) => setMinMinutes(Number(e.target.value))}
          className="px-3 py-1.5 text-sm rounded-lg border border-[var(--border)] bg-[var(--bg-input)] text-[var(--text-primary)]"
        >
          <option value={0}>Min. minutes</option>
          <option value={450}>+450 min</option>
          <option value={900}>+900 min</option>
          <option value={1350}>+1350 min</option>
        </select>
      </div>

      {/* Table */}
      {loading ? (
        <div className="text-[var(--text-muted)] text-sm">Chargement...</div>
      ) : (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)]">
                {["Nom", "Équipe", "Pos.", "xG/90", "xA/90", "Rating", "SoT/90", "Form xG (5)", "Minutes"].map((h) => (
                  <th key={h} className="px-3 py-2 text-left text-[11px] text-[var(--text-muted)] font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {players.map((p) => (
                <tr
                  key={p.api_id}
                  onClick={() => openPlayer(p.api_id)}
                  className="border-b border-[var(--border)] hover:bg-[var(--bg-hover)] cursor-pointer transition-colors"
                >
                  <td className="px-3 py-2 font-medium text-[var(--text-primary)]">{p.name}</td>
                  <td className="px-3 py-2 text-[var(--text-muted)]">{p.current_team ?? "–"}</td>
                  <td className="px-3 py-2">
                    <span className="px-1.5 py-0.5 text-[10px] rounded bg-[var(--bg-muted)] text-[var(--text-muted)] font-mono">
                      {p.position ?? "–"}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono">{fmt(p.xg_per_90)}</td>
                  <td className="px-3 py-2 font-mono">{fmt(p.xa_per_90)}</td>
                  <td className="px-3 py-2 font-mono" style={{ color: p.avg_rating && p.avg_rating >= 7 ? "#10B981" : undefined }}>
                    {fmt(p.avg_rating, 1)}
                  </td>
                  <td className="px-3 py-2 font-mono">{fmt(p.shots_on_target_per_90)}</td>
                  <td className="px-3 py-2 font-mono">{fmt(p.form_xg_5)}</td>
                  <td className="px-3 py-2 text-[var(--text-muted)]">{p.minutes_played}'</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {detailLoading && (
        <div className="fixed inset-0 bg-black/20 flex items-center justify-center z-50">
          <div className="text-[var(--text-primary)] text-sm">Chargement du joueur...</div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/dashboard/players/page.tsx \
        frontend/src/components/players/PlayerMatchChart.tsx
git commit -m "feat: players page — season stats + match-by-match chart + full table"
```

---

## Task 17: Docs update + final verification

**Files:**
- Modify: `docs/user-guide/04-data-scraping.md`

- [ ] **Step 1: Update data-scraping.md**

Replace the section on Understat/Sofascore with:

```markdown
## Primary data source: Bzzoiro Sports API

All player, match, odds, and prediction data is sourced from `sports.bzzoiro.com`.
API key: configured via `BZZOIRO_API_KEY` environment variable.

### Sync schedule
| Job | Schedule | Description |
|-----|----------|-------------|
| bzzoiro_reference | Daily 03:00 | Leagues + teams |
| bzzoiro_players | Daily 03:30 | Player profiles + team assignments |
| bzzoiro_events | Every 6h | Matches, odds, lineups, xG, shotmap |
| bzzoiro_player_stats | Every 6h | Per-match player stats + derived metrics |
| bzzoiro_aggregate | Daily 04:00 | Season stats recomputed from match rows |
| bzzoiro_predictions | Daily 07:00 | ML predictions for next 3 days |

### xG source mode
Toggle via Settings page: `bzzoiro` (blue badge) uses actual match xG from Bzzoiro.
`model` (orange badge) uses Dixon-Coles internal model. Automatic fallback to model
if Bzzoiro has no xG for a given match.

### Fallback sources
Understat and Sofascore scrapers remain in `app/ingestion/` but are not called by
the worker. Manual fallback only if Bzzoiro API is unavailable.
```

- [ ] **Step 2: Run full test suite**

```bash
cd backend && uv run pytest tests/ -x -q
# Expected: all PASS
```

- [ ] **Step 3: Run lint**

```bash
cd backend && uv run ruff check .
```

- [ ] **Step 4: Final commit**

```bash
git add docs/user-guide/04-data-scraping.md
git commit -m "docs: update data-scraping guide for Bzzoiro integration"
```

---

## Initial data load (run on VPS after deploy)

```bash
# 1. Apply migration
docker exec ev0-compose-z5hvqt-backend-1 alembic upgrade head

# 2. Rebuild backend + worker images with new code
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker

# 3. Trigger initial sync (run in order — reference first)
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio
from app.db import async_session
from app.config import settings
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_reference import sync_leagues, sync_teams
from app.ingestion.bzzoiro.sync_players import sync_players

async def run():
    async with async_session() as s:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as c:
            await sync_leagues(s, c)
            await sync_teams(s, c)
            await sync_players(s, c)

asyncio.run(run())
"

# 4. Sync events (last 90 days + next 14 days)
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio
from app.db import async_session
from app.config import settings
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_events import sync_events

async def run():
    async with async_session() as s:
        async with BzzoiroClient(api_key=settings.bzzoiro_api_key) as c:
            await sync_events(s, c, days_back=90, days_forward=14)

asyncio.run(run())
"

# 5. Sync player stats for all finished events
# (Let the worker job run automatically — or trigger manually for the first 200)

# 6. Aggregate season stats
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio
from app.db import async_session
from app.ingestion.bzzoiro.aggregate import aggregate_season_stats
async def run():
    async with async_session() as s:
        n = await aggregate_season_stats(s)
        print(f'Aggregated {n} season rows')
asyncio.run(run())
"

# 7. Verify
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT 'bzz_players' as t, count(*) FROM bzz_players
UNION ALL SELECT 'bzz_events', count(*) FROM bzz_events
UNION ALL SELECT 'bzz_player_match_stats', count(*) FROM bzz_player_match_stats
UNION ALL SELECT 'bzz_player_season_stats', count(*) FROM bzz_player_season_stats;
"
```
