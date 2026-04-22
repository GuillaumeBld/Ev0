"""Goalscorer pricing module — Model C (Understat + Sofascore).

Replaces FBref dependency with Understat (npxG, xGChain) + Sofascore
(shots on target, touches in attacking penalty area).

Architecture: anchor + quality multiplier
    npxG is the base rate anchor (Understat).
    SOT, TAP, xGChain act as a quality multiplier around 1.0.

Formula:
    quality_multiplier = norm(sot/90) × 0.40
                       + norm(tap/90) × 0.35
                       + norm(xgchain/90) × 0.25
    quality_multiplier clamped to [0.5, 2.0]

    λ = npxG_per_90 × (mins/90) × quality_multiplier
          × conversion_rate × opponent_factor × form_factor

    P(score ≥ 1) = 1 - e^(-λ)
    fair_odds = 1 / P

Sources:
    npxG, xGChain  → Understat
    SOT, TAP       → Sofascore
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

# ── Quality multiplier weights (must sum to 1.0) ──────────────────

QUALITY_WEIGHTS = {
    "sot":     0.40,   # Shots on target per 90         [Sofascore]
    "tap":     0.35,   # Touches in attack pen. area    [Sofascore]
    "xgchain": 0.25,   # xG Chain per 90                [Understat]
}

# Default league averages per 90 for normalization (all outfield positions)
LEAGUE_AVG_DEFAULTS = {
    "sot":     0.60,   # shots on target per 90
    "tap":     2.50,   # touches in attack pen. area per 90
    "xgchain": 0.35,   # xGChain per 90
}

CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0
CLAMP_LAMBDA_MIN = 0.01
CLAMP_LAMBDA_MAX = 3.0


# ── TypedDict ─────────────────────────────────────────────────────

class GoalscorerPriceResult(TypedDict):
    lambda_intensity: float
    probability: float
    fair_odds: float
    explanation: dict[str, Any]


# ── Quality multiplier ────────────────────────────────────────────

def calculate_quality_multiplier(
    sot_per_90: float = 0.0,
    touches_attack_pen_per_90: float = 0.0,
    xgchain_per_90: float = 0.0,
    league_averages: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Compute quality multiplier from Sofascore + Understat metrics.

    Returns a value around 1.0 (1.0 = league average quality).
    Clamped to [0.5, 2.0] to prevent outliers from dominating.

    Args:
        sot_per_90:                Shots on target per 90 (Sofascore)
        touches_attack_pen_per_90: Touches in att. penalty area per 90 (Sofascore)
        xgchain_per_90:            xG Chain per 90 (Understat)
        league_averages:           Override default per-90 league averages

    Returns:
        (multiplier, breakdown)
    """
    avgs = {**LEAGUE_AVG_DEFAULTS, **(league_averages or {})}

    def norm(val: float, key: str) -> float:
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 1.0

    raw_values = {
        "sot":     sot_per_90,
        "tap":     touches_attack_pen_per_90,
        "xgchain": xgchain_per_90,
    }

    components = {k: norm(v, k) for k, v in raw_values.items()}
    raw_multiplier = sum(components[k] * QUALITY_WEIGHTS[k] for k in QUALITY_WEIGHTS)
    multiplier = max(CLAMP_MULTIPLIER_MIN, min(raw_multiplier, CLAMP_MULTIPLIER_MAX))

    breakdown = {
        k: {
            "raw_per_90": round(raw_values[k], 4),
            "league_avg": round(avgs.get(k, 1.0), 4),
            "normalized": round(components[k], 3),
            "weight": QUALITY_WEIGHTS[k],
            "contribution": round(components[k] * QUALITY_WEIGHTS[k], 3),
        }
        for k in QUALITY_WEIGHTS
    }

    return multiplier, breakdown


# ── Pricing ───────────────────────────────────────────────────────

def calculate_goalscorer_price(
    npxg_per_90: float,
    expected_minutes: float = 90.0,
    sot_per_90: float = 0.0,
    touches_attack_pen_per_90: float = 0.0,
    xgchain_per_90: float = 0.0,
    conversion_rate: float = 1.0,
    opponent_xga_factor: float = 1.0,
    form_factor: float = 1.0,
    league_averages: dict[str, float] | None = None,
) -> GoalscorerPriceResult:
    """
    Calculate fair price for anytime goalscorer market (Model C).

    npxG (Understat) anchors the base rate.
    SOT + TAP + xGChain (Sofascore + Understat) scale it via quality multiplier.

    Args:
        npxg_per_90:               Non-penalty xG per 90 — BASE ANCHOR (Understat)
        expected_minutes:          Expected minutes to play
        sot_per_90:                Shots on target per 90 (Sofascore)
        touches_attack_pen_per_90: Touches in att. penalty area per 90 (Sofascore)
        xgchain_per_90:            xG Chain per 90 (Understat)
        conversion_rate:           Finishing = actual goals / npxG, 15-match rolling, clamped [0.5, 2.0]
        opponent_xga_factor:       opponent_xGA_per_match / league_avg_xGA
        form_factor:               Exponential decay form score
        league_averages:           Override default league averages for normalization

    Returns:
        GoalscorerPriceResult with lambda, probability, fair_odds, explanation
    """
    multiplier, breakdown = calculate_quality_multiplier(
        sot_per_90=sot_per_90,
        touches_attack_pen_per_90=touches_attack_pen_per_90,
        xgchain_per_90=xgchain_per_90,
        league_averages=league_averages,
    )

    # λ = npxG_per_90 × (mins/90) × quality_multiplier × adjustments
    raw_lambda = (
        npxg_per_90
        * (expected_minutes / 90.0)
        * multiplier
        * conversion_rate
        * opponent_xga_factor
        * form_factor
    )
    adjusted_lambda = max(CLAMP_LAMBDA_MIN, min(raw_lambda, CLAMP_LAMBDA_MAX))

    probability = 1 - math.exp(-adjusted_lambda)
    fair_odds = 1 / probability if probability > 0 else 99999.0

    explanation = {
        "model": "C — npxG anchor (Understat) × quality multiplier (SOT+TAP+xGChain)",
        "anchor": {
            "npxg_per_90": npxg_per_90,
            "role": "base rate — non-penalty expected goals per 90 (Understat)",
        },
        "quality_multiplier": {
            "value": round(multiplier, 4),
            "clamp_range": [CLAMP_MULTIPLIER_MIN, CLAMP_MULTIPLIER_MAX],
            "breakdown": breakdown,
        },
        "adjustments": {
            "conversion_rate": conversion_rate,
            "opponent_xga_factor": opponent_xga_factor,
            "form_factor": form_factor,
            "expected_minutes": expected_minutes,
        },
        "lambda": {
            "raw": round(raw_lambda, 4),
            "clamped": round(adjusted_lambda, 4),
        },
        "interpretation": _interpret_probability(probability),
    }

    return GoalscorerPriceResult(
        lambda_intensity=round(adjusted_lambda, 4),
        probability=round(probability, 4),
        fair_odds=round(fair_odds, 2),
        explanation=explanation,
    )


def _interpret_probability(prob: float) -> str:
    if prob >= 0.5:
        return "Strong scoring threat — expected to score more often than not"
    elif prob >= 0.3:
        return "Solid scoring chance — reasonable probability of finding the net"
    elif prob >= 0.15:
        return "Moderate chance — typical for mid-tier forwards/midfielders"
    elif prob >= 0.05:
        return "Low probability — supplementary attacking threat"
    else:
        return "Very unlikely — defensive player or limited minutes expected"


# ── Pen taker constants ───────────────────────────────────────────
PEN_CONVERSION = 0.78
PENS_PER_MATCH = 0.10

# ── Top-down finishing multiplier (Bzzoiro) ───────────────────────

GOALSCORER_POSITION_AVGS: dict[str, dict[str, float]] = {
    "FW": {"shot_accuracy": 0.42, "xg_per_shot": 0.12, "rating": 0.69},
    "MF": {"shot_accuracy": 0.35, "xg_per_shot": 0.09, "rating": 0.68},
    "DF": {"shot_accuracy": 0.30, "xg_per_shot": 0.07, "rating": 0.67},
}
_GOALSCORER_FALLBACK_AVGS: dict[str, float] = {
    "shot_accuracy": 0.37, "xg_per_shot": 0.10, "rating": 0.68,
}

FINISHING_MULT_WEIGHTS: dict[str, float] = {
    "shot_accuracy": 0.40,
    "xg_per_shot":   0.40,
    "rating":        0.20,
}
FINISHING_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
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
    return max(FINISHING_MULT_CLAMP[0], min(raw, FINISHING_MULT_CLAMP[1]))


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
