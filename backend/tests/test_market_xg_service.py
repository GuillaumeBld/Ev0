"""Integration-style tests for MarketXgService.compute().

Uses a mocked AsyncSession — no real DB required.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.market_xg import (
    MarketXgResult,
    MarketXgService,
)

# ---------------------------------------------------------------------------
# Helpers to build mock MatchOddsSnapshot rows and Fixture objects
# ---------------------------------------------------------------------------

KICKOFF = datetime(2025, 4, 10, 20, 0, 0, tzinfo=UTC)
# A snapshot taken 2 hours before kickoff → NOT stale
SNAPSHOT_UTC = KICKOFF - timedelta(hours=2)
# A snapshot taken 30 hours before kickoff → stale (>24 h)
STALE_SNAPSHOT_UTC = KICKOFF - timedelta(hours=30)


def _make_fixture(fixture_id: int = 1, kickoff_utc: datetime = KICKOFF) -> MagicMock:
    fx = MagicMock()
    fx.id = fixture_id
    fx.kickoff_utc = kickoff_utc
    fx.home_team = "Home FC"
    fx.away_team = "Away FC"
    return fx


def _make_row(
    market_type: str,
    outcome: str,
    odds: float,
    bookmaker: str = "betfair",
    snapshot_utc: datetime = SNAPSHOT_UTC,
) -> MagicMock:
    row = MagicMock()
    row.market_type = market_type
    row.outcome = outcome
    row.odds = odds
    row.bookmaker = bookmaker
    row.snapshot_utc = snapshot_utc
    return row


# ---------------------------------------------------------------------------
# Compute market probabilities for constructing valid test odds
# ---------------------------------------------------------------------------

# Use a known λh=1.3, λa=1.1 (from existing tests) to derive consistent probs
_LAMBDA_H = 1.3
_LAMBDA_A = 1.1
_LAMBDA_T = _LAMBDA_H + _LAMBDA_A  # 2.4

_P_OVER = 1 - math.exp(-_LAMBDA_T) * (1 + _LAMBDA_T + _LAMBDA_T**2 / 2)
_P_BTTS = (1 - math.exp(-_LAMBDA_H)) * (1 - math.exp(-_LAMBDA_A))
_P_UNDER = 1 - _P_OVER
_P_BTTS_NO = 1 - _P_BTTS

# Decimal odds (no margin — clean inverse probabilities)
_OVER_ODDS = 1.0 / _P_OVER
_UNDER_ODDS = 1.0 / _P_UNDER
_BTTS_YES_ODDS = 1.0 / _P_BTTS
_BTTS_NO_ODDS = 1.0 / _P_BTTS_NO

# H2H: home stronger → home win prob > away win prob
_P_HOME = 0.45
_P_DRAW = 0.27
_P_AWAY = 0.28
_HOME_ODDS = 1.0 / _P_HOME
_DRAW_ODDS = 1.0 / _P_DRAW
_AWAY_ODDS = 1.0 / _P_AWAY


def _make_full_rows(
    snapshot_utc: datetime = SNAPSHOT_UTC,
    bookmaker: str = "betfair",
) -> list[MagicMock]:
    return [
        _make_row("totals", "over_2.5", _OVER_ODDS, bookmaker, snapshot_utc),
        _make_row("totals", "under_2.5", _UNDER_ODDS, bookmaker, snapshot_utc),
        _make_row("btts", "yes", _BTTS_YES_ODDS, bookmaker, snapshot_utc),
        _make_row("btts", "no", _BTTS_NO_ODDS, bookmaker, snapshot_utc),
        _make_row("h2h", "home", _HOME_ODDS, bookmaker, snapshot_utc),
        _make_row("h2h", "draw", _DRAW_ODDS, bookmaker, snapshot_utc),
        _make_row("h2h", "away", _AWAY_ODDS, bookmaker, snapshot_utc),
    ]


# ---------------------------------------------------------------------------
# Session mock builder
# ---------------------------------------------------------------------------


def _make_session(
    fixture: MagicMock | None,
    freshest_snapshot_utc: datetime | None,
    rows: list[MagicMock],
) -> AsyncMock:
    """Build a mock AsyncSession with the minimal interface used by compute()."""
    session = AsyncMock()

    # session.get(Fixture, id) → fixture
    session.get.return_value = fixture

    # session.execute() is called twice:
    #   1st call: scalar_one_or_none() → freshest_snapshot_utc
    #   2nd call: scalars().all() → rows
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = freshest_snapshot_utc

    second_result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows
    second_result.scalars.return_value = scalars_mock

    session.execute.side_effect = [first_result, second_result]

    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarketXgServiceHappyPath:
    """Valid betfair odds for all 3 markets → market_implied result."""

    @pytest.mark.asyncio
    async def test_returns_market_implied_source(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result.xg_source == "market_implied"
        assert result.flagged_reason is None

    @pytest.mark.asyncio
    async def test_lambda_h_close_to_expected(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        # With clean inverse odds for λh=1.3, λa=1.1, home is stronger (p_home > p_away)
        # → select_lambda_home should return max → λh ≈ 1.3
        assert abs(result.xg_home - _LAMBDA_H) < 0.01
        assert abs(result.xg_away - _LAMBDA_A) < 0.01

    @pytest.mark.asyncio
    async def test_result_is_market_xg_result(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert isinstance(result, MarketXgResult)
        assert result.xg_home > 0
        assert result.xg_away > 0

    @pytest.mark.asyncio
    async def test_pinnacle_fallback_when_no_betfair(self):
        """Pinnacle rows used when betfair absent."""
        fixture = _make_fixture()
        rows = _make_full_rows(bookmaker="pinnacle")
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result.xg_source == "market_implied"


class TestMarketXgServiceStaleSnapshot:
    """Snapshot taken >24 h before kickoff → dixon_coles fallback."""

    @pytest.mark.asyncio
    async def test_stale_returns_dixon_coles(self):
        fixture = _make_fixture()
        # stale snapshot, no rows needed (but provide them anyway)
        rows = _make_full_rows(snapshot_utc=STALE_SNAPSHOT_UTC)
        session = _make_session(fixture, STALE_SNAPSHOT_UTC, rows)

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(1, session)

        assert result.xg_source == "dixon_coles"

    @pytest.mark.asyncio
    async def test_exactly_24h_before_kickoff_is_not_stale(self):
        """Snapshot exactly 24 h before kickoff is NOT stale (staleness = timedelta(hours=24)
        which is NOT > timedelta(hours=24))."""
        fixture = _make_fixture()
        exact_24h = KICKOFF - timedelta(hours=24)
        rows = _make_full_rows(snapshot_utc=exact_24h)
        session = _make_session(fixture, exact_24h, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        # Should NOT fall back
        assert result.xg_source in ("market_implied", "market_implied_flagged")


class TestMarketXgServiceMissingMarkets:
    """Missing required markets → fallback."""

    @pytest.mark.asyncio
    async def test_missing_totals_returns_fallback(self):
        fixture = _make_fixture()
        rows = [
            _make_row("btts", "yes", _BTTS_YES_ODDS),
            _make_row("btts", "no", _BTTS_NO_ODDS),
            _make_row("h2h", "home", _HOME_ODDS),
            _make_row("h2h", "draw", _DRAW_ODDS),
            _make_row("h2h", "away", _AWAY_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(1, session)

        assert result.xg_source == "dixon_coles"

    @pytest.mark.asyncio
    async def test_missing_btts_returns_fallback(self):
        fixture = _make_fixture()
        rows = [
            _make_row("totals", "over_2.5", _OVER_ODDS),
            _make_row("totals", "under_2.5", _UNDER_ODDS),
            _make_row("h2h", "home", _HOME_ODDS),
            _make_row("h2h", "draw", _DRAW_ODDS),
            _make_row("h2h", "away", _AWAY_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(1, session)

        assert result.xg_source == "dixon_coles"


class TestMarketXgServiceH2HMissing:
    """H2H absent → defaults to λt/2, still computes market_implied."""

    @pytest.mark.asyncio
    async def test_no_h2h_still_computes(self):
        fixture = _make_fixture()
        rows = [
            _make_row("totals", "over_2.5", _OVER_ODDS),
            _make_row("totals", "under_2.5", _UNDER_ODDS),
            _make_row("btts", "yes", _BTTS_YES_ODDS),
            _make_row("btts", "no", _BTTS_NO_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result.xg_source in ("market_implied", "market_implied_flagged")
        assert result.xg_home > 0
        assert result.xg_away > 0

    @pytest.mark.asyncio
    async def test_no_h2h_splits_lambda_t_evenly(self):
        """Without H2H, λh = λt/2 so xg_home should equal xg_away (within rounding)."""
        fixture = _make_fixture()
        rows = [
            _make_row("totals", "over_2.5", _OVER_ODDS),
            _make_row("totals", "under_2.5", _UNDER_ODDS),
            _make_row("btts", "yes", _BTTS_YES_ODDS),
            _make_row("btts", "no", _BTTS_NO_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        # With no H2H, λh = λa = λt/2 → xg_home == xg_away after rounding
        assert abs(result.xg_home - result.xg_away) < 0.01


class TestMarketXgServiceSolverFailure:
    """Degenerate BTTS odds → solver raises ValueError → fallback."""

    @pytest.mark.asyncio
    async def test_degenerate_btts_falls_back(self):
        """BTTS p > max achievable → solve_lambda_home raises ValueError."""
        fixture = _make_fixture()
        # Use a very high BTTS probability (>0.5) relative to a small λt
        # λt~0.5 → max BTTS is tiny → p_btts=0.9 is degenerate
        # We need over_odds that implies a very small λt: P(over2.5) ~ 0.01
        # For λt ≈ 0.3: P(over) ≈ 0.004 → use odds ~250
        rows = [
            _make_row("totals", "over_2.5", 250.0),   # very low probability
            _make_row("totals", "under_2.5", 1.004),  # almost certain under
            _make_row("btts", "yes", 1.11),            # high BTTS probability
            _make_row("btts", "no", 9.0),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(1, session)

        assert result.xg_source == "dixon_coles"


class TestMarketXgServiceCrossValidationFlag:
    """Intentionally inconsistent markets → market_implied_flagged."""

    @pytest.mark.asyncio
    async def test_inconsistent_markets_flagged(self):
        """Pass mismatched Over/BTTS odds so cross_validate returns (False, reason)."""
        # Use λh=1.3, λa=1.1 for over odds
        # But use λh=0.4, λa=0.4 (very different λt) for btts odds
        # → cross_validate will catch the mismatch

        p_over = _P_OVER

        # Build BTTS odds from a VERY different λ pair
        lh_btts, la_btts = 0.4, 0.4
        p_btts_small = (1 - math.exp(-lh_btts)) * (1 - math.exp(-la_btts))
        p_btts_no_small = 1 - p_btts_small
        btts_yes_odds = 1.0 / p_btts_small
        btts_no_odds = 1.0 / p_btts_no_small

        p_under = 1.0 - p_over
        rows = [
            _make_row("totals", "over_2.5", 1.0 / p_over),
            _make_row("totals", "under_2.5", 1.0 / p_under),
            _make_row("btts", "yes", btts_yes_odds),
            _make_row("btts", "no", btts_no_odds),
            # no H2H → default to λt/2
        ]
        session = _make_session(fixture=_make_fixture(), freshest_snapshot_utc=SNAPSHOT_UTC, rows=rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        # The solver derives λh from BTTS (lh1≈lh2≈0.4, λt≈2.4 from over odds).
        # cross_validate predicts P(BTTS) ≈ (1-e^-1.2)^2 ≈ 0.533 but p_btts ≈ 0.12
        # → absolute error ≈ 0.41 >> 0.08 threshold → must flag.
        assert result.xg_source == "market_implied_flagged"
        assert result.flagged_reason is not None


class TestMarketXgServiceFixtureNotFound:
    """Fixture not found in DB → fallback."""

    @pytest.mark.asyncio
    async def test_fixture_not_found_returns_dixon_coles(self):
        session = AsyncMock()
        session.get.return_value = None

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(999, session)

        assert result.xg_source == "dixon_coles"


class TestMarketXgServiceNoSnapshot:
    """No snapshot at all in DB → fallback."""

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_dixon_coles(self):
        fixture = _make_fixture()
        session = AsyncMock()
        session.get.return_value = fixture

        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = None
        session.execute.return_value = first_result

        with patch(
            "app.services.market_xg.get_dixon_coles_fallback",
            new_callable=AsyncMock,
            return_value=MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles"),
        ):
            svc = MarketXgService()
            result = await svc.compute(1, session)

        assert result.xg_source == "dixon_coles"
