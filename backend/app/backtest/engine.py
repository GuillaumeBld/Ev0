"""Backtesting engine for strategy validation.

Implements walk-forward validation with proper train/test splits
to avoid data leakage.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal
import math


def calculate_brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """
    Calculate Brier score for probability predictions.
    
    Brier = mean((prediction - outcome)^2)
    
    Lower is better:
    - 0.0 = perfect predictions
    - 0.25 = random 50/50 on 50/50 outcomes
    - 1.0 = perfectly wrong
    
    Args:
        predictions: Predicted probabilities (0-1)
        outcomes: Actual outcomes (1 = happened, 0 = didn't)
    
    Returns:
        Brier score
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")
    
    if not predictions:
        return 0.0
    
    squared_errors = [(p - o) ** 2 for p, o in zip(predictions, outcomes)]
    return sum(squared_errors) / len(squared_errors)


def calculate_calibration(
    predictions: list[float],
    outcomes: list[int],
    n_buckets: int = 10,
) -> list[dict[str, Any]]:
    """
    Calculate calibration buckets.
    
    Groups predictions into buckets and compares predicted vs actual rates.
    Well-calibrated model: predicted_mean ≈ actual_rate for each bucket.
    
    Args:
        predictions: Predicted probabilities
        outcomes: Actual outcomes
        n_buckets: Number of buckets
    
    Returns:
        List of bucket dicts with predicted_mean, actual_rate, count
    """
    if not predictions:
        return []
    
    # Create buckets
    bucket_size = 1.0 / n_buckets
    buckets = [[] for _ in range(n_buckets)]
    
    for pred, outcome in zip(predictions, outcomes):
        bucket_idx = min(int(pred / bucket_size), n_buckets - 1)
        buckets[bucket_idx].append((pred, outcome))
    
    # Calculate stats per bucket
    results = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        
        preds = [b[0] for b in bucket]
        outs = [b[1] for b in bucket]
        
        results.append({
            "bucket": i,
            "range": (i * bucket_size, (i + 1) * bucket_size),
            "predicted_mean": sum(preds) / len(preds),
            "actual_rate": sum(outs) / len(outs),
            "count": len(bucket),
        })
    
    return results


def calculate_roi(bets: list[dict[str, Any]]) -> float:
    """
    Calculate Return on Investment.
    
    ROI = (Total Returns - Total Staked) / Total Staked
    
    Args:
        bets: List of bet dicts with stake, odds, won
    
    Returns:
        ROI as decimal (0.1 = 10%)
    """
    if not bets:
        return 0.0
    
    total_staked = sum(b["stake"] for b in bets)
    if total_staked == 0:
        return 0.0
    
    total_returns = sum(
        b["stake"] * b["odds"] if b["won"] else 0
        for b in bets
    )
    
    return (total_returns - total_staked) / total_staked


def walk_forward_split(
    dates: list[date],
    train_days: int = 90,
    test_days: int = 30,
    step_days: int = 30,
) -> list[dict[str, list[date]]]:
    """
    Create walk-forward validation splits.
    
    Ensures no data leakage: test data always comes AFTER train data.
    
    Args:
        dates: Sorted list of dates
        train_days: Training period length
        test_days: Test period length
        step_days: Days to step forward between splits
    
    Returns:
        List of splits with train/test date lists
    """
    if not dates:
        return []
    
    dates = sorted(set(dates))
    splits = []
    
    # Find date range
    min_date = min(dates)
    max_date = max(dates)
    
    # Create splits
    current_start = min_date
    
    while True:
        train_end = date.fromordinal(current_start.toordinal() + train_days - 1)
        test_start = date.fromordinal(train_end.toordinal() + 1)
        test_end = date.fromordinal(test_start.toordinal() + test_days - 1)
        
        if test_end > max_date:
            break
        
        train_dates = [d for d in dates if current_start <= d <= train_end]
        test_dates = [d for d in dates if test_start <= d <= test_end]
        
        if train_dates and test_dates:
            splits.append({
                "train": train_dates,
                "test": test_dates,
                "train_range": (current_start, train_end),
                "test_range": (test_start, test_end),
            })
        
        current_start = date.fromordinal(current_start.toordinal() + step_days)
    
    return splits


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    
    # Filters
    min_edge: float = 0.05  # Minimum edge to bet
    max_odds: float = 15.0  # Maximum odds to bet
    min_odds: float = 1.2   # Minimum odds to bet
    min_confidence: float = 0.5
    
    # Stake sizing
    stake_method: Literal["flat", "kelly"] = "flat"
    flat_stake: float = 10.0
    kelly_fraction: float = 0.25  # Quarter Kelly
    max_stake: float = 100.0
    
    # Validation
    train_days: int = 90
    test_days: int = 30
    step_days: int = 30


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    
    total_bets: int
    wins: int
    losses: int
    total_staked: float
    total_returns: float
    profit: float
    
    metrics: dict[str, float] = field(default_factory=dict)
    bets: list[dict[str, Any]] = field(default_factory=list)
    calibration: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def win_rate(self) -> float:
        if self.total_bets == 0:
            return 0.0
        return self.wins / self.total_bets
    
    @property
    def roi(self) -> float:
        if self.total_staked == 0:
            return 0.0
        return self.profit / self.total_staked


class BacktestEngine:
    """Engine for running backtests on historical data."""
    
    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()
    
    def run(self, data: list[dict[str, Any]]) -> BacktestResult:
        """
        Run backtest on historical data.
        
        Args:
            data: List of historical records with:
                - date, fixture_id, player, market
                - fair_prob, fair_odds, market_odds, edge
                - outcome (1 = happened, 0 = didn't)
        
        Returns:
            BacktestResult with metrics and bet log
        """
        # Filter by config
        filtered = self._filter_bets(data)
        
        # Calculate stakes and simulate bets
        bets = []
        predictions = []
        outcomes = []
        
        for record in filtered:
            stake = self._calculate_stake(record)
            won = record["outcome"] == 1
            pnl = (stake * record["market_odds"] - stake) if won else -stake
            
            bets.append({
                "date": record["date"],
                "fixture_id": record["fixture_id"],
                "player": record["player"],
                "market": record["market"],
                "fair_prob": record["fair_prob"],
                "market_odds": record["market_odds"],
                "edge": record["edge"],
                "stake": stake,
                "odds": record["market_odds"],
                "won": won,
                "pnl": pnl,
            })
            
            predictions.append(record["fair_prob"])
            outcomes.append(record["outcome"])
        
        # Calculate metrics
        total_staked = sum(b["stake"] for b in bets)
        total_returns = sum(b["stake"] * b["odds"] if b["won"] else 0 for b in bets)
        profit = total_returns - total_staked
        wins = sum(1 for b in bets if b["won"])
        
        metrics = {
            "brier_score": calculate_brier_score(predictions, outcomes) if predictions else 0,
            "roi": profit / total_staked if total_staked > 0 else 0,
            "win_rate": wins / len(bets) if bets else 0,
            "avg_odds": sum(b["odds"] for b in bets) / len(bets) if bets else 0,
            "avg_edge": sum(b["edge"] for b in bets) / len(bets) if bets else 0,
        }
        
        calibration = calculate_calibration(predictions, outcomes) if predictions else []
        
        return BacktestResult(
            total_bets=len(bets),
            wins=wins,
            losses=len(bets) - wins,
            total_staked=total_staked,
            total_returns=total_returns,
            profit=profit,
            metrics=metrics,
            bets=bets,
            calibration=calibration,
        )
    
    def run_walk_forward(self, data: list[dict[str, Any]]) -> list[BacktestResult]:
        """
        Run walk-forward validation.
        
        Creates multiple train/test splits and evaluates on each.
        """
        # Extract dates
        dates = [record["date"] for record in data]
        
        splits = walk_forward_split(
            dates,
            train_days=self.config.train_days,
            test_days=self.config.test_days,
            step_days=self.config.step_days,
        )
        
        results = []
        for split in splits:
            test_dates = set(split["test"])
            test_data = [r for r in data if r["date"] in test_dates]
            
            if test_data:
                result = self.run(test_data)
                results.append(result)
        
        return results
    
    def _filter_bets(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter records by config criteria."""
        filtered = []
        
        for record in data:
            edge = record.get("edge", 0)
            odds = record.get("market_odds", 0)
            confidence = record.get("confidence", 1.0)
            
            if edge < self.config.min_edge:
                continue
            if odds < self.config.min_odds or odds > self.config.max_odds:
                continue
            if confidence < self.config.min_confidence:
                continue
            
            filtered.append(record)
        
        return filtered
    
    def _calculate_stake(self, record: dict[str, Any]) -> float:
        """Calculate stake based on config method."""
        if self.config.stake_method == "flat":
            return self.config.flat_stake
        
        elif self.config.stake_method == "kelly":
            # Kelly: f* = (bp - q) / b
            # where b = odds - 1, p = probability, q = 1 - p
            prob = record["fair_prob"]
            odds = record["market_odds"]
            b = odds - 1
            
            kelly = (b * prob - (1 - prob)) / b
            kelly = max(0, kelly)  # Don't bet negative
            
            # Apply fraction and cap
            stake = kelly * self.config.kelly_fraction * 1000  # Assume 1000 bankroll
            stake = min(stake, self.config.max_stake)
            
            return stake
        
        return self.config.flat_stake


def generate_backtest_report(result: BacktestResult) -> dict[str, Any]:
    """Generate a summary report from backtest results."""
    return {
        "summary": {
            "total_bets": result.total_bets,
            "wins": result.wins,
            "losses": result.losses,
            "win_rate": f"{result.win_rate:.1%}",
            "roi": f"{result.roi:.1%}",
            "profit": f"{result.profit:.2f}",
        },
        "metrics": {
            "brier_score": f"{result.metrics.get('brier_score', 0):.4f}",
            "avg_odds": f"{result.metrics.get('avg_odds', 0):.2f}",
            "avg_edge": f"{result.metrics.get('avg_edge', 0):.1%}",
        },
        "calibration": result.calibration,
        "bet_count_by_edge": _count_by_edge(result.bets),
    }


def _count_by_edge(bets: list[dict]) -> dict[str, int]:
    """Count bets by edge bucket."""
    buckets = {"5-10%": 0, "10-15%": 0, "15-20%": 0, "20%+": 0}
    
    for bet in bets:
        edge = bet.get("edge", 0)
        if edge >= 0.20:
            buckets["20%+"] += 1
        elif edge >= 0.15:
            buckets["15-20%"] += 1
        elif edge >= 0.10:
            buckets["10-15%"] += 1
        else:
            buckets["5-10%"] += 1
    
    return buckets
