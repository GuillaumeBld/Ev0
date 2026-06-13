"""Goalscorer pricing module — top-down Bzzoiro model.

Architecture: top-down allocation
    Team match xG (MarketXgService) is split among players via npxg_share.
    finishing_multiplier scales per player based on shot quality metrics.

Formula:
    λ = npxg_share × team_xg × finishing_multiplier × conversion_rate
    P(score ≥ 1) = 1 - e^(-λ)
    fair_odds = 1 / P

Source: Bzzoiro (bzz_player_season_stats)
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

CLAMP_LAMBDA_MIN = 0.01
CLAMP_LAMBDA_MAX = 3.0


# ── Pen taker constants ───────────────────────────────────────────
PEN_CONVERSION = 0.78
PENS_PER_MATCH = 0.10

# ── Top-down finishing multiplier (Bzzoiro) ───────────────────────

GOALSCORER_POSITION_AVGS: dict[str, dict[str, float]] = {
    # Calibrated on Bzzoiro 2025-2026 (≥450 min, Big5 + UCL)
    "FW": {"shot_accuracy": 0.515, "xg_per_shot": 0.176, "rating": 0.676},
    "MF": {"shot_accuracy": 0.442, "xg_per_shot": 0.116, "rating": 0.683},
    "DF": {"shot_accuracy": 0.365, "xg_per_shot": 0.105, "rating": 0.678},
}
_GOALSCORER_FALLBACK_AVGS: dict[str, float] = {
    "shot_accuracy": 0.37, "xg_per_shot": 0.10, "rating": 0.68,
}

FINISHING_MULT_WEIGHTS: dict[str, float] = {
    "shot_accuracy": 0.40,
    "xg_per_shot":   0.40,
    "rating":        0.20,
}
FINISHING_MULT_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.70, 1.50),
    "MF": (0.55, 1.50),
    "DF": (0.30, 1.30),
}
_FINISHING_MULT_CLAMP_DEFAULT: tuple[float, float] = (0.55, 1.50)
CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
CONVERSION_MIN_MATCHES: int = 5


def calculate_finishing_multiplier(stats: dict[str, Any], position: str | None) -> float:
    """Normalize finishing quality stats against position averages → multiplier ≈ 1.0."""
    avgs = GOALSCORER_POSITION_AVGS.get(position or "", _GOALSCORER_FALLBACK_AVGS)

    def norm(key: str) -> float:
        val = stats.get(key) or 0.0
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 0.0

    rating_raw = (stats.get("avg_rating") or 0.0) / 10.0  # normalize to 0-1
    rating_norm = rating_raw / avgs["rating"] if avgs["rating"] > 0 else 0.0

    raw = (
        norm("shot_accuracy") * FINISHING_MULT_WEIGHTS["shot_accuracy"]
        + norm("xg_per_shot")   * FINISHING_MULT_WEIGHTS["xg_per_shot"]
        + rating_norm           * FINISHING_MULT_WEIGHTS["rating"]
    )
    clamp = FINISHING_MULT_CLAMP.get(position or "", _FINISHING_MULT_CLAMP_DEFAULT)
    return max(clamp[0], min(raw, clamp[1]))


def calculate_conversion_rate(stats: dict[str, Any]) -> float:
    """Goals / xG conversion rate. Returns 1.0 if insufficient data."""
    matches = stats.get("matches_played") or 0
    if matches < CONVERSION_MIN_MATCHES:
        return 1.0
    xg = stats.get("npxg_total") or 0.0
    goals = stats.get("goals") or 0
    if xg <= 0:
        return 1.0
    return max(CONVERSION_CLAMP[0], min(goals / xg, CONVERSION_CLAMP[1]))


def calculate_goalscorer_lambda(
    share: float,
    lambda_team: float,
    finishing_mult: float,
    conversion: float,
    mins_ratio: float,
    is_pen_taker: bool = False,
) -> float:
    """Compute final goalscorer λ (top-down allocation)."""
    lam = share * lambda_team * finishing_mult * conversion
    if is_pen_taker:
        lam += PEN_CONVERSION * PENS_PER_MATCH * mins_ratio
    return max(CLAMP_LAMBDA_MIN, min(lam, CLAMP_LAMBDA_MAX))


# ── Edge & margin helpers ─────────────────────────────────────────

def calculate_edge(fair_odds: float, market_odds: float) -> float:
    """Edge = (market_odds / fair_odds) - 1."""
    if fair_odds <= 0 or market_odds <= 0:
        return 0.0
    return (market_odds / fair_odds) - 1


def remove_margin(odds_list: list[float]) -> list[float]:
    """Remove bookmaker margin proportionally."""
    total_prob = sum(1 / o for o in odds_list if o > 0)
    return [o * total_prob for o in odds_list]


# ── Supersub formula ──────────────────────────────────────────────

from app.pricing.sub_constants import SUB_GOAL_LAMBDA


def calculate_supersub_prob(
    lambda_A: float,
    p_sub: float,
    t_sub: float,
    lambda_B_sub: float | None = None,
    position: str = "FW",
) -> float:
    """
    P(pari gagné avec mécanique supersub).

    P = (1 - p_sub) × (1 - e^(-λ_A))
      + p_sub       × (1 - e^(-(λ_A×t_sub/90 + λ_B×(90-t_sub)/90)))
    """
    if lambda_B_sub is None:
        lambda_B_sub = SUB_GOAL_LAMBDA.get(position, 0.08)
    lA_adj  = lambda_A * (t_sub / 90.0)
    lB_adj  = lambda_B_sub * ((90.0 - t_sub) / 90.0)
    p_full  = (1.0 - p_sub) * (1.0 - math.exp(-lambda_A))
    p_chain = p_sub          * (1.0 - math.exp(-(lA_adj + lB_adj)))
    return p_full + p_chain
