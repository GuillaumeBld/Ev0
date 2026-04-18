"""Tests for top-down share calculation and new allocate_player."""
import math
import pytest
from app.pricing.team_xg import compute_player_shares, allocate_player, PlayerAllocation

SAMPLE_FW = {
    "player_id": 1, "player_name": "Striker A", "position": "FW",
    "matches_played": 15, "minutes_played": 1200,
    "npxg_per_90": 0.35, "xa_per_90": 0.08,
    "xg_per_90": 0.35, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9,
    "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15, "cross_accuracy": 0.30,
    "npxg_total": 4.0, "goals_total": 5, "xa_total": 1.0, "assists_total": 2,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}

SAMPLE_MF = {
    "player_id": 2, "player_name": "Mid B", "position": "MF",
    "matches_played": 15, "minutes_played": 1200,
    "npxg_per_90": 0.10, "xa_per_90": 0.12,
    "xg_per_90": 0.10, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.35, "xg_per_shot": 0.09, "avg_rating": 6.8,
    "key_pass_per_90": 0.55, "accurate_cross_per_90": 0.20, "cross_accuracy": 0.30,
    "npxg_total": 1.5, "goals_total": 1, "xa_total": 2.0, "assists_total": 3,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}


class TestComputePlayerSharesTopDown:
    def test_shares_sum_le_one(self):
        shares = compute_player_shares([SAMPLE_FW, SAMPLE_MF], "Team", lambda_team=1.5)
        assert sum(s.npxg_share for s in shares) <= 1.001

    def test_fw_has_higher_goal_share_than_mf(self):
        shares = compute_player_shares([SAMPLE_FW, SAMPLE_MF], "Team", lambda_team=1.5)
        fw = next(s for s in shares if s.position == "FW")
        mf = next(s for s in shares if s.position == "MF")
        assert fw.npxg_share > mf.npxg_share

    def test_form_blending_increases_share(self):
        fw_with_form = {**SAMPLE_FW, "form_xg_5": 3.0}  # hot form: 3 goals in 5 games
        shares_base = compute_player_shares([SAMPLE_FW, SAMPLE_MF], "Team", lambda_team=1.5)
        shares_form = compute_player_shares([fw_with_form, SAMPLE_MF], "Team", lambda_team=1.5)
        fw_base = next(s for s in shares_base if s.position == "FW")
        fw_form = next(s for s in shares_form if s.position == "FW")
        assert fw_form.npxg_share > fw_base.npxg_share

    def test_single_player_denom_equals_lambda_when_weight_less(self):
        small_fw = {**SAMPLE_FW, "npxg_per_90": 0.10, "xg_per_90": 0.10}
        shares = compute_player_shares([small_fw], "Team", lambda_team=2.0)
        # weight = 0.10 * (75/90) < 2.0 → denom = 2.0
        expected_weight = 0.10 * (75.0 / 90.0)
        assert abs(shares[0].npxg_share - expected_weight / 2.0) < 0.01


class TestAllocatePlayerTopDown:
    def _get_share(self, player=SAMPLE_FW):
        shares = compute_player_shares([player], "Team", lambda_team=1.5)
        return shares[0]

    def test_average_fw_fair_odds_goal_reasonable(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, team_match_xg=1.5, is_pen_taker=False, budget_assists=1.5 * 0.65)
        # Should be between 2 and 10 for an average FW with 75 min expected
        assert 2.0 <= alloc.fair_odds_goal <= 10.0

    def test_pen_taker_has_lower_fair_odds(self):
        share = self._get_share(SAMPLE_FW)
        alloc_no_pen = allocate_player(share, 1.5, False, 1.5 * 0.65)
        alloc_pen = allocate_player(share, 1.5, True, 1.5 * 0.65)
        assert alloc_pen.fair_odds_goal < alloc_no_pen.fair_odds_goal

    def test_has_form_flags(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, 1.5, False, 1.5 * 0.65)
        assert alloc.has_form_goal is False   # form_xg_5 was None
        assert alloc.has_form_assist is False  # form_assists_5 was None

    def test_form_data_sets_flag(self):
        fw_form = {**SAMPLE_FW, "form_xg_5": 2.0, "form_assists_5": 1.0}
        shares = compute_player_shares([fw_form], "Team", lambda_team=1.5)
        alloc = allocate_player(shares[0], 1.5, False, 1.5 * 0.65)
        assert alloc.has_form_goal is True
        assert alloc.has_form_assist is True

    def test_matches_played_propagated(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, 1.5, False, 1.5 * 0.65)
        assert alloc.matches_played == 15
