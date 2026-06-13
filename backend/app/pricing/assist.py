"""Assist pricing module — top-down Bzzoiro model (v2).

Architecture: top-down allocation with creation profile
    xA budget (team_xg × ASSIST_GOAL_RATE) is split via xa_share.
    creation_multiplier_v2 scales per player based on position and creation profile.

Formula:
    λ = xa_share × budget_assists × creation_multiplier × xa_conversion
    P(assist ≥ 1) = 1 - e^(-λ)
    fair_odds = 1 / P

Source: Bzzoiro (bzz_player_season_stats)
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0
CLAMP_LAMBDA_MIN = 0.01
CLAMP_LAMBDA_MAX = 2.0

# ── Top-down assist constants (Bzzoiro v2) ────────────────────────

ASSIST_GOAL_RATE: float = 0.65

ASSIST_POSITION_AVGS: dict[str, dict[str, float]] = {
    # Calibrated on Bzzoiro 2025-2026 (≥450 min, Big5 + UCL)
    "FW": {"xa_per_90": 0.109, "key_pass_per_90": 1.164, "accurate_cross_per_90": 0.373},
    "MF": {"xa_per_90": 0.116, "key_pass_per_90": 1.172, "accurate_cross_per_90": 0.527},
    "DF": {"xa_per_90": 0.056, "key_pass_per_90": 0.532, "accurate_cross_per_90": 0.306},
}
_ASSIST_FALLBACK_AVGS: dict[str, float] = {
    "xa_per_90": 0.093, "key_pass_per_90": 0.956, "accurate_cross_per_90": 0.402,
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

from app.pricing.sub_constants import SUB_ASSIST_LAMBDA


def calculate_supersub_prob_assist(
    lambda_A: float,
    p_sub: float,
    t_sub: float,
    lambda_B_sub: float | None = None,
    position: str = "MF",
) -> float:
    """
    P(passe décisive gagnée avec mécanique supersub).
    Même formule que pour les buts, λ_B_sub issu de SUB_ASSIST_LAMBDA.
    """
    if lambda_B_sub is None:
        lambda_B_sub = SUB_ASSIST_LAMBDA.get(position, 0.07)
    lA_adj  = lambda_A * (t_sub / 90.0)
    lB_adj  = lambda_B_sub * ((90.0 - t_sub) / 90.0)
    p_full  = (1.0 - p_sub) * (1.0 - math.exp(-lambda_A))
    p_chain = p_sub          * (1.0 - math.exp(-(lA_adj + lB_adj)))
    return p_full + p_chain


