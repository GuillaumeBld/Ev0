"""Tests for app.pricing.xg_resolver — xG source resolver with mode toggle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.xg_resolver import XgMode, get_global_xg_mode, resolve_xg_source, set_global_xg_mode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_with_execute_result(scalar_value) -> AsyncMock:
    """Build a mock AsyncSession where execute().scalar_one_or_none() returns scalar_value."""
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = scalar_value
    session.execute.return_value = execute_result
    return session


def _make_app_config_row(value: str) -> MagicMock:
    row = MagicMock()
    row.key = "xg_source"
    row.value = value
    return row


def _make_bzz_event(home_xg: float | None, away_xg: float | None) -> MagicMock:
    event = MagicMock()
    event.api_id = 42
    event.home_xg = home_xg
    event.away_xg = away_xg
    event.home_team_api_id = 1
    event.away_team_api_id = 2
    return event


# ---------------------------------------------------------------------------
# get_global_xg_mode
# ---------------------------------------------------------------------------


class TestGetGlobalXgMode:
    @pytest.mark.asyncio
    async def test_get_global_xg_mode_default(self):
        """When DB has no row, returns XgMode.BZZOIRO."""
        session = _make_session_with_execute_result(None)
        mode = await get_global_xg_mode(session)
        assert mode == XgMode.BZZOIRO

    @pytest.mark.asyncio
    async def test_get_global_xg_mode_bzzoiro_explicit(self):
        """When DB has key=xg_source, value=bzzoiro → returns XgMode.BZZOIRO."""
        row = _make_app_config_row("bzzoiro")
        session = _make_session_with_execute_result(row)
        mode = await get_global_xg_mode(session)
        assert mode == XgMode.BZZOIRO

    @pytest.mark.asyncio
    async def test_get_global_xg_mode_model(self):
        """When DB has key=xg_source, value=model → returns XgMode.MODEL."""
        row = _make_app_config_row("model")
        session = _make_session_with_execute_result(row)
        mode = await get_global_xg_mode(session)
        assert mode == XgMode.MODEL

    @pytest.mark.asyncio
    async def test_get_global_xg_mode_unknown_defaults_to_bzzoiro(self):
        """Unknown value falls back to BZZOIRO."""
        row = _make_app_config_row("unknown_source")
        session = _make_session_with_execute_result(row)
        mode = await get_global_xg_mode(session)
        assert mode == XgMode.BZZOIRO


# ---------------------------------------------------------------------------
# resolve_xg_source — BZZOIRO mode with data
# ---------------------------------------------------------------------------


class TestResolveXgSourceBzzoiro:
    @pytest.mark.asyncio
    async def test_resolve_xg_source_bzzoiro_has_data(self):
        """When mode is BZZOIRO and BzzEvent has xG data → returns bzzoiro values."""
        bzz_event = _make_bzz_event(home_xg=1.5, away_xg=0.8)

        session = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = bzz_event
        session.execute.return_value = execute_result

        home_xg, away_xg, source = await resolve_xg_source(
            session, event_api_id=42, global_mode=XgMode.BZZOIRO
        )
        assert home_xg == 1.5
        assert away_xg == 0.8
        assert source == "bzzoiro"

    @pytest.mark.asyncio
    async def test_resolve_xg_source_bzzoiro_fallback_when_home_xg_none(self):
        """When BzzEvent.home_xg is None → falls back to model."""
        bzz_event = _make_bzz_event(home_xg=None, away_xg=0.8)

        session = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = bzz_event
        session.execute.return_value = execute_result

        with patch(
            "app.pricing.xg_resolver._compute_model_xg",
            new=AsyncMock(return_value=(1.2, 0.9)),
        ):
            home_xg, away_xg, source = await resolve_xg_source(
                session, event_api_id=42, global_mode=XgMode.BZZOIRO
            )

        assert source == "model"
        assert home_xg == 1.2
        assert away_xg == 0.9

    @pytest.mark.asyncio
    async def test_resolve_xg_source_bzzoiro_fallback_when_no_event(self):
        """When BzzEvent not found → falls back to model."""
        session = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        session.execute.return_value = execute_result

        with patch(
            "app.pricing.xg_resolver._compute_model_xg",
            new=AsyncMock(return_value=(1.1, 0.7)),
        ):
            home_xg, away_xg, source = await resolve_xg_source(
                session, event_api_id=99, global_mode=XgMode.BZZOIRO
            )

        assert source == "model"
        assert home_xg == 1.1
        assert away_xg == 0.7


# ---------------------------------------------------------------------------
# resolve_xg_source — MODEL mode
# ---------------------------------------------------------------------------


class TestResolveXgSourceModel:
    @pytest.mark.asyncio
    async def test_resolve_xg_source_model_mode(self):
        """When mode=MODEL → calls model immediately, returns (x, y, 'model')."""
        session = AsyncMock()

        with patch(
            "app.pricing.xg_resolver._compute_model_xg",
            new=AsyncMock(return_value=(1.8, 1.1)),
        ):
            home_xg, away_xg, source = await resolve_xg_source(
                session, event_api_id=10, global_mode=XgMode.MODEL
            )

        assert source == "model"
        assert home_xg == 1.8
        assert away_xg == 1.1

    @pytest.mark.asyncio
    async def test_resolve_xg_source_model_failure_returns_none(self):
        """When model computation raises an exception → returns (None, None, 'model')."""
        session = AsyncMock()

        with patch(
            "app.pricing.xg_resolver._compute_model_xg",
            new=AsyncMock(side_effect=RuntimeError("db error")),
        ):
            home_xg, away_xg, source = await resolve_xg_source(
                session, event_api_id=10, global_mode=XgMode.MODEL
            )

        assert home_xg is None
        assert away_xg is None
        assert source == "model"


# ---------------------------------------------------------------------------
# resolve_xg_source — global_mode fetched from DB
# ---------------------------------------------------------------------------


class TestResolveXgSourceFetchesMode:
    @pytest.mark.asyncio
    async def test_resolve_fetches_mode_from_db_when_none(self):
        """When global_mode=None, mode is fetched from DB."""
        bzz_event = _make_bzz_event(home_xg=2.0, away_xg=1.0)

        session = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = bzz_event
        session.execute.return_value = execute_result

        with patch(
            "app.pricing.xg_resolver.get_global_xg_mode",
            new=AsyncMock(return_value=XgMode.BZZOIRO),
        ):
            home_xg, away_xg, source = await resolve_xg_source(
                session, event_api_id=42, global_mode=None
            )

        assert source == "bzzoiro"
        assert home_xg == 2.0
        assert away_xg == 1.0
