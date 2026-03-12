"""Performance metrics for the autopilot agent."""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from app.autopilot.agent import ACTION_LABELS, ACTIONS


def compute_sharpe(daily_pnls: list[float], risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio from daily P&L series."""
    if len(daily_pnls) < 2:
        return 0.0
    arr = np.array(daily_pnls, dtype=np.float64)
    excess = arr - risk_free / 252
    std = np.std(excess, ddof=1)
    if std == 0:
        return 0.0
    return float(np.mean(excess) / std * math.sqrt(252))


def compute_metrics(decisions: list) -> dict:
    """Compute full performance metrics from a list of AutopilotDecision ORM objects.

    Returns:
        total_bets, wins, losses, win_rate,
        total_staked, total_pnl, roi,
        sharpe, pnl_curve, action_distribution
    """
    settled = [d for d in decisions if d.result in ("won", "lost")]

    total_bets = len(settled)
    wins = sum(1 for d in settled if d.result == "won")
    losses = total_bets - wins
    win_rate = wins / total_bets if total_bets > 0 else 0.0

    total_staked = sum(d.stake for d in settled if d.stake)
    total_pnl = sum(d.pnl for d in settled if d.pnl is not None)
    roi = total_pnl / max(total_staked, 1.0)

    # Daily P&L for Sharpe
    daily: dict[str, float] = defaultdict(float)
    for d in settled:
        day_key = (
            d.created_utc.date().isoformat()
            if d.created_utc
            else "unknown"
        )
        daily[day_key] += d.pnl or 0.0

    sorted_days = sorted(daily.keys())
    daily_pnls = [daily[k] for k in sorted_days]
    sharpe = compute_sharpe(daily_pnls)

    # Cumulative P&L curve
    cum = 0.0
    pnl_curve = []
    for day in sorted_days:
        cum += daily[day]
        pnl_curve.append({"date": day, "pnl": round(daily[day], 2), "cumulative": round(cum, 2)})

    # Action distribution (all decisions, not just settled)
    action_dist: dict[int, int] = dict.fromkeys(range(len(ACTIONS)), 0)
    for d in decisions:
        action_dist[d.action_idx] = action_dist.get(d.action_idx, 0) + 1

    return {
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "total_staked": round(total_staked, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(roi, 4),
        "sharpe": round(sharpe, 3),
        "pnl_curve": pnl_curve,
        "action_distribution": {
            ACTION_LABELS[i]: action_dist[i] for i in range(len(ACTIONS))
        },
    }


def compute_brier_score(decisions: list) -> float | None:
    """Brier score: mean squared error of predicted probability vs outcome.
    Lower is better. 0.25 = coin flip baseline."""
    scored = [d for d in decisions if d.result in ("won", "lost") and d.best_odds and d.best_odds > 1]
    if not scored:
        return None
    total = 0.0
    for d in scored:
        predicted_prob = 1.0 / d.best_odds
        actual = 1.0 if d.result == "won" else 0.0
        total += (predicted_prob - actual) ** 2
    return round(total / len(scored), 6)


def compute_calibration_buckets(decisions: list, n_buckets: int = 5) -> list[dict]:
    """Group decisions by predicted probability bucket.
    Returns list of {bucket, predicted, actual, count}."""
    scored = [d for d in decisions if d.result in ("won", "lost") and d.best_odds and d.best_odds > 1]
    if not scored:
        return []

    scored.sort(key=lambda d: 1.0 / d.best_odds)
    bucket_size = max(len(scored) // n_buckets, 1)
    buckets = []

    for i in range(0, len(scored), bucket_size):
        chunk = scored[i:i + bucket_size]
        if not chunk:
            break
        probs = [1.0 / d.best_odds for d in chunk]
        actuals = [1.0 if d.result == "won" else 0.0 for d in chunk]
        avg_pred = sum(probs) / len(probs)
        avg_actual = sum(actuals) / len(actuals)
        lo = min(probs)
        hi = max(probs)
        buckets.append({
            "bucket": f"{lo*100:.0f}-{hi*100:.0f}%",
            "predicted": round(avg_pred, 4),
            "actual": round(avg_actual, 4),
            "count": len(chunk),
        })

    return buckets
