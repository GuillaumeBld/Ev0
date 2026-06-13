"""Dual-market recommendation builder (STANDARD + SUPERSUB).

This module provides build_recommendations_for_player, a pure function that
produces RecommendationCandidate objects for both the standard and supersub
markets given a player allocation and current market odds.

RecommendationCandidate is intentionally a lightweight dataclass (not an ORM
object) so it can be used in unit tests without a database session and then
mapped to the ORM Recommendation model by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.recommendations import MarketType


@dataclass
class RecommendationCandidate:
    """Lightweight recommendation result — not tied to a DB session.

    Attributes:
        player_name: Name of the player.
        fixture_id: External fixture identifier (str or int).
        bet_type: "goal" or "assist".
        market_type: MarketType.STANDARD or MarketType.SUPERSUB.
        fair_probability: Model probability (0–1).
        fair_odds: Fair odds derived from model probability.
        best_odds: Market odds passed in.
        ev: Expected value = prob * market_odds - 1.
        edge: (market_odds / fair_odds) - 1.
    """

    player_name: str
    fixture_id: Any
    bet_type: str
    market_type: MarketType
    fair_probability: float
    fair_odds: float
    best_odds: float
    ev: float
    edge: float


def build_recommendations_for_player(
    alloc: Any,
    market_odds: dict[str, float],
) -> list[RecommendationCandidate]:
    """Build RecommendationCandidate list for STANDARD and SUPERSUB markets.

    Args:
        alloc: A player allocation object with the following attributes:
            - player_name (str)
            - fixture_id (str | int)
            - prob_goal, fair_odds_goal            — standard goal pricing
            - prob_assist, fair_odds_assist        — standard assist pricing
            - p_goal_supersub, fair_odds_goal_supersub   — supersub goal
            - p_assist_supersub, fair_odds_assist_supersub — supersub assist
        market_odds: Dict with keys:
            "goal_standard", "goal_supersub", "assist_standard", "assist_supersub"
            Any key missing or with value <= 0 is skipped.

    Returns:
        List of RecommendationCandidate (empty if no valid odds provided).
    """
    configs = [
        {
            "market_type": MarketType.STANDARD,
            "goal_prob":   alloc.prob_goal,
            "goal_odds":   market_odds.get("goal_standard", 0.0),
            "goal_fair":   alloc.fair_odds_goal,
            "assist_prob": alloc.prob_assist,
            "assist_odds": market_odds.get("assist_standard", 0.0),
            "assist_fair": alloc.fair_odds_assist,
        },
        {
            "market_type": MarketType.SUPERSUB,
            "goal_prob":   alloc.p_goal_supersub,
            "goal_odds":   market_odds.get("goal_supersub", 0.0),
            "goal_fair":   alloc.fair_odds_goal_supersub,
            "assist_prob": alloc.p_assist_supersub,
            "assist_odds": market_odds.get("assist_supersub", 0.0),
            "assist_fair": alloc.fair_odds_assist_supersub,
        },
    ]

    results: list[RecommendationCandidate] = []
    for cfg in configs:
        for bet_type in ("goal", "assist"):
            odds: float = cfg[f"{bet_type}_odds"]
            prob: float = cfg[f"{bet_type}_prob"]
            fair: float = cfg[f"{bet_type}_fair"]

            if odds <= 0 or prob <= 0:
                continue

            ev = round(prob * odds - 1.0, 4)
            edge = round((odds / fair) - 1.0, 4) if fair > 0 else 0.0

            results.append(
                RecommendationCandidate(
                    player_name=alloc.player_name,
                    fixture_id=alloc.fixture_id,
                    bet_type=bet_type,
                    market_type=cfg["market_type"],
                    fair_probability=prob,
                    fair_odds=fair,
                    best_odds=odds,
                    ev=ev,
                    edge=edge,
                )
            )

    return results
