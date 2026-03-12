"""Feature extraction for the autopilot RL agent.

Converts a recommendation dict (backtest record or live rec) into a
normalised 13-dimensional state vector that the Q-agent can consume.

Backtest record keys  (from backtest/simulator.py):
  edge, confidence, market_odds, fair_odds, fair_prob, market, league,
  outcome, player, fixture_id, date, ...

Live recommendation keys (from models/recommendations.py):
  edge, confidence, best_odds, fair_odds, fair_probability,
  lambda_intensity, market_type, league, explanation{expected_minutes,...},
  player_name, fixture_id, ...
"""

import numpy as np

FEATURE_DIM = 13

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
    "edge_rank",      # normalised rank among today's recs
    "n_recs_today",   # number of recs today / 10
    "odds_movement",  # % drop from first snapshot (steam signal)
    "bias",           # always 1.0
]


def extract_features(rec: dict) -> np.ndarray:
    """Convert a recommendation dict to a normalised 13-dim feature vector.

    Works for both backtest records and live Recommendation ORM objects
    (passed as dicts).  The 3 contextual features (edge_rank, n_recs_today,
    odds_movement) are set to safe defaults here; use
    extract_features_with_context() to fill them with real values.
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

    features = np.zeros(FEATURE_DIM, dtype=np.float64)
    features[0] = edge
    features[1] = confidence
    features[2] = implied_prob
    features[3] = fair_prob
    features[4] = lambda_val
    features[5] = mins_ratio
    features[6] = is_goalscorer
    features[7] = is_premier_league
    features[8] = is_forward
    features[9] = 0.5   # edge_rank: unknown → middle
    features[10] = 0.1  # n_recs_today: assume 1 rec
    features[11] = 0.0  # odds_movement: no data
    features[12] = 1.0  # bias

    return normalize_features(features)


def extract_features_with_context(
    rec: dict,
    all_recs_today: list[dict] | None = None,
    odds_snapshots: list | None = None,
) -> np.ndarray:
    """Extract features with contextual info (rank among today's recs, odds movement).
    Falls back to extract_features() defaults if context not provided."""
    # Start with the base features (first 9 + bias at 12)
    features = extract_features(rec)

    # Override the 3 contextual features with real values if available

    # edge_rank: normalized rank of this rec's edge among today's recs (0=worst, 1=best)
    edge = float(rec.get("edge", 0.0))
    if all_recs_today and len(all_recs_today) > 1:
        edges = sorted([float(r.get("edge", 0)) for r in all_recs_today])
        rank = 0
        for i, e in enumerate(edges):
            if abs(e - edge) < 1e-9:
                rank = i
                break
        features[9] = rank / (len(edges) - 1)
    else:
        features[9] = 0.5

    # n_recs_today: number of recs today normalized (1=0.1, 10+=1.0)
    n_recs = len(all_recs_today) if all_recs_today else 1
    features[10] = min(n_recs / 10.0, 1.0)

    # odds_movement: % change from first to last snapshot (positive = odds dropped = steam)
    if odds_snapshots and len(odds_snapshots) >= 2:
        first_odds = float(odds_snapshots[0])
        current_odds = float(odds_snapshots[-1])
        if first_odds > 1.0:
            features[11] = (first_odds - current_odds) / first_odds
        else:
            features[11] = 0.0
    else:
        features[11] = 0.0

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
    clipped[9] = np.clip(clipped[9], 0.0, 1.0)     # edge_rank
    clipped[10] = np.clip(clipped[10], 0.0, 1.0)   # n_recs_today
    clipped[11] = np.clip(clipped[11], -0.5, 0.5)  # odds_movement
    # bias [12] is always 1.0
    return clipped
