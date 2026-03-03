"""Training routines for the autopilot RL agent.

run_backtest_training(): pre-train on synthetic backtest records
fine_tune_from_db(): one update pass on real settled AutopilotDecisions
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.agent import ACTIONS, LinearQAgent
from app.autopilot.features import extract_features
from app.autopilot.performance import compute_sharpe
from app.models.autopilot import AutopilotDecision

logger = logging.getLogger(__name__)

# File path kept for local dev / fallback; Redis is the primary store in prod.
WEIGHTS_PATH = Path(__file__).parent / "weights" / "agent.npz"
_REDIS_KEY = "autopilot:agent_weights"


def _save_agent(agent: LinearQAgent) -> None:
    """Persist weights to Redis (primary) and local file (fallback)."""
    buf = io.BytesIO()
    np.savez(
        buf,
        W=agent.W,
        meta=np.array(
            [agent.alpha, agent.gamma, agent.epsilon, agent.epsilon_min, agent.epsilon_decay],
            dtype=np.float64,
        ),
        steps=np.array([agent.steps_trained], dtype=np.int64),
    )
    weights_bytes = buf.getvalue()

    # Primary: Redis (shared across all containers)
    try:
        import redis as _redis

        from app.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=False)
        r.set(_REDIS_KEY, weights_bytes)
        logger.debug("Agent weights saved to Redis (%d bytes)", len(weights_bytes))
    except Exception as exc:
        logger.warning("Redis save failed, falling back to file: %s", exc)
        # Fallback: local file
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        WEIGHTS_PATH.write_bytes(weights_bytes)


def _load_agent() -> LinearQAgent | None:
    """Load weights from Redis (primary) or local file (fallback)."""
    # Primary: Redis
    try:
        import redis as _redis

        from app.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=False)
        weights_bytes = r.get(_REDIS_KEY)
        if weights_bytes:
            data = np.load(io.BytesIO(weights_bytes))
            meta = data["meta"]
            agent = LinearQAgent(
                alpha=float(meta[0]),
                gamma=float(meta[1]),
                epsilon=float(meta[2]),
                epsilon_min=float(meta[3]),
                epsilon_decay=float(meta[4]),
            )
            agent.W = data["W"]
            agent.steps_trained = int(data["steps"][0])
            logger.debug("Agent loaded from Redis (steps=%d)", agent.steps_trained)
            return agent
    except Exception as exc:
        logger.warning("Redis load failed, trying file: %s", exc)

    # Fallback: local file
    if WEIGHTS_PATH.exists():
        return LinearQAgent.load(WEIGHTS_PATH)

    return None


def weights_exist() -> bool:
    """Return True if trained weights are available (Redis or file)."""
    try:
        import redis as _redis

        from app.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=False)
        if r.exists(_REDIS_KEY):
            return True
    except Exception:
        pass
    return WEIGHTS_PATH.exists()

# Bankroll used for reward scaling during backtest training (paper bankroll)
_TRAINING_BANKROLL = 1000.0
# Base Kelly stake fraction applied to the training bankroll
_BASE_KELLY = 0.25


def _load_or_create_agent() -> LinearQAgent:
    return _load_agent() or LinearQAgent()


def _compute_stake(action_idx: int, rec: dict, bankroll: float = _TRAINING_BANKROLL) -> float:
    """Compute stake given action and record."""
    fraction = ACTIONS[action_idx]
    if fraction == 0.0:
        return 0.0

    fair_odds = float(rec.get("fair_odds") or 2.0)
    market_odds = float(rec.get("market_odds") or rec.get("best_odds") or 2.0)
    if market_odds <= 1 or fair_odds <= 1:
        return 0.0

    p = 1.0 / fair_odds
    q = 1.0 - p
    b = market_odds - 1.0
    kelly = (b * p - q) / b
    kelly = max(kelly, 0.0)

    return round(kelly * fraction * _BASE_KELLY * bankroll, 2)


def _compute_reward(stake: float, market_odds: float, outcome: int, bankroll: float) -> float:
    """Reward = pnl / bankroll (scale-invariant)."""
    if stake <= 0:
        return 0.0
    pnl = stake * (market_odds - 1.0) if outcome == 1 else -stake
    return pnl / max(bankroll, 1.0)


async def run_backtest_training(
    db: AsyncSession,
    min_date: str | None = None,
    max_date: str | None = None,
    n_epochs: int = 3,
) -> dict:
    """Pre-train the agent on synthetic backtest records.

    Steps:
    1. Call simulate_historical() to get historical records
    2. Sort by date (no lookahead)
    3. For each record: extract_features → act(explore=True) → reward → update
    4. Save weights
    """
    from datetime import date as date_type

    from app.backtest.simulator import simulate_historical

    t0 = time.monotonic()

    min_date_obj = (
        date_type.fromisoformat(min_date) if min_date else date_type(2024, 8, 1)
    )
    max_date_obj = (
        date_type.fromisoformat(max_date) if max_date else date_type.today()
    )

    logger.info(
        "Autopilot training: loading backtest records %s → %s",
        min_date_obj,
        max_date_obj,
    )

    records = await simulate_historical(
        db,
        min_date=min_date_obj,
        max_date=max_date_obj,
    )

    if not records:
        logger.warning("No backtest records available for training")
        return {
            "records_used": 0,
            "cumulative_pnl": 0.0,
            "sharpe": 0.0,
            "vs_kelly_roi": 0.0,
            "final_epsilon": 1.0,
            "duration_s": round(time.monotonic() - t0, 1),
        }

    # Filter to VALUE records (edge > 5%) and sort by date
    records = [r for r in records if r.get("edge", 0) >= 0.05]
    records.sort(key=lambda r: r["date"])

    agent = _load_or_create_agent()
    # Reset epsilon for fresh training pass
    agent.epsilon = 1.0

    # Track Kelly baseline for comparison
    kelly_pnl = 0.0
    kelly_staked = 0.0
    agent_pnl = 0.0
    agent_staked = 0.0
    daily_pnls: list[float] = []
    bankroll = _TRAINING_BANKROLL

    for _epoch in range(n_epochs):
        epoch_pnl = 0.0
        for rec in records:
            features = extract_features(rec)
            action_idx = agent.act(features, explore=True)
            market_odds = float(rec.get("market_odds") or 2.0)
            outcome = int(rec.get("outcome", 0))

            stake = _compute_stake(action_idx, rec, bankroll)
            reward = _compute_reward(stake, market_odds, outcome, bankroll)

            agent.update(features, action_idx, reward)

            if stake > 0:
                pnl = stake * (market_odds - 1) if outcome == 1 else -stake
                epoch_pnl += pnl
                agent_staked += stake

        agent_pnl += epoch_pnl
        daily_pnls.append(epoch_pnl)

        # Kelly baseline: always use full Kelly on all VALUE recs
        if _epoch == 0:
            for rec in records:
                ks = _compute_stake(2, rec, bankroll)  # action 2 = full kelly
                market_odds_k = float(rec.get("market_odds") or 2.0)
                outcome_k = int(rec.get("outcome", 0))
                kelly_pnl += ks * (market_odds_k - 1) if outcome_k == 1 else -ks
                kelly_staked += ks

    _save_agent(agent)

    sharpe = compute_sharpe(daily_pnls)
    kelly_roi = kelly_pnl / max(kelly_staked, 1.0)
    agent_roi = agent_pnl / max(agent_staked, 1.0)
    vs_kelly_roi = agent_roi - kelly_roi

    duration = round(time.monotonic() - t0, 1)
    logger.info(
        "Training complete: %d records, %d epochs, agent_roi=%.1f%%, vs_kelly=%.1f%%, ε=%.3f",
        len(records),
        n_epochs,
        agent_roi * 100,
        vs_kelly_roi * 100,
        agent.epsilon,
    )

    return {
        "records_used": len(records),
        "cumulative_pnl": round(agent_pnl, 2),
        "sharpe": round(sharpe, 3),
        "vs_kelly_roi": round(vs_kelly_roi, 4),
        "final_epsilon": round(agent.epsilon, 4),
        "duration_s": duration,
    }


async def fine_tune_from_db(db: AsyncSession) -> dict:
    """Fine-tune the agent on real settled AutopilotDecisions.

    Only processes decisions where trained_on=False and result is not NULL.
    Marks them as trained_on=True after processing.
    """
    stmt = select(AutopilotDecision).where(
        AutopilotDecision.result.isnot(None),
        AutopilotDecision.trained_on.is_(False),
    )
    result = await db.execute(stmt)
    decisions = list(result.scalars().all())

    if not decisions:
        logger.debug("fine_tune_from_db: no new settled decisions to train on")
        return {"decisions_used": 0, "td_error_mean": 0.0}

    agent = _load_or_create_agent()
    td_errors = []

    for decision in decisions:
        try:
            features = np.array(json.loads(decision.features_json), dtype=np.float64)
            reward = float(decision.reward or 0.0)
            td_err = agent.update(features, decision.action_idx, reward)
            td_errors.append(td_err)
        except Exception:
            logger.exception("Error fine-tuning on decision %d", decision.id)
            continue

    _save_agent(agent)

    # Mark as trained
    ids = [d.id for d in decisions]
    await db.execute(
        update(AutopilotDecision)
        .where(AutopilotDecision.id.in_(ids))
        .values(trained_on=True)
    )
    await db.commit()

    mean_td = float(np.mean(td_errors)) if td_errors else 0.0
    logger.info("fine_tune_from_db: %d decisions, mean_td=%.4f", len(decisions), mean_td)

    return {
        "decisions_used": len(decisions),
        "td_error_mean": round(mean_td, 4),
    }


def get_training_status() -> dict:
    """Return metadata about saved weights (Redis or file)."""
    if not weights_exist():
        return {
            "trained": False,
            "trained_at": None,
            "size_bytes": 0,
            "steps_trained": 0,
        }

    # Try to get mtime from file fallback; Redis has no native mtime
    trained_at = None
    size_bytes = 0
    if WEIGHTS_PATH.exists():
        stat = WEIGHTS_PATH.stat()
        trained_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        size_bytes = stat.st_size

    try:
        agent = _load_agent()
        steps = agent.steps_trained if agent else 0
    except Exception:
        steps = 0

    return {
        "trained": True,
        "trained_at": trained_at,
        "size_bytes": size_bytes,
        "steps_trained": steps,
    }
