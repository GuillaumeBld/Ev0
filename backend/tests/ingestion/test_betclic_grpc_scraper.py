import httpx
import pytest

from app.ingestion.betclic_grpc_scraper import (
    _GRPC_HEADERS,
    _PAGE_HEADERS,
    BetclicGrpcScraper,
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


@pytest.mark.asyncio
async def test_fetch_match_odds_returns_selections():
    """fetch_match_odds returns non-empty list for a known match."""
    async with (
        httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client,
        httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as page_client,
    ):
        scraper = BetclicGrpcScraper(page_client)
        scraper._grpc_client = grpc_client
        matches = await scraper.fetch_competition_matches("ligue_1")
        assert matches, "Need at least one L1 match to test"
        first = matches[0]
        sels = await scraper.fetch_match_odds(
            first["match_id"], first["home_team"], first["away_team"], first["league"]
        )
    assert isinstance(sels, list)
    assert len(sels) > 10, f"Expected 20+ player selections per match, got {len(sels)}"
    types = {s.market_type for s in sels}
    assert "goalscorer" in types


@pytest.mark.asyncio
async def test_scrape_league_returns_match_odds():
    """scrape_betclic_leagues returns MatchOdds with player selections for ligue_1."""
    from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
    results = await scrape_betclic_leagues(["ligue_1"])
    assert isinstance(results, list)
    assert len(results) > 0
    mo = results[0]
    assert mo.home_team and mo.away_team
    assert len(mo.selections) > 10
