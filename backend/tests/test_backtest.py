"""Tests for backtesting framework.

RED phase: Write failing tests first.
"""

import pytest
from datetime import datetime, date
from unittest.mock import Mock

from app.backtest.engine import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult,
    calculate_brier_score,
    calculate_calibration,
    calculate_roi,
    walk_forward_split,
)


class TestBrierScore:
    """Tests for Brier score calculation."""

    def test_perfect_predictions(self):
        """Perfect predictions = Brier 0."""
        predictions = [1.0, 0.0, 1.0, 0.0]
        outcomes = [1, 0, 1, 0]  # 1 = happened, 0 = didn't
        
        score = calculate_brier_score(predictions, outcomes)
        assert score == 0.0

    def test_worst_predictions(self):
        """Completely wrong = Brier 1."""
        predictions = [0.0, 1.0, 0.0, 1.0]
        outcomes = [1, 0, 1, 0]
        
        score = calculate_brier_score(predictions, outcomes)
        assert score == 1.0

    def test_random_predictions(self):
        """50/50 predictions on 50/50 outcomes = Brier 0.25."""
        predictions = [0.5, 0.5, 0.5, 0.5]
        outcomes = [1, 0, 1, 0]
        
        score = calculate_brier_score(predictions, outcomes)
        assert abs(score - 0.25) < 0.001

    def test_typical_goalscorer_range(self):
        """Goalscorer predictions should have Brier < 0.25."""
        # Typical predictions: 30-40% probability
        predictions = [0.35, 0.28, 0.42, 0.31, 0.38]
        # Typical outcomes: ~35% score
        outcomes = [1, 0, 1, 0, 0]
        
        score = calculate_brier_score(predictions, outcomes)
        # Should be reasonable (< 0.25 for decent model)
        assert 0.1 <= score <= 0.3


class TestCalibration:
    """Tests for calibration analysis."""

    def test_well_calibrated(self):
        """Well-calibrated model: predicted ~= actual rate."""
        # 10 predictions of 30%, 3 happened = perfect calibration
        predictions = [0.3] * 10
        outcomes = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
        
        buckets = calculate_calibration(predictions, outcomes, n_buckets=5)
        
        # Bucket containing 0.3 should have ~0.3 actual rate
        bucket_30 = next(b for b in buckets if 0.2 <= b["predicted_mean"] <= 0.4)
        assert abs(bucket_30["actual_rate"] - 0.3) < 0.1

    def test_returns_bucket_structure(self):
        predictions = [0.1, 0.2, 0.5, 0.8, 0.9]
        outcomes = [0, 0, 1, 1, 1]
        
        buckets = calculate_calibration(predictions, outcomes, n_buckets=3)
        
        assert len(buckets) <= 3
        for bucket in buckets:
            assert "predicted_mean" in bucket
            assert "actual_rate" in bucket
            assert "count" in bucket


class TestROI:
    """Tests for ROI calculation."""

    def test_profitable_bets(self):
        """Winning bets at good odds = positive ROI."""
        bets = [
            {"stake": 10, "odds": 2.5, "won": True},   # +15
            {"stake": 10, "odds": 2.0, "won": True},   # +10
            {"stake": 10, "odds": 3.0, "won": False},  # -10
            {"stake": 10, "odds": 2.5, "won": False},  # -10
        ]
        
        roi = calculate_roi(bets)
        # Total staked: 40, Returns: 25 + 20 = 45, Profit: 5
        # ROI = 5/40 = 12.5%
        assert abs(roi - 0.125) < 0.01

    def test_losing_strategy(self):
        """All losses = -100% ROI."""
        bets = [
            {"stake": 10, "odds": 2.0, "won": False},
            {"stake": 10, "odds": 2.0, "won": False},
        ]
        
        roi = calculate_roi(bets)
        assert roi == -1.0

    def test_breakeven(self):
        """Breakeven = 0% ROI."""
        bets = [
            {"stake": 10, "odds": 2.0, "won": True},   # +10
            {"stake": 10, "odds": 2.0, "won": False},  # -10
        ]
        
        roi = calculate_roi(bets)
        assert abs(roi) < 0.01


class TestWalkForwardSplit:
    """Tests for walk-forward validation splits."""

    def test_creates_correct_number_of_splits(self):
        dates = [date(2024, 1, i) for i in range(1, 31)]  # 30 days
        
        splits = walk_forward_split(
            dates,
            train_days=14,
            test_days=7,
            step_days=7,
        )
        
        # Should create multiple train/test splits
        assert len(splits) >= 2

    def test_no_leakage(self):
        """Test data must come AFTER train data."""
        dates = [date(2024, 1, i) for i in range(1, 31)]
        
        splits = walk_forward_split(dates, train_days=14, test_days=7, step_days=7)
        
        for split in splits:
            train_end = max(split["train"])
            test_start = min(split["test"])
            assert test_start > train_end

    def test_returns_split_structure(self):
        dates = [date(2024, 1, i) for i in range(1, 31)]
        
        splits = walk_forward_split(dates, train_days=14, test_days=7, step_days=7)
        
        for split in splits:
            assert "train" in split
            assert "test" in split
            assert len(split["train"]) > 0
            assert len(split["test"]) > 0


class TestBacktestConfig:
    """Tests for backtest configuration."""

    def test_default_config(self):
        config = BacktestConfig()
        
        assert config.min_edge >= 0.0
        assert config.stake_method in ["flat", "kelly"]
        assert config.flat_stake > 0

    def test_custom_config(self):
        config = BacktestConfig(
            min_edge=0.08,
            max_odds=10.0,
            min_odds=1.5,
            stake_method="kelly",
            kelly_fraction=0.25,
        )
        
        assert config.min_edge == 0.08
        assert config.kelly_fraction == 0.25


class TestBacktestEngine:
    """Tests for backtest engine."""

    @pytest.fixture
    def sample_data(self):
        """Sample historical data for backtesting."""
        return [
            {
                "date": date(2024, 1, 15),
                "fixture_id": "f1",
                "player": "Mbappe",
                "market": "goalscorer",
                "fair_prob": 0.40,
                "fair_odds": 2.50,
                "market_odds": 2.80,
                "edge": 0.12,
                "outcome": 1,  # Scored
            },
            {
                "date": date(2024, 1, 15),
                "fixture_id": "f1",
                "player": "Dembele",
                "market": "goalscorer",
                "fair_prob": 0.25,
                "fair_odds": 4.00,
                "market_odds": 4.50,
                "edge": 0.125,
                "outcome": 0,  # Didn't score
            },
            {
                "date": date(2024, 1, 22),
                "fixture_id": "f2",
                "player": "Mbappe",
                "market": "goalscorer",
                "fair_prob": 0.38,
                "fair_odds": 2.63,
                "market_odds": 2.90,
                "edge": 0.10,
                "outcome": 1,
            },
        ]

    def test_runs_backtest(self, sample_data):
        config = BacktestConfig(min_edge=0.05)
        engine = BacktestEngine(config)
        
        result = engine.run(sample_data)
        
        assert isinstance(result, BacktestResult)
        assert result.total_bets > 0

    def test_filters_by_min_edge(self, sample_data):
        # High min_edge = fewer bets
        config = BacktestConfig(min_edge=0.15)
        engine = BacktestEngine(config)
        
        result = engine.run(sample_data)
        
        # Only bets with edge >= 15% should be included
        assert result.total_bets < len(sample_data)

    def test_calculates_metrics(self, sample_data):
        config = BacktestConfig(min_edge=0.05)
        engine = BacktestEngine(config)
        
        result = engine.run(sample_data)
        
        assert "brier_score" in result.metrics
        assert "roi" in result.metrics
        assert "win_rate" in result.metrics

    def test_result_includes_bet_log(self, sample_data):
        config = BacktestConfig(min_edge=0.05)
        engine = BacktestEngine(config)
        
        result = engine.run(sample_data)
        
        assert len(result.bets) > 0
        for bet in result.bets:
            assert "stake" in bet
            assert "odds" in bet
            assert "pnl" in bet
