import pytest
from app.pricing.goalscorer import calculate_finishing_multiplier


class TestFinishingMultiplierDefender:
    def test_df_no_shots_clamped_low(self):
        """DF avec 0 tirs : multiplier plafonné à 0.30 (pas 0.70)."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 6.5,
            "matches_played": 20,
            "npxg_total": 0.5,
            "goals": 0,
        }
        mult = calculate_finishing_multiplier(stats, "DF")
        assert mult <= 0.32

    def test_fw_floor_unchanged(self):
        """FW avec stats normales : plancher toujours 0.70."""
        stats = {
            "shot_accuracy": 0.50,
            "xg_per_shot": 0.17,
            "avg_rating": 7.0,
            "matches_played": 20,
            "npxg_total": 5.0,
            "goals": 5,
        }
        mult = calculate_finishing_multiplier(stats, "FW")
        assert 0.70 <= mult <= 1.50

    def test_unknown_position_fallback(self):
        """Position inconnue → fallback MF (plancher 0.55)."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 6.5,
            "matches_played": 5,
            "npxg_total": 0.0,
            "goals": 0,
        }
        mult = calculate_finishing_multiplier(stats, None)
        assert mult >= 0.55
