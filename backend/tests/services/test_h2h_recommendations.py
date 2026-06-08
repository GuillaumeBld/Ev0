"""Tests for h2h Poisson probability helpers and _generate_h2h_recs."""

import pytest

from app.services.market_xg import p_poisson_away_win, p_poisson_draw, p_poisson_home_win


class TestPoissonH2hHelpers:
    def test_home_win_reasonable_range(self):
        # For equal lambdas, home win < 50% (no-draw symmetry makes it ~40%)
        result = p_poisson_home_win(1.5, 1.5)
        assert 0.35 < result < 0.45

    def test_home_win_dominant_home(self):
        # Strong home side should win majority of the time
        result = p_poisson_home_win(2.5, 0.5)
        assert result > 0.65

    def test_draw_reasonable_range(self):
        result = p_poisson_draw(1.5, 1.5)
        assert 0.23 < result < 0.35

    def test_away_win_is_complement(self):
        lh, la = 1.5, 1.0
        hw = p_poisson_home_win(lh, la)
        d = p_poisson_draw(lh, la)
        aw = p_poisson_away_win(lh, la)
        assert abs(hw + d + aw - 1.0) < 1e-6

    def test_probabilities_sum_to_one(self):
        for lh, la in [(1.2, 0.8), (1.0, 1.0), (0.5, 2.0)]:
            total = p_poisson_home_win(lh, la) + p_poisson_draw(lh, la) + p_poisson_away_win(lh, la)
            assert abs(total - 1.0) < 1e-6, f"lh={lh} la={la} sum={total}"
