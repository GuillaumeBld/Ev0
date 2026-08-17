"""Le rapport de sante ne sonne que si un indicateur vire au rouge."""
from datetime import UTC, datetime, timedelta

from app.worker import _health_red_flags

NOW = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def _row(**over):
    base = {
        "last_player_odds": NOW - timedelta(minutes=30),
        "last_match_odds": NOW - timedelta(hours=2),
        "wc_odds_actives": 120,
        "wc_pricing_at": NOW - timedelta(hours=1),
        "recs_24h": 14,
        "recs_pending": 6,
        "backlog_settle": 3,
    }
    base.update(over)
    return base


def test_all_green_returns_no_flags():
    assert _health_red_flags(_row(), NOW) == []


def test_stale_player_odds_is_red():
    flags = _health_red_flags(_row(last_player_odds=NOW - timedelta(hours=25)), NOW)
    assert flags == ["cotes joueurs"]


def test_never_scraped_is_red():
    flags = _health_red_flags(_row(last_match_odds=None), NOW)
    assert flags == ["cotes matchs"]


def test_settle_backlog_over_twenty_is_red():
    assert _health_red_flags(_row(backlog_settle=21), NOW) == ["backlog settlement"]
    assert _health_red_flags(_row(backlog_settle=20), NOW) == []


def test_zero_recos_in_24h_is_red():
    assert _health_red_flags(_row(recs_24h=0), NOW) == ["aucune reco en 24h"]


def test_multiple_flags_accumulate():
    flags = _health_red_flags(
        _row(last_player_odds=None, recs_24h=0, backlog_settle=99), NOW
    )
    assert set(flags) == {"cotes joueurs", "backlog settlement", "aucune reco en 24h"}
