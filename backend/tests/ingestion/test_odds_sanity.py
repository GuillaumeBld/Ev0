"""Tests for odds sanity checks and clean prob computation."""

import math

from app.ingestion.odds_sanity import compute_clean_probs, validate_market


class TestValidateMarket:
    def test_valid_h2h(self):
        assert validate_market("h2h", {"home": 2.05, "draw": 3.30, "away": 3.80}) is True

    def test_valid_totals(self):
        assert validate_market("totals", {"over_2.5": 1.95, "under_2.5": 1.95}) is True

    def test_valid_btts(self):
        assert validate_market("btts", {"yes": 1.80, "no": 1.95}) is True

    def test_rejects_odds_below_1_01(self):
        assert validate_market("h2h", {"home": 1.00, "draw": 3.30, "away": 3.80}) is False

    def test_rejects_missing_selection_h2h(self):
        assert validate_market("h2h", {"home": 2.05, "away": 3.80}) is False

    def test_rejects_missing_selection_totals(self):
        assert validate_market("totals", {"over_2.5": 1.95}) is False

    def test_rejects_none_value(self):
        assert validate_market("btts", {"yes": None, "no": 1.95}) is False

    def test_rejects_nan_value(self):
        assert validate_market("btts", {"yes": math.nan, "no": 1.95}) is False

    def test_rejects_absurd_margin(self):
        # Sum of implied probs = 2.5 (150% margin — absurd)
        assert validate_market("h2h", {"home": 1.20, "draw": 1.20, "away": 1.20}) is False

    def test_rejects_unknown_market_type(self):
        assert validate_market("unknown", {"foo": 2.0}) is False


class TestComputeCleanProbs:
    def test_h2h_sums_to_one(self):
        result = compute_clean_probs({"home": 2.05, "draw": 3.30, "away": 3.80})
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_preserves_keys(self):
        odds = {"home": 2.05, "draw": 3.30, "away": 3.80}
        result = compute_clean_probs(odds)
        assert set(result.keys()) == {"home", "draw", "away"}

    def test_higher_odds_lower_prob(self):
        result = compute_clean_probs({"home": 1.50, "away": 4.00})
        assert result["home"] > result["away"]

    def test_even_odds_equal_probs(self):
        result = compute_clean_probs({"yes": 2.00, "no": 2.00})
        assert abs(result["yes"] - 0.5) < 1e-9
        assert abs(result["no"] - 0.5) < 1e-9
