import struct

import httpx
import pytest

from app.ingestion.betclic_grpc_scraper import (
    BetclicGrpcScraper,
    _PAGE_HEADERS,
    decode_bytes_field,
    decode_odds_float64,
    encode_grpc_web_request,
)


def test_encode_grpc_web_request_known_match():
    """Reproduce the captured Lyon-Lens request body exactly.
    Captured: match_id=1025500931850240, lang='fr'
    Expected hex: 000000000d0880808ad68096e90112026672
    """
    result = encode_grpc_web_request(1025500931850240, "fr")
    assert result.hex() == "000000000d0880808ad68096e90112026672"


def test_decode_odds_float64_known_value():
    """field12=4613419904283925545 → 2.77 (verified from live Betclic response)."""
    assert decode_odds_float64(4613419904283925545) == 2.77


def test_decode_odds_float64_out_of_range():
    assert decode_odds_float64(0) is None
    assert decode_odds_float64(999999999999999999) is None


def test_decode_bytes_field_utf8():
    assert decode_bytes_field("b'Buteur (tps r\\xc3\\xa9g.)'") == "Buteur (tps rég.)"


def test_decode_bytes_field_plain_string():
    assert decode_bytes_field("Lyon") == "Lyon"


@pytest.mark.asyncio
async def test_fetch_matches_returns_list():
    """fetch_competition_matches returns a non-empty list for ligue_1."""
    async with httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as client:
        scraper = BetclicGrpcScraper(client)
        matches = await scraper.fetch_competition_matches("ligue_1")
    assert isinstance(matches, list)
    assert len(matches) > 0
    m = matches[0]
    assert "match_id" in m
    assert "home_team" in m
    assert "away_team" in m
    assert "competition_id" in m
