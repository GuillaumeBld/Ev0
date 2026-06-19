"""Market-implied xG service — derives λh, λa from bookmaker odds.

Pipeline:
  1. Load freshest MatchOddsSnapshot for the fixture.
  2. Devig totals + h2h markets (oddsportal preferred, betfair, pinnacle fallback).
  3. If BTTS market available: solve via 4-constraint L-BFGS-B (_fit_lambdas).
     Otherwise: solve λt from Over-2.5, then λh from H2H via brentq (2-constraint).
  4. Flag result if fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD.
  5. Return None (not a Dixon-Coles fallback) when data missing / solvers fail.

Note: BTTS market may not be available for all fixtures.
The legacy BTTS solvers (solve_lambda_home, cross_validate) are kept for tests.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

# Python 3.9 compatibility: UTC not available until 3.11
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from scipy.optimize import brentq, minimize
from scipy.stats import poisson as _poisson_dist
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_SNAPSHOT_AGE = timedelta(hours=4)  # scraper runs every 2h for matches >6h out
FIT_RESIDUAL_FLAG_THRESHOLD = 0.06


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged", "bzzoiro"]
    data_source: str = ""          # "oddsportal" | "betclic" | "unibet" | "pmu" | "bzzoiro"
    fallback_used: bool = False
    fit_residual: float = 0.0
    flagged: bool = False          # fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD
    as_of_utc: datetime | None = None
    input_snapshot_ids: list[int] = field(default_factory=list)
    flagged_reason: str | None = None   # kept for backward-compat
    last_snapshot_at: datetime | None = None  # freshest scraped_at, even if stale


# ---------------------------------------------------------------------------
# Legacy devig helper (kept for existing tests)
# ---------------------------------------------------------------------------


def multiplicative_devig(odds_list: list[float]) -> list[float]:
    """Multiplicative devigging. Returns true probabilities summing to 1."""
    implied = [1.0 / o for o in odds_list]
    total = sum(implied)
    return [p / total for p in implied]


# ---------------------------------------------------------------------------
# Legacy single-market solvers (kept for existing tests)
# ---------------------------------------------------------------------------


def solve_lambda_t(p_over_2_5: float) -> float:
    """Solve lambda_t from P(Over 2.5) via Poisson CDF inversion.
    P(total >= 3) = 1 - e^(-lambda_t)(1 + lambda_t + lambda_t^2/2)
    Uses scipy brentq on [0.1, 10].
    Raises ValueError if no root found.

    Requires: p_over_2_5 achievable by a Poisson rate in [0.1, 10],
    i.e. approximately (0.00016, 0.997). Values outside this range raise ValueError.
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

    # The BTTS function g(lh) = (1-e^{-lh})*(1-e^{-(lt-lh)}) is symmetric about lt/2,
    # so g(lo) = g(hi) always -- brentq([lo, hi]) would always fail. Instead bracket
    # [lo, mid] where g is strictly increasing; the second root is recovered via symmetry.
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


def _poisson_home_win(lambda_h: float, lambda_a: float, max_k: int = 20) -> float:
    """P(home score > away score) under independent Poisson(λh), Poisson(λa)."""
    total = 0.0
    for h in range(1, max_k + 1):
        ph = math.exp(-lambda_h) * lambda_h**h / math.factorial(h)
        for a in range(h):
            total += ph * math.exp(-lambda_a) * lambda_a**a / math.factorial(a)
    return total


def solve_lambda_home_from_h2h(lambda_t: float, p_home_win: float) -> float:
    """Solve λh from P(home win) given λt, using Poisson inversion (brentq).

    With λa = λt − λh, finds λh ∈ [0.05, λt−0.05] such that
    P_Poisson(home win | λh, λt−λh) = p_home_win.

    Raises ValueError if bracket has no sign change (degenerate case).
    """
    lo, hi = 0.05, lambda_t - 0.05
    if lo >= hi:
        raise ValueError(f"lambda_t={lambda_t} too small for H2H solve")

    def f(lh: float) -> float:
        return _poisson_home_win(lh, lambda_t - lh) - p_home_win

    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        raise ValueError(
            f"H2H solver: no sign change (f({lo:.2f})={f_lo:.3f}, f({hi:.2f})={f_hi:.3f})"
        )
    return brentq(f, lo, hi)


def cross_validate_h2h(
    lambda_h: float,
    lambda_a: float,
    p_over_2_5_true: float,
    p_home_win_true: float,
) -> tuple[bool, str | None]:
    """Cross-validate (λh, λa) against Over-2.5 and H2H market probabilities.

    Returns (ok, reason). Threshold: 8% absolute error on any market.
    """
    lambda_t = lambda_h + lambda_a
    pred_over = 1 - math.exp(-lambda_t) * (1 + lambda_t + lambda_t**2 / 2)
    pred_home_win = _poisson_home_win(lambda_h, lambda_a)

    over_err = abs(pred_over - p_over_2_5_true)
    if over_err > 0.08:
        return False, f"Over 2.5 cross-validation error {over_err:.3f} > 0.08"

    h2h_err = abs(pred_home_win - p_home_win_true)
    if h2h_err > 0.08:
        return False, f"H2H cross-validation error {h2h_err:.3f} > 0.08"

    return True, None


# ---------------------------------------------------------------------------
# Bookmaker preference helpers
# ---------------------------------------------------------------------------

_BOOKMAKER_PRIORITY = ["oddsportal", "betfair", "pinnacle", "betclic", "unibet", "pmu"]


def _preferred_bookmaker(available: set[str]) -> str | None:
    """Return preferred bookmaker from priority list, or any available."""
    if not available:
        return None
    for bm in _BOOKMAKER_PRIORITY:
        if bm in available:
            return bm
    return next(iter(available))


# ---------------------------------------------------------------------------
# 4-constraint Poisson solver (L-BFGS-B) for Task 11
# ---------------------------------------------------------------------------


def _p_poisson_home_win(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home > Away) under Poisson(lh) vs Poisson(la)."""
    total = 0.0
    for h in range(1, max_goals + 1):
        ph = _poisson_dist.pmf(h, lh)
        for a in range(0, h):
            total += ph * _poisson_dist.pmf(a, la)
    return total


def _p_poisson_draw(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home == Away) under Poisson."""
    return float(sum(_poisson_dist.pmf(k, lh) * _poisson_dist.pmf(k, la) for k in range(max_goals + 1)))


# ---------------------------------------------------------------------------
# Public aliases for external callers (h2h recommendation pipeline)
# ---------------------------------------------------------------------------


def p_poisson_home_win(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home > Away) under independent Poisson(lh) vs Poisson(la)."""
    return _p_poisson_home_win(lh, la, max_goals)


def p_poisson_draw(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Home == Away) under independent Poisson(lh) vs Poisson(la)."""
    return _p_poisson_draw(lh, la, max_goals)


def p_poisson_away_win(lh: float, la: float, max_goals: int = 10) -> float:
    """P(Away > Home) = 1 - P(home win) - P(draw)."""
    return 1.0 - _p_poisson_home_win(lh, la, max_goals) - _p_poisson_draw(lh, la, max_goals)


def _p_poisson_over_2_5(lt: float) -> float:
    """P(Total > 2.5) = 1 - e^{-lt}(1 + lt + lt^2/2)."""
    return 1.0 - math.exp(-lt) * (1.0 + lt + lt ** 2 / 2.0)


def _p_poisson_btts(lh: float, la: float) -> float:
    """P(Both teams score) = (1 - e^{-lh})(1 - e^{-la})."""
    return (1.0 - math.exp(-lh)) * (1.0 - math.exp(-la))


def _solve_lambda_t_approx(p_over_2_5: float) -> float:
    """Rough brentq estimate of total lambda — used as warm start for _fit_lambdas."""
    def eq(lt: float) -> float:
        return _p_poisson_over_2_5(lt) - p_over_2_5
    try:
        return float(brentq(eq, 0.05, 15.0))
    except ValueError:
        return 2.5


def _fit_lambdas(
    p_home_win: float,
    p_draw: float,
    p_over_2_5: float,
    p_btts_yes: float,
) -> tuple[float, float, float]:
    """
    Fit (lambda_home, lambda_away) to 4 market probabilities via L-BFGS-B.

    Returns (lambda_home, lambda_away, fit_residual).
    fit_residual is the sum of squared deviations across all 4 constraints.
    Flag result if fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD (0.06).
    """
    def objective(x: list[float]) -> float:
        lh, la = x
        r1 = (_p_poisson_home_win(lh, la) - p_home_win) ** 2
        r2 = (_p_poisson_draw(lh, la) - p_draw) ** 2
        r3 = (_p_poisson_over_2_5(lh + la) - p_over_2_5) ** 2
        r4 = (_p_poisson_btts(lh, la) - p_btts_yes) ** 2
        return r1 + r2 + r3 + r4

    lt_init = _solve_lambda_t_approx(p_over_2_5)
    lh_init = min(max(lt_init * 0.55, 0.3), 4.0)
    la_init = min(max(lt_init * 0.45, 0.3), 4.0)

    result = minimize(
        objective,
        x0=[lh_init, la_init],
        bounds=[(0.05, 4.5), (0.05, 4.5)],
        method="L-BFGS-B",
        options={"maxiter": 500, "ftol": 1e-12},
    )
    lh, la = float(result.x[0]), float(result.x[1])
    return lh, la, float(result.fun)


# ---------------------------------------------------------------------------
# MarketXgService
# ---------------------------------------------------------------------------


class MarketXgService:
    """Derive match-level xG from bookmaker odds snapshots.

    2-level fallback chain:
      Level 1 — market_implied : solve λh/λa from bookmaker odds (L-BFGS-B / brentq)
      Level 2 — bzzoiro        : use expected_home/away_goals from bzz_predictions
    """

    def __init__(self) -> None:
        self._last_snapshot_at: datetime | None = None

    async def compute(self, fixture_id: int, session: AsyncSession) -> MarketXgResult | None:
        """Compute xG pour un fixture en utilisant la meilleure source disponible.

        Returns None uniquement si les deux niveaux échouent (aucune donnée en DB).
        result.last_snapshot_at est toujours rempli si un snapshot existe en DB,
        même si trop vieux pour être utilisé.
        """
        from app.models.fixtures import Fixture

        fixture = await session.get(Fixture, fixture_id)
        if fixture is None:
            logger.warning("MarketXgService.compute: fixture %s not found", fixture_id)
            return None

        # Niveau 1 : market-implied depuis les odds bookmaker
        result = await self._try_market_implied(fixture_id, session)
        if result is not None:
            return result

        # Niveau 2 : prédictions Bzzoiro (expected_home/away_goals)
        result = await self._try_bzzoiro_predictions(fixture, session)
        if result is not None:
            result.last_snapshot_at = self._last_snapshot_at  # attach last scrape time
            logger.info(
                "market_xg: fallback bzzoiro pour fixture %s (%s vs %s)",
                fixture_id, fixture.home_team, fixture.away_team,
            )
            return result

        logger.warning(
            "market_xg: aucune donnée disponible pour fixture %s (%s vs %s)",
            fixture_id, fixture.home_team, fixture.away_team,
        )
        return None

    async def _try_market_implied(
        self, fixture_id: int, session: AsyncSession
    ) -> MarketXgResult | None:
        """Level 1: derive λh/λa from bookmaker odds snapshots.

        Returns None if no fresh snapshot, missing markets, or solvers fail.
        Uses 4-constraint L-BFGS-B solver when BTTS available, otherwise
        2-constraint brentq solver (Over-2.5 + H2H).
        """
        from app.models.match_odds import MatchOddsSnapshot

        # Find freshest snapshot_utc for this fixture
        freshest_result = await session.execute(
            select(MatchOddsSnapshot.snapshot_utc)
            .where(MatchOddsSnapshot.fixture_id == fixture_id)
            .order_by(MatchOddsSnapshot.snapshot_utc.desc())
            .limit(1)
        )
        freshest_row = freshest_result.scalar_one_or_none()
        if freshest_row is None:
            logger.info(
                "market_xg: no odds snapshot for fixture %s → trying fallbacks",
                fixture_id,
            )
            return None

        freshest_snapshot_utc = freshest_row
        if freshest_snapshot_utc.tzinfo is None:
            freshest_snapshot_utc = freshest_snapshot_utc.replace(tzinfo=UTC)

        self._last_snapshot_at = freshest_snapshot_utc  # always record, even if stale

        now = datetime.now(timezone.utc)
        snapshot_age = now - freshest_snapshot_utc
        if snapshot_age > MAX_SNAPSHOT_AGE:
            logger.warning(
                "market_xg: stale snapshot for fixture %s (age=%s) → trying fallbacks",
                fixture_id,
                snapshot_age,
            )
            return None

        # Load all rows within MAX_SNAPSHOT_AGE (absolute window).
        # Different scrapers write at different intervals; a 15-min relative window
        # would exclude betclic/pmu totals when bzzoiro h2h lands later.
        # ASC order so later rows overwrite older ones per (market, bm, outcome).
        rows_result = await session.execute(
            select(MatchOddsSnapshot)
            .where(MatchOddsSnapshot.fixture_id == fixture_id)
            .where(MatchOddsSnapshot.snapshot_utc >= now - MAX_SNAPSHOT_AGE)
            .order_by(MatchOddsSnapshot.snapshot_utc)
        )
        rows = rows_result.scalars().all()

        markets: dict[str, dict[str, dict[str, float]]] = {}
        snapshot_ids: list[int] = []
        snapshot_source: str = "unknown"
        snapshot_fallback_used: bool = False
        for row in rows:
            markets.setdefault(row.market_type, {}).setdefault(row.bookmaker, {})[
                row.outcome
            ] = row.odds
            if hasattr(row, "id") and row.id is not None:
                snapshot_ids.append(row.id)
            if hasattr(row, "source") and row.source:
                snapshot_source = row.source
            if hasattr(row, "fallback_used") and row.fallback_used:
                snapshot_fallback_used = True

        if "totals" not in markets or "h2h" not in markets:
            logger.info(
                "market_xg: missing totals or h2h for fixture %s → trying fallbacks",
                fixture_id,
            )
            return None

        # Devig totals
        totals_bm = _preferred_bookmaker(set(markets["totals"].keys()))
        if totals_bm is None:
            return None
        totals_outcomes = markets["totals"][totals_bm]
        over_odds = totals_outcomes.get("over_2.5")
        under_odds = totals_outcomes.get("under_2.5")
        if over_odds is None or under_odds is None:
            logger.info(
                "market_xg: missing over/under odds for fixture %s → trying fallbacks",
                fixture_id,
            )
            return None
        p_over_2_5, _ = multiplicative_devig([over_odds, under_odds])

        # Devig h2h
        h2h_bm = _preferred_bookmaker(set(markets["h2h"].keys()))
        if h2h_bm is None:
            return None
        h2h_outcomes = markets["h2h"][h2h_bm]
        home_odds = h2h_outcomes.get("home")
        draw_odds = h2h_outcomes.get("draw")
        away_odds = h2h_outcomes.get("away")
        if home_odds is None or draw_odds is None or away_odds is None:
            logger.info(
                "market_xg: missing h2h outcomes for fixture %s → trying fallbacks",
                fixture_id,
            )
            return None
        h2h_clean = multiplicative_devig([home_odds, draw_odds, away_odds])
        p_home_win, p_draw_val, p_away_win = h2h_clean

        data_source = _preferred_bookmaker(
            set(markets.get("h2h", {}).keys()) | set(markets.get("totals", {}).keys())
        ) or "unknown"

        # Devig btts (optional)
        p_btts_yes: float | None = None
        if "btts" in markets:
            btts_bm = _preferred_bookmaker(set(markets["btts"].keys()))
            if btts_bm is not None:
                btts_outcomes = markets["btts"][btts_bm]
                yes_odds = btts_outcomes.get("yes")
                no_odds = btts_outcomes.get("no")
                if yes_odds is not None and no_odds is not None:
                    btts_clean = multiplicative_devig([yes_odds, no_odds])
                    p_btts_yes = btts_clean[0]

        lambda_h: float
        lambda_a: float
        fit_residual: float

        if p_btts_yes is not None:
            try:
                lambda_h, lambda_a, fit_residual = _fit_lambdas(
                    p_home_win, p_draw_val, p_over_2_5, p_btts_yes
                )
            except Exception as exc:
                logger.warning(
                    "market_xg: L-BFGS-B solver failed for fixture %s: %s → trying fallbacks",
                    fixture_id, exc,
                )
                return None
        else:
            try:
                lambda_t = solve_lambda_t(p_over_2_5)
                lambda_h = solve_lambda_home_from_h2h(lambda_t, p_home_win)
                lambda_a = lambda_t - lambda_h
                ok, reason = cross_validate_h2h(lambda_h, lambda_a, p_over_2_5, p_home_win)
                fit_residual = 0.0 if ok else FIT_RESIDUAL_FLAG_THRESHOLD + 0.01
            except ValueError as exc:
                logger.warning(
                    "market_xg: brentq solver failed for fixture %s: %s → trying fallbacks",
                    fixture_id, exc,
                )
                return None

        lambda_h = max(0.05, lambda_h)
        lambda_a = max(0.05, lambda_a)
        flagged = fit_residual > FIT_RESIDUAL_FLAG_THRESHOLD
        xg_source: Literal["market_implied", "market_implied_flagged"] = (
            "market_implied_flagged" if flagged else "market_implied"
        )

        return MarketXgResult(
            xg_home=round(lambda_h, 3),
            xg_away=round(lambda_a, 3),
            xg_source=xg_source,
            data_source=snapshot_source if snapshot_source != "unknown" else data_source,
            fallback_used=snapshot_fallback_used,
            fit_residual=fit_residual,
            flagged=flagged,
            as_of_utc=freshest_snapshot_utc,
            input_snapshot_ids=snapshot_ids,
            flagged_reason=None,
            last_snapshot_at=freshest_snapshot_utc,
        )

    async def _try_bzzoiro_predictions(
        self, fixture: Any, session: AsyncSession
    ) -> MarketXgResult | None:
        """Level 2: use Bzzoiro's expected_home/away_goals from bzz_predictions.

        Only works for fixtures whose external_id starts with "bzz_<event_api_id>".
        """
        from app.models.bzzoiro import BzzPrediction

        ext_id = str(fixture.external_id or "")
        if not ext_id.startswith("bzz_"):
            return None

        try:
            event_api_id = int(ext_id[4:])  # strip "bzz_" prefix
        except ValueError:
            return None

        result = await session.execute(
            select(BzzPrediction).where(BzzPrediction.event_api_id == event_api_id)
        )
        pred = result.scalar_one_or_none()

        if pred is None:
            logger.info(
                "market_xg: no bzz_prediction for event_api_id %s", event_api_id
            )
            return None

        xg_home = pred.expected_home_goals
        xg_away = pred.expected_away_goals

        if xg_home is None or xg_away is None:
            logger.info(
                "market_xg: bzz_prediction missing xG for event_api_id %s", event_api_id
            )
            return None

        return MarketXgResult(
            xg_home=round(float(xg_home), 3),
            xg_away=round(float(xg_away), 3),
            xg_source="bzzoiro",
            data_source="bzzoiro",
            fallback_used=True,
            fit_residual=0.0,
            flagged=False,
            as_of_utc=datetime.now(UTC),
            input_snapshot_ids=[],
        )

