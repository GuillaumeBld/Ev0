"""Walk-forward validation with expanding window and temporal purge."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np

PURGE_DAYS = 30  # Gap between train and test to prevent lookahead
MIN_TRAIN_RECORDS = 200  # Minimum records in training set
N_FOLDS = 5


def create_walk_forward_folds(
    records: list[dict],
    n_folds: int = N_FOLDS,
    purge_days: int = PURGE_DAYS,
) -> list[tuple[list[dict], list[dict]]]:
    """Split time-sorted records into expanding train/test folds with purge gap.

    Returns list of (train_records, test_records) tuples.
    """
    records = sorted(records, key=lambda r: r["date"])
    dates = [r["date"] for r in records]
    min_date, max_date = dates[0], dates[-1]
    total_days = (max_date - min_date).days

    # Each test window covers total_days / (n_folds + 1) days
    test_window = total_days // (n_folds + 1)
    if test_window < 7:
        return []

    folds = []
    for fold_idx in range(n_folds):
        test_start = min_date + timedelta(days=test_window * (fold_idx + 1))
        test_end = test_start + timedelta(days=test_window)

        train_cutoff = test_start - timedelta(days=purge_days)
        train = [r for r in records if r["date"] < train_cutoff]
        test = [r for r in records if test_start <= r["date"] < test_end]

        if len(train) >= MIN_TRAIN_RECORDS and len(test) >= 20:
            folds.append((train, test))

    return folds


def compute_log_wealth(
    decisions: list[dict],
    initial_bankroll: float = 1000.0,
) -> float:
    """Compute log-wealth growth rate from a list of {stake, pnl} decisions.

    log_wealth = (1/T) * sum(log(1 + pnl_t / bankroll_t))

    This is equivalent to the Kelly criterion's growth rate.
    """
    if not decisions:
        return 0.0

    bankroll = initial_bankroll
    log_returns: list[float] = []

    for d in decisions:
        pnl = d.get("pnl", 0.0)
        if bankroll <= 0:
            break
        log_returns.append(np.log(max(1 + pnl / bankroll, 1e-10)))
        bankroll += pnl

    return float(np.mean(log_returns)) if log_returns else 0.0


def deflated_sharpe(sharpe: float, n_trials: int, T: int) -> float:
    """Deflated Sharpe Ratio — corrects for multiple testing.

    Args:
        sharpe: observed Sharpe ratio
        n_trials: number of strategies tried so far
        T: number of observations
    """
    from scipy import stats as sp_stats

    if n_trials <= 1 or T <= 1:
        return sharpe

    expected_max = sp_stats.norm.ppf(1 - 1 / n_trials) * math.sqrt(
        max(1 - 0.5 * math.log(T), 0.01)
    )
    denom = max(1.0, math.sqrt(2 * max(1 - 0.5 * math.log(T), 0.01) / T))
    dsr = (sharpe - expected_max) / denom
    return float(dsr)
