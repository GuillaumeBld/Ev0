import pytest
from app.pricing.goalscorer import calculate_finishing_multiplier


class TestFinishingMultiplierDefender:
    def test_df_no_shots_clamped_low(self):
        """DF avec stats nulles : multiplier = plancher DF = 0.30."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 0.0,
        }
        mult = calculate_finishing_multiplier(stats, "DF")
        assert mult == pytest.approx(0.30, abs=0.01)

    def test_fw_floor_unchanged(self):
        """FW avec stats normales : plancher toujours 0.70."""
        stats = {
            "shot_accuracy": 0.50,
            "xg_per_shot": 0.17,
            "avg_rating": 7.0,
        }
        mult = calculate_finishing_multiplier(stats, "FW")
        assert 0.70 <= mult <= 1.50

    def test_fw_floor_at_zero_stats(self):
        """FW avec stats nulles : plancher clampé à exactement 0.70."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 0.0,
        }
        mult = calculate_finishing_multiplier(stats, "FW")
        assert mult == pytest.approx(0.70, abs=0.01)

    def test_unknown_position_fallback(self):
        """Position inconnue → fallback clamp (0.55, 1.50), plancher = 0.55."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 0.0,
        }
        mult = calculate_finishing_multiplier(stats, None)
        assert mult == pytest.approx(0.55, abs=0.01)
