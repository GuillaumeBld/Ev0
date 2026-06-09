# backend/tests/ingestion/test_sync_wc_outrights.py
"""Tests unitaires pour sync_wc_outrights.py."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.wc2026.sync_wc_outrights import (
    _classify_kambi_outright,
    _kambi_odds,
    scrape_pmu_wc_outrights,
)


def test_classify_kambi_outright_winner():
    assert _classify_kambi_outright("Winner", "World Cup Winner") == "winner"


def test_classify_kambi_outright_top4():
    assert _classify_kambi_outright("Top 4", "To Reach Semi Final") == "top4"
    assert _classify_kambi_outright("Semi Final", "") == "top4"


def test_classify_kambi_outright_top8():
    assert _classify_kambi_outright("Quarter Final", "") == "top8"
    assert _classify_kambi_outright("Top 8", "") == "top8"


def test_classify_kambi_outright_top2():
    assert _classify_kambi_outright("Final", "To Reach the Final") == "top2"
    assert _classify_kambi_outright("Top 2", "") == "top2"


def test_classify_kambi_outright_group_stage():
    assert _classify_kambi_outright("Group Stage", "To Qualify") == "group_stage"


def test_classify_kambi_outright_top_scorer():
    assert _classify_kambi_outright("Top Goalscorer", "Top Scorer") == "top_scorer"
    assert _classify_kambi_outright("Top Scorer", "") == "top_scorer"


def test_classify_kambi_outright_top_assister():
    assert _classify_kambi_outright("Top Assister", "") == "top_assister"
    assert _classify_kambi_outright("Most Assists", "") == "top_assister"


def test_classify_kambi_outright_unknown():
    assert _classify_kambi_outright("Fair Play Award", "") is None


def test_kambi_odds_valid():
    assert _kambi_odds(3500) == pytest.approx(3.5)
    assert _kambi_odds(1010) == pytest.approx(1.01)


def test_kambi_odds_invalid():
    assert _kambi_odds(None) is None
    assert _kambi_odds(1000) is None  # ≤ 1000 → cote ≤ 1.00 → invalide
    assert _kambi_odds(0) is None


@pytest.mark.asyncio
async def test_scrape_pmu_wc_outrights_empty_on_http_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("connection refused")
        result = await scrape_pmu_wc_outrights()
    assert result == []


@pytest.mark.asyncio
async def test_scrape_pmu_wc_outrights_parses_winner():
    fake_response = {
        "events": [
            {
                "event": {
                    "id": 1001,
                    "englishName": "World Cup Winner",
                    "path": [
                        {"englishName": "Football"},
                        {"englishName": "World Cup 2026"},
                    ],
                    "betOffers": [
                        {
                            "betOfferType": {"englishName": "Winner"},
                            "criterion": {"englishLabel": "Winner"},
                            "outcomes": [
                                {"label": "France", "englishLabel": "France", "odds": 4000},
                                {"label": "Brésil", "englishLabel": "Brazil", "odds": 5000},
                            ],
                        }
                    ],
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = fake_response

    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        result = await scrape_pmu_wc_outrights()

    assert len(result) == 2
    france = next(r for r in result if r["nation"] == "France")
    assert france["market_type"] == "winner"
    assert france["odds"] == pytest.approx(4.0)
    assert france["bookmaker"] == "pmu"


from app.ingestion.wc2026.sync_wc_outrights import (
    scrape_unibet_wc_outrights,
    scrape_betclic_wc_outrights,
    _parse_lvs_price,
)


def test_parse_lvs_price_valid():
    assert _parse_lvs_price("4,50") == pytest.approx(4.5)
    assert _parse_lvs_price("2") == pytest.approx(2.0)


def test_parse_lvs_price_invalid():
    assert _parse_lvs_price(None) is None
    assert _parse_lvs_price("null") is None
    assert _parse_lvs_price("0,90") is None  # < 1.01


@pytest.mark.asyncio
async def test_scrape_unibet_wc_outrights_empty_on_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("timeout")
        result = await scrape_unibet_wc_outrights()
    assert result == []


@pytest.mark.asyncio
async def test_scrape_unibet_wc_outrights_parses_winner():
    # LVS outright response: flat dict with m{id} market entries and o{id} outcomes
    fake_token_resp = MagicMock()
    fake_token_resp.raise_for_status = MagicMock()
    fake_token_resp.json.return_value = {"hsToken": "test-token"}

    fake_events_resp = MagicMock()
    fake_events_resp.raise_for_status = MagicMock()
    fake_events_resp.json.return_value = {
        "items": {
            "e1001": {
                "a": "Vainqueur CDM 2026",
                "b": "",
                "start": "2607230000",
            },
        }
    }

    fake_ff_resp = MagicMock()
    fake_ff_resp.raise_for_status = MagicMock()
    fake_ff_resp.json.return_value = {
        "items": {
            "m1": {"markettypeId": 14, "n": "Vainqueur"},
            "o1": {"marketId": "m1", "a": "France", "pr": "4,00"},
            "o2": {"marketId": "m1", "a": "Brésil", "pr": "5,00"},
        }
    }

    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = [fake_token_resp, fake_events_resp, fake_ff_resp]
        result = await scrape_unibet_wc_outrights()

    assert any(r["nation"] == "France" and r["market_type"] == "winner" for r in result)


@pytest.mark.asyncio
async def test_scrape_betclic_wc_outrights_empty_on_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("timeout")
        result = await scrape_betclic_wc_outrights()
    assert result == []
