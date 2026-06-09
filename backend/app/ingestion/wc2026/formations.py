"""Formation definitions and validation for WC2026 expected lineups."""
from __future__ import annotations

FORMATIONS: dict[str, list[int]] = {
    # ── 4 defenders ───────────────────────────────────────────────
    "4-4-2":   [4, 4, 2],
    "4-4-2d":  [4, 1, 2, 1, 2],   # diamond
    "4-3-3":   [4, 3, 3],
    "4-2-3-1": [4, 2, 3, 1],
    "4-3-2-1": [4, 3, 2, 1],
    "4-5-1":   [4, 5, 1],
    "4-1-4-1": [4, 1, 4, 1],
    "4-1-3-2": [4, 1, 3, 2],
    "4-2-2-2": [4, 2, 2, 2],
    "4-3-1-2": [4, 3, 1, 2],
    # ── 3 defenders ───────────────────────────────────────────────
    "3-5-2":   [3, 5, 2],
    "3-4-3":   [3, 4, 3],
    "3-4-2-1": [3, 4, 2, 1],
    "3-4-1-2": [3, 4, 1, 2],
    "3-3-4":   [3, 3, 4],
    "3-6-1":   [3, 6, 1],
    # ── 5 defenders ───────────────────────────────────────────────
    "5-3-2":   [5, 3, 2],
    "5-4-1":   [5, 4, 1],
    "5-2-3":   [5, 2, 3],
    "5-2-2-1": [5, 2, 2, 1],
    "5-1-2-2": [5, 1, 2, 2],
}

_DEFAULT_MINUTES: dict[str, int] = {
    "starter":      85,
    "sub_planned":  30,
    "sub_tactical": 12,
    "reserve":       0,
}


def parse_formation(formation: str) -> list[int]:
    """Return per-line player counts for a formation string.

    Raises ValueError for unknown formations.
    """
    if formation not in FORMATIONS:
        raise ValueError(f"unknown formation: {formation!r}. Valid: {sorted(FORMATIONS)}")
    return FORMATIONS[formation]


def validate_lineup_formation(formation: str, players: list[dict]) -> None:
    """Raise ValueError if outfield player count doesn't equal 10.

    Counts all players with line_index > 0 (GK is line_index=0 and excluded).
    """
    outfield = [p for p in players if p.get("line_index", 0) > 0]
    if len(outfield) != 10:
        raise ValueError(
            f"expected 10 outfield starters for {formation!r}, got {len(outfield)}"
        )


def default_minutes_for_role(role: str) -> int:
    """Return default expected_minutes for a given role."""
    return _DEFAULT_MINUTES.get(role, 0)
