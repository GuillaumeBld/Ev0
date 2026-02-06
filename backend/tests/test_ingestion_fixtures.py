"""Tests for fixture ingestion from FBref.

RED phase: Write failing tests first.
"""

import pytest
from datetime import datetime, date
from unittest.mock import Mock, patch

from app.ingestion.fixtures import (
    FBrefFixtureParser,
    fetch_league_fixtures,
    normalize_team_name,
    generate_fixture_id,
)


class TestNormalizeTeamName:
    """Tests for team name normalization."""

    def test_removes_accents(self):
        assert normalize_team_name("Paris Saint-Germain") == "paris-saint-germain"
        assert normalize_team_name("Olympique Lyonnais") == "olympique-lyonnais"

    def test_handles_fc_suffix(self):
        assert normalize_team_name("Liverpool FC") == "liverpool"
        assert normalize_team_name("FC Barcelona") == "barcelona"

    def test_handles_special_chars(self):
        assert normalize_team_name("Monaco AS") == "monaco"
        assert normalize_team_name("Athletic Club") == "athletic-club"

    def test_lowercase(self):
        assert normalize_team_name("MANCHESTER UNITED") == "manchester-united"


class TestGenerateFixtureId:
    """Tests for stable fixture ID generation."""

    def test_generates_consistent_id(self):
        id1 = generate_fixture_id("2024-01-15", "paris-saint-germain", "olympique-lyon")
        id2 = generate_fixture_id("2024-01-15", "paris-saint-germain", "olympique-lyon")
        assert id1 == id2

    def test_different_matches_different_ids(self):
        id1 = generate_fixture_id("2024-01-15", "psg", "lyon")
        id2 = generate_fixture_id("2024-01-15", "psg", "marseille")
        assert id1 != id2

    def test_id_format(self):
        fixture_id = generate_fixture_id("2024-01-15", "psg", "lyon")
        assert isinstance(fixture_id, str)
        assert len(fixture_id) > 0


class TestFBrefFixtureParser:
    """Tests for FBref HTML parsing."""

    @pytest.fixture
    def sample_html(self):
        """Sample FBref fixtures HTML."""
        return """
        <table id="sched_2024-2025_1_1">
            <tbody>
                <tr>
                    <td data-stat="date">2024-08-16</td>
                    <td data-stat="time">21:00</td>
                    <td data-stat="home_team"><a href="/en/squads/123">Paris S-G</a></td>
                    <td data-stat="away_team"><a href="/en/squads/456">Le Havre</a></td>
                    <td data-stat="score">4–1</td>
                </tr>
                <tr>
                    <td data-stat="date">2024-08-17</td>
                    <td data-stat="time">17:00</td>
                    <td data-stat="home_team"><a href="/en/squads/789">Monaco</a></td>
                    <td data-stat="away_team"><a href="/en/squads/012">Saint-Étienne</a></td>
                    <td data-stat="score">1–0</td>
                </tr>
            </tbody>
        </table>
        """

    def test_parses_fixtures(self, sample_html):
        parser = FBrefFixtureParser()
        fixtures = parser.parse(sample_html)
        
        assert len(fixtures) == 2
        assert fixtures[0]["home_team"] == "Paris S-G"
        assert fixtures[0]["away_team"] == "Le Havre"

    def test_parses_date_correctly(self, sample_html):
        parser = FBrefFixtureParser()
        fixtures = parser.parse(sample_html)
        
        assert fixtures[0]["date"] == "2024-08-16"
        assert fixtures[0]["time"] == "21:00"

    def test_parses_score_when_available(self, sample_html):
        parser = FBrefFixtureParser()
        fixtures = parser.parse(sample_html)
        
        assert fixtures[0]["home_score"] == 4
        assert fixtures[0]["away_score"] == 1

    def test_handles_no_score(self):
        html = """
        <table id="sched_2024-2025_1_1">
            <tbody>
                <tr>
                    <td data-stat="date">2024-12-15</td>
                    <td data-stat="time">21:00</td>
                    <td data-stat="home_team"><a>PSG</a></td>
                    <td data-stat="away_team"><a>Lyon</a></td>
                    <td data-stat="score"></td>
                </tr>
            </tbody>
        </table>
        """
        parser = FBrefFixtureParser()
        fixtures = parser.parse(html)
        
        assert fixtures[0]["home_score"] is None
        assert fixtures[0]["away_score"] is None


class TestFetchLeagueFixtures:
    """Tests for fetching fixtures from FBref."""

    @patch("app.ingestion.fixtures.httpx.get")
    def test_fetches_ligue1(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="<html>...</html>"
        )
        
        # Should not raise
        fetch_league_fixtures("ligue1", "2024-2025")
        
        # Verify correct URL called
        call_url = mock_get.call_args[0][0]
        assert "Ligue-1" in call_url or "ligue-1" in call_url.lower()

    @patch("app.ingestion.fixtures.httpx.get")
    def test_fetches_premier_league(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="<html>...</html>"
        )
        
        fetch_league_fixtures("premier_league", "2024-2025")
        
        call_url = mock_get.call_args[0][0]
        assert "Premier-League" in call_url

    @patch("app.ingestion.fixtures.httpx.get")
    def test_raises_on_http_error(self, mock_get):
        mock_get.return_value = Mock(status_code=404)
        
        with pytest.raises(Exception):
            fetch_league_fixtures("ligue1", "2024-2025")

    @patch("app.ingestion.fixtures.httpx.get")
    def test_respects_rate_limit(self, mock_get):
        """FBref requires 3+ seconds between requests."""
        mock_get.return_value = Mock(status_code=200, text="<html></html>")
        
        import time
        start = time.time()
        fetch_league_fixtures("ligue1", "2024-2025")
        fetch_league_fixtures("premier_league", "2024-2025")
        elapsed = time.time() - start
        
        # Should have waited at least 3 seconds between calls
        assert elapsed >= 3.0
