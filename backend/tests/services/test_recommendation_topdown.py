"""Tests for top-down share computation helpers in recommendation_service."""

import pytest

from app.services.recommendation_service import _blend_rate, _compute_team_denominators


class TestBlendRate:
    def test_no_form_returns_season_rate(self):
        result = _blend_rate(season_rate=0.30, form_value=None, avg_mins=75.0)
        assert result == pytest.approx(0.30, abs=0.001)

    def test_form_blended_60_40(self):
        # form_value=1.0 xG over 5 matches at 75 min avg
        # form_rate = 1.0 / (5 × 75/90) = 1.0 / 4.167 = 0.24
        # blended = 0.60×0.30 + 0.40×0.24 = 0.18 + 0.096 = 0.276
        result = _blend_rate(season_rate=0.30, form_value=1.0, avg_mins=75.0)
        assert result == pytest.approx(0.276, abs=0.001)

    def test_zero_avg_mins_returns_season_rate(self):
        result = _blend_rate(season_rate=0.20, form_value=2.0, avg_mins=0.0)
        assert result == pytest.approx(0.20, abs=0.001)

    def test_form_zero_blended(self):
        # form_rate = 0.0 → blended = 0.60×0.30 = 0.18
        result = _blend_rate(season_rate=0.30, form_value=0.0, avg_mins=75.0)
        assert result == pytest.approx(0.18, abs=0.001)


class TestComputeTeamDenominators:
    def _make_player(self, team, xg, xa, mins=75.0, form_xg=None, form_xa=None):
        return {
            "team": team, "xg_per_90": xg, "xa_per_90": xa,
            "expected_minutes": mins, "avg_minutes_per_match": mins,
            "form_xg_5": form_xg, "form_assists_5": form_xa,
            "position": "MF",
        }

    def test_single_player_denominator_equals_lambda_when_weight_less(self):
        player_stats = {
            "PlayerA": self._make_player("Home FC", xg=0.20, xa=0.10),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # weight = 0.20 × (75/90) = 0.167 < lambda_home=1.5 → denom = 1.5
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(1.5, abs=0.001)

    def test_denominator_uses_player_sum_when_larger_than_lambda(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=1.0, xa=0.5, mins=90.0),
            "P2": self._make_player("Home FC", xg=1.0, xa=0.5, mins=90.0),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # sum weights = 1.0×1.0 + 1.0×1.0 = 2.0 > lambda_home=1.5 → denom = 2.0
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(2.0, abs=0.001)

    def test_away_team_uses_lambda_away(self):
        player_stats = {
            "P1": self._make_player("Away FC", xg=0.10, xa=0.05),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=0.8,
        )
        assert denoms["Away FC"]["goal_denom"] == pytest.approx(0.8, abs=0.001)

    def test_players_from_other_teams_ignored(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=0.20, xa=0.10),
            "P2": self._make_player("Third FC", xg=2.0, xa=1.0),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(1.5, abs=0.001)

    def test_assist_denom_uses_budget_assists(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=0.20, xa=0.05),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # budget_assists = 1.5 × 0.65 = 0.975; player xa weight = 0.05 × (75/90) = 0.042 < 0.975
        assert denoms["Home FC"]["assist_denom"] == pytest.approx(0.975, abs=0.001)
