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

from __future__ import annotations

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

# ── Top-down assist constants (Bzzoiro v2) ────────────────────────

ASSIST_GOAL_RATE: float = 0.65

ASSIST_POSITION_AVGS: dict[str, dict[str, float]] = {
    "FW": {"xa_per_90": 0.08, "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15},
    "MF": {"xa_per_90": 0.06, "key_pass_per_90": 0.55, "accurate_cross_per_90": 0.20},
    "DF": {"xa_per_90": 0.03, "key_pass_per_90": 0.20, "accurate_cross_per_90": 0.40},
}
_ASSIST_FALLBACK_AVGS: dict[str, float] = {
    "xa_per_90": 0.06, "key_pass_per_90": 0.45, "accurate_cross_per_90": 0.22,
}

CROSS_ACC_LEAGUE_AVG: float = 0.35

CREATION_WEIGHTS_BY_PROFILE: dict[str, dict[str, float]] = {
    "wide":    {"xa": 0.25, "kp": 0.20, "xc": 0.40, "xca": 0.15},
    "central": {"xa": 0.40, "kp": 0.50, "xc": 0.08, "xca": 0.02},
    "hybrid":  {"xa": 0.35, "kp": 0.35, "xc": 0.20, "xca": 0.10},
    "unknown": {"xa": 0.40, "kp": 0.35, "xc": 0.15, "xca": 0.10},
}

CREATION_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
XA_CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
XA_CONVERSION_MIN_MATCHES: int = 5
_PROFILE_WIDE_THRESHOLD: float = 0.55
_PROFILE_CENTRAL_THRESHOLD: float = 0.25
_PROFILE_MIN_TOTAL: float = 0.05


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


# ── Top-down v2 functions ─────────────────────────────────────────

def detect_creator_profile(stats: dict[str, Any]) -> str:
    """Detect player's creation style: 'wide', 'central', 'hybrid', or 'unknown'."""
    kp = stats.get("key_pass_per_90") or 0.0
    xc = stats.get("accurate_cross_per_90") or 0.0
    total = kp + xc
    if total < _PROFILE_MIN_TOTAL:
        return "unknown"
    cross_dominance = xc / total
    if cross_dominance > _PROFILE_WIDE_THRESHOLD:
        return "wide"
    elif cross_dominance < _PROFILE_CENTRAL_THRESHOLD:
        return "central"
    return "hybrid"


def calculate_creation_multiplier_v2(stats: dict[str, Any], position: str | None) -> float:
    """
    Hybrid position+profile creation multiplier (Bzzoiro v2).

    Normalises xa_per_90, key_pass_per_90, accurate_cross_per_90, cross_accuracy
    against position averages. Weights depend on detected creation profile.
    Returns value clamped to [0.70, 1.50] (1.0 = league average for this position).
    """
    profile = detect_creator_profile(stats)
    avgs = ASSIST_POSITION_AVGS.get(position or "", _ASSIST_FALLBACK_AVGS)

    def norm(stat_key: str, avg_key: str) -> float:
        val = stats.get(stat_key) or 0.0
        avg = avgs.get(avg_key, 1.0)
        return (val / avg) if avg > 0 else 0.0

    xa_norm  = norm("xa_per_90", "xa_per_90")
    kp_norm  = norm("key_pass_per_90", "key_pass_per_90")
    xc_norm  = norm("accurate_cross_per_90", "accurate_cross_per_90")
    xca_norm = (stats.get("cross_accuracy") or 0.0) / CROSS_ACC_LEAGUE_AVG

    w = CREATION_WEIGHTS_BY_PROFILE[profile]
    raw = (
        w["xa"]  * xa_norm
        + w["kp"]  * kp_norm
        + w["xc"]  * xc_norm
        + w["xca"] * xca_norm
    )
    return max(CREATION_MULT_CLAMP[0], min(raw, CREATION_MULT_CLAMP[1]))


def calculate_xa_conversion(stats: dict[str, Any]) -> float:
    """Assists / xA conversion rate. Returns 1.0 if insufficient data."""
    matches = stats.get("matches_played") or 0
    if matches < XA_CONVERSION_MIN_MATCHES:
        return 1.0
    xa = stats.get("xa_total") or 0.0
    assists = stats.get("assists") or 0
    if xa <= 0:
        return 1.0
    return max(XA_CONVERSION_CLAMP[0], min(assists / xa, XA_CONVERSION_CLAMP[1]))


def calculate_assist_lambda(
    share_xa: float,
    budget_assists: float,
    creation_mult: float,
    xa_conversion: float,
) -> float:
    """Compute final assist λ (top-down allocation)."""
    lam = share_xa * budget_assists * creation_mult * xa_conversion
    return max(CLAMP_LAMBDA_MIN, min(lam, CLAMP_LAMBDA_MAX))


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
