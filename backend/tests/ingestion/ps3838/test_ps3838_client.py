import json
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.ps3838.client import Ps3838Event, parse_events

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ps3838_events.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def test_parse_returns_events():
    evs = parse_events(_payload())
    assert len(evs) > 50
    assert all(isinstance(e, Ps3838Event) for e in evs)


def test_event_fields_are_typed():
    e = next(e for e in parse_events(_payload()) if e.h2h)
    assert isinstance(e.event_id, int)
    assert isinstance(e.home, str) and e.home
    assert isinstance(e.away, str) and e.away
    assert isinstance(e.kickoff_utc, datetime)
    assert e.kickoff_utc.tzinfo is not None


def test_h2h_order_is_away_home_draw():
    """PS3838 range le 1X2 en [exterieur, domicile, nul]. Inverser dom/ext est
    exactement la classe de bug que ce chantier elimine."""
    raw = ["7.130", "1.386", "5.110"]
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(raw) == {"home": 1.386, "draw": 5.11, "away": 7.13}


def test_h2h_missing_or_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(None) is None
    assert _parse_h2h(["", "", None]) is None
    assert _parse_h2h(["1.5"]) is None


def test_totals_uses_line_closest_to_2_5_and_ignores_quarter_lines():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3-3.5", 3.25, "2.090", "1.793"], ["3.0", 3.0, "1.854", "2.040"]]
    line, totals = _parse_totals(raw)
    assert line == 3.0
    assert totals == {"over_3.0": 1.854, "under_3.0": 2.04}


def test_totals_prefers_half_integer_when_available():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3.0", 3.0, "1.85", "2.04"], ["2.5", 2.5, "1.60", "2.35"]]
    line, totals = _parse_totals(raw)
    assert line == 2.5
    assert totals == {"over_2.5": 1.6, "under_2.5": 2.35}


def test_totals_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_totals

    assert _parse_totals([]) == (None, None)
    assert _parse_totals(None) == (None, None)


def test_event_ids_are_unique():
    evs = parse_events(_payload())
    ids = [e.event_id for e in evs]
    assert len(ids) == len(set(ids))
