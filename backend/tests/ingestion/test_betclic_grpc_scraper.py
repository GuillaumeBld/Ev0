import httpx
import pytest

from app.ingestion.betclic_grpc_scraper import (
    _GRPC_HEADERS,
    _PAGE_HEADERS,
    BetclicGrpcScraper,
    _classify_market,
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


def test_classify_goalscorer():
    assert _classify_market("Buteur (tps rég.)") == "goalscorer"


def test_classify_assist():
    assert _classify_market("Passeur décisif") == "assist"


def test_classify_h2h():
    assert _classify_market("Résultat du match (tps rég.)") == "h2h"


def test_classify_totals():
    assert _classify_market("Nombre total de buts") == "totals"


def test_classify_btts():
    assert _classify_market("Les 2 équipes marquent") == "btts"


def test_classify_unknown_returns_none():
    assert _classify_market("Paris en avance") is None


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
async def test_fetch_match_odds_returns_match_scrape_result():
    """fetch_match_odds returns MatchScrapeResult with goalscorer selections."""
    from app.ingestion.scrape_result import MatchScrapeResult
    async with (
        httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client,
        httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as page_client,
    ):
        scraper = BetclicGrpcScraper(page_client)
        scraper._grpc_client = grpc_client
        matches = await scraper.fetch_competition_matches("ligue_1")
        assert matches, "Need at least one L1 match to test"
        first = matches[0]
        result = await scraper.fetch_match_odds(
            first["match_id"], first["home_team"], first["away_team"], first["league"]
        )
    assert result is not None
    assert isinstance(result, MatchScrapeResult)
    assert result.bookmaker == "betclic"
    assert len(result.goalscorer) > 10, f"Expected 20+ goalscorer selections, got {len(result.goalscorer)}"


@pytest.mark.asyncio
async def test_scrape_league_returns_match_scrape_results():
    """scrape_betclic_leagues returns MatchScrapeResult for ligue_1."""
    from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
    from app.ingestion.scrape_result import MatchScrapeResult
    results = await scrape_betclic_leagues(["ligue_1"])
    assert isinstance(results, list)
    assert len(results) > 0
    r = results[0]
    assert isinstance(r, MatchScrapeResult)
    assert r.home_team and r.away_team
    assert len(r.goalscorer) > 10
