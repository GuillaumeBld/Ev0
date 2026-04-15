"""Autopilot RL agent API endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.agent import ACTION_LABELS, ACTIONS, LinearQAgent
from app.autopilot.features import extract_features, extract_features_with_context
from app.autopilot.performance import compute_brier_score, compute_calibration_buckets, compute_metrics
from app.autopilot.trainer import (
    WEIGHTS_PATH,
    _compute_stake,
    _load_agent,
    fine_tune_from_db,
    get_training_status,
    run_backtest_training,
    weights_exist,
)
from app.db import get_db
from app.models.autopilot import AutopilotDecision
from app.models.recommendations import Recommendation
from app.models.settings import UserSettings

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────


class TrainRequest(BaseModel):
    min_date: str | None = None
    max_date: str | None = None
    epochs: int = 3


class TrainingResult(BaseModel):
    records_used: int
    cumulative_pnl: float
    sharpe: float
    vs_kelly_roi: float
    final_epsilon: float
    duration_s: float
    fine_tune: dict | None = None


class MetricsSummary(BaseModel):
    total_bets: int
    wins: int
    losses: int
    win_rate: float
    roi: float
    sharpe: float


class AutopilotStatus(BaseModel):
    enabled: bool
    trained: bool
    trained_at: str | None
    training_records: int
    steps_trained: int
    mode: str
    metrics: MetricsSummary


class ToggleRequest(BaseModel):
    enabled: bool
    mode: str = "paper"  # paper | live


class AutopilotDecisionOut(BaseModel):
    recommendation_id: int | None
    player_name: str
    fixture_name: str
    market_type: str
    best_odds: float
    edge: float
    confidence: float
    action: str
    kelly_fraction: float
    stake: float
    rationale: dict


class OptimizationResult(BaseModel):
    best_params: dict | None = None
    best_log_wealth: float = 0.0
    best_roi: float = 0.0
    sharpe: float = 0.0
    dsr: float = 0.0
    n_features: int = 0
    n_trials: int = 0
    n_folds: int = 0
    records_used: int = 0
    duration_s: float = 0.0
    error: str | None = None


class OptimizeRequest(BaseModel):
    n_trials: int = 100


class AutopilotPerformanceResponse(BaseModel):
    metrics: dict
    pnl_curve: list[dict]
    action_distribution: dict
    feature_importance: dict


# ── Helpers ───────────────────────────────────────────────────────


async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(UserSettings).where(UserSettings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else default


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(UserSettings).where(UserSettings.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(UserSettings(key=key, value=value))
    await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/autopilot/train", response_model=TrainingResult)
async def train_autopilot(
    body: TrainRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pre-train on backtest data then fine-tune on real settled decisions."""
    training = await run_backtest_training(
        db,
        min_date=body.min_date,
        max_date=body.max_date,
        n_epochs=body.epochs,
    )
    fine = await fine_tune_from_db(db)
    return TrainingResult(**training, fine_tune=fine)


_REDIS_OPTIM_KEY = "autopilot:last_optimization"


def _save_optimization_result(result: dict) -> None:
    """Persist last optimization result to Redis."""
    import json

    try:
        import redis as _redis

        from app.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        from datetime import UTC, datetime
        payload = {**result, "completed_at": datetime.now(UTC).isoformat()}
        r.set(_REDIS_OPTIM_KEY, json.dumps(payload, default=str))
    except Exception as exc:
        logger.warning("Failed to save optimization result to Redis: %s", exc)


def _load_optimization_result() -> dict | None:
    """Load last optimization result from Redis."""
    import json

    try:
        import redis as _redis

        from app.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        raw = r.get(_REDIS_OPTIM_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


@router.get("/autopilot/optimization")
async def get_last_optimization():
    """Return the last optimization result (or null if never run)."""
    result = _load_optimization_result()
    if not result:
        return {"status": "never_run"}
    return result


@router.post("/autopilot/optimize", response_model=OptimizationResult)
async def optimize_agent(
    body: OptimizeRequest = OptimizeRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Run Optuna walk-forward optimization, retrain agent, and return results."""
    import asyncio

    from app.autopilot.autoresearch import run_optimization
    from app.backtest.simulator import simulate_historical

    records = await simulate_historical(db)

    # run_optimization is CPU-bound; run in thread to avoid blocking event loop
    result = await asyncio.to_thread(run_optimization, records, body.n_trials)

    if "error" not in result:
        # Retrain agent with best params and save
        best_params = result.get("best_params", {})
        if best_params:
            from app.autopilot.trainer import _save_agent, train_single_pass

            agent = LinearQAgent.from_params(best_params)
            value_records = sorted(
                [r for r in records if r.get("edge", 0) >= 0.05],
                key=lambda r: r["date"],
            )
            for _ in range(best_params.get("n_epochs", 3)):
                train_single_pass(
                    agent,
                    value_records,
                    l1_lambda=best_params.get("l1_lambda", 0),
                )
            _save_agent(agent)
            logger.info("Agent retrained with optimized params via API")

    # Persist result to Redis for dashboard display
    _save_optimization_result(result)

    return result


@router.get("/autopilot/status", response_model=AutopilotStatus)
async def get_autopilot_status(db: AsyncSession = Depends(get_db)):
    """Return autopilot status, training info, and performance summary."""
    enabled = (await _get_setting(db, "autopilot_enabled", "false")) == "true"
    mode = await _get_setting(db, "autopilot_mode", "paper")

    status = get_training_status()

    # Count settled decisions
    result = await db.execute(select(AutopilotDecision))
    all_decisions = list(result.scalars().all())

    # Metrics from last 30 days
    from datetime import timedelta
    cutoff = datetime.now(UTC) - timedelta(days=30)
    recent = [d for d in all_decisions if d.created_utc and d.created_utc >= cutoff]
    metrics_raw = compute_metrics(recent)

    return AutopilotStatus(
        enabled=enabled,
        trained=status["trained"],
        trained_at=status.get("trained_at"),
        training_records=len(all_decisions),
        steps_trained=status.get("steps_trained", 0),
        mode=mode,
        metrics=MetricsSummary(
            total_bets=metrics_raw["total_bets"],
            wins=metrics_raw["wins"],
            losses=metrics_raw["losses"],
            win_rate=metrics_raw["win_rate"],
            roi=metrics_raw["roi"],
            sharpe=metrics_raw["sharpe"],
        ),
    )


@router.post("/autopilot/toggle", response_model=AutopilotStatus)
async def toggle_autopilot(body: ToggleRequest, db: AsyncSession = Depends(get_db)):
    """Enable or disable the autopilot and set paper/live mode."""
    await _set_setting(db, "autopilot_enabled", "true" if body.enabled else "false")
    await _set_setting(db, "autopilot_mode", body.mode)
    return await get_autopilot_status(db)


@router.get("/autopilot/today", response_model=list[AutopilotDecisionOut])
async def get_autopilot_today(db: AsyncSession = Depends(get_db)):
    """Run the agent (greedy) on today's pending VALUE recommendations."""
    if not weights_exist():
        raise HTTPException(
            status_code=424,
            detail="Agent not trained yet. POST /api/v1/autopilot/train first.",
        )

    agent = _load_agent()

    # Load today's VALUE recommendations from DB
    from datetime import date, timedelta
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=UTC)
    tomorrow_start = today_start + timedelta(days=1)

    stmt = (
        select(Recommendation)
        .where(
            Recommendation.classification == "VALUE",
            Recommendation.status == "pending",
            Recommendation.generated_utc >= today_start,
            Recommendation.generated_utc < tomorrow_start,
        )
        .order_by(Recommendation.edge.desc())
    )
    result = await db.execute(stmt)
    recs = result.scalars().all()

    # Also load fixtures for names
    from app.models.fixtures import Fixture
    fixture_ids = list({r.fixture_id for r in recs})
    fixture_map: dict[int, Fixture] = {}
    if fixture_ids:
        fx_result = await db.execute(
            select(Fixture).where(Fixture.id.in_(fixture_ids))
        )
        for fx in fx_result.scalars().all():
            fixture_map[fx.id] = fx

    # Build all rec dicts up front for contextual features
    all_rec_dicts = []
    for rec in recs:
        all_rec_dicts.append({
            "edge": rec.edge,
            "confidence": rec.confidence,
            "best_odds": rec.best_odds,
            "fair_odds": rec.fair_odds,
            "fair_probability": rec.fair_probability,
            "lambda_intensity": rec.lambda_intensity,
            "market_type": rec.market_type,
            "explanation": rec.explanation or {},
        })

    # Query odds snapshots for contextual features
    from app.models.player_odds_snapshot import PlayerOddsSnapshot

    output = []
    for rec, rec_dict in zip(recs, all_rec_dicts):
        # Get odds history for this player+market+fixture
        odds_stmt = (
            select(PlayerOddsSnapshot.odds)
            .where(
                PlayerOddsSnapshot.fixture_id == rec.fixture_id,
                PlayerOddsSnapshot.player_name == rec.player_name,
                PlayerOddsSnapshot.market_type == rec.market_type,
            )
            .order_by(PlayerOddsSnapshot.scraped_at.asc())
        )
        odds_result = await db.execute(odds_stmt)
        odds_history = [row[0] for row in odds_result.all()]

        features = extract_features_with_context(rec_dict, all_rec_dicts, odds_history or None)
        action_idx = agent.act(features, explore=False)
        qvals = agent.q_values(features)

        fraction = ACTIONS[action_idx]
        stake = _compute_stake(action_idx, rec_dict)

        fx = fixture_map.get(rec.fixture_id)
        fixture_name = (
            f"{fx.home_team} vs {fx.away_team}" if fx else str(rec.fixture_id)
        )

        # Top feature by abs weight for chosen action
        importance = agent.feature_importance()
        top_feature = max(importance, key=lambda k: importance[k])

        output.append(
            AutopilotDecisionOut(
                recommendation_id=rec.id,
                player_name=rec.player_name,
                fixture_name=fixture_name,
                market_type=rec.market_type,
                best_odds=rec.best_odds,
                edge=rec.edge,
                confidence=rec.confidence,
                action=ACTION_LABELS[action_idx],
                kelly_fraction=fraction,
                stake=stake,
                rationale={
                    "q_values": {
                        ACTION_LABELS[i]: round(float(qvals[i]), 4)
                        for i in range(len(ACTIONS))
                    },
                    "top_feature": top_feature,
                    "features": features.tolist(),
                },
            )
        )

    return output


@router.get("/autopilot/performance", response_model=AutopilotPerformanceResponse)
async def get_autopilot_performance(db: AsyncSession = Depends(get_db)):
    """Return full performance metrics for all autopilot decisions."""
    result = await db.execute(
        select(AutopilotDecision).order_by(AutopilotDecision.created_utc.asc())
    )
    decisions = list(result.scalars().all())

    metrics = compute_metrics(decisions)

    feature_importance: dict = {}
    if weights_exist():
        try:
            agent = _load_agent()
            if agent:
                feature_importance = agent.feature_importance()
        except Exception:
            pass

    return AutopilotPerformanceResponse(
        metrics={
            **metrics,
            "brier_score": compute_brier_score(decisions),
            "calibration": compute_calibration_buckets(decisions),
        },
        pnl_curve=metrics.pop("pnl_curve", []),
        action_distribution=metrics.pop("action_distribution", {}),
        feature_importance=feature_importance,
    )
