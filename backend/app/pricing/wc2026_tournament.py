"""WC2026 tournament-level player pricing."""
from __future__ import annotations

import logging
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

N_MONTE_CARLO = 50_000
_MC_SEED = 42
_ASSIST_GOAL_RATE = 0.65  # assists budget = BM × rate


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def poisson_ge(lam: float, k: int) -> float:
    """P(X >= k) where X ~ Poisson(lam). Pure math, no scipy."""
    if lam <= 0:
        return 0.0
    cdf = sum(math.exp(-lam) * (lam ** j) / math.factorial(j) for j in range(k))
    return max(0.0, min(1.0, 1.0 - cdf))


def _fair_odds(p: float) -> float | None:
    return round(1.0 / p, 2) if p > 0.001 else None


def run_monte_carlo(
    lambdas_goals: list[float],
    lambdas_assists: list[float],
    n_sim: int = N_MONTE_CARLO,
    seed: int = _MC_SEED,
) -> list[dict[str, float]]:
    """Simulate n_sim tournaments; return p_top_scorer + p_top_assister per player."""
    rng = np.random.default_rng(seed)
    n = len(lambdas_goals)
    lg = np.array(lambdas_goals, dtype=float)
    la = np.array(lambdas_assists, dtype=float)
    goals_sim   = rng.poisson(lg[:, None], size=(n, n_sim))
    assists_sim = rng.poisson(la[:, None], size=(n, n_sim))
    top_scorer_idx   = goals_sim.argmax(axis=0)
    top_assister_idx = assists_sim.argmax(axis=0)
    results = []
    for i in range(n):
        results.append({
            "p_top_scorer":   float((top_scorer_idx   == i).sum()) / n_sim,
            "p_top_assister": float((top_assister_idx == i).sum()) / n_sim,
        })
    return results


async def compute_tournament_pricing(db: AsyncSession) -> list[dict[str, Any]]:
    """Compute per-player tournament pricing for WC2026.

    Formula:
        weight_g_i  = xg_p90_i × (expected_minutes_i / 90)
        share_g_i   = weight_g_i / sum(weight_g)
        lambda_goals = share_g_i × BM

    Stats source: wc2026_scouting_stats (all 48 nations).
    Lineup source: wc2026_expected_lineups / wc2026_expected_lineup_players.
    """
    from sqlalchemy import text
    from app.ingestion.wc2026.team_bm import (
        TEAM_BM,
        WC2026_LINEUP_NATION_MAP,
        WC2026_SCOUTING_NATION_MAP,
    )
    from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

    all_entries: list[dict[str, Any]] = []

    for nation, bm in TEAM_BM.items():
        # 1. Load default lineup (DB stores nation names in French)
        lineup_nation = WC2026_LINEUP_NATION_MAP.get(nation, nation)
        lineup_result = await db.execute(
            select(WC2026ExpectedLineup).where(
                WC2026ExpectedLineup.nation == lineup_nation,
                WC2026ExpectedLineup.context == "default",
            )
        )
        lineup = lineup_result.scalar_one_or_none()
        if lineup is None:
            logger.warning("wc2026_pricing: no default lineup for %s — skipped", nation)
            continue

        # 2. Load expected minutes per player from lineup
        lp_result = await db.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            )
        )
        lineup_minutes: dict[str, float] = {
            _norm_name(lp.player_name): float(lp.expected_minutes)
            for lp in lp_result.scalars().all()
        }

        # 3. Load scouting stats for this nation
        scouting_nation = WC2026_SCOUTING_NATION_MAP.get(nation, nation)
        rows = (await db.execute(
            text(
                "SELECT player_name, normalized_name, stats "
                "FROM wc2026_scouting_stats WHERE nation = :nation"
            ),
            {"nation": scouting_nation},
        )).mappings().all()

        if not rows:
            logger.warning(
                "wc2026_pricing: no scouting stats for %s (searched '%s') — skipped",
                nation, scouting_nation,
            )
            continue

        # 4. Match scouting rows to lineup by normalised name.
        # Fallback: try reversed token order for cultures where name format differs
        # (e.g. "Heung-min Son" in scouting vs "Son Heung-min" in lineup).
        matched: list[dict[str, Any]] = []
        for row in rows:
            norm = _norm_name(row["player_name"])
            mins = lineup_minutes.get(norm)
            if mins is None:
                parts = norm.split()
                if len(parts) == 2:
                    mins = lineup_minutes.get(f"{parts[1]} {parts[0]}")
            if mins is None:
                continue
            stats = row["stats"] or {}
            matched.append({
                "player_name": row["player_name"],
                "position":    stats.get("position"),
                "npxg_per_90": float(stats.get("xg_p90") or 0.0),
                "xa_per_90":   float(stats.get("xa_p90") or 0.0),
                "minutes":     mins,
            })

        if len(matched) < 3:
            logger.warning(
                "wc2026_pricing: only %d matched players for %s — skipped",
                len(matched), nation,
            )
            continue

        # 5. Compute weights and normalise to relative shares
        for p in matched:
            p["w_g"] = p["npxg_per_90"] * (p["minutes"] / 90.0)
            p["w_a"] = p["xa_per_90"]   * (p["minutes"] / 90.0)

        total_g = sum(p["w_g"] for p in matched) or 1e-9
        total_a = sum(p["w_a"] for p in matched) or 1e-9
        budget_assists = bm * _ASSIST_GOAL_RATE

        for p in matched:
            all_entries.append({
                "nation":        nation,
                "player_name":   p["player_name"],
                "position":      p["position"],
                "lambda_goals":  round((p["w_g"] / total_g) * bm,            4),
                "lambda_assists": round((p["w_a"] / total_a) * budget_assists, 4),
            })

    if not all_entries:
        logger.warning("wc2026_pricing: no entries collected — returning empty list")
        return []

    # 6. Monte Carlo across ALL players simultaneously
    mc_results = run_monte_carlo(
        [e["lambda_goals"]   for e in all_entries],
        [e["lambda_assists"] for e in all_entries],
        n_sim=N_MONTE_CARLO,
        seed=_MC_SEED,
    )

    # 7. Poisson cuts + assemble final rows
    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    for entry, mc in zip(all_entries, mc_results):
        lg = entry["lambda_goals"]
        la = entry["lambda_assists"]
        p_ts = mc["p_top_scorer"]
        p_ta = mc["p_top_assister"]
        output.append({
            "nation":           entry["nation"],
            "player_name":      entry["player_name"],
            "position":         entry["position"],
            "lambda_goals":     lg,
            "lambda_assists":   la,
            "p_1g":             round(poisson_ge(lg, 1), 6),
            "p_2g":             round(poisson_ge(lg, 2), 6),
            "p_3g":             round(poisson_ge(lg, 3), 6),
            "p_4g":             round(poisson_ge(lg, 4), 6),
            "fair_1g":          _fair_odds(poisson_ge(lg, 1)),
            "fair_2g":          _fair_odds(poisson_ge(lg, 2)),
            "fair_3g":          _fair_odds(poisson_ge(lg, 3)),
            "fair_4g":          _fair_odds(poisson_ge(lg, 4)),
            "p_1a":             round(poisson_ge(la, 1), 6),
            "p_2a":             round(poisson_ge(la, 2), 6),
            "p_3a":             round(poisson_ge(la, 3), 6),
            "fair_1a":          _fair_odds(poisson_ge(la, 1)),
            "fair_2a":          _fair_odds(poisson_ge(la, 2)),
            "fair_3a":          _fair_odds(poisson_ge(la, 3)),
            "p_top_scorer":     round(p_ts, 6),
            "p_top_assister":   round(p_ta, 6),
            "fair_top_scorer":  _fair_odds(p_ts),
            "fair_top_assister": _fair_odds(p_ta),
            "computed_at":      now,
        })

    logger.info(
        "wc2026_pricing: computed %d players across %d nations",
        len(output), len({e["nation"] for e in output}),
    )
    return output
