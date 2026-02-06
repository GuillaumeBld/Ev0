"""Goalscorer pricing module.

Calculates fair probability for "Anytime Goalscorer" market using Poisson distribution.

Formula:
    λ = (xG/90 * expected_mins/90) * conversion_rate * opponent_factor * form_factor
    P(goals >= 1) = 1 - P(goals = 0) = 1 - e^(-λ)
    fair_odds = 1 / P(goals >= 1)
"""

import math
from typing import TypedDict


class GoalscorerPriceResult(TypedDict):
    """Result of goalscorer pricing calculation."""
    
    lambda_intensity: float
    probability: float
    fair_odds: float
    explanation: dict[str, Any]


def calculate_goalscorer_price(
    xg_per_90: float,
    expected_minutes: float = 90.0,
    conversion_rate: float = 1.0,
    opponent_xga_factor: float = 1.0,
    form_factor: float = 1.0,
) -> GoalscorerPriceResult:
    """
    Calculate fair price for anytime goalscorer market.
    
    Args:
        xg_per_90: Player's expected goals per 90 minutes (historical average)
        expected_minutes: Expected minutes to play in the match
        conversion_rate: Player's finishing skill (actual goals / npxG), rolling 15 matches
        opponent_xga_factor: Opponent defensive factor (opponent_xGA / league_avg_xGA)
        form_factor: Recent form adjustment from exponential decay
    
    Returns:
        Pricing result with lambda, probability, fair odds, and explanation
    """
    # Calculate base lambda (expected goals for this match)
    base_lambda = xg_per_90 * (expected_minutes / 90.0)
    
    # Apply adjustments
    adjusted_lambda = (
        base_lambda
        * conversion_rate
        * opponent_xga_factor
        * form_factor
    )
    
    # Ensure lambda is positive and reasonable
    adjusted_lambda = max(0.001, min(adjusted_lambda, 3.0))
    
    # Poisson: P(X >= 1) = 1 - P(X = 0) = 1 - e^(-λ)
    probability = 1 - math.exp(-adjusted_lambda)
    
    # Fair odds (decimal)
    fair_odds = 1 / probability if probability > 0 else float("inf")
    
    # Build explanation payload
    explanation = {
        "inputs": {
            "xg_per_90": xg_per_90,
            "expected_minutes": expected_minutes,
            "conversion_rate": conversion_rate,
            "opponent_xga_factor": opponent_xga_factor,
            "form_factor": form_factor,
        },
        "calculation": {
            "base_lambda": round(base_lambda, 4),
            "adjusted_lambda": round(adjusted_lambda, 4),
            "formula": "P(score) = 1 - e^(-λ)",
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
    """Generate human-readable interpretation of probability."""
    if prob >= 0.5:
        return "Strong scoring threat - expected to score more often than not"
    elif prob >= 0.3:
        return "Solid scoring chance - reasonable probability of finding the net"
    elif prob >= 0.15:
        return "Moderate chance - typical for mid-tier forwards/midfielders"
    elif prob >= 0.05:
        return "Low probability - supplementary attacking threat"
    else:
        return "Very unlikely - defensive player or limited minutes expected"


def calculate_edge(fair_odds: float, market_odds: float) -> float:
    """
    Calculate edge vs market odds.
    
    Args:
        fair_odds: Our calculated fair odds
        market_odds: Bookmaker's offered odds
    
    Returns:
        Edge as decimal (e.g., 0.10 = 10% edge)
    """
    fair_prob = 1 / fair_odds
    market_prob = 1 / market_odds
    
    # Edge = (market_odds / fair_odds) - 1
    # Or equivalently: (fair_prob - market_prob) / market_prob
    return (market_odds / fair_odds) - 1


def remove_margin(odds_list: list[float]) -> list[float]:
    """
    Remove bookmaker margin from odds using proportional method.
    
    Args:
        odds_list: List of decimal odds for all selections in market
    
    Returns:
        List of fair odds with margin removed
    """
    # Calculate overround (total implied probability)
    total_prob = sum(1 / o for o in odds_list)
    
    # Remove margin proportionally
    fair_odds = [o * total_prob for o in odds_list]
    
    return fair_odds
