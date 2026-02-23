"""Tests for synthetic odds generation helpers."""

from app.scripts.generate_synthetic_odds import _apply_margin_and_noise


class TestApplyMarginAndNoise:
    """Unit tests for _apply_margin_and_noise()."""

    def test_margin_shortens_odds(self):
        """Positive overround should produce shorter odds than fair."""
        fair = 5.0
        result = _apply_margin_and_noise(fair, overround=0.10, noise=0.0)
        assert result < fair

    def test_positive_noise_shortens_further(self):
        """Positive noise on top of margin → even shorter odds."""
        fair = 5.0
        with_margin_only = _apply_margin_and_noise(fair, overround=0.10, noise=0.0)
        with_margin_and_noise = _apply_margin_and_noise(fair, overround=0.10, noise=0.05)
        assert with_margin_and_noise < with_margin_only

    def test_negative_noise_creates_edge(self):
        """Negative noise can push odds longer than fair (edge opportunity)."""
        fair = 3.0
        # Large negative noise with small margin → odds > fair
        result = _apply_margin_and_noise(fair, overround=0.02, noise=-0.15)
        assert result > fair

    def test_floor_at_1_10(self):
        """Odds should never go below 1.10."""
        result = _apply_margin_and_noise(1.05, overround=0.50, noise=0.50)
        assert result == 1.10

    def test_ceiling_at_51(self):
        """Odds should never exceed 51.0."""
        result = _apply_margin_and_noise(100.0, overround=-0.05, noise=-0.80)
        assert result == 51.0

    def test_zero_margin_and_noise(self):
        """With no margin or noise, output should equal fair odds (rounded)."""
        fair = 4.0
        result = _apply_margin_and_noise(fair, overround=0.0, noise=0.0)
        assert result == fair

    def test_typical_range(self):
        """Typical values should produce reasonable odds."""
        fair = 6.0
        result = _apply_margin_and_noise(fair, overround=0.08, noise=0.03)
        # Should be less than fair (margin + positive noise)
        assert 1.10 <= result < fair
        # Should be in a reasonable range
        assert result > 4.0  # Not too compressed
