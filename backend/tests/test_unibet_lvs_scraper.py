# backend/tests/test_unibet_lvs_scraper.py
"""Tests for Unibet LVS scraper (new post-PSEL-merger site)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.unibet_lvs_scraper import (
    UnibetLVSScraper,
    _parse_price,
    _parse_start,
    scrape_all_unibet,
)


class TestParsePrice:
    def test_french_decimal(self):
        assert _parse_price("3,05") == pytest.approx(3.05)

    def test_integer_string(self):
        assert _parse_price("2") == pytest.approx(2.0)

    def test_none_returns_none(self):
        assert _parse_price(None) is None

    def test_null_string_returns_none(self):
        assert _parse_price("null") is None

    def test_below_one_returns_none(self):
        assert _parse_price("0,95") is None

    def test_above_max_returns_none(self):
        assert _parse_price("1001") is None

    def test_empty_string_returns_none(self):
        assert _parse_price("") is None


class TestParseStart:
    def test_valid_format(self):
        # "2604051930" → 2026-04-05 19:30 UTC
        dt = _parse_start("2604051930")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 5
        assert dt.hour == 19
        assert dt.minute == 30

    def test_invalid_returns_none(self):
        assert _parse_start("") is None
        assert _parse_start(None) is None
        assert _parse_start("abc") is None

    def test_non_numeric_ten_chars_returns_none(self):
        # 10 chars but non-numeric → ValueError branch
        assert _parse_start("260405193X") is None

    def test_short_string_returns_none(self):
        assert _parse_start("26040519") is None  # 8 chars


class TestParseMatchItems:
    """Tests for _parse_match_items: extracts MatchOdds from LVS /ff/ response."""

    SAMPLE_ITEMS = {
        "e100": {"a": "PSG", "b": "Marseille", "start": "2604051930"},
        # markettypeId=31 : buteur anytime
        "m200": {"parent": "e100", "markettypeId": 31, "desc": "Buteur"},
        # markettypeId=100002524 : passeur décisif
        "m201": {"parent": "e100", "markettypeId": 100002524, "desc": "Passeur décisif"},
        # markettypeId=4 : 1er buteur (doit être dédupliqué au profit de l'anytime)
        "m202": {"parent": "e100", "markettypeId": 4, "desc": "1er Buteur"},
        # Outcomes buteur anytime
        "o300": {"parent": "m200", "desc": "Mbappé", "price": "2,75"},
        "o301": {"parent": "m200", "desc": "Neymar", "price": "3,50"},
        # Outcome passeur décisif
        "o302": {"parent": "m201", "desc": "Mbappé", "price": "3,20"},
        # Outcome 1er buteur pour Mbappé — doit être ignoré au profit de o300 (anytime)
        "o303": {"parent": "m202", "desc": "Mbappé", "price": "4,00"},
        # Outcome suspendu (price=None) — doit être ignoré
        "o304": {"parent": "m200", "desc": "Giroud", "price": None},
    }

    def _scraper(self):
        return UnibetLVSScraper(httpx.AsyncClient())

    def test_extracts_goalscorer_selections(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert mo is not None
        goal = [s for s in mo.selections if s.market_type == "goalscorer"]
        names = [s.player_name for s in goal]
        assert "Mbappé" in names
        assert "Neymar" in names

    def test_extracts_assist_selections(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assist = [s for s in mo.selections if s.market_type == "assist"]
        assert len(assist) == 1
        assert assist[0].player_name == "Mbappé"
        assert assist[0].odds == pytest.approx(3.20)

    def test_deduplicates_anytime_over_first_scorer(self):
        """Anytime (markettypeId=31) bat toujours First Scorer (markettypeId=4)."""
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        mbappe_goals = [
            s for s in mo.selections
            if s.market_type == "goalscorer" and s.player_name == "Mbappé"
        ]
        # Un seul Mbappé goalscorer, avec les cotes anytime (2.75) pas first scorer (4.00)
        assert len(mbappe_goals) == 1
        assert mbappe_goals[0].odds == pytest.approx(2.75)

    def test_skips_null_price(self):
        """Outcomes avec price=None (marché suspendu) sont ignorés."""
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        # Giroud a price=None → ne doit pas apparaître
        goal_names = [s.player_name for s in mo.selections if s.market_type == "goalscorer"]
        assert "Giroud" not in goal_names
        # Exactement 2 buteurs (Mbappé + Neymar)
        assert len([s for s in mo.selections if s.market_type == "goalscorer"]) == 2

    def test_correct_teams_and_league(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert mo.home_team == "PSG"
        assert mo.away_team == "Marseille"
        assert mo.league == "ligue_1"

    def test_returns_none_when_no_event(self):
        assert self._scraper()._parse_match_items({}, "ligue_1") is None

    def test_returns_none_when_no_target_markets(self):
        items = {
            "e100": {"a": "PSG", "b": "Marseille", "start": "2604051930"},
            # Seul un marché 1X2 (non ciblé)
            "m200": {"parent": "e100", "markettypeId": 1, "desc": "1X2"},
            "o300": {"parent": "m200", "desc": "PSG", "price": "1,80"},
        }
        assert self._scraper()._parse_match_items(items, "ligue_1") is None

    def test_bookmaker_is_unibet(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert all(s.bookmaker == "unibet" for s in mo.selections)


class TestFetchEventIds:
    """Tests for fetch_event_ids: appel /next/ et filtrage des matchs passés."""

    SAMPLE_NEXT_RESPONSE = {
        "items": {
            "e1001": {"a": "PSG", "b": "Lyon", "start": "2612311930"},      # futur
            "e1002": {"a": "Monaco", "b": "Nice", "start": "2001011200"},   # passé (2020)
            "e1003": {"a": "Lens", "b": "Lille", "start": "2612251500"},    # futur
            "m9999": {"parent": "e1001", "markettypeId": 31},               # marché ignoré
        }
    }

    @pytest.mark.asyncio
    async def test_returns_only_future_events(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self.SAMPLE_NEXT_RESPONSE

        with patch.object(scraper._client, "get", new=AsyncMock(return_value=mock_response)):
            events = await scraper.fetch_event_ids("ligue_1")

        # Monaco vs Nice (passé) doit être exclu
        event_ids = [e[0] for e in events]
        assert 1001 in event_ids
        assert 1003 in event_ids
        assert 1002 not in event_ids

    @pytest.mark.asyncio
    async def test_ignores_non_event_items(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self.SAMPLE_NEXT_RESPONSE

        with patch.object(scraper._client, "get", new=AsyncMock(return_value=mock_response)):
            events = await scraper.fetch_event_ids("ligue_1")

        # m9999 ne doit pas apparaître comme événement
        event_ids = [e[0] for e in events]
        assert 9999 not in event_ids

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        with patch.object(
            scraper._client, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            events = await scraper.fetch_event_ids("ligue_1")

        assert events == []

    @pytest.mark.asyncio
    async def test_unknown_league_returns_empty(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        events = await scraper.fetch_event_ids("ligue_inconnue")
        assert events == []


class TestScrapeAllUnibet:
    """Tests for scrape_all_unibet: token acquisition + multi-league aggregation."""

    @pytest.mark.asyncio
    async def test_auth_failure_returns_empty(self):
        """If _get_token raises, scrape_all_unibet returns []."""
        with patch.object(
            UnibetLVSScraper,
            "_get_token",
            new=AsyncMock(side_effect=httpx.ConnectError("refused")),
        ):
            result = await scrape_all_unibet(["ligue_1"])

        assert result == []

    @pytest.mark.asyncio
    async def test_aggregates_results(self):
        """Results from each league are concatenated into a single list."""
        ligue_1_matches = [MagicMock(), MagicMock()]
        premier_league_matches = [MagicMock(), MagicMock(), MagicMock()]

        async def fake_scrape_league(self, league: str):
            if league == "ligue_1":
                return ligue_1_matches
            if league == "premier_league":
                return premier_league_matches
            return []

        with patch.object(UnibetLVSScraper, "_get_token", new=AsyncMock(return_value="tok")):
            with patch.object(UnibetLVSScraper, "scrape_league", new=fake_scrape_league):
                result = await scrape_all_unibet(["ligue_1", "premier_league"])

        assert len(result) == len(ligue_1_matches) + len(premier_league_matches)
