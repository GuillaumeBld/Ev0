"""Tests for odds ingestion from The Odds API.

RED phase: Write failing tests first.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

from app.ingestion.odds import (
    OddsAPIClient,
    normalize_selection_name,
    remove_margin,
    OddsSnapshot,
)


class TestNormalizeSelectionName:
    """Tests for selection name normalization."""

    def test_normalizes_player_names(self):
        assert normalize_selection_name("Kylian Mbappe") == "kylian-mbappe"
        assert normalize_selection_name("K. Mbappe") == "k-mbappe"

    def test_handles_special_formats(self):
        # Different bookmakers format names differently
        assert normalize_selection_name("MBAPPE K.") == "mbappe-k"
        assert normalize_selection_name("Mbappé, Kylian") == "mbappe-kylian"


class TestRemoveMargin:
    """Tests for bookmaker margin removal."""

    def test_proportional_removal_two_way(self):
        # Two selections with 5% margin
        # Implied: 52.6% + 52.6% = 105.2%
        odds = [1.9, 1.9]
        fair = remove_margin(odds)
        
        # Fair odds should imply ~50% each
        total_prob = sum(1/o for o in fair)
        assert abs(total_prob - 1.0) < 0.01

    def test_proportional_removal_multi_selection(self):
        # Player props market with many selections
        odds = [3.0, 4.0, 5.0, 8.0, 10.0]
        fair = remove_margin(odds)
        
        total_prob = sum(1/o for o in fair)
        assert abs(total_prob - 1.0) < 0.01

    def test_preserves_relative_probabilities(self):
        odds = [2.0, 4.0]  # 50% vs 25% implied (before margin)
        fair = remove_margin(odds)
        
        # Ratio should be preserved
        ratio = (1/fair[0]) / (1/fair[1])
        original_ratio = (1/2.0) / (1/4.0)
        assert abs(ratio - original_ratio) < 0.1


class TestOddsSnapshot:
    """Tests for OddsSnapshot dataclass."""

    def test_creates_snapshot(self):
        snap = OddsSnapshot(
            fixture_id="abc123",
            player_name="Kylian Mbappe",
            market_type="goalscorer",
            bookmaker="betclic",
            odds=2.50,
            snapshot_utc=datetime.utcnow(),
        )
        
        assert snap.fixture_id == "abc123"
        assert snap.implied_probability == pytest.approx(0.4, rel=0.01)

    def test_implied_probability_calculation(self):
        snap = OddsSnapshot(
            fixture_id="x",
            player_name="Test",
            market_type="goalscorer",
            bookmaker="test",
            odds=4.0,
            snapshot_utc=datetime.utcnow(),
        )
        
        assert snap.implied_probability == 0.25


class TestOddsAPIClient:
    """Tests for The Odds API client."""

    @pytest.fixture
    def client(self):
        return OddsAPIClient(api_key="test-key")

    @pytest.fixture
    def sample_response(self):
        return {
            "id": "abc123",
            "sport_key": "soccer_france_ligue_one",
            "commence_time": "2024-02-01T20:00:00Z",
            "home_team": "Paris Saint Germain",
            "away_team": "Lyon",
            "bookmakers": [
                {
                    "key": "betclic",
                    "title": "Betclic",
                    "markets": [
                        {
                            "key": "player_goal_scorer",
                            "outcomes": [
                                {"name": "Kylian Mbappe", "price": 2.25},
                                {"name": "Ousmane Dembele", "price": 3.50},
                            ]
                        }
                    ]
                }
            ]
        }

    @patch("app.ingestion.odds.httpx.AsyncClient.get")
    async def test_fetches_events(self, mock_get, client):
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=[{"id": "123", "home_team": "PSG"}])
        )
        
        events = await client.get_events("soccer_france_ligue_one")
        assert len(events) == 1

    @patch("app.ingestion.odds.httpx.AsyncClient.get")
    async def test_fetches_player_props(self, mock_get, client, sample_response):
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=sample_response)
        )
        
        odds = await client.get_player_props("abc123", "player_goal_scorer")
        
        assert len(odds) > 0
        assert odds[0]["player_name"] == "Kylian Mbappe"
        assert odds[0]["bookmaker"] == "betclic"

    @patch("app.ingestion.odds.httpx.AsyncClient.get")
    async def test_handles_api_error(self, mock_get, client):
        mock_get.return_value = Mock(status_code=401)
        
        with pytest.raises(Exception):
            await client.get_events("soccer_france_ligue_one")

    def test_sport_key_mapping(self, client):
        assert client.get_sport_key("ligue1") == "soccer_france_ligue_one"
        assert client.get_sport_key("premier_league") == "soccer_epl"


class TestOddsIngestion:
    """Integration tests for odds ingestion workflow."""

    @patch("app.ingestion.odds.OddsAPIClient")
    async def test_ingests_goalscorer_odds(self, mock_client_class):
        """Full workflow: fetch events -> fetch odds -> store."""
        from app.ingestion.odds import ingest_odds_for_league
        
        mock_client = AsyncMock()
        mock_client.get_events.return_value = [
            {"id": "match1", "home_team": "PSG", "away_team": "Lyon"}
        ]
        mock_client.get_player_props.return_value = [
            {"player_name": "Mbappe", "odds": 2.25, "bookmaker": "betclic"}
        ]
        mock_client_class.return_value = mock_client
        
        snapshots = await ingest_odds_for_league("ligue1", "goalscorer")
        
        assert len(snapshots) > 0
