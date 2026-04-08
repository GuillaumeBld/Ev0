"""Tests for the market scrape fallback chain."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.market_scrape_chain import ScrapeResult, run_scrape_chain


def _complete_result(source: str, fallback: bool = False) -> ScrapeResult:
    return ScrapeResult(
        source=source,
        source_url=f"https://{source}.com/match",
        parse_version=f"{source[:2]}-v1",
        h2h={"home": 2.05, "draw": 3.30, "away": 3.80},
        totals={"over_2.5": 1.95, "under_2.5": 1.95},
        btts={"yes": 1.80, "no": 1.95},
        ingested_at_utc=datetime.now(timezone.utc),
        fallback_used=fallback,
    )


def _incomplete_result(source: str) -> ScrapeResult:
    return ScrapeResult(
        source=source,
        source_url=f"https://{source}.com/match",
        parse_version=f"{source[:2]}-v1",
        h2h=None,
        totals=None,
        btts=None,
        ingested_at_utc=datetime.now(timezone.utc),
        error="timeout",
    )


def _make_poll_state(
    fixture_id: int = 42,
    op_url: str = "https://oddsportal.com/match",
    bc_url: str | None = "https://betclic.fr/match",
    ub_url: str | None = "https://unibet.fr/sport/1234567",
) -> MagicMock:
    state = MagicMock()
    state.fixture_id = fixture_id
    state.oddsportal_url = op_url
    state.betclic_url = bc_url
    state.unibet_url = ub_url
    return state


class TestScrapeResult:
    def test_is_complete_when_all_markets_present(self):
        r = _complete_result("oddsportal")
        assert r.is_complete is True

    def test_is_not_complete_when_any_market_missing(self):
        r = ScrapeResult(
            source="oddsportal",
            source_url="https://oddsportal.com/match",
            parse_version="op-v1",
            h2h={"home": 2.05, "draw": 3.30, "away": 3.80},
            totals=None,
            btts={"yes": 1.80, "no": 1.95},
        )
        assert r.is_complete is False

    def test_fallback_used_defaults_false(self):
        r = _complete_result("oddsportal")
        assert r.fallback_used is False


class TestRunScrapeChain:
    @pytest.mark.asyncio
    async def test_returns_oddsportal_on_success(self):
        poll_state = _make_poll_state()
        op_result = _complete_result("oddsportal")

        with patch(
            "app.ingestion.market_scrape_chain._scrape_oddsportal",
            new=AsyncMock(return_value=op_result),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is not None
        assert result.source == "oddsportal"
        assert result.fallback_used is False

    @pytest.mark.asyncio
    async def test_falls_back_to_betclic_on_oddsportal_failure(self):
        poll_state = _make_poll_state()
        bc_result = _complete_result("betclic", fallback=True)

        with (
            patch(
                "app.ingestion.market_scrape_chain._scrape_oddsportal",
                new=AsyncMock(return_value=_incomplete_result("oddsportal")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_betclic",
                new=AsyncMock(return_value=bc_result),
            ),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is not None
        assert result.source == "betclic"
        assert result.fallback_used is True

    @pytest.mark.asyncio
    async def test_falls_back_to_unibet_when_op_and_bc_fail(self):
        poll_state = _make_poll_state()
        ub_result = _complete_result("unibet", fallback=True)

        with (
            patch(
                "app.ingestion.market_scrape_chain._scrape_oddsportal",
                new=AsyncMock(return_value=_incomplete_result("oddsportal")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_betclic",
                new=AsyncMock(return_value=_incomplete_result("betclic")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_unibet",
                new=AsyncMock(return_value=ub_result),
            ),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is not None
        assert result.source == "unibet"

    @pytest.mark.asyncio
    async def test_returns_none_when_all_sources_fail(self):
        poll_state = _make_poll_state()

        with (
            patch(
                "app.ingestion.market_scrape_chain._scrape_oddsportal",
                new=AsyncMock(return_value=_incomplete_result("oddsportal")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_betclic",
                new=AsyncMock(return_value=_incomplete_result("betclic")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_unibet",
                new=AsyncMock(return_value=_incomplete_result("unibet")),
            ),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_betclic_when_no_url(self):
        poll_state = _make_poll_state(bc_url=None, ub_url=None)

        with patch(
            "app.ingestion.market_scrape_chain._scrape_oddsportal",
            new=AsyncMock(return_value=_incomplete_result("oddsportal")),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_unibet_when_no_url(self):
        poll_state = _make_poll_state(ub_url=None)

        with (
            patch(
                "app.ingestion.market_scrape_chain._scrape_oddsportal",
                new=AsyncMock(return_value=_incomplete_result("oddsportal")),
            ),
            patch(
                "app.ingestion.market_scrape_chain._scrape_betclic",
                new=AsyncMock(return_value=_incomplete_result("betclic")),
            ),
        ):
            result = await run_scrape_chain(
                poll_state, browser=MagicMock(), http_client=MagicMock()
            )

        assert result is None
