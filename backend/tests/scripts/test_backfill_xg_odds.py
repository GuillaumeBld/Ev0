"""Rattrapage des cotes sur les estimations deja archivees."""
from types import SimpleNamespace

from app.scripts.backfill_xg_odds import rebuild_markets


def _row(market_type, outcome, odds):
    return SimpleNamespace(market_type=market_type, outcome=outcome, odds=odds)


def test_rebuild_groups_by_market_and_outcome():
    rows = [
        _row("h2h", "home", 1.347),
        _row("h2h", "draw", 5.35),
        _row("h2h", "away", 9.46),
        _row("totals", "over_3.0", 1.854),
        _row("totals", "under_3.0", 2.04),
    ]
    assert rebuild_markets(rows) == {
        "h2h": {"home": 1.347, "draw": 5.35, "away": 9.46},
        "totals": {"over_3.0": 1.854, "under_3.0": 2.04},
    }


def test_rebuild_with_no_rows_is_empty():
    """Snapshots disparus : on rend un dictionnaire vide, on ne fabrique rien."""
    assert rebuild_markets([]) == {}


def test_rebuild_keeps_every_market_present():
    rows = [_row("h2h", "home", 2.0), _row("btts", "yes", 1.9)]
    out = rebuild_markets(rows)
    assert set(out) == {"h2h", "btts"}
