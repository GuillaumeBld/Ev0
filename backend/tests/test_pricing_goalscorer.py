"""Tests for goalscorer pricing module."""

import math
import pytest

from app.pricing.goalscorer import (
    calculate_goalscorer_price,
    calculate_edge,
    remove_margin,
)


class TestGoalscorerPricing:
    """Tests for goalscorer pricing calculations."""
    
    def test_basic_calculation(self):
        """Test basic pricing with default factors."""
        result = calculate_goalscorer_price(xg_per_90=0.5)
        
        # Lambda should be 0.5 (xg_per_90 * 90/90 * 1 * 1 * 1)
        assert result["lambda_intensity"] == 0.5
        
        # P(score >= 1) = 1 - e^(-0.5) ≈ 0.3935
        expected_prob = 1 - math.exp(-0.5)
        assert abs(result["probability"] - expected_prob) < 0.001
        
        # Fair odds = 1 / 0.3935 ≈ 2.54
        assert 2.5 <= result["fair_odds"] <= 2.6
    
    def test_minutes_adjustment(self):
        """Test that expected minutes properly adjusts lambda."""
        # Player expected to play 60 minutes
        result = calculate_goalscorer_price(xg_per_90=0.6, expected_minutes=60)
        
        # Lambda = 0.6 * (60/90) = 0.4
        assert result["lambda_intensity"] == 0.4
    
    def test_conversion_rate(self):
        """Test conversion rate adjustment."""
        # Elite finisher with 1.2 conversion rate
        result = calculate_goalscorer_price(xg_per_90=0.5, conversion_rate=1.2)
        
        # Lambda = 0.5 * 1.2 = 0.6
        assert result["lambda_intensity"] == 0.6
    
    def test_opponent_factor(self):
        """Test opponent defensive factor."""
        # Weak defense (1.3x league avg xGA)
        result = calculate_goalscorer_price(xg_per_90=0.5, opponent_xga_factor=1.3)
        
        # Lambda = 0.5 * 1.3 = 0.65
        assert result["lambda_intensity"] == 0.65
    
    def test_combined_factors(self):
        """Test all factors combined."""
        result = calculate_goalscorer_price(
            xg_per_90=0.6,
            expected_minutes=75,
            conversion_rate=1.1,
            opponent_xga_factor=1.2,
            form_factor=0.9,
        )
        
        # Lambda = 0.6 * (75/90) * 1.1 * 1.2 * 0.9
        expected_lambda = 0.6 * (75/90) * 1.1 * 1.2 * 0.9
        assert abs(result["lambda_intensity"] - expected_lambda) < 0.001
    
    def test_lambda_clamping(self):
        """Test that lambda is clamped to reasonable range."""
        # Very high xG should be clamped
        result = calculate_goalscorer_price(xg_per_90=5.0)
        assert result["lambda_intensity"] <= 3.0
        
        # Very low should be floored
        result = calculate_goalscorer_price(xg_per_90=0.0001)
        assert result["lambda_intensity"] >= 0.001
    
    def test_explanation_present(self):
        """Test that explanation payload is included."""
        result = calculate_goalscorer_price(xg_per_90=0.5)
        
        assert "explanation" in result
        assert "inputs" in result["explanation"]
        assert "calculation" in result["explanation"]
        assert "interpretation" in result["explanation"]


class TestEdgeCalculation:
    """Tests for edge calculation."""
    
    def test_positive_edge(self):
        """Test positive edge when market odds > fair odds."""
        # Fair odds 2.5, market odds 3.0 = 20% edge
        edge = calculate_edge(fair_odds=2.5, market_odds=3.0)
        assert abs(edge - 0.2) < 0.001
    
    def test_negative_edge(self):
        """Test negative edge when market odds < fair odds."""
        # Fair odds 3.0, market odds 2.5 = -16.7% edge
        edge = calculate_edge(fair_odds=3.0, market_odds=2.5)
        assert edge < 0
    
    def test_no_edge(self):
        """Test zero edge when odds match."""
        edge = calculate_edge(fair_odds=2.5, market_odds=2.5)
        assert abs(edge) < 0.001


class TestMarginRemoval:
    """Tests for margin removal."""
    
    def test_proportional_removal(self):
        """Test proportional margin removal."""
        # 5% overround market
        odds = [2.0, 2.0]  # Implied prob = 50% + 50% = 100% + overround
        
        # Actually need odds that create overround
        odds = [1.9, 1.9]  # Implied = 52.6% + 52.6% = 105.2%
        
        fair = remove_margin(odds)
        
        # After removal, implied probabilities should sum to 100%
        total_prob = sum(1/o for o in fair)
        assert abs(total_prob - 1.0) < 0.01
    
    def test_three_way_market(self):
        """Test margin removal on 3-way market."""
        # Home/Draw/Away
        odds = [2.5, 3.2, 3.0]
        
        fair = remove_margin(odds)
        
        total_prob = sum(1/o for o in fair)
        assert abs(total_prob - 1.0) < 0.01
