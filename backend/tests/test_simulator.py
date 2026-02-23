"""Tests for the historical simulator."""

from datetime import date

import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.outcome_resolver import resolve_outcome


class TestSimulatorOutputFormat:
    """Tests that simulator output matches BacktestEngine input format."""

    @pytest.fixture
    def sample_records(self):
        """Sample records in the format simulate_historical() would produce."""
        return [
            {
                "date": date(2025, 9, 15),
                "fixture_id": "fotmob_12345",
                "player": "Kylian Mbappe",
                "market": "goalscorer",
                "fair_prob": 0.38,
                "fair_odds": 2.63,
                "market_odds": 2.90,
                "edge": 0.103,
                "confidence": 0.80,
                "outcome": 1,
            },
            {
                "date": date(2025, 9, 15),
                "fixture_id": "fotmob_12345",
                "player": "Ousmane Dembele",
                "market": "goalscorer",
                "fair_prob": 0.22,
                "fair_odds": 4.55,
                "market_odds": 5.00,
                "edge": 0.099,
                "confidence": 0.65,
                "outcome": 0,
            },
            {
                "date": date(2025, 9, 22),
                "fixture_id": "fotmob_12346",
                "player": "Bradley Barcola",
                "market": "goalscorer",
                "fair_prob": 0.25,
                "fair_odds": 4.00,
                "market_odds": 4.50,
                "edge": 0.125,
                "confidence": 0.55,
                "outcome": 1,
            },
        ]

    def test_records_have_required_fields(self, sample_records):
        """All records must have the fields BacktestEngine expects."""
        required = {"date", "fixture_id", "player", "market", "fair_prob",
                     "fair_odds", "market_odds", "edge", "outcome"}
        for record in sample_records:
            assert required.issubset(record.keys()), f"Missing fields: {required - record.keys()}"

    def test_backtest_engine_accepts_records(self, sample_records):
        """BacktestEngine.run() should work with simulator output format."""
        config = BacktestConfig(min_edge=0.05)
        engine = BacktestEngine(config)

        result = engine.run(sample_records)

        assert result.total_bets > 0
        assert result.wins + result.losses == result.total_bets
        assert "brier_score" in result.metrics
        assert "roi" in result.metrics

    def test_edge_filtering_works(self, sample_records):
        """High min_edge should filter out some records."""
        config = BacktestConfig(min_edge=0.15)
        engine = BacktestEngine(config)

        result = engine.run(sample_records)

        # All records have edge < 0.15, so no bets should pass
        assert result.total_bets == 0

    def test_outcomes_are_binary(self, sample_records):
        """Outcomes must be 0 or 1."""
        for record in sample_records:
            assert record["outcome"] in (0, 1)


class TestOutcomeResolverIntegration:
    """Test outcome resolver works correctly with typical match events."""

    def test_multi_goal_match(self):
        """Match with multiple goals resolves correctly for each player."""
        events = [
            {"player_name": "Mbappe", "event_type": "goal", "minute": 12},
            {"player_name": "Barcola", "event_type": "assist", "minute": 12},
            {"player_name": "Dembele", "event_type": "goal", "minute": 45},
            {"player_name": "Mbappe", "event_type": "assist", "minute": 45},
            {"player_name": "Mbappe", "event_type": "goal", "minute": 78},
        ]

        # Mbappe scored and assisted
        assert resolve_outcome("Mbappe", "goalscorer", events) == 1
        assert resolve_outcome("Mbappe", "assist", events) == 1

        # Dembele scored only
        assert resolve_outcome("Dembele", "goalscorer", events) == 1
        assert resolve_outcome("Dembele", "assist", events) == 0

        # Barcola assisted only
        assert resolve_outcome("Barcola", "goalscorer", events) == 0
        assert resolve_outcome("Barcola", "assist", events) == 1

    def test_0_0_draw(self):
        """0-0 draw: no one scored, all outcomes should be 0."""
        events = []  # No goal events

        assert resolve_outcome("Anyone", "goalscorer", events) == 0
        assert resolve_outcome("Anyone", "assist", events) == 0
