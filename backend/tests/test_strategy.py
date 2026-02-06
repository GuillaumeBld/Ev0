"""Tests for strategy and selection logic.

RED phase: Write failing tests first.
"""

import pytest
from datetime import datetime, date

from app.strategy.selector import (
    RecommendationFilter,
    SelectionResult,
    filter_recommendations,
    rank_by_value,
    check_correlation,
    calculate_kelly_stake,
    apply_exposure_limits,
)


class TestRecommendationFilter:
    """Tests for recommendation filtering."""

    @pytest.fixture
    def sample_recommendations(self):
        return [
            {
                "id": "1",
                "player": "Mbappe",
                "fixture_id": "f1",
                "team": "PSG",
                "market": "goalscorer",
                "edge": 0.12,
                "fair_odds": 2.50,
                "market_odds": 2.80,
                "confidence": 0.85,
            },
            {
                "id": "2",
                "player": "Dembele",
                "fixture_id": "f1",
                "team": "PSG",
                "market": "goalscorer",
                "edge": 0.08,
                "fair_odds": 3.50,
                "market_odds": 3.80,
                "confidence": 0.75,
            },
            {
                "id": "3",
                "player": "Salah",
                "fixture_id": "f2",
                "team": "Liverpool",
                "market": "goalscorer",
                "edge": 0.15,
                "fair_odds": 2.20,
                "market_odds": 2.50,
                "confidence": 0.90,
            },
            {
                "id": "4",
                "player": "Low Edge",
                "fixture_id": "f3",
                "team": "Team",
                "market": "goalscorer",
                "edge": 0.03,
                "fair_odds": 5.00,
                "market_odds": 5.20,
                "confidence": 0.60,
            },
        ]

    def test_filters_by_min_edge(self, sample_recommendations):
        filter_config = RecommendationFilter(min_edge=0.10)
        
        result = filter_recommendations(sample_recommendations, filter_config)
        
        # Only Mbappe (12%) and Salah (15%) should pass
        assert len(result) == 2
        assert all(r["edge"] >= 0.10 for r in result)

    def test_filters_by_min_confidence(self, sample_recommendations):
        filter_config = RecommendationFilter(min_confidence=0.80)
        
        result = filter_recommendations(sample_recommendations, filter_config)
        
        assert all(r["confidence"] >= 0.80 for r in result)

    def test_filters_by_odds_range(self, sample_recommendations):
        filter_config = RecommendationFilter(
            min_odds=2.0,
            max_odds=3.0,
        )
        
        result = filter_recommendations(sample_recommendations, filter_config)
        
        assert all(2.0 <= r["market_odds"] <= 3.0 for r in result)

    def test_combined_filters(self, sample_recommendations):
        filter_config = RecommendationFilter(
            min_edge=0.05,
            min_confidence=0.70,
            min_odds=2.0,
            max_odds=4.0,
        )
        
        result = filter_recommendations(sample_recommendations, filter_config)
        
        for r in result:
            assert r["edge"] >= 0.05
            assert r["confidence"] >= 0.70
            assert 2.0 <= r["market_odds"] <= 4.0


class TestRankByValue:
    """Tests for value-based ranking."""

    def test_ranks_by_edge(self):
        recs = [
            {"id": "1", "edge": 0.10, "confidence": 0.80},
            {"id": "2", "edge": 0.15, "confidence": 0.75},
            {"id": "3", "edge": 0.08, "confidence": 0.90},
        ]
        
        ranked = rank_by_value(recs, method="edge")
        
        # Highest edge first
        assert ranked[0]["id"] == "2"
        assert ranked[1]["id"] == "1"

    def test_ranks_by_ev(self):
        """Expected value = edge * odds * stake."""
        recs = [
            {"id": "1", "edge": 0.10, "market_odds": 2.0},  # EV: 0.20
            {"id": "2", "edge": 0.05, "market_odds": 5.0},  # EV: 0.25
            {"id": "3", "edge": 0.08, "market_odds": 3.0},  # EV: 0.24
        ]
        
        ranked = rank_by_value(recs, method="ev")
        
        # Highest EV first
        assert ranked[0]["id"] == "2"

    def test_ranks_by_composite(self):
        """Composite = edge * confidence."""
        recs = [
            {"id": "1", "edge": 0.10, "confidence": 0.80},  # 0.08
            {"id": "2", "edge": 0.15, "confidence": 0.50},  # 0.075
            {"id": "3", "edge": 0.08, "confidence": 0.90},  # 0.072
        ]
        
        ranked = rank_by_value(recs, method="composite")
        
        assert ranked[0]["id"] == "1"


class TestCorrelationCheck:
    """Tests for correlation detection between bets."""

    def test_same_match_correlated(self):
        bets = [
            {"fixture_id": "f1", "player": "Mbappe", "team": "PSG"},
            {"fixture_id": "f1", "player": "Dembele", "team": "PSG"},
        ]
        
        correlation = check_correlation(bets[0], bets[1])
        
        # Same match = high correlation
        assert correlation > 0.5

    def test_same_team_correlated(self):
        bets = [
            {"fixture_id": "f1", "player": "Mbappe", "team": "PSG"},
            {"fixture_id": "f2", "player": "Hakimi", "team": "PSG"},
        ]
        
        correlation = check_correlation(bets[0], bets[1])
        
        # Same team, different match = moderate correlation
        assert 0.2 <= correlation <= 0.6

    def test_different_teams_uncorrelated(self):
        bets = [
            {"fixture_id": "f1", "player": "Mbappe", "team": "PSG"},
            {"fixture_id": "f2", "player": "Salah", "team": "Liverpool"},
        ]
        
        correlation = check_correlation(bets[0], bets[1])
        
        # Different everything = low correlation
        assert correlation < 0.2


class TestKellyStake:
    """Tests for Kelly criterion stake calculation."""

    def test_positive_edge_gives_positive_stake(self):
        stake = calculate_kelly_stake(
            probability=0.40,
            odds=3.0,
            bankroll=1000,
            fraction=0.25,
        )
        
        # Edge = (3 * 0.4 - 0.6) / 2 = 0.3
        # Full Kelly = 30% of bankroll
        # Quarter Kelly = 7.5%
        assert stake > 0

    def test_no_edge_gives_zero_stake(self):
        stake = calculate_kelly_stake(
            probability=0.33,  # Fair odds = 3.0
            odds=3.0,
            bankroll=1000,
            fraction=0.25,
        )
        
        assert stake == 0

    def test_negative_edge_gives_zero_stake(self):
        stake = calculate_kelly_stake(
            probability=0.30,
            odds=3.0,  # Implied prob 33%
            bankroll=1000,
            fraction=0.25,
        )
        
        # We think 30%, market thinks 33% - negative edge
        assert stake == 0

    def test_respects_fraction(self):
        full_kelly = calculate_kelly_stake(0.40, 3.0, 1000, 1.0)
        quarter_kelly = calculate_kelly_stake(0.40, 3.0, 1000, 0.25)
        
        assert abs(quarter_kelly - full_kelly * 0.25) < 0.01


class TestExposureLimits:
    """Tests for exposure limit enforcement."""

    def test_limits_per_match(self):
        bets = [
            {"fixture_id": "f1", "stake": 50},
            {"fixture_id": "f1", "stake": 50},
            {"fixture_id": "f1", "stake": 50},
        ]
        
        limited = apply_exposure_limits(
            bets,
            max_per_match=100,
            max_per_day=500,
        )
        
        # Total for f1 should be capped at 100
        f1_total = sum(b["stake"] for b in limited if b["fixture_id"] == "f1")
        assert f1_total <= 100

    def test_limits_per_day(self):
        bets = [
            {"fixture_id": "f1", "stake": 100},
            {"fixture_id": "f2", "stake": 100},
            {"fixture_id": "f3", "stake": 100},
            {"fixture_id": "f4", "stake": 100},
        ]
        
        limited = apply_exposure_limits(
            bets,
            max_per_match=150,
            max_per_day=250,
        )
        
        total = sum(b["stake"] for b in limited)
        assert total <= 250
