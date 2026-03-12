"""Inner optimization loop: Optuna + walk-forward evaluation.

Usage:
  python -m app.autopilot.autoresearch          # CLI (for LLM outer loop)
  job_autopilot_reoptimize()                    # worker job (weekly)
  POST /api/v1/autopilot/optimize               # API endpoint
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
import optuna

from app.autopilot.agent import ACTIONS, LinearQAgent
from app.autopilot.features import FEATURE_NAMES, extract_features
from app.autopilot.search_space import SEARCH_SPACE
from app.autopilot.trainer import _compute_stake, _TRAINING_BANKROLL
from app.autopilot.walk_forward import (
    compute_log_wealth,
    create_walk_forward_folds,
    deflated_sharpe,
)

N_TRIALS = 100
logger = logging.getLogger(__name__)


def _sample_params(trial: optuna.Trial) -> dict:
    """Sample hyperparameters from search space."""
    params: dict = {}
    for name, spec in SEARCH_SPACE.items():
        if spec["type"] == "loguniform":
            params[name] = trial.suggest_float(name, spec["low"], spec["high"], log=True)
        elif spec["type"] == "uniform":
            params[name] = trial.suggest_float(name, spec["low"], spec["high"])
        elif spec["type"] == "int":
            params[name] = trial.suggest_int(name, spec["low"], spec["high"])
        elif spec["type"] == "categorical":
            params[name] = trial.suggest_categorical(name, spec["choices"])
    return params


def _mask_features(features: np.ndarray, params: dict) -> np.ndarray:
    """Zero out features that are toggled off."""
    masked = features.copy()
    # FEATURE_NAMES has 13 entries; last is "bias" (always on)
    feature_names_no_bias = FEATURE_NAMES[:-1]
    for i, name in enumerate(feature_names_no_bias):
        if not params.get(f"use_{name}", True):
            masked[i] = 0.0
    return masked


def _evaluate_params(params: dict, folds: list) -> dict:
    """Train agent with given params, evaluate on walk-forward folds.

    Returns: {log_wealth, roi, win_rate, total_pnl, decisions_count}
    """
    all_test_decisions: list[dict] = []

    for train_recs, test_recs in folds:
        # Create fresh agent with these params
        agent = LinearQAgent(
            alpha=params["alpha"],
            epsilon=params["epsilon_init"],
            epsilon_decay=params["epsilon_decay"],
        )
        # Apply supervised prior
        agent.W[2][0] = params["prior_edge_weight"]   # kelly <- edge
        agent.W[2][1] = params["prior_conf_weight"]    # kelly <- confidence
        agent.W[1][0] = params["prior_edge_weight"] / 2
        agent.W[1][1] = params["prior_conf_weight"] / 2

        l1_lambda = params["l1_lambda"]

        # Train on training fold
        for _epoch in range(params["n_epochs"]):
            for rec in train_recs:
                features = _mask_features(extract_features(rec), params)
                action_idx = agent.act(features, explore=True)
                edge = float(rec.get("edge", 0))
                stake = _compute_stake(action_idx, rec, _TRAINING_BANKROLL)
                reward = edge * stake / _TRAINING_BANKROLL
                agent.update(features, action_idx, reward)
                # L1 regularization
                agent.W -= l1_lambda * np.sign(agent.W)

        # Evaluate on test fold (greedy)
        for rec in test_recs:
            features = _mask_features(extract_features(rec), params)
            action_idx = agent.act(features, explore=False)
            stake = _compute_stake(action_idx, rec, _TRAINING_BANKROLL)
            market_odds = float(rec.get("market_odds", 2.0))
            outcome = 1 if rec.get("outcome") in (1, "won") else 0
            pnl = stake * (market_odds - 1) if outcome == 1 else -stake
            all_test_decisions.append({"stake": stake, "pnl": pnl, "outcome": outcome})

    log_w = compute_log_wealth(all_test_decisions)
    total_staked = sum(d["stake"] for d in all_test_decisions)
    total_pnl = sum(d["pnl"] for d in all_test_decisions)
    wins = sum(1 for d in all_test_decisions if d["outcome"] == 1 and d["stake"] > 0)
    bets = sum(1 for d in all_test_decisions if d["stake"] > 0)

    return {
        "log_wealth": log_w,
        "roi": total_pnl / max(total_staked, 1),
        "win_rate": wins / max(bets, 1),
        "total_pnl": total_pnl,
        "decisions_count": len(all_test_decisions),
    }


def run_optimization(records: list[dict], n_trials: int = N_TRIALS) -> dict:
    """Run Optuna optimization over walk-forward folds.

    Args:
        records: backtest records from simulate_historical()
        n_trials: number of Optuna trials

    Returns:
        {best_params, best_log_wealth, best_roi, sharpe, dsr, duration_s, ...}
    """
    t0 = time.monotonic()
    records = [r for r in records if r.get("edge", 0) >= 0.05]
    records.sort(key=lambda r: r["date"])
    folds = create_walk_forward_folds(records)

    if not folds:
        return {"error": "Not enough data for walk-forward validation"}

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = _sample_params(trial)
        result = _evaluate_params(params, folds)
        return result["log_wealth"]

    study.optimize(objective, n_trials=n_trials)

    best = study.best_trial
    best_result = _evaluate_params(best.params, folds)

    # Compute Sharpe and DSR
    from app.autopilot.performance import compute_sharpe

    sharpe = best_result.get("roi", 0) * 2.0  # approximate
    dsr = deflated_sharpe(sharpe, n_trials, best_result["decisions_count"])

    duration = round(time.monotonic() - t0, 1)

    # Count active features
    feature_names_no_bias = FEATURE_NAMES[:-1]
    n_features = sum(
        1 for name in feature_names_no_bias
        if best.params.get(f"use_{name}", True)
    )

    return {
        "best_params": best.params,
        "best_log_wealth": round(best.value, 6),
        "best_roi": round(best_result["roi"], 4),
        "sharpe": round(sharpe, 3),
        "dsr": round(dsr, 3),
        "n_features": n_features,
        "n_trials": n_trials,
        "n_folds": len(folds),
        "records_used": len(records),
        "duration_s": duration,
    }


# CLI entry point for the LLM outer loop
if __name__ == "__main__":
    import asyncio

    from app.backtest.simulator import simulate_historical
    from app.db import async_session

    async def main() -> None:
        async with async_session() as db:
            records = await simulate_historical(db)
        result = run_optimization(records)
        print("---")
        for k, v in result.items():
            if k == "best_params":
                print(f"best_params: {json.dumps(v, default=str)}")
            else:
                print(f"{k}: {v}")

    asyncio.run(main())
