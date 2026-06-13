import pytest
from app.pricing.goalscorer import calculate_finishing_multiplier
from app.pricing.assist import calculate_creation_multiplier_v2, calculate_xa_conversion


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


class TestAssistMultiplierDefender:
    def test_creation_mult_df_clamped_low(self):
        """DF avec 0 stats création : creation_mult = 0.40 (plancher DF)."""
        stats = {
            "xa_per_90": 0.0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "DF")
        assert mult == pytest.approx(0.40, abs=0.01)

    def test_creation_mult_fw_unchanged(self):
        """FW avec 0 stats : plancher toujours 0.70."""
        stats = {
            "xa_per_90": 0.0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "FW")
        assert mult == pytest.approx(0.70, abs=0.01)

    def test_xa_conversion_df_floor(self):
        """DF avec 0 assists sur 10 matchs : conversion = 0.50 (plancher DF, pas 0.75)."""
        stats = {"matches_played": 10, "xa_total": 2.0, "assists": 0}
        conv = calculate_xa_conversion(stats, "DF")
        assert conv == pytest.approx(0.50, abs=0.01)

    def test_xa_conversion_no_position_backward_compat(self):
        """Appel sans position → plancher par défaut 0.75."""
        stats = {"matches_played": 10, "xa_total": 2.0, "assists": 0}
        conv = calculate_xa_conversion(stats)
        assert conv == pytest.approx(0.75, abs=0.01)
