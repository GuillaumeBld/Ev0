"""Assist pricing module.

Calculates fair probability for "Anytime Assist" market using enhanced Poisson model.

The assist model uses a composite "creation score" based on:
- xA (expected assists)
- Key passes
- Shot-creating actions (SCA)
- Crosses (attempted/completed)
- Passes into penalty area
- Progressive passes

Formula:
    creation_score = weighted_composite(xA, key_passes, SCA, crosses, passes_into_box, progressive)
    λ = (creation_score * expected_mins/90) * teammate_factor * opponent_factor * form_factor
    P(assist >= 1) = 1 - e^(-λ)
"""

import math
from typing import TypedDict


class AssistPriceResult(TypedDict):
    """Result of assist pricing calculation."""
    
    lambda_intensity: float
    probability: float
    fair_odds: float
    explanation: dict[str, Any]


# Weights for composite creation score (should sum to 1)
CREATION_WEIGHTS = {
    "xa": 0.35,
    "key_passes": 0.20,
    "sca": 0.15,
    "crosses": 0.10,
    "passes_into_box": 0.10,
    "progressive_passes": 0.10,
}


def calculate_creation_score(
    xa_per_90: float,
    key_passes_per_90: float = 0.0,
    sca_per_90: float = 0.0,
    crosses_per_90: float = 0.0,
    passes_into_box_per_90: float = 0.0,
    progressive_passes_per_90: float = 0.0,
    league_averages: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Calculate composite creation score from multiple metrics.
    
    Each metric is normalized by league average, then weighted.
    
    Returns:
        Tuple of (creation_score, component_breakdown)
    """
    # Default league averages (approximate, should be updated with real data)
    defaults = {
        "xa": 0.15,
        "key_passes": 1.5,
        "sca": 2.5,
        "crosses": 2.0,
        "passes_into_box": 1.0,
        "progressive_passes": 4.0,
    }
    avgs = league_averages or defaults
    
    # Normalize each metric
    components = {
        "xa": (xa_per_90 / avgs["xa"]) if avgs["xa"] > 0 else 1.0,
        "key_passes": (key_passes_per_90 / avgs["key_passes"]) if avgs["key_passes"] > 0 else 1.0,
        "sca": (sca_per_90 / avgs["sca"]) if avgs["sca"] > 0 else 1.0,
        "crosses": (crosses_per_90 / avgs["crosses"]) if avgs["crosses"] > 0 else 1.0,
        "passes_into_box": (passes_into_box_per_90 / avgs["passes_into_box"]) if avgs["passes_into_box"] > 0 else 1.0,
        "progressive_passes": (progressive_passes_per_90 / avgs["progressive_passes"]) if avgs["progressive_passes"] > 0 else 1.0,
    }
    
    # Weighted sum
    score = sum(
        components[key] * CREATION_WEIGHTS[key]
        for key in CREATION_WEIGHTS
    )
    
    breakdown = {
        key: {
            "raw": locals().get(f"{key}_per_90", xa_per_90 if key == "xa" else 0),
            "normalized": round(components[key], 3),
            "weight": CREATION_WEIGHTS[key],
            "contribution": round(components[key] * CREATION_WEIGHTS[key], 3),
        }
        for key in CREATION_WEIGHTS
    }
    
    return score, breakdown


def calculate_assist_price(
    xa_per_90: float,
    expected_minutes: float = 90.0,
    creation_score: float = 1.0,
    teammate_finishing_factor: float = 1.0,
    opponent_defense_factor: float = 1.0,
    form_factor: float = 1.0,
) -> AssistPriceResult:
    """
    Calculate fair price for anytime assist market.
    
    Args:
        xa_per_90: Player's expected assists per 90 minutes
        expected_minutes: Expected minutes to play
        creation_score: Composite creation score (normalized to 1.0 = league avg)
        teammate_finishing_factor: Team's finishing quality (goals/xG)
        opponent_defense_factor: Opponent's defensive weakness (xGA/league_avg)
        form_factor: Recent form from exponential decay
    
    Returns:
        Pricing result with lambda, probability, fair odds, and explanation
    """
    # Base expected assists for this match
    base_lambda = xa_per_90 * (expected_minutes / 90.0)
    
    # Apply adjustments
    adjusted_lambda = (
        base_lambda
        * creation_score
        * teammate_finishing_factor
        * opponent_defense_factor
        * form_factor
    )
    
    # Clamp to reasonable range
    adjusted_lambda = max(0.001, min(adjusted_lambda, 2.0))
    
    # Poisson: P(X >= 1) = 1 - e^(-λ)
    probability = 1 - math.exp(-adjusted_lambda)
    
    # Fair odds
    fair_odds = 1 / probability if probability > 0 else float("inf")
    
    explanation = {
        "inputs": {
            "xa_per_90": xa_per_90,
            "expected_minutes": expected_minutes,
            "creation_score": creation_score,
            "teammate_finishing_factor": teammate_finishing_factor,
            "opponent_defense_factor": opponent_defense_factor,
            "form_factor": form_factor,
        },
        "calculation": {
            "base_lambda": round(base_lambda, 4),
            "adjusted_lambda": round(adjusted_lambda, 4),
            "formula": "P(assist) = 1 - e^(-λ)",
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
    """Generate human-readable interpretation of assist probability."""
    if prob >= 0.4:
        return "Elite creator - primary chance generator for the team"
    elif prob >= 0.25:
        return "High assist threat - key playmaker role"
    elif prob >= 0.12:
        return "Moderate creator - contributes to attack regularly"
    elif prob >= 0.05:
        return "Occasional assists - secondary creative role"
    else:
        return "Rarely assists - defensive or limited creative duties"
