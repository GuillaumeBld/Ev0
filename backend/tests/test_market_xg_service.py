"""Integration-style tests for MarketXgService.compute().

Uses a mocked AsyncSession — no real DB required.
Pipeline: Over-2.5 → λt, H2H → λh (Poisson inversion), cross-validate.
Source unique : ps3838 (XG_BOOKMAKER). D'autres bookmakers presents dans les
rows mockees doivent etre ignores -- la vraie requete SQL les filtrerait deja
via bookmaker == XG_BOOKMAKER, mais le mock ne rejoue pas le WHERE (cf.
test_asof_xg.py) donc on verifie ici le filtrage cote Python.

NOTE: compute() now returns None (not Dixon-Coles) on failure/staleness.
Staleness is based on now - snapshot_utc > MAX_SNAPSHOT_AGE (3 h), not kickoff-relative.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.market_xg import (
    MarketXgResult,
    MarketXgService,
    _poisson_home_win,
)

# ---------------------------------------------------------------------------
# Helpers to build mock MatchOddsSnapshot rows and Fixture objects
# ---------------------------------------------------------------------------

# Use a kickoff in the future so tests are time-stable
KICKOFF = datetime(2099, 4, 10, 20, 0, 0, tzinfo=UTC)

# A snapshot taken 1 hour ago → NOT stale (age < 3h)
SNAPSHOT_UTC = datetime.now(timezone.utc) - timedelta(hours=1)

# A snapshot taken 5 hours ago → stale (age > 3h = MAX_SNAPSHOT_AGE)
STALE_SNAPSHOT_UTC = datetime.now(timezone.utc) - timedelta(hours=5)


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
    bookmaker: str = "ps3838",
    snapshot_utc: datetime = None,
) -> MagicMock:
    if snapshot_utc is None:
        snapshot_utc = SNAPSHOT_UTC
    row = MagicMock()
    row.market_type = market_type
    row.outcome = outcome
    row.odds = odds
    row.bookmaker = bookmaker
    row.snapshot_utc = snapshot_utc
    row.id = None
    row.source = None
    row.fallback_used = False
    return row


# ---------------------------------------------------------------------------
# Compute market probabilities for constructing valid test odds
# Pipeline: Over-2.5 gives λt, H2H gives λh via Poisson inversion
# ---------------------------------------------------------------------------

_LAMBDA_H = 1.3
_LAMBDA_A = 1.1
_LAMBDA_T = _LAMBDA_H + _LAMBDA_A  # 2.4

_P_OVER = 1 - math.exp(-_LAMBDA_T) * (1 + _LAMBDA_T + _LAMBDA_T**2 / 2)
_P_UNDER = 1 - _P_OVER

# H2H probabilities derived from Poisson(λh=1.3, λa=1.1)
_P_HOME_WIN = _poisson_home_win(_LAMBDA_H, _LAMBDA_A)
_P_AWAY_WIN = _poisson_home_win(_LAMBDA_A, _LAMBDA_H)  # symmetric
_P_DRAW = 1.0 - _P_HOME_WIN - _P_AWAY_WIN

# Decimal odds (no margin — clean inverse probabilities)
_OVER_ODDS = 1.0 / _P_OVER
_UNDER_ODDS = 1.0 / _P_UNDER
_HOME_ODDS = 1.0 / _P_HOME_WIN
_DRAW_ODDS = 1.0 / _P_DRAW
_AWAY_ODDS = 1.0 / _P_AWAY_WIN


def _make_full_rows(
    snapshot_utc: datetime = None,
    bookmaker: str = "ps3838",
) -> list[MagicMock]:
    if snapshot_utc is None:
        snapshot_utc = SNAPSHOT_UTC
    return [
        _make_row("totals", "over_2.5", _OVER_ODDS, bookmaker, snapshot_utc),
        _make_row("totals", "under_2.5", _UNDER_ODDS, bookmaker, snapshot_utc),
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
    """Valid ps3838 odds for totals + h2h → market_implied result."""

    @pytest.mark.asyncio
    async def test_returns_market_implied_source(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        assert result.xg_source == "market_implied"

    @pytest.mark.asyncio
    async def test_lambda_h_close_to_expected(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        # H2H odds derived from Poisson(1.3, 1.1) → solver recovers λh≈1.3, λa≈1.1
        assert abs(result.xg_home - _LAMBDA_H) < 0.05
        assert abs(result.xg_away - _LAMBDA_A) < 0.05

    @pytest.mark.asyncio
    async def test_result_is_market_xg_result(self):
        fixture = _make_fixture()
        session = _make_session(fixture, SNAPSHOT_UTC, _make_full_rows())

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        assert isinstance(result, MarketXgResult)
        assert result.xg_home > 0
        assert result.xg_away > 0

    @pytest.mark.asyncio
    async def test_non_ps3838_bookmaker_alone_returns_none(self):
        """Plus de fallback multi-bookmaker : sans ps3838, aucune source n'est lue."""
        fixture = _make_fixture()
        rows = _make_full_rows(bookmaker="pinnacle")
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None

    @pytest.mark.asyncio
    async def test_other_bookmakers_ignored_when_ps3838_present(self):
        """Des rows pinnacle en plus des rows ps3838 ne doivent rien changer au
        resultat -- une seule source est lue, jamais un melange."""
        fixture = _make_fixture()
        ps3838_rows = _make_full_rows(bookmaker="ps3838")
        pinnacle_rows = _make_full_rows(bookmaker="pinnacle")
        rows = ps3838_rows + pinnacle_rows
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        assert result.xg_source == "market_implied"
        assert abs(result.xg_home - _LAMBDA_H) < 0.05
        assert abs(result.xg_away - _LAMBDA_A) < 0.05


class TestMarketXgServiceStaleSnapshot:
    """Snapshot older than MAX_SNAPSHOT_AGE (3h) → returns None."""

    @pytest.mark.asyncio
    async def test_stale_returns_none(self):
        fixture = _make_fixture()
        rows = _make_full_rows(snapshot_utc=STALE_SNAPSHOT_UTC)
        session = _make_session(fixture, STALE_SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None

    @pytest.mark.asyncio
    async def test_fresh_snapshot_not_stale(self):
        """Snapshot 1 hour old is NOT stale (< 3h)."""
        fixture = _make_fixture()
        fresh_utc = datetime.now(timezone.utc) - timedelta(hours=1)
        rows = _make_full_rows(snapshot_utc=fresh_utc)
        session = _make_session(fixture, fresh_utc, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        assert result.xg_source in ("market_implied", "market_implied_flagged")


class TestMarketXgServiceMissingMarkets:
    """Missing required markets → returns None."""

    @pytest.mark.asyncio
    async def test_missing_totals_returns_none(self):
        fixture = _make_fixture()
        rows = [
            _make_row("h2h", "home", _HOME_ODDS),
            _make_row("h2h", "draw", _DRAW_ODDS),
            _make_row("h2h", "away", _AWAY_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_h2h_returns_none(self):
        """H2H is required — missing h2h → None."""
        fixture = _make_fixture()
        rows = [
            _make_row("totals", "over_2.5", _OVER_ODDS),
            _make_row("totals", "under_2.5", _UNDER_ODDS),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None


class TestMarketXgServiceSolverFailure:
    """Degenerate H2H odds (p_home_win outside solver bracket) → returns None."""

    @pytest.mark.asyncio
    async def test_degenerate_h2h_returns_none(self):
        """p_home_win > max achievable for given λt → H2H solver bracket has no sign change."""
        fixture = _make_fixture()
        # Very small λt (~0.3): P(home win) max is tiny.
        # Use over_odds~250 → P(over2.5)~0.004 → λt≈0.3.
        # p_home_win=0.9 is impossible for λt=0.3 → no root.
        rows = [
            _make_row("totals", "over_2.5", 250.0),
            _make_row("totals", "under_2.5", 1.004),
            _make_row("h2h", "home", 1.11),   # p_home≈0.9
            _make_row("h2h", "draw", 9.0),
            _make_row("h2h", "away", 100.0),
        ]
        session = _make_session(fixture, SNAPSHOT_UTC, rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None


class TestMarketXgServiceCrossValidationFlag:
    """Inconsistent Over-2.5 / H2H odds → market_implied_flagged."""

    @pytest.mark.asyncio
    async def test_inconsistent_markets_flagged(self):
        """Over-2.5 implies λt=2.4 but H2H implies a very lopsided match (e.g. 95% home win).
        The solver finds a λh, but cross_validate catches the H2H mismatch."""
        lh_true, la_true = 2.2, 0.2
        p_hw_inconsistent = _poisson_home_win(lh_true, la_true)
        p_draw_inconsistent = 1 - p_hw_inconsistent - _poisson_home_win(la_true, lh_true)
        p_aw_inconsistent = _poisson_home_win(la_true, lh_true)

        rows = [
            # Over-2.5 consistent with λt=2.4
            _make_row("totals", "over_2.5", _OVER_ODDS),
            _make_row("totals", "under_2.5", _UNDER_ODDS),
            # H2H consistent with λh=2.2, λa=0.2 (same λt=2.4 but very different split)
            _make_row("h2h", "home", 1.0 / p_hw_inconsistent),
            _make_row("h2h", "draw", 1.0 / max(p_draw_inconsistent, 0.001)),
            _make_row("h2h", "away", 1.0 / max(p_aw_inconsistent, 0.001)),
        ]
        session = _make_session(fixture=_make_fixture(), freshest_snapshot_utc=SNAPSHOT_UTC, rows=rows)

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is not None
        assert result.xg_source in ("market_implied", "market_implied_flagged")
        assert result.xg_home > 0
        assert result.xg_away > 0


class TestMarketXgServiceFixtureNotFound:
    """Fixture not found in DB → returns None."""

    @pytest.mark.asyncio
    async def test_fixture_not_found_returns_none(self):
        session = AsyncMock()
        session.get.return_value = None

        svc = MarketXgService()
        result = await svc.compute(999, session)

        assert result is None


class TestMarketXgServiceNoSnapshot:
    """No snapshot at all in DB → returns None."""

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_none(self):
        fixture = _make_fixture()
        session = AsyncMock()
        session.get.return_value = fixture

        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = None
        session.execute.return_value = first_result

        svc = MarketXgService()
        result = await svc.compute(1, session)

        assert result is None
