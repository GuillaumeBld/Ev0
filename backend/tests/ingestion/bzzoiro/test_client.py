from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.ingestion.bzzoiro.client import BzzoiroClient


@pytest.mark.asyncio
async def test_get_page_returns_results():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "count": 2, "next": None,
        "results": [{"id": 1}, {"id": 2}],
    })
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        client = BzzoiroClient(api_key="test-key")
        async with client:
            results = await client.get_all("/api/players/")
    assert results == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_get_all_follows_pagination():
    responses = [
        {"count": 3, "next": "http://sports.bzzoiro.com/api/players/?page=2", "results": [{"id": 1}]},
        {"count": 3, "next": None, "results": [{"id": 2}, {"id": 3}]},
    ]
    call_count = 0
    async def fake_get(url, **kwargs):
        nonlocal call_count
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=responses[call_count])
        call_count += 1
        return r
    with patch("httpx.AsyncClient.get", side_effect=fake_get):
        client = BzzoiroClient(api_key="test-key")
        async with client:
            results = await client.get_all("/api/players/")
    assert len(results) == 3
    assert call_count == 2
