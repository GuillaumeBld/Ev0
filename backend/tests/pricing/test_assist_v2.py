"""Tests for top-down assist pricing functions (v2, Bzzoiro-based)."""

import pytest

from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    detect_creator_profile,
    calculate_creation_multiplier_v2,
    calculate_xa_conversion,
    calculate_assist_lambda,
)


class TestDetectCreatorProfile:
    def test_wide_profile_cross_dominant(self):
        stats = {"key_pass_per_90": 0.30, "accurate_cross_per_90": 1.20}
        assert detect_creator_profile(stats) == "wide"

    def test_central_profile_pass_dominant(self):
        stats = {"key_pass_per_90": 1.00, "accurate_cross_per_90": 0.10}
        assert detect_creator_profile(stats) == "central"

    def test_hybrid_profile(self):
        stats = {"key_pass_per_90": 0.60, "accurate_cross_per_90": 0.40}
        assert detect_creator_profile(stats) == "hybrid"

    def test_unknown_when_no_data(self):
        stats = {"key_pass_per_90": 0.0, "accurate_cross_per_90": 0.0}
        assert detect_creator_profile(stats) == "unknown"

    def test_unknown_when_none(self):
        stats = {"key_pass_per_90": None, "accurate_cross_per_90": None}
        assert detect_creator_profile(stats) == "unknown"

    def test_boundary_wide_threshold(self):
        # cross_dominance = 0.56 → wide
        stats = {"key_pass_per_90": 0.44, "accurate_cross_per_90": 0.56}
        assert detect_creator_profile(stats) == "wide"

    def test_boundary_central_threshold(self):
        # cross_dominance = 0.20 → central
        stats = {"key_pass_per_90": 0.80, "accurate_cross_per_90": 0.20}
        assert detect_creator_profile(stats) == "central"


class TestCreationMultiplierV2:
    def test_average_mf_returns_near_one(self):
        stats = {
            "xa_per_90": 0.06, "key_pass_per_90": 0.55,
            "accurate_cross_per_90": 0.20, "cross_accuracy": 0.35,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert 0.95 <= mult <= 1.05

    def test_elite_creator_clamped_to_max(self):
        stats = {
            "xa_per_90": 0.30, "key_pass_per_90": 3.0,
            "accurate_cross_per_90": 2.0, "cross_accuracy": 0.80,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert mult == 1.50

    def test_poor_creator_clamped_to_min(self):
        stats = {
            "xa_per_90": 0.0, "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0, "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert mult == 0.70

    def test_wide_profile_weights_crosses_more(self):
        # Two players same xa+kp at DF-average level, but one has high crosses.
        # low_cross → hybrid profile; high_cross → wide profile (xc-dominant).
        # DF position avgs: xa=0.03, kp=0.20, xc=0.40.
        # With kp at DF average, low_cross stays well below clamp ceiling.
        base = {"xa_per_90": 0.03, "key_pass_per_90": 0.20, "cross_accuracy": 0.35}
        low_cross = {**base, "accurate_cross_per_90": 0.10}
        high_cross = {**base, "accurate_cross_per_90": 1.50}
        # high_cross → wide profile → xc weight=0.40 → higher multiplier
        mult_low  = calculate_creation_multiplier_v2(low_cross, "DF")
        mult_high = calculate_creation_multiplier_v2(high_cross, "DF")
        assert mult_high > mult_low

    def test_none_position_uses_unknown_weights(self):
        stats = {
            "xa_per_90": 0.06, "key_pass_per_90": 0.45,
            "accurate_cross_per_90": 0.22, "cross_accuracy": 0.35,
        }
        mult = calculate_creation_multiplier_v2(stats, None)
        assert 0.70 <= mult <= 1.50


class TestXaConversion:
    def test_below_min_matches_returns_one(self):
        stats = {"matches_played": 3, "assists": 5, "xa_total": 2.0}
        assert calculate_xa_conversion(stats) == 1.0

    def test_overperformer_clamped(self):
        stats = {"matches_played": 10, "assists": 12, "xa_total": 4.0}
        assert calculate_xa_conversion(stats) == 1.40

    def test_underperformer_clamped(self):
        stats = {"matches_played": 10, "assists": 1, "xa_total": 8.0}
        assert calculate_xa_conversion(stats) == 0.75

    def test_zero_xa_returns_one(self):
        stats = {"matches_played": 10, "assists": 0, "xa_total": 0.0}
        assert calculate_xa_conversion(stats) == 1.0


class TestAssistLambda:
    def test_basic_lambda(self):
        budget = 1.5 * ASSIST_GOAL_RATE
        lam = calculate_assist_lambda(
            share_xa=0.25, budget_assists=budget,
            creation_mult=1.0, xa_conversion=1.0,
        )
        assert lam == pytest.approx(0.25 * budget, abs=0.001)

    def test_lambda_clamped_to_max(self):
        lam = calculate_assist_lambda(1.0, 10.0, 1.5, 1.4)
        assert lam == 2.0  # CLAMP_LAMBDA_MAX pour assist

    def test_lambda_clamped_to_min(self):
        lam = calculate_assist_lambda(0.0, 0.0, 0.70, 0.75)
        assert lam == 0.01  # CLAMP_LAMBDA_MIN

    def test_assist_goal_rate_constant(self):
        assert ASSIST_GOAL_RATE == pytest.approx(0.65, abs=0.001)
