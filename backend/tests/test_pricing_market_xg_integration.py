"""Focused integration tests for MarketXgService integration in load_match_pricing().

Verifies:
- MarketXgResult values flow into MatchPricingResult (home/away xg + xg_source)
- Override parameters bypass market xg and produce xg_source='override'
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.team_xg import MatchPricingResult, load_match_pricing
from app.services.market_xg import MarketXgResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fixture(fixture_id: int = 1) -> MagicMock:
    fx = MagicMock()
    fx.id = fixture_id
    fx.home_team = "Home FC"
    fx.away_team = "Away FC"
    return fx


def _make_db_session() -> AsyncMock:
    """Return an AsyncSession mock that returns empty player lists."""
    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.all.return_value = []
    session.execute.return_value = empty_result
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarketXgIntegration:
    """MarketXgService.compute result is correctly propagated to MatchPricingResult."""

    @pytest.mark.asyncio
    async def test_market_implied_xg_flows_into_pricing_result(self):
        """MarketXgResult(1.5, 1.2, market_implied) → MatchPricingResult mirrors values."""
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.5,
            xg_away=1.2,
            xg_source="market_implied",
        )

        with patch("app.pricing.team_xg.MarketXgService") as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(db, fixture)

        assert isinstance(result, MatchPricingResult)
        assert result.home_match_xg == pytest.approx(1.5, abs=0.001)
        assert result.away_match_xg == pytest.approx(1.2, abs=0.001)
        assert result.xg_source == "market_implied"

    @pytest.mark.asyncio
    async def test_home_xg_override_sets_source_override(self):
        """When home_xg_override=2.0 is passed, xg_source='override' and home_match_xg=2.0."""
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.5,
            xg_away=1.2,
            xg_source="market_implied",
        )

        with patch("app.pricing.team_xg.MarketXgService") as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(
                db,
                fixture,
                home_xg_override=2.0,
            )

        assert result.home_match_xg == pytest.approx(2.0, abs=0.001)
        assert result.away_match_xg == pytest.approx(1.2, abs=0.001)
        assert result.xg_source == "override"

    @pytest.mark.asyncio
    async def test_both_overrides_bypass_market_service(self):
        """When both overrides are provided, MarketXgService.compute is not called."""
        fixture = _make_fixture()
        db = _make_db_session()

        with patch("app.pricing.team_xg.MarketXgService") as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock()

            result = await load_match_pricing(
                db,
                fixture,
                home_xg_override=2.0,
                away_xg_override=0.8,
            )

        instance.compute.assert_not_called()
        assert result.home_match_xg == pytest.approx(2.0, abs=0.001)
        assert result.away_match_xg == pytest.approx(0.8, abs=0.001)
        assert result.xg_source == "override"
