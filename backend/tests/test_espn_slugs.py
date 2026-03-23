"""Tests for ESPN client configuration."""
from app.ingestion.espn_client import ESPN_LEAGUE_SLUGS


def test_espn_covers_all_big5_and_cl():
    required = {"ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"}
    missing = required - set(ESPN_LEAGUE_SLUGS.keys())
    assert not missing, f"Missing ESPN slugs: {missing}"


def test_espn_slugs_are_correct():
    assert ESPN_LEAGUE_SLUGS["bundesliga"] == "ger.1"
    assert ESPN_LEAGUE_SLUGS["la_liga"] == "esp.1"
    assert ESPN_LEAGUE_SLUGS["serie_a"] == "ita.1"
