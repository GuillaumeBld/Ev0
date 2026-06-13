import pytest
from app.pricing.goalscorer import calculate_finishing_multiplier
from app.pricing.assist import calculate_creation_multiplier_v2, calculate_xa_conversion
from app.pricing.team_xg import compute_player_shares, DF_SETPIECE_DISCOUNT


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

    def test_creation_mult_fw_clamped_low(self):
        """FW avec stats nulles : plancher = 0.70."""
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


class TestSetpieceDiscount:
    def _make_player(self, name: str, position: str, npxg_per_90: float) -> dict:
        return {
            "player_id": abs(hash(name)) % (10**9),
            "player_name": name,
            "position": position,
            "matches_played": 20,
            "minutes_played": 1800,
            "avg_minutes_per_match": 90.0,
            "npxg_per_90": npxg_per_90,
            "xg_per_90": npxg_per_90,
            "xa_per_90": 0.05,
            "form_xg_5": None,
            "form_assists_5": None,
            "shot_accuracy": 0.4,
            "xg_per_shot": 0.12,
            "avg_rating": 6.8,
            "cross_accuracy": 0.3,
            "xa_total": 1.0,
            "assists_total": 1,
            "npxg_total": 2.0,
            "goals_total": 2,
            "key_pass_per_90": 0.8,
            "accurate_cross_per_90": 0.3,
            "has_bzz_stats": True,
            "xgchain_per_90": 0.0,
            "shots_on_target_per_90": 0.5,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": 0.3,
            "through_balls_per_90": 0.0,
            "finishing_delta": 0.0,
        }

    def test_df_share_discounted(self):
        """DF avec mêmes stats qu'un FW : share DF = share FW × DF_SETPIECE_DISCOUNT."""
        fw = self._make_player("FW1", "FW", 0.30)
        df = self._make_player("DF1", "DF", 0.30)

        shares = compute_player_shares([fw, df], "TeamA", lambda_team=1.5)
        share_fw = next(s for s in shares if s.player_name == "FW1").npxg_share
        share_df = next(s for s in shares if s.player_name == "DF1").npxg_share

        ratio = share_df / share_fw
        assert ratio == pytest.approx(DF_SETPIECE_DISCOUNT, abs=0.01)

    def test_fw_share_unaffected(self):
        """npxg_share d'un FW est identique avec ou sans DF dans l'équipe."""
        fw = self._make_player("FW1", "FW", 0.30)
        shares_solo = compute_player_shares([fw], "TeamA", lambda_team=1.5)
        share_solo = shares_solo[0].npxg_share

        df = self._make_player("DF1", "DF", 0.10)
        shares_with_df = compute_player_shares([fw, df], "TeamA", lambda_team=1.5)
        share_with_df = next(s for s in shares_with_df if s.player_name == "FW1").npxg_share

        assert share_solo == pytest.approx(share_with_df, abs=0.0001)

    def test_multiple_dfs_all_discounted(self):
        """Plusieurs DF avec les mêmes stats que FW : tous discounted de 0.55."""
        fw = self._make_player("FW1", "FW", 0.20)
        df1 = self._make_player("DF1", "DF", 0.20)
        df2 = self._make_player("DF2", "DF", 0.20)

        shares = compute_player_shares([fw, df1, df2], "TeamA", lambda_team=1.5)
        share_fw = next(s for s in shares if s.player_name == "FW1").npxg_share
        share_df1 = next(s for s in shares if s.player_name == "DF1").npxg_share
        share_df2 = next(s for s in shares if s.player_name == "DF2").npxg_share

        ratio1 = share_df1 / share_fw
        ratio2 = share_df2 / share_fw

        assert ratio1 == pytest.approx(DF_SETPIECE_DISCOUNT, abs=0.01)
        assert ratio2 == pytest.approx(DF_SETPIECE_DISCOUNT, abs=0.01)

    def test_assist_share_unaffected_by_discount(self):
        """Le discount 0.55 ne s'applique que sur npxg_share, pas xa_share."""
        fw = self._make_player("FW1", "FW", 0.30)
        df = self._make_player("DF1", "DF", 0.30)

        shares = compute_player_shares([fw, df], "TeamA", lambda_team=1.5)
        # Les xa_share doivent être basés sur les poids blendés (sans discount)
        # Donc si FW et DF ont même xa_per_90, les xa_share doivent être similaires (ratio ~1.0)
        # même si les npxg_share ont un ratio de 0.55.

        xa_fw = next(s for s in shares if s.player_name == "FW1").xa_share
        xa_df = next(s for s in shares if s.player_name == "DF1").xa_share

        # Les assiste : DF a 0.05 xa_per_90 (même que FW) donc ratio doit être ~1.0, pas 0.55
        xa_ratio = xa_df / xa_fw if xa_fw > 0 else 0
        # Ratio proche de 1.0, pas 0.55
        assert xa_ratio == pytest.approx(1.0, abs=0.1)
