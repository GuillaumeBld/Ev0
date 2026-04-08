"""Tests for enhanced MarketXgService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.market_xg import MAX_SNAPSHOT_AGE, MarketXgResult, _preferred_bookmaker


class TestPreferredBookmaker:
    def test_prefers_oddsportal(self):
        assert _preferred_bookmaker({"oddsportal", "betfair", "pinnacle"}) == "oddsportal"

    def test_prefers_betfair_over_pinnacle(self):
        assert _preferred_bookmaker({"betfair", "pinnacle"}) == "betfair"

    def test_prefers_pinnacle_over_betclic(self):
        assert _preferred_bookmaker({"pinnacle", "betclic"}) == "pinnacle"

    def test_returns_none_for_empty(self):
        assert _preferred_bookmaker(set()) is None

    def test_returns_any_for_unknown(self):
        result = _preferred_bookmaker({"unknown_book"})
        assert result == "unknown_book"


class TestMaxSnapshotAge:
    def test_max_snapshot_age_is_3_hours(self):
        assert MAX_SNAPSHOT_AGE == timedelta(hours=3)


class TestMarketXgResultFields:
    def test_has_data_source_field(self):
        result = MarketXgResult(
            xg_home=1.5,
            xg_away=1.0,
            xg_source="market_implied",
            data_source="oddsportal",
            fallback_used=False,
            fit_residual=0.01,
            flagged=False,
            as_of_utc=datetime.now(timezone.utc),
            input_snapshot_ids=[1, 2, 3],
        )
        assert result.data_source == "oddsportal"
        assert result.fallback_used is False
        assert result.flagged is False


from app.services.market_xg import _fit_lambdas, _p_poisson_btts, _p_poisson_over_2_5


class TestPoissonHelpers:
    def test_btts_zero_when_one_team_cannot_score(self):
        # lambda_home = 0 → P(home scores) ≈ 0 → BTTS ≈ 0
        assert _p_poisson_btts(0.0001, 1.5) < 0.01

    def test_over_2_5_increases_with_lambda(self):
        assert _p_poisson_over_2_5(4.0) > _p_poisson_over_2_5(2.0)

    def test_over_2_5_known_value(self):
        # lambda_t = 2.5: P(over 2.5) ≈ 0.456
        assert abs(_p_poisson_over_2_5(2.5) - 0.456) < 0.01


class TestFitLambdas:
    def test_recovers_known_lambdas(self):
        """Given probs derived from (1.5, 1.0), solver should recover close values."""
        import math
        from app.services.market_xg import _p_poisson_home_win, _p_poisson_draw

        lh_true, la_true = 1.5, 1.0
        lt = lh_true + la_true

        p_over = _p_poisson_over_2_5(lt)
        p_btts = _p_poisson_btts(lh_true, la_true)
        p_home = _p_poisson_home_win(lh_true, la_true)
        p_draw = _p_poisson_draw(lh_true, la_true)

        lh_hat, la_hat, residual = _fit_lambdas(p_home, p_draw, p_over, p_btts)

        assert abs(lh_hat - lh_true) < 0.05
        assert abs(la_hat - la_true) < 0.05
        assert residual < 1e-6

    def test_clamps_to_bounds(self):
        # Edge case: very high probabilities → solver stays within [0.05, 4.5]
        lh, la, _ = _fit_lambdas(0.99, 0.005, 0.99, 0.98)
        assert 0.05 <= lh <= 4.5
        assert 0.05 <= la <= 4.5

    def test_flags_high_residual(self):
        # Contradictory market probs → residual > threshold
        _, _, residual = _fit_lambdas(
            p_home_win=0.90,   # home very dominant
            p_draw=0.01,
            p_over_2_5=0.10,   # but very low total goals (contradictory)
            p_btts_yes=0.80,   # and high BTTS (contradictory)
        )
        assert residual > 0.01
