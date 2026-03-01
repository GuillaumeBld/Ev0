"""Feature extraction for the autopilot RL agent.

Converts a recommendation dict (backtest record or live rec) into a
normalised 10-dimensional state vector that the Q-agent can consume.

Backtest record keys  (from backtest/simulator.py):
  edge, confidence, market_odds, fair_odds, fair_prob, market, league,
  outcome, player, fixture_id, date, ...

Live recommendation keys (from models/recommendations.py):
  edge, confidence, best_odds, fair_odds, fair_probability,
  lambda_intensity, market_type, league, explanation{expected_minutes,...},
  player_name, fixture_id, ...
"""

import numpy as np

FEATURE_DIM = 10

FEATURE_NAMES = [
    "edge",
    "confidence",
    "implied_prob",   # 1 / market_odds
    "fair_prob",      # 1 / fair_odds
    "lambda",         # Poisson intensity
    "mins_ratio",     # expected_minutes / 90
    "is_goalscorer",  # 1 if goalscorer market
    "is_premier_league",  # 1 if PL
    "is_forward",     # 1 if FW/ST position
    "bias",           # always 1.0
]


def extract_features(rec: dict) -> np.ndarray:
    """Convert a recommendation dict to a normalised 10-dim feature vector.

    Works for both backtest records and live Recommendation ORM objects
    (passed as dicts).
    """
    # --- edge ---
    edge = float(rec.get("edge", 0.0))

    # --- confidence ---
    confidence = float(rec.get("confidence", 0.5))

    # --- implied probability (1 / market odds) ---
    market_odds = float(
        rec.get("market_odds") or rec.get("best_odds") or 2.0
    )
    implied_prob = 1.0 / max(market_odds, 1.01)

    # --- fair probability ---
    fair_odds = float(rec.get("fair_odds") or 2.0)
    fair_prob_from_odds = 1.0 / max(fair_odds, 1.01)
    # backtest record has fair_prob directly
    fair_prob = float(rec.get("fair_prob") or rec.get("fair_probability") or fair_prob_from_odds)

    # --- lambda intensity ---
    # backtest records don't store lambda; approximate from fair_prob using -ln(1-p)
    import math
    lambda_val = float(
        rec.get("lambda_intensity")
        or (-math.log(max(1 - fair_prob, 1e-6)))
    )

    # --- expected minutes ratio ---
    explanation = rec.get("explanation") or {}
    if isinstance(explanation, str):
        import json
        try:
            explanation = json.loads(explanation)
        except Exception:
            explanation = {}
    expected_minutes = float(
        explanation.get("expected_minutes")
        or rec.get("expected_minutes")
        or 75.0
    )
    mins_ratio = min(expected_minutes / 90.0, 1.0)

    # --- market type ---
    market = rec.get("market_type") or rec.get("market") or ""
    is_goalscorer = 1.0 if "goal" in market.lower() else 0.0

    # --- league ---
    league = rec.get("league") or ""
    is_premier_league = 1.0 if "premier" in league.lower() else 0.0

    # --- position ---
    position = (
        explanation.get("position")
        or rec.get("position")
        or ""
    )
    if isinstance(position, str):
        position = position.upper()
    is_forward = 1.0 if position in ("FW", "ST", "CF", "SS") else 0.0

    features = np.array(
        [
            edge,
            confidence,
            implied_prob,
            fair_prob,
            lambda_val,
            mins_ratio,
            is_goalscorer,
            is_premier_league,
            is_forward,
            1.0,  # bias
        ],
        dtype=np.float64,
    )

    return normalize_features(features)


def normalize_features(features: np.ndarray) -> np.ndarray:
    """Clip features to valid ranges and return clean array."""
    clipped = features.copy()
    clipped[0] = np.clip(clipped[0], -0.5, 0.5)   # edge
    clipped[1] = np.clip(clipped[1], 0.0, 1.0)     # confidence
    clipped[2] = np.clip(clipped[2], 0.0, 1.0)     # implied_prob
    clipped[3] = np.clip(clipped[3], 0.0, 1.0)     # fair_prob
    clipped[4] = np.clip(clipped[4], 0.0, 2.0)     # lambda
    clipped[5] = np.clip(clipped[5], 0.0, 1.0)     # mins_ratio
    # binary features [6..8] are already 0 or 1
    # bias [9] is always 1.0
    return clipped
