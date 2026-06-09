# backend/tests/ingestion/test_sync_wc_outrights.py
"""Tests unitaires pour sync_wc_outrights.py."""
from unittest.mock import AsyncMock, patch

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
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = AsyncMock()
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
