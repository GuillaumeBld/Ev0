import math

import pytest

from app.services.market_xg import (
    MarketXgResult,
    cross_validate,
    multiplicative_devig,
    select_lambda_home,
    solve_lambda_home,
    solve_lambda_t,
)

# ---------------------------------------------------------------------------
# multiplicative_devig
# ---------------------------------------------------------------------------

class TestMultiplicativeDevig:
    def test_balanced_market(self):
        result = multiplicative_devig([1.9, 1.9])
        assert len(result) == 2
        assert abs(result[0] - 0.5) < 1e-9
        assert abs(result[1] - 0.5) < 1e-9

    def test_three_way_sums_to_one(self):
        result = multiplicative_devig([2.0, 3.5, 4.0])
        assert abs(sum(result) - 1.0) < 1e-9

    def test_three_way_order_preserved(self):
        # Lower odds -> higher probability
        result = multiplicative_devig([2.0, 3.5, 4.0])
        assert result[0] > result[1] > result[2]

    def test_even_odds_exact(self):
        result = multiplicative_devig([2.0, 2.0])
        assert abs(result[0] - 0.5) < 1e-9
        assert abs(result[1] - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# solve_lambda_t
# ---------------------------------------------------------------------------

class TestSolveLambdaT:
    def test_round_trip_at_2_5(self):
        # P(Over 2.5) at lambda=2.5 ~ 0.456187
        p_over = 1 - math.exp(-2.5) * (1 + 2.5 + 2.5**2 / 2)
        lam = solve_lambda_t(p_over)
        assert abs(lam - 2.5) < 1e-4

    def test_known_value(self):
        # Direct known-value test as specified
        lam = solve_lambda_t(0.4562)
        assert abs(lam - 2.5) < 1e-4

    def test_high_probability_large_lambda(self):
        # p=0.99 should give a large lambda
        lam = solve_lambda_t(0.99)
        assert lam > 5.0

    def test_low_probability_small_lambda(self):
        # p=0.01 should give a small lambda
        lam = solve_lambda_t(0.01)
        assert lam < 1.0

    def test_result_satisfies_equation(self):
        p_target = 0.55
        lam = solve_lambda_t(p_target)
        pred = 1 - math.exp(-lam) * (1 + lam + lam**2 / 2)
        assert abs(pred - p_target) < 1e-8


# ---------------------------------------------------------------------------
# solve_lambda_home
# ---------------------------------------------------------------------------

class TestSolveLambdaHome:
    # For lambda_t=2.5, max achievable BTTS ~ 0.509 (at lh=1.25).
    # Use p_btts=0.45 which is well below the max.
    LAMBDA_T = 2.5
    P_BTTS = 0.45

    def test_two_solutions_sum_to_lambda_t(self):
        lh1, lh2 = solve_lambda_home(self.LAMBDA_T, self.P_BTTS)
        assert abs(lh1 + lh2 - self.LAMBDA_T) < 1e-8

    def test_both_solutions_satisfy_btts_equation(self):
        lh1, lh2 = solve_lambda_home(self.LAMBDA_T, self.P_BTTS)
        for lh in (lh1, lh2):
            la = self.LAMBDA_T - lh
            pred = (1 - math.exp(-lh)) * (1 - math.exp(-la))
            assert abs(pred - self.P_BTTS) < 1e-6

    def test_solutions_are_symmetric(self):
        lh1, lh2 = solve_lambda_home(self.LAMBDA_T, self.P_BTTS)
        assert abs(lh1 - (self.LAMBDA_T - lh2)) < 1e-8

    def test_lh1_leq_half_lambda_t(self):
        # lh1 is found on [lo, midpoint]
        lambda_t = 3.0
        p_btts = 0.55  # below max of ~0.604
        lh1, lh2 = solve_lambda_home(lambda_t, p_btts)
        assert lh1 <= lambda_t / 2
        assert lh2 >= lambda_t / 2

    def test_degenerate_lambda_t_too_small(self):
        # lambda_t=0.08 -> lo=0.05 >= mid=0.04 -> ValueError
        with pytest.raises(ValueError, match="too small"):
            solve_lambda_home(0.08, 0.5)

    def test_degenerate_no_root(self):
        # p_btts above max achievable for given lambda_t -> no root in bracket
        with pytest.raises(ValueError):
            solve_lambda_home(1.0, 0.99)

    def test_with_explicit_known_pair(self):
        # lh=1.0, la=1.5 -> p_btts ~ 0.491075
        lambda_t = 2.5
        p_btts = (1 - math.exp(-1.0)) * (1 - math.exp(-1.5))
        lh1, lh2 = solve_lambda_home(lambda_t, p_btts)
        # One of the solutions should be close to 1.0
        assert abs(lh1 - 1.0) < 1e-6 or abs(lh2 - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# select_lambda_home
# ---------------------------------------------------------------------------

class TestSelectLambdaHome:
    def test_home_stronger_returns_max(self):
        lh = select_lambda_home(1.0, 1.5, p_home_win=0.5, p_away_win=0.3)
        assert lh == 1.5

    def test_away_stronger_returns_min(self):
        lh = select_lambda_home(1.0, 1.5, p_home_win=0.3, p_away_win=0.5)
        assert lh == 1.0

    def test_equal_strength_returns_max(self):
        # p_home_win >= p_away_win -> max
        lh = select_lambda_home(1.2, 1.8, p_home_win=0.4, p_away_win=0.4)
        assert lh == 1.8


# ---------------------------------------------------------------------------
# cross_validate
# ---------------------------------------------------------------------------

class TestCrossValidate:
    def _expected_probs(self, lh: float, la: float):
        lt = lh + la
        over = 1 - math.exp(-lt) * (1 + lt + lt**2 / 2)
        btts = (1 - math.exp(-lh)) * (1 - math.exp(-la))
        return over, btts

    def test_valid_case_no_flag(self):
        lh, la = 1.3, 1.1
        p_over, p_btts = self._expected_probs(lh, la)
        ok, reason = cross_validate(lh, la, p_over, p_btts)
        assert ok is True
        assert reason is None

    def test_over_error_above_threshold_flags(self):
        lh, la = 1.3, 1.1
        p_over, p_btts = self._expected_probs(lh, la)
        # Perturb Over 2.5 by more than 8%
        ok, reason = cross_validate(lh, la, p_over + 0.10, p_btts)
        assert ok is False
        assert reason is not None
        assert "Over 2.5" in reason

    def test_btts_error_above_threshold_flags(self):
        lh, la = 1.3, 1.1
        p_over, p_btts = self._expected_probs(lh, la)
        # Perturb BTTS by more than 8%
        ok, reason = cross_validate(lh, la, p_over, p_btts + 0.10)
        assert ok is False
        assert reason is not None
        assert "BTTS" in reason

    def test_error_exactly_at_threshold_does_not_flag(self):
        lh, la = 1.3, 1.1
        p_over, p_btts = self._expected_probs(lh, la)
        # 8% exactly -> not flagged (strictly greater than)
        ok, reason = cross_validate(lh, la, p_over + 0.08, p_btts)
        assert ok is True
        assert reason is None

    def test_over_error_checked_before_btts(self):
        lh, la = 1.3, 1.1
        p_over, p_btts = self._expected_probs(lh, la)
        ok, reason = cross_validate(lh, la, p_over + 0.10, p_btts + 0.10)
        assert ok is False
        assert "Over 2.5" in reason


# ---------------------------------------------------------------------------
# MarketXgResult dataclass
# ---------------------------------------------------------------------------

class TestMarketXgResult:
    def test_default_flagged_reason_none(self):
        r = MarketXgResult(xg_home=1.2, xg_away=0.9, xg_source="market_implied")
        assert r.flagged_reason is None

    def test_flagged_variant(self):
        r = MarketXgResult(
            xg_home=1.2,
            xg_away=0.9,
            xg_source="market_implied_flagged",
            flagged_reason="some reason",
        )
        assert r.xg_source == "market_implied_flagged"
        assert r.flagged_reason == "some reason"

