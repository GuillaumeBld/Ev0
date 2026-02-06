"""Tests for player stats ingestion from FBref.

RED phase: Failing tests for goalscorer and assist stats.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from app.ingestion.player_stats import (
    FBrefPlayerStatsParser,
    fetch_player_stats,
    normalize_player_name,
    calculate_per_90,
    calculate_form_factor,
)


class TestNormalizePlayerName:
    """Tests for player name normalization."""

    def test_removes_accents(self):
        assert normalize_player_name("Kylian Mbappé") == "kylian-mbappe"
        assert normalize_player_name("Ángel Di María") == "angel-di-maria"

    def test_handles_special_chars(self):
        assert normalize_player_name("Son Heung-min") == "son-heung-min"
        assert normalize_player_name("Bruno Fernandes") == "bruno-fernandes"

    def test_lowercase(self):
        assert normalize_player_name("ERLING HAALAND") == "erling-haaland"


class TestCalculatePer90:
    """Tests for per-90 stats calculation."""

    def test_basic_calculation(self):
        # 5 goals in 450 minutes = 1.0 per 90
        assert calculate_per_90(5, 450) == 1.0

    def test_handles_zero_minutes(self):
        assert calculate_per_90(5, 0) == 0.0

    def test_rounds_correctly(self):
        # 3 goals in 270 minutes = 1.0 per 90
        result = calculate_per_90(3, 270)
        assert abs(result - 1.0) < 0.001


class TestCalculateFormFactor:
    """Tests for exponential decay form factor."""

    def test_recent_form_weighted_higher(self):
        # Last 5 matches: recent has higher weight
        recent_xg = [0.8, 0.6, 0.5, 0.4, 0.3]  # Most recent first
        factor = calculate_form_factor(recent_xg, decay_lambda=0.025)
        
        # Should be influenced more by recent (0.8) than old (0.3)
        assert factor > 0.5

    def test_empty_list_returns_one(self):
        assert calculate_form_factor([]) == 1.0

    def test_consistent_form_near_one(self):
        # All 0.5 xG = average, factor should be ~1.0
        consistent = [0.5, 0.5, 0.5, 0.5, 0.5]
        factor = calculate_form_factor(consistent, baseline=0.5)
        assert 0.9 <= factor <= 1.1


class TestFBrefPlayerStatsParser:
    """Tests for FBref player stats HTML parsing."""

    @pytest.fixture
    def sample_shooting_html(self):
        """Sample FBref shooting stats table."""
        return """
        <table id="stats_shooting">
            <tbody>
                <tr>
                    <td data-stat="player"><a href="/en/players/abc123">Kylian Mbappé</a></td>
                    <td data-stat="minutes">1800</td>
                    <td data-stat="goals">15</td>
                    <td data-stat="shots">65</td>
                    <td data-stat="shots_on_target">35</td>
                    <td data-stat="xg">12.5</td>
                    <td data-stat="npxg">11.2</td>
                </tr>
                <tr>
                    <td data-stat="player"><a href="/en/players/def456">Ousmane Dembélé</a></td>
                    <td data-stat="minutes">1500</td>
                    <td data-stat="goals">8</td>
                    <td data-stat="shots">45</td>
                    <td data-stat="shots_on_target">20</td>
                    <td data-stat="xg">6.8</td>
                    <td data-stat="npxg">6.5</td>
                </tr>
            </tbody>
        </table>
        """

    @pytest.fixture
    def sample_passing_html(self):
        """Sample FBref passing stats table."""
        return """
        <table id="stats_passing">
            <tbody>
                <tr>
                    <td data-stat="player"><a href="/en/players/abc123">Kylian Mbappé</a></td>
                    <td data-stat="minutes">1800</td>
                    <td data-stat="assists">8</td>
                    <td data-stat="xa">6.5</td>
                    <td data-stat="key_passes">42</td>
                    <td data-stat="passes_into_penalty_area">28</td>
                    <td data-stat="progressive_passes">55</td>
                    <td data-stat="crosses">12</td>
                </tr>
            </tbody>
        </table>
        """

    def test_parses_shooting_stats(self, sample_shooting_html):
        parser = FBrefPlayerStatsParser()
        stats = parser.parse_shooting(sample_shooting_html)
        
        assert len(stats) == 2
        mbappe = stats[0]
        assert mbappe["player_name"] == "Kylian Mbappé"
        assert mbappe["minutes"] == 1800
        assert mbappe["goals"] == 15
        assert mbappe["xg"] == 12.5
        assert mbappe["npxg"] == 11.2

    def test_parses_passing_stats(self, sample_passing_html):
        parser = FBrefPlayerStatsParser()
        stats = parser.parse_passing(sample_passing_html)
        
        assert len(stats) == 1
        mbappe = stats[0]
        assert mbappe["assists"] == 8
        assert mbappe["xa"] == 6.5
        assert mbappe["key_passes"] == 42

    def test_calculates_per90_automatically(self, sample_shooting_html):
        parser = FBrefPlayerStatsParser()
        stats = parser.parse_shooting(sample_shooting_html)
        
        mbappe = stats[0]
        # 12.5 xG in 1800 minutes = 0.625 per 90
        assert "xg_per_90" in mbappe
        assert abs(mbappe["xg_per_90"] - 0.625) < 0.001


class TestFetchPlayerStats:
    """Tests for fetching player stats from FBref."""

    @patch("app.ingestion.player_stats.httpx.get")
    def test_fetches_team_stats(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="<html><table id='stats_shooting'><tbody></tbody></table></html>"
        )
        
        fetch_player_stats("paris-saint-germain", "2024-2025")
        
        # Should call FBref
        assert mock_get.called

    @patch("app.ingestion.player_stats.httpx.get")
    def test_combines_shooting_and_passing(self, mock_get):
        """Stats should merge shooting and passing data per player."""
        mock_get.return_value = Mock(
            status_code=200,
            text="<html></html>"
        )
        
        # This tests the merge logic
        # Implementation should combine both stat types
        stats = fetch_player_stats("liverpool", "2024-2025")
        # After merge, each player should have both xg and xa
