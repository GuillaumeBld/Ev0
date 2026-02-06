"""Tests for assist pricing module."""

import math
import pytest

from app.pricing.assist import (
    calculate_assist_price,
    calculate_creation_score,
    CREATION_WEIGHTS,
)


class TestAssistPricing:
    """Tests for assist pricing calculations."""
    
    def test_basic_calculation(self):
        """Test basic pricing with default factors."""
        result = calculate_assist_price(xa_per_90=0.2)
        
        # Lambda should be 0.2 * 1 * 1 * 1 * 1 = 0.2
        assert result["lambda_intensity"] == 0.2
        
        # P(assist >= 1) = 1 - e^(-0.2) ≈ 0.1813
        expected_prob = 1 - math.exp(-0.2)
        assert abs(result["probability"] - expected_prob) < 0.001
    
    def test_minutes_adjustment(self):
        """Test expected minutes adjustment."""
        result = calculate_assist_price(xa_per_90=0.3, expected_minutes=60)
        
        # Lambda = 0.3 * (60/90) = 0.2
        assert result["lambda_intensity"] == 0.2
    
    def test_creation_score(self):
        """Test creation score multiplier."""
        result = calculate_assist_price(xa_per_90=0.2, creation_score=1.5)
        
        # Lambda = 0.2 * 1.5 = 0.3
        assert result["lambda_intensity"] == 0.3
    
    def test_teammate_factor(self):
        """Test teammate finishing adjustment."""
        # Team with elite finishers (1.2 goals/xG)
        result = calculate_assist_price(xa_per_90=0.2, teammate_finishing_factor=1.2)
        
        # Lambda = 0.2 * 1.2 = 0.24
        assert result["lambda_intensity"] == 0.24
    
    def test_lambda_clamping(self):
        """Test lambda is clamped."""
        result = calculate_assist_price(xa_per_90=5.0)
        assert result["lambda_intensity"] <= 2.0
    
    def test_explanation_included(self):
        """Test explanation payload."""
        result = calculate_assist_price(xa_per_90=0.2)
        
        assert "explanation" in result
        assert "inputs" in result["explanation"]


class TestCreationScore:
    """Tests for composite creation score calculation."""
    
    def test_weights_sum_to_one(self):
        """Verify weights sum to 1."""
        total = sum(CREATION_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001
    
    def test_average_player_score_one(self):
        """Player at league average should have score ~1."""
        score, _ = calculate_creation_score(
            xa_per_90=0.15,
            key_passes_per_90=1.5,
            sca_per_90=2.5,
            crosses_per_90=2.0,
            passes_into_box_per_90=1.0,
            progressive_passes_per_90=4.0,
        )
        
        assert abs(score - 1.0) < 0.001
    
    def test_elite_creator_high_score(self):
        """Elite creator should have score > 1.5."""
        score, _ = calculate_creation_score(
            xa_per_90=0.35,  # More than double average
            key_passes_per_90=3.0,
            sca_per_90=5.0,
            crosses_per_90=4.0,
            passes_into_box_per_90=2.5,
            progressive_passes_per_90=8.0,
        )
        
        assert score > 1.5
    
    def test_breakdown_returned(self):
        """Test component breakdown is returned."""
        _, breakdown = calculate_creation_score(xa_per_90=0.2)
        
        assert "xa" in breakdown
        assert "normalized" in breakdown["xa"]
        assert "weight" in breakdown["xa"]
