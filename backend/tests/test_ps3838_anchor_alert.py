from datetime import UTC, datetime, timedelta

from app.worker import _unanchored_alert_lines

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def test_fixture_within_seven_days_is_reported():
    rows = [("Atlético Madrid - Málaga CF", NOW + timedelta(days=3))]
    lines = _unanchored_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "Atlético Madrid" in lines[0]


def test_fixture_beyond_seven_days_is_silent():
    rows = [("Lille - Paris Saint-Germain", NOW + timedelta(days=9))]
    assert _unanchored_alert_lines(rows, NOW) == []


def test_boundary_at_seven_days():
    assert _unanchored_alert_lines([("A - B", NOW + timedelta(days=7))], NOW) == []
    assert len(_unanchored_alert_lines([("A - B", NOW + timedelta(days=6, hours=23))], NOW)) == 1


def test_past_fixture_is_ignored():
    assert _unanchored_alert_lines([("A - B", NOW - timedelta(hours=1))], NOW) == []


def test_empty_input_is_silent():
    assert _unanchored_alert_lines([], NOW) == []
