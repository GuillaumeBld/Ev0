"""Assist pricing module — Model C (Understat + Sofascore).

Replaces FBref dependency with:
  - xA, xGChain  → Understat (already integrated)
  - BCC, accurate crosses, through balls → Sofascore

Architecture: anchor + creation multiplier
    xA is the base rate anchor (Understat).
    BCC, xGChain, Crosses, Through Balls act as a creation multiplier around 1.0.

Formula:
    creation_multiplier = norm(bcc/90)     × w_bcc
                        + norm(xgchain/90) × w_xgchain
                        + norm(crosses/90) × w_crosses
                        + norm(tb/90)      × w_tb
    creation_multiplier clamped to [0.5, 2.0]

    λ = xA_per_90 × (mins/90) × creation_multiplier
          × opponent_factor × form_factor

    P(assist ≥ 1) = 1 - e^(-λ)
    fair_odds = 1 / P

Position-specific weight profiles are applied automatically on the multiplier.

Sources:
    xA, xGChain  → Understat
    BCC, accurate crosses, through balls → Sofascore
"""

import math
from typing import Any, TypedDict

# ── Multiplier weights ─────────────────────────────────────────────

# Standard weights for creation multiplier (position-agnostic, must sum to 1.0)
CREATION_WEIGHTS_DEFAULT: dict[str, float] = {
    "bcc":     0.35,   # Big Chances Created per 90      [Sofascore]
    "xgchain": 0.25,   # xG Chain per 90                 [Understat]
    "crosses": 0.25,   # Accurate crosses per 90         [Sofascore]
    "tb":      0.15,   # Through balls per 90            [Sofascore]
}

# Position-specific weight profiles (must each sum to 1.0)
CREATION_WEIGHTS_BY_POSITION: dict[str, dict[str, float]] = {
    "MF": {
        # Midfielders: BCC and xGChain dominate, fewer crosses
        "bcc":     0.40,
        "xgchain": 0.35,
        "crosses": 0.10,
        "tb":      0.15,
    },
    "AM": {
        "bcc":     0.40,
        "xgchain": 0.35,
        "crosses": 0.10,
        "tb":      0.15,
    },
    "FW": {
        # Forwards: involve heavily in build-up chains
        "bcc":     0.45,
        "xgchain": 0.35,
        "crosses": 0.05,
        "tb":      0.15,
    },
    "W": {
        # Wingers: crosses are primary creative outlet
        "bcc":     0.30,
        "xgchain": 0.20,
        "crosses": 0.40,
        "tb":      0.10,
    },
    "FB": {
        # Fullbacks: crosses are primary creative outlet
        "bcc":     0.20,
        "xgchain": 0.15,
        "crosses": 0.50,
        "tb":      0.15,
    },
    "DF": {
        "bcc":     0.25,
        "xgchain": 0.20,
        "crosses": 0.40,
        "tb":      0.15,
    },
}

# Default league averages per 90 for normalization (all outfield positions)
LEAGUE_AVG_DEFAULTS: dict[str, float] = {
    "bcc":     0.18,   # Big Chances Created per 90
    "xgchain": 0.35,   # xGChain per 90
    "crosses": 0.80,   # Accurate crosses per 90
    "tb":      0.25,   # Through balls per 90
}

CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0
CLAMP_LAMBDA_MIN = 0.01
CLAMP_LAMBDA_MAX = 2.0


# ── TypedDict ─────────────────────────────────────────────────────

class AssistPriceResult(TypedDict):
    lambda_intensity: float
    probability: float
    fair_odds: float
    explanation: dict[str, Any]


# ── Helpers ───────────────────────────────────────────────────────

def _get_weights(position: str | None) -> dict[str, float]:
    """Return the weight profile for a given position."""
    if not position:
        return CREATION_WEIGHTS_DEFAULT
    pos = position.upper()
    return CREATION_WEIGHTS_BY_POSITION.get(pos, CREATION_WEIGHTS_DEFAULT)


# ── Creation multiplier ───────────────────────────────────────────

def calculate_creation_multiplier(
    bcc_per_90: float = 0.0,
    xgchain_per_90: float = 0.0,
    accurate_crosses_per_90: float = 0.0,
    through_balls_per_90: float = 0.0,
    position: str | None = None,
    league_averages: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Compute creation multiplier from Sofascore + Understat metrics.

    Returns a value around 1.0 (1.0 = league average creation quality).
    Applied as a multiplier on top of the xA anchor.
    Clamped to [0.5, 2.0].

    Args:
        bcc_per_90:              Big Chances Created per 90 (Sofascore)
        xgchain_per_90:          xG Chain per 90 (Understat)
        accurate_crosses_per_90: Accurate crosses per 90 (Sofascore)
        through_balls_per_90:    Through balls per 90 (Sofascore)
        position:                Player position for weight profile
        league_averages:         Override default league averages

    Returns:
        (multiplier, breakdown)
    """
    avgs = {**LEAGUE_AVG_DEFAULTS, **(league_averages or {})}
    weights = _get_weights(position)

    def norm(val: float, key: str) -> float:
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 1.0

    raw_values = {
        "bcc":     bcc_per_90,
        "xgchain": xgchain_per_90,
        "crosses": accurate_crosses_per_90,
        "tb":      through_balls_per_90,
    }

    components = {k: norm(v, k) for k, v in raw_values.items()}
    raw_multiplier = sum(components[k] * weights[k] for k in weights)
    multiplier = max(CLAMP_MULTIPLIER_MIN, min(raw_multiplier, CLAMP_MULTIPLIER_MAX))

    breakdown = {
        k: {
            "raw_per_90": round(raw_values[k], 4),
            "league_avg": round(avgs.get(k, 1.0), 4),
            "normalized": round(components[k], 3),
            "weight": weights[k],
            "contribution": round(components[k] * weights[k], 3),
        }
        for k in weights
    }

    return multiplier, breakdown


# ── Pricing ───────────────────────────────────────────────────────

def calculate_assist_price(
    xa_per_90: float,
    expected_minutes: float = 90.0,
    bcc_per_90: float = 0.0,
    xgchain_per_90: float = 0.0,
    accurate_crosses_per_90: float = 0.0,
    through_balls_per_90: float = 0.0,
    position: str | None = None,
    opponent_defense_factor: float = 1.0,
    form_factor: float = 1.0,
    league_averages: dict[str, float] | None = None,
) -> AssistPriceResult:
    """
    Calculate fair price for anytime assist market (Model C).

    xA (Understat) anchors the base rate.
    BCC + xGChain + Crosses + Through Balls scale it via creation multiplier.

    Args:
        xa_per_90:               Expected assists per 90 — BASE ANCHOR (Understat)
        expected_minutes:        Expected playing time in the match
        bcc_per_90:              Big Chances Created per 90 (Sofascore)
        xgchain_per_90:          xG Chain per 90 (Understat)
        accurate_crosses_per_90: Accurate crosses per 90 (Sofascore)
        through_balls_per_90:    Through balls per 90 (Sofascore)
        position:                Player position for weight profile (FW/MF/DF/W/FB)
        opponent_defense_factor: opponent_xGA / league_avg_xGA
        form_factor:             Exponential decay form score
        league_averages:         Override default league averages

    Returns:
        AssistPriceResult with lambda, probability, fair_odds, explanation
    """
    multiplier, breakdown = calculate_creation_multiplier(
        bcc_per_90=bcc_per_90,
        xgchain_per_90=xgchain_per_90,
        accurate_crosses_per_90=accurate_crosses_per_90,
        through_balls_per_90=through_balls_per_90,
        position=position,
        league_averages=league_averages,
    )

    # λ = xA_per_90 × (mins/90) × creation_multiplier × adjustments
    raw_lambda = (
        xa_per_90
        * (expected_minutes / 90.0)
        * multiplier
        * opponent_defense_factor
        * form_factor
    )
    adjusted_lambda = max(CLAMP_LAMBDA_MIN, min(raw_lambda, CLAMP_LAMBDA_MAX))

    probability = 1 - math.exp(-adjusted_lambda)
    fair_odds = 1 / probability if probability > 0 else 99999.0

    used_weights = _get_weights(position)

    explanation = {
        "model": "C — xA anchor (Understat) × creation multiplier (BCC+xGChain+Crosses+TB)",
        "anchor": {
            "xa_per_90": xa_per_90,
            "role": "base rate — expected assists per 90 (Understat)",
        },
        "creation_multiplier": {
            "value": round(multiplier, 4),
            "clamp_range": [CLAMP_MULTIPLIER_MIN, CLAMP_MULTIPLIER_MAX],
            "position": position,
            "weights_used": used_weights,
            "breakdown": breakdown,
        },
        "adjustments": {
            "opponent_defense_factor": opponent_defense_factor,
            "form_factor": form_factor,
            "expected_minutes": expected_minutes,
        },
        "lambda": {
            "raw": round(raw_lambda, 4),
            "clamped": round(adjusted_lambda, 4),
        },
        "interpretation": _interpret_assist_probability(probability),
    }

    return AssistPriceResult(
        lambda_intensity=round(adjusted_lambda, 4),
        probability=round(probability, 4),
        fair_odds=round(fair_odds, 2),
        explanation=explanation,
    )


def _interpret_assist_probability(prob: float) -> str:
    if prob >= 0.4:
        return "Elite creator — primary chance generator for the team"
    elif prob >= 0.25:
        return "High assist threat — key playmaker role"
    elif prob >= 0.12:
        return "Moderate creator — contributes to attack regularly"
    elif prob >= 0.05:
        return "Occasional assists — secondary creative role"
    else:
        return "Rarely assists — defensive or limited creative duties"


# ── Bzzoiro creation multiplier ──────────────────────────────────

# New creation multiplier weights for bzz_player_season_stats fields
# Formula: key_pass_per_90 × 0.40 + xa_per_90 × 0.40 + accurate_cross_per_90 × 0.20
BZZ_CREATION_WEIGHTS = {
    "key_pass_per_90":       0.40,
    "xa_per_90":             0.40,
    "accurate_cross_per_90": 0.20,
}


def calculate_creation_multiplier_bzz(
    stats: dict[str, Any],
) -> float:
    """
    Compute creation score from bzz_player_season_stats fields.

    Formula:
        creation = key_pass_per_90 × 0.40 + xa_per_90 × 0.40 + accurate_cross_per_90 × 0.20

    Args:
        stats: Player dict with keys key_pass_per_90, xa_per_90, accurate_cross_per_90.

    Returns:
        Raw creation score (not clamped — caller may clamp if needed).
    """
    return (
        (stats.get("key_pass_per_90") or 0) * BZZ_CREATION_WEIGHTS["key_pass_per_90"]
        + (stats.get("xa_per_90") or 0) * BZZ_CREATION_WEIGHTS["xa_per_90"]
        + (stats.get("accurate_cross_per_90") or 0) * BZZ_CREATION_WEIGHTS["accurate_cross_per_90"]
    )


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
