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


from unittest.mock import AsyncMock, MagicMock

from app.services.recommendation_service import _generate_h2h_recs


class TestGenerateH2hRecs:
    """Unit tests for _generate_h2h_recs."""

    def _make_snapshot(self, bookmaker: str, outcome: str, odds: float):
        snap = MagicMock()
        snap.bookmaker = bookmaker
        snap.outcome = outcome
        snap.odds = odds
        return snap

    @pytest.mark.asyncio
    async def test_returns_home_rec_when_generous_odds(self):
        """When betclic quotes 2.20 on home win but fair is ~2.00, edge ~10% → VALUE."""
        # lh=1.5, la=1.0 → p_home ≈ 0.494 → fair_odds ≈ 2.02
        lh, la = 1.5, 1.0
        snapshots = [
            self._make_snapshot("betclic", "home", 2.25),
            self._make_snapshot("betclic", "draw", 3.40),
            self._make_snapshot("betclic", "away", 3.60),
        ]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = snapshots
        session.execute = AsyncMock(return_value=mock_result)

        recs = await _generate_h2h_recs(fixture_id=1, lh=lh, la=la, session=session)

        home_rec = next((r for r in recs if r["outcome"] == "home"), None)
        assert home_rec is not None
        assert home_rec["edge"] > 0.05
        assert home_rec["classification"] == "VALUE"
        assert home_rec["best_bookmaker"] == "betclic"
        assert home_rec["best_odds"] == 2.25

    @pytest.mark.asyncio
    async def test_skips_outcome_when_no_odds_available(self):
        """If no FR book has home odds, the home outcome is omitted."""
        snapshots = [
            self._make_snapshot("betclic", "draw", 3.40),
            self._make_snapshot("betclic", "away", 3.60),
        ]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = snapshots
        session.execute = AsyncMock(return_value=mock_result)

        recs = await _generate_h2h_recs(fixture_id=1, lh=1.5, la=1.0, session=session)

        assert not any(r["outcome"] == "home" for r in recs)

    @pytest.mark.asyncio
    async def test_excludes_negative_edge_outcomes(self):
        """Outcomes with edge < 0 (AVOID) are not returned."""
        # lh=la=1.2 → all fair prices are close market prices; squeeze odds to be lower
        snapshots = [
            self._make_snapshot("betclic", "home", 1.80),  # well below fair ~2.3
            self._make_snapshot("betclic", "draw", 2.90),
            self._make_snapshot("betclic", "away", 3.10),
        ]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = snapshots
        session.execute = AsyncMock(return_value=mock_result)

        recs = await _generate_h2h_recs(fixture_id=1, lh=1.2, la=1.2, session=session)

        assert not any(r["outcome"] == "home" for r in recs)

    @pytest.mark.asyncio
    async def test_picks_best_odds_across_bookmakers(self):
        """Best (highest) odds win across FR books."""
        snapshots = [
            self._make_snapshot("betclic", "home", 2.10),
            self._make_snapshot("unibet", "home", 2.30),
            self._make_snapshot("pmu", "home", 2.05),
        ]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = snapshots
        session.execute = AsyncMock(return_value=mock_result)

        recs = await _generate_h2h_recs(fixture_id=1, lh=1.5, la=1.0, session=session)

        home_rec = next((r for r in recs if r["outcome"] == "home"), None)
        assert home_rec is not None
        assert home_rec["best_odds"] == 2.30
        assert home_rec["best_bookmaker"] == "unibet"
