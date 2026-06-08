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


from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.recommendation_service import process_scraped_fixtures


class TestProcessScrapedFixturesH2h:
    """Integration-level test: process_scraped_fixtures creates h2h recs."""

    def _make_fixture(self, fixture_id: int = 42):
        fx = MagicMock()
        fx.id = fixture_id
        fx.home_team = "PSG"
        fx.away_team = "Lyon"
        return fx

    def _make_pricing(self, home_xg: float = 1.5, away_xg: float = 1.0):
        pricing = MagicMock()
        pricing.home_match_xg = home_xg
        pricing.away_match_xg = away_xg
        pricing.xg_source = "market_implied"
        pricing.home_players = []
        pricing.away_players = []
        return pricing

    def _make_h2h_snap(self, bookmaker: str, outcome: str, odds: float):
        snap = MagicMock(spec=["bookmaker", "market_type", "outcome", "odds"])
        snap.bookmaker = bookmaker
        snap.market_type = "h2h"
        snap.outcome = outcome
        snap.odds = odds
        return snap

    @pytest.mark.asyncio
    async def test_h2h_recs_created_for_value_outcomes(self):
        fixture = self._make_fixture(42)
        pricing = self._make_pricing(1.5, 1.0)

        # lh=1.5, la=1.0 → p_home ≈ 0.494 → fair ≈ 2.02 → 2.25 gives edge ~11%
        h2h_snaps = [
            self._make_h2h_snap("betclic", "home", 2.25),
            self._make_h2h_snap("unibet", "draw", 3.40),
            self._make_h2h_snap("betclic", "away", 3.60),
        ]

        session = AsyncMock()
        added_recs: list = []
        session.add = lambda rec: added_recs.append(rec)
        session.commit = AsyncMock()

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_res = MagicMock()
            if call_count == 1:  # SELECT Fixture
                mock_res.scalars.return_value.all.return_value = [fixture]
            elif call_count == 2:  # SELECT Recommendation (all_recs)
                mock_res.scalars.return_value.all.return_value = []
            elif call_count == 3:  # SELECT AppConfig (pen takers)
                mock_res.scalars.return_value.all.return_value = []
            elif call_count == 4:  # SELECT PlayerOddsSnapshot (player pipeline)
                mock_res.scalars.return_value.all.return_value = []
            elif call_count == 5:  # SELECT MatchOddsSnapshot (h2h)
                mock_res.scalars.return_value.all.return_value = h2h_snaps
            else:
                mock_res.scalars.return_value.all.return_value = []
            return mock_res

        session.execute = fake_execute

        with patch(
            "app.services.recommendation_service.load_match_pricing",
            new=AsyncMock(return_value=pricing),
        ):
            stats = await process_scraped_fixtures([42], session)

        assert stats["created"] >= 1
        outcomes_created = {r.player_name for r in added_recs if r.market_type == "h2h"}
        assert "home" in outcomes_created
