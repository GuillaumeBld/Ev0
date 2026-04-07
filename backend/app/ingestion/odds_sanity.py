"""Sanity checks and clean probability computation for scraped odds."""

import math

_EXPECTED_SELECTIONS: dict[str, set[str]] = {
    "h2h": {"home", "draw", "away"},
    "totals": {"over_2.5", "under_2.5"},
    "btts": {"yes", "no"},
}

_MIN_ODDS = 1.01
_MAX_IMPLIED_SUM = 1.50  # sum of 1/odds ceiling (50% overround = suspicious)
_MIN_IMPLIED_SUM = 1.00  # sum of 1/odds floor


def validate_market(market_type: str, outcomes: dict[str, float | None]) -> bool:
    """
    Return True iff odds dict is valid for xG inference.

    Checks:
    - market_type is known
    - all expected selections present (no more, no less)
    - all odds > 1.01, not None, not NaN
    - sum(1/odds) in [1.0, 1.50]
    """
    expected = _EXPECTED_SELECTIONS.get(market_type)
    if expected is None:
        return False

    if set(outcomes.keys()) != expected:
        return False

    total_implied = 0.0
    for odds in outcomes.values():
        if odds is None:
            return False
        if isinstance(odds, float) and math.isnan(odds):
            return False
        if odds < _MIN_ODDS:
            return False
        total_implied += 1.0 / odds

    return _MIN_IMPLIED_SUM <= total_implied <= _MAX_IMPLIED_SUM


def compute_clean_probs(outcomes: dict[str, float]) -> dict[str, float]:
    """
    Normalise raw odds to sum-to-one probabilities (simple margin removal).

    p_implied[i] = 1 / odds[i]
    p_clean[i]   = p_implied[i] / sum(p_implied)
    """
    implied = {k: 1.0 / v for k, v in outcomes.items()}
    total = sum(implied.values())
    return {k: v / total for k, v in implied.items()}
