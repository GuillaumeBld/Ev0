"""UEFA Super Cup wired end-to-end: bzz constants → scheduler → PMU/Kambi.

Guards the one-off Super Cup integration (PSG vs Aston Villa, 2026-08-12,
bzzoiro league id 90). PMU/Kambi is the source that exposes both buteur and
assist markets for this fixture; Betclic/Unibet skip unknown leagues cleanly.
"""
from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_API_IDS,
    INTERNATIONAL_LEAGUE_INTERNAL_IDS,
)
from app.ingestion.odds_scheduler import _league_key
from app.ingestion.pmu_scraper import _KAMBI_LEAGUE_MAP, _league_from_event


def test_super_cup_in_bzz_constants():
    assert INTERNATIONAL_LEAGUE_INTERNAL_IDS["uefa_super_cup"] == 90
    assert INTERNATIONAL_LEAGUE_API_IDS["uefa_super_cup"] == 90


def test_scheduler_recognizes_super_cup_league():
    # fixture.league value set by sync (key form) and a human spelling both map.
    assert _league_key("uefa_super_cup") == "uefa_super_cup"
    assert _league_key("UEFA Super Cup") == "uefa_super_cup"


def test_pmu_kambi_maps_super_cup_group():
    assert _KAMBI_LEAGUE_MAP["UEFA Super Cup"] == "uefa_super_cup"


def test_pmu_league_from_event_path():
    # Kambi event path for PSG vs Aston Villa: ['Football', 'UEFA Super Cup']
    event = {"path": [{"englishName": "Football"}, {"englishName": "UEFA Super Cup"}]}
    assert _league_from_event(event) == "uefa_super_cup"
