"""Backtesting framework."""

from app.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    calculate_brier_score,
    calculate_calibration,
    calculate_roi,
    generate_backtest_report,
    walk_forward_split,
)
from app.backtest.outcome_resolver import load_outcomes_for_fixtures, resolve_outcome
from app.backtest.simulator import simulate_historical

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "calculate_brier_score",
    "calculate_calibration",
    "calculate_roi",
    "walk_forward_split",
    "generate_backtest_report",
    "load_outcomes_for_fixtures",
    "resolve_outcome",
    "simulate_historical",
]
