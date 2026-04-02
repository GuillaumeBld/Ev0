import math
from dataclasses import dataclass
from typing import Literal

from scipy.optimize import brentq


@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged", "dixon_coles"]
    flagged_reason: str | None = None


def multiplicative_devig(odds_list: list[float]) -> list[float]:
    """Multiplicative devigging. Returns true probabilities summing to 1."""
    implied = [1.0 / o for o in odds_list]
    total = sum(implied)
    return [p / total for p in implied]


def solve_lambda_t(p_over_2_5: float) -> float:
    """Solve lambda_t from P(Over 2.5) via Poisson CDF inversion.
    P(total >= 3) = 1 - e^(-lambda_t)(1 + lambda_t + lambda_t^2/2)
    Uses scipy brentq on [0.1, 10].
    Raises ValueError if no root found.
    """
    def f(lam):
        return 1 - math.exp(-lam) * (1 + lam + lam**2 / 2) - p_over_2_5
    return brentq(f, 0.1, 10.0)


def solve_lambda_home(lambda_t: float, p_btts: float) -> tuple[float, float]:
    """Solve lambda_h from P(BTTS Yes) given lambda_t.
    P(BTTS) = (1 - e^(-lambda_h)) * (1 - e^(-(lambda_t - lambda_h)))
    Returns (lambda_h_solution_1, lambda_h_solution_2) -- two symmetric solutions.
    Raises ValueError if no root found (degenerate case).
    lambda_h search range: [0.05, lambda_t / 2]
    """
    def f(lh):
        la = lambda_t - lh
        return (1 - math.exp(-lh)) * (1 - math.exp(-la)) - p_btts

    lo, mid = 0.05, lambda_t / 2
    if lo >= mid:
        raise ValueError(f"lambda_t={lambda_t} too small for BTTS solve")

    # The BTTS function peaks at lh=lambda_t/2; roots exist on [lo, mid] and [mid, hi].
    # Bracket check: f(lo) must be negative and f(mid) must be positive.
    if f(lo) >= 0 or f(mid) <= 0:
        raise ValueError("No root in BTTS bracket -- degenerate case")

    lh1 = brentq(f, lo, mid)  # first solution (left of midpoint)
    lh2 = lambda_t - lh1  # symmetric solution
    return lh1, lh2


def select_lambda_home(lh1: float, lh2: float, p_home_win: float, p_away_win: float) -> float:
    """Select correct lambda_h using H2H sign: if home stronger, lambda_h = max(lh1, lh2)."""
    if p_home_win >= p_away_win:
        return max(lh1, lh2)
    return min(lh1, lh2)


def cross_validate(lambda_h: float, lambda_a: float,
                   p_over_2_5_true: float, p_btts_true: float) -> tuple[bool, str | None]:
    """Cross-validate (lambda_h, lambda_a) against market probabilities.
    Returns (ok, reason) -- ok=False means flagged.
    Threshold: 8% absolute error on any market.
    """
    lambda_t = lambda_h + lambda_a

    pred_over = 1 - math.exp(-lambda_t) * (1 + lambda_t + lambda_t**2 / 2)
    pred_btts = (1 - math.exp(-lambda_h)) * (1 - math.exp(-lambda_a))

    over_err = abs(pred_over - p_over_2_5_true)
    btts_err = abs(pred_btts - p_btts_true)

    if over_err > 0.08:
        return False, f"Over 2.5 cross-validation error {over_err:.3f} > 0.08"
    if btts_err > 0.08:
        return False, f"BTTS cross-validation error {btts_err:.3f} > 0.08"
    return True, None
