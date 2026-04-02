"""Market-implied xG service — derives λh, λa from bookmaker odds.

Pipeline:
  1. Load freshest MatchOddsSnapshot for the fixture.
  2. Devig totals + btts markets (betfair preferred, pinnacle fallback).
  3. Solve λt from Over-2.5 probability, then λh from BTTS.
  4. Use H2H market to disambiguate the two symmetric λh solutions.
  5. Cross-validate; flag if error > 8%.
  6. Fall back to Dixon-Coles when data is missing / solvers fail.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Literal

from scipy.optimize import brentq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Bookmaker preference helpers
# ---------------------------------------------------------------------------

_BOOKMAKER_PRIORITY = {"betfair": 0, "pinnacle": 1}

_STALENESS_LIMIT = timedelta(hours=24)


def _preferred_bookmaker(available: set[str]) -> str | None:
    """Return betfair if available, then pinnacle, else any."""
    for bm in ("betfair", "pinnacle"):
        if bm in available:
            return bm
    return next(iter(sorted(available)), None)


# ---------------------------------------------------------------------------
# Dixon-Coles fallback (module-level helper)
# ---------------------------------------------------------------------------


async def get_dixon_coles_fallback(
    fixture_id: int, session: AsyncSession
) -> MarketXgResult:
    """Call the Dixon-Coles estimator and wrap result as MarketXgResult.

    Returns a result with xg_source='dixon_coles' using compute_team_stats
    and estimate_team_match_xg from the pricing engine.
    """
    from app.models.fixtures import Fixture
    from app.pricing.team_xg import (
        HOME_ADVANTAGE,
        compute_team_stats,
        estimate_team_match_xg,
    )

    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        logger.warning("get_dixon_coles_fallback: fixture %s not found", fixture_id)
        return MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles")

    try:
        team_stats = await compute_team_stats(session)
        home_ts = team_stats.get(fixture.home_team)
        away_ts = team_stats.get(fixture.away_team)

        all_ts = list(team_stats.values())
        league_avg_xg = (
            sum(ts.attack_xg_per_match for ts in all_ts) / len(all_ts) if all_ts else 1.2
        )
        xga_values = [ts.defense_xga_per_match for ts in all_ts if ts.defense_xga_per_match > 0]
        league_avg_xga = sum(xga_values) / len(xga_values) if xga_values else league_avg_xg

        if home_ts:
            home_xg = estimate_team_match_xg(
                home_ts.attack_xg_per_match,
                away_ts.defense_xga_per_match if away_ts else league_avg_xga,
                league_avg_xg, league_avg_xga, is_home=True,
            )
        else:
            home_xg = league_avg_xg * HOME_ADVANTAGE

        if away_ts:
            away_xg = estimate_team_match_xg(
                away_ts.attack_xg_per_match,
                home_ts.defense_xga_per_match if home_ts else league_avg_xga,
                league_avg_xg, league_avg_xga, is_home=False,
            )
        else:
            away_xg = league_avg_xg
    except Exception:
        logger.warning(
            "get_dixon_coles_fallback: compute_team_stats failed for fixture %s → sentinel",
            fixture_id,
        )
        return MarketXgResult(xg_home=1.3, xg_away=1.0, xg_source="dixon_coles")

    return MarketXgResult(
        xg_home=round(home_xg, 3),
        xg_away=round(away_xg, 3),
        xg_source="dixon_coles",
    )


# ---------------------------------------------------------------------------
# MarketXgService
# ---------------------------------------------------------------------------


class MarketXgService:
    """Derive match-level xG from bookmaker odds snapshots."""

    async def compute(self, fixture_id: int, session: AsyncSession) -> MarketXgResult:
        """Compute market-implied xG for a fixture.

        Falls back to Dixon-Coles if:
        - fixture not found
        - no odds snapshot available
        - snapshot is stale (>24 h before kickoff)
        - required markets (totals, btts) are missing
        - solvers raise ValueError
        """
        from app.models.fixtures import Fixture
        from app.models.match_odds import MatchOddsSnapshot

        # 1. Load fixture
        fixture = await session.get(Fixture, fixture_id)
        if fixture is None:
            logger.warning("MarketXgService.compute: fixture %s not found", fixture_id)
            return await get_dixon_coles_fallback(fixture_id, session)

        # 2. Find freshest snapshot_utc for this fixture
        freshest_result = await session.execute(
            select(MatchOddsSnapshot.snapshot_utc)
            .where(MatchOddsSnapshot.fixture_id == fixture_id)
            .order_by(MatchOddsSnapshot.snapshot_utc.desc())
            .limit(1)
        )
        freshest_row = freshest_result.scalar_one_or_none()
        if freshest_row is None:
            logger.info(
                "MarketXgService.compute: no odds snapshot for fixture %s → fallback",
                fixture_id,
            )
            return await get_dixon_coles_fallback(fixture_id, session)

        freshest_snapshot_utc = freshest_row

        # Staleness check: reject snapshots more than 24 h BEFORE kickoff
        # Ensure freshest_snapshot_utc is timezone-aware before subtraction.
        if freshest_snapshot_utc.tzinfo is None:
            freshest_snapshot_utc = freshest_snapshot_utc.replace(tzinfo=UTC)

        try:
            staleness = fixture.kickoff_utc - freshest_snapshot_utc
        except (TypeError, AttributeError):
            logger.warning(
                "MarketXgService.compute: datetime tz mismatch for fixture %s → fallback",
                fixture_id,
            )
            return await get_dixon_coles_fallback(fixture_id, session)
        if staleness > _STALENESS_LIMIT:
            logger.info(
                "MarketXgService.compute: snapshot for fixture %s is stale "
                "(gap=%.1f h) → fallback",
                fixture_id,
                staleness.total_seconds() / 3600,
            )
            return await get_dixon_coles_fallback(fixture_id, session)

        # 3. Load all rows for the freshest snapshot_utc
        rows_result = await session.execute(
            select(MatchOddsSnapshot)
            .where(MatchOddsSnapshot.fixture_id == fixture_id)
            .where(MatchOddsSnapshot.snapshot_utc == freshest_snapshot_utc)
        )
        rows = rows_result.scalars().all()

        # Group by market_type → bookmaker → outcome → odds
        # Structure: markets[market_type][bookmaker][outcome] = odds
        markets: dict[str, dict[str, dict[str, float]]] = {}
        for row in rows:
            markets.setdefault(row.market_type, {}).setdefault(row.bookmaker, {})[
                row.outcome
            ] = row.odds

        # 4. Check minimum required markets
        if "totals" not in markets or "btts" not in markets:
            logger.info(
                "MarketXgService.compute: missing totals or btts for fixture %s → fallback",
                fixture_id,
            )
            return await get_dixon_coles_fallback(fixture_id, session)

        # 5. Devig each market using preferred bookmaker
        # --- totals ---
        totals_bm = _preferred_bookmaker(set(markets["totals"].keys()))
        if totals_bm is None:
            return await get_dixon_coles_fallback(fixture_id, session)
        totals_outcomes = markets["totals"][totals_bm]
        over_odds = totals_outcomes.get("over_2.5")
        under_odds = totals_outcomes.get("under_2.5")
        if over_odds is None or under_odds is None:
            logger.info(
                "MarketXgService.compute: missing over/under odds for fixture %s → fallback",
                fixture_id,
            )
            return await get_dixon_coles_fallback(fixture_id, session)
        p_over_2_5, _ = multiplicative_devig([over_odds, under_odds])

        # --- btts ---
        btts_bm = _preferred_bookmaker(set(markets["btts"].keys()))
        if btts_bm is None:
            return await get_dixon_coles_fallback(fixture_id, session)
        btts_outcomes = markets["btts"][btts_bm]
        yes_odds = btts_outcomes.get("yes")
        no_odds = btts_outcomes.get("no")
        if yes_odds is None or no_odds is None:
            logger.info(
                "MarketXgService.compute: missing btts yes/no odds for fixture %s → fallback",
                fixture_id,
            )
            return await get_dixon_coles_fallback(fixture_id, session)
        p_btts, _ = multiplicative_devig([yes_odds, no_odds])

        # --- h2h (optional — used only for λh disambiguation) ---
        p_home_win: float | None = None
        p_away_win: float | None = None
        if "h2h" in markets:
            h2h_bm = _preferred_bookmaker(set(markets["h2h"].keys()))
            if h2h_bm:
                h2h_outcomes = markets["h2h"][h2h_bm]
                home_odds = h2h_outcomes.get("home")
                draw_odds = h2h_outcomes.get("draw")
                away_odds = h2h_outcomes.get("away")
                if home_odds and draw_odds and away_odds:
                    p_home_win, _, p_away_win = multiplicative_devig(
                        [home_odds, draw_odds, away_odds]
                    )

        # 6. Solve λt and λh, λa
        try:
            lambda_t = solve_lambda_t(p_over_2_5)
            lh1, lh2 = solve_lambda_home(lambda_t, p_btts)
            if p_home_win is not None and p_away_win is not None:
                lambda_h = select_lambda_home(lh1, lh2, p_home_win, p_away_win)
            else:
                # No H2H — default to midpoint (λt / 2)
                lambda_h = lambda_t / 2
            lambda_a = lambda_t - lambda_h
        except ValueError as exc:
            logger.info(
                "MarketXgService.compute: solver failed for fixture %s (%s) → fallback",
                fixture_id,
                exc,
            )
            return await get_dixon_coles_fallback(fixture_id, session)

        # 7. Clamp
        lambda_h = max(0.05, lambda_h)
        lambda_a = max(0.05, lambda_a)

        # 8. Cross-validate
        ok, reason = cross_validate(lambda_h, lambda_a, p_over_2_5, p_btts)
        xg_source: Literal["market_implied", "market_implied_flagged"] = (
            "market_implied" if ok else "market_implied_flagged"
        )

        # 9. Return result
        return MarketXgResult(
            xg_home=round(lambda_h, 3),
            xg_away=round(lambda_a, 3),
            xg_source=xg_source,
            flagged_reason=reason,
        )
