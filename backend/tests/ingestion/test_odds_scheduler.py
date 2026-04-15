# backend/tests/ingestion/test_odds_scheduler.py
from datetime import datetime, timedelta, timezone
from app.ingestion.odds_scheduler import scrape_interval_seconds, should_scrape


def _ko(minutes_from_now: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)


def test_interval_far_from_ko():
    """More than 6h → 7200s (2h)."""
    assert scrape_interval_seconds(_ko(600)) == 7200


def test_interval_mid_range():
    """2h–6h → 1800s (30min)."""
    assert scrape_interval_seconds(_ko(240)) == 1800


def test_interval_close_to_ko():
    """5min–2h → 120s (2min)."""
    assert scrape_interval_seconds(_ko(30)) == 120


def test_should_not_scrape_within_5min():
    """Less than 5min before KO → stop."""
    assert should_scrape(_ko(3), last_scraped_at=None) is False


def test_should_not_scrape_past_ko():
    """After KO → stop."""
    assert should_scrape(_ko(-10), last_scraped_at=None) is False


def test_should_scrape_when_never_scraped():
    """Never scraped + in window → True."""
    assert should_scrape(_ko(60), last_scraped_at=None) is True


def test_should_not_scrape_when_recent():
    """Scraped 1min ago, interval=120s → False."""
    last = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert should_scrape(_ko(60), last_scraped_at=last) is False


def test_should_scrape_when_overdue():
    """Scraped 3min ago, interval=120s → True."""
    last = datetime.now(timezone.utc) - timedelta(seconds=180)
    assert should_scrape(_ko(60), last_scraped_at=last) is True
