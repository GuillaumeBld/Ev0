"""Integration tests for load_match_pricing() + MarketXgService integration.

Mocks MarketXgService.compute to avoid DB dependency and verifies that:
- xg_home / xg_away from MarketXgResult are passed into MatchPricingResult
- xg_source is propagated correctly for all branches
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.team_xg import MatchPricingResult, load_match_pricing
from app.services.market_xg import MarketXgResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fixture(fixture_id: int = 42) -> MagicMock:
    fx = MagicMock()
    fx.id = fixture_id
    fx.home_team = "Home FC"
    fx.away_team = "Away FC"
    return fx


def _make_db_session() -> AsyncMock:
    """Return an AsyncSession mock that returns empty player lists."""
    session = AsyncMock()

    # _load_team_players calls db.execute(); return an empty result set
    empty_result = MagicMock()
    empty_result.all.return_value = []
    session.execute.return_value = empty_result

    return session


# ---------------------------------------------------------------------------
# Tests: MarketXgService result flows into MatchPricingResult
# ---------------------------------------------------------------------------


class TestLoadMatchPricingMarketImplied:
    """MarketXgService returns market_implied → MatchPricingResult mirrors it."""

    @pytest.mark.asyncio
    async def test_market_implied_xg_used(self):
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.45,
            xg_away=1.10,
            xg_source="market_implied",
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(db, fixture)

        assert isinstance(result, MatchPricingResult)
        assert result.home_match_xg == pytest.approx(1.45, abs=0.001)
        assert result.away_match_xg == pytest.approx(1.10, abs=0.001)
        assert result.xg_source == "market_implied"

    @pytest.mark.asyncio
    async def test_market_implied_flagged_propagated(self):
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.20,
            xg_away=0.95,
            xg_source="market_implied_flagged",
            flagged_reason="Over 2.5 cross-validation error 0.12 > 0.08",
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(db, fixture)

        assert result.xg_source == "market_implied_flagged"

    @pytest.mark.asyncio
    async def test_dixon_coles_fallback_propagated(self):
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.30,
            xg_away=1.00,
            xg_source="dixon_coles",
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(db, fixture)

        assert result.xg_source == "dixon_coles"
        assert result.home_match_xg == pytest.approx(1.30, abs=0.001)
        assert result.away_match_xg == pytest.approx(1.00, abs=0.001)


# ---------------------------------------------------------------------------
# Tests: override parameters bypass MarketXgService
# ---------------------------------------------------------------------------


class TestLoadMatchPricingOverrides:
    """When both overrides are supplied, MarketXgService is not called and
    xg_source is 'override'."""

    @pytest.mark.asyncio
    async def test_both_overrides_set_source_override(self):
        fixture = _make_fixture()
        db = _make_db_session()

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock()

            result = await load_match_pricing(
                db,
                fixture,
                home_xg_override=2.0,
                away_xg_override=0.8,
            )

        # MarketXgService.compute should NOT have been called
        instance.compute.assert_not_called()

        assert result.xg_source == "override"
        assert result.home_match_xg == pytest.approx(2.0, abs=0.001)
        assert result.away_match_xg == pytest.approx(0.8, abs=0.001)

    @pytest.mark.asyncio
    async def test_partial_override_home_only_uses_market_for_away(self):
        """When only home_xg_override is set, MarketXgService is called and
        xg_source becomes 'override'."""
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.40,
            xg_away=1.15,
            xg_source="market_implied",
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(
                db,
                fixture,
                home_xg_override=2.5,
            )

        # MarketXgService IS called (only one override)
        instance.compute.assert_called_once()

        assert result.home_match_xg == pytest.approx(2.5, abs=0.001)
        assert result.away_match_xg == pytest.approx(1.15, abs=0.001)
        # Partial override sets xg_source to 'override'
        assert result.xg_source == "override"

    @pytest.mark.asyncio
    async def test_partial_override_away_only_uses_market_for_home(self):
        """When only away_xg_override is set, MarketXgService is called and
        xg_source becomes 'override'."""
        fixture = _make_fixture()
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.40,
            xg_away=1.15,
            xg_source="market_implied",
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            result = await load_match_pricing(
                db,
                fixture,
                away_xg_override=0.5,
            )

        instance.compute.assert_called_once()

        assert result.home_match_xg == pytest.approx(1.40, abs=0.001)
        assert result.away_match_xg == pytest.approx(0.5, abs=0.001)
        assert result.xg_source == "override"


# ---------------------------------------------------------------------------
# Test: fixture_id is passed correctly to MarketXgService
# ---------------------------------------------------------------------------


class TestLoadMatchPricingFixtureId:
    @pytest.mark.asyncio
    async def test_correct_fixture_id_passed_to_service(self):
        fixture = _make_fixture(fixture_id=99)
        db = _make_db_session()

        market_result = MarketXgResult(
            xg_home=1.0, xg_away=1.0, xg_source="dixon_coles"
        )

        with patch(
            "app.pricing.team_xg.MarketXgService",
        ) as MockService:
            instance = MockService.return_value
            instance.compute = AsyncMock(return_value=market_result)

            await load_match_pricing(db, fixture)

        instance.compute.assert_called_once_with(99, db)
